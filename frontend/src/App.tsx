import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from './components/layout/Layout';
import ChatInterface from './components/chat/ChatInterface';
import ObserverPage from './components/observer/ObserverPage';
import LoadingSpinner from './components/common/LoadingSpinner';
import DialogHost from './components/common/DialogHost';
import { useSessionStore } from './stores/useSessionStore';
import { useSettingsStore } from './stores/useSettingsStore';
import { useTheme } from './contexts/ThemeContext';
import { ProtectedRoute, useAuth } from './contexts/AuthContext';
import { useAuthenticatedBootstrap } from './hooks/useAuthenticatedBootstrap';
import { useStreamNotify } from './hooks/useStreamNotify';
import { handleNotifyEvent } from './stores/notifyManager';
import { wsManager } from './stores/wsManager';
import { FONT_ZOOM_MAP } from './constants';

function App() {
  const { i18n } = useTranslation();
  const { currentSessionId, pinningSession } = useSessionStore();
  const { settings } = useSettingsStore();
  const { setTheme } = useTheme();
  const { isAuthenticated } = useAuth();
  useStreamNotify(currentSessionId);

  // Boot loaders — settings, workspaces and the providers-gate warm-up all
  // hit auth-protected endpoints, so they wait for a session instead of
  // firing on mount and answering 401 on the first clean boot (issue #5).
  useAuthenticatedBootstrap(isAuthenticated);

  // Connect to WebSocket with a small delay to allow font loading first.
  React.useEffect(() => {
    const timer = setTimeout(() => {
      // Register notify event handler for WebSocket
      wsManager.addNotifyListener(handleNotifyEvent);
      wsManager.connect();
    }, 100); // Small delay to let font requests start first

    // Reconnect WS when tab becomes visible (e.g. after screen lock).
    // This is the primary mechanism for recovering from OS-level suspend
    // where the browser's JS engine is frozen and reconnect attempts are exhausted.
    const onVisibility = () => {
      if (document.visibilityState === 'visible') {
        wsManager.ensureConnected();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      clearTimeout(timer);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, []);

  // Apply language from settings
  React.useEffect(() => {
    if (settings?.language && i18n.language !== settings.language) {
      i18n.changeLanguage(settings.language);
    }
  }, [settings?.language, i18n]);

  // Apply theme from server settings when they load
  React.useEffect(() => {
    if (settings?.theme) {
      setTheme(settings.theme);
    }
  }, [settings?.theme]);

  // Apply font size from settings
  React.useEffect(() => {
    const fontSize = settings?.font_size || 'medium';
    document.documentElement.setAttribute('data-font-size', fontSize);
    // Apply zoom to the root element for global scaling
    document.documentElement.style.setProperty('--app-zoom', FONT_ZOOM_MAP[fontSize] || FONT_ZOOM_MAP.medium);
  }, [settings?.font_size]);

  return (
    <ProtectedRoute>
      <Layout>
        <Routes>
          <Route path="/" element={<ChatInterface sessionId={currentSessionId} />} />
          <Route path="/session/:sessionId" element={<ChatInterface />} />
          <Route path="/observations/:sessionId" element={<ObserverPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
      {/* Fullscreen overlay shown while /api/session/pin synchronously
          boots SessionDisplay on the server (10-15s cold start). Mounted
          once at App root; visibility is driven entirely by the store
          flag, so it stays out of the way during normal use.
          Scrim + centered spinner, no card wrapper — matches the
          M3 progress indicator overlay spec and keeps this spinner
          visually identical to the inline/overlay variants. */}
      {pinningSession && (
        <LoadingSpinner mode="fullscreen" />
      )}
      {/* Global host for showConfirmDialog / showPromptDialog helpers.
          Mounted once at the App root so any module can queue a dialog
          request without prop drilling. Renders nothing when no dialog
          is open. */}
      <DialogHost />
    </ProtectedRoute>
  );
}

export default App;
