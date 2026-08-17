import { useEffect, useRef, useState, useCallback } from 'react';
import { useParams } from 'react-router-dom';
import { observerService } from '@/services/api';

class VncClient {
  canvas: HTMLCanvasElement;
  ctx: CanvasRenderingContext2D;
  sessionId: string;
  private fb: ImageData | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lastHash = '';
  width = 0;
  height = 0;

  constructor(canvas: HTMLCanvasElement, sessionId: string) {
    this.canvas = canvas;
    this.sessionId = sessionId;
    this.ctx = canvas.getContext('2d')!;
  }

  start() {
    this._poll();
    this.timer = setInterval(() => this._poll(), 500);
  }

  private async _poll() {
    try {
      const resp = await fetch(`/observations/${encodeURIComponent(this.sessionId)}/vnc`);
      if (!resp.ok) return;
      const buf = await resp.arrayBuffer();
      const raw = new Uint8Array(buf);
      if (raw.length < 4) return;

      // Skip duplicate frames (simple hash of first 64 bytes)
      const hash = Array.from(raw.subarray(0, Math.min(64, raw.length))).join(',');
      if (hash === this.lastHash) return;
      this.lastHash = hash;

      // Parse: 4-byte header (w u16, h u16) + RFB FramebufferUpdate
      const w = (raw[0] << 8) | raw[1];
      const h = (raw[2] << 8) | raw[3];

      if (w !== this.width || h !== this.height) {
        this.width = w; this.height = h;
        this.canvas.width = w; this.canvas.height = h;
        this.fb = this.ctx.createImageData(w, h);
      }
      if (!this.fb || raw.length <= 4) return;

      this._decode(raw.subarray(4), w, h);
    } catch (_) { /* ignore */ }
  }

  private _decode(rfb: Uint8Array, cw: number, _ch: number) {
    if (rfb.length < 4) return;
    // msg[0]=type(0=FramebufferUpdate), msg[1]=padding, msg[2-3]=numRects
    const numRects = (rfb[2] << 8) | rfb[3];
    let off = 4;

    for (let i = 0; i < numRects; i++) {
      if (off + 12 > rfb.length) return;
      const x = (rfb[off] << 8) | rfb[off + 1];
      const y = (rfb[off + 2] << 8) | rfb[off + 3];
      const rw = (rfb[off + 4] << 8) | rfb[off + 5];
      const rh = (rfb[off + 6] << 8) | rfb[off + 7];
      const enc = ((rfb[off + 8] << 24) | (rfb[off + 9] << 16) |
                   (rfb[off + 10] << 8) | rfb[off + 11]) >>> 0;
      off += 12;

      if (enc === 0 && this.fb) {
        const len = rw * rh * 4;
        if (off + len > rfb.length) return;
        const fbD = this.fb.data;
        for (let row = 0; row < rh; row++) {
          fbD.set(rfb.subarray(off + row * rw * 4, off + (row + 1) * rw * 4),
                  ((y + row) * cw + x) * 4);
        }
        this.ctx.putImageData(this.fb, 0, 0);
        off += len;
      } else if (enc === 0xFFFFFF21) {
        // DesktopSize — handled by w/h change above
      }
    }
  }

  stop() {
    if (this.timer) { clearInterval(this.timer); this.timer = null; }
  }
}

// ── React Component ─────────────────────────────────────────────────────────

export default function ObserverPage() {
  const { sessionId } = useParams<{ sessionId: string }>();
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const clientRef = useRef<VncClient | null>(null);
  const [status, setStatus] = useState<'loading' | 'connecting' | 'connected' | 'error'>('loading');
  const [statusText, setStatusText] = useState('Fetching session info...');
  const [showRetry, setShowRetry] = useState(false);

  const doConnect = useCallback(async () => {
    if (!sessionId || !canvasRef.current) return;
    setShowRetry(false);
    setStatus('loading'); setStatusText('Fetching session info...');
    try {
      const info = await observerService.getInfo(sessionId);
      if (!info.ok || !info.running) {
        setStatus('error'); setStatusText(info.error || 'Not available'); setShowRetry(true); return;
      }
      setStatus('connecting'); setStatusText(`Connecting to VNC (port ${info.vnc_port})...`);
      clientRef.current?.stop();
      const c = new VncClient(canvasRef.current, sessionId);
      clientRef.current = c;
      c.start();
      setStatus('connected');
    } catch (err: any) {
      setStatus('error'); setStatusText(err.message || 'Failed'); setShowRetry(true);
    }
  }, [sessionId]);

  useEffect(() => { doConnect(); return () => clientRef.current?.stop(); }, [doConnect]);

  const conn = status === 'connected';
  return (
    <div style={{ width: '100vw', height: '100vh', background: '#0d1117', position: 'relative', overflow: 'hidden' }}>
      {!conn && (
        <div style={{ position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', zIndex: 10, background: '#0d1117' }}>
          {(status === 'loading' || status === 'connecting') && (
            <div style={{ width: 40, height: 40, border: '3px solid #30363d', borderTopColor: '#58a6ff', borderRadius: '50%', animation: 'spin 0.8s linear infinite', marginBottom: 16 }} />
          )}
          <p style={{ fontSize: 16, color: '#8b949e', marginBottom: 12 }}>{statusText}</p>
          {showRetry && <button onClick={doConnect} style={{ padding: '8px 20px', background: '#238636', color: '#fff', border: '1px solid #2ea043', borderRadius: 6, cursor: 'pointer', fontSize: 16 }}>Retry</button>}
        </div>
      )}
      <canvas ref={canvasRef} style={{ width: '100%', height: '100%', display: conn ? 'block' : 'none' }} />
    </div>
  );
}
