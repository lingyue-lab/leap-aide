import React from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import Layout from './components/layout/Layout';
import ChatInterface from './components/chat/ChatInterface';
import { useSessionStore } from './stores/useSessionStore';
import { useSettingsStore } from './stores/useSettingsStore';
import { ProtectedRoute } from './contexts/AuthContext';

function App() {
  const { i18n } = useTranslation();
  const { currentSessionId } = useSessionStore();
  const { settings } = useSettingsStore();

  // Apply language from settings
  React.useEffect(() => {
    if (settings?.language && i18n.language !== settings.language) {
      i18n.changeLanguage(settings.language);
    }
  }, [settings?.language, i18n]);

  return (
    <ProtectedRoute>
      <Layout>
        <Routes>
          <Route path="/" element={<ChatInterface sessionId={currentSessionId} />} />
          <Route path="/session/:sessionId" element={<ChatInterface />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </ProtectedRoute>
  );
}

export default App;
