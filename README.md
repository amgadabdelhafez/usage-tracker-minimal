# usage-tracker (minimal)

Trimmed-down AI coding tool usage tracker for **three providers — Claude, Codex, Cursor** — serving only what the `UsageMenuBar` macOS app needs.

## Data flow

```
collector.py (launchd, 60s)
  ├─ scans ~/.claude/projects/*.jsonl  (Claude Code CLI messages/tokens)
  ├─ scans Claude desktop-app session JSONL (Cowork), same format
  ├─ scans ~/.codex/state_5.sqlite + ~/.codex/sessions/*.jsonl
  └─ [subscription access only] scrapes claude/codex/cursor web usage
     (PTY/tmux or cookie-based web, every 5 min)
        ↓ POST /cc/report
API (uvicorn, 127.0.0.1:8000)
  └─ stores snapshots in claude_usage.db (sqlite, optional Turso sync)
        ↓
Swift menubar app polls GET /stats + GET /budget/weekly,
and POSTs /sentinel/report to refresh provider cookies.
```

## Access Modes

The quota scraping (session/weekly percentages) only exists for subscription
access, where Claude/Codex/Cursor expose quota pages in the logged-in product.
If Claude/Codex run through **Bedrock, Vertex, OpenAI enterprise, or any
API-key setup**, run the collector with API-based access:

```bash
python3 -m src.collector --access api             # scans only, no web/PTY scraping
python3 -m src.collector --access subscription    # default: scans + quota scrapes
export USAGE_TRACKER_ACCESS=api                   # or set the default via env (.env works)
```

With API-based access all token, model, message, and active-hours stats keep
working (they come from local JSONL/SQLite); quota gauges stay empty unless
self-imposed quotas are configured. To make launchd use it, add
`export USAGE_TRACKER_ACCESS=api` to `.env`.

Compatibility: old installs using `USAGE_TRACKER_MODE=full|local` or
`--mode full|local` still work. The canonical names are now
`subscription` and `api`.

### Self-imposed quotas

To get session/weekly gauges without subscription scraping, configure
`[claude.self_quota]` / `[codex.self_quota]` in `~/.usage-tracker/plans.toml`
(see the commented blocks in `plans.example.toml`, including approximate
subscription limits to mirror). Caps can be token-based or cost-based
(cost uses the API pricing table in `src/plan_config.py`, overridable per
model prefix). Usage is measured from the same local session files the
scanners read, over rolling windows (default 5h / 7d), cached 5 min.
Scraped subscription quota always wins while live; self-quota fills in
when scraping is off or stale. When active, `claude_quota`/`codex_quota`
in `/stats` carry `"source": "self_quota"` plus measurement detail.

## Endpoints

| Endpoint | Method | Purpose |
|---|---|---|
| `/stats` | GET | Menubar payload (quota, pacing, today's activity, gap rollups). Cached 15s. |
| `/budget/weekly` | GET | Day-by-day weekly forecast per provider (needs `plans.toml`). |
| `/cc/report` | POST | Collector ingest (usage samples + provider snapshots). |
| `/sentinel/report` | POST | Cookie refresh from the menubar sentinel (claude/codex/cursor). |
| `/health` | GET | Liveness (no auth). |

All except `/health` require `Authorization: Bearer $USAGE_TRACKER_SECRET`.

## Run

```bash
cd usage-tracker-minimal
pip install -r requirements.txt
export USAGE_TRACKER_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
uvicorn src.api:app --host 127.0.0.1 --port 8000   # API
python3 -m src.collector                            # one collector cycle
```

For always-on operation, install the plists in `launchd/` (`launchctl load ~/Library/LaunchAgents/...`) — they source `.env` and run with `WorkingDirectory` set to this folder.

## Config

- **`~/.usage-tracker/plans.toml`** — provider plans/caps/costs; see `plans.example.toml`. Needed for `/budget/weekly`. Cached 60s.
- **`.env`** (this folder, sourced by launchd):
  - `USAGE_TRACKER_SECRET` — API bearer secret (required)
  - `USAGE_TRACKER_ACCESS` — `subscription` (default) | `api` (no quota scraping; Bedrock/Vertex/enterprise/API key)
  - `CLAUDE_USAGE_SOURCE` — `auto` | `web` | `tmux`
  - `CLAUDE_WEB_COOKIE_FILE`, `CLAUDE_WEB_HEADERS_FILE`, `CLAUDE_WEB_ORG_ID` — Claude web usage scrape
  - `CODEX_WEB_COOKIE_FILE`, `CODEX_WEB_USAGE_URL`, `CODEX_WEB_ANALYTICS_URL` — Codex web scrape
  - `CURSOR_WEB_COOKIE_FILE`, `CURSOR_WEB_USAGE_URL` — Cursor web scrape
  - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` — optional remote DB sync

  Cookie files are rewritten automatically by `/sentinel/report`.

## Tests

```bash
cd usage-tracker-minimal && python3 -m pytest tests/ -q
```
