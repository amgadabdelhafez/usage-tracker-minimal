import SwiftUI

@main
struct UsageMenuBarApp: App {
    @StateObject private var vm = UsageViewModel()

    init() {
        Sentinel.shared.start()
    }

    var body: some Scene {
        MenuBarExtra {
            MenuContent(vm: vm)
        } label: {
            MenuBarLabel(vm: vm)
        }
        .menuBarExtraStyle(.window)
    }
}

// Menu bar icon mode — toggle between designs
enum MenuBarIconMode: String, CaseIterable, Identifiable {
    case reactor = "Reactor Irises"
    case eclipse = "Eclipse Orbitals"
    case thermometer = "Thermometer"
    case grid = "Breathing Grid"
    var id: String { rawValue }
}

struct MenuBarLabel: View {
    @ObservedObject var vm: UsageViewModel

    var body: some View {
        // Force SwiftUI to re-evaluate when stats change
        let _ = vm.stats?.claudeQuota?.sessionUsedPct
        let _ = vm.stats?.claudeQuota?.weeklyUsedPct
        let _ = vm.stats?.codexQuota?.sessionUsedPct
        let _ = vm.isOffline

        if let img = renderIcon() {
            Image(nsImage: img)
        } else {
            Text("⏣")
        }
    }

    private func renderIcon() -> NSImage? {
        guard let s = vm.stats, !vm.isOffline else { return nil }

        let claudeSession = vm.visibleBars.claudeSession ? (s.claudeQuota?.sessionUsedPct.map { $0 / 100 } ?? 0) : 0
        let claudeWeekly = vm.visibleBars.claudeWeekly ? s.claudeQuota?.weeklyUsedPct.map { $0 / 100 } : nil
        let codexSession = vm.visibleBars.codexSession ? (s.codexQuota?.sessionUsedPct.map { $0 / 100 } ?? 0) : 0
        let codexWeekly = vm.visibleBars.codexWeekly ? s.codexQuota?.weeklyUsedPct.map { $0 / 100 } : nil

        let content: AnyView
        switch vm.iconMode {
        case .reactor:
            content = AnyView(ReactorIrisIcon(cS: claudeSession, cW: claudeWeekly, xS: codexSession, xW: codexWeekly))
        case .eclipse:
            content = AnyView(EclipseOrbitalIcon(cS: claudeSession, cW: claudeWeekly, xS: codexSession, xW: codexWeekly))
        case .thermometer:
            content = AnyView(
                ThermometerIcon(
                    claudeSession: claudeSession, claudeWeekly: claudeWeekly,
                    codexSession: codexSession, codexWeekly: codexWeekly
                )
                .background(Color.clear)
            )
        case .grid:
            content = AnyView(BreathingGridIcon(
                claudeSession: claudeSession, claudeWeekly: claudeWeekly,
                codexSession: codexSession, codexWeekly: codexWeekly
            ))
        }

        let renderer = ImageRenderer(content: content)
        renderer.scale = NSScreen.main?.backingScaleFactor ?? 2.0
        guard let cgImage = renderer.cgImage else { return nil }
        let nsImage = NSImage(cgImage: cgImage, size: NSSize(
            width: cgImage.width / Int(renderer.scale),
            height: cgImage.height / Int(renderer.scale)
        ))
        nsImage.isTemplate = false
        return nsImage
    }
}

// MARK: — Shared color logic

private func usageColor(_ pct: Double, dark: Bool = true) -> Color {
    if pct >= 0.85 {
        return dark ? Color(red: 1, green: 0.40, blue: 0.36)   // #FF655C
                    : Color(red: 0.71, green: 0.19, blue: 0.17) // #B6312B
    }
    if pct >= 0.60 {
        return dark ? Color(red: 1, green: 0.70, blue: 0.30)   // #FFB24C
                    : Color(red: 0.66, green: 0.39, blue: 0)    // #A86400
    }
    return dark ? Color(red: 0.54, green: 0.89, blue: 0.82)    // #89E2D0
                : Color(red: 0.07, green: 0.49, blue: 0.45)    // #117D72
}

// MARK: — Twin Reactor Irises

struct ReactorIrisIcon: View {
    let cS: Double, cW: Double?, xS: Double, xW: Double?  // 0...1

    var body: some View {
        Canvas { context, size in
            let centers: [(x: CGFloat, session: Double, weekly: Double?)] = [
                (7.5, cS, cW),
                (20.5, xS, xW),
            ]
            let cy = size.height / 2

            for c in centers {
                let session = min(c.session, 1.0)
                let weekly = c.weekly.map { min($0, 1.0) }

                if let weekly {
                    // Outer shell: circle → rounded hexagon as weekly increases
                    let outerR: CGFloat = 5.0
                    let shellWidth: CGFloat = 1.1 + CGFloat(weekly) * 1.5  // 1.1 ... 2.6
                    let sides = 6
                    let cornerFraction = 1.0 - weekly  // 1.0 = full circle, 0.0 = hex
                    let shellColor = Color.white.opacity(0.45 + weekly * 0.15)

                    let shellPath = roundedPolygonPath(
                        center: CGPoint(x: c.x, y: cy),
                        radius: outerR,
                        sides: sides,
                        cornerRadius: outerR * cornerFraction * 0.85
                    )
                    context.stroke(shellPath, with: .color(shellColor), lineWidth: shellWidth)

                    let highlightArc = Path { p in
                        p.addArc(center: CGPoint(x: c.x, y: cy),
                                 radius: outerR - shellWidth / 2,
                                 startAngle: .degrees(-160),
                                 endAngle: .degrees(-100),
                                 clockwise: false)
                    }
                    context.stroke(highlightArc, with: .color(.white.opacity(0.18)), lineWidth: 0.8)
                }

                // Inner core
                let coreR: CGFloat = 1.2 + CGFloat(session) * 2.9  // 1.2 ... 4.1
                let coreColor = usageColor(session)
                let corePath = Path(ellipseIn: CGRect(
                    x: c.x - coreR, y: cy - coreR,
                    width: coreR * 2, height: coreR * 2
                ))
                context.fill(corePath, with: .color(coreColor.opacity(0.7 + session * 0.3)))

                // Center notch (cooling vent) — tiny dark circle
                let notchR: CGFloat = max(0.6, coreR * 0.2)
                let notch = Path(ellipseIn: CGRect(
                    x: c.x - notchR, y: cy - notchR,
                    width: notchR * 2, height: notchR * 2
                ))
                context.fill(notch, with: .color(Color(red: 0.04, green: 0.06, blue: 0.09)))
            }
        }
        .frame(width: 28, height: 22)
    }
}

