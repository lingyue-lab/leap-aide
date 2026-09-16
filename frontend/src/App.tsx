import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from './components/layout/Layout';
import ChatInterface from './components/chat/ChatInterface';
import ObserverPage from './components/observer/ObserverPage';
import { useSessionStore } from './stores/useSessionStore';
import { useSettingsStore } from './stores/useSettingsStore';
import { useWorkspaceStore } from './stores/useWorkspaceStore';
import { useTheme } from './contexts/ThemeContext';
import { ProtectedRoute } from './contexts/AuthContext';
import { useStreamNotify } from './hooks/useStreamNotify';
import { handleNotifyEvent } from './stores/notifyManager';
import { wsManager } from './stores/wsManager';
import { FONT_ZOOM_MAP } from './constants';

function App() {
  const { i18n } = useTranslation();
  const { currentSessionId } = useSessionStore();
  const { settings, loadSettings } = useSettingsStore();
  const loadWorkspaces = useWorkspaceStore((s) => s.loadWorkspaces);
  const { setTheme } = useTheme();
  useStreamNotify(currentSessionId);

  // Load settings and workspaces on mount
  React.useEffect(() => {
    loadSettings();
    loadWorkspaces();
  }, []);

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
    </ProtectedRoute>
  );
}

export default App;
