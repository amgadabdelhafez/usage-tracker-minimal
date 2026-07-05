"""Minimal usage-tracker API for the UsageMenuBar Swift app.

Endpoints:
    GET  /stats          — menubar payload (15s cache)
    GET  /budget/weekly  — weekly forecast per provider
    POST /cc/report      — collector ingest
    POST /sentinel/report — cookie refresh from the menubar sentinel
    GET  /health         — liveness probe
"""

import hmac
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

API_SECRET = os.environ.get("USAGE_TRACKER_SECRET")
if not API_SECRET:
    raise RuntimeError(
        "USAGE_TRACKER_SECRET must be set. "
        "Generate one with: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
    )


def verify_auth(authorization: str = Header(None)) -> None:
    """Dependency that enforces bearer-token auth on protected endpoints."""
    if not hmac.compare_digest(authorization or "", f"Bearer {API_SECRET}"):
        raise HTTPException(status_code=401, detail="Unauthorized")


from src.database import (
    cc_messages_today,
    cc_token_usage_today_cached,
    codex_last_completed_cycles,
    codex_local_stats,
    init,
    insert,
    insert_codex,
    insert_provider_metric_samples,
    latest_codex,
    latest_provider_metric_samples,
    latest_sample,
    load_claude_code_stats,
    prune_provider_metric_samples,
    sync,
)
from src.metrics import (
    cache_health,
    codex_burn_rate,
    current_streak,
    output_density,
    predict_lock,
    session_burn_rate,
    weekly_forecast,
    weekly_utilization_pace,
    workload_label,
)
from src.plan_config import load_plans
from src.provider_metrics import (
    CODEX_LEGACY_LOCAL_ALIAS_MAP,
    CODEX_LEGACY_SESSION_ALIAS_MAP,
    build_provider_snapshots,
    empty_claude_code_stats,
    empty_codex_session_stats,
    empty_codex_thread_stats,
    provider_registry,
    scan_codex_session_metrics,
    shape_claude_code_stats,
)
from src.scanners import scan_cc_daily_model_tokens

# ── Helpers ──────────────────────────────────────────────


def _first_present(raw: dict, *keys: str):
    for key in keys:
        if key in raw:
            return raw.get(key)
    return None


CLAUDE_SESSION_RESET_MAX_HOURS = 6


def _relative_hours_left(reset_str: str | None) -> float | None:
    if not reset_str:
        return None
    h = re.search(r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hour|hours)\b", reset_str, re.IGNORECASE)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minute|minutes)\b", reset_str, re.IGNORECASE)
    if h or m:
        hours = float(h.group(1)) if h else 0.0
        minutes = float(m.group(1)) if m else 0.0
        return round(hours + minutes / 60, 1)
    return None


def parse_hours_left(reset_str: str | None) -> float | None:
    """Parse a reset string into hours remaining for generic quota windows."""
    relative = _relative_hours_left(reset_str)
    if relative is not None:
        return relative
    if not reset_str:
        return None

    cleaned = reset_str.strip()
    now = datetime.now()
    for fmt in ["%I%p", "%I:%M%p", "%I:%M %p", "%I %p"]:
        try:
            t = datetime.strptime(cleaned.lower(), fmt)
        except ValueError:
            continue
        t = t.replace(year=now.year, month=now.month, day=now.day)
        if t <= now:
            t += timedelta(days=1)
        return round((t - now).total_seconds() / 3600, 1)

    for fmt in [
        "%b %d %I:%M %p",
        "%b %d %I:%M%p",
        "%b %d at %I:%M%p",
        "%b %d at %I%p",
        "%b %d at %I:%M %p",
        "%b %d, %Y %I:%M %p",
    ]:
        for candidate in (cleaned, cleaned.lower()):
            try:
                t = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            if t.year == 1900:
                t = t.replace(year=now.year)
            delta = (t - now).total_seconds() / 3600
            if delta > 0:
                return round(delta, 1)
    return None