// Helper: rounded polygon path
func roundedPolygonPath(center: CGPoint, radius: CGFloat, sides: Int, cornerRadius: CGFloat) -> Path {
    Path { path in
        let angleStep = (2 * .pi) / Double(sides)
        // If cornerRadius is large enough, just draw a circle
        if cornerRadius >= radius * 0.8 {
            path.addEllipse(in: CGRect(x: center.x - radius, y: center.y - radius,
                                        width: radius * 2, height: radius * 2))
            return
        }

        for i in 0..<sides {
            let angle1 = Double(i) * angleStep - .pi / 2
            let angle2 = Double(i + 1) * angleStep - .pi / 2

            let p1 = CGPoint(x: center.x + radius * cos(angle1),
                             y: center.y + radius * sin(angle1))
            let p2 = CGPoint(x: center.x + radius * cos(angle2),
                             y: center.y + radius * sin(angle2))

            let sideLen = hypot(p2.x - p1.x, p2.y - p1.y)
            let clampedR = min(cornerRadius, sideLen / 2.5)
            let t = clampedR / sideLen

            let start = CGPoint(x: p1.x + (p2.x - p1.x) * t, y: p1.y + (p2.y - p1.y) * t)
            let end = CGPoint(x: p2.x - (p2.x - p1.x) * t, y: p2.y - (p2.y - p1.y) * t)

            if i == 0 {
                path.move(to: start)
            } else {
                path.addLine(to: start)
            }
            path.addLine(to: end)
            // Round the corner at p2
            let angle3 = Double(i + 2) * angleStep - .pi / 2
            let p3 = CGPoint(x: center.x + radius * cos(angle3),
                             y: center.y + radius * sin(angle3))
            let nextStart = CGPoint(x: p2.x + (p3.x - p2.x) * t, y: p2.y + (p3.y - p2.y) * t)
            path.addQuadCurve(to: nextStart, control: p2)
        }
        path.closeSubpath()
    }
}

// MARK: — Eclipse Orbitals

struct EclipseOrbitalIcon: View {
    let cS: Double, cW: Double?, xS: Double, xW: Double?  // 0...1

    var body: some View {
        Canvas { context, size in
            let centers: [(x: CGFloat, session: Double, weekly: Double?)] = [
                (7.0, cS, cW),
                (21.0, xS, xW),
            ]
            let cy = size.height / 2

            for c in centers {
                let session = min(c.session, 1.0)
                let weekly = c.weekly.map { min($0, 1.0) }
                let center = CGPoint(x: c.x, y: cy)

                let discR: CGFloat = 4.8
                let discColor = usageColor(session)

                // Draw bright disc
                let disc = Path(ellipseIn: CGRect(
                    x: center.x - discR, y: center.y - discR,
                    width: discR * 2, height: discR * 2
                ))
                context.fill(disc, with: .color(discColor.opacity(0.85)))

                // Occluder (dark moon) carves the eclipse
                let occR: CGFloat = 1.7 + CGFloat(session) * 3.0  // 1.7 ... 4.7
                let offsetX: CGFloat = 1.8 - CGFloat(session) * 1.4  // 1.8 → 0.4
                let offsetY: CGFloat = 2.0 - CGFloat(session) * 2.0  // 2.0 → 0.0
                let occCenter = CGPoint(x: center.x + offsetX, y: center.y - offsetY)
                let occluder = Path(ellipseIn: CGRect(
                    x: occCenter.x - occR, y: occCenter.y - occR,
                    width: occR * 2, height: occR * 2
                ))
                // Dark occluder
                context.fill(occluder, with: .color(Color(red: 0.04, green: 0.05, blue: 0.07)))

                // Orbit ring
                if let weekly {
                    let orbitW: CGFloat = 10.5
                    let orbitH: CGFloat = 7.0
                    let orbitStroke: CGFloat = 1.0 + CGFloat(weekly) * 1.3  // 1.0 ... 2.3
                    let tiltDeg: Double = 15 + weekly * 20  // 15° ... 35°
                    let orbitColor = Color.white.opacity(0.4 + weekly * 0.25)

                    let tilt = c.x < 14 ? -tiltDeg : tiltDeg

                    context.drawLayer { ctx in
                        let orbitRect = CGRect(
                            x: center.x - orbitW / 2,
                            y: center.y - orbitH / 2,
                            width: orbitW,
                            height: orbitH
                        )
                        var orbitPath = Path(ellipseIn: orbitRect)
                        let transform = CGAffineTransform(translationX: center.x, y: center.y)
                            .rotated(by: tilt * .pi / 180)
                            .translatedBy(x: -center.x, y: -center.y)
                        orbitPath = orbitPath.applying(transform)
                        ctx.stroke(orbitPath, with: .color(orbitColor), lineWidth: orbitStroke)
                    }
                }

                // Crescent highlight — thin bright edge opposite the occluder
                let highlightArc = Path { p in
                    p.addArc(center: center, radius: discR - 0.5,
                             startAngle: .degrees(160), endAngle: .degrees(260), clockwise: false)
                }
                context.stroke(highlightArc, with: .color(.white.opacity(0.2 + session * 0.15)),
                               lineWidth: 0.7)
            }
        }
        .frame(width: 28, height: 22)
    }
}

// MARK: — Dual Thermometer Icon

struct ThermometerIcon: View {
    let claudeSession: Double  // 0...1
    let claudeWeekly: Double?
    let codexSession: Double
    let codexWeekly: Double?

    private let pillW: CGFloat = 7
    private let pillH: CGFloat = 18
    private let gap: CGFloat = 5

