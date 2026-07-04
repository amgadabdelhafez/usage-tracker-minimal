import SwiftUI
import ServiceManagement

// MARK: - Settings Keys

enum SettingsKey {
    static let iconMode = "iconMode"
    static let showClaudeSession = "showClaudeSession"
    static let showClaudeWeekly = "showClaudeWeekly"
    static let showCodexSession = "showCodexSession"
    static let showCodexWeekly = "showCodexWeekly"
    static let apiURL = "apiURL"
    static let apiToken = "apiToken"
    static let refreshInterval = "refreshInterval"
    static let launchAtLogin = "launchAtLogin"
}

// MARK: - Settings View

struct SettingsView: View {
    @ObservedObject var vm: UsageViewModel

    @AppStorage(SettingsKey.iconMode) private var iconMode = "Thermometer"
    @AppStorage(SettingsKey.showClaudeSession) private var showClaudeSession = true
    @AppStorage(SettingsKey.showClaudeWeekly) private var showClaudeWeekly = true
    @AppStorage(SettingsKey.showCodexSession) private var showCodexSession = true
    @AppStorage(SettingsKey.showCodexWeekly) private var showCodexWeekly = true

    @AppStorage(SettingsKey.refreshInterval) private var refreshInterval = 30.0
    @AppStorage(SettingsKey.launchAtLogin) private var launchAtLogin = false

    var body: some View {
        Form {
            Section {
                Picker("Icon Style", selection: $iconMode) {
                    ForEach(MenuBarIconMode.allCases) { mode in
                        Text(mode.rawValue).tag(mode.rawValue)
                    }
                }

                VStack(alignment: .leading, spacing: 6) {
                    Text("Visible Bars")
                        .foregroundStyle(.secondary)
                        .font(.system(.subheadline))
                    HStack(spacing: 16) {
                        VStack(alignment: .leading, spacing: 4) {
                            Toggle("Claude Session", isOn: $showClaudeSession)
                            Toggle("Claude Weekly", isOn: $showClaudeWeekly)
                        }
                        VStack(alignment: .leading, spacing: 4) {
                            Toggle("Codex Session", isOn: $showCodexSession)
                            Toggle("Codex Weekly", isOn: $showCodexWeekly)
                        }
                    }
                    .toggleStyle(.checkbox)
                }
            } header: {
                Label("Appearance", systemImage: "paintbrush")
            }

            Section {
                HStack {
                    Text("Refresh")
                    Slider(value: $refreshInterval, in: 10...120, step: 5)
                    Text("\(Int(refreshInterval))s")
                        .font(.system(.body, design: .monospaced))
                        .foregroundStyle(.secondary)
                        .frame(width: 36, alignment: .trailing)
                }

                Toggle("Launch at Login", isOn: $launchAtLogin)
                    .onChange(of: launchAtLogin) { _, newValue in
                        setLaunchAtLogin(newValue)
                    }
            } header: {
                Label("General", systemImage: "gearshape")
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
        .frame(width: 480, height: 380)
        .onAppear { loadFromVM() }
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Done") { applyAndClose() }
            }
        }
    }

    // MARK: - Actions

    private func loadFromVM() {
        iconMode = vm.iconMode.rawValue
    }

    private func applyAndClose() {
        vm.applySettings(
            iconMode: MenuBarIconMode(rawValue: iconMode) ?? .thermometer,
            refreshInterval: refreshInterval,
            visibleBars: VisibleBars(
                claudeSession: showClaudeSession,
                claudeWeekly: showClaudeWeekly,
                codexSession: showCodexSession,
                codexWeekly: showCodexWeekly
            )
        )
        SettingsWindowController.shared.close()
    }

    private func setLaunchAtLogin(_ enabled: Bool) {
        if #available(macOS 13.0, *) {
            do {
                if enabled {
                    try SMAppService.mainApp.register()
                } else {
                    try SMAppService.mainApp.unregister()
                }
            } catch {}
        }
    }
}

// MARK: - Visible Bars Config

struct VisibleBars {
    var claudeSession: Bool
    var claudeWeekly: Bool
    var codexSession: Bool
    var codexWeekly: Bool
}

// MARK: - Settings Window Controller

class SettingsWindowController {
    static let shared = SettingsWindowController()
    private var window: NSWindow?

    func open(vm: UsageViewModel) {
        if let window = window, window.isVisible {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate(ignoringOtherApps: true)
            return
        }

        let view = SettingsView(vm: vm)
        let hostingView = NSHostingView(rootView: view)

        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 480, height: 380),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        w.title = "Settings"
        w.contentView = hostingView
        w.center()
        w.isReleasedWhenClosed = false
        w.level = .floating
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        self.window = w
    }

    func close() {
        window?.close()
    }
}