def _claude_session_reset_datetime(reset_str: str, now: datetime) -> datetime | None:
    cleaned = reset_str.strip()
    time_only_fmts = ["%I%p", "%I:%M%p", "%I:%M %p", "%I %p"]
    absolute_fmts = [
        "%b %d %I:%M %p",
        "%b %d %I:%M%p",
        "%b %d at %I:%M%p",
        "%b %d at %I%p",
        "%b %d at %I:%M %p",
        "%b %d, %Y %I:%M %p",
    ]

    for fmt in absolute_fmts:
        for candidate in (cleaned, cleaned.lower()):
            try:
                t = datetime.strptime(candidate, fmt)
            except ValueError:
                continue
            if t.year == 1900:
                t = t.replace(year=now.year)
            delta = (t - now).total_seconds() / 3600
            if 0 < delta <= CLAUDE_SESSION_RESET_MAX_HOURS:
                return t
            return None

    for fmt in time_only_fmts:
        try:
            t = datetime.strptime(cleaned.lower(), fmt)
        except ValueError:
            continue
        t = t.replace(year=now.year, month=now.month, day=now.day)
        if t <= now:
            t += timedelta(days=1)
        delta = (t - now).total_seconds() / 3600
        if 0 < delta <= CLAUDE_SESSION_RESET_MAX_HOURS:
            return t
        return None
    return None


def parse_claude_session_hours_left(reset_str: str | None, now: datetime | None = None) -> float | None:
    """Parse Claude's rolling five-hour session reset into hours remaining."""
    relative = _relative_hours_left(reset_str)
    if relative is not None:
        return relative
    if not reset_str:
        return None

    now = now or datetime.now()
    reset_at = _claude_session_reset_datetime(reset_str, now)
    if reset_at is None:
        return None
    return round((reset_at - now).total_seconds() / 3600, 1)


def normalize_claude_session_reset(reset_str: str | None, now: datetime | None = None) -> str | None:
    """Normalize Claude's rolling five-hour reset without inventing tomorrow."""
    if not reset_str:
        return reset_str
    cleaned = reset_str.strip()
    if _relative_hours_left(cleaned) is not None:
        return cleaned

    now = now or datetime.now()
    reset_at = _claude_session_reset_datetime(cleaned, now)
    if reset_at is None:
        return None
    return reset_at.strftime("%b %-d %-I:%M %p")


def normalize_reset(s: str | None) -> str | None:
    """Normalize reset strings to consistent 'Apr 6 1:00 AM' format."""
    if not s:
        return s
    s = s.strip()
    now = datetime.now()
    for fmt in ["%b %d %I:%M %p", "%b %d at %I:%M%p", "%b %d at %I%p", "%b %d at %I:%M %p"]:
        try:
            t = datetime.strptime(s, fmt).replace(year=now.year)
            return t.strftime("%b %-d %-I:%M %p")
        except ValueError:
            continue
    for fmt in ["%I%p", "%I:%M%p", "%I:%M %p", "%I %p"]:
        try:
            t = datetime.strptime(s.lower(), fmt)
            t = t.replace(year=now.year, month=now.month, day=now.day)
            if t < now:
                t += timedelta(days=1)
            return t.strftime("%b %-d %-I:%M %p")
        except ValueError:
            continue
    return s


def compute_risk_outlook(burn: float, session_pct: float, pace: dict, codex: dict) -> str:
    """One sentence summarizing trajectory across all tools."""
    risks = []
    if burn > 0 and session_pct < 100:
        remaining = 100 - session_pct
        hours_left = remaining / burn
        if hours_left < 2:
            risks.append(f"Claude session: ~{hours_left:.0f}h at current pace")
    if pace.get("pace_status") == "front_loaded":
        risks.append(f"Claude front-loaded ({pace['projected_pct']:.0f}% projected)")
    cdx_burn = codex_burn_rate()
    if cdx_burn > 0 and codex:
        cdx_sess = codex.get("session_remaining_pct")
        if cdx_sess is not None and cdx_sess < 100:
            cdx_hours = cdx_sess / cdx_burn
            if cdx_hours < 2:
                risks.append(f"Codex session: ~{cdx_hours:.0f}h at current pace")
    if risks:
        return "⚠ " + " · ".join(risks)
    days_left = pace.get("days_remaining")
    pace_pct = pace.get("projected_pct", 0)
    if days_left:
        return f"Claude week: {pace_pct:.0f}% projected · {days_left:.1f}d left"
    return "All clear"


