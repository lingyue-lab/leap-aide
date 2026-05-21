import { useEffect, useRef, useState } from 'react';
import { useVncStore, maybeSavePassword } from '@/stores/useVncStore';

// @novnc/novnc 1.7.0 — npm install @novnc/novnc
// The package has no "exports" map; vite.config.ts maps this import
// directly to the physical file via resolve.alias.
//
// Requires a secure context (HTTPS/WSS).
// Enable TLS: HERMES_WEBUI_TLS_CERT=<path>.crt HERMES_WEBUI_TLS_KEY=<path>.key

/* eslint-disable @typescript-eslint/ban-ts-comment */
// @ts-ignore – no bundled type declarations
import RFBRaw from '@novnc/novnc/core/rfb.js';

interface RFBOptions {
  credentials?: { password?: string };
}

interface RFBInstance {
  scaleViewport: boolean;
  resizeSession: boolean;
  clipViewport: boolean;
  disconnect(): void;
  sendCredentials(creds: { password: string }): void;
  addEventListener(
    event: 'connect' | 'disconnect' | 'credentialsrequired' | 'securityfailure'
         | 'clipboard' | 'bell' | 'desktopname' | 'capabilities' | 'serveridentity',
    handler: (ev: CustomEvent) => void
  ): void;
}

type RFBClass = new (
  target: HTMLElement,
  url: string,
  options?: RFBOptions
) => RFBInstance;

const RFB = RFBRaw as unknown as RFBClass;

// Visual states of the VNC view
type VncViewState = 'idle' | 'connecting' | 'connected';

