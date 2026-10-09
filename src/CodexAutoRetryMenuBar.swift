import Cocoa
import Darwin

private let label = "com.openai.codex-auto-retry"
private let retryPlist = NSString(string: "~/Library/LaunchAgents/com.openai.codex-auto-retry.plist").expandingTildeInPath
private let retryScript = NSString(string: "~/.codex/auto-retry/codex-auto-retry.py").expandingTildeInPath
private let statePath = NSString(string: "~/.codex/auto-retry/state.json").expandingTildeInPath
private let configPath = NSString(string: "~/.codex/auto-retry/config.json").expandingTildeInPath
private let logPath = NSString(string: "~/.codex/auto-retry/watcher.log").expandingTildeInPath

private struct Pending: Decodable {
    let exhausted: Bool?
    let attempts: Int?
}

private struct ThreadState: Decodable {
    let pending: Pending?
    let active: Bool?
}

private struct RetryState: Decodable {
    let threads: [String: ThreadState]?
}

private final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private let stateItem = NSMenuItem(title: "正在读取状态…", action: nil, keyEquivalent: "")
    private let toggleItem = NSMenuItem(title: "暂停自动重试", action: #selector(toggleService), keyEquivalent: "")
    private var serviceRunning = false
    private var timer: Timer?
    private var settingsWindow: NSWindow?
    private var enabledCheckbox: NSButton?
    private var maxAttemptsField: NSTextField?
    private var maxChainMinutesField: NSTextField?
    private var initialBackoffField: NSTextField?
    private var maxBackoffField: NSTextField?
    private var pollSecondsField: NSTextField?
    private var backoffModePopup: NSPopUpButton?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        configureStatusItem()
        configureMenu()
        refresh()
        timer = Timer.scheduledTimer(timeInterval: 2.0, target: self, selector: #selector(refresh), userInfo: nil, repeats: true)
    }

    func applicationWillTerminate(_ notification: Notification) {
        timer?.invalidate()
    }

    private func configureStatusItem() {
        guard let button = statusItem.button else { return }
        if #available(macOS 11.0, *) {
            button.image = NSImage(systemSymbolName: "arrow.clockwise.circle", accessibilityDescription: "Codex 自动重试")
        } else {
            button.title = "↻"
        }
        button.toolTip = "Codex 自动重试"
    }

    private func configureMenu() {
        let titleItem = NSMenuItem(title: "Codex 自动重试", action: nil, keyEquivalent: "")
        titleItem.isEnabled = false
        menu.addItem(titleItem)
        menu.addItem(.separator())
        stateItem.isEnabled = false
        menu.addItem(stateItem)
        menu.addItem(toggleItem)
        menu.addItem(NSMenuItem(title: "设置…", action: #selector(showSettings), keyEquivalent: ","))
        menu.addItem(NSMenuItem(title: "检查 Codex IPC", action: #selector(checkIPC), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "打开日志", action: #selector(openLog), keyEquivalent: ""))
        menu.addItem(NSMenuItem(title: "打开配置目录", action: #selector(openDirectory), keyEquivalent: ""))
        menu.addItem(.separator())
        menu.addItem(NSMenuItem(title: "退出菜单栏应用", action: #selector(quit), keyEquivalent: "q"))
        for item in menu.items {
            item.target = self
        }
        statusItem.menu = menu
    }

    @objc private func refresh() {
        serviceRunning = launchctlPrint().contains("state = running")
        let state = readState()
        let pending = state?.threads?.values.filter { item in
            guard let pending = item.pending else { return false }
            return pending.exhausted != true
        }.count ?? 0
        let active = state?.threads?.values.filter { $0.active == true }.count ?? 0
        if serviceRunning {
            stateItem.title = "后台服务运行中 · 待重试 \(pending) · 活跃 \(active)"
            toggleItem.title = "暂停自动重试"
        } else {
            stateItem.title = "后台服务已暂停 · 待重试 \(pending)"
            toggleItem.title = "启用自动重试"
        }
        toggleItem.isEnabled = FileManager.default.fileExists(atPath: retryPlist)
    }

    private func readState() -> RetryState? {
        guard let data = FileManager.default.contents(atPath: statePath) else { return nil }
        return try? JSONDecoder().decode(RetryState.self, from: data)
    }

    private func launchctlPrint() -> String {
        runProcess("/bin/launchctl", arguments: ["print", "gui/\(getuid())/\(label)"]).stdout
    }

    @objc private func toggleService() {
        if serviceRunning {
            _ = runProcess("/bin/launchctl", arguments: ["bootout", "gui/\(getuid())/\(label)"])
        } else {
            _ = runProcess("/bin/launchctl", arguments: ["bootstrap", "gui/\(getuid())", retryPlist])
            _ = runProcess("/bin/launchctl", arguments: ["kickstart", "-k", "gui/\(getuid())/\(label)"])
        }
        refresh()
    }

    @objc private func showSettings() {
        if let window = settingsWindow {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 460, height: 390), styleMask: [.titled, .closable], backing: .buffered, defer: false)
        window.title = "Codex 自动重试设置"
        window.isReleasedWhenClosed = false
        let content = NSView(frame: NSRect(x: 0, y: 0, width: 460, height: 390))
        window.contentView = content

        let enabled = NSButton(checkboxWithTitle: "启用自动重试", target: nil, action: nil)
        enabled.frame = NSRect(x: 28, y: 332, width: 200, height: 24)
        enabled.state = serviceRunning ? .on : .off
        content.addSubview(enabled)
        enabledCheckbox = enabled

        let maxAttempts = makeField()
        let maxChain = makeField()
        let initialBackoff = makeField()
        let maxBackoff = makeField()
        let pollSeconds = makeField()
        let modePopup = NSPopUpButton(frame: NSRect(x: 270, y: 0, width: 130, height: 26), pullsDown: false)
        modePopup.addItems(withTitles: ["固定等待", "线性增加", "指数翻倍"])
        addRow(to: content, label: "最大重试次数", field: maxAttempts, y: 282)
        addRow(to: content, label: "最长故障链（分钟）", field: maxChain, y: 244)
        addRow(to: content, label: "退避策略", field: modePopup, y: 206)
        addRow(to: content, label: "首次等待（秒）", field: initialBackoff, y: 168)
        addRow(to: content, label: "最大退避（秒）", field: maxBackoff, y: 130)
        addRow(to: content, label: "扫描间隔（秒）", field: pollSeconds, y: 92)
        maxAttemptsField = maxAttempts
        maxChainMinutesField = maxChain
        initialBackoffField = initialBackoff
        maxBackoffField = maxBackoff
        pollSecondsField = pollSeconds
        backoffModePopup = modePopup
        loadConfig(into: enabled, maxAttempts: maxAttempts, maxChainMinutes: maxChain, modePopup: modePopup, initialBackoff: initialBackoff, maxBackoff: maxBackoff, pollSeconds: pollSeconds)

        let cancel = NSButton(title: "取消", target: self, action: #selector(cancelSettings))
        cancel.frame = NSRect(x: 270, y: 22, width: 78, height: 30)
        content.addSubview(cancel)
        let save = NSButton(title: "保存", target: self, action: #selector(saveSettings))
        save.frame = NSRect(x: 360, y: 22, width: 78, height: 30)
        save.keyEquivalent = "\r"
        content.addSubview(save)

        settingsWindow = window
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func makeField() -> NSTextField {
        let field = NSTextField(string: "")
        field.alignment = .right
        field.isEditable = true
        field.isSelectable = true
        field.bezelStyle = .roundedBezel
        field.frame = NSRect(x: 270, y: 0, width: 130, height: 26)
        return field
    }

    private func addRow(to content: NSView, label: String, field: NSView, y: CGFloat) {
        let labelField = NSTextField(labelWithString: label)
        labelField.frame = NSRect(x: 28, y: y + 4, width: 225, height: 22)
        field.frame.origin.y = y
        content.addSubview(labelField)
        content.addSubview(field)
    }

    private func loadConfig(into enabled: NSButton, maxAttempts: NSTextField, maxChainMinutes: NSTextField, modePopup: NSPopUpButton, initialBackoff: NSTextField, maxBackoff: NSTextField, pollSeconds: NSTextField) {
        var values: [String: Any] = [:]
        if let data = FileManager.default.contents(atPath: configPath), let object = try? JSONSerialization.jsonObject(with: data), let dictionary = object as? [String: Any] {
            values = dictionary
        }
        let enabledValue = (values["enabled"] as? NSNumber)?.boolValue ?? serviceRunning
        enabled.state = enabledValue ? .on : .off
        maxAttempts.stringValue = formattedNumber(numeric(values["max_attempts"], fallback: 15))
        maxChainMinutes.stringValue = formattedNumber(numeric(values["max_chain_seconds"], fallback: 1800) / 60)
        let mode = values["backoff_mode"] as? String ?? "linear"
        modePopup.selectItem(withTitle: mode == "fixed" ? "固定等待" : mode == "linear" ? "线性增加" : "指数翻倍")
        initialBackoff.stringValue = formattedNumber(numeric(values["initial_backoff"], fallback: 5))
        maxBackoff.stringValue = formattedNumber(numeric(values["max_backoff"], fallback: 120))
        pollSeconds.stringValue = formattedNumber(numeric(values["poll_seconds"], fallback: 1))
    }

    private func numeric(_ value: Any?, fallback: Double) -> Double {
        guard let number = value as? NSNumber else { return fallback }
        return number.doubleValue
    }

    private func formattedNumber(_ value: Double) -> String {
        if value.rounded() == value {
            return String(Int(value))
        }
        return String(value)
    }

    @objc private func cancelSettings() {
        settingsWindow?.close()
    }

    @objc private func saveSettings() {
        guard let enabledCheckbox, let maxAttemptsField, let maxChainMinutesField, let backoffModePopup, let initialBackoffField, let maxBackoffField, let pollSecondsField,
              let maxAttempts = Int(maxAttemptsField.stringValue),
              let maxChainMinutes = Double(maxChainMinutesField.stringValue),
              let initialBackoff = Double(initialBackoffField.stringValue),
              let maxBackoff = Double(maxBackoffField.stringValue),
              let pollSeconds = Double(pollSecondsField.stringValue),
              maxAttempts > 0, maxChainMinutes >= 1, initialBackoff >= 1, maxBackoff >= initialBackoff, pollSeconds >= 0.2 else {
            showAlert(title: "设置无效", message: "请填写有效数字：重试次数大于 0，等待时间至少 1 秒，扫描间隔至少 0.2 秒。")
            return
        }
        let values: [String: Any] = [
            "enabled": enabledCheckbox.state == .on,
            "max_attempts": maxAttempts,
            "max_chain_seconds": maxChainMinutes * 60,
            "backoff_mode": backoffModePopup.titleOfSelectedItem == "固定等待" ? "fixed" : backoffModePopup.titleOfSelectedItem == "线性增加" ? "linear" : "exponential",
            "initial_backoff": initialBackoff,
            "max_backoff": maxBackoff,
            "poll_seconds": pollSeconds,
        ]
        guard JSONSerialization.isValidJSONObject(values), let data = try? JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys]) else {
            showAlert(title: "保存失败", message: "无法生成配置文件。")
            return
        }
        do {
            try data.write(to: URL(fileURLWithPath: configPath), options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: configPath)
        } catch {
            showAlert(title: "保存失败", message: error.localizedDescription)
            return
        }
        let shouldRun = enabledCheckbox.state == .on
        if shouldRun != serviceRunning {
            toggleService()
        } else {
            refresh()
        }
        settingsWindow?.close()
    }

    @objc private func checkIPC() {
        let result = runProcess("/usr/bin/python3", arguments: [retryScript, "--check"])
        let text = result.stdout.isEmpty ? result.stderr : result.stdout
        showAlert(title: result.status == 0 ? "Codex IPC 正常" : "Codex IPC 检查失败", message: text.trimmingCharacters(in: .whitespacesAndNewlines))
    }

    @objc private func openLog() {
        NSWorkspace.shared.open(URL(fileURLWithPath: logPath))
    }

    @objc private func openDirectory() {
        NSWorkspace.shared.open(URL(fileURLWithPath: (statePath as NSString).deletingLastPathComponent))
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    private func showAlert(title: String, message: String) {
        let alert = NSAlert()
        alert.alertStyle = .informational
        alert.messageText = title
        alert.informativeText = message.isEmpty ? "无详细信息" : message
        alert.addButton(withTitle: "好")
        alert.runModal()
    }

    private func runProcess(_ executable: String, arguments: [String]) -> (status: Int32, stdout: String, stderr: String) {
        let process = Process()
        let outPipe = Pipe()
        let errPipe = Pipe()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.standardOutput = outPipe
        process.standardError = errPipe
        do {
            try process.run()
            process.waitUntilExit()
            let out = String(data: outPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            let err = String(data: errPipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
            return (process.terminationStatus, out, err)
        } catch {
            return (1, "", error.localizedDescription)
        }
    }
}

let app = NSApplication.shared
private let delegate = AppDelegate()
app.delegate = delegate
app.run()
