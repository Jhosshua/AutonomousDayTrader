/**
 * Plain-language helpers for the redesigned dashboard.
 *
 * Pure functions only (no React, no fetch). Every number that reaches the screen still comes
 * from the live API payloads; this file only turns codes/ids/ISO timestamps into the plain
 * words, colors, and layout math the mockups (docs/ui_redesign_2026_09_24/*.dc.html) specify.
 */

// ---------------------------------------------------------------------------
// Strategy identity: friendly names, colors ("muted palette"), and copy
// ---------------------------------------------------------------------------

export interface StrategyTheme {
  name: string;
  band: string; // card header background
  ink: string; // header text color
  tint: string; // soft chip/note background
  bar: string; // hours-bar segment / icon tile color
  track: string; // hours-bar track background
  what: string; // one-sentence plain description
}

export const STRATEGY_THEMES: Record<string, StrategyTheme> = {
  orb: {
    name: "Morning Breakout",
    band: "#F4E0CF",
    ink: "#7A3E1D",
    tint: "#FAF0E6",
    bar: "#D98B5F",
    track: "#F2E3D5",
    what: "Watches the first few minutes of the day. If a stock then shoots past that range, it jumps in.",
  },
  vwap_pullback: {
    name: "Ride the Trend",
    band: "#DCE8DE",
    ink: "#2F5A45",
    tint: "#EDF3EE",
    bar: "#6E9C82",
    track: "#DDE9E0",
    what: "When a stock is moving steadily one way, it waits for a small dip, then joins the ride.",
  },
  news_momentum: {
    name: "Big News",
    band: "#F1DDDF",
    ink: "#7A3343",
    tint: "#F7EBED",
    bar: "#C47A88",
    track: "#EFDFE2",
    what: "Jumps in when a company gets very big news and lots of people rush to trade it.",
  },
  mean_reversion: {
    name: "Snap Back",
    band: "#E0E1F0",
    ink: "#3E4478",
    tint: "#EEEFF7",
    bar: "#8189C4",
    track: "#E2E3F1",
    what: "Bets that a stock that ran too far, too fast will bounce back a little.",
  },
};

export const NEUTRAL_THEME: Omit<StrategyTheme, "name" | "what"> = {
  band: "#ECE9DE",
  ink: "#5D5A73",
  tint: "#F3F1EA",
  bar: "#A7A2B8",
  track: "#E7E3D6",
};

/** Theme + copy for a strategy id. Unknown ids fall back to neutral grey with the raw name. */
export function strategyTheme(id: string, fallbackName?: string): StrategyTheme {
  const known = STRATEGY_THEMES[id];
  if (known) return known;
  return { ...NEUTRAL_THEME, name: fallbackName || id, what: "" };
}

const COMPANY_NAMES: Record<string, string> = {
  AAPL: "Apple",
  NVDA: "Nvidia",
  PLTR: "Palantir",
  TSLA: "Tesla",
  META: "Meta",
  AMD: "AMD",
  MSFT: "Microsoft",
  AMZN: "Amazon",
  GOOGL: "Alphabet",
  MU: "Micron",
  LRCX: "Lam Research",
  KLAC: "KLA",
  GS: "Goldman Sachs",
};

/** Company display name for a ticker, falling back to the ticker itself. */
export function companyName(ticker: string | undefined | null): string {
  if (!ticker) return "";
  return COMPANY_NAMES[ticker.toUpperCase()] || ticker.toUpperCase();
}

// ---------------------------------------------------------------------------
// Trading-window state -> status chip (color family + label)
// ---------------------------------------------------------------------------

export type ChipTone = "sage" | "lavender" | "grey" | "terracotta";

export interface StatusChip {
  label: string;
  tone: ChipTone;
  breathing: boolean; // animated "live" dot
}

/** Maps a StrategyWindow.state to the plain status chip. Idle is NEVER red/terracotta (rule 3). */
export function windowToChip(win: { state: string; headline: string; blockers?: string[] } | undefined): StatusChip {
  if (!win) return { label: "Unknown", tone: "grey", breathing: false };
  switch (win.state) {
    case "CAN_TRADE":
      return { label: "Watching now", tone: "sage", breathing: true };
    case "LIMITED":
      return { label: "Watching, extra careful", tone: "sage", breathing: true };
    case "WAITING":
      return { label: win.headline || "Waiting", tone: "lavender", breathing: false };
    case "DONE_FOR_DAY":
      return { label: "Done for today", tone: "grey", breathing: false };
    case "BLOCKED":
      return { label: win.blockers?.[0] ? `Paused: ${win.blockers[0]}` : "Paused right now", tone: "grey", breathing: false };
    case "PAUSED":
      return { label: "Paused", tone: "grey", breathing: false };
    case "MARKET_CLOSED":
      return { label: "Market closed", tone: "grey", breathing: false };
    default:
      return { label: win.headline || win.state, tone: "grey", breathing: false };
  }
}