def _empty_provider_snapshot(provider_id: str) -> dict:
    return {
        "provider": provider_id,
        "timestamp": None,
        "status": "stale",
        "shared": {
            "primary_used_pct": None,
            "primary_remaining_pct": None,
            "primary_reset": None,
            "secondary_used_pct": None,
            "secondary_remaining_pct": None,
            "secondary_reset": None,
            "tokens_total_day": None,
            "messages_total_day": None,
            "active_hours_day": None,
        },
        "unique": {},
        "source": {},
        "error_text": None,
    }


def _providers_latest_payload() -> dict[str, dict]:
    latest = latest_provider_metric_samples()
    payload: dict[str, dict] = {}
    for item in provider_registry():
        provider_id = str(item["id"])
        payload[provider_id] = latest.get(provider_id) or _empty_provider_snapshot(provider_id)
    return payload


def _normalize_claude_code_stats_payload(raw: dict | None) -> dict:
    if not raw:
        return empty_claude_code_stats()
    if "dailyActivity" in raw or "totalSessions" in raw:
        return shape_claude_code_stats(raw)
    shaped = empty_claude_code_stats()
    shaped.update(raw)
    return shaped


def _model_display_name(model: str) -> str:
    return model.replace("claude-", "").replace("-", " ").title()


def _with_recent_claude_daily_tokens(stats: dict) -> dict:
    recent = scan_cc_daily_model_tokens(days=30)
    if not recent:
        return stats

    merged: dict[str, dict] = {}
    for entry in stats.get("daily_tokens") or []:
        if not isinstance(entry, dict) or not entry.get("date"):
            continue
        merged[entry["date"]] = {
            "date": entry["date"],
            "tokens_by_model": dict(entry.get("tokens_by_model") or {}),
        }

    for entry in recent:
        merged[entry["date"]] = entry

    updated = dict(stats)
    updated["daily_tokens"] = [merged[day] for day in sorted(merged)]

    model_totals: dict[str, int] = {}
    for entry in updated["daily_tokens"]:
        for model, tokens in (entry.get("tokens_by_model") or {}).items():
            model_totals[model] = model_totals.get(model, 0) + int(tokens or 0)
    if model_totals:
        updated["total_tokens_by_model"] = model_totals
        favorite = max(model_totals, key=model_totals.get)
        updated["favorite_model"] = _model_display_name(favorite)
        updated["models_used"] = sorted(model_totals)

    return updated


def _normalize_codex_local_payload(raw: dict | None) -> dict:
    normalized = empty_codex_thread_stats()
    if not raw:
        return normalized

    # Compatibility-only aliases are accepted only at this boundary.
    normalized["total_tokens"] = raw.get("total_tokens")
    for canonical_field, aliases in CODEX_LEGACY_LOCAL_ALIAS_MAP.items():
        normalized[canonical_field] = _first_present(raw, canonical_field, *aliases)
    normalized["today_tokens"] = raw.get("today_tokens")
    normalized["recent_threads"] = raw.get("recent_threads", []) or []

    by_model: dict[str, dict] = {}
    for model, info in (raw.get("by_model") or {}).items():
        if not isinstance(info, dict):
            continue
        by_model[model] = {
            "tokens": info.get("tokens"),
            "threads": info.get("threads", info.get("sessions")),
        }
    normalized["by_model"] = by_model

    today_by_model: dict[str, dict] = {}
    for model, info in (raw.get("today_by_model") or {}).items():
        if not isinstance(info, dict):
            continue
        today_by_model[model] = {
            "tokens": info.get("tokens"),
            "threads": info.get("threads", info.get("sessions")),
        }
    normalized["today_by_model"] = today_by_model
    if raw.get("by_source"):
        normalized["by_source"] = raw["by_source"]
    return normalized


