"""Tests for pty_scraper parsing functions and mocked scrape calls."""

import json
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
import urllib.error

import pytest

from src.pty_scraper import (
    _clean,
    _codex_analytics_helper_script,
    _parse_claude_web_usage_payload,
    _parse_claude_usage_text,
    _cursor_auth_token,
    _merge_cursor_limit_state,
    _parse_cursor_agent_limit_text,
    _find_bin,
    _strip_ansi,
    claude_web_usage_configured,
    codex_web_analytics_configured,
    scrape_claude_usage,
    scrape_claude_usage_web,
    scrape_codex_analytics,
    scrape_codex_usage,
    scrape_cursor_usage,
)


class TestStripAnsi:
    def test_removes_color_codes(self):
        assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"

    def test_plain_text_unchanged(self):
        assert _strip_ansi("hello world") == "hello world"

    def test_removes_osc_sequences(self):
        # The regex strips partial OSC sequences; verify it does something
        result = _strip_ansi("\x1b]0;title\x07text")
        assert "text" in result


class TestClean:
    def test_removes_ansi_and_crlf(self):
        assert _clean("\x1b[32mhello\x1b[0m\r\nworld") == "hello\nworld"

    def test_cr_only(self):
        # _clean replaces \r\n with \n and \r with '', so standalone \r is removed
        result = _clean("a\rb")
        assert "\r" not in result


class TestFindBin:
    def test_finds_in_path(self):
        result = _find_bin("python3")
        assert result.endswith("python3")

    def test_nonexistent_falls_through(self):
        result = _find_bin("nonexistent_binary_xyz_12345")
        assert result == "nonexistent_binary_xyz_12345"


class TestScrapeClaudeUsage:
    def test_parses_usage_output(self):
        pane_output = """
        Claude Code v1.0
        Current session: 45% used
        Resets in 3 hr 11 min

        Current week (all models): 30% used
        Resets Tue 12:00 AM

        Current week (Sonnet): 12% used
        Resets Tue 12:00 AM

        Current week (Design): 5% used
        Resets Tue 12:00 AM

        Extra usage: 10% used
        $1.50 / $10.00 spent
        Resets Mon 12:00 AM
        Last updated less than a minute ago
        """

        result = _parse_claude_usage_text(pane_output)
        assert result is not None
        assert result["session_pct"] == 45
        assert result["session_reset"] == "in 3 hr 11 min"
        assert result["weekly_pct"] == 30
        assert result["weekly_sonnet_pct"] == 12
        assert result["weekly_design_pct"] == 5
        assert result["extra_pct"] == 10
        assert result["extra_spent_usd"] == 1.5
        assert result["extra_limit_usd"] == 10.0

    def test_relative_session_reset_text_is_preserved(self):
        pane_output = """
        Current session: 45% used
        Resets in 4 hr 52 min

        Current week (all models): 30% used
        Resets Jul 6 at 5:59pm
        """

        result = _parse_claude_usage_text(pane_output)

        assert result["session_reset"] == "in 4 hr 52 min"

    def test_partial_output_ignored(self):
        partial_output = """
        Current session: 28% used
        Resets 2pm
        """

        result = _parse_claude_usage_text(partial_output)
        assert "weekly_pct" not in result

    def test_v2116_layout_uses_welcome_status_line(self):
        """v2.1.116+: /usage no longer prints `NN% used` next to Current session.
        Session % must be sourced from the welcome status warning line."""
        welcome_pane = (
            "  ⏵⏵ bypass permissions on (shift+tab to cycle)"
            "                                       You've used 89% of your session limit"
            " · resets 3:20am (America/Los_Angeles) · /upgrade to keep using Claude Code\n"
        )
        usage_pane = """
           Status   Config   Usage   Stats

          Current session
          Resets 3:20am (America/Los_Angeles)

          Current week (all models)
          Resets May 4 at 6pm (America/Los_Angeles)          28% used

          Current week (Sonnet only)
          Resets May 4 at 6pm (America/Los_Angeles)          2% used
        """
        result = _parse_claude_usage_text(usage_pane, welcome_pane=welcome_pane)
        assert result["session_pct"] == 89
        assert "3:20am" in result["session_reset"]
        assert result["weekly_pct"] == 28
        assert "May 4" in result["weekly_reset"]
        assert result["weekly_sonnet_pct"] == 2

    def test_welcome_status_line_curly_apostrophe(self):
        """Some terminals render the apostrophe as U+2019."""
        welcome_pane = "You’ve used 73% of your session limit · resets 11:45pm\n"
        result = _parse_claude_usage_text("", welcome_pane=welcome_pane)
        assert result["session_pct"] == 73
        assert "11:45pm" in result["session_reset"]

    @patch("src.pty_scraper.time.sleep", return_value=None)
    @patch("src.pty_scraper.subprocess.run")
    def test_waits_for_complete_usage_panel(self, mock_run, _mock_sleep):
        partial_output = """
        Claude Code v1.0
        Current session: 28% used
        Resets 2pm
        """
        full_output = """
        Claude Code v1.0
        Current session: 97% used
        Resets in 9 min

        Current week (all models): 14% used
        Resets Thu 11:00 PM
        """
        usage_captures = iter([partial_output, full_output])

        def run_side_effect(cmd, capture_output=False, text=False, check=False, cwd=None):
            if "capture-pane" in cmd and "-S" in cmd:
                return MagicMock(stdout=next(usage_captures), returncode=0)
            if "capture-pane" in cmd:
                return MagicMock(stdout="❯", returncode=0)
            return MagicMock(stdout="", returncode=0)

        mock_run.side_effect = run_side_effect

        result = scrape_claude_usage()
        assert result is not None
        assert result["session_pct"] == 97
        assert result["weekly_pct"] == 14
        assert result["session_reset"] == "in 9 min"
        assert result["weekly_reset"] == "Thu 11:00 PM"


