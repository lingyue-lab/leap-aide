import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import CustomScrollbar from '@/components/common/CustomScrollbar';
import LoadingSpinner from '@/components/common/LoadingSpinner';

interface InfoCardAction {
  label: string;
  primary?: boolean;
  onClick: () => Promise<void> | void;
}

interface InfoCardOptions {
  loading?: boolean;
  noDismiss?: boolean;
  onDismiss?: () => void;
  /** Custom action buttons. Each button manages its own loading state. */
  actions?: InfoCardAction[];
}

interface CardActionState {
  label: string;
  primary: boolean;
  loading: boolean;
  handler: () => Promise<void> | void;
}

interface CardState {
  content: string;
  loading: boolean;
  noDismiss: boolean;
  onDismiss: (() => void) | null;
  actions: CardActionState[];
}

// Per-session Map for card state storage
const _cards = new Map<string, CardState>();

// Current session ID — set by ChatInterface on session change
let _currentSessionId: string | null = null;

// Visible-change callbacks — registered by ChatComposer, ChatMessages, etc.
const _onVisibleChangeCallbacks = new Set<(visible: boolean) => void>();

function _notifyVisible(visible: boolean) {
  _onVisibleChangeCallbacks.forEach(cb => cb(visible));
}

// Internal setters — set by the component on mount
let _renderCard: ((state: CardState | null) => void) | null = null;
let _renderVisible: ((v: boolean) => void) | null = null;

/** Show an informational card for a specific session.
 *  If sessionId ≠ current session, stores but does not display.
 *  Options:
 *    loading   — show a spinner instead of content
 *    noDismiss — hide the X close button (used for non-interruptible actions)
 *    onDismiss — called when user clicks close
 *    actions   — custom action buttons; each manages its own loading state */
export function showInfoCard(sessionId: string, content: string, options?: InfoCardOptions) {
  const state: CardState = {
    content,
    loading: options?.loading ?? false,
    noDismiss: options?.noDismiss ?? false,
    onDismiss: options?.onDismiss ?? null,
    actions: (options?.actions || []).map(a => ({
      label: a.label,
      primary: a.primary ?? false,
      loading: false,
      handler: a.onClick,
    })),
  };
  _cards.set(sessionId, state);

  if (sessionId === _currentSessionId) {
    _renderCard?.(state);
    _renderVisible?.(true);
    _notifyVisible(true);
  }
}

/** Update loading state for a specific action button on an existing card. */
export function setInfoCardActionLoading(
  sessionId: string,
  actionIndex: number,
  loading: boolean,
) {
  const state = _cards.get(sessionId);
  if (!state || actionIndex < 0 || actionIndex >= state.actions.length) return;
  state.actions[actionIndex].loading = loading;
  if (sessionId === _currentSessionId) {
    _renderCard?.({ ...state });
  }
}

/** Dismiss the info card for a specific session. */
export function dismissInfoCard(sessionId: string) {
  _cards.delete(sessionId);
  if (sessionId === _currentSessionId) {
    // Check if there's a lower-priority card pending for this session
    _renderCard?.(null);
    _renderVisible?.(false);
    _notifyVisible(false);
  }
}

/** Register a callback that fires when card visibility changes.
 *  Returns an unsubscribe function. Multiple listeners are supported. */
export function onInfoCardVisibleChange(fn: (visible: boolean) => void): () => void {
  _onVisibleChangeCallbacks.add(fn);
  return () => { _onVisibleChangeCallbacks.delete(fn); };
}

/** Notify InfoCard of a session change.
 *  Called by ChatInterface when currentSessionId changes. */
export function setInfoCardCurrentSessionId(sessionId: string | null) {
  _currentSessionId = sessionId;
  if (sessionId) {
    const pending = _cards.get(sessionId);
    if (pending) {
      _renderCard?.(pending);
      _renderVisible?.(true);
      _notifyVisible(true);
    } else {
      _renderVisible?.(false);
      _notifyVisible(false);
    }
  } else {
    _renderVisible?.(false);
    _notifyVisible(false);
  }
}

/** Renders content as plain text: escape HTML, line breaks, auto-link URLs. */
function renderContent(text: string): string {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/(https?:\/\/[^\s<>]+)/g, '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/\n/g, '<br/>');
}