def _normalize_codex_session_payload(raw: dict | None) -> dict:
    normalized = empty_codex_session_stats()
    if not raw:
        return normalized

    # Compatibility-only aliases are accepted only at this boundary.
    for canonical_field, aliases in CODEX_LEGACY_SESSION_ALIAS_MAP.items():
        normalized[canonical_field] = _first_present(raw, canonical_field, *aliases)
    normalized["total_sessions"] = raw.get("total_sessions")
    normalized["events_scanned"] = raw.get("events_scanned")
    return normalized


def _normalize_codex_quota_payload(raw: dict | None) -> dict:
    if not raw:
        return {
            "timestamp": None,
            "session_used_pct": None,
            "weekly_used_pct": None,
            "code_review_used_pct": None,
            "weekly_gpt54_used_pct": None,
            "weekly_spark_used_pct": None,
            "session_remaining_pct": None,
            "weekly_remaining_pct": None,
            "code_review_remaining_pct": None,
            "session_reset": None,
            "weekly_reset": None,
        }

    session_remaining_pct = raw.get("session_remaining_pct")
    weekly_remaining_pct = raw.get("weekly_remaining_pct")
    review_remaining_pct = raw.get("code_review_remaining_pct")
    gpt54_remaining = raw.get("weekly_gpt54_remaining_pct")
    spark_remaining = raw.get("weekly_spark_remaining_pct")
    return {
        "timestamp": raw.get("timestamp"),
        "session_used_pct": 100 - session_remaining_pct if session_remaining_pct is not None else None,
        "weekly_used_pct": 100 - weekly_remaining_pct if weekly_remaining_pct is not None else None,
        "code_review_used_pct": 100 - review_remaining_pct if review_remaining_pct is not None else None,
        "weekly_gpt54_used_pct": 100 - gpt54_remaining if gpt54_remaining is not None else None,
        "weekly_spark_used_pct": 100 - spark_remaining if spark_remaining is not None else None,
        "session_remaining_pct": session_remaining_pct,
        "weekly_remaining_pct": weekly_remaining_pct,
        "code_review_remaining_pct": review_remaining_pct,
        "session_reset": raw.get("session_reset"),
        "weekly_reset": raw.get("weekly_reset") or raw.get("reset_at"),
    }


def _claude_today_contract(cc_today: dict, cc_tokens: dict) -> dict:
    # Per-model breakdown for today: tokens = input + output + cache
    # (consistent with input_tokens_today, which folds cache in).
    models_today: dict[str, dict] = {}
    for model, info in (cc_tokens.get("by_model") or {}).items():
        if not isinstance(info, dict):
            continue
        models_today[model] = {
            "tokens": (info.get("input") or 0)
            + (info.get("output") or 0)
            + (info.get("cache_read") or 0)
            + (info.get("cache_create") or 0),
            "requests": info.get("requests"),
        }
    return {
        "active_hours_today": cc_today.get("active_hours"),
        "messages_today": cc_today.get("total_messages"),
        "output_tokens_today": cc_tokens.get("output_tokens"),
        "input_tokens_today": (cc_tokens.get("input_tokens") or 0) + (cc_tokens.get("cache_read_tokens") or 0) + (cc_tokens.get("cache_create_tokens") or 0),
        "threads_today": None,
        "sessions_today": None,
        "conversations_today": cc_today.get("conversations"),
        "models_today": models_today,
    }


def _claude_totals_contract(cc_stats_payload: dict) -> dict:
    return {
        "total_threads": None,
        "total_sessions": cc_stats_payload.get("total_sessions"),
        "total_messages": cc_stats_payload.get("total_messages"),
        "favorite_model": cc_stats_payload.get("favorite_model"),
    }