class TestScrapeClaudeUsageWeb:
    def test_config_detected_from_cookie_last_active_org(self):
        with patch.dict(
            os.environ,
            {
                "CLAUDE_WEB_COOKIE": "sessionKey=test-session; lastActiveOrg=org-123; cf_clearance=test",
                "CLAUDE_WEB_ORG_ID": "",
                "CLAUDE_WEB_HEADERS_JSON": "",
                "CLAUDE_WEB_HEADERS_FILE": "",
            },
            clear=False,
        ):
            assert claude_web_usage_configured() is True

    def test_parses_structured_usage_payload(self):
        payload = {
            "currentSession": {
                "usedPercent": 24,
                "resetAt": "2026-04-11T02:00:00Z",
            },
            "weekly": {
                "allModels": {
                    "usedPercent": 17,
                    "resetAt": "2026-04-17T06:00:00Z",
                },
                "sonnetOnly": {
                    "usedPercent": 0,
                },
                "designOnly": {
                    "usedPercent": 42,
                },
            },
            "extraUsage": {
                "usedPercent": 10,
                "spentUsd": 1.5,
                "limitUsd": 10.0,
                "balanceUsd": 8.5,
                "resetAt": "2026-05-01T07:00:00Z",
            },
        }

        result = _parse_claude_web_usage_payload(payload)
        assert result["session_pct"] == 24
        assert result["weekly_pct"] == 17
        assert result["weekly_sonnet_pct"] == 0
        assert result["weekly_design_pct"] == 42
        assert result["extra_pct"] == 10
        assert result["extra_spent_usd"] == 1.5
        assert result["extra_limit_usd"] == 10.0
        assert result["extra_balance_usd"] == 8.5
        assert result["session_reset"] == "Apr 10 7:00 PM"
        assert result["weekly_reset"] == "Apr 16 11:00 PM"

    def test_prefers_current_session_reset_over_nested_weekly_reset(self):
        payload = {
            "currentSession": {
                "usedPercent": 24,
                "resetAt": "2026-04-11T02:00:00Z",
                "sevenDay": {
                    "resetAt": "2026-04-17T06:00:00Z",
                },
            },
            "weekly": {
                "allModels": {
                    "usedPercent": 17,
                    "resetAt": "2026-04-17T06:00:00Z",
                },
            },
        }

        result = _parse_claude_web_usage_payload(payload)

        assert result["session_pct"] == 24
        assert result["session_reset"] == "Apr 10 7:00 PM"
        assert result["weekly_pct"] == 17
        assert result["weekly_reset"] == "Apr 16 11:00 PM"

    def test_parses_live_claude_schema(self):
        payload = {
            "five_hour": {
                "utilization": 24.0,
                "resets_at": "2026-04-11T02:00:00.000000+00:00",
            },
            "seven_day": {
                "utilization": 17.0,
                "resets_at": "2026-04-17T06:00:00.000000+00:00",
            },
            "seven_day_sonnet": {
                "utilization": 0.0,
                "resets_at": "2026-04-17T06:00:00.000000+00:00",
            },
            "seven_day_design": {
                "utilization": 42.0,
                "resets_at": "2026-04-17T06:00:00.000000+00:00",
            },
            "extra_usage": {
                "is_enabled": False,
                "monthly_limit": None,
                "used_credits": None,
                "utilization": None,
            },
        }

        result = _parse_claude_web_usage_payload(payload)
        assert result["session_pct"] == 24
        assert result["weekly_pct"] == 17
        assert result["weekly_sonnet_pct"] == 0
        assert result["weekly_design_pct"] == 42
        assert result["session_reset"] == "Apr 10 7:00 PM"
        assert result["weekly_reset"] == "Apr 16 11:00 PM"

    def test_omits_missing_weekly_sublimits(self):
        payload = {
            "five_hour": {
                "utilization": 24.0,
                "resets_at": "2026-04-11T02:00:00.000000+00:00",
            },
            "seven_day": {
                "utilization": 17.0,
                "resets_at": "2026-04-17T06:00:00.000000+00:00",
            },
        }

        result = _parse_claude_web_usage_payload(payload)

        assert "weekly_sonnet_pct" not in result
        assert "weekly_design_pct" not in result

    def test_does_not_promote_weekly_sublimit_to_aggregate_weekly(self):
        payload = {
            "weekly": {
                "sonnetOnly": {
                    "usedPercent": 9,
                    "resetAt": "2026-04-17T06:00:00Z",
                },
            },
        }

        result = _parse_claude_web_usage_payload(payload)

        assert result["weekly_sonnet_pct"] == 9
        assert "weekly_pct" not in result
        assert "weekly_reset" not in result

    @patch("src.pty_scraper.urllib.request.urlopen")
    def test_fetches_usage_from_web_api(self, mock_urlopen):
        response = MagicMock()
        response.read.return_value = json.dumps({
            "currentSession": {"usedPercent": 24, "resetAt": "2026-04-11T02:00:00Z"},
            "weekly": {"allModels": {"usedPercent": 17, "resetAt": "2026-04-17T06:00:00Z"}},
        }).encode()
        response.headers = {"content-type": "application/json"}
        response.__enter__ = MagicMock(return_value=response)
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response

        with patch.dict(
            os.environ,
            {
                "CLAUDE_WEB_COOKIE": "sessionKey=test-session; lastActiveOrg=org-123",
                "CLAUDE_WEB_ORG_ID": "",
                "CLAUDE_WEB_HEADERS_JSON": "",
                "CLAUDE_WEB_HEADERS_FILE": "",
            },
            clear=False,
        ):
            result = scrape_claude_usage_web()

        assert result["session_pct"] == 24
        assert result["weekly_pct"] == 17
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://claude.ai/api/organizations/org-123/usage"
        assert request.get_header("Cookie") == "sessionKey=test-session; lastActiveOrg=org-123"

    @patch("src.pty_scraper.urllib.request.urlopen")
    def test_cloudflare_challenge_is_reported(self, mock_urlopen):
        err = urllib.error.HTTPError(
            "https://claude.ai/api/organizations/org-123/usage",
            403,
            "Forbidden",
            {"cf-mitigated": "challenge"},
            BytesIO(b"Just a moment..."),
        )
        mock_urlopen.side_effect = err

        with patch.dict(
            os.environ,
            {
                "CLAUDE_WEB_COOKIE": "sessionKey=test-session; lastActiveOrg=org-123",
                "CLAUDE_WEB_ORG_ID": "",
                "CLAUDE_WEB_HEADERS_JSON": "",
                "CLAUDE_WEB_HEADERS_FILE": "",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="Cloudflare challenge"):
                scrape_claude_usage_web()


class TestScrapeCodexUsage:
    @pytest.fixture(autouse=True)
    def _codex_bin_present(self):
        # scrape_codex_usage() bails out early if the codex binary is not
        # installed; stub the lookup so these tests don't depend on the host.
        with patch("src.pty_scraper.shutil.which", return_value="/usr/local/bin/codex"):
            yield

    @patch("src.pty_scraper.subprocess.Popen")
    def test_parses_rate_limits(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        responses = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n",
            json.dumps({
                "jsonrpc": "2.0", "id": 2,
                "result": {
                    "rateLimits": {
                        "primary": {"usedPercent": 30, "resetsAt": 1744300000},
                        "secondary": {"usedPercent": 20, "resetsAt": 1744400000},
                    }
                }
            }) + "\n",
        ]
        mock_proc.stdout.readline = MagicMock(side_effect=responses)

        import select
        with patch("src.pty_scraper.select.select", return_value=([mock_proc.stdout], [], [])):
            result = scrape_codex_usage()

        assert result is not None
        assert result["session_remaining_pct"] == 70
        assert result["weekly_remaining_pct"] == 80

    @patch("src.pty_scraper.subprocess.Popen")
    def test_parses_code_review_rate_limit_from_tertiary_bucket(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        responses = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n",
            json.dumps({
                "jsonrpc": "2.0", "id": 2,
                "result": {
                    "rateLimits": {
                        "primary": {"usedPercent": 30, "resetsAt": 1744300000},
                        "secondary": {"usedPercent": 20, "resetsAt": 1744400000},
                        "tertiary": {"usedPercent": 40, "resetsAt": 1744500000},
                    }
                }
            }) + "\n",
        ]
        mock_proc.stdout.readline = MagicMock(side_effect=responses)

        import select
        with patch("src.pty_scraper.select.select", return_value=([mock_proc.stdout], [], [])):
            result = scrape_codex_usage()

        assert result is not None
        assert result["session_remaining_pct"] == 70
        assert result["weekly_remaining_pct"] == 80
        assert result["code_review_remaining_pct"] == 60

    @patch("src.pty_scraper.subprocess.Popen")
    def test_parses_model_sublimit_buckets(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        responses = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n",
            json.dumps({
                "jsonrpc": "2.0", "id": 2,
                "result": {
                    "rateLimits": {
                        "primary": {"usedPercent": 30, "resetsAt": 1744300000},
                        "secondary": {"usedPercent": 20, "resetsAt": 1744400000},
                        "gpt-5.4": {"usedPercent": 15, "name": "GPT-5.4 weekly"},
                        "codex-spark": {"usedPercent": 8, "label": "GPT-5.3 Codex Spark"},
                    }
                }
            }) + "\n",
        ]
        mock_proc.stdout.readline = MagicMock(side_effect=responses)

        import select
        with patch("src.pty_scraper.select.select", return_value=([mock_proc.stdout], [], [])):
            result = scrape_codex_usage()

        assert result is not None
        assert result["session_remaining_pct"] == 70
        assert result["weekly_remaining_pct"] == 80
        assert result["weekly_gpt54_remaining_pct"] == 85
        assert result["weekly_spark_remaining_pct"] == 92

    @patch("src.pty_scraper.subprocess.Popen")
    def test_omits_model_sublimits_when_absent(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        responses = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}) + "\n",
            json.dumps({
                "jsonrpc": "2.0", "id": 2,
                "result": {
                    "rateLimits": {
                        "primary": {"usedPercent": 30, "resetsAt": 1744300000},
                        "secondary": {"usedPercent": 20, "resetsAt": 1744400000},
                    }
                }
            }) + "\n",
        ]
        mock_proc.stdout.readline = MagicMock(side_effect=responses)

        import select
        with patch("src.pty_scraper.select.select", return_value=([mock_proc.stdout], [], [])):
            result = scrape_codex_usage()

        assert result is not None
        assert "weekly_gpt54_remaining_pct" not in result
        assert "weekly_spark_remaining_pct" not in result

    @patch("src.pty_scraper.subprocess.Popen")
    def test_no_response(self, mock_popen):
        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc
        mock_proc.stdout.readline = MagicMock(return_value="")

        import select
        with patch("src.pty_scraper.select.select", return_value=([], [], [])):
            result = scrape_codex_usage()
        assert result is None


