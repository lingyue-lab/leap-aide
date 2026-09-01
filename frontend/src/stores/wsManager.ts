/**
 * WebSocket event manager for unified streaming.
 *
 * Replaces SSE connections (notify + replay) with a single WebSocket.
 * Auto-reconnects with exponential backoff.
 */

type NotifyHandler = (event: string, data: any) => void;
type ReplayHandler = (sessionId: string, event: string, data: any) => void;

interface WsMessage {
  channel?: string;
  type?: string;
  event?: string;
  session_id?: string;
  data?: any;
}

const WS_URL = `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/api/ws/events`;
const RECONNECT_DELAYS = [3000, 6000, 12000, 30000]; // Exponential backoff
const MAX_RECONNECT_ATTEMPTS = 10;
const HEARTBEAT_INTERVAL = 30000; // 30s
const MESSAGE_MAX_SIZE = 65536; // 64KB

class WsManager {
  private ws: WebSocket | null = null;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setInterval> | null = null;
  private lastMessageTime = 0;

  // Subscriptions
  private notifyListeners: Set<NotifyHandler> = new Set();
  private replayListeners: Map<string, Set<ReplayHandler>> = new Map();
  private subscriptions: Set<string> = new Set(); // Active replay session IDs

  // State
  private _connected = false;
  private _enabled = false;

  get connected(): boolean {
    return this._connected;
  }

  /**
   * Check if WebSocket is supported in this browser.
   */
  static isSupported(): boolean {
    return typeof WebSocket !== 'undefined' && WebSocket.CLOSING === 2;
  }

  /**
   * Instance method wrapper for static isSupported().
   * Convenience for checking support on the singleton instance.
   */
  checkSupported(): boolean {
    return WsManager.isSupported();
  }

  /**
   * Enable and connect the WebSocket.
   */
  connect(): void {
    if (!WsManager.isSupported()) {
      console.warn('[WsManager] WebSocket not supported, falling back to SSE');
      return;
    }

    this._enabled = true;
    this._doConnect();
  }

  /**
   * Disable and disconnect the WebSocket.
   */
  disconnect(): void {
    this._enabled = false;
    this._cleanup();
  }

  /**
   * Subscribe to global notify events.
   * Returns an unsubscribe function.
   */
  addNotifyListener(fn: NotifyHandler): () => void {
    this.notifyListeners.add(fn);
    return () => this.notifyListeners.delete(fn);
  }

  /**
   * Subscribe to replay events for a specific session.
   * Returns an unsubscribe function.
   */
  addReplayListener(sessionId: string, fn: ReplayHandler): () => void {
    if (!this.replayListeners.has(sessionId)) {
      this.replayListeners.set(sessionId, new Set());
    }
    this.replayListeners.get(sessionId)!.add(fn);
    return () => {
      const listeners = this.replayListeners.get(sessionId);
      if (listeners) {
        listeners.delete(fn);
        if (listeners.size === 0) {
          this.replayListeners.delete(sessionId);
        }
      }
    };
  }

  /**
   * Subscribe to replay events for a session.
   * Sends subscribe_replay message to server.
   */
  subscribeReplay(sessionId: string): void {
    if (this.subscriptions.has(sessionId)) {
      return; // Already subscribed
    }
    this.subscriptions.add(sessionId);
    this._send({
      type: 'subscribe_replay',
      session_id: sessionId,
    });
  }

  /**
   * Unsubscribe from replay events for a session.
   * Sends unsubscribe_replay message to server.
   */
  unsubscribeReplay(sessionId: string): void {
    if (!this.subscriptions.has(sessionId)) {
      return;
    }
    this.subscriptions.delete(sessionId);
    this._send({
      type: 'unsubscribe_replay',
      session_id: sessionId,
    });
  }

  /**
   * Connect to a session's replay stream (subscribe + listen).
   * Returns an unsubscribe function.
   */
  connectSession(sessionId: string, handler: ReplayHandler): () => void {
    const unsub = this.addReplayListener(sessionId, handler);
    this.subscribeReplay(sessionId);
    return () => {
      unsub();
      // Note: We don't auto-unsubscribe replay because other listeners may exist
    };
  }