    var body: some View {
        Canvas { context, size in
            let totalW = pillW * 2 + gap
            let baseX = (size.width - totalW) / 2
            let baseY = (size.height - pillH) / 2

            let pills: [(session: Double, weekly: Double?, x: CGFloat)] = [
                (claudeSession, claudeWeekly, baseX),
                (codexSession, codexWeekly, baseX + pillW + gap),
            ]

            for pill in pills {
                let rect = CGRect(x: pill.x, y: baseY, width: pillW, height: pillH)
                let cornerR = pillW / 2

                // Track background — always visible
                let track = Path(roundedRect: rect, cornerRadius: cornerR)
                context.fill(track, with: .color(Color.white.opacity(0.18)))
                context.stroke(track, with: .color(Color.white.opacity(0.45)), lineWidth: 0.6)

                // Fill from BOTTOM up, growing with usage
                let used = min(max(pill.session, 0), 1.0)
                if used > 0 {
                    let rawFillH = pillH * used
                    // Keep low-but-real usage visually legible in the menu bar.
                    let fillH = ceil(max(rawFillH, minimumVisibleFillHeight(for: used)))
                    let fillY = floor(baseY + pillH - fillH)
                    // Draw as rounded rect at bottom of pill — bottom corners match pill, top straight
                    let fillRect = CGRect(x: pill.x, y: fillY, width: pillW, height: fillH)
                    let fillPath = Path { p in
                        let bottomR = min(cornerR, fillH / 2)
                        p.move(to: CGPoint(x: fillRect.minX, y: fillRect.minY))
                        p.addLine(to: CGPoint(x: fillRect.maxX, y: fillRect.minY))
                        p.addLine(to: CGPoint(x: fillRect.maxX, y: fillRect.maxY - bottomR))
                        p.addQuadCurve(
                            to: CGPoint(x: fillRect.maxX - bottomR, y: fillRect.maxY),
                            control: CGPoint(x: fillRect.maxX, y: fillRect.maxY)
                        )
                        p.addLine(to: CGPoint(x: fillRect.minX + bottomR, y: fillRect.maxY))
                        p.addQuadCurve(
                            to: CGPoint(x: fillRect.minX, y: fillRect.maxY - bottomR),
                            control: CGPoint(x: fillRect.minX, y: fillRect.maxY)
                        )
                        p.closeSubpath()
                    }
                    context.fill(fillPath, with: .color(usageColor(used)))
                }

                // Weekly tick mark — positioned from bottom by weekly used %
                let weeklyUsed = pill.weekly.map { min(max($0, 0), 1.0) }
                if let weeklyUsed, weeklyUsed > 0 {
                    let tickY = baseY + pillH - (pillH * weeklyUsed)
                    let tickPath = Path { p in
                        p.move(to: CGPoint(x: pill.x - 1.8, y: tickY))
                        p.addLine(to: CGPoint(x: pill.x + pillW + 1.8, y: tickY))
                    }
                    context.stroke(tickPath, with: .color(.white.opacity(0.9)), lineWidth: 1.5)
                }
            }
        }
        .frame(width: 28, height: 22)
    }

    private func minimumVisibleFillHeight(for used: Double) -> CGFloat {
        if used <= 0 { return 0 }
        if used < 0.08 { return 3.0 }
        if used < 0.20 { return 5.0 }
        return 0
    }
}

// MARK: — Breathing Grid Icon

struct BreathingGridIcon: View {
    let claudeSession: Double  // 0...1
    let claudeWeekly: Double?
    let codexSession: Double
    let codexWeekly: Double?

    var body: some View {
        Canvas { context, size in
            // 2x2 grid: [Claude Session, Codex Session] / [Claude Weekly, Codex Weekly]
            let values: [[Double?]] = [
                [claudeSession, codexSession],
                [claudeWeekly, codexWeekly],
            ]
            let centerX = size.width / 2
            let centerY = size.height / 2
            let spacing: CGFloat = 9  // distance between cell centers

            let minSize: CGFloat = 2.5
            let maxSize: CGFloat = 7.5

            for row in 0..<2 {
                for col in 0..<2 {
                    let val = values[row][col].map { min($0, 1.0) }
                    let fillValue = val ?? 0
                    let cellSize = minSize + fillValue * (maxSize - minSize)
                    let cornerR = (1 - fillValue) * (cellSize / 2)  // circle at 0%, square at 100%

                    let cx = centerX + CGFloat(col == 0 ? -1 : 1) * spacing / 2
                    let cy = centerY + CGFloat(row == 0 ? -1 : 1) * spacing / 2

                    let rect = CGRect(
                        x: cx - cellSize / 2,
                        y: cy - cellSize / 2,
                        width: cellSize,
                        height: cellSize
                    )

                    // Faint track
                    let trackRect = CGRect(x: cx - maxSize/2, y: cy - maxSize/2, width: maxSize, height: maxSize)
                    let trackShape = Path(roundedRect: trackRect, cornerRadius: maxSize * 0.15)
                    context.stroke(trackShape, with: .color(.white.opacity(0.08)), lineWidth: 0.5)

                    if let val {
                        let color = gridColorForPct(val)
                        let shape = Path(roundedRect: rect, cornerRadius: cornerR)
                        context.fill(shape, with: .color(color.opacity(0.4 + val * 0.6)))
                    }
                }
            }
        }
        .frame(width: 22, height: 22)
    }

    private func gridColorForPct(_ pct: Double) -> Color {
        if pct >= 0.85 { return Color(red: 1, green: 0.32, blue: 0.32) }    // red
        if pct >= 0.65 { return Color(red: 1, green: 0.72, blue: 0.28) }    // orange
        if pct >= 0.35 { return Color(red: 1, green: 0.84, blue: 0.31) }    // amber
        return Color(red: 0.5, green: 0.8, blue: 0.77)                       // teal-mint
    }
}

struct MenuContent: View {
    @ObservedObject var vm: UsageViewModel

