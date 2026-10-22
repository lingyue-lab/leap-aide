"""
leap-aide -- Main server entry point.
Thin routing shell: imports Handler, delegates to api/routes.py, runs server.
All business logic lives in api/*.
"""
import logging
import os
import signal
import socket
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

from app.api.auth import check_auth
from app.api.config import HOST, PORT, STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE
from app.api.helpers import j, get_profile_cookie
from app.api.profiles import set_request_profile, clear_request_profile
from app.api.routes import handle_get, handle_post
from app.api.startup import auto_install_agent_deps, fix_credential_permissions
from app.api.updates import WEBUI_VERSION


class QuietHTTPServer(ThreadingHTTPServer):
    """Custom HTTP server that silently handles common network errors."""
    request_queue_size = 128  # Increase backlog for concurrent SSE connections

    def handle_error(self, request, client_address):
        """Override to suppress logging for common client disconnect errors."""
        exc_type, exc_value, _ = sys.exc_info()

        # Silently ignore common connection errors caused by client disconnects
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
            return

        # Also handle socket errors that indicate client disconnect
        if exc_type is socket.error:
            # errno 54 is Connection reset by peer on macOS/BSD
            # errno 104 is Connection reset by peer on Linux
            if exc_value.errno in (54, 104, 32):  # ECONNRESET, EPIPE
                return

        # Handle SSL errors from abrupt client disconnects (e.g., browser refresh during streaming)
        import ssl
        if exc_type is ssl.SSLError:
            return

        # For other errors, use default logging
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    timeout = 30  # seconds — kills idle/incomplete connections to prevent thread exhaustion
    protocol_version = 'HTTP/1.1'  # Enable keep-alive and proper cache semantics
    _ver_suffix = WEBUI_VERSION.removeprefix('v')
    server_version = ('leap-aide/' + _ver_suffix) if _ver_suffix != 'unknown' else 'leap-aide'
    def log_message(self, fmt, *args): pass  # suppress default Apache-style log

    def log_request(self, code: str='-', size: str='-') -> None:
        """Structured JSON logs for each request."""
        import json as _json
        duration_ms = round((time.time() - vars(self).get('_req_t0', time.time())) * 1000, 1)
        record = _json.dumps({
            'method': vars(self).get('command', '-') or '-',
            'path': vars(self).get('path', '-') or '-',
            'status': int(code) if str(code).isdigit() else code,
            'ms': duration_ms,
        })
        logger.info(record)

    def do_GET(self) -> None:
        self._req_t0 = time.time()
        # Log all incoming requests for debugging
        logger.warning('[REQ] %s %s from %s', self.command, self.path, self.client_address)
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        try:
            parsed = urlparse(self.path)
            # Static asset requests use a shorter timeout to release threads quickly
            if parsed.path.startswith('/assets/') or parsed.path.startswith('/static/'):
                self.timeout = 5
            if not check_auth(self, parsed): return

            # ── WebSocket terminal upgrade ─────────────────────────────────────
            if parsed.path == '/api/ws/terminal':
                if self.headers.get('Upgrade', '').lower() == 'websocket':
                    from app.api.terminal import handle_ws_terminal
                    handle_ws_terminal(self)
                    return
                return j(self, {'error': 'websocket upgrade required'}, status=426)

            # ── WebSocket VNC proxy upgrade ────────────────────────────────────
            # Matches: /api/ws/vnc (default port) or /api/ws/vnc/<port>
            import re as _re_vnc
            _vnc_match = _re_vnc.match(r'^/api/ws/vnc(?:/(\d+))?$', parsed.path)
            if _vnc_match:
                if self.headers.get('Upgrade', '').lower() == 'websocket':
                    from app.api.vnc import handle_ws_vnc
                    vnc_path_port = int(_vnc_match.group(1)) if _vnc_match.group(1) else None
                    handle_ws_vnc(self, path_port=vnc_path_port)
                    return
                return j(self, {'error': 'websocket upgrade required'}, status=426)

            # ── WebSocket events endpoint ─────────────────────────────────────
            if parsed.path == '/api/ws/events':
                if self.headers.get('Upgrade', '').lower() == 'websocket':
                    from app.api.ws_events import handle_ws_events
                    handle_ws_events(self)
                    return
                return j(self, {'error': 'websocket upgrade required'}, status=426)

            result = handle_get(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Silently ignore client disconnect errors
            pass
        except Exception as e:
            # Check if this is a socket error (errno 32 = Broken pipe)
            import socket
            if isinstance(e, socket.error) and e.errno == 32:
                pass  # Silently ignore broken pipe
            else:
                logger.error('%s %s\n%s', self.command, self.path, traceback.format_exc())
                return j(self, {'error': 'Internal server error'}, status=500)
        finally:
            clear_request_profile()

    def do_POST(self) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        try:
            parsed = urlparse(self.path)
            if not check_auth(self, parsed): return
            result = handle_post(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Silently ignore client disconnect errors
            pass
        except Exception as e:
            # Check if this is a socket error (errno 32 = Broken pipe)
            import socket
            if isinstance(e, socket.error) and e.errno == 32:
                pass  # Silently ignore broken pipe
            else:
                logger.error('%s %s\n%s', self.command, self.path, traceback.format_exc())
                return j(self, {'error': 'Internal server error'}, status=500)
        finally:
            clear_request_profile()


# ── Startup stage helpers ──

_t0 = 0.0
_TOTAL_STAGES = 10
_current_stage = ''


def _stage(n: int, name: str) -> None:
    """Buffer stage name for later output by _done/_fail/_warn."""
    global _current_stage
    _current_stage = f'[{n}/{_TOTAL_STAGES}] {name}'


def _done(extra: str = '') -> None:
    """Mark stage complete with elapsed time."""
    global _current_stage
    elapsed = round(time.monotonic() - _t0, 2)
    suffix = f'  {extra}' if extra else ''
    logger.info('%s  OK  %ss%s', _current_stage, elapsed, suffix)
    _current_stage = ''


def _fail(msg: str) -> None:
    """Mark stage failed."""
    global _current_stage
    elapsed = round(time.monotonic() - _t0, 2)
    logger.error('%s  FAIL  %ss  %s', _current_stage, elapsed, msg)
    _current_stage = ''


def _warn(msg: str) -> None:
    """Mark stage completed with warning."""
    global _current_stage
    elapsed = round(time.monotonic() - _t0, 2)
    logger.warning('%s  WARN  %ss  %s', _current_stage, elapsed, msg)
    _current_stage = ''


def _prewarm() -> None:
    """Pre-warm the agent runtime before serving the first chat.

    Two phases:

    1. Synchronous import warm-up (main thread): the chat path lazy-imports
       a large chunk of the hermes-agent SDK inside functions (e.g.
       agent.conversation_loop, agent.auxiliary_client, hermes_state,
       gateway.session_context), so without this the FIRST chat after boot
       pays ~2-3s of module loading on the request path.  Imports are local
       and deterministic — no network — so they run inline rather than on a
       thread (avoids concurrent-import hazards).  Individual failures are
       logged and skipped; the lazy import on the chat path retries them.
    2. Background cache warm-up (daemon threads): network-bound work
       (models list, MCP discovery, tool definitions, connectivity probe)
       that can take minutes on a slow boot network, so it must not block
       listening.  Failures never abort the server — subsequent requests
       fall back to the normal lazy-init path (issue #8).

    Besides warming, this marks the coordination state in app.api.prewarm so
    a chat sent within seconds of boot joins the in-flight prewarm instead of
    racing it.
    """
    import threading

    # ── Phase 1: synchronous import warm-up (main thread) ──
    # Covers the lazy-import surface of one full agent turn: stream setup,
    # AIAgent.run_conversation, tool execution dispatch, and auxiliary
    # clients (title/approval).  Keep in sync with the lazy imports in
    # app/api/streaming.py and run_agent.py.
    _CHAT_PATH_IMPORTS = (
        'gateway.session_context',   # set_session_vars in _run_agent_streaming
        'hermes_state',              # SessionDB for session_search
        'hermes_cli.runtime_provider',  # resolve_runtime_provider (API key)
        'tools.approval',            # gateway notify registration
        'tools.process_registry',    # process_loop drain
        'tools.async_delegation',    # drain event claiming
        'tools.mcp_tool',            # module only; discovery runs in prewarm-mcp
        'agent.conversation_loop',   # lazy-imported by run_agent.run_conversation
        'agent.chat_completion_helpers',
        'agent.auxiliary_client',    # title/approval auxiliary clients
        'agent.title_generator',
    )
    from importlib import import_module as _import_module
    _imports_t0 = time.monotonic()
    _import_failed = []
    for _mod in _CHAT_PATH_IMPORTS:
        try:
            _import_module(_mod)
        except Exception as _e:
            _import_failed.append(f'{_mod}: {type(_e).__name__}')
    if _import_failed:
        logger.warning('Pre-warm: agent imports partial (%.1fs, failed: %s)',
                       time.monotonic() - _imports_t0, '; '.join(_import_failed))
    else:
        logger.info('Pre-warm: agent imports ready (%.1fs, %d modules)',
                    time.monotonic() - _imports_t0, len(_CHAT_PATH_IMPORTS))

    # ── Phase 2: background cache warm-up (daemon threads) ──
    from app.api import prewarm as _prewarm_coord
    _prewarm_coord.begin_prewarm()

    def _warm_models():
        t0 = time.monotonic()
        try:
            from app.api.config import get_available_models
            result = get_available_models()
            n_groups = len(result.get('groups', []))
            logger.info('Pre-warm: models cache ready (%.1fs, %d groups)',
                        time.monotonic() - t0, n_groups)
        except Exception:
            logger.warning('Pre-warm: models cache failed (%.1fs)',
                           time.monotonic() - t0, exc_info=True)

    def _warm_mcp():
        t0 = time.monotonic()
        try:
            from tools.mcp_tool import discover_mcp_tools
            discover_mcp_tools()
            logger.info('Pre-warm: MCP tools discovered (%.1fs)',
                        time.monotonic() - t0)
        except Exception:
            logger.warning('Pre-warm: MCP discovery failed (%.1fs)',
                           time.monotonic() - t0, exc_info=True)
        finally:
            # Always release chat threads waiting in wait_for_mcp_discovery,
            # even when discovery failed (issue #8).
            _prewarm_coord.mark_mcp_done()

    def _warm_tool_defs():
        # Runs AFTER MCP discovery: MCP server registration bumps the SDK
        # registry generation, which is part of the get_tool_definitions
        # memo key — a snapshot taken before discovery would miss the memo
        # on the first chat (issue #8).
        if _prewarm_coord.tool_definitions_disabled():
            _prewarm_coord.mark_tool_defs_done()
            return
        if not _prewarm_coord.wait_for_mcp_discovery(timeout=150.0):
            logger.warning('Pre-warm: MCP discovery still running after 150s; '
                           'snapshotting tool definitions anyway')
        try:
            from app.api.config import get_config, _resolve_cli_toolsets
            # Same helper + args the chat path passes to AIAgent
            # (streaming._resolve_cli_toolsets on the active profile config),
            # so the memo keys match.
            _prewarm_coord.warm_tool_definitions(list(_resolve_cli_toolsets(get_config())))
        except Exception:
            logger.warning('Pre-warm: tool definitions warmup failed to start',
                           exc_info=True)
            _prewarm_coord.mark_tool_defs_done()

    def _warm_connectivity():
        # GET <active provider base_url>/models with a 5s cap; proves the
        # outbound path (DNS/TLS/proxy/IPv4 preference) works before the
        # first chat rather than as a 30s ConnectTimeout then (issue #8).
        # Disabled via HERMES_WEBUI_CONNECTIVITY_PROBE=0 inside the probe.
        from app.api.providers import probe_provider_connectivity
        probe_provider_connectivity()

    threading.Thread(target=_warm_models, name='prewarm-models', daemon=True).start()
    threading.Thread(target=_warm_mcp, name='prewarm-mcp', daemon=True).start()
    threading.Thread(target=_warm_tool_defs, name='prewarm-tooldefs', daemon=True).start()
    threading.Thread(target=_warm_connectivity, name='prewarm-connectivity', daemon=True).start()


def main() -> None:
    global _t0
    _t0 = time.monotonic()

    # ── Logging setup ──
    _log_level = os.getenv('LOG_LEVEL', 'WARNING').upper()
    logging.basicConfig(
        level=getattr(logging, _log_level, logging.WARNING),
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    from app.api.config import (
        print_startup_config,
        apply_network_preferences,
        verify_hermes_imports,
        _HERMES_FOUND,
    )

    # ── Banner ──

    # ── Stage 1: Load config ──
    _stage(1, 'Loading config')
    print_startup_config()
    # Patch getaddrinfo before any prewarm thread does network I/O (issue #8).
    apply_network_preferences()
    _done()

    # ── Stage 2: Fix credential permissions ──
    _stage(2, 'Fixing credential permissions')
    fix_credential_permissions()
    _done()

    # ── Stage 3: Detect runtime environment ──
    _stage(3, 'Detecting runtime environment')
    within_container = False
    try:
        with open('/.within_container', 'r') as f:
            within_container = True
    except FileNotFoundError:
        pass
    if within_container:
        _done('container')
    else:
        _done()

    # Security: warn if binding non-loopback without authentication
    from app.api.auth import is_auth_enabled
    if HOST not in ('127.0.0.1', '::1', 'localhost') and not is_auth_enabled():
        logger.warning('Binding to %s with NO PASSWORD SET.', HOST)
        logger.warning('Anyone on the network can access your filesystem and agent.')
        logger.warning('Set a password via Settings or HERMES_WEBUI_PASSWORD env var.')
        if within_container:
            logger.warning('Running within container, must bind to 0.0.0.0 to publish the port.')
    elif not is_auth_enabled():
        logger.info('No password set. Any local process can read sessions via the API.')

    # ── Stage 4: Verify agent dependencies ──
    _stage(4, 'Verifying agent dependencies')
    ok, missing, errors = verify_hermes_imports()
    if not ok and _HERMES_FOUND:
        logger.warning('Missing modules: %s', missing)
        for mod, err in errors.items():
            logger.warning('  %s: %s', mod, err)
        logger.info('Attempting auto-install...')
        auto_install_agent_deps()
        ok, missing, errors = verify_hermes_imports()
        if not ok:
            _warn(f'missing: {", ".join(missing)}')
        else:
            _done('auto-installed')
    else:
        _done()

    # ── Stage 5: Create directories ──
    _stage(5, 'Creating directories')
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    from app.api.config import BUILTIN_DEFAULT_WORKSPACE
    BUILTIN_DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)
    _done()

    # ── Stage 6: Start gateway watcher ──
    _stage(6, 'Starting gateway watcher')
    try:
        from app.api.session_watcher import start_watcher
        start_watcher()
        _done()
    except Exception as e:
        _warn(str(e))

    # ── Stage 7: Create HTTP server + TLS ──
    _stage(7, 'Creating HTTP server + TLS')
    httpd = QuietHTTPServer((HOST, PORT), Handler)
    from app.api.config import TLS_ENABLED, TLS_CERT, TLS_KEY
    scheme = 'https' if TLS_ENABLED else 'http'
    if TLS_ENABLED:
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(TLS_CERT, TLS_KEY)
            # Use permissive SSL settings for browser compatibility
            ctx.set_ciphers('DEFAULT:@SECLEVEL=1')  # Allow more cipher suites
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            _done(f'TLS={TLS_CERT}')
        except Exception as e:
            logger.warning('TLS setup failed, falling back to HTTP: %s', e)
            _fail(str(e))
            scheme = 'http'
    else:
        _done()

    # ── Stage 8: Validate state.db schema ──────────────────────────────
    # Check __webui column types before any code that swallows exceptions.
    # If types are wrong, the database is from an incompatible version.
    _stage(8, 'Validating state.db schema')
    try:
        from app.api.webui_schema import check_webui_column_types, IncompatibleSchemaError
        from app.api.models import _get_session_db
        db = _get_session_db()
        if db is not None:
            check_webui_column_types(db)
        _done()
    except IncompatibleSchemaError as e:
        _fail(str(e))
        sys.exit(1)
    except Exception as e:
        _done(f'skipped ({e})')

    # ── Stage 9: Start session displays ──
    _stage(9, 'Starting session displays')
    try:
        from app.api.session_display import ensure_pinned_displays, sync_gateway_session_cwd
        sync_gateway_session_cwd()
        ensure_pinned_displays()
        _done()
    except BaseException as e:
        _fail(f'{type(e).__name__}: {e}')

    # ── Pre-warm imports (sync) + expensive caches (background) ──
    _prewarm()

    # ── Signal handlers ──
    # httpd.shutdown() must be called from a different thread than serve_forever()
    # to avoid deadlock (shutdown() waits for serve_forever() to return).
    import threading
    def _shutdown_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info('Received %s, shutting down gracefully...', sig_name)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    # ── Startup complete ──
    total_time = round(time.monotonic() - _t0, 2)
    logger.info('Listening: %s://%s:%s', scheme, HOST, PORT)
    logger.info('Startup:   %ss', total_time)
    logger.info('Log level: %s', _log_level)

    try:
        httpd.serve_forever()
    finally:
        logger.info('Shutting down...')

        # 1. Cancel all active agent streams (parallel)
        try:
            from app.api.streaming import AGENT_INSTANCES, cancel_stream
            import concurrent.futures
            active = list(AGENT_INSTANCES.keys())
            if active:
                logger.info('Cancelling %d active stream(s)...', len(active))
                with concurrent.futures.ThreadPoolExecutor(max_workers=len(active)) as pool:
                    list(pool.map(cancel_stream, active))
        except Exception:
            pass

        # 2. Stop SessionDisplay processes
        try:
            from app.api.session_display import _DISPLAYS
            displays = list(_DISPLAYS.items())
            if displays:
                logger.info('Stopping %d session display(s)...', len(displays))
                for sid, sd in displays:
                    try:
                        sd.stop(remove_json=False)
                    except Exception:
                        pass
        except Exception:
            pass

        # 3. Stop gateway watcher
        try:
            from app.api.session_watcher import stop_watcher
            stop_watcher()
        except Exception:
            pass

        # 4. Shutdown MCP server connections
        try:
            from tools.mcp_tool import shutdown_mcp_servers
            shutdown_mcp_servers()
        except Exception:
            pass

        # 5. Clear agent cache
        try:
            from app.api.streaming import SESSION_AGENT_CACHE, SESSION_AGENT_LOCKS
            SESSION_AGENT_CACHE.clear()
            SESSION_AGENT_LOCKS.clear()
        except Exception:
            pass

        # 6. Close SessionDB connection
        try:
            from app.api.models import _close_session_db
            _close_session_db()
        except Exception:
            pass

        logger.info('Shutdown complete.')


if __name__ == '__main__':
    main()