export default function InfoCard() {
  const [visible, setVisible] = useState(false);
  const [cardState, setCardState] = useState<CardState | null>(null);

  useEffect(() => {
    _renderCard = (state: CardState | null) => {
      setCardState(state);
    };
    _renderVisible = (v: boolean) => {
      setVisible(v);
    };
    return () => {
      _renderCard = null;
      _renderVisible = null;
    };
  }, []);

  const handleDismiss = () => {
    if (!_currentSessionId) return;
    const state = _cards.get(_currentSessionId);
    _cards.delete(_currentSessionId);
    setVisible(false);
    setCardState(null);
    _notifyVisible(false);
    if (state?.onDismiss) {
      state.onDismiss();
    }
  };

  const handleAction = async (actionIndex: number) => {
    if (!_currentSessionId) return;
    const state = _cards.get(_currentSessionId);
    if (!state || actionIndex < 0 || actionIndex >= state.actions.length) return;
    const action = state.actions[actionIndex];

    // Set loading for this specific action button
    action.loading = true;
    setCardState({ ...state });

    try {
      const result = action.handler();
      if (result instanceof Promise) {
        await result;
      }
    } finally {
      // The action handler is expected to dismiss the card when appropriate.
      // If the card is still visible after the handler completes, dismiss it.
      const stillPending = _cards.get(_currentSessionId!);
      if (stillPending === state) {
        action.loading = false;
        // Don't auto-dismiss — leave it to the action handler.
        // If the handler didn't dismiss, update the loading state only.
        setCardState({ ...state });
      }
    }
  };

  const scrollWrapRef = useRef<HTMLDivElement>(null);
  const [scrollH, setScrollH] = useState<number | undefined>(undefined);

  // Reset height when card is dismissed so re-open starts fresh
  useEffect(() => {
    if (!visible) setScrollH(undefined);
  }, [visible]);

  useLayoutEffect(() => {
    if (!visible || !cardState) return;
    // Measure after a microtask to let the DOM settle with height: auto
    requestAnimationFrame(() => {
      if (!scrollWrapRef.current) return;
      const maxH = window.innerHeight * 0.5;
      const naturalH = scrollWrapRef.current.scrollHeight;
      setScrollH(Math.min(naturalH, maxH));
    });
  }, [visible, cardState?.content]);

  if (!visible || !cardState) return null;
  const showClose = !cardState.noDismiss && cardState.actions.length === 0;
  const hasActions = cardState.actions.length > 0;
  const anyActionLoading = cardState.actions.some(a => a.loading);

  return (
    <div className={`bg-task-card ${visible ? 'visible' : ''}`}>
      <div className="bg-task-inner">
        <div className="bg-task-content">
          {cardState.loading ? (
            <div className="bg-task-desc" style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <LoadingSpinner mode="inline" minimal size={21} />
              <span>{cardState.content}</span>
            </div>
          ) : (
            <>
              <div ref={scrollWrapRef} style={{ flex: 1, minWidth: 0, height: scrollH ?? 'auto', overflow: 'hidden' }}>
                <CustomScrollbar style={{ height: '100%' }}>
                  <div
                    className="bg-task-desc"
                    style={{ maxWidth: 'none' }}
                    dangerouslySetInnerHTML={{ __html: renderContent(cardState.content) }}
                  />
                  {hasActions && (
                    <div className="bg-task-btns" style={{ marginTop: 10 }}>
                      {cardState.actions.map((action, idx) => (
                        <button
                          key={idx}
                          className={`bg-task-btn${action.primary ? ' bg-task-btn--primary' : ''}`}
                          onClick={() => handleAction(idx)}
                          disabled={anyActionLoading}
                        >
                          {action.loading ? '...' : action.label}
                        </button>
                      ))}
                    </div>
                  )}
                </CustomScrollbar>
              </div>
              {!hasActions && (
                <div className="bg-task-sider">
                  {showClose ? (
                    <button className="bg-task-close" onClick={handleDismiss} aria-label="Dismiss">Close</button>
                  ) : (
                    <button className="bg-task-btn bg-task-btn--primary" onClick={handleDismiss} style={{ marginTop: 0 }}>
                      OK
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
