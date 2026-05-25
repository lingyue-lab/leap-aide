/**
 * useNotificationStore — DEPRECATED / empty stub
 *
 * Unread badge counts are now derived directly from session data:
 *   unreadCount = session.message_count - session.last_seen_msg_count
 *
 * This store is no longer used. It is kept as an empty stub to avoid
 * import errors during the transition period. It can be safely deleted
 * once all import references are cleaned up.
 */
export const useNotificationStore = {
  getState: () => ({
    addUnread: (_sessionId: string) => {},
    setUnread: (_sessionId: string, _count: number) => {},
    clearUnread: (_sessionId: string) => {},
    clearAll: () => {},
    unreadMap: {} as Record<string, number>,
  }),
};
