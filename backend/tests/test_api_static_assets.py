"""Regression tests for Vite content-hash asset detection.

The regex that decides whether a /assets/* file is immutable (long-lived
disk-cacheable) must accept hyphens AND underscores in the hash, since
Vite's default hash alphabet is [a-zA-Z0-9_-]. Missing either character
silently downgrades a hashed asset to Cache-Control: no-store, which on
hard refresh causes Chrome ERR_TOO_MANY_RETRIES on this server's 5s
ThreadingHTTPServer timeout.
"""
from app.api.routes import _is_immutable_asset


def test_immutable_asset_accepts_hyphen_in_hash():
    # Reported regression: index-Pf-vFOO9.css was served with no-store.
    assert _is_immutable_asset("index-Pf-vFOO9.css") is True


def test_immutable_asset_accepts_underscore_in_hash():
    # Same regression class — TerminalView-qxJ8_QYu.css.
    assert _is_immutable_asset("TerminalView-qxJ8_QYu.css") is True


def test_immutable_asset_accepts_alphanumeric_only_hash():
    # Existing happy path must still work.
    assert _is_immutable_asset("index-CUX2vmMn.js") is True


def test_immutable_asset_accepts_hyphen_only_hash_inside_caps():
    # xterm-DGKw3h-6.js — split by hyphen; still needs to be immutable.
    assert _is_immutable_asset("xterm-DGKw3h-6.js") is True


def test_non_hashed_files_are_not_immutable():
    assert _is_immutable_asset("index.html") is False
    assert _is_immutable_asset("favicon.ico") is False
    assert _is_immutable_asset("logo.svg") is False