    var body: some View {
        VStack(spacing: 0) {
            if vm.isOffline {
                Label("API Offline", systemImage: "wifi.slash")
                    .foregroundStyle(.secondary)
                    .padding(20)
            } else if let s = vm.stats {

                // ── Header ──────────────────────────────
                HStack(spacing: 0) {
                    Text("Tool Usage")
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text(freshness(s.timestamp))
                        .foregroundStyle(.tertiary)
                }
                .font(.system(.caption2, design: .rounded).weight(.medium))
                .padding(.horizontal, 14).padding(.vertical, 8)

                let providerCards = providerCards(from: s)
                let selectedProviderID = providerCards.contains(where: { $0.id == vm.selectedTool })
                    ? vm.selectedTool
                    : (providerCards.first?.id ?? vm.selectedTool)
                let selectedCard = providerCards.first(where: { $0.id == selectedProviderID })

                // ── Tool rows ───────────────────────────
                VStack(spacing: 0) {
                    ForEach(Array(providerCards.enumerated()), id: \.element.id) { idx, card in
                        if idx > 0 {
                            Divider().padding(.leading, 44)
                        }
                        toolRow(
                            name: card.name,
                            selected: selectedProviderID == card.id,
                            sessionPct: card.primaryPct,
                            weeklyPct: card.secondaryPct,
                            status: card.status,
                            reset: card.primaryReset,
                            plan: card.plan,
                            summary: card.summary,
                            onTap: { vm.selectedTool = card.id }
                        )
                    }
                }

                // ── Risk outlook (warnings always, pacing only for Claude) ──
                if let outlook = s.riskOutlook,
                   outlook.hasPrefix("⚠") || selectedProviderID == "claude" {
                    Text(outlook)
                        .font(.system(.caption2, design: .rounded))
                        .foregroundStyle(outlook.hasPrefix("⚠") ? Color.orange : Color.gray)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 14).padding(.vertical, 6)
                }

                Divider()

                // ── Focused detail ──────────────────────
                VStack(alignment: .leading, spacing: 6) {
                    if let card = selectedCard {
                        ForEach(Array(card.sections.enumerated()), id: \.offset) { _, section in
                            if !section.lines.isEmpty {
                                SectionHeader(title: section.title)
                                ForEach(Array(section.lines.enumerated()), id: \.offset) { _, line in
                                    detailLine(line.label, val: line.value)
                                }
                            }
                        }
                    } else {
                        detailLine("Status", val: "No provider data yet")
                    }
                }
                .font(.system(.caption, design: .rounded))
                .padding(.horizontal, 14).padding(.vertical, 8)

                Divider()

                // ── Weekly View Toggle ─────────────────
                if vm.showWeeklyView, let forecast = vm.weeklyForecast {
                    WeeklyForecastView(forecast: forecast)
                }

                Button(action: { vm.showWeeklyView.toggle() }) {
                    HStack {
                        Image(systemName: vm.showWeeklyView ? "calendar.circle.fill" : "calendar.circle")
                            .font(.caption)
                        Text(vm.showWeeklyView ? "Hide Weekly Plan" : "Weekly Plan")
                            .font(.system(.caption2, design: .rounded))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
                }
                .buttonStyle(.plain)
                .foregroundStyle(vm.showWeeklyView ? .primary : .secondary)
                .padding(.horizontal, 14).padding(.vertical, 2)

                Divider()

                // ── Footer ──────────────────────────────
                HStack(spacing: 8) {
                    Button(action: { vm.fetch() }) {
                        Image(systemName: "arrow.clockwise").font(.caption)
                    }
                    .keyboardShortcut("r")

                    Button(action: { SettingsWindowController.shared.open(vm: vm) }) {
                        Image(systemName: "gearshape").font(.caption)
                    }
                    .buttonStyle(.plain).foregroundStyle(.secondary)
                    .keyboardShortcut(",")

                    linkIcon("globe", UserDefaults.standard.string(forKey: SettingsKey.apiURL) ?? "http://localhost:8000")
                    linkIcon("c.circle", "https://claude.ai/settings/usage")
                    linkIcon("x.circle", "https://chatgpt.com/codex/settings/usage")
                    linkIcon("cursorarrow.click", "https://cursor.com/dashboard/usage")

                    Spacer()

                    if let streak = s.streak, streak > 0 {
                        Text("🔥 \(streak)d").font(.caption2).foregroundStyle(.tertiary)
                    }

                    Button("Quit") { NSApplication.shared.terminate(nil) }
                        .font(.caption2).foregroundStyle(.tertiary)
                        .keyboardShortcut("q")
                }
                .padding(.horizontal, 14).padding(.vertical, 8)
            }
        }
        .frame(width: 340)
    }

    private struct ProviderCard {
        let id: String
        let name: String
        let plan: String?
        let status: String
        let primaryPct: Double?
        let secondaryPct: Double?
        let primaryReset: String?
        let summary: String
        let sections: [ProviderSection]
    }

    private struct ProviderSection {
        let title: String
        let lines: [ProviderLine]
    }

    private struct ProviderLine {
        let label: String
        let value: String
    }

    private func providerCards(from stats: UsageStats) -> [ProviderCard] {
        let defaultRegistry = coreProviderRegistry()
        let registrySource = stats.providerRegistry.flatMap { $0.isEmpty ? nil : $0 } ?? defaultRegistry
        let registry = registrySource
            .sorted {
                let lhsOrder = $0.order ?? Int.max
                let rhsOrder = $1.order ?? Int.max
                if lhsOrder == rhsOrder {
                    return $0.id < $1.id
                }
                return lhsOrder < rhsOrder
            }

        var cards: [ProviderCard] = []
        var seen = Set<String>()

        for entry in registry {
            seen.insert(entry.id)
            if let card = providerCard(for: entry.id, label: providerLabel(for: entry.id, fallback: entry.label), stats: stats) {
                cards.append(card)
            }
        }

        for entry in defaultRegistry where !seen.contains(entry.id) {
            if let card = providerCard(for: entry.id, label: providerLabel(for: entry.id, fallback: entry.label), stats: stats) {
                cards.append(card)
            }
        }

        if let latest = stats.providersLatest {
            for providerID in latest.keys.sorted() where !seen.contains(providerID) {
                if let card = providerCard(for: providerID, label: providerLabel(for: providerID, fallback: nil), stats: stats) {
                    cards.append(card)
                }
            }
        }

        return cards
    }

