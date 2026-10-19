import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {
    var backend: Process?
    var log: FileHandle?
    let data = ProcessInfo.processInfo.environment["CHATSEEK_DATA_DIR"].map { URL(fileURLWithPath: $0) }
        ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/ChatSeek")
    func applicationDidFinishLaunching(_ notification: Notification) {
        let menu = NSMenu()
        let item = NSMenuItem(); menu.addItem(item)
        let appMenu = NSMenu(); item.submenu = appMenu
        appMenu.addItem(withTitle: "打开 ChatSeek", action: #selector(openBrowser), keyEquivalent: "o").target = self
        appMenu.addItem(withTitle: "在 Finder 中显示数据", action: #selector(showData), keyEquivalent: "").target = self
        appMenu.addItem(withTitle: "清除本机聊天数据…", action: #selector(clearData), keyEquivalent: "").target = self
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "退出 ChatSeek", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        NSApp.mainMenu = menu
        launchBackend()
    }
    func launchBackend() {
        do {
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let logURL = data.appendingPathComponent("desktop.log")
            FileManager.default.createFile(atPath: logURL.path, contents: nil, attributes: [.posixPermissions: 0o600])
            log = try FileHandle(forWritingTo: logURL)
            let process = Process()
            process.executableURL = Bundle.main.resourceURL!.appendingPathComponent("runtime/ChatSeekRuntime")
            process.arguments = ["--chatseek-desktop"]
            if ProcessInfo.processInfo.environment["CHATSEEK_NO_BROWSER"] == "1" { process.arguments?.append("--no-browser") }
            var environment = ProcessInfo.processInfo.environment
            environment["CHATSEEK_DATA_DIR"] = data.path
            environment.removeValue(forKey: "DEEPSEEK_API_KEY")
            environment.removeValue(forKey: "LLM_API_KEY")
            process.environment = environment
            process.standardOutput = log; process.standardError = log
            process.terminationHandler = { process in
                DispatchQueue.main.async {
                    if process.terminationStatus != 0 {
                        let alert = NSAlert(); alert.messageText = "ChatSeek 启动失败"
                        alert.informativeText = "请检查本机数据目录中的 desktop.log，或联系内测管理员。"
                        alert.runModal()
                    }
                    NSApp.terminate(nil)
                }
            }
            try process.run(); backend = process
        } catch {
            let alert = NSAlert(); alert.messageText = "ChatSeek 无法启动"; alert.informativeText = "请将完整应用复制到“应用程序”后重试。"; alert.runModal()
            NSApp.terminate(nil)
        }
    }
    @objc func openBrowser() {
        if let content = try? Data(contentsOf: data.appendingPathComponent("desktop.json")),
           let json = try? JSONSerialization.jsonObject(with: content) as? [String: Any],
           let value = json["url"] as? String, let url = URL(string: value) {
            NSWorkspace.shared.open(url)
        }
    }
    @objc func showData() { NSWorkspace.shared.open(data) }
    @objc func clearData() {
        let alert = NSAlert()
        alert.messageText = "清除 ChatSeek 本机聊天数据？"
        alert.informativeText = "将删除聊天索引、问答历史和媒体缓存。保留微信原始数据库、初始化密钥、公共模型和内测凭证。此操作无法撤销。"
        alert.addButton(withTitle: "取消")
        alert.addButton(withTitle: "清除数据")
        if alert.runModal() != .alertSecondButtonReturn { return }
        let previous = backend
        previous?.terminationHandler = nil
        if previous?.isRunning == true { previous?.terminate() }
        DispatchQueue.global().async {
            previous?.waitUntilExit()
            let cleaner = Process()
            cleaner.executableURL = Bundle.main.resourceURL!.appendingPathComponent("runtime/ChatSeekRuntime")
            cleaner.arguments = ["--chatseek-clear-data", "--confirm"]
            var environment = ProcessInfo.processInfo.environment
            environment["CHATSEEK_DATA_DIR"] = self.data.path
            cleaner.environment = environment
            do {
                try cleaner.run(); cleaner.waitUntilExit()
                DispatchQueue.main.async { self.launchBackend() }
            } catch {
                DispatchQueue.main.async {
                    let failure = NSAlert(); failure.messageText = "清理未完成，请检查数据目录。"; failure.runModal()
                    self.launchBackend()
                }
            }
        }
    }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { openBrowser(); return true }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let process = backend, process.isRunning {
            process.terminationHandler = { _ in DispatchQueue.main.async { NSApp.reply(toApplicationShouldTerminate: true) } }
            process.terminate()
            return .terminateLater
        }
        return .terminateNow
    }
}
let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
