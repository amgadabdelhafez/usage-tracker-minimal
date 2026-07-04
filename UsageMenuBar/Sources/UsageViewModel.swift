import Foundation
import SwiftUI

@MainActor
class UsageViewModel: ObservableObject {
    @Published var stats: UsageStats?
    @Published var weeklyForecast: WeeklyForecastResponse?
    @Published var isOffline = true
    @Published var showWeeklyView = false
    @Published var iconMode: MenuBarIconMode = .thermometer
    @Published var selectedTool: String = "claude"
    @Published var visibleBars = VisibleBars(claudeSession: true, claudeWeekly: true, codexSession: true, codexWeekly: true)

    private var timer: Timer?
    private var apiBaseURL: String
    private var apiToken: String
    private var refreshInterval: TimeInterval
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.connectionProxyDictionary = [:]  // bypass any proxy
        config.timeoutIntervalForRequest = 30
        config.timeoutIntervalForResource = 30
        return URLSession(configuration: config)
    }()

    init() {
        // Load persisted settings
        let defaults = UserDefaults.standard
        let storedURL = defaults.string(forKey: SettingsKey.apiURL)
        apiBaseURL = (storedURL?.isEmpty == false) ? storedURL! : "http://localhost:8000"

        let storedToken = defaults.string(forKey: SettingsKey.apiToken)
        apiToken = (storedToken?.isEmpty == false) ? storedToken! : Self.tokenFromConfigFile()

        refreshInterval = defaults.double(forKey: SettingsKey.refreshInterval)
        if refreshInterval < 10 { refreshInterval = 30 }

        if let stored = defaults.string(forKey: SettingsKey.iconMode),
           let mode = MenuBarIconMode(rawValue: stored) {
            iconMode = mode
        }

        visibleBars = VisibleBars(
            claudeSession: defaults.object(forKey: SettingsKey.showClaudeSession) as? Bool ?? true,
            claudeWeekly: defaults.object(forKey: SettingsKey.showClaudeWeekly) as? Bool ?? true,
            codexSession: defaults.object(forKey: SettingsKey.showCodexSession) as? Bool ?? true,
            codexWeekly: defaults.object(forKey: SettingsKey.showCodexWeekly) as? Bool ?? true
        )

        fetch()
        startTimer()
    }

    func applySettings(iconMode: MenuBarIconMode, refreshInterval: TimeInterval, visibleBars: VisibleBars) {
        self.iconMode = iconMode
        self.refreshInterval = refreshInterval
        self.visibleBars = visibleBars
        startTimer()
        fetch()
    }

    private func startTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: refreshInterval, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.fetch()
            }
        }
    }

    private static func tokenFromConfigFile() -> String {
        if let env = ProcessInfo.processInfo.environment["USAGE_TRACKER_SECRET"] {
            return env
        }
        let configPath = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".usage-tracker/config")
        if let contents = try? String(contentsOf: configPath, encoding: .utf8) {
            for line in contents.split(separator: "\n") {
                let trimmed = line.trimmingCharacters(in: .whitespaces)
                if trimmed.hasPrefix("USAGE_TRACKER_SECRET=") {
                    return String(trimmed.dropFirst("USAGE_TRACKER_SECRET=".count))
                }
            }
        }
        return ""
    }

    func fetch() {
        Task {
            let logFile = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".usage-menubar.log")
            func log(_ msg: String) {
                let line = "\(Date()): \(msg)\n"
                if let fh = try? FileHandle(forWritingTo: logFile) {
                    fh.seekToEndOfFile()
                    fh.write(line.data(using: .utf8)!)
                    fh.closeFile()
                } else {
                    try? line.write(to: logFile, atomically: true, encoding: .utf8)
                }
            }
            guard let url = URL(string: "\(apiBaseURL)/stats") else {
                log("Invalid API URL: \(apiBaseURL)")
                self.isOffline = true
                return
            }
            do {
                var request = URLRequest(url: url)
                request.timeoutInterval = 30
                if !apiToken.isEmpty {
                    request.setValue("Bearer \(apiToken)", forHTTPHeaderField: "Authorization")
                }
                let (data, _) = try await session.data(for: request)
                self.stats = try JSONDecoder().decode(UsageStats.self, from: data)
                self.isOffline = false
                log("OK: session=\(self.stats?.claudeQuota?.sessionUsedPct ?? -1)")

                // Fetch weekly forecast in parallel
                if let weeklyURL = URL(string: "\(apiBaseURL)/budget/weekly") {
                    var weeklyReq = URLRequest(url: weeklyURL)
                    weeklyReq.timeoutInterval = 10
                    if !apiToken.isEmpty {
                        weeklyReq.setValue("Bearer \(apiToken)", forHTTPHeaderField: "Authorization")
                    }
                    if let (wData, _) = try? await session.data(for: weeklyReq),
                       let forecast = try? JSONDecoder().decode(WeeklyForecastResponse.self, from: wData) {
                        self.weeklyForecast = forecast
                    }
                }
            } catch {
                log("API error: \(error)")
                self.isOffline = true
            }
        }
    }

    // MARK: - Reset Time Helpers

    func formatResetDisplay(for resetStr: String?) -> String? {
        ResetTimeFormatter.formatResetDisplay(resetStr)
    }
}