    private func providerCard(for providerID: String, label: String, stats: UsageStats) -> ProviderCard? {
        let snapshot = stats.providersLatest?[providerID]
        let shared = snapshot?.shared

        var primaryUsed = shared?.primaryUsedPct
        var primaryRemaining = shared?.primaryRemainingPct
        var primaryReset = shared?.primaryReset
        var secondaryUsed = shared?.secondaryUsedPct
        var secondaryRemaining = shared?.secondaryRemainingPct
        var secondaryReset = shared?.secondaryReset
        // Labels for the primary/secondary slots. Default to Claude/Codex semantics
        // (session + weekly buckets); providers whose slots mean something else
        // override these in their switch case below.
        var primaryLabel = "Session"
        let secondaryLabel = "Weekly"
        var tokensDay = shared?.tokensTotalDay
        var messagesDay = shared?.messagesTotalDay
        var activeHoursDay = shared?.activeHoursDay
        var plan = normalizedText(snapshot?.plan ?? snapshot?.unique?["plan"]?.stringValue)
        var status = normalizedStatus(snapshot?.status)

        var uniqueLines = uniqueLines(from: snapshot?.unique)
        var modelLines: [ProviderLine] = []

        func appendUnique(_ label: String, _ value: String?) {
            guard let value = normalizedText(value) else { return }
            guard !uniqueLines.contains(where: { $0.label.caseInsensitiveCompare(label) == .orderedSame }) else { return }
            uniqueLines.append(ProviderLine(label: label, value: value))
        }

        switch providerID {
        case "claude":
            primaryUsed = primaryUsed ?? stats.claudeQuota?.sessionUsedPct
            primaryRemaining = primaryRemaining ?? stats.claudeQuota?.sessionRemainingPct
            primaryReset = primaryReset ?? stats.claudeQuota?.sessionReset
            secondaryUsed = secondaryUsed ?? stats.claudeQuota?.weeklyUsedPct
            secondaryRemaining = secondaryRemaining ?? stats.claudeQuota?.weeklyRemainingPct
            secondaryReset = secondaryReset ?? stats.claudeQuota?.weeklyReset
            activeHoursDay = activeHoursDay ?? stats.claudeToday?.activeHoursToday
            messagesDay = messagesDay ?? stats.claudeToday?.messagesToday.map(Double.init)
            tokensDay = tokensDay ?? stats.claudeToday?.outputTokensToday.map(Double.init)
            plan = plan ?? "Max"
            status = status ?? toolStatus(session: primaryUsed ?? 0, burn: stats.burn)
            modelLines = modelDetailLines(stats.claudeToday?.modelsToday)

            appendUnique("Burn", stats.burn.map { String(format: "%.1f%%/hr · %@", $0, stats.workload ?? "") })
            appendUnique(
                "Value",
                stats.outputDensity.map { "\(formatTokens(Int($0)))/hr · Cache \(String(format: "%.0f%%", stats.cacheHealthPct ?? 0))" }
            )
            if let pace = stats.weeklyPace {
                let paceLabel = pace.paceStatus == "front_loaded" ? "front-loaded"
                    : pace.paceStatus == "under" ? "under pace" : "on track"
                appendUnique(
                    "Week Pace",
                    String(format: "%.0f%% projected · %.1fd left · %@", pace.projectedPct, pace.daysRemaining ?? 0, paceLabel)
                )
            }
            // 4-state gap rollups: focus/attention/off-hours/agent-runtime for today.
            // Sourced from /stats.gap_rollups (parity with /analytics/sessions).
            if let today = stats.gapRollups?.today {
                appendUnique("Focus Today", formatGapDuration(today.focusGapSec))
                appendUnique("Attention Idle", formatGapDuration(today.attentionIdleSec))
                appendUnique("Agent Runtime", formatGapDuration(today.agentRuntimeSec))
                appendUnique("Off-Hours", formatGapDuration(today.offHoursAwaySec))
            }
        case "codex":
            primaryUsed = primaryUsed ?? stats.codexQuota?.sessionUsedPct
            primaryRemaining = primaryRemaining ?? stats.codexQuota?.sessionRemainingPct
            primaryReset = primaryReset ?? stats.codexQuota?.sessionReset
            secondaryUsed = secondaryUsed ?? stats.codexQuota?.weeklyUsedPct
            secondaryRemaining = secondaryRemaining ?? stats.codexQuota?.weeklyRemainingPct
            secondaryReset = secondaryReset ?? stats.codexQuota?.weeklyReset
            activeHoursDay = activeHoursDay ?? stats.codexToday?.activeHoursToday
            messagesDay = messagesDay ?? stats.codexToday?.messagesToday.map(Double.init)
            tokensDay = tokensDay ?? stats.codexToday?.outputTokensToday.map(Double.init)
            plan = plan ?? "Plus"
            status = status ?? toolStatus(
                session: primaryUsed ?? secondaryUsed ?? stats.codexQuota?.codeReviewUsedPct ?? 0,
                burn: stats.codexBurn
            )
            modelLines = modelDetailLines(stats.codexToday?.modelsToday)

            appendUnique("Burn", stats.codexBurn.map {
                let mode = $0 > 15 ? "Heavy" : $0 > 5 ? "Active" : "Light"
                return String(format: "%.1f%%/hr · %@", $0, mode)
            })
            appendUnique("Review", stats.codexQuota?.codeReviewUsedPct.map { String(format: "%.0f%% used", $0) })
            appendUnique("Threads Today", stats.codexToday?.threadsToday.map { "\($0)" })
            appendUnique("Sessions Today", stats.codexToday?.sessionsToday.map { "\($0)" })
            appendUnique("User Msgs", stats.codexToday?.userMessagesToday.map { "\($0)" })
            appendUnique("Reasoning", stats.codexToday?.reasoningTokensToday.map { formatTokens($0) })
            appendUnique(
                "Totals",
                [
                    stats.codexTotals?.totalThreads.map { "\($0) threads" },
                    stats.codexTotals?.totalSessions.map { "\($0) sessions" },
                    stats.codexTotals?.totalTokens.map { "\(formatTokens($0)) tok" },
                ].compactMap { $0 }.joined(separator: " · ")
            )
            if let analytics = stats.codexAnalyticsSummary {
                appendUnique(
                    "Window",
                    [
                        analytics.avgDailyTurns.map { "\(formatStat($0)) turns/day" },
                        analytics.dominantSurface,
                    ].compactMap { $0 }.joined(separator: " · ")
                )
                if analytics.reviewsAvailable == true,
                   let reviews = analytics.avgDailyReviews,
                   let comments = analytics.avgDailyComments,
                   reviews > 0 || comments > 0 {
                    appendUnique("Reviews", "\(formatStat(reviews))/day · \(formatStat(comments)) comments/day")
                }
            }
        case "cursor":
            let cursor = stats.cursor
            primaryUsed = primaryUsed ?? cursor.map { $0.usagePct * 100 }
            primaryReset = primaryReset ?? cursor?.resetAt
            primaryLabel = "Monthly"
            plan = plan ?? cursor?.plan?.capitalized ?? "Free"
            status = status ?? {
                let maxRequests = cursor?.totalMaxRequests ?? 0
                if cursor?.atLimit == true || cursor?.limitHit == true { return "at_limit" }
                if maxRequests > 0 && (cursor?.remaining ?? maxRequests) <= 0 { return "at_limit" }
                return (cursor?.totalRequests ?? 0) > 0 ? "active" : "idle"
            }()

            appendUnique("Requests", {
                let maxRequests = cursor?.totalMaxRequests ?? 0
                if maxRequests > 0 {
                    return "\(cursor?.totalRequests ?? 0)/\(maxRequests)"
                }
                return cursor?.totalRequests.map { "\($0) this month" }
            }())
            appendUnique("Remaining", cursor.map { $0.remaining > 0 ? "\($0.remaining) requests" : "Limit reached" })
            appendUnique("Limit", cursor?.limitMessage)
            appendUnique("Reset", cursor?.resetAt)
            appendUnique("Tokens", cursor?.totalTokens.map { formatTokens($0) })
            appendUnique("Window", cursor?.startOfMonth)
            modelLines = cursorModelDetailLines(cursor?.models)
        default:
            status = status ?? "idle"
        }

        let normalizedStatusValue = normalizedStatus(status) ?? "idle"
        let usageLines = usageDetailLines(
            primaryUsed: primaryUsed,
            primaryRemaining: primaryRemaining,
            secondaryUsed: secondaryUsed,
            secondaryRemaining: secondaryRemaining,
            primaryLabel: primaryLabel,
            secondaryLabel: secondaryLabel
        )
        let limitLines = limitDetailLines(
            status: normalizedStatusValue,
            primaryReset: primaryReset,
            secondaryReset: secondaryReset,
            primaryLabel: primaryLabel,
            secondaryLabel: secondaryLabel
        )
        let activityLines = activityDetailLines(
            activeHoursDay: activeHoursDay,
            messagesDay: messagesDay,
            tokensDay: tokensDay
        )

        let hasSignal = primaryUsed != nil
            || secondaryUsed != nil
            || !usageLines.isEmpty
            || !limitLines.isEmpty
            || !activityLines.isEmpty
            || !uniqueLines.isEmpty
            || snapshot != nil

        if !hasSignal && !coreProviderIDs.contains(providerID) {
            return nil
        }

        if !hasSignal {
            uniqueLines.append(ProviderLine(label: "Status", value: "No data yet"))
        }

        let summary = rowSummary(
            primaryUsed: primaryUsed,
            secondaryUsed: secondaryUsed,
            primaryLabel: primaryLabel,
            secondaryLabel: secondaryLabel,
            activeHoursDay: activeHoursDay,
            messagesDay: messagesDay,
            tokensDay: tokensDay
        )

        return ProviderCard(
            id: providerID,
            name: label,
            plan: plan,
            status: normalizedStatusValue,
            primaryPct: primaryUsed,
            secondaryPct: secondaryUsed,
            primaryReset: primaryReset,
            summary: summary,
            sections: [
                ProviderSection(title: "Usage", lines: usageLines),
                ProviderSection(title: "Limits", lines: limitLines),
                ProviderSection(title: "Activity", lines: activityLines),
                ProviderSection(title: "Models", lines: modelLines),
                ProviderSection(title: "Unique", lines: uniqueLines),
            ]
        )
    }

