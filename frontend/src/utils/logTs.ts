/**
 * Returns a timestamp string in HH:MM:SS.mmm format for log correlation
 * with backend Python logging (which uses YYYY-MM-DD HH:MM:SS).
 *
 * Usage: console.log(`[${ts()}] my message`)
 */
export function ts(): string {
  const d = new Date();
  const pad = (n: number, w: number) => String(n).padStart(w, '0');
  return `${pad(d.getHours(), 2)}:${pad(d.getMinutes(), 2)}:${pad(d.getSeconds(), 2)}.${pad(d.getMilliseconds(), 3)}`;
}