// ---------------------------------------------------------------------------
// ET time helpers (no dependency on server clock; formats the browser's Date in America/New_York)
// ---------------------------------------------------------------------------

const ET_TZ = "America/New_York";

export function etParts(now: Date = new Date()): { hour: number; minute: number; weekday: number } {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: ET_TZ,
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    weekday: "short",
  });
  const parts = fmt.formatToParts(now);
  const hour = Number(parts.find((p) => p.type === "hour")?.value ?? "0");
  const minute = Number(parts.find((p) => p.type === "minute")?.value ?? "0");
  const weekdayStr = parts.find((p) => p.type === "weekday")?.value ?? "Sun";
  const map: Record<string, number> = { Sun: 0, Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6 };
  return { hour: hour === 24 ? 0 : hour, minute, weekday: map[weekdayStr] ?? 0 };
}

/** YYYY-MM-DD for "now" in America/New_York, used to compare against a trade's session_date. */
export function etDateKey(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: ET_TZ, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** "h:mm AM/PM" for an ISO timestamp, in America/New_York. */
export function etTimeLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-US", { timeZone: ET_TZ, hour: "numeric", minute: "2-digit", hour12: true }).format(d);
}

const SESSION_START_MIN = 9 * 60 + 30;
const SESSION_END_MIN = 16 * 60;

/** Clamp a minutes-of-day value to 0-100% across the 9:30-16:00 ET session. */
export function sessionPct(minutesOfDay: number): number {
  const span = SESSION_END_MIN - SESSION_START_MIN;
  return Math.min(100, Math.max(0, ((minutesOfDay - SESSION_START_MIN) / span) * 100));
}

export function etMinutesOfDay(now: Date = new Date()): number {
  const { hour, minute } = etParts(now);
  return hour * 60 + minute;
}

/** Minutes-of-day (ET) for an ISO timestamp string, for placing a trade on the session axis. */
export function etMinutesOfDayFromIso(iso: string): number {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return SESSION_START_MIN;
  return etMinutesOfDay(d);
}

export function isWithinSession(minutesOfDay: number): boolean {
  return minutesOfDay >= SESSION_START_MIN && minutesOfDay <= SESSION_END_MIN;
}

// ---------------------------------------------------------------------------
// Hours bar: backend `window.ranges` ("HH:MM" ET pairs) -> track segments
// ---------------------------------------------------------------------------

export interface TrackSegment {
  left: number; // percent
  width: number; // percent
}

export function rangesToSegments(ranges: [string, string][] | undefined | null): TrackSegment[] {
  if (!ranges || ranges.length === 0) return [];
  return ranges
    .map(([startStr, endStr]) => {
      const [sh, sm] = startStr.split(":").map(Number);
      const [eh, em] = endStr.split(":").map(Number);
      const left = sessionPct(sh * 60 + sm);
      const right = sessionPct(eh * 60 + em);
      return { left, width: Math.max(0, right - left) };
    })
    .filter((s) => s.width > 0);
}

// ---------------------------------------------------------------------------
// "Quick trades close in" countdown to 15:55 ET
// ---------------------------------------------------------------------------

