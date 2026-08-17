import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { useTheme } from '@/contexts/ThemeContext';
import { useSettingsStore } from '@/stores/useSettingsStore';
import { FONT_ZOOM_MAP } from '@/constants';
import '@xterm/xterm/css/xterm.css';

// ── Font size mapping (same scale as the global --app-zoom) ──────────────────
const BASE_TERM_FONT_SIZE = 15;
const FONT_SIZE_PX: Record<string, number> = {
  small: Math.round(BASE_TERM_FONT_SIZE * parseFloat(FONT_ZOOM_MAP.small)),
  medium: Math.round(BASE_TERM_FONT_SIZE * parseFloat(FONT_ZOOM_MAP.medium)),
  large: Math.round(BASE_TERM_FONT_SIZE * parseFloat(FONT_ZOOM_MAP.large)),
};

// ── Terminal color palettes ─────────────────────────────────────────────────
// Derived from the M3 palette (seed #4176E6). background/foreground/red/blue/
// magenta map to M3 surface/onSurface/error/primary/tertiary; green/yellow are
// the deepseek-harness official semantic colors (success #22C55E / warn #F59E0B).
const TERM_THEME_DARK = {
  background:   '#121316', // surface
  foreground:   '#E4E2E6', // onSurface
  cursor:       '#B1C5FF', // primary
  cursorAccent: '#002C71', // onPrimary
  selectionBackground: 'rgba(177, 197, 255, 0.22)',
  black:        '#121316',
  red:          '#FFB4AB', // error
  green:        '#22C55E', // success (green-500)
  yellow:       '#F59E0B', // warn (amber-500)
  blue:         '#B1C5FF', // primary
  magenta:      '#E0BBDD', // tertiary
  cyan:         '#9DD6E4', // cyan ext
  white:        '#E4E2E6',
  brightBlack:  '#5A5A63',
  brightRed:    '#FFDAD6', // errorContainer
  brightGreen:  '#4ED17E', // green-400
  brightYellow: '#F7AD31', // amber-400
  brightBlue:   '#DAE2FF', // onPrimaryContainer
  brightMagenta:'#FED7FA', // tertiaryContainer
  brightCyan:   '#C6ECF5',
  brightWhite:  '#FFFFFF',
};

// M3 light theme derived from seed #4176E6.
const TERM_THEME_LIGHT = {
  background:   '#FBF8FD', // surface
  foreground:   '#1B1B1F', // onSurface
  cursor:       '#1758C7', // primary
  cursorAccent: '#FFFFFF', // onPrimary
  selectionBackground: 'rgba(23, 88, 199, 0.15)',
  black:        '#1B1B1F',
  red:          '#BA1A1A', // error
  green:        '#22C55E', // success (green-500)
  yellow:       '#F59E0B', // warn (amber-500)
  blue:         '#1758C7', // primary
  magenta:      '#735572', // tertiary
  cyan:         '#006B78', // cyan ext
  white:        '#1B1B1F',
  brightBlack:  '#44464F',
  brightRed:    '#C62828',
  brightGreen:  '#4ED17E', // green-400
  brightYellow: '#F7AD31', // amber-400
  brightBlue:   '#4176E6', // seed
  brightMagenta:'#9A4E86',
  brightCyan:   '#00879A',
  brightWhite:  '#E4E2E6',
};

interface TerminalViewProps {
  /** Unique ID for this terminal instance. Changing the id remounts the terminal. */
  terminalId: string;
}

/**
 * TerminalView – xterm.js terminal connected to the backend PTY via WebSocket.
 *
 * Each instance maintains its own WebSocket connection to /api/ws/terminal,
 * giving each terminal a separate bash process on the server.
 *
 * Protocol:
 *   Client → Server (JSON text frames):
 *     { type: "input",  data: "<string>" }
 *     { type: "resize", cols: N, rows: N }
 *   Server → Client (JSON text frames):
 *     { type: "output", data: "<base64>" }
 *     { type: "error",  message: "<string>" }
 */
