import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from './components/layout/Layout';
import ChatInterface from './components/chat/ChatInterface';
import ObserverPage from './components/observer/ObserverPage';
import { useSessionStore } from './stores/useSessionStore';
import { useSettingsStore } from './stores/useSettingsStore';
import { useTheme } from './contexts/ThemeContext';
import { ProtectedRoute } from './contexts/AuthContext';
import { useStreamNotify } from './hooks/useStreamNotify';
import { FONT_ZOOM_MAP } from './constants';

function App() {
  const { i18n } = useTranslation();
  const { currentSessionId } = useSessionStore();
  const { settings, loadSettings } = useSettingsStore();
  const { setTheme } = useTheme();
  useStreamNotify();

  // Load settings on mount
  React.useEffect(() => {
    loadSettings();
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