export default function VncView() {
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFBInstance | null>(null);


  const config = useVncStore((s) => s.config);
  const setConnected = useVncStore((s) => s.setConnected);
  const setConfig = useVncStore((s) => s.setConfig);
  const registerDisconnect = useVncStore((s) => s.registerDisconnect);
  const rememberPassword = useVncStore((s) => s.rememberPassword);
  const setRememberPassword = useVncStore((s) => s.setRememberPassword);

  // Keep a ref to the latest committed password so event handlers always see the current value.
  const passwordRef = useRef(config.password);
  useEffect(() => { passwordRef.current = config.password; }, [config.password]);

  // Local draft password — only committed to store when user clicks Connect.
  const [draftPassword, setDraftPassword] = useState(config.password);
  // Local draft for remember-password checkbox.
  const [draftRemember, setDraftRemember] = useState(rememberPassword);
  // Visual state machine.
  const [viewState, setViewState] = useState<VncViewState>('idle');
  // Error message shown in the connect card.
  const [connectError, setConnectError] = useState<string | null>(null);

  const buildWsUrl = () => {
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    return `${proto}://${location.host}/api/ws/vnc`;
  };

  const doDisconnect = () => {
    if (rfbRef.current) {
      try { rfbRef.current.disconnect(); } catch (_) { /* ignore */ }
      rfbRef.current = null;
    }
    setConnected(false);
    setViewState('idle');
  };

  // Register doDisconnect into the store so the sidebar can call it.
  // We use a ref-based stable wrapper to avoid re-registering on every render.
  const doDisconnectRef = useRef(doDisconnect);
  doDisconnectRef.current = doDisconnect;
  useEffect(() => {
    registerDisconnect(() => doDisconnectRef.current());
    return () => registerDisconnect(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Separate trigger counter so the RFB effect dependency is NOT `viewState`.
  // This prevents effect cleanup (which calls rfb.disconnect) from firing when
  // viewState transitions from 'connecting' → 'connected', which would kill the
  // live session immediately after it was established.
  const [connectTrigger, setConnectTrigger] = useState(0);
  const startConnection = () => setConnectTrigger((n) => n + 1);

  const handleConnect = () => {
    setConnectError(null);
    setConfig({ password: draftPassword });
    setRememberPassword(draftRemember);
    passwordRef.current = draftPassword;
    maybeSavePassword(draftPassword, draftRemember);
    doDisconnect();
    setViewState('connecting');
    startConnection();
  };

  const handleCancel = () => {
    doDisconnect();
  };

  useEffect(() => {
    if (connectTrigger === 0) return;          // initial render — do nothing
    if (!containerRef.current) return;

    // Guard flag — prevents stale callbacks from touching state after cleanup.
    let cancelled = false;

    const container = containerRef.current;
    const wsUrl = buildWsUrl();

    let rfb: RFBInstance;
    try {
      rfb = new RFB(container, wsUrl, {
        credentials: passwordRef.current ? { password: passwordRef.current } : undefined,
      });
    } catch (_) {
      if (!cancelled) setViewState('idle');
      return;
    }

    rfb.scaleViewport = true;
    rfb.resizeSession = true;
    rfb.clipViewport = false;

    rfb.addEventListener('connect', () => {
      if (cancelled) return;
      setConnected(true);
      setViewState('connected');
    });

    rfb.addEventListener('disconnect', () => {
      if (cancelled) return;
      setConnected(false);
      setViewState((prev) => (prev === 'connected' || prev === 'connecting' ? 'idle' : prev));
    });

    rfb.addEventListener('credentialsrequired', () => {
      if (cancelled) return;
      try { rfb.disconnect(); } catch (_) { /* ignore */ }
      rfbRef.current = null;
      setConnected(false);
      setConnectError('Password required — please enter the VNC password and try again.');
      setViewState('idle');
    });

    rfb.addEventListener('securityfailure', (ev: CustomEvent) => {
      if (cancelled) return;
      try { rfb.disconnect(); } catch (_) { /* ignore */ }
      rfbRef.current = null;
      setConnected(false);
      setConnectError(`Security failure: ${ev.detail?.reason ?? 'wrong password or auth rejected'}`);
      setViewState('idle');
    });

    rfbRef.current = rfb;

    return () => {
      cancelled = true;
      // Only disconnect here if we are truly being torn down (component unmount
      // or StrictMode re-mount). We do NOT update React state to avoid fighting
      // the upcoming re-mount.
      if (rfbRef.current) {
        try { rfbRef.current.disconnect(); } catch (_) { /* ignore */ }
        rfbRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connectTrigger]);


  return (
    <div className="vnc-view-wrapper">
      {/* ── Canvas — always in the DOM so noVNC has a mount target ──────────── */}
      <div
        className="vnc-canvas-container"
        ref={containerRef}
        style={{ display: viewState === 'connected' ? undefined : 'none' }}
      />

      {/* ── Idle: centered connect card ──────────────────────────────────────── */}
      {viewState === 'idle' && (
        <div className="vnc-center-area">
          <div className="vnc-connect-card">
            {/* Monitor icon */}
            <div className="vnc-connect-card-icon">
              <svg
                width="48"
                height="48"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>
            </div>

            <h3 className="vnc-connect-card-title">Connect to VNC</h3>
            <p className="vnc-connect-card-desc">localhost:5901 · via backend proxy</p>

            {/* Error message */}
            {connectError && (
              <div className="vnc-connect-card-error">{connectError}</div>
            )}

            {/* Password field */}
            <div className="vnc-connect-card-field">
              <label className="vnc-connect-card-label" htmlFor="vnc-password">
                Password
              </label>
              <input
                id="vnc-password"
                type="password"
                className={`vnc-connect-card-input${connectError ? ' error' : ''}`}
                placeholder="VNC password (leave blank if none)"
                value={draftPassword}
                onChange={(e) => { setDraftPassword(e.target.value); setConnectError(null); }}
                onKeyDown={(e) => { if (e.key === 'Enter') handleConnect(); }}
                autoComplete="current-password"
                // eslint-disable-next-line jsx-a11y/no-autofocus
                autoFocus={!!connectError}
              />
            </div>

            {/* Remember password checkbox */}
            <label className="vnc-remember-label">
              <input
                type="checkbox"
                className="vnc-remember-checkbox"
                checked={draftRemember}
                onChange={(e) => setDraftRemember(e.target.checked)}
              />
              <span>Remember password</span>
            </label>

            {/* Connect button */}
            <button className="vnc-connect-card-btn" onClick={handleConnect}>
              Connect
            </button>
          </div>
        </div>
      )}

      {/* ── Connecting: spinner ───────────────────────────────────────────────── */}
      {viewState === 'connecting' && (
        <div className="vnc-center-area">
          <div className="vnc-connecting-card">
            <div className="vnc-spinner" aria-label="Connecting…" />
            <p className="vnc-connecting-text">Connecting to VNC server…</p>
            <button className="vnc-cancel-btn" onClick={handleCancel}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