export default function TerminalView({ terminalId }: TerminalViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const { effectiveTheme } = useTheme();
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const fontSize = useSettingsStore(s => s.settings?.font_size ?? 'medium');

  // ── Update xterm theme when effective theme changes ───────────────────────
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    term.options.theme = effectiveTheme === 'light' ? TERM_THEME_LIGHT : TERM_THEME_DARK;
  }, [effectiveTheme]);

  // ── Update xterm font size when user changes the font_size setting ────────
  useEffect(() => {
    const term = termRef.current;
    const fitAddon = fitAddonRef.current;
    if (!term || !fitAddon) return;
    term.options.fontSize = FONT_SIZE_PX[fontSize] ?? BASE_TERM_FONT_SIZE;
    try { fitAddon.fit(); } catch { /* ignore */ }
  }, [fontSize]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let destroyed = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let ws: WebSocket | null = null;

    // ── xterm.js setup ────────────────────────────────────────────────────────
    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: 'block',
      fontSize: FONT_SIZE_PX[fontSize] ?? BASE_TERM_FONT_SIZE,
      lineHeight: 1.2,
      fontFamily: '"MesloLGS NF", "Menlo", "Monaco", "Cascadia Code", "Fira Code", "DejaVu Sans Mono", monospace',
      letterSpacing: 0,
      theme: effectiveTheme === 'light' ? TERM_THEME_LIGHT : TERM_THEME_DARK,
    });
    termRef.current = term;

    const fitAddon = new FitAddon();
    fitAddonRef.current = fitAddon;
    term.loadAddon(fitAddon);
    term.open(container);

    // Defer fit() so the browser has finished layout before xterm reads dimensions.
    let rafId = requestAnimationFrame(() => {
      if (!destroyed) {
        try { fitAddon.fit(); } catch { /* ignore */ }
      }
    });

    // ── WebSocket ─────────────────────────────────────────────────────────────
    const buildUrl = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${proto}//${window.location.host}/api/ws/terminal`;
    };

    const connect = () => {
      if (destroyed) return;
      ws = new WebSocket(buildUrl());

      ws.onopen = () => {
        if (destroyed) { ws?.close(); return; }
        ws!.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
      };

      ws.onmessage = (event: MessageEvent) => {
        if (destroyed) return;
        try {
          const msg = JSON.parse(event.data as string) as { type: string; data?: string; message?: string };
          if (msg.type === 'output' && typeof msg.data === 'string') {
            const raw = atob(msg.data);
            const bytes = new Uint8Array(raw.length);
            for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
            term.write(bytes);
          } else if (msg.type === 'error') {
            term.writeln(`\r\n\x1b[31m[Terminal error] ${msg.message ?? ''}\x1b[0m\r\n`);
          }
        } catch { /* ignore malformed frames */ }
      };

      ws.onclose = () => {
        if (destroyed) return;
        term.writeln('\r\n\x1b[33m[Connection closed – reconnecting in 3 s…]\x1b[0m');
        reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        if (destroyed) return;
        term.writeln('\r\n\x1b[31m[WebSocket error]\x1b[0m');
      };
    };

    connect();

    const onData = term.onData((data: string) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });

    const onResize = term.onResize(({ cols, rows }: { cols: number; rows: number }) => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    // Re-fit when container changes size.
    const observer = new ResizeObserver(() => {
      if (!destroyed) {
        try { fitAddon.fit(); } catch { /* ignore */ }
      }
    });
    observer.observe(container);

    // ── Cleanup ───────────────────────────────────────────────────────────────
    return () => {
      destroyed = true;
      cancelAnimationFrame(rafId);
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      observer.disconnect();
      onData.dispose();
      onResize.dispose();
      if (ws) {
        ws.onclose = null;
        ws.close();
        ws = null;
      }
      term.dispose();
    };
  }, [terminalId]); // remount when terminalId changes

  return (
    <div className="terminal-view" ref={containerRef} />
  );
}
