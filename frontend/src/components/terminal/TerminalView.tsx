import { useEffect, useRef } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import '@xterm/xterm/css/xterm.css';

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
      fontSize: 13,
      lineHeight: 1.2,
      // iTerm2 uses "Monaco" → "Menlo" → system mono as fallback chain.
      // "MesloLGS NF" / "Menlo" are the most iTerm2-like freely available fonts.
      fontFamily: '"MesloLGS NF", "Menlo", "Monaco", "Cascadia Code", "Fira Code", "DejaVu Sans Mono", monospace',
      letterSpacing: 0,
      // iTerm2 default profile: Solarized Dark
      theme: {
        background:   '#000000',
        foreground:   '#d8d8d8',
        cursor:       '#e0e0e0',
        cursorAccent: '#000000',
        selectionBackground: 'rgba(255, 255, 255, 0.25)',
        black:        '#073642',   // base02
        red:          '#dc322f',
        green:        '#859900',
        yellow:       '#b58900',
        blue:         '#268bd2',
        magenta:      '#d33682',
        cyan:         '#2aa198',
        white:        '#eee8d5',   // base2
        brightBlack:  '#444444',
        brightRed:    '#cb4b16',
        brightGreen:  '#586e75',   // base01
        brightYellow: '#657b83',   // base00
        brightBlue:   '#839496',   // base0
        brightMagenta:'#6c71c4',
        brightCyan:   '#93a1a1',   // base1
        brightWhite:  '#fdf6e3',   // base3
      },
    });

    const fitAddon = new FitAddon();
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
