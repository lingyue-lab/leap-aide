import { useEffect, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { isToday, isYesterday, isThisWeek, isThisYear } from 'date-fns';
import { useSessionStore } from '@/stores/useSessionStore';
import SessionItem from './SessionItem';

export default function SessionList() {
  const { t, i18n } = useTranslation('common');
  const { sessions, currentSessionId, loadSessions, createSession, selectSession } = useSessionStore();
  const [searchQuery, setSearchQuery] = useState('');

  // Load sessions on mount
  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  // Filter sessions by search query
  const filteredSessions = useMemo(() => {
    const sessionList = sessions || [];
    if (!searchQuery.trim()) return sessionList;
    const query = searchQuery.toLowerCase();
    return sessionList.filter((s) => s.title.toLowerCase().includes(query));
  }, [sessions, searchQuery]);

  // Group sessions by date
  const groupedSessions = useMemo(() => {
    const groups: { label: string; sessions: typeof sessions; key: string }[] = [];
    const pinned: typeof sessions = [];
    const today: typeof sessions = [];
    const yesterday: typeof sessions = [];
    const thisWeek: typeof sessions = [];
    const thisMonth: typeof sessions = [];
    const older: typeof sessions = [];

    filteredSessions.forEach((s) => {
      const date = new Date(s.created_at * 1000);
      
      if (s.pinned) {
        pinned.push(s);
      } else if (isToday(date)) {
        today.push(s);
      } else if (isYesterday(date)) {
        yesterday.push(s);
      } else if (isThisWeek(date)) {
        thisWeek.push(s);
      } else if (isThisYear(date)) {
        thisMonth.push(s);
      } else {
        older.push(s);
      }
    });

    if (pinned.length) {
      groups.push({ label: t('sessions.pinned'), sessions: pinned, key: 'pinned' });
    }
    if (today.length) {
      groups.push({ label: t('sessions.today'), sessions: today, key: 'today' });
    }
    if (yesterday.length) {
      groups.push({ label: t('sessions.yesterday'), sessions: yesterday, key: 'yesterday' });
    }
    if (thisWeek.length) {
      groups.push({ label: t('sessions.this_week'), sessions: thisWeek, key: 'week' });
    }
    if (thisMonth.length) {
      groups.push({ label: t('sessions.this_month'), sessions: thisMonth, key: 'month' });
    }
    if (older.length) {
      groups.push({ label: t('sessions.older'), sessions: older, key: 'older' });
    }

    return groups;
  }, [filteredSessions, t, i18n.language]);

  const handleNewChat = async () => {
    await createSession();
  };

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value);
  };

  return (
    <>
      <div className="sidebar-section">
        <button className="new-chat-btn" onClick={handleNewChat}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
            <line x1="12" y1="5" x2="12" y2="19" />
            <line x1="5" y1="12" x2="19" y2="12" />
          </svg>
          <span>{t('sessions.new_conversation')}</span>
          <span className="text-xs opacity-50 ml-1">Cmd+K</span>
        </button>
      </div>
      
      <div className="session-search">
        <input
          type="text"
          placeholder={t('sessions.filter')}
          value={searchQuery}
          onChange={handleSearchChange}
        />
      </div>
      
      <div className="session-list">
        {groupedSessions.map((group) => (
          <div key={group.key}>
            <div className={`session-date-header ${group.key === 'pinned' ? 'pinned' : ''}`}>
              <span className="session-date-caret">{/* Caret icon */}</span>
              {group.label}
            </div>
            {group.sessions.map((session) => (
              <SessionItem
                key={session.session_id}
                session={session}
                isActive={currentSessionId === session.session_id}
                onClick={() => selectSession(session.session_id)}
              />
            ))}
          </div>
        ))}
        
        {filteredSessions.length === 0 && (
          <div className="text-center text-[var(--muted)] text-sm py-4">
            {searchQuery ? t('sessions.no_results') : t('sessions.empty')}
          </div>
        )}
      </div>
    </>
  );
}
