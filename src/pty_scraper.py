"""Scrapers for AI tool usage data:
- Claude Code: tmux-based /usage scrape
- Codex: app-server JSON-RPC
- Cursor: api2.cursor.sh REST API
"""

import json
import os
import re
import select
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

def _find_bin(name: str) -> str:
    """Find binary via PATH, with Homebrew fallback."""
    found = shutil.which(name)
    if found:
        return found
    # Homebrew defaults
    for prefix in ["/opt/homebrew/bin", "/usr/local/bin"]:
        path = f"{prefix}/{name}"
        if os.path.exists(path):
            return path
    return name  # fall through to PATH


def _strip_ansi(text):
    return re.sub(r'\x1b[\[\]()>=#][0-9;?]*[a-zA-Z\x07]?|\x1b.', '', text)


def _clean(text):
    return _strip_ansi(text).replace('\r\n', '\n').replace('\r', '')


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _normalize_json_key(key: str) -> str:
    key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    key = re.sub(r"[^a-zA-Z0-9]+", "_", key)
    return key.strip("_").lower()


def _iter_nodes(node, path=()):
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_nodes(value, path + (_normalize_json_key(key),))
    elif isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _iter_nodes(value, path + (f"item_{idx}",))


def _iter_leaves(node, path=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _iter_leaves(value, path + (_normalize_json_key(key),))
        return
    if isinstance(node, list):
        for idx, value in enumerate(node):
            yield from _iter_leaves(value, path + (f"item_{idx}",))
        return
    yield path, node


def _path_tokens(path: tuple[str, ...]) -> list[str]:
    tokens = []
    for part in path:
        tokens.extend(token for token in part.split("_") if token)
    return tokens


def _read_env_or_file(env_name: str, file_env_name: str) -> str | None:
    value = os.environ.get(env_name, "").strip()
    if value:
        return value

    file_value = os.environ.get(file_env_name, "").strip()
    if not file_value:
        return None

    try:
        return Path(file_value).expanduser().read_text().strip()
    except OSError:
        return None


def _normalize_cookie_header(cookie_text: str) -> str:
    cookie_text = cookie_text.strip()
    if cookie_text.lower().startswith("cookie:"):
        cookie_text = cookie_text.split(":", 1)[1].strip()
    return cookie_text


def _parse_cookie_header(cookie_text: str) -> dict[str, str]:
    cookies = {}
    if not cookie_text:
        return cookies
    for part in _normalize_cookie_header(cookie_text).split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies[name] = value.strip()
    return cookies


def _load_web_header_overrides(
    raw_env_name: str,
    file_env_name: str,
    label: str,
) -> dict[str, str]:
    raw = os.environ.get(raw_env_name, "").strip()
    if not raw:
        path = os.environ.get(file_env_name, "").strip()
        if path:
            try:
                raw = Path(path).expanduser().read_text().strip()
            except OSError as exc:
                raise RuntimeError(f"could not read {file_env_name}: {exc}") from exc
    if not raw:
        return {}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid {label} web headers JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"{label} web headers must be a JSON object")

    headers = {}
    for key, value in data.items():
        if value is None:
            continue
        text = str(value).strip()
        if text:
            headers[str(key)] = text
    return headers


def _load_claude_web_header_overrides() -> dict[str, str]:
    return _load_web_header_overrides(
        "CLAUDE_WEB_HEADERS_JSON",
        "CLAUDE_WEB_HEADERS_FILE",
        "Claude",
    )


def _load_codex_web_header_overrides() -> dict[str, str]:
    return _load_web_header_overrides(
        "CODEX_WEB_HEADERS_JSON",
        "CODEX_WEB_HEADERS_FILE",
        "Codex",
    )


def _format_reset_time(value) -> str | None:
    if value is None:
        return None

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        dt = datetime.fromtimestamp(ts).astimezone()
        return dt.strftime("%b %-d %-I:%M %p")

    if not isinstance(value, str):
        return None

    text = _normalize_whitespace(value)
    if not text:
        return None

    iso_text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso_text).astimezone()
        return dt.strftime("%b %-d %-I:%M %p")
    except ValueError:
        return text


def _pick_section(
    payload,
    include_tokens: tuple[str, ...],
    exclude_tokens: tuple[str, ...] = (),
    minimum_hits: int = 1,
) -> dict | None:
    candidates = []
    for path, node in _iter_nodes(payload):
        if not isinstance(node, dict) or not node:
            continue
        tokens = _path_tokens(path)
        if any(token in tokens for token in exclude_tokens):
            continue
        hits = sum(1 for token in include_tokens if token in tokens)
        if hits < minimum_hits:
            continue
        candidates.append((hits, len(path), node))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def _find_value_in_section(
    section,
    wanted_tokens: tuple[str, ...],
    *,
    value_predicate,
    forbidden_tokens: tuple[str, ...] = (),
):
    candidates = []
    for path, value in _iter_leaves(section):
        tokens = _path_tokens(path)
        if any(token in tokens for token in forbidden_tokens):
            continue
        if not value_predicate(value):
            continue
        hits = sum(1 for token in wanted_tokens if token in tokens)
        if hits < 1:
            continue
        candidates.append((hits, len(path), value))

    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]
    return None


def _merge_usage_pct_reset(
    result: dict,
    section,
    *,
    pct_key: str,
    pct_field: str,
    reset_key: str | None = None,
    reset_field: str | None = None,
) -> None:
    if not isinstance(section, dict):
        return

    pct_value = section.get(pct_field)
    if isinstance(pct_value, (int, float)) and not isinstance(pct_value, bool):
        result[pct_key] = int(round(float(pct_value)))

    if reset_key and reset_field:
        reset_value = _format_reset_time(section.get(reset_field))
        if reset_value:
            result[reset_key] = reset_value


