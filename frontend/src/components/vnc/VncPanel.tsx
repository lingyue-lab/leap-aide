import { useVncStore } from '@/stores/useVncStore';

export default function VncPanel() {
  const connected = useVncStore((s) => s.connected);
  const disconnect = useVncStore((s) => s.disconnect);

  return (
    <div className="vnc-panel">
      {/* Header — mirrors WorkspacesPanel header */}
      <div className="flex items-center justify-between p-2 border-b border-[var(--border)]">
        <span className="text-xs font-bold uppercase tracking-wider text-[var(--muted)]">
          VNC
        </span>
      </div>

      {/* Description */}
      <div className="px-2 py-1 text-xs text-[var(--muted)] border-b border-[var(--border)]">
        Remote desktop connections
      </div>

      {/* Connection list — mirrors workspace-list */}
      <div className="workspace-list flex-1 overflow-y-auto p-2">
        <div
          className={`workspace-item p-2 mb-2 rounded-lg border cursor-pointer transition-colors ${
            connected
              ? 'border-transparent bg-[var(--hover-bg)]'
              : 'border-transparent hover:bg-[color-mix(in_srgb,var(--hover-bg)_50%,transparent)]'
          }`}
        >
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-2 flex-1 min-w-0">
              {/* Monitor icon */}
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                className="flex-shrink-0"
                aria-hidden="true"
              >
                <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                <line x1="8" y1="21" x2="16" y2="21" />
                <line x1="12" y1="17" x2="12" y2="21" />
              </svg>

              {/* Name + address */}
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium truncate">localhost:5901</div>
                <div className="text-xs text-[var(--muted)] truncate">via backend proxy</div>
              </div>
            </div>

            {/* Right side: status dot + disconnect button */}
            <div className="flex items-center gap-1 flex-shrink-0 ml-1">
              {/* Status dot */}
              <span
                className={`vnc-status-dot-inline ${connected ? 'connected' : ''}`}
                title={connected ? 'Connected' : 'Disconnected'}
              />

              {/* Disconnect button — only shown when connected */}
              {connected && (
                <button
                  className="session-actions-trigger"
                  style={{ opacity: 1, width: 22, height: 22 }}
                  title="Disconnect"
                  onClick={(e) => {
                    e.stopPropagation();
                    disconnect();
                  }}
                >
                  {/* Power-off / unplug icon */}
                  <svg
                    width="13"
                    height="13"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
                    <line x1="12" y1="2" x2="12" y2="12" />
                  </svg>
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
