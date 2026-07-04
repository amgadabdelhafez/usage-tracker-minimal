"""Load and validate the user's plan configuration from ~/.usage-tracker/plans.toml.

Also defines the model tier registry used by the weekly forecast: which models
are tier 1 (flagship), tier 2 (mid-range), or tier 3 (lightweight), and their
quota cost multipliers.
"""

import time
import tomllib
from pathlib import Path
from typing import Any

PLANS_PATH = Path.home() / ".usage-tracker" / "plans.toml"

# Model tier registry: tier + quota_weight (how much quota a model burns
# relative to the "standard" tier-2 model). Anthropic charges Opus ~1.7x
# more quota than Sonnet; Haiku is ~0.1x.
MODEL_TIERS: dict[str, dict[str, Any]] = {
    # Claude
    "claude-fable-5": {"tier": 1, "label": "Fable 5", "provider": "claude", "quota_weight": 1.7},
    "claude-opus-4-8": {"tier": 1, "label": "Opus 4.8", "provider": "claude", "quota_weight": 1.7},
    "claude-opus-4-6": {"tier": 1, "label": "Opus 4.6", "provider": "claude", "quota_weight": 1.7},
    "claude-sonnet-5": {"tier": 2, "label": "Sonnet 5", "provider": "claude", "quota_weight": 1.0},
    "claude-sonnet-4-6": {"tier": 2, "label": "Sonnet 4.6", "provider": "claude", "quota_weight": 1.0},
    "claude-haiku-4-5-20251001": {"tier": 3, "label": "Haiku 4.5", "provider": "claude", "quota_weight": 0.1},
    # Codex / OpenAI
    "gpt-5.4": {"tier": 1, "label": "GPT-5.4", "provider": "codex", "quota_weight": 1.0},
    "gpt-5.4-mini": {"tier": 2, "label": "GPT-5.4 Mini", "provider": "codex", "quota_weight": 0.3},
    "gpt-5.3-codex": {"tier": 2, "label": "GPT-5.3 Codex", "provider": "codex", "quota_weight": 0.5},
    "gpt-5.3-codex-spark": {"tier": 3, "label": "GPT-5.3 Spark", "provider": "codex", "quota_weight": 0.2},
    "gpt-5.1-codex-mini": {"tier": 3, "label": "GPT-5.1 Mini", "provider": "codex", "quota_weight": 0.1},
}

TIER_LABELS = {1: "Tier 1 (Flagship)", 2: "Tier 2 (Standard)", 3: "Tier 3 (Lightweight)"}

# API list prices in USD per million tokens, keyed by model-name prefix
# (longest prefix wins). Used only for cost-basis self-imposed quotas
# (API-based access setups). Override per provider via
# [<provider>.self_quota.pricing."<model-prefix>"] in plans.toml.
MODEL_COSTS: dict[str, dict[str, float]] = {
    # Claude (per-Mtok: input, output, cache_read ≈ 0.1x in, cache_write ≈ 1.25x in)
    "claude-fable": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-opus": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_write": 18.75},
    "claude-sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
    "claude-haiku": {"input": 1.0, "output": 5.0, "cache_read": 0.1, "cache_write": 1.25},
    # OpenAI / Codex (verify against current price list before relying on these)
    "gpt-5": {"input": 1.25, "output": 10.0, "cache_read": 0.125, "cache_write": 1.25},
    # Fallback when no prefix matches
    "default": {"input": 3.0, "output": 15.0, "cache_read": 0.3, "cache_write": 3.75},
}


def model_cost_rates(model: str, pricing: dict[str, dict[str, float]] | None = None) -> dict[str, float]:
    """Return per-Mtok rates for a model via longest-prefix match."""
    table = {**MODEL_COSTS, **(pricing or {})}
    best_key = None
    for prefix in table:
        if prefix != "default" and model.startswith(prefix):
            if best_key is None or len(prefix) > len(best_key):
                best_key = prefix
    return table[best_key] if best_key else table["default"]


def self_quota_config(provider_id: str, plans: dict | None = None) -> dict[str, Any] | None:
    """Parse [<provider>.self_quota] from plans.toml.

    Returns a normalized dict or None when not configured. Caps may be
    token-based (session_cap_tokens / weekly_cap_tokens) or cost-based
    (session_cap_usd / weekly_cap_usd); at least one cap must be set.
    """
    plans = plans if plans is not None else load_plans()
    provider = plans.get(provider_id)
    if not isinstance(provider, dict):
        return None
    raw = provider.get("self_quota")
    if not isinstance(raw, dict):
        return None

    _CAP_KEYS = (
        "session_cap_tokens", "weekly_cap_tokens",
        "session_cap_usd", "weekly_cap_usd",
    )

    def _caps(table: dict) -> dict:
        return {k: table.get(k) for k in _CAP_KEYS}

    cfg = {
        "window_hours": float(raw.get("window_hours", 5)),
        "weekly_days": float(raw.get("weekly_days", 7)),
        **_caps(raw),
        "pricing": raw.get("pricing") if isinstance(raw.get("pricing"), dict) else None,
        # Per-model caps: [<provider>.self_quota.models."<model-prefix>"]
        "models": {
            prefix: _caps(caps)
            for prefix, caps in (raw.get("models") or {}).items()
            if isinstance(caps, dict) and any(caps.get(k) for k in _CAP_KEYS)
        },
    }
    has_cap = any(cfg[k] for k in _CAP_KEYS) or bool(cfg["models"])
    return cfg if has_cap else None

_cache: dict[str, Any] | None = None
_cache_ts: float = 0
_CACHE_TTL = 60  # seconds


def load_plans(path: Path | None = None) -> dict[str, Any]:
    """Load plans config. Returns empty dict if file missing. Cached for 60s."""
    global _cache, _cache_ts
    p = path or PLANS_PATH
    now = time.time()
    if _cache is not None and (now - _cache_ts) < _CACHE_TTL:
        return _cache
    if not p.exists():
        _cache = {}
        _cache_ts = now
        return _cache
    with open(p, "rb") as f:
        _cache = tomllib.load(f)
    _cache_ts = now
    return _cache


def total_monthly_cost(plans: dict | None = None) -> float:
    """Sum of cost_usd_month across all configured providers."""
    plans = plans or load_plans()
    return sum(
        float(config.get("cost_usd_month", 0))
        for config in plans.values()
        if isinstance(config, dict)
    )


def invalidate_cache() -> None:
    """Force reload on next call. Useful for tests."""
    global _cache, _cache_ts
    _cache = None
    _cache_ts = 0