    private func coreProviderRegistry() -> [ProviderRegistryEntry] {
        [
            ProviderRegistryEntry(id: "claude", label: "Claude Code", color: nil, order: 1),
            ProviderRegistryEntry(id: "codex", label: "Codex", color: nil, order: 2),
            ProviderRegistryEntry(id: "cursor", label: "Cursor", color: nil, order: 3),
        ]
    }

    private var coreProviderIDs: Set<String> {
        Set(coreProviderRegistry().map(\.id))
    }

    private func providerLabel(for providerID: String, fallback: String?) -> String {
        if let fallback = normalizedText(fallback) {
            return fallback
        }
        switch providerID {
        case "claude": return "Claude Code"
        case "codex": return "Codex"
        case "cursor": return "Cursor"
        default:
            return titleCase(providerID)
        }
    }

    private func usageDetailLines(primaryUsed: Double?, primaryRemaining: Double?,
                                  secondaryUsed: Double?, secondaryRemaining: Double?,
                                  primaryLabel: String, secondaryLabel: String) -> [ProviderLine] {
        var lines: [ProviderLine] = []
        if let primaryUsed {
            lines.append(ProviderLine(label: primaryLabel, value: String(format: "%.0f%% used", primaryUsed)))
        }
        if let primaryRemaining {
            lines.append(ProviderLine(label: "\(primaryLabel) Left", value: String(format: "%.0f%%", primaryRemaining)))
        }
        if let secondaryUsed {
            lines.append(ProviderLine(label: secondaryLabel, value: String(format: "%.0f%% used", secondaryUsed)))
        }
        if let secondaryRemaining {
            lines.append(ProviderLine(label: "\(secondaryLabel) Left", value: String(format: "%.0f%%", secondaryRemaining)))
        }
        return lines
    }

    private func limitDetailLines(status: String, primaryReset: String?, secondaryReset: String?,
                                  primaryLabel: String, secondaryLabel: String) -> [ProviderLine] {
        var lines: [ProviderLine] = [
            ProviderLine(label: "Status", value: titleCase(status)),
        ]
        if let primaryReset = normalizedText(primaryReset) {
            lines.append(ProviderLine(label: "\(primaryLabel) Reset", value: vm.formatResetDisplay(for: primaryReset) ?? primaryReset))
        }
        if let secondaryReset = normalizedText(secondaryReset) {
            lines.append(ProviderLine(label: "\(secondaryLabel) Reset", value: vm.formatResetDisplay(for: secondaryReset) ?? secondaryReset))
        }
        return lines
    }

    /// Per-model breakdown rows, sorted by tokens descending.
    private func modelDetailLines(_ models: [String: ModelTokenUsage]?) -> [ProviderLine] {
        guard let models, !models.isEmpty else { return [] }
        return models
            .sorted { ($0.value.tokens ?? 0) > ($1.value.tokens ?? 0) }
            .map { name, usage in
                ProviderLine(label: shortModelName(name), value: modelUsageValue(tokens: usage.tokens, requests: usage.requests))
            }
    }

    private func cursorModelDetailLines(_ models: [String: CursorModelStats]?) -> [ProviderLine] {
        guard let models, !models.isEmpty else { return [] }
        return models
            .sorted { ($0.value.tokens ?? 0) > ($1.value.tokens ?? 0) }
            .map { name, usage in
                ProviderLine(label: shortModelName(name), value: modelUsageValue(tokens: usage.tokens, requests: usage.requests))
            }
    }

    private func modelUsageValue(tokens: Int?, requests: Int?) -> String {
        var parts: [String] = []
        if let tokens, tokens > 0 {
            parts.append("\(formatTokens(tokens)) tok")
        }
        if let requests, requests > 0 {
            parts.append("\(requests) req")
        }
        return parts.isEmpty ? "—" : parts.joined(separator: " · ")
    }

    /// Compact display name: drops the "claude-" prefix and trailing
    /// date-stamp suffixes (e.g. "claude-sonnet-4-5-20250929" → "sonnet-4-5").
    private func shortModelName(_ raw: String) -> String {
        var name = raw
        if name.lowercased().hasPrefix("claude-") {
            name = String(name.dropFirst("claude-".count))
        }
        if let range = name.range(of: #"-20\d{6}$"#, options: .regularExpression) {
            name.removeSubrange(range)
        }
        return name.isEmpty ? raw : name
    }

    private func activityDetailLines(activeHoursDay: Double?, messagesDay: Double?, tokensDay: Double?) -> [ProviderLine] {
        var lines: [ProviderLine] = []
        if let activeHoursDay, activeHoursDay > 0 {
            lines.append(ProviderLine(label: "Active", value: String(format: "%.1fh today", activeHoursDay)))
        }
        if let messagesDay, messagesDay > 0 {
            lines.append(ProviderLine(label: "Messages", value: "\(Int(messagesDay.rounded()))"))
        }
        if let tokensDay, tokensDay > 0 {
            lines.append(ProviderLine(label: "Tokens", value: formatTokens(Int(tokensDay.rounded()))))
        }
        if lines.isEmpty {
            lines.append(ProviderLine(label: "Today", value: "No activity"))
        }
        return lines
    }

