"""Tests for API endpoints using FastAPI TestClient."""

import os
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("USAGE_TRACKER_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

import src.database as db
import src.api as api_module
from src.api import API_SECRET, _remote_cc, app

AUTH = {"Authorization": f"Bearer {API_SECRET}"}
EXPECTED_PROVIDERS = {"claude", "codex", "cursor"}


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path):
    test_db = tmp_path / "test.db"
    with patch.object(db, "DB", test_db):
        db.init()
        yield


@pytest.fixture(autouse=True)
def reset_remote_cc():
    for key in list(_remote_cc.keys()):
        _remote_cc[key] = 0 if key == "ts" else None
    api_module._stats_cache = None
    api_module._stats_cache_ts = 0
    yield
    for key in list(_remote_cc.keys()):
        _remote_cc[key] = 0 if key == "ts" else None
    api_module._stats_cache = None
    api_module._stats_cache_ts = 0


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seeded_db():
    """Insert sample data for endpoints that need it."""
    db.insert(50, 30, 5, ts=int(time.time()) - 60,
              session_reset="3 hr", weekly_reset="Tue 12:00 AM",
              extra_spent_usd=1.5, extra_limit_usd=10.0, extra_balance_usd=8.5)


class TestAuth:
    def test_no_token_returns_401(self, client):
        r = client.get("/stats")
        assert r.status_code == 401

    def test_wrong_token_returns_401(self, client):
        r = client.get("/stats", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401

    def test_valid_token_passes(self, client, seeded_db):
        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200


class TestSecurityHeaders:
    def test_headers_present(self, client, seeded_db):
        r = client.get("/stats", headers=AUTH)
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["X-Frame-Options"] == "DENY"


class TestHealth:
    def test_health(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


class TestStatsEndpoint:
    def test_empty_db(self, client):
        # No scraped samples: /stats still returns a full payload with null
        # quota gauges (self-imposed quotas / API-based access rely on
        # this; the Swift app requires the non-optional `extra` field).
        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["extra"] == 0.0
        assert data["claude_quota"]["session_used_pct"] is None
        assert data["timestamp"] is None

    def test_with_data(self, client, seeded_db):
        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert "burn" in data
        assert "risk_outlook" in data
        assert data["claude_today"]["messages_today"] is not None
        assert data["claude_quota"]["session_used_pct"] == 50
        assert data["claude_quota"]["weekly_sonnet_used_pct"] is None
        assert data["claude_quota"]["weekly_design_used_pct"] is None
        registry_ids = {item["id"] for item in data["provider_registry"]}
        assert registry_ids == EXPECTED_PROVIDERS
        assert set(data["providers_latest"].keys()) == EXPECTED_PROVIDERS
        assert data["providers_latest"]["claude"]["status"] in {"stale", "partial", "ok", "error"}

    def test_claude_past_time_only_session_reset_is_unknown(self, client):
        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 7, 5, 14, 29)
                return value.replace(tzinfo=tz) if tz else value

        db.insert(
            87,
            59,
            0,
            ts=int(time.time()) - 60,
            session_reset="2:20pm",
            weekly_reset="Jul 6 at 5:59pm",
        )

        with patch.object(api_module, "datetime", FixedDateTime):
            r = client.get("/stats", headers=AUTH)

        assert r.status_code == 200
        data = r.json()
        assert data["cc_session_hours_left"] is None
        assert data["claude_quota"]["session_reset"] is None
        assert data["claude_quota"]["weekly_reset"] == "Jul 6 5:59 PM"

    def test_stats_exposes_normalized_codex_contract(self, client, seeded_db):
        db.insert_codex(80, 25, "Apr 10", session_remaining_pct=90, session_reset="Apr 9 3:00 PM", ts=1000)
        _remote_cc["messages"] = {"total_messages": 4, "active_hours": 1.5, "conversations": 2}
        _remote_cc["tokens"] = {"input_tokens": 200, "output_tokens": 80, "cache_read_tokens": 0, "cache_create_tokens": 0}
        _remote_cc["codex_local"] = {
            "total_tokens": 900,
            "total_sessions": 3,
            "recent_threads": [],
            "today_tokens": 400,
            "today_threads": 1,
            "by_model": {"o4-mini": {"tokens": 900, "sessions": 3}},
            "today_by_model": {"o4-mini": {"tokens": 400, "sessions": 1}},
        }
        _remote_cc["codex_sessions"] = {
            "messages_today": 2,
            "sessions_today": 1,
            "total_sessions": 7,
            "active_hours_today": 0.5,
            "input_tokens_today": 120,
            "output_tokens_today": 60,
            "user_messages_today": 1,
            "reasoning_tokens_today": 30,
        }
        _remote_cc["cc_stats"] = {"total_sessions": 12, "total_messages": 120, "daily_activity": []}
        _remote_cc["ts"] = time.time()

        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["claude_today"]["messages_today"] == 4
        assert data["claude_today"]["output_tokens_today"] == 80
        assert data["claude_today"]["threads_today"] is None
        assert data["claude_today"]["sessions_today"] is None
        assert data["codex_today"]["messages_today"] == 2
        assert data["codex_today"]["threads_today"] == 1
        assert data["codex_today"]["sessions_today"] == 1
        for legacy_key in (
            "active_hours",
            "messages",
            "sessions",
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "user_messages",
        ):
            assert legacy_key not in data["codex_today"]
        assert "codex" not in data
        assert data["codex_totals"]["total_threads"] == 3
        assert data["codex_totals"]["total_sessions"] == 7
        assert data["claude_quota"]["session_used_pct"] == 50
        assert data["codex_quota"]["weekly_used_pct"] == 20
        assert data["codex_quota"]["session_used_pct"] == 10
        assert data["codex_quota"]["weekly_reset"] == "Apr 10"

        _remote_cc["messages"] = None
        _remote_cc["tokens"] = None
        _remote_cc["codex_local"] = None
        _remote_cc["codex_sessions"] = None
        _remote_cc["cc_stats"] = None
        _remote_cc["ts"] = 0

    def test_stats_exposes_codex_model_sublimits(self, client, seeded_db):
        db.insert_codex(80, 25, "Apr 10", session_remaining_pct=90,
                        weekly_gpt54_remaining_pct=85.0,
                        weekly_spark_remaining_pct=92.0, ts=1000)
        _remote_cc["ts"] = time.time()

        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        codex_quota = r.json()["codex_quota"]
        assert codex_quota["weekly_gpt54_used_pct"] == 15.0
        assert codex_quota["weekly_spark_used_pct"] == 8.0

        _remote_cc["ts"] = 0

    def test_stats_accepts_legacy_codex_session_aliases_for_transition(self, client, seeded_db):
        _remote_cc["codex_local"] = {
            "total_tokens": 500,
            "total_sessions": 2,
            "today_tokens": 100,
            "today_threads": 1,
            "recent_threads": [],
            "by_model": {},
            "today_by_model": {},
        }
        _remote_cc["codex_sessions"] = {
            "messages": 3,
            "sessions": 1,
            "active_hours": 0.4,
            "input_tokens": 50,
            "output_tokens": 20,
            "reasoning_tokens": 10,
            "user_messages": 2,
            "total_sessions": 6,
        }
        _remote_cc["ts"] = time.time()

        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["codex_today"]["messages_today"] == 3
        assert data["codex_today"]["sessions_today"] == 1
        assert data["codex_today"]["active_hours_today"] == 0.4
        assert data["codex_today"]["input_tokens_today"] == 50
        assert data["codex_today"]["output_tokens_today"] == 20
        assert data["codex_today"]["reasoning_tokens_today"] == 10
        assert data["codex_today"]["user_messages_today"] == 2
        assert "messages" not in data["codex_today"]
        assert "sessions" not in data["codex_today"]

        _remote_cc["codex_local"] = None
        _remote_cc["codex_sessions"] = None
        _remote_cc["ts"] = 0

    def test_codex_analytics_summary_present(self, client, seeded_db):
        """codex_analytics_summary is present in /stats response."""
        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert "codex_analytics_summary" in data

    def test_stats_exposes_gap_rollups(self, client, seeded_db):
        """/stats exposes 4-state gap rollups for today/yesterday/last_7d."""
        from datetime import datetime

        import src.gap_rollups as gap_rollups_module

        today_key = datetime.now(gap_rollups_module._local_timezone()).strftime("%Y-%m-%d")

        synthetic = {
            "period_days": 7,
            "daily": [
                {
                    "date": today_key,
                    "focus_gap_sec": 1800,
                    "attention_idle_sec": 600,
                    "off_hours_away_sec": 0,
                    "agent_runtime_sec": 1200,
                },
                {
                    "date": "1900-01-01",  # synthetic "yesterday-or-earlier" row
                    "focus_gap_sec": 300,
                    "attention_idle_sec": 100,
                    "off_hours_away_sec": 7200,
                    "agent_runtime_sec": 400,
                },
            ],
        }

        with patch.object(gap_rollups_module, "session_analytics", return_value=synthetic):
            r = client.get("/stats", headers=AUTH)

        assert r.status_code == 200
        rollups = r.json()["gap_rollups"]
        assert rollups is not None
        for bucket in ("today", "yesterday", "last_7d"):
            assert bucket in rollups
            for key in (
                "focus_gap_sec",
                "attention_idle_sec",
                "off_hours_away_sec",
                "agent_runtime_sec",
                "human_time_sec",
                "downtime_sec",
            ):
                assert key in rollups[bucket]

        # Today row picked up by today_key match
        today = rollups["today"]
        assert today["focus_gap_sec"] == 1800
        assert today["attention_idle_sec"] == 600
        assert today["agent_runtime_sec"] == 1200
        # Legacy fields: human_time = focus + attention; downtime zeroed.
        assert today["human_time_sec"] == 2400
        assert today["downtime_sec"] == 0

        # last_7d sums every daily row in the period.
        last_7d = rollups["last_7d"]
        assert last_7d["focus_gap_sec"] == 1800 + 300
        assert last_7d["off_hours_away_sec"] == 7200
        assert last_7d["agent_runtime_sec"] == 1600
        assert last_7d["downtime_sec"] == 0

    def test_stale_remote_quota_data_is_hidden(self, client, seeded_db):
        _remote_cc["cursor_usage"] = {"plan": "Pro", "total_requests": 42, "total_tokens": 9000}
        _remote_cc["ts"] = time.time() - 600
        r = client.get("/stats", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["cursor"] is None
        _remote_cc["cursor_usage"] = None
        _remote_cc["ts"] = 0

    def test_codex_analytics_summary_none(self, client, seeded_db):
        with patch("src.api.codex_local_stats", return_value={}):
            r = client.get("/stats", headers=AUTH)
            data = r.json()
            assert data["codex_analytics_summary"] is None

    def test_codex_analytics_summary_with_data(self, client, seeded_db):
        _remote_cc["codex_local"] = {
            "total_tokens": 8000,
            "total_sessions": 5,
            "by_model": {},
            "by_source": {
                "cli": {"tokens": 5000, "sessions": 3},
                "web": {"tokens": 2000, "sessions": 1},
                "ide": {"tokens": 1000, "sessions": 1},
            },
            "recent_threads": [],
        }
        _remote_cc["messages"] = {"total_messages": 1, "active_hours": 0}
        _remote_cc["tokens"] = {"total_tokens": 0}
        _remote_cc["ts"] = time.time()
        r = client.get("/stats", headers=AUTH)
        data = r.json()
        ca = data["codex_analytics_summary"]
        assert ca is not None
        assert ca["dominant_surface"] == "cli"
        assert ca["dominant_share_pct"] == 60
        assert ca["total_threads"] == 5
        _remote_cc["ts"] = 0
        _remote_cc["codex_local"] = None
        _remote_cc["messages"] = None
        _remote_cc["tokens"] = None


class TestCCReport:
    def test_post_report(self, client):
        payload = {
            "messages": {"total_messages": 10, "active_hours": 1},
            "tokens": {"total_tokens": 500},
            "projects": [{"project": "test", "active_hours": 1, "messages": 5}],
        }
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_post_without_auth(self, client):
        payload = {"messages": {}, "tokens": {}, "projects": []}
        r = client.post("/cc/report", json=payload)
        assert r.status_code == 401

    def test_post_with_usage_inserts_to_db(self, client):
        payload = {
            "messages": {"total_messages": 5},
            "tokens": {"total_tokens": 100},
            "projects": [],
            "usage": {
                "session_pct": 40,
                "weekly_pct": 20,
                "extra_pct": 0,
                "weekly_sonnet_pct": 12.5,
                "weekly_design_pct": 67.0,
            },
        }
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        s = db.latest_sample()
        assert s is not None
        assert s["session"] == 40
        assert s["weekly_sonnet_pct"] == 12.5
        assert s["weekly_design_pct"] == 67.0

        stats = client.get("/stats", headers=AUTH)
        assert stats.status_code == 200
        claude_quota = stats.json()["claude_quota"]
        assert claude_quota["weekly_sonnet_used_pct"] == 12.5
        assert claude_quota["weekly_design_used_pct"] == 67.0

    def test_post_with_codex_usage(self, client):
        payload = {
            "messages": {}, "tokens": {}, "projects": [],
            "codex_usage": {
                "weekly_remaining_pct": 80,
                "code_review_remaining_pct": 70,
                "session_remaining_pct": 90,
                "weekly_reset": "Apr 10",
                "session_reset": "Apr 9 3:00 PM",
            },
        }
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        c = db.latest_codex()
        assert c is not None
        assert c["reset_at"] == "Apr 10"
        assert c["code_review_remaining_pct"] == 70
        assert c["session_reset"] == "Apr 9 3:00 PM"

    def test_post_with_codex_model_sublimits(self, client):
        payload = {
            "messages": {}, "tokens": {}, "projects": [],
            "codex_usage": {
                "weekly_remaining_pct": 80,
                "code_review_remaining_pct": 70,
                "session_remaining_pct": 90,
                "weekly_reset": "Apr 10",
                "session_reset": "Apr 9 3:00 PM",
                "weekly_gpt54_remaining_pct": 85,
                "weekly_spark_remaining_pct": 92,
            },
        }
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        c = db.latest_codex()
        assert c is not None
        assert c["weekly_gpt54_remaining_pct"] == 85
        assert c["weekly_spark_remaining_pct"] == 92

    def test_post_with_codex_analytics(self, client):
        payload = {
            "messages": {},
            "tokens": {},
            "projects": [],
            "codex_analytics": {
                "summary": {
                    "workspace": {"avg_daily_users": 12.0},
                    "code_review": {"avg_daily_reviews": 4.0},
                }
            },
        }
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        assert _remote_cc["codex_analytics"]["summary"]["workspace"]["avg_daily_users"] == 12.0

    def test_post_without_codex_analytics_does_not_clear_cached_bundle(self, client):
        _remote_cc["codex_analytics"] = {"summary": {"workspace": {"avg_daily_users": 12.0}}}
        payload = {"messages": {}, "tokens": {}, "projects": []}
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        assert _remote_cc["codex_analytics"]["summary"]["workspace"]["avg_daily_users"] == 12.0

    def test_post_provider_snapshots_persists_latest_samples(self, client):
        payload = {
            "messages": {"total_messages": 1, "active_hours": 0.5},
            "tokens": {"total_tokens": 10},
            "projects": [],
            "provider_snapshots": {
                "claude": {
                    "provider": "claude",
                    "timestamp": int(time.time()),
                    "status": "ok",
                    "shared": {"primary_used_pct": 11.0},
                    "unique": {"conversations_today": 2},
                    "source": {"collector_access": "subscription"},
                    "error_text": None,
                }
            },
        }
        r = client.post("/cc/report", json=payload, headers=AUTH)
        assert r.status_code == 200
        latest = db.latest_provider_metric_samples()
        assert latest["claude"]["status"] == "ok"
        assert latest["claude"]["shared"]["primary_used_pct"] == 11.0


class TestSentinelAPI:
    def test_report_cookies(self, client, tmp_path):
        # Mock Path.home() to use tmp_path
        with patch("pathlib.Path.home", return_value=tmp_path):
            payload = {
                "cookies": {
                    "claude": "claude-session-key",
                    "codex": "codex-session-token",
                    "cursor": "cursor-token"
                }
            }
            r = client.post("/sentinel/report", json=payload, headers=AUTH)
            assert r.status_code == 200
            assert "claude" in r.json()["received"]

            # Verify files were created
            base_dir = tmp_path / ".usage-tracker"
            assert (base_dir / "claude-cookie.txt").read_text() == "claude-session-key"
            assert (base_dir / "codex-cookie.txt").read_text() == "codex-session-token"

            # Verify .env was updated
            env_file = Path(__file__).resolve().parent.parent / ".env"
            if env_file.exists():
                content = env_file.read_text()
                assert "CLAUDE_WEB_COOKIE_FILE" in content
                assert "CODEX_WEB_COOKIE_FILE" in content


class TestBudgetWeekly:
    def test_requires_auth(self, client):
        r = client.get("/budget/weekly")
        assert r.status_code == 401

    def test_returns_forecasts_dict(self, client, seeded_db):
        r = client.get("/budget/weekly", headers=AUTH)
        assert r.status_code == 200
        assert "forecasts" in r.json()


class TestModelsToday:
    def test_claude_models_today_sums_all_token_kinds(self):
        from src.api import _claude_today_contract

        cc_tokens = {
            "output_tokens": 10,
            "input_tokens": 5,
            "cache_read_tokens": 100,
            "cache_create_tokens": 20,
            "by_model": {
                "claude-sonnet-4-5": {
                    "input": 5, "output": 10,
                    "cache_read": 100, "cache_create": 20,
                    "requests": 3,
                },
                "bogus": "not-a-dict",
            },
        }
        result = _claude_today_contract({}, cc_tokens)
        assert result["models_today"] == {
            "claude-sonnet-4-5": {"tokens": 135, "requests": 3}
        }

    def test_claude_models_today_empty_when_absent(self):
        from src.api import _claude_today_contract

        assert _claude_today_contract({}, {})["models_today"] == {}

    def test_codex_models_today_from_today_by_model(self):
        from src.api import _codex_today_contract

        local = {
            "today_by_model": {
                "gpt-5.2-codex": {"tokens": 42000, "threads": 4},
                "bogus": None,
            }
        }
        result = _codex_today_contract(local, {})
        assert result["models_today"] == {
            "gpt-5.2-codex": {"tokens": 42000, "requests": 4}
        }
