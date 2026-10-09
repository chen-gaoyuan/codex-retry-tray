# Codex Auto Retry

跨平台 Codex Desktop 自动重试工具：监听会话生命周期事件，在服务过载、限流、网络、超时和空响应等可恢复错误后，继续同一个 Codex 会话。

当前包含：

- macOS Apple Silicon 原生菜单栏 `.app`
- Windows x64 托盘 `.exe` 构建流程
- 固定、线性和指数退避策略
- 仅保存生命周期和重试状态，不保存会话正文、工具输入输出或凭据

## macOS

在 Apple Silicon Mac 上构建：

```sh
./scripts/build_macos.sh
```

输出在 `dist/macos/`。现在 watcher 已嵌入 `.app` 的 `Contents/Resources`，可以直接把 `.app` 拖到“应用程序”后双击运行。需要登录自动启动时再运行安装脚本：

```sh
./scripts/install_macos.sh
```

当前 Mac 版本使用 Codex Desktop 的本地 Unix IPC（`~/.codex/ipc/ipc.sock`）和菜单栏 AppKit UI。`.app` 启动时会自动启动内置 Python watcher，并使用锁文件防止重复启动。

## Windows

Windows 版使用 Codex Desktop 官方命名管道 `\\.\pipe\codex-ipc`，托盘 UI 使用 Python/pystray，设置窗口使用 Tkinter。

在 Windows PowerShell 中构建：

```powershell
.\scripts\build_windows.ps1
```

输出的 `dist\windows\CodexAutoRetry.exe` 是单文件程序。安装：

```powershell
.\scripts\install_windows.ps1
```

安装脚本只写入当前用户的 `%LOCALAPPDATA%` 和启动文件夹，不需要管理员权限。

也可以使用 GitHub Actions：`.github/workflows/build-windows.yml` 会在 Windows runner 上生成 x64 ZIP。当前 macOS 开发机无法直接运行 Windows EXE，因此 Windows 构建需要 Windows runner 或实际 Windows 主机。

## 默认设置

- 退避策略：线性增加
- 首次等待：5 秒
- 最大退避：120 秒
- 最大重试次数：15 次
- 故障链最长：30 分钟

设置文件位于各平台的 Codex Home 下的 `auto-retry/config.json`。

## 兼容性说明

Codex Desktop IPC 是桌面端内部接口，Codex 更新后可能改变版本或消息结构。启动时应先运行 `--check`（Windows 托盘中的“检查 IPC”也会执行）。