    private func rowSummary(primaryUsed: Double?, secondaryUsed: Double?,
                            primaryLabel: String, secondaryLabel: String,
                            activeHoursDay: Double?, messagesDay: Double?, tokensDay: Double?) -> String {
        var parts: [String] = []
        if let primaryUsed {
            parts.append(String(format: "%.0f%% %@", primaryUsed, primaryLabel.lowercased()))
        }
        if let secondaryUsed {
            parts.append(String(format: "%.0f%% %@", secondaryUsed, secondaryLabel.lowercased()))
        }
        if parts.isEmpty, let messagesDay, messagesDay > 0 {
            parts.append("\(Int(messagesDay.rounded())) msgs")
        }
        if parts.isEmpty, let tokensDay, tokensDay > 0 {
            parts.append("\(formatTokens(Int(tokensDay.rounded()))) tok")
        }
        if parts.isEmpty, let activeHoursDay, activeHoursDay > 0 {
            parts.append(String(format: "%.1fh active", activeHoursDay))
        }
        if parts.isEmpty {
            return "No quota data"
        }
        return parts.joined(separator: " · ")
    }

    private func uniqueLines(from unique: [String: JSONValue]?) -> [ProviderLine] {
        guard let unique else { return [] }
        return unique.keys.sorted().compactMap { key in
            guard key != "plan", let value = uniqueValueText(unique[key]) else { return nil }
            return ProviderLine(label: titleCase(key), value: value)
        }
    }

    private func uniqueValueText(_ value: JSONValue?) -> String? {
        guard let value else { return nil }
        switch value {
        case .string(let text):
            return normalizedText(text)
        case .number(let number):
            if number.rounded() == number {
                return String(format: "%.0f", number)
            }
            return String(format: "%.2f", number)
        case .bool(let flag):
            return flag ? "true" : "false"
        case .array(let values):
            let rendered = values.compactMap { $0.stringValue }
            guard !rendered.isEmpty else { return "\(values.count) items" }
            return rendered.prefix(3).joined(separator: ", ")
        case .object(let object):
            return object.isEmpty ? nil : "\(object.count) fields"
        case .null:
            return nil
        }
    }

    private func titleCase(_ input: String) -> String {
        input
            .replacingOccurrences(of: "_", with: " ")
            .split(separator: " ")
            .map { $0.capitalized }
            .joined(separator: " ")
    }