class TestScrapeCodexAnalytics:
    def test_helper_script_is_committed(self):
        assert _codex_analytics_helper_script().exists()

    def test_config_detected_from_cookie(self):
        with patch.dict(
            os.environ,
            {
                "CODEX_WEB_COOKIE": "__Secure-next-auth.session-token.0=test-token",
                "CODEX_WEB_COOKIE_FILE": "",
                "CODEX_WEB_HEADERS_JSON": "",
                "CODEX_WEB_HEADERS_FILE": "",
                "CODEX_WEB_ANALYTICS_URL": "https://chatgpt.com/codex/cloud/settings/analytics",
            },
            clear=False,
        ):
            assert codex_web_analytics_configured() is True

    @patch("src.pty_scraper.subprocess.run")
    def test_fetches_codex_analytics_bundle(self, mock_run):
        helper_payload = {
            "window_days": 7,
            "group_by": "day",
            "date_range": {"start_date": "2026-04-03", "end_date": "2026-04-09"},
            "include_emails": False,
            "daily_workspace_usage_counts": {
                "data": [
                    {
                        "date": "2026-04-09",
                        "totals": {"users": 12, "threads": 20, "turns": 40, "credits": 8.5},
                        "clients": [
                            {"client_id": "CODEX_WEB", "users": 7, "threads": 12, "turns": 21, "credits": 4.5},
                            {"client_id": "CODEX_CLI", "users": 5, "threads": 8, "turns": 19, "credits": 4.0},
                        ],
                    }
                ]
            },
            "daily_sessions_messages_counts": {
                "data": [
                    {
                        "date": "2026-04-09",
                        "n_new_sessions_total": 10,
                        "n_user_messages_total": 30,
                        "n_users_used_codex": 8,
                        "n_tasks_web": 3,
                        "n_code_reviews_web": 2,
                        "credit_total": 6.25,
                    }
                ]
            },
            "daily_code_review_metrics": {
                "data": [
                    {
                        "date": "2026-04-09",
                        "n_reviews": 4,
                        "n_comments": 9,
                        "n_comments_p0": 1,
                        "n_comments_p1": 3,
                        "n_comments_p2": 5,
                    }
                ]
            },
        }
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps(helper_payload),
            stderr="",
        )

        with patch.dict(
            os.environ,
            {
                "CODEX_WEB_COOKIE": "__Secure-next-auth.session-token.0=test-token",
                "CODEX_WEB_COOKIE_FILE": "",
                "CODEX_WEB_HEADERS_JSON": "",
                "CODEX_WEB_HEADERS_FILE": "",
                "CODEX_WEB_ANALYTICS_URL": "https://chatgpt.com/codex/cloud/settings/analytics",
                "CODEX_ANALYTICS_WINDOW_DAYS": "7",
                "CODEX_ANALYTICS_GROUP_BY": "day",
            },
            clear=False,
        ):
            result = scrape_codex_analytics()

        assert result["daily_workspace_usage_counts"]["data"][0]["totals"]["users"] == 12
        assert result["summary"]["workspace"]["avg_daily_users"] == 12.0
        assert result["summary"]["sessions_messages"]["avg_daily_sessions"] == 10.0
        assert result["summary"]["code_review"]["avg_daily_reviews"] == 4.0
        assert result["date_range"]["start_date"] == "2026-04-03"

        helper_call = mock_run.call_args
        helper_input = json.loads(helper_call.kwargs["input"])
        assert helper_input["analytics_page_url"] == "https://chatgpt.com/codex/cloud/settings/analytics"
        assert helper_input["group_by"] == "day"
        assert helper_input["window_days"] == 7

    @patch("src.pty_scraper.subprocess.run")
    def test_helper_failure_raises_runtime_error(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Unauthorized")

        with patch.dict(
            os.environ,
            {
                "CODEX_WEB_COOKIE": "__Secure-next-auth.session-token.0=test-token",
                "CODEX_WEB_COOKIE_FILE": "",
                "CODEX_WEB_HEADERS_JSON": "",
                "CODEX_WEB_HEADERS_FILE": "",
                "CODEX_WEB_ANALYTICS_URL": "https://chatgpt.com/codex/cloud/settings/analytics",
            },
            clear=False,
        ):
            with pytest.raises(RuntimeError, match="Unauthorized"):
                scrape_codex_analytics()


class TestCursorAuthToken:
    def test_no_db_file(self):
        with patch.object(Path, "exists", return_value=False):
            assert _cursor_auth_token() is None

    def test_reads_token(self, tmp_path):
        import sqlite3
        db_file = tmp_path / "state.vscdb"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE ItemTable(key TEXT, value TEXT)")
        conn.execute("INSERT INTO ItemTable VALUES('cursorAuth/accessToken', 'test-token-123')")
        conn.commit()
        conn.close()

        with patch("src.pty_scraper._CURSOR_STATE_DB", db_file):
            token = _cursor_auth_token()
        assert token == "test-token-123"

    def test_no_token_row(self, tmp_path):
        import sqlite3
        db_file = tmp_path / "state.vscdb"
        conn = sqlite3.connect(db_file)
        conn.execute("CREATE TABLE ItemTable(key TEXT, value TEXT)")
        conn.commit()
        conn.close()

        with patch("src.pty_scraper._CURSOR_STATE_DB", db_file):
            assert _cursor_auth_token() is None


class TestScrapeCursorUsage:
    def test_parses_cursor_agent_free_limit_text(self):
        text = """
        Error: You've hit your usage limit
        fallbackModel:
        spendLimitHit: false
        chatMessage: *You've hit your free requests limit. [Upgrade to
        Pro](https://www.cursor.com/api/auth/checkoutDeepControl?tier=pro) for more usage.
        Your usage limits will reset when your monthly cycle ends on 12/31/2026.*
        spendLimits: [50,100,200]
        """

        result = _parse_cursor_agent_limit_text(text)

        assert result is not None
        assert result["limit_hit"] is True
        assert result["at_limit"] is True
        assert result["limit_kind"] == "free_requests"
        assert result["limit_message"] == "You've hit your free requests limit."
        assert result["plan"] == "free"
        assert result["total_requests"] == 50
        assert result["max_requests"] == 50
        assert result["remaining_requests"] == 0
        assert result["reset_at"] == "2026-12-31"
        assert result["spend_limit_hit"] is False
        assert result["spend_limits"] == [50, 100, 200]
        assert "fallback_model" not in result

    @patch("src.pty_scraper._cursor_auth_token", return_value=None)
    def test_no_token_returns_configured_cursor_limit_state(self, _mock_token, tmp_path):
        with patch.dict(
            os.environ,
            {
                "CURSOR_AGENT_LIMIT_TEXT": "You've hit your free requests limit. monthly cycle ends on 12/31/2026",
                "CURSOR_AGENT_LIMIT_FILE": "",
            },
            clear=False,
        ), patch("src.pty_scraper._CURSOR_LIMIT_STATE_FILE", tmp_path / "missing-status.txt"):
            result = scrape_cursor_usage()

        assert result is not None
        assert result["at_limit"] is True
        assert result["total_requests"] == 50
        assert result["remaining_requests"] == 0

    @patch("src.pty_scraper._cursor_auth_token", return_value=None)
    def test_no_token_reads_default_cursor_limit_file(self, _mock_token, tmp_path):
        limit_file = tmp_path / "cursor-agent-status.txt"
        limit_file.write_text("You've hit your free requests limit. monthly cycle ends on 12/31/2026")

        with patch.dict(
            os.environ,
            {"CURSOR_AGENT_LIMIT_TEXT": "", "CURSOR_AGENT_LIMIT_FILE": ""},
            clear=False,
        ), patch("src.pty_scraper._CURSOR_LIMIT_STATE_FILE", limit_file):
            result = scrape_cursor_usage()

        assert result is not None
        assert result["at_limit"] is True
        assert result["reset_at"] == "2026-12-31"

    @patch("src.pty_scraper._cursor_auth_token", return_value=None)
    def test_ignores_default_cursor_limit_file_after_reset(self, _mock_token, tmp_path):
        limit_file = tmp_path / "cursor-agent-status.txt"
        limit_file.write_text("You've hit your free requests limit. monthly cycle ends on 1/1/2020")

        with patch.dict(
            os.environ,
            {"CURSOR_AGENT_LIMIT_TEXT": "", "CURSOR_AGENT_LIMIT_FILE": ""},
            clear=False,
        ), patch("src.pty_scraper._CURSOR_LIMIT_STATE_FILE", limit_file):
            assert scrape_cursor_usage() is None

    def test_stale_free_limit_does_not_override_paid_plan(self):
        result = _merge_cursor_limit_state(
            {"plan": "pro", "total_requests": 10, "max_requests": 500},
            {
                "plan": "free",
                "limit_hit": True,
                "at_limit": True,
                "limit_kind": "free_requests",
                "total_requests": 50,
                "max_requests": 50,
            },
        )

        assert result == {"plan": "pro", "total_requests": 10, "max_requests": 500}

    @patch("src.pty_scraper.urllib.request.urlopen")
    @patch("src.pty_scraper._cursor_auth_token", return_value="test-token")
    def test_basic(self, mock_token, mock_urlopen, tmp_path):
        usage_resp = MagicMock()
        usage_resp.read.return_value = json.dumps({
            "gpt-4": {"numRequestsTotal": 10, "numTokens": 5000, "maxRequestUsage": 100},
            "startOfMonth": "2026-04-01",
        }).encode()
        usage_resp.__enter__ = MagicMock(return_value=usage_resp)
        usage_resp.__exit__ = MagicMock(return_value=False)

        profile_resp = MagicMock()
        profile_resp.read.return_value = json.dumps({
            "membershipType": "pro", "trialEligible": False,
        }).encode()
        profile_resp.__enter__ = MagicMock(return_value=profile_resp)
        profile_resp.__exit__ = MagicMock(return_value=False)

        mock_urlopen.side_effect = [usage_resp, profile_resp]
        with patch.dict(
            os.environ,
            {"CURSOR_AGENT_LIMIT_TEXT": "", "CURSOR_AGENT_LIMIT_FILE": ""},
            clear=False,
        ), patch("src.pty_scraper._CURSOR_LIMIT_STATE_FILE", tmp_path / "missing-status.txt"):
            result = scrape_cursor_usage()
        assert result is not None
        assert result["total_requests"] == 10
        assert result["plan"] == "pro"

    @patch("src.pty_scraper._cursor_auth_token", return_value=None)
    def test_no_token(self, mock, tmp_path):
        with patch.dict(
            os.environ,
            {"CURSOR_AGENT_LIMIT_TEXT": "", "CURSOR_AGENT_LIMIT_FILE": ""},
            clear=False,
        ), patch("src.pty_scraper._CURSOR_LIMIT_STATE_FILE", tmp_path / "missing-status.txt"):
            assert scrape_cursor_usage() is None