  /**
   * Disconnect from a session's replay stream (unsubscribe + remove listeners).
   */
  disconnectSession(sessionId: string): void {
    this.replayListeners.delete(sessionId);
    this.unsubscribeReplay(sessionId);
  }

  // ── Private methods ──────────────────────────────────────────────────────

  private _doConnect(): void {
    if (this.ws) {
      this.ws.close();
    }

    try {
      this.ws = new WebSocket(WS_URL);
    } catch (e) {
      console.error('[WsManager] Failed to create WebSocket:', e);
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log('[WsManager] Connected');
      this._connected = true;
      this.reconnectAttempts = 0;
      this._startHeartbeat();
      this._resubscribeAll();
    };

    this.ws.onmessage = (event) => {
      this.lastMessageTime = Date.now();
      this._handleMessage(event.data);
    };

    this.ws.onclose = (event) => {
      console.log('[WsManager] Disconnected:', event.code, event.reason);
      this._connected = false;
      this._stopHeartbeat();

      if (this._enabled && event.code !== 1000) {
        this._scheduleReconnect();
      }
    };

    this.ws.onerror = (event) => {
      console.error('[WsManager] Error:', event);
    };
  }

  private _handleMessage(data: string): void {
    try {
      const msg: WsMessage = JSON.parse(data);

      // Handle pong response
      if (msg.type === 'pong') {
        return;
      }

      // Route by channel
      if (msg.channel === 'notify') {
        this._dispatchNotify(msg.event!, msg.data);
      } else if (msg.channel === 'replay') {
        this._dispatchReplay(msg.session_id!, msg.event!, msg.data);
      }
    } catch (e) {
      console.error('[WsManager] Failed to parse message:', e);
    }
  }

  private _dispatchNotify(event: string, data: any): void {
    for (const fn of this.notifyListeners) {
      try {
        fn(event, data);
      } catch (e) {
        console.error('[WsManager] Notify listener error:', e);
      }
    }
  }

  private _dispatchReplay(sessionId: string, event: string, data: any): void {
    const listeners = this.replayListeners.get(sessionId);
    if (listeners) {
      for (const fn of listeners) {
        try {
          fn(sessionId, event, data);
        } catch (e) {
          console.error('[WsManager] Replay listener error:', e);
        }
      }
    }
  }

  private _send(msg: object): boolean {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return false;
    }
    const json = JSON.stringify(msg);
    if (json.length > MESSAGE_MAX_SIZE) {
      console.error('[WsManager] Message too large:', json.length);
      return false;
    }
    this.ws.send(json);
    return true;
  }

  private _resubscribeAll(): void {
    // Resubscribe all active replay sessions
    for (const sessionId of this.subscriptions) {
      this._send({
        type: 'subscribe_replay',
        session_id: sessionId,
      });
    }
  }

  private _scheduleReconnect(): void {
    if (this.reconnectAttempts >= MAX_RECONNECT_ATTEMPTS) {
      console.error('[WsManager] Max reconnect attempts reached');
      return;
    }

    const delay = RECONNECT_DELAYS[
      Math.min(this.reconnectAttempts, RECONNECT_DELAYS.length - 1)
    ];
    this.reconnectAttempts++;

    console.log(`[WsManager] Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
    this.reconnectTimer = setTimeout(() => this._doConnect(), delay);
  }

  private _startHeartbeat(): void {
    this._stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      // Check if we've received any message recently
      const now = Date.now();
      if (now - this.lastMessageTime > HEARTBEAT_INTERVAL * 2) {
        console.warn('[WsManager] No messages received, connection may be stale');
        this.ws?.close();
        return;
      }
      // Send ping
      this._send({ type: 'ping' });
    }, HEARTBEAT_INTERVAL);
  }

  private _stopHeartbeat(): void {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  private _cleanup(): void {
    this._stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close(1000, 'Client disconnect');
      this.ws = null;
    }
    this._connected = false;
  }
}

// Singleton instance
export const wsManager = new WsManager();

// Export types for consumers
export type { NotifyHandler, ReplayHandler };
