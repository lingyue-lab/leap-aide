"""Diagnostic monkey-patch: log base_url + model on every OpenAI chat.completions.create call.

**Default: OFF** — this module is installed only when ``LEAP_AIDE_DEBUG_OPENAI_CALLS=1``
is in the **test process** env (conftest.py reads this env var when launching the
test_server subprocess and, when set, exec's this file before running ``app.server``).

History: kept around after the 2026-09-18 DNS error investigation
(``/.claude/plans/dns-error-in-test-server-log.md``). The next time server.log
shows a ``httpcore.ConnectError`` / ``Name or service not known`` whose hostname
isn't obvious from context, the steps to identify it are:

    ssh testbox
    export LEAP_AIDE_DEBUG_OPENAI_CALLS=1
    export LEAP_AIDE_KEEP_TEST_STATE=1     # also keep server.log past teardown
    bash .lingyue-lab/scripts/test-backend.sh
    grep -E "DEBUG openai chat.completions.create|ConnectError" \
        /tmp/leap-aide-test-state-keep/server.log

The DEBUG line just before each ConnectError pinpoints the failing base_url +
model. Set ``LEAP_AIDE_DEBUG_OPENAI_CALLS=`` (unset) to silence again.

Why this lives in webui, not hermes-agent:
  Per CLAUDE.md, ``/usr/local/lib/hermes-agent/`` is an external dependency and
  must not be modified. ``chat_completion_helpers._open_stream`` is where the
  actual HTTP request goes out, but ``httpcore.ConnectError`` drops the URL.
  Wrapping the OpenAI SDK at runtime captures every call (main chat,
  auxiliary ``title_generation``, vision, compression, anything that hits an
  OpenAI-compatible endpoint) without touching hermes-agent.

Install order (matches conftest's launch code):
    python -c "exec(open('<path-to-this-file>').read()); \
               import runpy; runpy.run_module('app.server', run_name='__main__', alter_sys=True)"

What you'll see in server.log when enabled:
    DEBUG _debug_openai_patch: installed (logging every chat.completions.create call)
    DEBUG openai chat.completions.create base_url=https://api.deepseek.com/v1/ model='deepseek-v4-flash'
    DEBUG openai chat.completions.create base_url=https://openrouter.ai/api/v1/ model='anthropic/claude-opus-4.6'
    ...
"""
import sys


def install() -> None:
    try:
        import openai
        import openai.resources.chat.completions as _oc
    except ImportError as _e:
        print(
            f"DEBUG _debug_openai_patch: openai import failed ({_e!r}); "
            "patch skipped — chat completions will not be logged",
            flush=True,
        )
        return

    _orig_create = _oc.Completions.create

    def _wrapped_create(self, *args, **kwargs):
        try:
            _client = getattr(self, "_client", None)
            _base = getattr(_client, "base_url", None) if _client is not None else None
            # chat.completions.create(model=..., ...) → kwargs.get("model")
            # legacy positional form → args[0]
            _model = kwargs.get("model")
            if _model is None and args:
                _model = args[0]
            print(
                f"DEBUG openai chat.completions.create base_url={_base} model={_model!r}",
                flush=True,
            )
        except Exception as _dbg_e:
            # Never let the diagnostic itself break the real call.
            print(
                f"DEBUG openai chat.completions.create: pre-call print failed {_dbg_e!r}",
                flush=True,
            )
        return _orig_create(self, *args, **kwargs)

    _oc.Completions.create = _wrapped_create
    print(
        "DEBUG _debug_openai_patch: installed (logging every chat.completions.create call)",
        flush=True,
    )


# Run on import (the conftest launch exec's this file before app.server).
install()