import Foundation

struct UsageStats: Codable {
    let timestamp: Int?
    let extra: Double
    let extraReset: String?
    let extraSpentUsd: Double?
    let extraLimitUsd: Double?
    let extraBalanceUsd: Double?
    let riskOutlook: String?
    let burn: Double?
    let workload: String?
    let codexBurn: Double?
    let lockEta: Double?
    let outputDensity: Double?
    let cacheHealthPct: Double?
    let streak: Int?
    let weeklyPace: WeeklyPace?
    let claudeToday: ClaudeToday?
    let codexToday: CodexToday?
    let claudeTotals: ClaudeTotals?
    let codexTotals: CodexTotals?
    let claudeQuota: ClaudeQuota?
    let codexQuota: CodexQuota?
    let codexAnalyticsSummary: CodexAnalyticsSummary?
    let cursor: CursorStats?
    let providerRegistry: [ProviderRegistryEntry]?
    let providersLatest: [String: ProviderLatestSnapshot]?
    let gapRollups: GapRollupsPayload?

    enum CodingKeys: String, CodingKey {
        case timestamp, extra, burn, workload, streak, cursor
        case riskOutlook = "risk_outlook"
        case codexBurn = "codex_burn"
        case lockEta = "lock_eta"
        case outputDensity = "output_density"
        case cacheHealthPct = "cache_health"
        case weeklyPace = "weekly_pace"
        case extraReset = "extra_reset"
        case extraSpentUsd = "extra_spent_usd"
        case extraLimitUsd = "extra_limit_usd"
        case extraBalanceUsd = "extra_balance_usd"
        case claudeToday = "claude_today"
        case codexToday = "codex_today"
        case claudeTotals = "claude_totals"
        case codexTotals = "codex_totals"
        case claudeQuota = "claude_quota"
        case codexQuota = "codex_quota"
        case codexAnalyticsSummary = "codex_analytics_summary"
        case providerRegistry = "provider_registry"
        case providersLatest = "providers_latest"
        case gapRollups = "gap_rollups"
    }
}

struct GapRollupsPayload: Codable {
    let today: GapRollup?
    let yesterday: GapRollup?
    let last7d: GapRollup?

    enum CodingKeys: String, CodingKey {
        case today, yesterday
        case last7d = "last_7d"
    }
}

struct GapRollup: Codable {
    let focusGapSec: Int?
    let attentionIdleSec: Int?
    let offHoursAwaySec: Int?
    let agentRuntimeSec: Int?
    // Legacy: human_time_sec = focus + attention; downtime_sec retired and zeroed.
    let humanTimeSec: Int?
    let downtimeSec: Int?

    enum CodingKeys: String, CodingKey {
        case focusGapSec = "focus_gap_sec"
        case attentionIdleSec = "attention_idle_sec"
        case offHoursAwaySec = "off_hours_away_sec"
        case agentRuntimeSec = "agent_runtime_sec"
        case humanTimeSec = "human_time_sec"
        case downtimeSec = "downtime_sec"
    }
}

struct ProviderRegistryEntry: Codable {
    let id: String
    let label: String?
    let color: String?
    let order: Int?
}

struct ProviderLatestSnapshot: Codable {
    let provider: String?
    let timestamp: Int?
    let status: String?
    let plan: String?
    let shared: ProviderSharedMetrics?
    let unique: [String: JSONValue]?
    let source: [String: JSONValue]?
    let errorText: String?

    enum CodingKeys: String, CodingKey {
        case provider, timestamp, status, plan, shared, unique, source
        case errorText = "error_text"
    }
}

struct ProviderSharedMetrics: Codable {
    let primaryUsedPct: Double?
    let primaryRemainingPct: Double?
    let primaryReset: String?
    let secondaryUsedPct: Double?
    let secondaryRemainingPct: Double?
    let secondaryReset: String?
    let tokensTotalDay: Double?
    let messagesTotalDay: Double?
    let activeHoursDay: Double?

    enum CodingKeys: String, CodingKey {
        case primaryUsedPct = "primary_used_pct"
        case primaryRemainingPct = "primary_remaining_pct"
        case primaryReset = "primary_reset"
        case secondaryUsedPct = "secondary_used_pct"
        case secondaryRemainingPct = "secondary_remaining_pct"
        case secondaryReset = "secondary_reset"
        case tokensTotalDay = "tokens_total_day"
        case messagesTotalDay = "messages_total_day"
        case activeHoursDay = "active_hours_day"
    }
}

