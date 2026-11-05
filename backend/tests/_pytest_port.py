"""
Shared test server constants for use in individual test files.

Instead of hardcoding ``BASE = "http://127.0.0.1:8788"`` in every test file,
import from here so the port and state dir are always consistent with
what conftest.py computed for this worktree.

Usage::

    from tests._pytest_port import BASE

conftest.py publishes ``HERMES_WEBUI_TEST_PORT`` and
``HERMES_WEBUI_TEST_STATE_DIR`` to ``os.environ`` at module level
(before any test file is imported), so this module always reads the
correct values.  The auto-derivation fallback matches conftest's logic
exactly, so standalone imports also work correctly.
"""
import hashlib
import os
import pathlib

def _auto_test_port(repo_root: pathlib.Path) -> int:
    h = int(hashlib.md5(str(repo_root).encode()).hexdigest(), 16)
    return 20000 + (h % 10000)

def _auto_state_dir_name(repo_root: pathlib.Path) -> str:
    h = hashlib.md5(str(repo_root).encode()).hexdigest()[:8]
    return f"webui-test-{h}"

_TESTS_DIR   = pathlib.Path(__file__).parent.resolve()
_REPO_ROOT   = _TESTS_DIR.parent.resolve()

TEST_PORT = int(os.environ.get('HERMES_WEBUI_TEST_PORT',
                               str(_auto_test_port(_REPO_ROOT))))
BASE = f"http://127.0.0.1:{TEST_PORT}"

# Default parent is $HOME/.hermes, NOT $HERMES_HOME -- mirrors conftest.py
# so standalone use of this module still lands the state dir under a path the
# test user owns. HERMES_HOME is a data/state dir and may point at a
# root-owned location (a build-time seed dir in the Docker image), which
# would otherwise route TEST_STATE_DIR to an unwritable path. Conftest's env
# still wins via HERMES_WEBUI_TEST_STATE_DIR when set.
TEST_STATE_DIR = pathlib.Path(os.environ.get(
    'HERMES_WEBUI_TEST_STATE_DIR',
    str(pathlib.Path.home() / '.hermes' / _auto_state_dir_name(_REPO_ROOT))
))

# Default model injected by conftest — tests that mutate the default model
# must restore to this value so later tests see a consistent baseline.
# DeepSeek V4 Flash is the smoke default because it's cheap/fast; CI/local
# runs that need a different model set HERMES_WEBUI_DEFAULT_MODEL before
# invoking pytest.
#
# This is the CLI-shaped default (a bare/namespaced id).  It belongs to the
# config.yaml side of the model identity and is what /api/default-model
# expects — NOT what a chat request may carry (see TEST_ROUTABLE_MODEL).
TEST_DEFAULT_MODEL = os.environ.get('HERMES_WEBUI_DEFAULT_MODEL', 'deepseek/deepseek-v4-flash')

# A routable WebUI model identity, i.e. the only form /api/chat/start accepts
# (issue #55): the picker's "@provider:model" id.  A bare or namespaced value
# carries no provider hint, so the backend has to map it onto a catalog entry
# before it can be routed — and that mapping needs a warm model catalog.  The
# server answers /health as soon as it starts listening while the boot
# prewarm is still building the catalog, so a legacy value in a chat request
# races the prewarm and the request is rejected with 400 "Session model is
# invalid".  Tests that POST to /api/chat/start must therefore use a
# canonical identity: it is routable without consulting the catalog at all.
#
# OpenRouter is used because the non-live test server has no provider key
# (the conftest injects a stub OPENROUTER_API_KEY); the identity is what
# matters, not whether the provider answers.
TEST_ROUTABLE_MODEL = os.environ.get(
    'HERMES_WEBUI_ROUTABLE_MODEL', '@openrouter:deepseek/deepseek-v4-flash'
)
