import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import RFB from '@novnc/novnc';
import { Icon } from '@/components/common/Icons';
import {
  getBrowserDesktopStatus,
  useBrowserDesktopStore,
} from '@/stores/useBrowserDesktopStore';

type VncStatus = 'connecting' | 'connected' | 'disconnected';

/**
 * Full-area noVNC view of the shared browser desktop (issue #37).
 *
 * Mounted once inside a keep-mounted `<main>` in MainContent (mirroring the
 * terminal area) so the RFB WebSocket survives sidebar tab switches —
 * switching to chat and back never tears the VNC session down.
 *
 * Connects to the fixed backend route `/api/ws/vnc` (no port, no sid):
 * the backend resolves the target from HERMES_VNC_PORT (default 5900).
 * Reconnection is driven by the store's `reconnectToken`, bumped by the
 * reconnect button.
 *
 * This view owns the status polling and the status/reconnect controls: there
 * is only one shared desktop, so it needs no supporting sidebar panel. The
 * compact control pill lives in the view's top-right corner (M3 icon button
 * + badge surface), and the nav rail reads the same store for its alert dot.
 */
export default function BrowserDesktopView() {
  const { t } = useTranslation('common');
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);
  const [status, setStatus] = useState<VncStatus>('connecting');
  const reconnectToken = useBrowserDesktopStore((s) => s.reconnectToken);
  const info = useBrowserDesktopStore((s) => s.info);
  const load = useBrowserDesktopStore((s) => s.load);
  const reconnect = useBrowserDesktopStore((s) => s.reconnect);

  const desktopStatus = getBrowserDesktopStatus(info);
  const desktopStatusLabel = t(`browser_desktop.status_${desktopStatus}`);
  const desktopDetail = info?.error || info?.receptionist_url;

  // Poll so both the in-view chip and the nav-rail alert dot stay current.
  useEffect(() => {
    void load();
    const timer = setInterval(() => { void load(); }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    setStatus('connecting');
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/api/ws/vnc`;

    const rfb = new RFB(container, url);
    // noVNC's screen wrapper defaults to rgb(40, 40, 40); match the black
    // framebuffer so letterboxed regions share one surface with the canvas.
    rfb.background = '#000000';
    rfb.scaleViewport = true;
    rfb.resizeSession = false;
    rfbRef.current = rfb;

    const onConnect = () => setStatus('connected');
    const onDisconnect = () => setStatus('disconnected');
    // The shared desktop's x11vnc runs without a password; if the server
    // ever asks, answer with empty credentials rather than hanging.
    const onCredentialsRequired = () => {
      rfb.credentials = { username: '', password: '' };
    };
    rfb.addEventListener('connect', onConnect);
    rfb.addEventListener('disconnect', onDisconnect);
    rfb.addEventListener('credentialsrequired', onCredentialsRequired);

    return () => {
      rfb.removeEventListener('connect', onConnect);
      rfb.removeEventListener('disconnect', onDisconnect);
      rfb.removeEventListener('credentialsrequired', onCredentialsRequired);
      rfb.disconnect();
      rfbRef.current = null;
      // noVNC appends its canvas to the container; clear it so a
      // reconnect doesn't stack a second canvas.
      container.innerHTML = '';
    };
  }, [reconnectToken]);

  return (
    <div className="browser-desktop-view">
      <div className="browser-desktop-novnc" ref={containerRef} />

      <div className="browser-desktop-controls">
        <div
          className={`browser-desktop-status-chip status-${desktopStatus}`}
          title={desktopDetail || undefined}
          role="status"
          aria-live="polite"
        >
          <span className="browser-desktop-status-dot" aria-hidden="true" />
          {desktopStatusLabel}
        </div>
        <button
          className="icon-btn browser-desktop-reconnect"
          onClick={() => reconnect()}
          title={t('browser_desktop.reconnect')}
          aria-label={t('browser_desktop.reconnect')}
        >
          <Icon name="refresh" size={18} />
        </button>
      </div>

      {info?.error && (
        <div className="browser-desktop-error" role="alert">{info.error}</div>
      )}

      {status !== 'connected' && (
        <div className="browser-desktop-status-overlay" role="status">
          {status === 'connecting'
            ? t('browser_desktop.status_connecting')
            : t('browser_desktop.status_disconnected')}
        </div>
      )}
    </div>
  );
}
