# Usage Tracker

Usage Tracker is a local macOS menu bar app and backend for watching AI coding
tool usage across Claude, Codex, and Cursor. It combines local activity scans
with optional subscription quota scraping so the menu bar can show current
usage, model breakdowns, weekly pacing, and quota risk without opening each
provider dashboard.

The app is designed to run on your machine:

- A Python collector scans local Claude and Codex activity every minute.
- Optional subscription access scraping refreshes Claude, Codex, and Cursor
  quota gauges every five minutes.
- A FastAPI server stores snapshots in SQLite and serves the menu bar contract.
- A Swift menu bar app polls the local API and refreshes provider cookies.

## What It Tracks

Claude:

- Claude Code CLI JSONL under `~/.claude/projects`
- Claude desktop/Cowork local-agent session JSONL
- Tokens, messages, active hours, model usage, session quota, weekly quota

Codex:

- Local Codex SQLite and session JSONL under `~/.codex`
- Threads, sessions, tokens, model usage, session quota, weekly quota

Cursor:

- Subscription usage from Cursor's web/API surface
- Requests, limits, reset state, and model breakdown when available

## Access Modes

The collector has two access modes:

| Mode | Use When | Behavior |
|---|---|---|
| `subscription` | You use Claude/Codex/Cursor logged-in subscription plans. | Local scans plus web/PTY quota scraping. |
| `api` | You use Bedrock, Vertex, OpenAI enterprise, or API-key based access. | Local scans only; no web/PTY quota scraping. |

Run one collector cycle with:

```bash
python3 -m src.collector --access subscription
python3 -m src.collector --access api
```

`subscription` is the default. Old values `--mode full` and `--mode local`
still work as compatibility aliases, but new installs should use
`--access subscription` or `--access api`.

## Requirements

- macOS 14 or newer for the Swift menu bar app
- Python 3.11 or newer
- Swift 5.9 or newer
- Homebrew-style paths if you use the included launchd plists:
  `/opt/homebrew/bin/python3` and `/opt/homebrew/bin/uvicorn`

If your Python or `uvicorn` live elsewhere, edit the plist files in `launchd/`
before loading them.

## Install

Clone the repo:

```bash
git clone https://github.com/amgadabdelhafez/usage-tracker-minimal.git
cd usage-tracker-minimal
```

Install backend dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Create local state folders:

```bash
mkdir -p ~/.usage-tracker/logs
```

Create a bearer secret and write both config files:

```bash
SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

cat > .env <<EOF
export USAGE_TRACKER_SECRET=$SECRET
export USAGE_TRACKER_ACCESS=subscription
EOF

cat > ~/.usage-tracker/config <<EOF
USAGE_TRACKER_SECRET=$SECRET
EOF
```

Use `USAGE_TRACKER_ACCESS=api` instead if you do not want subscription quota
scraping.

Optional: copy and edit the plan config:

```bash
cp plans.example.toml ~/.usage-tracker/plans.toml
```

`plans.toml` is used for weekly budget forecasts and self-imposed quotas.

Build the menu bar app:

```bash
cd UsageMenuBar
swift build -c release
cd ..
```

## Run Manually

Start the API:

```bash
set -a
source .env
set +a
uvicorn src.api:app --host 127.0.0.1 --port 8000
```

In another shell, run one collector cycle:

```bash
set -a
source .env
set +a
python3 -m src.collector --access subscription
```

Launch the menu bar app:

```bash
./UsageMenuBar/.build/arm64-apple-macosx/release/UsageMenuBar
```

The menu bar app polls `http://localhost:8000/stats` and
`http://localhost:8000/budget/weekly` using the bearer token from
`~/.usage-tracker/config`.

## Run With launchd

The included plists are configured for this checkout path:

```text
/Users/amgad/dev_projects/usage-tracker-minimal
```

If your checkout is somewhere else, edit the paths in `launchd/*.plist` first.

Install and start the jobs:

```bash
cp launchd/*.plist ~/Library/LaunchAgents/

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.amgad.usage-tracker.api.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.amgad.cc-collector.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.usage-tracker.menubar.plist
```

Restart a job after changes:

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.amgad.usage-tracker.api.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.amgad.usage-tracker.api.plist
```

Logs:

- API: `~/.usage-tracker/logs/api.log` and `~/.usage-tracker/logs/api.err.log`
- Collector: `~/.usage-tracker/logs/collector.log` and
  `~/.usage-tracker/logs/collector.err.log`
- Menu bar app: `/tmp/usage-menubar.log` and `/tmp/usage-menubar.err`

## Self-Imposed Quotas

Subscription quota gauges come from provider dashboards. API-based setups do
not have those dashboards, so you can define local caps in
`~/.usage-tracker/plans.toml`:

```toml
[claude.self_quota]
window_hours = 5
weekly_days = 7
session_cap_tokens = 44_000_000
weekly_cap_tokens = 300_000_000

[codex.self_quota]
window_hours = 5
weekly_days = 7
session_cap_usd = 10.0
weekly_cap_usd = 75.0
```

Claude self-quota usage is measured from Claude JSONL. Codex self-quota usage
is measured from Codex session JSONL. Caps can be token-based or cost-based,
and pricing can be overridden per model prefix in `plans.toml`.

Scraped subscription quota wins while it is live. Self-imposed quota fills in
when scraping is off or stale.

## API

All endpoints except `/health` require:

```text
Authorization: Bearer $USAGE_TRACKER_SECRET
```

| Endpoint | Method | Purpose |
|---|---|---|
| `/health` | GET | Liveness probe |
| `/stats` | GET | Menu bar payload, cached for 15 seconds |
| `/budget/weekly` | GET | Weekly forecast from `plans.toml` |
| `/cc/report` | POST | Collector ingest |
| `/sentinel/report` | POST | Cookie refresh from the menu bar sentinel |

## Development

Run backend tests:

```bash
python3 -m pytest tests/ -q
```

Build the Swift app:

```bash
cd UsageMenuBar
swift build -c release
```

Useful checks before publishing changes:

```bash
python3 -m src.collector --help
python3 -m pytest tests/ -q
cd UsageMenuBar && swift build -c release
```

Generated files such as `.env`, `claude_usage.db`, `__pycache__`, pytest
caches, and Swift `.build` output are intentionally ignored.
