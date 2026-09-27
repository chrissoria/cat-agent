"""Shared fixtures.

classify() runs a sign-in preflight (catclaws._auth.ensure_signed_in) that
shells out to the real agent CLI. Unit tests must not depend on this
machine's login state, so it is a no-op by default; tests/test_auth.py
exercises the real function with subprocess mocked.
"""
import sys

import pytest

import catclaws.classify  # noqa: F401  (registers the module in sys.modules)

# `catclaws.classify` the attribute is the FUNCTION (re-exported by the
# package); patch the module object itself.
CLASSIFY_MODULE = sys.modules["catclaws.classify"]


@pytest.fixture(autouse=True)
def _no_sign_in_preflight(request, monkeypatch):
    if request.node.get_closest_marker("real_auth"):
        return
    monkeypatch.setattr(CLASSIFY_MODULE, "ensure_signed_in", lambda *a, **k: None)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_auth: run the real sign-in preflight")