def _codex_today_contract(codex_local_payload: dict, codex_session_payload: dict) -> dict:
    models_today: dict[str, dict] = {}
    for model, info in (codex_local_payload.get("today_by_model") or {}).items():
        if not isinstance(info, dict):
            continue
        models_today[model] = {
            "tokens": info.get("tokens"),
            "requests": info.get("threads"),
        }
    return {
        "active_hours_today": codex_session_payload.get("active_hours_today"),
        "messages_today": codex_session_payload.get("messages_today"),
        "output_tokens_today": codex_session_payload.get("output_tokens_today"),
        "input_tokens_today": codex_session_payload.get("input_tokens_today"),
        "threads_today": codex_local_payload.get("today_threads"),
        "sessions_today": codex_session_payload.get("sessions_today"),
        "user_messages_today": codex_session_payload.get("user_messages_today"),
        "reasoning_tokens_today": codex_session_payload.get("reasoning_tokens_today"),
        "models_today": models_today,
    }


def _codex_totals_contract(codex_local_payload: dict, codex_session_payload: dict) -> dict:
    return {
        "total_threads": codex_local_payload.get("total_threads"),
        "total_sessions": codex_session_payload.get("total_sessions"),
        "total_tokens": codex_local_payload.get("total_tokens"),
    }


# ── App ──────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app):
    init()
    yield

