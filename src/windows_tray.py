import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

import codex_auto_retry as retry


def read_config() -> Dict[str, Any]:
    try:
        with retry.CONFIG_PATH.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(values: Dict[str, Any]) -> None:
    retry.atomic_write_json(retry.CONFIG_PATH, values)


class WindowsTray:
    def __init__(self) -> None:
        import pystray
        from PIL import Image, ImageDraw

        self.pystray = pystray
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 60, 60), fill=(40, 125, 220, 255))
        draw.arc((15, 13, 49, 49), start=35, end=320, fill="white", width=6)
        draw.polygon([(45, 11), (53, 12), (49, 21)], fill="white")
        self.icon = pystray.Icon("Codex Auto Retry", image, "Codex 自动重试", self.make_menu())
        threading.Thread(target=self.watch_status, daemon=True).start()

    def is_enabled(self) -> bool:
        return bool(read_config().get("enabled", True))

    def pending_count(self) -> int:
        try:
            with retry.STATE_PATH.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
            threads = state.get("threads", {})
            return sum(1 for item in threads.values() if isinstance(item, dict) and isinstance(item.get("pending"), dict) and not item["pending"].get("exhausted"))
        except (OSError, ValueError, AttributeError):
            return 0

    def status_text(self) -> str:
        return f"{'运行中' if self.is_enabled() else '已暂停'} · 待重试 {self.pending_count()}"

    def make_menu(self):
        pystray = self.pystray
        return pystray.Menu(
            pystray.MenuItem(lambda item: self.status_text(), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("设置…", self.open_settings),
            pystray.MenuItem("暂停/启用自动重试", self.toggle_enabled),
            pystray.MenuItem("打开日志", self.open_log),
            pystray.MenuItem("检查 Codex IPC", self.check_ipc),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self.quit),
        )

    def watch_status(self) -> None:
        while True:
            try:
                self.icon.update_menu()
            except Exception:
                pass
            threading.Event().wait(2.0)

    def toggle_enabled(self, icon, item) -> None:
        values = read_config()
        values["enabled"] = not self.is_enabled()
        save_config(values)
        self.icon.update_menu()

    def open_log(self, icon, item) -> None:
        os.startfile(str(retry.LOG_PATH))

    def check_ipc(self, icon, item) -> None:
        import tkinter as tk
        from tkinter import messagebox

        result = retry.check_ipc(None)
        messagebox.showinfo("Codex IPC", "连接成功" if result == 0 else "连接失败，请检查 Codex Desktop 是否运行。")

    def open_settings(self, icon, item) -> None:
        threading.Thread(target=show_settings, daemon=True).start()

    def quit(self, icon, item) -> None:
        icon.stop()


def show_settings() -> None:
    import tkinter as tk
    from tkinter import messagebox, ttk

    values = read_config()
    root = tk.Tk()
    root.title("Codex 自动重试设置")
    root.geometry("430x330")
    root.resizable(False, False)

    enabled = tk.BooleanVar(value=bool(values.get("enabled", True)))
    mode = tk.StringVar(value=str(values.get("backoff_mode", "linear")))
    entries: Dict[str, tk.StringVar] = {
        "max_attempts": tk.StringVar(value=str(values.get("max_attempts", 15))),
        "max_chain_minutes": tk.StringVar(value=str(float(values.get("max_chain_seconds", 1800)) / 60).rstrip("0").rstrip(".")),
        "initial_backoff": tk.StringVar(value=str(values.get("initial_backoff", 5))),
        "max_backoff": tk.StringVar(value=str(values.get("max_backoff", 120))),
        "poll_seconds": tk.StringVar(value=str(values.get("poll_seconds", 1))),
    }

    ttk.Checkbutton(root, text="启用自动重试", variable=enabled).grid(row=0, column=0, columnspan=2, sticky="w", padx=24, pady=(18, 10))
    rows = [
        ("最大重试次数", "max_attempts"),
        ("最长故障链（分钟）", "max_chain_minutes"),
        ("首次等待（秒）", "initial_backoff"),
        ("最大退避（秒）", "max_backoff"),
        ("扫描间隔（秒）", "poll_seconds"),
    ]
    for index, (label, key) in enumerate(rows, start=1):
        ttk.Label(root, text=label).grid(row=index, column=0, sticky="w", padx=24, pady=6)
        ttk.Entry(root, textvariable=entries[key], width=18).grid(row=index, column=1, sticky="e", padx=24, pady=6)
    ttk.Label(root, text="退避策略").grid(row=6, column=0, sticky="w", padx=24, pady=6)
    ttk.Combobox(root, textvariable=mode, values=["fixed", "linear", "exponential"], state="readonly", width=15).grid(row=6, column=1, sticky="e", padx=24, pady=6)

    def save() -> None:
        try:
            max_attempts = int(entries["max_attempts"].get())
            max_chain_minutes = float(entries["max_chain_minutes"].get())
            initial_backoff = float(entries["initial_backoff"].get())
            max_backoff = float(entries["max_backoff"].get())
            poll_seconds = float(entries["poll_seconds"].get())
            if max_attempts < 1 or max_chain_minutes < 1 or initial_backoff < 1 or max_backoff < initial_backoff or poll_seconds < 0.2:
                raise ValueError
        except ValueError:
            messagebox.showerror("设置无效", "请填写有效数字。")
            return
        save_config({
            "enabled": enabled.get(),
            "backoff_mode": mode.get(),
            "max_attempts": max_attempts,
            "max_chain_seconds": max_chain_minutes * 60,
            "initial_backoff": initial_backoff,
            "max_backoff": max_backoff,
            "poll_seconds": poll_seconds,
        })
        root.destroy()

    buttons = ttk.Frame(root)
    buttons.grid(row=7, column=0, columnspan=2, sticky="e", padx=24, pady=(16, 20))
    ttk.Button(buttons, text="取消", command=root.destroy).pack(side="left", padx=5)
    ttk.Button(buttons, text="保存", command=save).pack(side="left", padx=5)
    root.mainloop()


def main() -> None:
    retry.ensure_dirs()
    retry.setup_logging()
    threading.Thread(target=retry.watcher_loop, daemon=True).start()
    WindowsTray().icon.run()


if __name__ == "__main__":
    main()
