"""Sign-in helpers for the subscription backends.

cat-claws runs on the agent CLIs' own logins (no API key). For Claude that is
the Claude Code CLI the Agent SDK launches -- a login separate from the Claude
desktop app's sign-in, so users of the app can be signed in there and still
be signed out here. These helpers make that one-time step easy wherever the
user is:

- ``auth_status(agent)``: cheap check (no model call) via ``claude auth
  status``; the check uses the SAME binary the SDK launches.
- ``login(agent)``: starts the browser sign-in from Python -- works from a
  terminal, a notebook, or inside the Claude app (ask Claude to run it).
- ``ensure_signed_in(agent)``: the preflight classify()/cat-stack call before
  processing rows. Signed in -> returns immediately. Signed out -> opens ONE
  browser window for the sign-in by itself (once per process, even when many
  calls start at once) and continues when it completes; where no browser can
  open (CI, headless servers, CATCLAWS_NO_AUTO_LOGIN=1) or the sign-in does
  not complete, raises NotSignedInError with instructions for the context.

The Claude desktop app's own sign-in cannot be reused: the app hands its
token to its Claude process over a private channel and shares it with no
other program, so the CLI needs its own login -- once; it then persists.
- ``sign_in_help(agent)``: those instructions as text.
"""

import json
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

__all__ = ["NotSignedInError", "auth_status", "ensure_signed_in", "login", "sign_in_help"]


class NotSignedInError(ConnectionError):
    """The agent CLI is not signed in; the message says how to sign in."""


# Signed-in results are cached per process (a login does not silently expire
# mid-run often enough to justify re-checking every row); signed-out results
# are never cached, so a login during the session is picked up.
_SIGNED_IN = set()
# One automatic sign-in attempt per agent per process: concurrent callers wait
# on the lock and reuse the outcome, so at most one browser window opens.
_LOGIN_LOCK = threading.Lock()
_LOGIN_ATTEMPTED = set()
LOGIN_TIMEOUT_S = 300


def in_claude_app() -> bool:
    """True when running inside the Claude desktop app (its Code tab)."""
    return os.environ.get("CLAUDE_CODE_ENTRYPOINT", "").startswith("claude-desktop")