app = FastAPI(
    title="Usage Tracker (minimal)",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    return response


# ── Sentinel cookie refresh ──────────────────────────────

class SentinelReport(BaseModel):
    cookies: dict[str, str]


_SENTINEL_ALLOWED_PROVIDERS = {"claude", "codex", "cursor"}

_SENTINEL_ENV_VAR_MAP: dict[str, str] = {
    "claude": "CLAUDE_WEB_COOKIE_FILE",
    "codex": "CODEX_WEB_COOKIE_FILE",
    "cursor": "CURSOR_WEB_COOKIE_FILE",
}


def _update_env_with_cookies(reports: dict[str, str]):
    base_dir = Path.home() / ".usage-tracker"
    base_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    env_file = Path(__file__).resolve().parent.parent / ".env"

    env_content = ""
    if env_file.exists():
        env_content = env_file.read_text()

    for provider, cookie_val in reports.items():
        if provider not in _SENTINEL_ALLOWED_PROVIDERS:
            continue
        cookie_file = base_dir / f"{provider}-cookie.txt"
        cookie_file.write_text(cookie_val)
        cookie_file.chmod(0o600)

        env_var = _SENTINEL_ENV_VAR_MAP[provider]
        line = f'export {env_var}="{cookie_file}"'
        if env_var in env_content:
            env_content = re.sub(re.escape(f'export {env_var}="') + '.*"', line, env_content)
        else:
            env_content += f"\n{line}"

    env_file.write_text(env_content.strip() + "\n")


@app.post("/sentinel/report")
async def sentinel_report(report: SentinelReport, _ = Depends(verify_auth)):
    _update_env_with_cookies(report.cookies)
    sync()
    return {"status": "ok", "received": list(report.cookies.keys())}


# ── CC stats cache (pushed by collector.py) ──────────────

_remote_cc: dict = {
    "messages": None,
    "tokens": None,
    "projects": None,
    "codex_local": None,
    "codex_sessions": None,
    "cc_stats": None,
    "usage": None,
    "codex_usage": None,
    "codex_analytics": None,
    "cursor_usage": None,
    "provider_snapshots": None,
    "ts": 0,
}
_REMOTE_CC_TTL = 120  # consider stale after 2 min


class CCReport(BaseModel):
    messages: dict
    tokens: dict
    projects: list
    codex_local: dict | None = None
    cc_stats: dict | None = None
    usage: dict | None = None
    codex_usage: dict | None = None
    codex_analytics: dict | None = None
    codex_sessions: dict | None = None
    cursor_usage: dict | None = None
    provider_snapshots: dict[str, dict] | None = None


def _remote_cc_fresh() -> bool:
    return _remote_cc["ts"] > 0 and (time.time() - _remote_cc["ts"]) < _REMOTE_CC_TTL


def _codex_local_payload() -> dict:
    if _remote_cc_fresh() and _remote_cc["codex_local"]:
        return _normalize_codex_local_payload(_remote_cc["codex_local"])
    return codex_local_stats()


def _codex_analytics_summary() -> dict | None:
    """Surface breakdown (CLI/Web/IDE) from Codex local SQLite."""
    cl = _codex_local_payload()
    by_source = cl.get("by_source")
    if not by_source:
        return None
    total = sum(s["sessions"] for s in by_source.values())
    if total == 0:
        return None
    dominant = max(by_source.items(), key=lambda x: x[1]["sessions"])
    share = round(dominant[1]["sessions"] / total * 100)
    return {
        "dominant_surface": dominant[0],
        "dominant_share_pct": share,
        "surfaces": {k: v["sessions"] for k, v in by_source.items()},
        "total_threads": total,
    }


def _overlay_self_quota(quota: dict, provider_id: str, stale: bool = False) -> None:
    """Fill quota gauges from self-imposed caps when scraped data is unusable.

    Mutates `quota` in place. Only applies when the scraped session pct is
    missing or flagged stale, so subscription scraping always wins when live.
    """
    if quota.get("session_used_pct") is not None and not stale:
        return
    try:
        from src.self_quota import self_quota_snapshot
        snapshot = self_quota_snapshot(provider_id)
    except Exception:
        return
    if not snapshot:
        return
    for key in (
        "session_used_pct", "weekly_used_pct",
        "session_remaining_pct", "weekly_remaining_pct",
    ):
        if snapshot.get(key) is not None:
            quota[key] = snapshot[key]
    quota["source"] = snapshot["source"]
    quota["self_quota"] = {
        "window_hours": snapshot["window_hours"],
        "weekly_days": snapshot["weekly_days"],
        "session": snapshot["session_detail"],
        "weekly": snapshot["weekly_detail"],
        "models": snapshot.get("models") or {},
    }


# ── Endpoints ────────────────────────────────────────────

_stats_cache: dict | None = None
_stats_cache_ts: float = 0


@app.get("/stats")
def stats(_auth: None = Depends(verify_auth)) -> dict:
    global _stats_cache, _stats_cache_ts
    now = time.time()
    if _stats_cache is not None and (now - _stats_cache_ts) < 15:
        return _stats_cache

    sample = latest_sample()
    if not sample:
        # Local mode / fresh install: no scraped quota samples exist.
        # API-based access / fresh install: no scraped quota samples exist.
        # Self-imposed quotas (plans.toml [<provider>.self_quota]) can still
        # populate the gauges, so continue with an empty sample.
        sample = {
            "timestamp": None,
            "session": None,
            "weekly": None,
            "extra": 0.0,
            "session_reset": None,
            "weekly_reset": None,
            "extra_reset": None,
            "extra_spent_usd": None,
            "extra_limit_usd": None,
            "extra_balance_usd": None,
        }

    burn = session_burn_rate()
    eta = predict_lock(sample["session"]) if sample["session"] is not None else None

    codex_quota = _normalize_codex_quota_payload(latest_codex())
    codex_cycles = codex_last_completed_cycles()
    codex_quota["session_last_cycle_used_pct"] = codex_cycles["session_used_pct"]
    codex_quota["weekly_last_cycle_used_pct"] = codex_cycles["weekly_used_pct"]
    fresh = _remote_cc_fresh()

    cc_today = (_remote_cc["messages"] if fresh else cc_messages_today()) or {}
    cc_tokens = (_remote_cc["tokens"] if fresh else cc_token_usage_today_cached()) or {}
    cc_stats_payload = (
        _normalize_claude_code_stats_payload(_remote_cc["cc_stats"])
        if fresh
        else shape_claude_code_stats(load_claude_code_stats())
    )
    cc_stats_payload = _with_recent_claude_daily_tokens(cc_stats_payload)
    codex_local_payload = (
        _normalize_codex_local_payload(_remote_cc["codex_local"])
        if fresh
        else codex_local_stats()
    )
    codex_session_payload = (
        _normalize_codex_session_payload(_remote_cc["codex_sessions"])
        if fresh
        else scan_codex_session_metrics()
    )
    claude_today = _claude_today_contract(cc_today, cc_tokens)
    codex_today = _codex_today_contract(codex_local_payload, codex_session_payload)
    claude_totals = _claude_totals_contract(cc_stats_payload)
    codex_totals = _codex_totals_contract(codex_local_payload, codex_session_payload)
    pace = weekly_utilization_pace()
    streak = current_streak()

    # Productivity metrics from today's token data
    today_active_hours = claude_today["active_hours_today"] or 0
    today_output = claude_today["output_tokens_today"] or 0
    today_density = output_density(today_output, today_active_hours)
    today_cache = cache_health(
        cc_tokens.get("cache_read_tokens", 0),
        cc_tokens.get("cache_create_tokens", 0),
    )

    cc_session_left = parse_claude_session_hours_left(sample["session_reset"])
    if codex_quota.get("session_reset"):
        codex_quota["session_reset"] = normalize_reset(codex_quota["session_reset"])
    if codex_quota.get("weekly_reset"):
        codex_quota["weekly_reset"] = normalize_reset(codex_quota["weekly_reset"])
    # Freshness: insert() only fires when the collector successfully scrapes
    # Claude subscription quota. If both the web and tmux paths fail (e.g.
    # broken /usage parser, expired cookie), no new row is written and
    # latest_sample() returns the last good values — stale, but
    # indistinguishable unless we flag them. Threshold is 2× the collector
    # cycle (5 min) plus
    # margin, giving one missed cycle of grace.
    CLAUDE_QUOTA_STALE_AFTER = 660  # seconds (11 min)
    quota_age = max(0, int(now - (sample.get("timestamp") or now)))
    quota_stale = quota_age > CLAUDE_QUOTA_STALE_AFTER
    claude_quota = {
        "session_used_pct": sample["session"],
        "weekly_used_pct": sample["weekly"],
        "weekly_sonnet_used_pct": sample.get("weekly_sonnet_pct"),
        "weekly_design_used_pct": sample.get("weekly_design_pct"),
        "session_remaining_pct": 100 - sample["session"] if sample["session"] is not None else None,
        "weekly_remaining_pct": 100 - sample["weekly"] if sample["weekly"] is not None else None,
        "session_reset": normalize_claude_session_reset(sample["session_reset"]),
        "weekly_reset": normalize_reset(sample["weekly_reset"]),
        "age_seconds": quota_age,
        "is_stale": quota_stale,
        "stale_threshold_seconds": CLAUDE_QUOTA_STALE_AFTER,
    }

    # Self-imposed quotas: when the scraped quota is absent or stale
    # (Bedrock / Vertex / enterprise / API-based access), fill the gauges from
    # locally measured usage against plans.toml [<provider>.self_quota] caps.
    _overlay_self_quota(claude_quota, "claude", stale=quota_stale)
    _overlay_self_quota(codex_quota, "codex")

    try:
        from src.gap_rollups import gap_rollups_for_stats
        gap_rollups = gap_rollups_for_stats()
    except Exception:
        gap_rollups = None

    result = {
        "timestamp": sample["timestamp"],
        "provider_registry": provider_registry(),
        "providers_latest": _providers_latest_payload(),
        "risk_outlook": compute_risk_outlook(burn, claude_quota["session_used_pct"] or 0, pace, codex_quota),
        "extra": sample["extra"],
        "extra_reset": sample["extra_reset"],
        "extra_spent_usd": sample["extra_spent_usd"],
        "extra_limit_usd": sample["extra_limit_usd"],
        "extra_balance_usd": sample["extra_balance_usd"],
        # Pacing — Claude
        "burn": burn,
        "workload": workload_label(burn),
        "lock_eta": eta,
        # Pacing — Codex
        "codex_burn": codex_burn_rate(),
        # Pacing — shared
        "weekly_pace": pace,
        "streak": streak,
        # Productivity
        "output_density": today_density,
        "cache_health": today_cache,
        # 4-state gap rollups (focus / attention-idle / off-hours / agent-runtime).
        # Rolled up over today, yesterday, and last 7d.
        # Legacy human_time_sec is retained (= focus + attention); downtime_sec is
        # zeroed because the legacy "downtime" bucket folded into attention_idle.
        "gap_rollups": gap_rollups,
        # Normalized provider contract
        "claude_today": claude_today,
        "codex_today": codex_today,
        "claude_totals": claude_totals,
        "codex_totals": codex_totals,
        "claude_quota": claude_quota,
        "codex_quota": codex_quota,
        "codex_analytics_summary": _codex_analytics_summary(),
        "cursor": _remote_cc.get("cursor_usage") if fresh else None,
        "cc_session_hours_left": cc_session_left,
    }
    _stats_cache = result
    _stats_cache_ts = time.time()
    return result


@app.post("/cc/report")
def cc_report(report: CCReport, _auth: None = Depends(verify_auth)) -> dict:
    _remote_cc["messages"] = report.messages
    _remote_cc["tokens"] = report.tokens
    _remote_cc["projects"] = report.projects
    _remote_cc["codex_local"] = report.codex_local
    _remote_cc["codex_sessions"] = report.codex_sessions
    _remote_cc["cc_stats"] = report.cc_stats
    _remote_cc["usage"] = report.usage
    _remote_cc["codex_usage"] = report.codex_usage
    if report.codex_analytics is not None:
        _remote_cc["codex_analytics"] = report.codex_analytics
    if report.cursor_usage is not None:
        _remote_cc["cursor_usage"] = report.cursor_usage
    if report.provider_snapshots is not None:
        _remote_cc["provider_snapshots"] = report.provider_snapshots
    _remote_cc["ts"] = time.time()

    # If codex usage data included, insert into DB for history
    if report.codex_usage:
        cu = report.codex_usage
        insert_codex(
            cu.get("weekly_remaining_pct"),
            cu.get("code_review_remaining_pct"),
            cu.get("weekly_reset") or cu.get("reset_at"),
            session_remaining_pct=cu.get("session_remaining_pct"),
            session_reset=cu.get("session_reset"),
            weekly_gpt54_remaining_pct=cu.get("weekly_gpt54_remaining_pct"),
            weekly_spark_remaining_pct=cu.get("weekly_spark_remaining_pct"),
        )

    # If usage data included, also insert into the DB so history works
    if report.usage:
        u = report.usage
        insert(
            u.get("session_pct", 0),
            u.get("weekly_pct", 0),
            u.get("extra_pct", 0),
            session_reset=u.get("session_reset"),
            weekly_reset=u.get("weekly_reset"),
            extra_reset=u.get("extra_reset"),
            extra_spent_usd=u.get("extra_spent_usd"),
            extra_limit_usd=u.get("extra_limit_usd"),
            extra_balance_usd=u.get("extra_balance_usd"),
            weekly_sonnet_pct=u.get("weekly_sonnet_pct"),
            weekly_design_pct=u.get("weekly_design_pct"),
        )

    snapshots_map = report.provider_snapshots
    if snapshots_map is None:
        snapshots_map = build_provider_snapshots(
            timestamp=int(_remote_cc["ts"] or time.time()),
            collector_access="subscription",
            messages=report.messages,
            tokens=report.tokens,
            usage=report.usage,
            codex_local=report.codex_local,
            codex_sessions=report.codex_sessions,
            codex_usage=report.codex_usage,
            cursor_usage=report.cursor_usage,
            errors=None,
        )
    if isinstance(snapshots_map, dict):
        snapshots = [snapshot for snapshot in snapshots_map.values() if isinstance(snapshot, dict)]
        if snapshots:
            insert_provider_metric_samples(snapshots)
    prune_provider_metric_samples(retention_days=180)

    return {"status": "ok"}


@app.get("/budget/weekly")
def budget_weekly_api(_auth: None = Depends(verify_auth)) -> dict:
    """Weekly forecast with day-by-day quota projections through reset."""
    plans = load_plans()
    if not plans:
        return {"forecasts": {}}
    providers = _providers_latest_payload()
    return weekly_forecast(plans, providers)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


if __name__ == "__main__":
    # Hard-locked to loopback. Remote access must go through SSH/tailscale, not direct exposure.
    import uvicorn

    uvicorn.run("src.api:app", host="127.0.0.1", port=8000)
