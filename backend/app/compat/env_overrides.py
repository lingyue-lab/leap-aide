"""Per-session environment overrides (ContextVar).

ContextVar, NOT threading.local: Hermes runs each tool call on a
ThreadPoolExecutor worker wrapped in propagate_context_to_thread()
(tools/thread_context.py), which copies contextvars into the worker.
threading.local never reached those worker threads, so tool-spawned
subprocesses (e.g. the terminal tool's bash) fell back to the raw
process-global os.environ — which concurrent sessions also write
(BROWSER_CDP_URL, HERMES_HOME, ...) — leaking one session's values
into another session's commands.

Consumers of this ContextVar:
- ``app.api.config._IsolatedPopen`` — merges the overrides into the
  env of every subprocess spawned on the current context;
- ``app.compat.browser_cdp`` — resolves the session's CDP endpoint for
  the Hermes SDK's in-process browser-tool readers (via the carried
  ``HERMES_SESSION_KEY`` → session-display JSON; the SDK itself must
  not be modified). The CDP URL itself deliberately does NOT ride this
  ContextVar — the session-display JSON is the single source of truth.
"""

import contextvars

env_override_ctx: "contextvars.ContextVar[dict]" = contextvars.ContextVar(
    "leap_aide_env_overrides", default=None
)