export function countdownToClose(now: Date = new Date(), isTradingDay?: boolean): string {
  const { hour, minute, weekday } = etParts(now);
  const tradingDay = isTradingDay ?? (weekday !== 0 && weekday !== 6);
  if (!tradingDay) return "Market closed";
  const nowMin = hour * 60 + minute;
  const closeMin = 15 * 60 + 55;
  if (nowMin < SESSION_START_MIN || nowMin >= closeMin) return "Market closed";
  const remaining = closeMin - nowMin;
  const h = Math.floor(remaining / 60);
  const m = remaining % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

// ---------------------------------------------------------------------------
// "Right now" headline sentence (dark hero card)
// ---------------------------------------------------------------------------

export interface RightNowStrategy {
  window?: { state: string; headline: string };
}

export interface RightNowInputs {
  isCircuitBroken: boolean;
  marketStatus: string | undefined;
  strategies: RightNowStrategy[];
  positionsCount: number;
  maxDailyLossDollars?: number | null;
}

export function rightNowSentence(inputs: RightNowInputs): string {
  const { isCircuitBroken, marketStatus, strategies, positionsCount, maxDailyLossDollars } = inputs;
  let base: string;
  if (isCircuitBroken) {
    base =
      maxDailyLossDollars != null
        ? `Stopped for today. It hit the $${Math.round(maxDailyLossDollars).toLocaleString()} loss limit.`
        : "Stopped for today. It hit the daily loss limit.";
  } else if ((marketStatus || "").toUpperCase() !== "OPEN") {
    base = "Market is closed. It starts again at 9:30 AM.";
  } else {
    const active = strategies.filter((s) => s.window?.state === "CAN_TRADE" || s.window?.state === "LIMITED");
    const waiting = strategies.filter((s) => s.window?.state === "WAITING");
    if (active.length > 0) {
      const plural = active.length === 1 ? "playbook is" : "playbooks are";
      base = `${active.length} ${plural} watching for a good chance.`;
      if (waiting.length > 0) {
        const when = (waiting[0].window?.headline || "").replace(/^Opens\s*/i, "");
        base += ` One is resting until ${when || "later"}.`;
      }
    } else if (waiting.length > 0) {
      base = `Waiting. ${waiting[0].window?.headline || ""}`.trim();
    } else {
      base = "Nothing is watching right now.";
    }
  }
  if (positionsCount > 0) {
    const plural = positionsCount === 1 ? "trade" : "trades";
    base = `Holding ${positionsCount} ${plural} right now. ${base}`;
  }
  return base;
}

// ---------------------------------------------------------------------------
// Strategy card "today" note line
// ---------------------------------------------------------------------------

export interface StrategyDecisionsLike {
  signals_today: number;
  orders_today: number;
  top_block_text?: string | null;
}

export function strategyNoteLine(
  decisions: StrategyDecisionsLike | undefined,
  marketText: string | undefined,
  scheduleNote?: string
): string {
  const signals = decisions?.signals_today ?? 0;
  const orders = decisions?.orders_today ?? 0;
  let lead: string;
  if (orders > 0 && signals > orders) {
    lead = `${orders} trade${orders === 1 ? "" : "s"} today. Skipped ${signals - orders} other chance${signals - orders === 1 ? "" : "s"}.`;
  } else if (orders > 0) {
    lead = `${orders} trade${orders === 1 ? "" : "s"} today.`;
  } else if (signals > 0) {
    lead = `Saw ${signals} chance${signals === 1 ? "" : "s"}, skipped all.`;
  } else {
    lead = scheduleNote || "No chances yet today.";
  }
  const tail = marketText || "";
  return tail ? `${lead} ${tail}` : lead;
}

// ---------------------------------------------------------------------------
// Ledger grouping (F4/F5): today's trades grouped by strategy
// ---------------------------------------------------------------------------

export interface LedgerTradeLike {
  trade_id: string;
  strategy_id: string;
  realized_pnl: number;
  closed_at: string;
  session_date: string;
}

export interface StrategyLedgerAgg {
  realized_pnl: number;
  trades_count: number;
  wins: number;
  losses: number;
}

/** Group ledger rows by strategy_id, deduping by trade_id. Pure aggregation, no fetching. */
export function groupLedgerByStrategy(items: LedgerTradeLike[]): Record<string, StrategyLedgerAgg> {
  const seen = new Set<string>();
  const out: Record<string, StrategyLedgerAgg> = {};
  for (const item of items) {
    if (seen.has(item.trade_id)) continue;
    seen.add(item.trade_id);
    const agg = out[item.strategy_id] || { realized_pnl: 0, trades_count: 0, wins: 0, losses: 0 };
    agg.realized_pnl = Math.round((agg.realized_pnl + item.realized_pnl) * 100) / 100;
    agg.trades_count += 1;
    if (item.realized_pnl > 0) agg.wins += 1;
    else if (item.realized_pnl < 0) agg.losses += 1;
    out[item.strategy_id] = agg;
  }
  return out;
}

/** Dedupe trade rows by trade_id, keeping the first occurrence (used while draining pages). */
export function dedupeTrades<T extends { trade_id: string }>(items: T[]): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of items) {
    if (seen.has(item.trade_id)) continue;
    seen.add(item.trade_id);
    out.push(item);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Money / percent formatting
// ---------------------------------------------------------------------------

export function formatMoney(value: number): string {
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value < 0 ? "-" : ""}$${formatted}`;
}

export function formatSignedMoney(value: number): string {
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${value < 0 ? "-" : "+"}$${formatted}`;
}

/** "2026-10-21" -> "Oct 21" (date-only strings, no timezone shift). Falls back to the input. */
export function shortDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const mi = Number(m[2]) - 1;
  return mi >= 0 && mi < 12 ? `${months[mi]} ${Number(m[3])}` : iso;
}
