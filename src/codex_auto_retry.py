#!/usr/bin/env python3
"""Retry transient Codex Desktop failures on macOS through the app's local IPC.

The watcher only observes new rollout events after it starts. It retries a failed
turn by asking the owning Codex Desktop client to start an empty continuation on
the same conversation, so the desktop app remains the source of truth.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import selectors
import socket
import struct
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


CODEX_HOME = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
AUTO_RETRY_DIR = CODEX_HOME / "auto-retry"
SESSIONS_DIR = CODEX_HOME / "sessions"
SOCKET_PATH = r"\\.\pipe\codex-ipc" if os.name == "nt" else CODEX_HOME / "ipc" / "ipc.sock"
STATE_PATH = AUTO_RETRY_DIR / "state.json"
CONFIG_PATH = AUTO_RETRY_DIR / "config.json"
LOCK_PATH = AUTO_RETRY_DIR / "watcher.lock"
LOG_PATH = AUTO_RETRY_DIR / "watcher.log"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.openai.codex-auto-retry.plist"

POLL_SECONDS = float(os.environ.get("CODEX_AUTO_RETRY_POLL_SECONDS", "1.0"))
INITIAL_BACKOFF = float(os.environ.get("CODEX_AUTO_RETRY_INITIAL_BACKOFF", "5"))
MAX_BACKOFF = float(os.environ.get("CODEX_AUTO_RETRY_MAX_BACKOFF", "120"))
MAX_ATTEMPTS = int(os.environ.get("CODEX_AUTO_RETRY_MAX_ATTEMPTS", "15"))
MAX_CHAIN_SECONDS = float(os.environ.get("CODEX_AUTO_RETRY_MAX_CHAIN_SECONDS", "1800"))

DEFAULT_SETTINGS = {
    "enabled": True,
    "backoff_mode": "linear",
    "poll_seconds": POLL_SECONDS,
    "initial_backoff": INITIAL_BACKOFF,
    "max_backoff": MAX_BACKOFF,
    "max_attempts": MAX_ATTEMPTS,
    "max_chain_seconds": MAX_CHAIN_SECONDS,
}


def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        if isinstance(raw, dict):
            for key in settings:
                if key == "backoff_mode" and isinstance(raw.get(key), str):
                    settings[key] = raw[key]
                elif key in raw and isinstance(raw[key], (bool, int, float)):
                    settings[key] = raw[key]
    except (OSError, ValueError):
        pass
    settings["enabled"] = bool(settings["enabled"])
    settings["poll_seconds"] = max(0.2, float(settings["poll_seconds"]))
    settings["initial_backoff"] = max(1.0, float(settings["initial_backoff"]))
    settings["max_backoff"] = max(settings["initial_backoff"], float(settings["max_backoff"]))
    settings["max_attempts"] = max(1, min(100, int(settings["max_attempts"])))
    settings["max_chain_seconds"] = max(60.0, min(86400.0, float(settings["max_chain_seconds"])))
    if settings["backoff_mode"] not in ("fixed", "linear", "exponential"):
        settings["backoff_mode"] = "linear"
    return settings


SETTINGS = load_settings()

RETRY_PATTERNS = [
    re.compile(r"\b(?:408|425|429|500|501|502|503|504|505|506|507|508|509|510|511)\b"),
    re.compile(r"\b5\d\d\b"),
    re.compile(r"rate[ -]?limit", re.I),
    re.compile(r"too many requests", re.I),
    re.compile(r"server[ _-]?overloaded", re.I),
    re.compile(r"at capacity", re.I),
    re.compile(r"capacity", re.I),
    re.compile(r"tim(?:e|ed)[ -]?out", re.I),
    re.compile(r"network", re.I),
    re.compile(r"connection (?:reset|closed|refused|aborted)", re.I),
    re.compile(r"socket", re.I),
    re.compile(r"dns", re.I),
    re.compile(r"empty response", re.I),
    re.compile(r"incomplete|interrupted stream", re.I),
]
EXCLUDE_PATTERNS = [
    re.compile(r"invalid[_ -]?request", re.I),
    re.compile(r"not supported", re.I),
    re.compile(r"unsupported", re.I),
    re.compile(r"context length|context window|too many tokens", re.I),
    re.compile(r"policy|safety|permission|forbidden|unauthorized", re.I),
    re.compile(r"cancel(?:led|ed)|user.?abort|interrupted by user", re.I),
    re.compile(r"model .* not supported", re.I),
]


def ensure_dirs() -> None:
    AUTO_RETRY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(AUTO_RETRY_DIR, 0o700)
    except OSError:
        pass


def setup_logging(foreground: bool = False) -> None:
    ensure_dirs()
    handlers: List[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if foreground:
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def acquire_watcher_lock() -> Any:
    ensure_dirs()
    handle = LOCK_PATH.open("a+")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            handle.write("0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        handle.close()
        return None
    return handle


def load_state() -> Dict[str, Any]:
    try:
        with STATE_PATH.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if isinstance(value, dict) and value.get("schema") == 1:
            return value
    except (OSError, ValueError):
        logging.warning("state file could not be read; starting a new state")
    return {"schema": 1, "files": {}, "threads": {}}


def safe_error_text(error: Any) -> str:
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = error.get("message")
        info = error.get("codex_error_info")
        pieces = [str(item) for item in (message, info) if item is not None]
        return " ".join(pieces)
    return str(error) if error is not None else ""


def retry_category(error: Any) -> Optional[str]:
    text = safe_error_text(error)
    if not text:
        return None
    for pattern in EXCLUDE_PATTERNS:
        if pattern.search(text):
            return None
    if isinstance(error, dict) and error.get("codex_error_info") == "server_overloaded":
        return "server_overloaded"
    for pattern in RETRY_PATTERNS:
        if pattern.search(text):
            if re.search(r"\b429\b|rate[ -]?limit|too many requests", text, re.I):
                return "rate_limited"
            if re.search(r"\b5\d\d\b|overloaded|capacity", text, re.I):
                return "server_unavailable"
            if re.search(r"tim(?:e|ed)[ -]?out|network|connection|socket|dns", text, re.I):
                return "transport"
            return "transient"
    return None


def session_id_from_path(path: Path) -> Optional[str]:
    match = re.search(r"rollout-\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d-([0-9a-z-]+?)(?:\.jsonl|_[0-9a-z-]+\.jsonl)$", path.name, re.I)
    if match:
        return match.group(1)
    return None


def read_session_id(path: Path) -> Optional[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            first = json.loads(handle.readline())
        payload = first.get("payload", {})
        if first.get("type") == "session_meta":
            return payload.get("session_id") or payload.get("id")
    except (OSError, ValueError, TypeError):
        pass
    return session_id_from_path(path)


def list_rollouts() -> Iterable[Path]:
    if not SESSIONS_DIR.exists():
        return []
    return SESSIONS_DIR.glob("**/rollout-*.jsonl")


def b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def b64decode(value: str) -> bytes:
    try:
        return base64.b64decode(value.encode("ascii"))
    except (ValueError, TypeError):
        return b""


def initialize_file_state(state: Dict[str, Any], path: Path, at_end: bool = False) -> None:
    key = str(path)
    if key in state["files"]:
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    state["files"][key] = {
        "offset": size if at_end else 0,
        "pending": "",
        "session_id": read_session_id(path),
    }


def refresh_file_session_id(file_state: Dict[str, Any], path: Path) -> bool:
    """Keep persisted file ownership correct after a watcher restart.

    Rollout filenames identify the turn, while the first session_meta event
    identifies the conversation that owns it. The latter is the source of
    truth, and reading the first line is cheap even for large rollout files.
    """
    session_id = read_session_id(path)
    if not session_id or file_state.get("session_id") == session_id:
        return False
    previous = file_state.get("session_id")
    file_state["session_id"] = session_id
    logging.info("rollout ownership updated path=%s old_thread=%s new_thread=%s", path, previous, session_id)
    return True


def prune_orphaned_threads(state: Dict[str, Any], paths: Iterable[Path]) -> bool:
    """Drop pending work for conversations whose rollout files were archived."""
    live_threads = set()
    for path in paths:
        file_state = state["files"].get(str(path))
        if isinstance(file_state, dict):
            session_id = file_state.get("session_id")
            if session_id:
                live_threads.add(str(session_id))

    changed = False
    for thread_id, thread in state.get("threads", {}).items():
        if thread_id in live_threads or not isinstance(thread, dict):
            continue
        if thread.get("pending") is not None or thread.get("active"):
            logging.info("clearing archived/orphaned thread=%s", thread_id)
            thread["pending"] = None
            thread["active"] = False
            changed = True
    return changed


class IpcError(RuntimeError):
    pass


class WindowsNamedPipe:
    def __init__(self, path: str) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._handle = self._connect(path)

    def _connect(self, path: str) -> Any:
        ctypes = self._ctypes
        wintypes = self._wintypes
        if not self._kernel32.WaitNamedPipeW(path, 5000):
            raise IpcError("Codex Windows IPC pipe is not available")
        handle = self._kernel32.CreateFileW(
            path,
            wintypes.DWORD(0xC0000000),
            wintypes.DWORD(0),
            None,
            wintypes.DWORD(3),
            wintypes.DWORD(0),
            None,
        )
        invalid = ctypes.c_void_p(-1).value
        if handle == invalid:
            error = ctypes.get_last_error()
            raise IpcError(f"Codex Windows IPC pipe connection failed ({error})")
        return handle

    def write(self, data: bytes) -> None:
        ctypes = self._ctypes
        written = self._wintypes.DWORD(0)
        buffer = ctypes.create_string_buffer(data)
        if not self._kernel32.WriteFile(self._handle, buffer, len(data), ctypes.byref(written), None):
            raise IpcError("Codex Windows IPC pipe write failed")

    def read(self, size: int) -> bytes:
        ctypes = self._ctypes
        buffer = ctypes.create_string_buffer(size)
        read = self._wintypes.DWORD(0)
        if not self._kernel32.ReadFile(self._handle, buffer, size, ctypes.byref(read), None):
            error = ctypes.get_last_error()
            if error != 234:
                raise IpcError(f"Codex Windows IPC pipe read failed ({error})")
        return buffer.raw[: read.value]

    def close(self) -> None:
        if self._handle is not None:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class CodexIpc:
    def __init__(self, path: Path = SOCKET_PATH) -> None:
        self.path = path
        self.sock: Any = None
        self.client_id = "initializing-client"

    def __enter__(self) -> "CodexIpc":
        if os.name == "nt":
            self.sock = WindowsNamedPipe(str(self.path))
        else:
            if not self.path.exists():
                raise IpcError("Codex IPC socket is not available")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            try:
                sock.connect(str(self.path))
            except OSError as exc:
                sock.close()
                raise IpcError(str(exc)) from exc
            self.sock = sock
        response = self.request("initialize", {"clientType": "codex-auto-retry"}, 0)
        result = response.get("result")
        if not isinstance(result, dict) or not result.get("clientId"):
            raise IpcError("Codex IPC initialize returned no client id")
        self.client_id = str(result["clientId"])
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _send(self, value: Dict[str, Any]) -> None:
        if self.sock is None:
            raise IpcError("IPC socket is closed")
        body = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        frame = struct.pack("<I", len(body)) + body
        if os.name == "nt":
            self.sock.write(frame)
        else:
            self.sock.sendall(frame)

    def _recv_exact(self, size: int) -> bytes:
        if self.sock is None:
            raise IpcError("IPC socket is closed")
        chunks: List[bytes] = []
        remaining = size
        while remaining:
            try:
                if os.name == "nt":
                    chunk = self.sock.read(remaining)
                else:
                    chunk = self.sock.recv(remaining)
            except socket.timeout as exc:
                raise IpcError("IPC response timed out") from exc
            except OSError as exc:
                raise IpcError(str(exc)) from exc
            if not chunk:
                raise IpcError("IPC socket closed while reading response")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv(self) -> Dict[str, Any]:
        header = self._recv_exact(4)
        size = struct.unpack("<I", header)[0]
        if size > 32 * 1024 * 1024:
            raise IpcError("IPC response is too large")
        try:
            return json.loads(self._recv_exact(size).decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise IpcError("IPC response was not valid JSON") from exc

    def request(
        self,
        method: str,
        params: Dict[str, Any],
        version: int,
        target_client_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_id = str(uuid.uuid4())
        request: Dict[str, Any] = {
            "type": "request",
            "requestId": request_id,
            "sourceClientId": self.client_id,
            "version": version,
            "method": method,
            "params": params,
        }
        if target_client_id:
            request["targetClientId"] = target_client_id
        self._send(request)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            response = self._recv()
            if response.get("type") != "response" or response.get("requestId") != request_id:
                continue
            if response.get("resultType") == "error":
                raise IpcError(f"{method} returned an IPC error")
            return response
        raise IpcError(f"{method} returned no response")

    def start_empty_continuation(self, thread_id: str) -> Dict[str, Any]:
        owner = self.request(
            "thread-owner-discovery",
            {"hostId": "local", "conversationId": thread_id},
            1,
        )
        owner_id = owner.get("handledByClientId")
        if not owner_id:
            raise IpcError("Codex Desktop did not report a thread owner")
        return self.request(
            "thread-follower-start-turn",
            {
                "conversationId": thread_id,
                "turnStart": {
                    "request": {"threadId": thread_id, "input": []},
                    "context": {},
                },
            },
            2,
            str(owner_id),
        )


def backoff_delay(attempts: int) -> float:
    initial = float(SETTINGS["initial_backoff"])
    mode = SETTINGS["backoff_mode"]
    if mode == "fixed":
        delay = initial
    elif mode == "linear":
        delay = initial * (attempts + 1)
    else:
        delay = initial * (2 ** min(attempts, 6))
    return min(float(SETTINGS["max_backoff"]), delay)


def append_failure(thread: Dict[str, Any], failure_key: str, category: str, now: float) -> None:
    pending = thread.get("pending")
    if not isinstance(pending, dict) or pending.get("failure_key") != failure_key:
        attempts = int(pending.get("attempts", 0)) if isinstance(pending, dict) else 0
        first_at = float(pending.get("first_at", now)) if isinstance(pending, dict) else now
        if isinstance(pending, dict) and (
            pending.get("exhausted") or now - first_at > float(SETTINGS["max_chain_seconds"])
        ):
            attempts = 0
            first_at = now
        thread["pending"] = {
            "failure_key": failure_key,
            "category": category,
            "attempts": attempts,
            "first_at": first_at,
            "due_at": now + backoff_delay(attempts),
            "active": False,
        }
    else:
        pending["category"] = category
        pending["active"] = False
        pending["due_at"] = now + backoff_delay(int(pending.get("attempts", 0)))


def process_event(state: Dict[str, Any], path: Path, event: Dict[str, Any]) -> None:
    key = str(path)
    file_state = state["files"].setdefault(key, {"offset": 0, "pending": "", "session_id": None})
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        return
    if event.get("type") == "session_meta":
        file_state["session_id"] = payload.get("session_id") or payload.get("id")
        return
    thread_id = file_state.get("session_id")
    if not thread_id:
        return
    thread = state["threads"].setdefault(str(thread_id), {"pending": None, "active": False})
    event_type = payload.get("type")
    now = time.time()
    if event_type == "task_started":
        # A user-initiated retry after a chain was exhausted starts a new
        # chain instead of inheriting the expired first_at timestamp.
        if isinstance(thread.get("pending"), dict) and thread["pending"].get("exhausted"):
            thread["pending"] = None
        thread["active"] = True
        return
    if event_type == "turn_aborted":
        thread["active"] = False
        thread["pending"] = None
        return
    if event_type != "task_complete":
        return
    thread["active"] = False
    error = payload.get("error")
    if error is None:
        thread["pending"] = None
        return
    category = retry_category(error)
    if category is None:
        thread["pending"] = None
        return
    ordinal = event.get("ordinal", payload.get("turn_id", str(now)))
    append_failure(thread, f"{key}:{ordinal}", category, now)
    logging.info("retryable failure detected thread=%s category=%s", thread_id, category)


def consume_file(state: Dict[str, Any], path: Path) -> bool:
    key = str(path)
    if key not in state["files"]:
        initialize_file_state(state, path, at_end=False)
        return False
    file_state = state["files"][key]
    changed = refresh_file_session_id(file_state, path)
    try:
        size = path.stat().st_size
    except OSError:
        return False
    offset = int(file_state.get("offset", 0))
    if size < offset:
        offset = 0
        file_state["pending"] = ""
    try:
        with path.open("rb") as handle:
            handle.seek(offset)
            new_bytes = handle.read()
    except OSError:
        return False
    if not new_bytes:
        return False
    prefix = b64decode(str(file_state.get("pending", "")))
    data = prefix + new_bytes
    lines = data.split(b"\n")
    pending = lines.pop()
    consumed_new = len(data) - len(prefix) - len(pending)
    for raw_line in lines:
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            logging.debug("skipping malformed rollout line path=%s", path)
            continue
        if isinstance(event, dict):
            process_event(state, path, event)
            changed = True
    file_state["offset"] = offset + max(0, consumed_new)
    file_state["pending"] = b64encode(pending)
    return changed or consumed_new > 0


def dispatch_due(state: Dict[str, Any], dry_run: bool = False) -> bool:
    if not SETTINGS["enabled"]:
        return False
    changed = False
    now = time.time()
    for thread_id, thread in list(state["threads"].items()):
        pending = thread.get("pending")
        if not isinstance(pending, dict) or thread.get("active"):
            continue
        attempts = int(pending.get("attempts", 0))
        first_at = float(pending.get("first_at", now))
        due_at = float(pending.get("due_at", now))
        if now < due_at:
            continue
        if attempts >= int(SETTINGS["max_attempts"]):
            logging.warning("retry limit reached thread=%s attempts=%d", thread_id, attempts)
            pending["exhausted"] = True
            pending["due_at"] = now + float(SETTINGS["max_backoff"])
            changed = True
            continue
        if now - first_at > float(SETTINGS["max_chain_seconds"]):
            logging.warning("retry chain expired thread=%s", thread_id)
            pending["exhausted"] = True
            pending["due_at"] = now + float(SETTINGS["max_backoff"])
            changed = True
            continue
        if dry_run:
            logging.info("dry-run would retry thread=%s attempt=%d", thread_id, attempts + 1)
            pending["due_at"] = now + backoff_delay(attempts)
            changed = True
            continue
        try:
            with CodexIpc() as client:
                client.start_empty_continuation(str(thread_id))
        except IpcError as exc:
            delay = backoff_delay(attempts)
            pending["due_at"] = now + delay
            logging.info("retry postponed thread=%s reason=%s", thread_id, str(exc))
            changed = True
            continue
        pending["attempts"] = attempts + 1
        pending["active"] = True
        pending["due_at"] = now + float(SETTINGS["max_backoff"])
        thread["active"] = True
        logging.info("retry dispatched thread=%s attempt=%d", thread_id, attempts + 1)
        changed = True
    return changed


def watcher_loop(dry_run: bool = False) -> None:
    global SETTINGS
    lock_handle = acquire_watcher_lock()
    if lock_handle is None:
        logging.info("another watcher instance is already running")
        return
    state = load_state()
    for path in list_rollouts():
        if path.is_file():
            initialize_file_state(state, path, at_end=True)
    atomic_write_json(STATE_PATH, state)
    logging.info("watching %s", SESSIONS_DIR)
    try:
        while True:
            SETTINGS = load_settings()
            changed = False
            paths = [path for path in list_rollouts() if path.is_file()]
            for path in paths:
                if path.is_file():
                    changed = consume_file(state, path) or changed
            changed = prune_orphaned_threads(state, paths) or changed
            changed = dispatch_due(state, dry_run) or changed
            if changed:
                atomic_write_json(STATE_PATH, state)
            time.sleep(float(SETTINGS["poll_seconds"]))
    finally:
        lock_handle.close()


def check_ipc(thread_id: Optional[str]) -> int:
    try:
        with CodexIpc() as client:
            if thread_id:
                response = client.request(
                    "thread-owner-discovery",
                    {"hostId": "local", "conversationId": thread_id},
                    1,
                )
                print(json.dumps({"ok": True, "thread_id": thread_id, "owner": response.get("handledByClientId")}))
            else:
                print(json.dumps({"ok": True, "client_id": client.client_id}))
        return 0
    except IpcError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 1


def show_status() -> int:
    state = load_state()
    pending = []
    for thread_id, thread in state.get("threads", {}).items():
        item = thread.get("pending")
        if isinstance(item, dict) and not item.get("exhausted"):
            pending.append({"thread_id": thread_id, "attempts": item.get("attempts", 0), "category": item.get("category")})
    socket_exists = SOCKET_PATH.exists() if isinstance(SOCKET_PATH, Path) else None
    print(json.dumps({"socket": str(SOCKET_PATH), "socket_exists": socket_exists, "pending": pending}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Retry transient Codex Desktop turns on macOS")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--foreground", action="store_true", help="watch rollout files until stopped")
    group.add_argument("--dry-run", action="store_true", help="watch without sending IPC retries")
    group.add_argument("--check", action="store_true", help="check the Codex Desktop IPC connection")
    group.add_argument("--status", action="store_true", help="show watcher state")
    parser.add_argument("--thread", help="thread id for --check")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.check:
        return check_ipc(args.thread)
    if args.status:
        return show_status()
    setup_logging(foreground=args.foreground or args.dry_run)
    watcher_loop(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
