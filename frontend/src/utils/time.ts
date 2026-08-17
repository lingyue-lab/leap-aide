/** Format a unix-seconds timestamp as "HH:MM:SS", or null when absent. */
export function formatTimestamp(ts?: number): string | null {
  if (!ts) return null;
  const d = new Date(ts * 1000);
  const hh = String(d.getHours()).padStart(2, '0');
  const mm = String(d.getMinutes()).padStart(2, '0');
  const ss = String(d.getSeconds()).padStart(2, '0');
  return `${hh}:${mm}:${ss}`;
}
