/** Detect scraper CAPTCHA / anti-bot block notices in job.last_error */
export function isBlockNotice(msg: string | null | undefined): boolean {
  if (!msg) return false;
  const m = msg.toLowerCase();
  return (
    m.includes("captcha") ||
    m.includes("blocked") ||
    m.includes("blockage") ||
    m.includes("unusual traffic") ||
    m.includes("search engines") ||
    m.includes("wikipedia-only") ||
    m.includes("wikipedia scrape")
  );
}

/** Backend stores UTC without Z — treat as UTC so IST users don't see +5h offset */
function parseActivityTime(iso: string): number {
  const s = iso.trim();
  if (s.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(s)) {
    return new Date(s).getTime();
  }
  return new Date(`${s}Z`).getTime();
}

/** Human-readable "time ago" for scrape activity heartbeat */
export function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return "just now";
  const diffMs = Date.now() - parseActivityTime(iso);
  if (diffMs < 0 || Number.isNaN(diffMs)) return "just now";
  const sec = Math.floor(diffMs / 1000);
  if (sec < 15) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  return `${Math.floor(min / 60)}h ago`;
}

export function isActivityStale(iso: string | null | undefined, minutes = 3): boolean {
  if (!iso) return true;
  return Date.now() - parseActivityTime(iso) > minutes * 60 * 1000;
}

export function isActivityVeryStale(iso: string | null | undefined, minutes = 10): boolean {
  if (!iso) return true;
  return Date.now() - parseActivityTime(iso) > minutes * 60 * 1000;
}