def _parse_claude_web_usage_payload(payload: dict | list) -> dict:
    if not isinstance(payload, (dict, list)):
        return {}

    result = {}

    if isinstance(payload, dict):
        _merge_usage_pct_reset(
            result,
            payload.get("five_hour"),
            pct_key="session_pct",
            pct_field="utilization",
            reset_key="session_reset",
            reset_field="resets_at",
        )
        if "session_pct" not in result or "session_reset" not in result:
            _merge_usage_pct_reset(
                result,
                payload.get("currentSession"),
                pct_key="session_pct",
                pct_field="usedPercent",
                reset_key="session_reset",
                reset_field="resetAt",
            )
        _merge_usage_pct_reset(
            result,
            payload.get("seven_day"),
            pct_key="weekly_pct",
            pct_field="utilization",
            reset_key="weekly_reset",
            reset_field="resets_at",
        )
        _merge_usage_pct_reset(
            result,
            payload.get("seven_day_sonnet"),
            pct_key="weekly_sonnet_pct",
            pct_field="utilization",
        )
        _merge_usage_pct_reset(
            result,
            payload.get("seven_day_design"),
            pct_key="weekly_design_pct",
            pct_field="utilization",
        )

        weekly = payload.get("weekly")
        if isinstance(weekly, dict):
            _merge_usage_pct_reset(
                result,
                weekly.get("allModels"),
                pct_key="weekly_pct",
                pct_field="usedPercent",
                reset_key="weekly_reset",
                reset_field="resetAt",
            )
            _merge_usage_pct_reset(
                result,
                weekly.get("sonnetOnly"),
                pct_key="weekly_sonnet_pct",
                pct_field="usedPercent",
            )
            _merge_usage_pct_reset(
                result,
                weekly.get("designOnly"),
                pct_key="weekly_design_pct",
                pct_field="usedPercent",
            )

        extra_usage = payload.get("extra_usage")
        if not isinstance(extra_usage, dict):
            extra_usage = payload.get("extraUsage")
        if isinstance(extra_usage, dict):
            utilization = extra_usage.get("utilization")
            if not isinstance(utilization, (int, float)) or isinstance(utilization, bool):
                utilization = extra_usage.get("usedPercent")
            if isinstance(utilization, (int, float)) and not isinstance(utilization, bool):
                result["extra_pct"] = int(round(float(utilization)))
            reset_at = _format_reset_time(extra_usage.get("resets_at") or extra_usage.get("resetAt"))
            if reset_at:
                result["extra_reset"] = reset_at
            monthly_limit = extra_usage.get("monthly_limit")
            if monthly_limit is None:
                monthly_limit = extra_usage.get("limitUsd")
            used_credits = extra_usage.get("used_credits")
            if used_credits is None:
                used_credits = extra_usage.get("spentUsd")
            if isinstance(monthly_limit, (int, float)) and not isinstance(monthly_limit, bool):
                result["extra_limit_usd"] = float(monthly_limit)
            if isinstance(used_credits, (int, float)) and not isinstance(used_credits, bool):
                result["extra_spent_usd"] = float(used_credits)
                if "extra_limit_usd" in result:
                    result["extra_balance_usd"] = max(result["extra_limit_usd"] - result["extra_spent_usd"], 0.0)
            balance_usd = extra_usage.get("balanceUsd")
            if isinstance(balance_usd, (int, float)) and not isinstance(balance_usd, bool):
                result["extra_balance_usd"] = float(balance_usd)

        if _claude_usage_complete(result):
            return result

    session_section = _pick_section(
        payload,
        include_tokens=("session",),
        exclude_tokens=("week", "weekly", "sonnet", "extra"),
    )
    if session_section:
        session_pct = _find_value_in_section(
            session_section,
            ("used", "percent", "pct", "usage"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100,
            forbidden_tokens=("remaining", "reset", "time", "spent", "limit", "balance"),
        )
        session_reset = _find_value_in_section(
            session_section,
            ("reset", "resets", "time", "at"),
            value_predicate=lambda value: isinstance(value, (str, int, float)) and _format_reset_time(value) is not None,
        )
        if session_pct is not None:
            result["session_pct"] = int(round(float(session_pct)))
        if session_reset is not None:
            result["session_reset"] = _format_reset_time(session_reset)

    weekly_section = _pick_section(
        payload,
        include_tokens=("week", "weekly", "all", "models"),
        exclude_tokens=("session", "sonnet", "design", "extra"),
        minimum_hits=2,
    )
    if not weekly_section:
        weekly_section = _pick_section(
            payload,
            include_tokens=("all", "models"),
            exclude_tokens=("session", "sonnet", "design", "extra"),
            minimum_hits=2,
        )
    if weekly_section:
        weekly_pct = _find_value_in_section(
            weekly_section,
            ("used", "percent", "pct", "usage"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100,
            forbidden_tokens=("remaining", "reset", "time", "spent", "limit", "balance"),
        )
        weekly_reset = _find_value_in_section(
            weekly_section,
            ("reset", "resets", "time", "at"),
            value_predicate=lambda value: isinstance(value, (str, int, float)) and _format_reset_time(value) is not None,
        )
        if weekly_pct is not None:
            result["weekly_pct"] = int(round(float(weekly_pct)))
        if weekly_reset is not None:
            result["weekly_reset"] = _format_reset_time(weekly_reset)

    weekly_sonnet_section = _pick_section(
        payload,
        include_tokens=("week", "weekly", "sonnet"),
        exclude_tokens=("session", "design", "extra"),
        minimum_hits=2,
    )
    if not weekly_sonnet_section:
        weekly_sonnet_section = _pick_section(
            payload,
            include_tokens=("sonnet",),
            exclude_tokens=("session", "design", "extra"),
        )
    if weekly_sonnet_section:
        weekly_sonnet_pct = _find_value_in_section(
            weekly_sonnet_section,
            ("used", "percent", "pct", "usage"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100,
            forbidden_tokens=("remaining", "reset", "time", "spent", "limit", "balance"),
        )
        if weekly_sonnet_pct is not None:
            result["weekly_sonnet_pct"] = int(round(float(weekly_sonnet_pct)))

    weekly_design_section = _pick_section(
        payload,
        include_tokens=("week", "weekly", "design"),
        exclude_tokens=("session", "sonnet", "extra"),
        minimum_hits=2,
    )
    if not weekly_design_section:
        weekly_design_section = _pick_section(
            payload,
            include_tokens=("design",),
            exclude_tokens=("session", "sonnet", "extra"),
        )
    if weekly_design_section:
        weekly_design_pct = _find_value_in_section(
            weekly_design_section,
            ("used", "percent", "pct", "usage"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100,
            forbidden_tokens=("remaining", "reset", "time", "spent", "limit", "balance"),
        )
        if weekly_design_pct is not None:
            result["weekly_design_pct"] = int(round(float(weekly_design_pct)))

    extra_section = _pick_section(
        payload,
        include_tokens=("extra",),
        exclude_tokens=("session", "week", "weekly", "sonnet"),
    )
    if extra_section:
        extra_pct = _find_value_in_section(
            extra_section,
            ("used", "percent", "pct", "usage"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 100,
            forbidden_tokens=("remaining", "reset", "time", "spent", "limit", "balance"),
        )
        extra_reset = _find_value_in_section(
            extra_section,
            ("reset", "resets", "time", "at"),
            value_predicate=lambda value: isinstance(value, (str, int, float)) and _format_reset_time(value) is not None,
        )
        extra_spent = _find_value_in_section(
            extra_section,
            ("spent", "cost"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            forbidden_tokens=("percent", "pct", "used", "limit", "balance"),
        )
        extra_limit = _find_value_in_section(
            extra_section,
            ("limit", "max", "cap"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            forbidden_tokens=("percent", "pct", "used", "spent", "balance"),
        )
        extra_balance = _find_value_in_section(
            extra_section,
            ("balance", "remaining"),
            value_predicate=lambda value: isinstance(value, (int, float)) and not isinstance(value, bool),
            forbidden_tokens=("percent", "pct", "used"),
        )
        if extra_pct is not None:
            result["extra_pct"] = int(round(float(extra_pct)))
        if extra_reset is not None:
            result["extra_reset"] = _format_reset_time(extra_reset)
        if extra_spent is not None:
            result["extra_spent_usd"] = float(extra_spent)
        if extra_limit is not None:
            result["extra_limit_usd"] = float(extra_limit)
        if extra_balance is not None:
            result["extra_balance_usd"] = float(extra_balance)

    return result


def _claude_web_request_config() -> tuple[str, dict[str, str]] | None:
    cookie = _read_env_or_file("CLAUDE_WEB_COOKIE", "CLAUDE_WEB_COOKIE_FILE")
    cookie = _normalize_cookie_header(cookie) if cookie else None

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://claude.ai/settings/usage",
        "User-Agent": os.environ.get(
            "CLAUDE_WEB_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        ),
        "anthropic-client-platform": os.environ.get("CLAUDE_WEB_CLIENT_PLATFORM", "web_claude_ai"),
    }
    if cookie:
        headers["Cookie"] = cookie

    overrides = _load_claude_web_header_overrides()
    headers.update(overrides)
    if "Cookie" in headers:
        headers["Cookie"] = _normalize_cookie_header(headers["Cookie"])

    cookie_text = headers.get("Cookie", "")
    org_id = os.environ.get("CLAUDE_WEB_ORG_ID", "").strip()
    if not org_id and cookie_text:
        org_id = _parse_cookie_header(cookie_text).get("lastActiveOrg", "")

    if not org_id or not cookie_text:
        return None

    url = f"https://claude.ai/api/organizations/{org_id}/usage"
    return url, headers


def claude_web_usage_configured() -> bool:
    return _claude_web_request_config() is not None


def _codex_web_origin() -> str:
    source_url = (
        os.environ.get("CODEX_WEB_ANALYTICS_URL", "").strip()
        or os.environ.get("CODEX_WEB_USAGE_URL", "").strip()
        or "https://chatgpt.com/codex/cloud/settings/analytics"
    )
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return "https://chatgpt.com"


def _codex_web_request_config() -> tuple[str, dict[str, str]] | None:
    cookie = _read_env_or_file("CODEX_WEB_COOKIE", "CODEX_WEB_COOKIE_FILE")
    cookie = _normalize_cookie_header(cookie) if cookie else None

    headers = {
        "Accept": "application/json, text/plain, */*",
        "Referer": os.environ.get(
            "CODEX_WEB_ANALYTICS_URL",
            "https://chatgpt.com/codex/cloud/settings/analytics",
        ),
        "User-Agent": os.environ.get(
            "CODEX_WEB_USER_AGENT",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        ),
    }
    if cookie:
        headers["Cookie"] = cookie

    overrides = _load_codex_web_header_overrides()
    headers.update(overrides)
    if "Cookie" in headers:
        headers["Cookie"] = _normalize_cookie_header(headers["Cookie"])

    if not headers.get("Cookie"):
        return None

    return _codex_web_origin(), headers


def codex_web_analytics_configured() -> bool:
    return _codex_web_request_config() is not None


def _parse_bool_env(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _codex_analytics_window_days() -> int:
    raw = os.environ.get("CODEX_ANALYTICS_WINDOW_DAYS", "").strip()
    if not raw:
        return 30
    try:
        window_days = int(raw)
    except ValueError:
        return 30
    return max(1, min(window_days, 90))


def _codex_analytics_group_by() -> str:
    value = os.environ.get("CODEX_ANALYTICS_GROUP_BY", "day").strip().lower()
    return value if value in {"day", "week"} else "day"


def _codex_analytics_date_range(window_days: int) -> tuple[str, str]:
    end_date = datetime.now(timezone.utc).date() - timedelta(days=1)
    start_date = end_date - timedelta(days=max(window_days - 1, 0))
    return start_date.isoformat(), end_date.isoformat()


def _codex_analytics_helper_script() -> Path:
    return Path(__file__).resolve().parent.parent / "scripts" / "fetch_codex_web_analytics.mjs"


def _run_codex_analytics_browser_helper(payload: dict) -> dict:
    helper = _codex_analytics_helper_script()
    if not helper.exists():
        raise RuntimeError(f"Codex analytics helper missing: {helper}")

    try:
        proc = subprocess.run(
            [_find_bin("node"), str(helper)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("Codex analytics helper timed out") from exc
    except OSError as exc:
        raise RuntimeError(f"Codex analytics helper failed to start: {exc}") from exc

    if proc.returncode != 0:
        detail = _normalize_whitespace(proc.stderr) or f"exit {proc.returncode}"
        raise RuntimeError(f"Codex analytics helper failed: {detail}")

    stdout = proc.stdout.strip()
    if not stdout:
        raise RuntimeError("Codex analytics helper returned no data")

    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Codex analytics helper returned invalid JSON: {exc}") from exc

    if not isinstance(result, dict):
        raise RuntimeError("Codex analytics helper returned a non-object payload")
    return result


def _safe_number(value) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return 0.0


def _round_metric(value: float) -> float:
    return round(value, 1) if abs(value - round(value)) > 1e-9 else float(int(round(value)))


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)


def _summarize_codex_analytics(bundle: dict) -> dict:
    workspace_rows = bundle.get("daily_workspace_usage_counts", {}).get("data", []) or []
    workspace_users = []
    workspace_threads = []
    workspace_turns = []
    workspace_credits = []
    for row in workspace_rows:
        totals = row.get("totals", {}) if isinstance(row, dict) else {}
        workspace_users.append(_safe_number(totals.get("users")))
        workspace_threads.append(_safe_number(totals.get("threads")))
        workspace_turns.append(_safe_number(totals.get("turns")))
        workspace_credits.append(_safe_number(totals.get("credits")))

    session_rows = bundle.get("daily_sessions_messages_counts", {}).get("data", []) or []
    sessions_by_day: dict[str, dict[str, float]] = {}
    for row in session_rows:
        if not isinstance(row, dict):
            continue
        date_key = str(row.get("date", "")).strip()
        if not date_key:
            continue
        day = sessions_by_day.setdefault(
            date_key,
            {
                "sessions": 0.0,
                "messages": 0.0,
                "credits": 0.0,
                "users": 0.0,
                "tasks_web": 0.0,
                "code_reviews_web": 0.0,
            },
        )
        day["sessions"] += _safe_number(row.get("n_new_sessions_total"))
        day["messages"] += _safe_number(row.get("n_user_messages_total"))
        day["credits"] += _safe_number(row.get("credit_total"))
        day["users"] += _safe_number(row.get("n_users_used_codex"))
        day["tasks_web"] += _safe_number(row.get("n_tasks_web"))
        day["code_reviews_web"] += _safe_number(row.get("n_code_reviews_web"))

    session_days = list(sessions_by_day.values())
    review_rows = bundle.get("daily_code_review_metrics", {}).get("data", []) or []
    review_counts = []
    review_comments = []
    review_p0 = []
    review_p1 = []
    review_p2 = []
    for row in review_rows:
        if not isinstance(row, dict):
            continue
        review_counts.append(_safe_number(row.get("n_reviews")))
        review_comments.append(_safe_number(row.get("n_comments")))
        review_p0.append(_safe_number(row.get("n_comments_p0")))
        review_p1.append(_safe_number(row.get("n_comments_p1")))
        review_p2.append(_safe_number(row.get("n_comments_p2")))

    return {
        "workspace": {
            "days": len(workspace_rows),
            "avg_daily_users": _round_metric(_average(workspace_users)),
            "avg_daily_threads": _round_metric(_average(workspace_threads)),
            "avg_daily_turns": _round_metric(_average(workspace_turns)),
            "avg_daily_credits": _round_metric(_average(workspace_credits)),
            "peak_daily_users": _round_metric(max(workspace_users, default=0.0)),
        },
        "sessions_messages": {
            "days": len(session_days),
            "avg_daily_sessions": _round_metric(_average([row["sessions"] for row in session_days])),
            "avg_daily_user_messages": _round_metric(_average([row["messages"] for row in session_days])),
            "avg_daily_credits": _round_metric(_average([row["credits"] for row in session_days])),
            "avg_daily_users": _round_metric(_average([row["users"] for row in session_days])),
            "avg_daily_web_tasks": _round_metric(_average([row["tasks_web"] for row in session_days])),
            "avg_daily_web_reviews": _round_metric(_average([row["code_reviews_web"] for row in session_days])),
        },
        "code_review": {
            "days": len(review_rows),
            "avg_daily_reviews": _round_metric(_average(review_counts)),
            "avg_daily_comments": _round_metric(_average(review_comments)),
            "avg_daily_p0_comments": _round_metric(_average(review_p0)),
            "avg_daily_p1_comments": _round_metric(_average(review_p1)),
            "avg_daily_p2_comments": _round_metric(_average(review_p2)),
        },
    }


def scrape_codex_analytics() -> dict | None:
    """Fetch Codex analytics datasets from ChatGPT private web APIs."""
    config = _codex_web_request_config()
    if not config:
        return None

    origin, headers = config
    window_days = _codex_analytics_window_days()
    group_by = _codex_analytics_group_by()
    include_emails = _parse_bool_env("CODEX_ANALYTICS_INCLUDE_EMAILS", default=False)
    start_date, end_date = _codex_analytics_date_range(window_days)
    bundle = _run_codex_analytics_browser_helper(
        {
            "origin": origin,
            "analytics_page_url": os.environ.get(
                "CODEX_WEB_ANALYTICS_URL",
                f"{origin}/codex/cloud/settings/analytics",
            ),
            "cookie": headers.get("Cookie", ""),
            "window_days": window_days,
            "group_by": group_by,
            "include_emails": include_emails,
            "date_range": {
                "start_date": start_date,
                "end_date": end_date,
            },
        }
    )

    bundle.setdefault("window_days", window_days)
    bundle.setdefault("group_by", group_by)
    bundle.setdefault(
        "date_range",
        {
            "start_date": start_date,
            "end_date": end_date,
        },
    )
    bundle.setdefault("include_emails", include_emails)
    bundle.setdefault("daily_workspace_usage_counts", {"data": []})
    bundle.setdefault("daily_sessions_messages_counts", {"data": []})
    bundle.setdefault("daily_code_review_metrics", {"data": []})
    bundle["summary"] = _summarize_codex_analytics(bundle)
    return bundle


def _parse_claude_usage_text(usage_text: str, welcome_pane: str = "") -> dict:
    text = _clean(usage_text)
    result = {}

    # New format (2026+): "Resets <time> (<tz>)   NN% used" — all on one line
    # Old format: "Current session   NN% used\nResets <time>"
    # Try new format first: find a "Resets ... NN% used" line after "Current session"
    session_block = re.search(
        r"Current session(.*?)(?=Current week|Extra usage|Last updated|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if session_block:
        block = session_block.group(1)
        # New format: "Resets <time>   NN% used" on one line
        m = re.search(r"Rese[ts]+\s+(.+?)\s{2,}(\d+)%\s*used", block)
        if m:
            result["session_reset"] = _normalize_whitespace(m.group(1).split("(")[0])
            result["session_pct"] = int(m.group(2))
        else:
            # Old format: "NN% used" then "Resets <time>" on separate lines
            pct_m = re.search(r"(\d+)%\s*used", block)
            reset_m = re.search(r"Rese[ts]+\s+(.+?)(?:\n|$)", block)
            if pct_m:
                result["session_pct"] = int(pct_m.group(1))
            if reset_m:
                result["session_reset"] = _normalize_whitespace(reset_m.group(1).split("(")[0])

    # v2.1.116+ dropped the session percentage from /usage entirely — only
    # the resets time renders there. The welcome status line still shows it:
    #   "You've used NN% of your session limit · resets <time> (<tz>) · /upgrade…"
    # Pull it from the welcome pane (or any pane) as a fallback.
    if "session_pct" not in result or "session_reset" not in result:
        for pane in (welcome_pane, usage_text):
            if not pane:
                continue
            m = re.search(
                r"You['’]ve used\s+(\d+)%\s+of your session limit\s*[·•‧]\s*"
                r"resets\s+(.+?)(?:\s*[·•‧]|\s*\n|\s*$)",
                pane,
                re.IGNORECASE,
            )
            if m:
                result.setdefault("session_pct", int(m.group(1)))
                result.setdefault(
                    "session_reset",
                    _normalize_whitespace(m.group(2).split("(")[0]),
                )
                break

    weekly_patterns = (
        ("weekly_pct", "weekly_reset", r"all\s+models"),
        ("weekly_sonnet_pct", None, r"[^)\n]*\bsonnet\b[^)\n]*"),
        ("weekly_design_pct", None, r"[^)\n]*\bdesign\b[^)\n]*"),
    )
    for pct_key, reset_key, label_pattern in weekly_patterns:
        # Extract block between this "Current week" header and the next section
        block_m = re.search(
            rf"Current week \((?:{label_pattern})\)(.*?)(?=Current week|Extra usage|Last updated|\Z)",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if not block_m:
            continue
        block = block_m.group(1)
        # New format: "Resets <time>   NN% used" on one line
        m = re.search(r"Resets\s+(.+?)\s{2,}(\d+)%\s*used", block)
        if m:
            if reset_key:
                result[reset_key] = _normalize_whitespace(m.group(1).split("(")[0])
            result[pct_key] = int(m.group(2))
        else:
            # Old format: separate lines
            pct_m = re.search(r"(\d+)%\s*used", block)
            reset_m = re.search(r"Resets\s+(.+?)(?:\n|$)", block)
            if pct_m:
                result[pct_key] = int(pct_m.group(1))
            if reset_key and reset_m:
                result[reset_key] = _normalize_whitespace(reset_m.group(1).split("(")[0])

    m = re.search(
        r"Extra usage.*?(\d+)%\s*used.*?\$([0-9.,]+)\s*/\s*\$([0-9.,]+)\s*spent.*?Resets\s+(.+?)(?=\n\s*(?:Last updated)|\n?\(esc|\Z)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    if m:
        result["extra_pct"] = int(m.group(1))
        result["extra_spent_usd"] = float(m.group(2).replace(",", ""))
        result["extra_limit_usd"] = float(m.group(3).replace(",", ""))
        result["extra_reset"] = _normalize_whitespace(m.group(4))

    return result


def _claude_usage_complete(result: dict) -> bool:
    required = ("session_pct", "session_reset", "weekly_pct", "weekly_reset")
    return all(key in result for key in required)


def scrape_claude_usage_web() -> dict | None:
    """Fetch Claude plan usage from the private claude.ai web endpoint."""
    config = _claude_web_request_config()
    if not config:
        return None

    url, headers = config
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            content_type = resp.headers.get("content-type", "").lower()
            cf_mitigated = resp.headers.get("cf-mitigated", "").lower()
    except urllib.error.HTTPError as exc:
        body = exc.read()
        cf_mitigated = exc.headers.get("cf-mitigated", "").lower()
        if exc.code == 403 and cf_mitigated == "challenge":
            raise RuntimeError("Claude web API was blocked by a Cloudflare challenge") from exc
        raise RuntimeError(f"Claude web API returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Claude web API request failed: {exc.reason}") from exc

    if cf_mitigated == "challenge":
        raise RuntimeError("Claude web API returned a Cloudflare challenge")
    if "json" not in content_type:
        text = body.decode("utf-8", errors="replace")
        if "Just a moment" in text:
            raise RuntimeError("Claude web API returned a Cloudflare challenge page")
        raise RuntimeError(f"Claude web API returned non-JSON content ({content_type or 'unknown'})")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Claude web API returned invalid JSON: {exc}") from exc

    result = _parse_claude_web_usage_payload(payload)
    return result if _claude_usage_complete(result) else None


def scrape_claude_usage() -> dict | None:
    """Scrape Claude Code /usage via tmux to get account-level usage.

    Uses tmux to create a real terminal session that Claude's Ink TUI
    can interact with properly, even from launchd.
    """
    tmux = _find_bin('tmux')
    claude = _find_bin('claude')
    session = 'claude_usage_scrape'

    subprocess.run([tmux, 'kill-session', '-t', session], capture_output=True)

    subprocess.run([
        tmux, 'new-session', '-d', '-s', session, '-x', '120', '-y', '50',
        claude, '--dangerously-skip-permissions',
    ], check=True, cwd=str(Path(__file__).resolve().parent.parent))

    welcome_pane = ""
    try:
        for i in range(30):
            time.sleep(1)
            r = subprocess.run(
                [tmux, 'capture-pane', '-t', session, '-p'],
                capture_output=True, text=True,
            )
            pane = r.stdout
            lower = pane.lower()
            # Handle modal prompts BEFORE the readiness check — the menu cursor "❯"
            # appears inside the trust/accept dialogs and would otherwise short-circuit
            # the loop, causing /usage to be typed into the modal selector.
            if 'i accept' in lower:
                subprocess.run([tmux, 'send-keys', '-t', session, 'Down', ''], capture_output=True)
                time.sleep(0.3)
                subprocess.run([tmux, 'send-keys', '-t', session, 'Enter', ''], capture_output=True)
                continue
            if 'trust this' in lower:
                subprocess.run([tmux, 'send-keys', '-t', session, 'Enter', ''], capture_output=True)
                continue
            # Require a Claude-specific marker alongside the prompt cursor.
            # "❯" alone fires too early (transient cursor before Ink draws the TUI).
            ready_markers = ('bypass permissions', 'welcome back', 'claude code v', 'tips for getting started')
            if ('❯' in pane or 'effort' in pane) and any(m in lower for m in ready_markers):
                welcome_pane = pane
                break

        subprocess.run([tmux, 'send-keys', '-t', session, '/usage', 'Enter'], check=True)

        usage = None
        for _ in range(20):
            time.sleep(1)
            r = subprocess.run(
                [tmux, 'capture-pane', '-t', session, '-p', '-S', '-'],
                capture_output=True, text=True,
            )
            parsed = _parse_claude_usage_text(r.stdout, welcome_pane=welcome_pane)
            if _claude_usage_complete(parsed):
                usage = parsed
                break

        subprocess.run([tmux, 'send-keys', '-t', session, 'Escape', ''], capture_output=True)
        time.sleep(0.5)
        subprocess.run([tmux, 'send-keys', '-t', session, '/exit', 'Enter'], capture_output=True)
        time.sleep(1)
    finally:
        subprocess.run([tmux, 'kill-session', '-t', session], capture_output=True)

    return usage if usage and _claude_usage_complete(usage) else None



def scrape_codex_usage() -> dict | None:
    """Get Codex usage via the app-server JSON-RPC API.

    Starts `codex app-server`, sends initialize + account/rateLimits/read,
    returns structured data with exact reset timestamps.
    """
    codex_bin = _find_bin('codex')
    if not shutil.which('codex') and not os.path.exists(codex_bin):
        return None

    proc = subprocess.Popen(
        [codex_bin, 'app-server'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )

    def send(msg):
        proc.stdin.write(json.dumps(msg) + '\n')
        proc.stdin.flush()

    def recv(timeout=10):
        # Codex 0.129+ pushes unsolicited JSON-RPC notifications
        # (e.g. `remoteControl/status/changed`) between request/response pairs.
        # Filter them out — only return objects that carry an `id` (responses).
        deadline = time.time() + timeout
        while time.time() < deadline:
            r, _, _ = select.select([proc.stdout], [], [], 1)
            if r:
                line = proc.stdout.readline()
                if line:
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "id" not in msg:
                        continue
                    return msg
        return None

    try:
        # Initialize
        send({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
              'params': {'clientInfo': {'name': 'usage-tracker', 'version': '1.0'}}})
        recv(timeout=10)

        # Initialized notification
        send({'jsonrpc': '2.0', 'method': 'initialized', 'params': {}})

        # Read rate limits
        send({'jsonrpc': '2.0', 'id': 2, 'method': 'account/rateLimits/read', 'params': {}})
        resp = recv(timeout=10)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    if not resp or 'result' not in resp:
        return None

    rl = resp['result'].get('rateLimits', {})
    result = {}

    def _bucket_used_pct(bucket: dict | None) -> float | None:
        if not isinstance(bucket, dict):
            return None
        used = bucket.get('usedPercent')
        if isinstance(used, (int, float)) and not isinstance(used, bool):
            return float(used)
        return None

    def _find_code_review_bucket(rate_limits: dict) -> dict | None:
        for key, bucket in rate_limits.items():
            if not isinstance(bucket, dict):
                continue
            label_parts = [
                str(key),
                str(bucket.get('name') or ''),
                str(bucket.get('label') or ''),
                str(bucket.get('kind') or ''),
                str(bucket.get('scope') or ''),
                str(bucket.get('type') or ''),
            ]
            label = ' '.join(label_parts).lower()
            if 'review' in label:
                return bucket
        tertiary = rate_limits.get('tertiary')
        return tertiary if isinstance(tertiary, dict) else None

    def _find_model_bucket(rate_limits: dict, keywords: tuple[str, ...]) -> dict | None:
        """Find a rate-limit bucket whose key/label matches any of *keywords*."""
        for key, bucket in rate_limits.items():
            if not isinstance(bucket, dict):
                continue
            label_parts = [
                str(key),
                str(bucket.get('name') or ''),
                str(bucket.get('label') or ''),
                str(bucket.get('kind') or ''),
                str(bucket.get('scope') or ''),
                str(bucket.get('type') or ''),
                str(bucket.get('model') or ''),
            ]
            label = ' '.join(label_parts).lower()
            if any(kw in label for kw in keywords):
                return bucket
        return None

    # Primary = 5hr session window
    primary = rl.get('primary')
    if primary:
        used = _bucket_used_pct(primary)
        if used is not None:
            result['session_remaining_pct'] = 100 - used
        reset_ts = primary.get('resetsAt')
        if reset_ts:
            dt = datetime.fromtimestamp(reset_ts)
            result['session_reset'] = dt.strftime("%b %-d %-I:%M %p")

    # Secondary = weekly window
    secondary = rl.get('secondary')
    if secondary:
        used = _bucket_used_pct(secondary)
        if used is not None:
            result['weekly_remaining_pct'] = 100 - used
        reset_ts = secondary.get('resetsAt')
        if reset_ts:
            dt = datetime.fromtimestamp(reset_ts)
            result['reset_at'] = dt.strftime("%b %-d %-I:%M %p")

    code_review = _find_code_review_bucket(rl)
    if code_review:
        used = _bucket_used_pct(code_review)
        if used is not None:
            result['code_review_remaining_pct'] = 100 - used

    # Model-level weekly sublimits (GPT-5.4, GPT-5.3-Codex-Spark)
    gpt54_bucket = _find_model_bucket(rl, ('gpt-5.4', 'gpt5.4', 'gpt54'))
    if gpt54_bucket:
        used = _bucket_used_pct(gpt54_bucket)
        if used is not None:
            result['weekly_gpt54_remaining_pct'] = 100 - used

    spark_bucket = _find_model_bucket(rl, ('spark', 'gpt-5.3-codex-spark', 'codex-spark'))
    if spark_bucket:
        used = _bucket_used_pct(spark_bucket)
        if used is not None:
            result['weekly_spark_remaining_pct'] = 100 - used

    return result if result else None


# ── Cursor ───────────────────────────────────────────────

_CURSOR_STATE_DB = Path.home() / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
_CURSOR_LIMIT_STATE_FILE = Path.home() / ".usage-tracker" / "cursor-agent-status.txt"
_CURSOR_FREE_REQUEST_LIMIT = 50


def _cursor_auth_token() -> str | None:
    """Extract Cursor access token from local state database."""
    if not _CURSOR_STATE_DB.exists():
        return None
    try:
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(_CURSOR_STATE_DB)) as conn:
            row = conn.execute(
                "SELECT value FROM ItemTable WHERE key = 'cursorAuth/accessToken'"
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def _parse_cursor_agent_limit_text(text: str) -> dict | None:
    """Parse Cursor Agent quota-limit text copied from the CLI/chat surface."""
    cleaned = _clean(text or "")
    if not cleaned.strip():
        return None

    lower = cleaned.lower()
    free_limit_hit = "free requests limit" in lower
    generic_limit_hit = "usage limit" in lower or "requests limit" in lower
    spend_limit_match = re.search(r"spendLimitHit:[ \t]*(true|false)", cleaned, re.IGNORECASE)
    spend_limit_hit = None
    if spend_limit_match:
        spend_limit_hit = spend_limit_match.group(1).lower() == "true"

    spend_limits: list[int] = []
    spend_limits_match = re.search(r"spendLimits:[ \t]*\[([^\]]*)\]", cleaned, re.IGNORECASE)
    if spend_limits_match:
        spend_limits = [
            int(value)
            for value in re.findall(r"\d+", spend_limits_match.group(1))
        ]

    reset_at = None
    reset_match = re.search(
        r"monthly cycle ends on\s+(\d{1,2}/\d{1,2}/\d{4})",
        cleaned,
        re.IGNORECASE,
    )
    if reset_match:
        try:
            reset_at = datetime.strptime(reset_match.group(1), "%m/%d/%Y").date().isoformat()
        except ValueError:
            reset_at = reset_match.group(1)

    fallback_model = None
    fallback_match = re.search(r"fallbackModel:[ \t]*([^\n\r]*)", cleaned)
    if fallback_match:
        fallback_model = fallback_match.group(1).strip() or None

    if not (generic_limit_hit or spend_limit_hit or spend_limits or reset_at or fallback_model):
        return None

    result: dict[str, object] = {}
    if generic_limit_hit:
        result.update(
            {
                "limit_hit": True,
                "at_limit": True,
                "limit_kind": "free_requests" if free_limit_hit else "usage",
                "limit_message": (
                    "You've hit your free requests limit."
                    if free_limit_hit
                    else "You've hit your Cursor usage limit."
                ),
                "remaining_requests": 0,
            }
        )
        if free_limit_hit:
            result["plan"] = "free"
            result["max_requests"] = _CURSOR_FREE_REQUEST_LIMIT
            result["total_requests"] = _CURSOR_FREE_REQUEST_LIMIT
            result["models"] = {
                "agent": {
                    "requests": _CURSOR_FREE_REQUEST_LIMIT,
                    "tokens": 0,
                    "max_requests": _CURSOR_FREE_REQUEST_LIMIT,
                }
            }

    if reset_at:
        result["reset_at"] = reset_at
    if spend_limit_hit is not None:
        result["spend_limit_hit"] = spend_limit_hit
    if spend_limits:
        result["spend_limits"] = spend_limits
    if fallback_model:
        result["fallback_model"] = fallback_model

    return result


def _cursor_agent_limit_state() -> dict | None:
    text = _read_env_or_file("CURSOR_AGENT_LIMIT_TEXT", "CURSOR_AGENT_LIMIT_FILE")
    state = _parse_cursor_agent_limit_text(text or "")
    if state and _cursor_limit_state_active(state):
        return state

    try:
        state = _parse_cursor_agent_limit_text(_CURSOR_LIMIT_STATE_FILE.read_text())
    except OSError:
        return None
    return state if state and _cursor_limit_state_active(state) else None


def _cursor_limit_state_active(state: dict) -> bool:
    reset_at = state.get("reset_at")
    if not isinstance(reset_at, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", reset_at):
        return True
    try:
        return datetime.strptime(reset_at, "%Y-%m-%d").date() >= datetime.now().date()
    except ValueError:
        return True


def _merge_cursor_limit_state(result: dict, limit_state: dict | None) -> dict:
    if not limit_state:
        return result

    plan = str(result.get("plan") or "").strip().lower()
    if (
        limit_state.get("limit_kind") == "free_requests"
        and plan
        and plan not in {"free", "free_trial", "hobby", "unknown"}
    ):
        return result

    merged = dict(result)
    for key, value in limit_state.items():
        if value is not None:
            merged[key] = value

    if merged.get("at_limit") or merged.get("limit_hit"):
        max_requests = merged.get("max_requests")
        total_requests = merged.get("total_requests")
        if isinstance(max_requests, (int, float)) and max_requests > 0:
            if not isinstance(total_requests, (int, float)) or total_requests < max_requests:
                merged["total_requests"] = int(max_requests)
            merged["remaining_requests"] = 0
    return merged


def scrape_cursor_usage() -> dict | None:
    """Fetch Cursor usage and profile via api2.cursor.sh."""
    limit_state = _cursor_agent_limit_state()
    token = _cursor_auth_token()
    if not token:
        return limit_state

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    result = {}

    # Usage data
    try:
        req = urllib.request.Request(
            "https://api2.cursor.sh/auth/usage",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            usage = json.loads(resp.read())
        result["usage"] = usage

        # Aggregate totals across all models
        total_requests = 0
        total_tokens = 0
        models = {}
        for model_key, model_data in usage.items():
            if model_key == "startOfMonth":
                result["start_of_month"] = model_data
                continue
            if not isinstance(model_data, dict):
                continue
            reqs = model_data.get("numRequestsTotal", 0) or 0
            toks = model_data.get("numTokens", 0) or 0
            max_reqs = model_data.get("maxRequestUsage")
            total_requests += reqs
            total_tokens += toks
            if reqs > 0 or toks > 0:
                models[model_key] = {
                    "requests": reqs,
                    "tokens": toks,
                    "max_requests": max_reqs,
                }
        result["total_requests"] = total_requests
        result["total_tokens"] = total_tokens
        result["models"] = models
    except Exception:
        pass

    # Profile/plan data
    try:
        req = urllib.request.Request(
            "https://api2.cursor.sh/auth/full_stripe_profile",
            headers=headers,
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            profile = json.loads(resp.read())
        result["plan"] = profile.get("membershipType", "free")
        result["trial_eligible"] = profile.get("trialEligible", False)
    except Exception:
        if "plan" not in result:
            result["plan"] = "unknown"

    # Apply known free plan limits when API returns null
    KNOWN_FREE_LIMITS = {
        "free": _CURSOR_FREE_REQUEST_LIMIT,
        "free_trial": _CURSOR_FREE_REQUEST_LIMIT,
        "hobby": _CURSOR_FREE_REQUEST_LIMIT,
    }
    plan = (result.get("plan") or "free").lower()
    if plan in KNOWN_FREE_LIMITS:
        default_max = KNOWN_FREE_LIMITS[plan]
        result["max_requests"] = default_max
        # Backfill models that had null maxRequestUsage
        for model_data in result.get("models", {}).values():
            if model_data.get("max_requests") is None:
                model_data["max_requests"] = default_max
        # If no models were tracked but we have a limit, add a synthetic entry
        if not result.get("models"):
            result["models"] = {
                "premium": {
                    "requests": result.get("total_requests", 0),
                    "tokens": result.get("total_tokens", 0),
                    "max_requests": default_max,
                }
            }

    result = _merge_cursor_limit_state(result, limit_state)
    return result if result else None