    private func normalizedText(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }
        return value
    }

    private func normalizedStatus(_ status: String?) -> String? {
        guard let status = normalizedText(status) else { return nil }
        return status.lowercased()
    }

    // ── Tool row ────────────────────────────────────

    func toolRow(name: String, selected: Bool, sessionPct: Double?, weeklyPct: Double?,
                 status: String, reset: String?, plan: String? = nil, summary: String? = nil,
                 onTap: @escaping () -> Void) -> some View {
        Button(action: onTap) {
            HStack(spacing: 10) {
                // Mini gauge glyph
                MiniGauge(pct: sessionPct, weeklyPct: weeklyPct)
                    .frame(width: 28, height: 28)

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(name)
                            .font(.system(.callout, design: .rounded, weight: .medium))
                        if let plan, !plan.isEmpty {
                            Text(plan)
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                        Text(status)
                            .font(.caption2)
                            .foregroundStyle(statusColor(status))
                    }

                    HStack(spacing: 8) {
                        if let summary, !summary.isEmpty {
                            Text(summary)
                        } else {
                            if let sessionPct {
                                Text(String(format: "%.0f%% session", sessionPct))
                            } else if weeklyPct != nil {
                                Text("-- session")
                            }
                            if sessionPct != nil || weeklyPct != nil {
                                if let w = weeklyPct {
                                    Text(String(format: "· %.0f%% week", w))
                                }
                            }
                            if let r = reset {
                                Text("resets \(shortTime(r))")
                            }
                        }
                    }
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                }

                Spacer()

                if selected {
                    Image(systemName: "chevron.right")
                        .font(.caption2)
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 8)
            .background(selected ? Color.primary.opacity(0.06) : Color.clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    func toolStatus(session: Double, burn: Double?) -> String {
        if session >= 90 { return "critical" }
        if session >= 70 || (burn ?? 0) > 20 { return "heating" }
        if session > 0 || (burn ?? 0) > 0 { return "active" }
        return "idle"
    }

    func statusColor(_ status: String) -> Color {
        switch status.lowercased() {
        case "critical", "at_limit", "limit", "error":
            return .red
        case "heating", "warning", "partial":
            return .orange
        case "active", "ok":
            return .green
        default:
            return .gray
        }
    }
}

// ── Mini gauge glyph (28×28) ────────────────────

struct MiniGauge: View {
    let pct: Double?       // 0...100 — session usage (fill)
    var weeklyPct: Double? // 0...100 — weekly usage (tick mark)

    var body: some View {
        Canvas { context, size in
            let center = CGPoint(x: size.width / 2, y: size.height / 2)
            let r: CGFloat = 11
            let startAngle: Double = 135
            let sweep: Double = 270

            // Track
            let track = Path { p in
                p.addArc(center: center, radius: r,
                         startAngle: .degrees(startAngle),
                         endAngle: .degrees(startAngle + sweep),
                         clockwise: false)
            }
            context.stroke(track, with: .color(.primary.opacity(0.12)), lineWidth: 3)

            // Session fill
            if let pct {
                let fillAngle = startAngle + (min(pct, 100) / 100) * sweep
                let fill = Path { p in
                    p.addArc(center: center, radius: r,
                             startAngle: .degrees(startAngle),
                             endAngle: .degrees(fillAngle),
                             clockwise: false)
                }
                let color: Color = pct >= 90 ? .red : pct >= 70 ? .orange : pct >= 50 ? .yellow : .green
                context.stroke(fill, with: .color(color), style: StrokeStyle(lineWidth: 3, lineCap: .round))
            }

            // Weekly tick mark
            if let wk = weeklyPct {
                let tickAngle = (startAngle + (min(wk, 100) / 100) * sweep) * .pi / 180
                let innerR = r - 3.5
                let outerR = r + 3.5
                let innerPt = CGPoint(x: center.x + innerR * cos(tickAngle),
                                       y: center.y - innerR * sin(tickAngle))
                let outerPt = CGPoint(x: center.x + outerR * cos(tickAngle),
                                       y: center.y - outerR * sin(tickAngle))
                var tick = Path()
                tick.move(to: innerPt)
                tick.addLine(to: outerPt)
                context.stroke(tick, with: .color(.primary.opacity(0.5)), lineWidth: 1.5)
            }

            // Center text
            let text = Text(pct.map { String(format: "%.0f", $0) } ?? "--")
                .font(.system(size: 8, weight: .bold, design: .rounded))
            context.draw(text, at: center)
        }
    }
}

// ── Helpers ──────────────────────────────────────

struct SectionHeader: View {
    let title: String
    var body: some View {
        HStack {
            Text(title)
                .font(.system(.caption2, design: .rounded, weight: .semibold))
                .foregroundStyle(.tertiary)
                .textCase(.uppercase)
            Spacer()
        }
    }
}

@ViewBuilder
func detailLine(_ label: String, val: String?) -> some View {
    if let v = val {
        HStack(alignment: .top, spacing: 8) {
            Text(label)
                .foregroundStyle(.tertiary)
                .frame(width: 64, alignment: .trailing)
            Text(v)
                .foregroundStyle(.secondary)
        }
    }
}

func shortTime(_ s: String) -> String {
    // "Apr 9 3:01 PM" → "3:01 PM" if today, otherwise "Apr 9 3:01 PM" unchanged
    let parts = s.split(separator: " ")
    guard parts.count >= 3, let day = Int(parts[1]) else { return s }

    let cal = Calendar.current
    let now = Date()
    let todayDay = cal.component(.day, from: now)

    let months = ["Jan":1,"Feb":2,"Mar":3,"Apr":4,"May":5,"Jun":6,
                   "Jul":7,"Aug":8,"Sep":9,"Oct":10,"Nov":11,"Dec":12]
    let currentMonth = cal.component(.month, from: now)

    if let month = months[String(parts[0])], month == currentMonth, day == todayDay {
        // Today — show just time
        return parts.dropFirst(2).joined(separator: " ")
    }
    // Future date — keep "Apr 14 12:00 AM"
    return s
}

func freshness(_ ts: Int?) -> String {
    guard let ts = ts else { return "—" }
    let age = Int(Date().timeIntervalSince1970) - ts
    if age < 60 { return "just now" }
    if age < 3600 { return "\(age / 60)m ago" }
    return "\(age / 3600)h ago"
}

func linkIcon(_ symbol: String, _ url: String, label: String? = nil) -> some View {
    Button(action: { if let u = URL(string: url) { NSWorkspace.shared.open(u) } }) {
        Image(systemName: symbol).font(.caption)
    }
    .buttonStyle(.plain)
    .foregroundStyle(.secondary)
    .help(label ?? url)
    .accessibilityLabel(label ?? url)
}

func formatTokens(_ n: Int) -> String {
    if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
    if n >= 1_000 { return String(format: "%.1fk", Double(n) / 1_000) }
    return "\(n)"
}

func formatStat(_ value: Double) -> String {
    if value.rounded() == value {
        return String(format: "%.0f", value)
    }
    return String(format: "%.1f", value)
}

// MARK: - Weekly Forecast View

struct WeeklyForecastView: View {
    let forecast: WeeklyForecastResponse

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(sortedProviders, id: \.key) { pid, prov in
                providerForecast(pid: pid, prov: prov)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
    }

    private var sortedProviders: [(key: String, value: ProviderForecast)] {
        let order = ["claude": 0, "codex": 1]
        return forecast.forecasts.sorted { (order[$0.key] ?? 9) < (order[$1.key] ?? 9) }
    }

    private func providerForecast(pid: String, prov: ProviderForecast) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            // Header
            HStack {
                Text(pid.capitalized)
                    .font(.system(.caption, design: .rounded).weight(.semibold))
                    .foregroundStyle(providerColor(pid))
                if let plan = prov.plan {
                    Text(plan)
                        .font(.system(.caption2, design: .rounded))
                        .foregroundStyle(.tertiary)
                }
                Spacer()
                if let reset = prov.resetDate {
                    Text("Reset: \(reset)")
                        .font(.system(.caption2, design: .rounded))
                        .foregroundStyle(.secondary)
                }
            }

            // Remaining summary
            if let remaining = prov.remaining, let cap = prov.weeklyCap {
                HStack(spacing: 4) {
                    Text("\(formatTokens(remaining)) / \(formatTokens(cap)) remaining")
                        .font(.system(.caption2, design: .rounded))
                        .foregroundStyle(.secondary)
                    Spacer()
                    if let days = prov.resetInDays {
                        Text("\(String(format: "%.0f", days))d left")
                            .font(.system(.caption2, design: .rounded).weight(.medium))
                            .foregroundStyle(.orange)
                    }
                }
            }

            // Day-by-day bars
            ForEach(prov.days) { day in
                dayRow(day: day, cap: prov.weeklyCap ?? 1)
            }
        }
    }

    private func dayRow(day: ForecastDay, cap: Int) -> some View {
        HStack(spacing: 6) {
            // Day label
            Text(day.label)
                .font(.system(.caption2, design: .rounded).weight(day.isToday ? .bold : .regular))
                .foregroundColor(day.isToday ? .primary : (day.isResetDay ? .orange : (day.postReset == true ? .green : .secondary)))
                .frame(width: 72, alignment: .leading)

            if day.isResetDay {
                // Reset marker
                HStack(spacing: 4) {
                    Image(systemName: "arrow.counterclockwise.circle.fill")
                        .font(.caption2)
                        .foregroundStyle(.orange)
                    Text("RESET \u{2014} Full quota restored")
                        .font(.system(.caption2, design: .rounded).weight(.medium))
                        .foregroundStyle(.orange)
                }
                Spacer()
            } else {
                // Budget bar
                let pct = min(Double(day.budgetTokens) / Double(max(cap, 1)) * 100, 100)
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(Color.white.opacity(0.06))
                        RoundedRectangle(cornerRadius: 3)
                            .fill(barColor(day: day))
                            .frame(width: geo.size.width * pct / 100)
                    }
                }
                .frame(height: 10)

                // Token amount
                Text(formatTokens(day.budgetTokens))
                    .font(.system(.caption2, design: .rounded).monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(width: 44, alignment: .trailing)
            }
        }
        .frame(height: 16)
    }

    private func barColor(day: ForecastDay) -> Color {
        if day.isToday { return Color(hex: "6dd3ff") }
        if day.postReset == true { return Color(hex: "34d399") }
        return Color(hex: "a78bfa")
    }

    private func providerColor(_ pid: String) -> Color {
        switch pid {
        case "claude": return Color(hex: "6dd3ff")
        case "codex": return Color(hex: "a78bfa")
        default: return .primary
        }
    }

    private func formatTokens(_ n: Int) -> String {
        if n >= 1_000_000 { return String(format: "%.1fM", Double(n) / 1_000_000) }
        if n >= 1_000 { return String(format: "%.0fK", Double(n) / 1_000) }
        return "\(n)"
    }
}

func formatGapDuration(_ seconds: Int?) -> String? {
    guard let seconds, seconds > 0 else { return nil }
    if seconds >= 3600 {
        return String(format: "%.1fh", Double(seconds) / 3600)
    }
    if seconds >= 60 {
        return "\(seconds / 60)m"
    }
    return "\(seconds)s"
}

private extension Color {
    init(hex: String) {
        let hex = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: hex).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8) & 0xFF) / 255
        let b = Double(int & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