enum JSONValue: Codable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container,
                debugDescription: "Unsupported JSON value"
            )
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value):
            try container.encode(value)
        case .number(let value):
            try container.encode(value)
        case .bool(let value):
            try container.encode(value)
        case .object(let value):
            try container.encode(value)
        case .array(let value):
            try container.encode(value)
        case .null:
            try container.encodeNil()
        }
    }

    var stringValue: String? {
        switch self {
        case .string(let value):
            return value
        case .number(let value):
            if value.rounded() == value {
                return String(format: "%.0f", value)
            }
            return String(format: "%.2f", value)
        case .bool(let value):
            return value ? "true" : "false"
        default:
            return nil
        }
    }
}

/// Per-model usage for today (tokens = input + output + cache).
struct ModelTokenUsage: Codable {
    let tokens: Int?
    let requests: Int?
}

struct ClaudeToday: Codable {
    let activeHoursToday: Double?
    let messagesToday: Int?
    let inputTokensToday: Int?
    let outputTokensToday: Int?
    let threadsToday: Int?
    let sessionsToday: Int?
    let conversationsToday: Int?
    let modelsToday: [String: ModelTokenUsage]?

    enum CodingKeys: String, CodingKey {
        case activeHoursToday = "active_hours_today"
        case messagesToday = "messages_today"
        case inputTokensToday = "input_tokens_today"
        case outputTokensToday = "output_tokens_today"
        case threadsToday = "threads_today"
        case sessionsToday = "sessions_today"
        case conversationsToday = "conversations_today"
        case modelsToday = "models_today"
    }
}

struct CodexToday: Codable {
    let activeHoursToday: Double?
    let messagesToday: Int?
    let inputTokensToday: Int?
    let outputTokensToday: Int?
    let threadsToday: Int?
    let sessionsToday: Int?
    let userMessagesToday: Int?
    let reasoningTokensToday: Int?
    let modelsToday: [String: ModelTokenUsage]?

    enum CodingKeys: String, CodingKey {
        case activeHoursToday = "active_hours_today"
        case messagesToday = "messages_today"
        case inputTokensToday = "input_tokens_today"
        case outputTokensToday = "output_tokens_today"
        case threadsToday = "threads_today"
        case sessionsToday = "sessions_today"
        case userMessagesToday = "user_messages_today"
        case reasoningTokensToday = "reasoning_tokens_today"
        case modelsToday = "models_today"
    }
}

struct ClaudeTotals: Codable {
    let totalSessions: Int?
    let totalMessages: Int?
    let favoriteModel: String?

    enum CodingKeys: String, CodingKey {
        case totalSessions = "total_sessions"
        case totalMessages = "total_messages"
        case favoriteModel = "favorite_model"
    }
}

struct CodexTotals: Codable {
    let totalThreads: Int?
    let totalSessions: Int?
    let totalTokens: Int?

    enum CodingKeys: String, CodingKey {
        case totalThreads = "total_threads"
        case totalSessions = "total_sessions"
        case totalTokens = "total_tokens"
    }
}

struct ClaudeQuota: Codable {
    let sessionUsedPct: Double?
    let weeklyUsedPct: Double?
    let sessionRemainingPct: Double?
    let weeklyRemainingPct: Double?
    let sessionReset: String?
    let weeklyReset: String?

    enum CodingKeys: String, CodingKey {
        case sessionUsedPct = "session_used_pct"
        case weeklyUsedPct = "weekly_used_pct"
        case sessionRemainingPct = "session_remaining_pct"
        case weeklyRemainingPct = "weekly_remaining_pct"
        case sessionReset = "session_reset"
        case weeklyReset = "weekly_reset"
    }
}

struct CodexQuota: Codable {
    let timestamp: Int?
    let sessionUsedPct: Double?
    let weeklyUsedPct: Double?
    let codeReviewUsedPct: Double?
    let sessionRemainingPct: Double?
    let weeklyRemainingPct: Double?
    let codeReviewRemainingPct: Double?
    let sessionReset: String?
    let weeklyReset: String?

    enum CodingKeys: String, CodingKey {
        case timestamp
        case sessionUsedPct = "session_used_pct"
        case weeklyUsedPct = "weekly_used_pct"
        case codeReviewUsedPct = "code_review_used_pct"
        case sessionRemainingPct = "session_remaining_pct"
        case weeklyRemainingPct = "weekly_remaining_pct"
        case codeReviewRemainingPct = "code_review_remaining_pct"
        case sessionReset = "session_reset"
        case weeklyReset = "weekly_reset"
    }
}

struct CodexAnalyticsSummary: Codable {
    let dominantSurface: String?
    let dominantSurfaceSharePct: Double?
    let avgDailyTurns: Double?
    let avgDailyReviews: Double?
    let avgDailyComments: Double?
    let reviewsAvailable: Bool?

