import { useEffect, useRef } from 'react';
import { cronService } from '@/services/api';
import { notifyCompletion } from '@/utils/notify';
import { useCronStore } from '@/stores/useCronStore';

/**
 * useCronNotify — Polls /api/crons/recent and sends notifications
 * (sound + desktop) when a scheduled job completes.
 * Also refreshes unreadCronCount for the +N badge on the Tasks tab.
 *
 * Polls every 15 seconds. Tracks the last-checked timestamp so only
 * genuinely new completions trigger a notification.
 */
export function useCronNotify() {
  const lastCheckedRef = useRef(Math.floor(Date.now() / 1000));

  useEffect(() => {
    // Initial timestamp = current time, so we don't notify on
    // jobs that completed before the page loaded.
    lastCheckedRef.current = Math.floor(Date.now() / 1000);

    // Also compute the initial unread count
    useCronStore.getState().refreshUnreadCount();

    const interval = setInterval(async () => {
      try {
        const since = lastCheckedRef.current;
        const res = await cronService.recent(since);
        const completions = res.completions || [];

        if (completions.length > 0) {
          // Notify for each new completion
          for (const completion of completions) {
            const jobName = completion.name || completion.job_id;
            const statusLabel = completion.status === 'error' ? '❌ Failed' : '✅ Completed';

            notifyCompletion({
              title: 'Hermes — Scheduled Job',
              body: `${jobName}: ${statusLabel}`,
              tag: `hermes-cron-${completion.job_id}-${completion.completed_at}`,
            });
          }

          // Update the last-checked timestamp to the most recent completion
          // so we don't re-notify on the same jobs next poll
          const latestTs = Math.max(...completions.map(c => c.completed_at));
          lastCheckedRef.current = Math.max(latestTs, since);
        } else {
          // No completions — bump the check timestamp forward so
          // the next poll doesn't re-check stale time ranges.
          lastCheckedRef.current = Math.floor(Date.now() / 1000);
        }

        // Always refresh unread count for the badge
        // (backend uses per-job last_seen_cron_ts_map, so since=0 is fine)
        useCronStore.getState().refreshUnreadCount();
      } catch {
        // Network error or API unavailable — silently ignore, retry next interval
      }
    }, 15000);

    return () => clearInterval(interval);
  }, []);
}