"""Sign-in helpers: status parsing, context-aware help, the preflight's
fail-fast / offer-to-sign-in behavior. subprocess is mocked throughout; no
real CLI is launched."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import catclaws
from catclaws import _auth
from catclaws._auth import NotSignedInError, auth_status, ensure_signed_in, sign_in_help


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(_auth, "_SIGNED_IN", set())
    monkeypatch.setattr(_auth, "_LOGIN_ATTEMPTED", set())
    monkeypatch.setattr(_auth, "_claude_cli", lambda: "/fake/claude")


def _status(logged_in, method="claude.ai"):
    out = json.dumps({"loggedIn": logged_in, "authMethod": method if logged_in else "none"})
    return SimpleNamespace(stdout=out, stderr="", returncode=0)


def test_exported_from_package():
    for name in ("login", "auth_status", "ensure_signed_in", "sign_in_help", "NotSignedInError"):
        assert hasattr(catclaws, name)


def test_status_parses_cli_json_and_uses_subscription_env():
    with patch("subprocess.run", return_value=_status(True)) as run:
        st = auth_status("claude")
    assert st["logged_in"] is True and st["method"] == "claude.ai"
    args, kwargs = run.call_args
    assert args[0] == ["/fake/claude", "auth", "status"]
    assert kwargs["env"]["ANTHROPIC_API_KEY"] == ""  # same auth path as the adapter


def test_status_unknown_when_cli_missing(monkeypatch):
    monkeypatch.setattr(_auth, "_claude_cli", lambda: None)
    assert auth_status("claude")["logged_in"] is None


def test_help_in_claude_app_points_at_claude_and_terminal_panel(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "claude-desktop")
    msg = sign_in_help("claude")
    assert "ask Claude to run catclaws.login()" in msg and "Terminal panel" in msg
    assert "CLAUDE_CODE_OAUTH_TOKEN" in msg


def test_help_in_terminal_points_at_login(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_ENTRYPOINT", raising=False)
    msg = sign_in_help("claude")
    assert "catclaws.login()" in msg and "claude auth login" in msg
    assert "Claude app" not in msg


@pytest.mark.real_auth
def test_preflight_signed_in_is_quiet_and_cached():
    with patch("subprocess.run", return_value=_status(True)) as run:
        ensure_signed_in("claude")
        ensure_signed_in("claude")
    assert run.call_count == 1


@pytest.mark.real_auth
def test_signed_out_without_browser_raises_with_help():
    with patch("subprocess.run", return_value=_status(False)):
        with pytest.raises(NotSignedInError) as ei:
            ensure_signed_in("claude", auto_login=False)
    assert "not signed in" in str(ei.value)


@pytest.mark.real_auth
def test_signed_out_opens_sign_in_automatically(monkeypatch):
    logins = []
    monkeypatch.setattr(_auth, "login", lambda agent: logins.append(agent) or True)
    with patch("subprocess.run", return_value=_status(False)):
        ensure_signed_in("claude", auto_login=True)  # no prompt, no raise
    assert logins == ["claude"]


@pytest.mark.real_auth
def test_concurrent_callers_open_one_browser_window(monkeypatch):
    import threading
    import time
    logins = []

    def slow_login(agent):
        logins.append(agent)
        time.sleep(0.2)
        _auth._SIGNED_IN.add(agent)  # login() records success
        return True

    monkeypatch.setattr(_auth, "login", slow_login)
    with patch("subprocess.run", return_value=_status(False)):
        threads = [threading.Thread(target=ensure_signed_in, args=("claude", True))
                   for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert logins == ["claude"]


@pytest.mark.real_auth
def test_failed_sign_in_is_not_retried_in_the_same_process(monkeypatch):
    logins = []
    monkeypatch.setattr(_auth, "login", lambda agent: logins.append(agent) or False)
    monkeypatch.setattr(_auth, "_LOGIN_ATTEMPTED", set())
    with patch("subprocess.run", return_value=_status(False)):
        for _ in range(3):
            with pytest.raises(NotSignedInError):
                ensure_signed_in("claude", auto_login=True)
    assert logins == ["claude"]


@pytest.mark.parametrize("env", [{"CATCLAWS_NO_AUTO_LOGIN": "1"}, {"CI": "true"},
                                 {"GITHUB_ACTIONS": "true"}])
def test_no_automatic_browser_in_ci_or_when_opted_out(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert _auth.can_open_browser() is False


@pytest.mark.real_auth
def test_preflight_unknown_status_never_blocks(monkeypatch):
    monkeypatch.setattr(_auth, "_claude_cli", lambda: None)
    ensure_signed_in("claude", auto_login=False)  # no raise


@pytest.mark.real_auth
def test_classify_fails_fast_before_any_row(monkeypatch):
    calls = []

    class Adapter:
        default_model = "claude-sonnet-5"

        async def one_shot(self, *a, **k):
            calls.append(1)
            return '{"1": "1"}', None

    from conftest import CLASSIFY_MODULE
    monkeypatch.setattr(CLASSIFY_MODULE, "get_adapter", lambda name: Adapter())
    monkeypatch.setenv("CATCLAWS_NO_AUTO_LOGIN", "1")  # no browser in tests
    with patch("subprocess.run", return_value=_status(False)):
        with pytest.raises(NotSignedInError):
            catclaws.classify(input_data=["a", "b"], categories=["X"], description="d",
                              agent="claude")
    assert calls == []  # no row was attempted