    enum CodingKeys: String, CodingKey {
        case dominantSurface = "dominant_surface"
        case dominantSurfaceSharePct = "dominant_surface_share_pct"
        case avgDailyTurns = "avg_daily_turns"
        case avgDailyReviews = "avg_daily_reviews"
        case avgDailyComments = "avg_daily_comments"
        case reviewsAvailable = "reviews_available"
    }
}

struct CursorModelStats: Codable {
    let requests: Int?
    let tokens: Int?
    let maxRequests: Int?

    enum CodingKeys: String, CodingKey {
        case requests, tokens
        case maxRequests = "max_requests"
    }
}

struct CursorStats: Codable {
    let plan: String?
    let totalRequests: Int?
    let totalTokens: Int?
    let startOfMonth: String?
    let maxRequests: Int?
    let remainingRequests: Int?
    let atLimit: Bool?
    let limitHit: Bool?
    let limitKind: String?
    let limitMessage: String?
    let resetAt: String?
    let spendLimitHit: Bool?
    let spendLimits: [Int]?
    let models: [String: CursorModelStats]?

    enum CodingKeys: String, CodingKey {
        case plan, models
        case totalRequests = "total_requests"
        case totalTokens = "total_tokens"
        case startOfMonth = "start_of_month"
        case maxRequests = "max_requests"
        case remainingRequests = "remaining_requests"
        case atLimit = "at_limit"
        case limitHit = "limit_hit"
        case limitKind = "limit_kind"
        case limitMessage = "limit_message"
        case resetAt = "reset_at"
        case spendLimitHit = "spend_limit_hit"
        case spendLimits = "spend_limits"
    }

    var totalMaxRequests: Int {
        if let maxRequests, maxRequests > 0 {
            return maxRequests
        }
        return (models ?? [:]).values.reduce(0) { $0 + ($1.maxRequests ?? 0) }
    }

    var usagePct: Double {
        if atLimit == true || limitHit == true {
            return 1
        }
        let max = totalMaxRequests
        guard max > 0 else { return 0 }
        return Double(totalRequests ?? 0) / Double(max)
    }

    var remaining: Int {
        if let remainingRequests {
            return remainingRequests
        }
        return totalMaxRequests - (totalRequests ?? 0)
    }
}

struct WeeklyPace: Codable {
    let currentWeeklyPct: Double
    let daysElapsed: Double
    let daysRemaining: Double?
    let projectedPct: Double
    let paceStatus: String?
    let onTrack: Bool

    enum CodingKeys: String, CodingKey {
        case currentWeeklyPct = "current_weekly_pct"
        case daysElapsed = "days_elapsed"
        case daysRemaining = "days_remaining"
        case projectedPct = "projected_pct"
        case paceStatus = "pace_status"
        case onTrack = "on_track"
    }
}

// MARK: - Weekly Forecast

struct WeeklyForecastResponse: Codable {
    let forecasts: [String: ProviderForecast]
    let generatedAt: Int?

    enum CodingKeys: String, CodingKey {
        case forecasts
        case generatedAt = "generated_at"
    }
}

struct ProviderForecast: Codable {
    let plan: String?
    let weeklyCap: Int?
    let usedPct: Double?
    let remaining: Int?
    let resetDate: String?
    let resetInDays: Double?
    let dailyBudgetPreReset: Int?
    let dailyBudgetPostReset: Int?
    let days: [ForecastDay]

    enum CodingKeys: String, CodingKey {
        case plan, days, remaining
        case weeklyCap = "weekly_cap"
        case usedPct = "used_pct"
        case resetDate = "reset_date"
        case resetInDays = "reset_in_days"
        case dailyBudgetPreReset = "daily_budget_pre_reset"
        case dailyBudgetPostReset = "daily_budget_post_reset"
    }
}

struct ForecastDay: Codable, Identifiable {
    var id: String { date }

    let date: String
    let label: String
    let isToday: Bool
    let isResetDay: Bool
    let budgetTokens: Int
    let cumulativeRemaining: Int?
    let pctOfCap: Double?
    let tiers: [String: ForecastTier]?
    let resetEvent: Bool?
    let postReset: Bool?

    enum CodingKeys: String, CodingKey {
        case date, label, tiers
        case isToday = "is_today"
        case isResetDay = "is_reset_day"
        case budgetTokens = "budget_tokens"
        case cumulativeRemaining = "cumulative_remaining"
        case pctOfCap = "pct_of_cap"
        case resetEvent = "reset_event"
        case postReset = "post_reset"
    }
}

struct ForecastTier: Codable {
    let label: String
    let tokens: Int
    let weight: Double
}