def can_open_browser() -> bool:
    """Whether an automatic browser sign-in makes sense here: not opted out,
    not CI, and a desktop session (macOS/Windows, or Linux with a display)."""
    if os.environ.get("CATCLAWS_NO_AUTO_LOGIN") or os.environ.get("CI") \
            or os.environ.get("GITHUB_ACTIONS"):
        return False
    if platform.system() in ("Darwin", "Windows"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _claude_cli() -> str | None:
    """The claude binary the Agent SDK will launch: the SDK's bundled CLI
    first, then PATH (mirrors claude_agent_sdk's _find_cli order)."""
    try:
        import claude_agent_sdk
        name = "claude.exe" if platform.system() == "Windows" else "claude"
        bundled = Path(claude_agent_sdk.__file__).parent / "_bundled" / name
        if bundled.is_file():
            return str(bundled)
    except ImportError:
        pass
    return shutil.which("claude")


def _child_env() -> dict:
    """Same auth environment as the adapter: an inherited ANTHROPIC_API_KEY
    would take precedence over the subscription login (see claude.py)."""
    env = dict(os.environ)
    if not os.environ.get("CATCLAWS_USE_API_KEY"):
        env["ANTHROPIC_API_KEY"] = ""
    return env


def auth_status(agent: str = "claude") -> dict:
    """Is the agent CLI signed in? No model call.

    Returns ``{"agent", "logged_in", "method", "detail"}``; ``logged_in`` is
    True, False, or None when it cannot be determined (never blocks a run).
    """
    if agent == "claude":
        cli = _claude_cli()
        if not cli:
            return {"agent": agent, "logged_in": None, "method": None,
                    "detail": "Claude CLI not found"}
        try:
            out = subprocess.run([cli, "auth", "status"], capture_output=True, text=True,
                                 timeout=30, env=_child_env())
            info = json.loads(out.stdout)
            return {"agent": agent, "logged_in": bool(info.get("loggedIn")),
                    "method": info.get("authMethod"), "detail": out.stdout.strip()}
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return {"agent": agent, "logged_in": None, "method": None, "detail": str(e)}

    if agent == "codex":
        cli = shutil.which("codex")
        if not cli:
            return {"agent": agent, "logged_in": None, "method": None,
                    "detail": "codex CLI not on PATH"}
        try:
            out = subprocess.run([cli, "login", "status"], capture_output=True, text=True,
                                 timeout=30)
            text = (out.stdout + out.stderr).strip()
            if out.returncode == 0:
                return {"agent": agent, "logged_in": True, "method": None, "detail": text}
            if "not logged in" in text.lower():
                return {"agent": agent, "logged_in": False, "method": None, "detail": text}
            return {"agent": agent, "logged_in": None, "method": None, "detail": text}
        except (OSError, subprocess.SubprocessError) as e:
            return {"agent": agent, "logged_in": None, "method": None, "detail": str(e)}

    raise ValueError(f"Unknown agent {agent!r}; choose 'claude' or 'codex'.")


def sign_in_help(agent: str = "claude") -> str:
    """How to sign in, worded for where the user is."""
    if agent == "codex":
        return ("Codex is not signed in (needs a ChatGPT plan with Codex access). "
                "Run catclaws.login('codex') in Python, or `codex login` in a terminal.")
    if in_claude_app():
        where = ("You're in the Claude app: ask Claude to run catclaws.login() for you "
                 "(a browser window opens for you to approve), or open the Terminal "
                 "panel in the Code tab and run `claude auth login`.")
    else:
        where = ("Run catclaws.login() in Python, or `claude auth login` in a terminal; "
                 "a browser window opens for you to approve.")
    return ("The Claude CLI is not signed in. cat-claws uses the Claude Code CLI's "
            "own login, which is separate from the Claude desktop app's sign-in "
            "(sign in once; it is then kept). " + where + " For unattended or "
            "scheduled runs (no browser), run `claude setup-token` once and set "
            "CLAUDE_CODE_OAUTH_TOKEN.")


def login(agent: str = "claude", timeout: float = LOGIN_TIMEOUT_S) -> bool:
    """Start the browser sign-in for the agent CLI; True once signed in.

    Output is streamed so the sign-in URL is visible in terminals and
    notebooks alike (the browser normally opens by itself). stdin is closed
    so the CLI never waits for typed input, and the attempt is bounded by
    `timeout` seconds.
    """
    if agent == "claude":
        cli = _claude_cli()
        if not cli:
            raise FileNotFoundError('Claude CLI not found. Run: pip install "cat-claws[claude]"')
        cmd = [cli, "auth", "login", "--claudeai"]
    elif agent == "codex":
        cli = shutil.which("codex")
        if not cli:
            raise FileNotFoundError("codex CLI not found on PATH.")
        cmd = [cli, "login"]
    else:
        raise ValueError(f"Unknown agent {agent!r}; choose 'claude' or 'codex'.")

    print(f"Signing in to {agent}: approve it in the browser window that opens "
          f"(one time; the login is then kept).")
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                env=_child_env() if agent == "claude" else None)
    except OSError as e:
        print(f"Could not start the {agent} sign-in: {e}")
        return False
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        for line in proc.stdout:
            print(line, end="")
        proc.wait()
    finally:
        timer.cancel()
    ok = auth_status(agent)["logged_in"] is True
    if ok:
        _SIGNED_IN.add(agent)
    print("Signed in." if ok else "Sign-in did not complete.")
    return ok


def ensure_signed_in(agent: str = "claude", auto_login: bool | None = None) -> None:
    """Preflight before any row runs.

    Signed in (or status undeterminable) -> return. Signed out -> open ONE
    browser sign-in automatically and continue once it completes; with
    concurrent callers only the first opens a window and the rest reuse its
    outcome, and a failed attempt is not repeated in the same process.
    `auto_login=None` means "when a browser can open here"
    (see can_open_browser); otherwise raise NotSignedInError with
    instructions for this context.
    """
    if agent in _SIGNED_IN:
        return
    with _LOGIN_LOCK:
        if agent in _SIGNED_IN:
            return
        status = auth_status(agent)
        if status["logged_in"] is True:
            _SIGNED_IN.add(agent)
            return
        if status["logged_in"] is None:
            return  # can't tell; let the call itself report any problem
        if auto_login is None:
            auto_login = can_open_browser()
        if auto_login and agent not in _LOGIN_ATTEMPTED:
            _LOGIN_ATTEMPTED.add(agent)
            if login(agent):
                return
    raise NotSignedInError(sign_in_help(agent))
