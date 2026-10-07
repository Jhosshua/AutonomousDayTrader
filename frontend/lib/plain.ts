// @steered SNARE-2 2026-09-30
/**
 * Plain-language helpers for the redesigned dashboard.
 *
 * Pure functions only (no React, no fetch). Every number that reaches the screen still comes
 * from the live API payloads; this file only turns codes/ids/ISO timestamps into the plain
 * words, colors, and layout math the mockups (docs/ui_redesign_2026_09_24/*.dc.html) specify.
 */

import type { EntryContext } from "@/types/trading";

// ---------------------------------------------------------------------------
// Strategy identity: friendly names, colors ("muted palette"), and copy
// ---------------------------------------------------------------------------

export function planStatusText(reason: string): string {
  const messages: Record<string, string> = {
    CALENDAR_UNAVAILABLE_OR_CLOSED: "Market closed today.",
    OPERATOR_PAUSED: "Paused by you.",
    MISSING_OPENING_RANGE_BAR: "Skipped today because the opening price data was incomplete.",
    MISSING_REQUIRED_BAR: "Skipped today because required price data was missing.",
    MISSED_ENTRY_WINDOW: "The entry window passed before an order could be sent.",
    SYMBOL_ALREADY_COMMITTED: "Another strategy is already trading this stock.",
    PAPER_BROKER_OR_SIP_UNAVAILABLE: "Waiting for the paper broker and live price feed.",
    ENTRY_NOT_FILLED: "The entry order did not fill.",
    INSUFFICIENT_RISK_OR_BUYING_POWER: "There was not enough buying power or risk budget for this trade.",
    NON_POSITIVE_RISK: "The price was already beyond the planned safety exit.",
    PROTECTION_REJECTED: "The broker could not accept the safety exits. Closing the trade.",
  };
  return messages[reason] || "A required trading check did not pass.";
}

export function historyDateLabel(day: string): string {
  return new Date(`${day}T12:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "America/New_York", weekday: "short", month: "short", day: "numeric", year: "numeric",
  });
}

export function trendBlockText(event: string): string {
  const reasons: Record<string, string> = {
    DUP_BAR: "Repeated price bar ignored.", LATE_BAR: "Delayed price bar ignored.",
    BAD_BAR: "Price data failed validation.", FEED_GAP: "Price feed gap; waiting for fresh history.",
    ZERO_VOLUME_BAR: "No volume in this minute.", AMBIGUOUS_BAR: "Price broke both sides of the range.",
    TOUCH_TOO_EARLY: "Pullback arrived too soon.", NO_TOUCH: "Price did not return to the entry zone in time.",
    HIGH_VOLUME_PULLBACK: "Too much volume in the pullback.", PVR_NOT_THIN: "Pullback volume was not low enough.",
    TICK_UNAVAILABLE: "Trade data was incomplete.", AGGRESSIVE_PULLBACK: "Too much trading against the pullback.",
    PULLBACK_TIMEOUT: "Pullback lasted too long.", RESUMPTION_TOO_OLD: "The bounce took too long.",
    NO_UP_CLOSE: "Price did not resume in the trade's direction.", SLOPE_TOO_SLOW: "The bounce was too slow.",
    CHASED: "Price moved too far from the entry zone.", TICK_VELOCITY_LOW: "Recent trades did not show enough speed.",
    BOOK_UNAVAILABLE: "Bid and ask data was incomplete.", BOOK_AGAINST: "Bid and ask sizes opposed the trade.",
    WINDOW_CLOSED: "The morning entry window closed.", EMISSION_DISABLED: "Strategy paused, cooling down, or at its daily limit.",
    STOP_TOO_WIDE: "The required safety exit was too far away.",
    IMPULSE_NOT_AGGRESSIVE: "Trading flow did not support the initial move.",
    RESUMPTION_NOT_AGGRESSIVE: "Trading flow did not support the bounce.", CUM_DELTA_AGAINST: "Recent net trading opposed the move.",
    SESSION_DELTA_AGAINST: "Net buying and selling opposed this trade.", SPREAD_WIDE: "The bid–ask spread was too wide.",
    HVN_NO_SUPPORT: "The dip missed prior trading support.", HVN_OVERHEAD: "A prior trading level blocked the path to the target.",
    PROFILE_UNAVAILABLE: "Prior-session volume history was unavailable.",
  };
  return reasons[event] || "A required entry check did not pass.";
}

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
  tsla_asymmetric_dual: {
    name: "Tesla Morning Plan", band: "#DDE4FF", ink: "#1E36B8", tint: "#EEF1FF",
    bar: "#2B4BFF", track: "#E1E7FF",
    what: "Trades a confirmed morning bounce or breakdown. Splits the position into two equal parts, each with a fixed target and closing time.",
  },
  cde_asymmetric_dual: {
    name: "Coeur Morning Plan", band: "#FBEBB8", ink: "#7A5900", tint: "#FFF6DD",
    bar: "#B88600", track: "#F7EDC8",
    what: "Trades Coeur Mining after a morning bounce or breakdown, with QQQ confirmation. Uses one fixed target and a three-hour limit.",
  },
  tsla_or15_retest: {
    name: "Tesla Morning Retest",
    band: "#DDE4FF",
    ink: "#1E36B8",
    tint: "#EEF1FF",
    bar: "#2B4BFF",
    track: "#E1E7FF",
    what: "Buys one Tesla share after a morning breakout pulls back and holds. Checks QQQ for support, then uses fixed exits and a two-hour limit.",
  },
  orb: {
    name: "Opening Range Breakout (ORBStraddle rules)",
    band: "#FFDFCF",
    ink: "#8F3600",
    tint: "#FFF0E8",
    bar: "#E85A1B",
    track: "#FCE5D9",
    what: "Scans about 250 stocks for a clean break of their 9:30 to 9:35 range. Decides at 9:38 AM, may add a trade until 10:15 AM, and closes every trade by 11:00 AM. Each trade is a broker bracket: its stop and target wait at Alpaca.",
  },
  vwap_pullback: {
    name: "Ride the Trend",
    band: "#CDEFF1",
    ink: "#06707A",
    tint: "#E6F7F8",
    bar: "#0A8F9C",
    track: "#D5F0F2",
    what: "Version 2. When a stock pushes to a new high (or low), it waits for a quiet, low-volume dip back toward the day's average price, then joins only if the move picks up speed again. Mornings only.",
  },
  news_momentum: {
    name: "Big News",
    band: "#FFD6EA",
    ink: "#A3135B",
    tint: "#FFEAF4",
    bar: "#D61F7A",
    track: "#FBDDEC",
    what: "Jumps in when a company gets very big news and lots of people rush to trade it.",
  },
  mean_reversion: {
    name: "Snap Back",
    band: "#E2D8FF",
    ink: "#4A2AB5",
    tint: "#F0EBFF",
    bar: "#6D3BFF",
    track: "#E6DEFF",
    what: "Bets that a stock that ran too far, too fast will bounce back a little.",
  },
  // Overnight holds: one ink colour for all three (they are the only plays that run past the close).
  overnight_nvda: {
    name: "NVDA overnight", band: "#E3E6F1", ink: "#0E1330", tint: "#F1F3FA", bar: "#0E1330", track: "#DFE3F0",
    what: "Buys NVDA near the 4:00 PM close and sells it at the next 9:30 AM open. No stop.",
  },
  overnight_iren: {
    name: "IREN overnight", band: "#E3E6F1", ink: "#0E1330", tint: "#F1F3FA", bar: "#0E1330", track: "#DFE3F0",
    what: "Buys IREN near the 4:00 PM close and sells it at the next 9:30 AM open. No stop.",
  },
  overnight_hut: {
    name: "HUT overnight", band: "#E3E6F1", ink: "#0E1330", tint: "#F1F3FA", bar: "#0E1330", track: "#DFE3F0",
    what: "Buys HUT near the 4:00 PM close and sells it at the next 9:30 AM open. No stop.",
  },
};

export const NEUTRAL_THEME: Omit<StrategyTheme, "name" | "what"> = {
  band: "#E6EAF5",
  ink: "#4B5273",
  tint: "#F1F3FB",
  bar: "#8A91B0",
  track: "#E3E7F3",
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
  CDE: "Coeur Mining",
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

export type ChipTone = "sage" | "lavender" | "grey" | "terracotta" | "amber";

export interface StatusChip {
  label: string;
  tone: ChipTone;
  breathing: boolean; // animated "live" dot
}

/** Maps a StrategyWindow.state to the plain status chip. Idle is NEVER red/terracotta (rule 3). */
export function windowToChip(win: { state: string; headline: string; blockers?: string[] } | undefined): StatusChip {
  if (!win) return { label: "Unknown", tone: "grey", breathing: false };
  switch (win.state) {
    case "MANAGING":
      return { label: "Managing trade", tone: "sage", breathing: true };
    case "CAN_TRADE":
      return { label: "Watching now", tone: "sage", breathing: true };
    case "LIMITED":
      return { label: "Watching, extra careful", tone: "sage", breathing: true };
    case "WAITING":
      return { label: win.headline || "Waiting", tone: "lavender", breathing: false };
    case "DONE_FOR_DAY":
      return { label: "Done for today", tone: "grey", breathing: false };
    case "NO_TRADE_TODAY":
      // cannot trade for the rest of the day (e.g. ORB's 9:38 scan failed): amber, never "watching"
      return { label: win.headline || "No trade today", tone: "amber", breathing: false };
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

/** Share of the hours bar the 9:30 to 4:00 PM day takes when the bar also has a night part (overnight on). */
export const NIGHT_AXIS_DAY_PCT = 88;

/** Position on the hours bar. With the night part, 9:30 to 4:00 PM maps onto 0 to 88% and 88 to 100% is the night
 * (not to scale). Without it this is sessionPct. */
export function axisPct(minutesOfDay: number, night = false): number {
  const p = sessionPct(minutesOfDay);
  return night ? (p * NIGHT_AXIS_DAY_PCT) / 100 : p;
}

export function rangesToSegments(ranges: [string, string][] | undefined | null, night = false): TrackSegment[] {
  if (!ranges || ranges.length === 0) return [];
  return ranges
    .map(([startStr, endStr]) => {
      const [sh, sm] = startStr.split(":").map(Number);
      const [eh, em] = endStr.split(":").map(Number);
      const left = axisPct(sh * 60 + sm, night);
      const right = axisPct(eh * 60 + em, night);
      return { left, width: Math.max(0, right - left) };
    })
    .filter((s) => s.width > 0);
}

// ---------------------------------------------------------------------------
// "Quick trades close in" countdown to 15:55 ET. A quick trade in a stock the robot buys
// overnight tonight closes at 15:46 instead (plan X6), so the caller can pass that minute.
// ---------------------------------------------------------------------------

export function countdownToClose(now: Date = new Date(), isTradingDay?: boolean, closeMinute: number = 15 * 60 + 55): string {
  const { hour, minute, weekday } = etParts(now);
  const tradingDay = isTradingDay ?? (weekday !== 0 && weekday !== 6);
  if (!tradingDay) return "Market closed";
  const nowMin = hour * 60 + minute;
  const closeMin = closeMinute;
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
  /** The overnight holds exist on this robot. A loss limit day still buys at the close (D5), so the
   * stop is worded as day trading only. Absent on an older backend: the old words stay. */
  overnightOn?: boolean;
  /** One sentence about the overnight holds held right now (built by the caller), or null. */
  overnightLine?: string | null;
}

export function rightNowSentence(inputs: RightNowInputs): string {
  const { isCircuitBroken, marketStatus, strategies, positionsCount, maxDailyLossDollars, overnightOn, overnightLine } = inputs;
  let base: string;
  if (isCircuitBroken) {
    const lead = overnightOn ? "Day trading stopped for today." : "Stopped for today.";
    base =
      maxDailyLossDollars != null
        ? `${lead} It hit the $${Math.round(maxDailyLossDollars).toLocaleString()} loss limit.`
        : `${lead} It hit the daily loss limit.`;
  } else if ((marketStatus || "").toUpperCase() !== "OPEN") {
    // with holds the overnight line already says when the next open is
    base = overnightLine ? "Market is closed." : "Market is closed. It starts again at 9:30 AM.";
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
      const headline = waiting[0].window?.headline || "";
      base = /^Waiting\b/i.test(headline) ? headline : `Waiting. ${headline}`.trim();
    } else {
      base = "Nothing is watching right now.";
    }
  }
  if (overnightLine) base = `${overnightLine} ${base}`;
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

/** Plain name for one fixed part of a Tesla/Coeur plan trade. */
export function trancheName(index: number, count: number): string {
  if (count <= 1) return "Whole position";
  return index === 0 ? "First half" : "Second half";
}

// ---------------------------------------------------------------------------
// Market mood + "why this trade" (2026-09-29). Every sentence is built from values the backend sent
// (recorded at entry, or live config); nothing here invents a number.
// ---------------------------------------------------------------------------

/** Playbooks that size and place stops by the market mood. */
export const ADAPTIVE_IDS = ["vwap_pullback", "news_momentum", "mean_reversion"];
/** Playbooks with fixed plans that never react to the mood. */
export const FIXED_PLAN_IDS = ["tsla_asymmetric_dual", "cde_asymmetric_dual", "tsla_or15_retest"];

/** Fear gauge level (backend vix_regime) -> plain adjective for a sentence, and chip label. */
export const LEVEL_WORD: Record<string, string> = { LOW: "calm", NORMAL: "normal", ELEVATED: "nervous", CRISIS: "panicky" };
export const LEVEL_CHIP: Record<string, string> = { LOW: "Calm", NORMAL: "Normal", ELEVATED: "Nervous", CRISIS: "Panic" };
/** One neutral scale (grey, then deepening amber). Never the strategy colors, never terracotta. */
export const LEVEL_COLORS: Record<string, { bg: string; ink: string }> = {
  LOW: { bg: "#E6EAF5", ink: "#3F4663" },
  NORMAL: { bg: "#DDE2F2", ink: "#3F4663" },
  ELEVATED: { bg: "#FFE9A8", ink: "#6A4500" },
  CRISIS: { bg: "#FFC94D", ink: "#4A3000" },
};

export const TREND_WORD: Record<string, string> = { BULLISH: "rising", BEARISH: "falling", NEUTRAL: "flat", UNKNOWN: "unclear" };

export const PHASE_PLAIN: Record<string, string> = {
  PRE_MARKET: "Before the open",
  OPEN_VOLATILITY_FLUSH: "Opening rush",
  TREND_CONTINUATION: "Morning",
  MIDDAY_CHOP: "Midday",
  AFTERNOON_PUSH: "Afternoon",
  POWER_HOUR: "Last hour",
  EOD_FLATTEN: "Closing time",
  POST_MARKET: "After the close",
};

export function levelWord(regime: string | null | undefined): string {
  return LEVEL_WORD[(regime || "").toUpperCase()] || "unclear";
}

/** "0.7" -> "70%" (a sizing multiplier as a share of the usual amount). */
export function pctOfUsual(multiplier: number): string {
  return `${Math.round(multiplier * 100)}%`;
}

/** "11:30" -> "11:30 AM" */
export function hmLabel(hm: string | null | undefined): string {
  const m = /^(\d{1,2}):(\d{2})/.exec(hm || "");
  if (!m) return "";
  const h = Number(m[1]);
  return `${h % 12 === 0 ? 12 : h % 12}:${m[2]} ${h >= 12 ? "PM" : "AM"}`;
}

export function joinNames(names: string[]): string {
  if (names.length <= 1) return names[0] || "";
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function strategyNames(ids: string[]): string[] {
  return ids.map((id) => STRATEGY_THEMES[id]?.name ?? id);
}

export interface MoodInputs {
  vix: number | null | undefined;
  vix_regime?: string | null;
  market_status?: string | null;
  sizing_multiplier?: number | null;
  time_multiplier?: number | null;
  market_trend?: string | null;
  adaptive_strategies?: string[] | null;
}

export interface MoodHeadline {
  tone: "grey" | "level";
  /** Show the three tiles (market open and data seen). Closed and waiting states are the headline alone. */
  tiles: boolean;
  text: string;
  /** Second line, only when the robot is trading by the mood. */
  note: string | null;
}

/** The mood card's one-sentence answer, decided in this order: closed, no data, trend unclear, normal. */
export function moodHeadline(ctx: MoodInputs, tradingDay: boolean | undefined): MoodHeadline {
  const status = (ctx.market_status || "").toUpperCase();
  if (tradingDay === false || status === "CLOSED") {
    return {
      tone: "grey",
      tiles: false,
      text: "Market closed. When it opens, the robot will size and place new trades for the market's mood.",
      note: null,
    };
  }
  const trend = ctx.market_trend ? ctx.market_trend.toUpperCase() : null;
  if (ctx.vix == null && trend == null) return { tone: "grey", tiles: false, text: "Waiting for market data.", note: null };
  const on = ctx.adaptive_strategies ?? null;
  const ownChecks = "ORB and the Tesla/Coeur plans use their own checks.";
  if (trend === "UNKNOWN") {
    const names = on ? strategyNames(on) : [];
    const who = on ? (names.length === 0 ? "No mood-sized playbook is switched on. " : `${joinNames(names)} ${names.length === 1 ? "is" : "are"} waiting until the robot can see SPY and QQQ again. `) : "The mood-sized playbooks are waiting until the robot can see SPY and QQQ again. ";
    return { tone: "grey", tiles: true, text: `${who}${ownChecks}`, note: null };
  }
  const level = levelWord(ctx.vix_regime);
  const direction = trend ? TREND_WORD[trend] ?? "unclear" : null;
  const opening = direction ? `The market is ${level} and ${direction}.` : `The market is ${level}.`;
  const names = on ? strategyNames(on) : null;
  const m = ctx.sizing_multiplier ?? null;
  let body: string;
  if (names && names.length === 0) {
    body = "No mood-sized playbook is switched on right now.";
  } else {
    const subject = names ? joinNames(names) : "The mood-sized playbooks";
    const plural = names ? names.length > 1 : true;
    const verb = plural ? "risk" : "risks";
    if (m == null) body = `${subject} size each trade for the mood.`;
    else if (m === 1) body = `${subject} ${verb} the usual amount per trade.`;
    else {
      const smaller = m < 1;
      body = `${subject} ${verb} ${pctOfUsual(m)} of the usual amount per trade; only trades with a wide safety exit end up ${smaller ? "smaller" : "bigger"}.`;
    }
    if (ctx.time_multiplier != null && ctx.time_multiplier < 1) body += " Midday halves that.";
  }
  return { tone: "level", tiles: true, text: `${opening} ${body}`, note: "ORB uses its own 9:38 market check instead. The Tesla/Coeur plans do not change with the mood." };
}

/** What the market direction means for new trades (live rules of the three mood-sized playbooks). */
export function trendEffect(trend: string | null | undefined): string {
  switch ((trend || "").toUpperCase()) {
    case "BULLISH": return "Rising: these playbooks only buy.";
    case "BEARISH": return "Falling: these playbooks only sell short.";
    case "NEUTRAL": return "Flat: Ride the Trend waits for a direction. Snap Back trades both ways.";
    case "UNKNOWN": return "SPY and QQQ can't be read, so new trades wait.";
    default: return "Direction not reported yet.";
  }
}

/** "1.4" -> "placed 40% farther away", "0.85" -> "placed 15% closer". */
function stopDistancePhrase(mult: number | null | undefined): "farther" | "closer" | "normal" | null {
  if (mult == null) return null;
  if (mult > 1.001) return "farther";
  if (mult < 0.999) return "closer";
  return "normal";
}

export interface WhyLine { label: string; value: string }

export interface AdaptiveWhy {
  sizeLine: string;
  stopLine: string | null;
  trendLine: string | null;
  rows: WhyLine[];
}

/** The one-sentence "why this size" plus the closed-by-default factor rows, honest to what was recorded. */
export function adaptiveWhy(ctx: EntryContext, shares: number, isLong: boolean): AdaptiveWhy {
  const verb = isLong ? "bought" : "sold short";
  const fin = ctx.qty_final ?? null;
  const neutral = ctx.qty_if_neutral ?? null;
  const sizing = ctx.sizing_multiplier ?? 1;
  const timeMult = ctx.time_multiplier ?? 1;
  const lead = fin != null && fin === shares ? (isLong ? "Bought" : "Sold short") : "Sized at";
  let sizeLine: string;
  if (fin == null || neutral == null) {
    sizeLine = "The robot did not keep how this trade was sized.";
  } else if (fin < neutral) {
    const parts: string[] = [];
    if (sizing < 1) parts.push(`${ctx.vix_regime === "CRISIS" ? "panicky" : "jumpy"} market (${pctOfUsual(sizing)})`);
    if (timeMult < 1) parts.push("midday (half)");
    let why = parts.join(" and ");
    if (ctx.size_limited_by === "account_limits") why = why ? `${why}, and the account's limits trimmed it` : "the account's limits trimmed it";
    sizeLine = `${lead} ${fin} shares, fewer than the usual ${neutral}${why ? `: ${why}` : ""}.`;
  } else if (fin > neutral) {
    sizeLine = `Bigger than usual: ${lead.toLowerCase()} ${fin} shares instead of ${neutral} because the market was calm (${pctOfUsual(sizing)}).`;
  } else if (ctx.size_limited_by === "notional_cap") {
    sizeLine = "Normal size. The per-trade money cap was the limit, so the market mood did not change the share count.";
  } else {
    sizeLine = "Normal size. The market mood did not change the share count.";
  }

  const dist = stopDistancePhrase(ctx.stop_multiplier);
  let stopLine: string | null = null;
  if (ctx.stop_basis === "structure") {
    stopLine = isLong ? "Safety exit placed below the dip's low." : "Safety exit placed above the bounce's high.";
  } else if (ctx.stop_basis === "floor") {
    stopLine = "Safety exit placed at the closest distance the robot allows.";
  } else if (dist === "farther") {
    stopLine = "Safety exit placed farther away because the market is jumpy.";
  } else if (dist === "closer") {
    stopLine = "Safety exit placed closer because the market is calm.";
  } else if (dist === "normal") {
    stopLine = "Safety exit placed a normal distance away.";
  }

  let trendLine: string | null = null;
  const trend = (ctx.market_trend || "").toUpperCase();
  if (ctx.trend_reason === "APPROVED_EXTREME_CATALYST") {
    trendLine = `It ${verb} against the market's direction because the news was extreme and trading was heavy.`;
  } else if (ctx.trend_reason === "APPROVED_IDIOSYNCRATIC_BREAKOUT") {
    trendLine = "It traded in a flat market because this stock was trading far more than normal.";
  } else if (trend === "BULLISH" && isLong) trendLine = "It bought because the market was rising too.";
  else if (trend === "BEARISH" && !isLong) trendLine = "It sold short because the market was falling too.";
  else if (trend === "NEUTRAL") trendLine = "The market was flat, which this playbook allows.";

  const rows: WhyLine[] = [];
  if (ctx.vix != null) {
    rows.push({
      label: "Fear gauge",
      value: `${ctx.vix.toFixed(1)}${ctx.vix_regime ? `, ${levelWord(ctx.vix_regime)}` : ""}${ctx.vix_stale ? " (reading was old)" : ""}: risks ${pctOfUsual(sizing)} of usual`,
    });
  }
  if (ctx.time_phase) {
    rows.push({ label: "Time of day", value: `${PHASE_PLAIN[ctx.time_phase] ?? ctx.time_phase}: ${timeMult < 1 ? "half size" : "full size"}` });
  }
  if (trend) {
    rows.push({ label: "Market direction", value: `${TREND_WORD[trend] ?? "unclear"}` });
  }
  if (ctx.macro) {
    rows.push({ label: "News calendar", value: ctx.macro === "MACRO_CLEAR" ? "No big news release blocking" : "Checked before buying" });
  }
  if (ctx.rs) {
    rows.push({ label: "Stronger than SPY", value: ctx.rs.day && ctx.rs.recent ? "Yes, today and the last 30 minutes" : "Not on both measures" });
  }
  return { sizeLine, stopLine, trendLine, rows };
}

/** Sentence for the "Now" line under an adaptive trade. */
export function stillHeldLine(regimeNow: string | null | undefined): string {
  const keep = "This trade keeps the size it got; its safety exit never moves farther away.";
  return regimeNow ? `Market is ${levelWord(regimeNow)} now. ${keep}` : keep;
}

export interface OrbBoxInputs {
  classification: string | null;
  short_frac: number | null;
  short_bounds: { min: number; max: number } | null;
  risk_usd: number | null;
  breakeven_r: number | null;
}

/** ORB's short box: its own 9:38 check, the risk on this trade, when its stop moves. No stop/target repeat. */
export function orbBoxText(o: OrbBoxInputs): string {
  const pct = (x: number) => `${Math.round(x * 100)}%`;
  const parts: string[] = [];
  if (o.classification === "CALM_TREND") {
    const bal = o.short_frac != null && o.short_bounds
      ? `the breakout list balanced (${pct(o.short_frac)} bets down, needs ${pct(o.short_bounds.min)} to ${pct(o.short_bounds.max)})`
      : "the breakout list balanced";
    parts.push(`Traded after its 9:38 market check: SPY and QQQ data complete and ${bal}.`);
  } else {
    parts.push("Traded after passing its own 9:38 market check (the robot did not keep the numbers).");
  }
  if (o.risk_usd != null) parts.push(`Risk on this trade: ${formatMoney(o.risk_usd)}.`);
  if (o.breakeven_r != null) {
    parts.push(`Its stop moves to its entry price at +${o.breakeven_r}x its risk, and it can close early if the trade fails fast.`);
  }
  return parts.join(" ");
}

// ---------------------------------------------------------------------------
// Overnight holds (NVDA, IREN, HUT overnight), PLAN_2026_09_30_overnight_holds.md section 5.
// Words only. Every number and state comes from the backend's overnight payload or the positions.
// ---------------------------------------------------------------------------

export const OVERNIGHT_IDS = ["overnight_nvda", "overnight_iren", "overnight_hut"];

/** A position that is an overnight hold (backend flag, or its strategy id when the flag is missing). */
export function isOvernightPosition(p: { strategy_id?: string | null; overnight?: boolean | null }): boolean {
  return p.overnight === true || OVERNIGHT_IDS.includes(String(p.strategy_id || "").toLowerCase());
}

const WEEKDAY_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTH_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-10-01" -> "Thu Oct 1" (a calendar date, no time zone shift). Empty when unreadable. */
export function dayLabel(ymd: string | null | undefined): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(ymd || "");
  if (!m) return "";
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return `${WEEKDAY_SHORT[d.getUTCDay()]} ${MONTH_SHORT[d.getUTCMonth()]} ${d.getUTCDate()}`;
}

/** The ET calendar date of an ISO timestamp, "YYYY-MM-DD". */
export function etDateOfIso(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : etDateKey(d);
}

/** Plain words for the backend's skip and wait reasons (overnight_schedule.py). Each is a full sentence. */
export function overnightReasonText(code: string | null | undefined): string {
  const words: Record<string, string> = {
    EARLY_CLOSE: "The market closes early today, and the rule only buys on full days.",
    NOT_TRADING_DAY: "The market is closed today.",
    CALENDAR_NOT_COVERED: "The robot's market calendar does not cover this date.",
    CALENDAR_DISAGREES: "Alpaca's market calendar did not agree with the robot's.",
    ALPACA_CALENDAR_UNAVAILABLE: "Alpaca's market calendar could not be read.",
    NO_0930_BAR: "Today's 9:30 AM price data is missing.",
    DATA_SHORT: "Too much of today's price data is missing.",
    RELAY_UNAVAILABLE: "The price data service did not answer.",
    MODE_OFF: "Overnight holds are switched off.",
    STOCK_OFF: "This stock is switched off.",
    NO_BROKER: "The robot is not connected to Alpaca.",
    OPERATOR_NO_BUY_TONIGHT: "You turned off tonight's buy.",
    EARLIER_HOLD_UNSOLD: "An earlier hold in this stock is not sold yet.",
    HELD_BY_OTHER_STRATEGY: "Another strategy holds this stock.",
    BROKER_MISMATCH: "The robot's positions do not match the Alpaca account.",
    INTENT_NOT_DURABLE: "The robot could not save its plan first.",
    DAY_TRADE_NOT_CLOSED: "A day trade in this stock could not be closed in time.",
    BROKER_NOT_FLAT: "Alpaca still showed shares or an open order in this stock.",
    ACCOUNT_UNAVAILABLE: "The Alpaca account could not be read.",
    NO_PRICE: "There was no recent price for this stock.",
    NO_ROOM: "There was not enough room in the account.",
    BUY_REFUSED: "Alpaca refused the buy.",
    WASH_TRADE_REFUSED: "Alpaca refused the buy because of another open order in this stock.",
    BUYING_POWER_REFUSED: "Alpaca said there was not enough buying power.",
    BROKER_UNREACHABLE: "Alpaca did not answer.",
    MISSED_BUY_WINDOW: "The buy window ended before an order could be sent.",
    AUCTION_NO_FILL: "The closing buy did not fill.",
    MARKET_NO_FILL: "The near close market buy did not fill.",
    CANCELED_AT_ALPACA: "The buy was cancelled in the Alpaca app.",
    BOOK_MORE_THAN_ALPACA: "The robot's book shows more shares of this stock than Alpaca holds. Check the Alpaca app.",
    SYMBOL_CHANGE_UNCLEAR: "This stock had a company change the robot cannot handle alone. Check the Alpaca app.",
    MARGIN_UNKNOWN: "Alpaca's margin rule for this stock could not be read, so the robot assumed the usual 50%.",
  };
  return words[code || ""] || "A required check did not pass.";
}

const MOVE_TOGETHER = "IREN and HUT are both bitcoin miners and move together.";

export interface HoldLineInputs {
  symbol: string;
  shares: number;
  buyPrice: number | null;
  saleDate: string | null; // YYYY-MM-DD
  nights: string | null; // weeknight | weekend | holiday
  needsLook?: boolean;
  bookOnly?: boolean;
}

/** One line per hold. Always names the sale day. */
export function holdLine(h: HoldLineInputs): string {
  if (h.bookOnly) return `Alpaca already sold ${h.symbol}, but the robot's book still shows ${h.shares} shares. Check the Alpaca app.`;
  const bought = h.buyPrice != null ? ` bought at ${formatMoney(h.buyPrice)} near the close` : " bought near the close";
  const day = dayLabel(h.saleDate);
  const over = h.nights === "weekend" ? "Held over the weekend, sells" : h.nights === "holiday" ? "Held over the holiday, sells" : "Sells";
  const sale = day ? `${over} at the 9:30 AM open on ${day}.` : `${over} at the next 9:30 AM open.`;
  let line = `${h.shares} shares of ${h.symbol}${bought}. No stop. ${sale}`;
  if (h.symbol === "IREN" || h.symbol === "HUT") line += ` ${MOVE_TOGETHER}`;
  if (h.needsLook) line += " The robot flagged it for a look. It still sells at the open.";
  return line;
}

export interface TonightRowLike {
  symbol: string;
  enabled: boolean;
  state: string | null;
  buy_date: string | null;
  reason: string | null;
  block: string | null;
  qty: number | null;
}

const BUY_WAITING = ["IDLE", "INTENT", "BUY_SENT"];
const HELD_STATES = ["HELD", "SALE_QUEUED", "SOLD"];

/** From 3:45 PM, what happens to one stock at the close, or why it has no buy tonight.
 * Null when the stock is bought (its hold line says the rest). `today` is the ET date, `etMin` minutes of day. */
export function tonightStatusLine(row: TonightRowLike, o: { modeOn: boolean; noBuyTonight: boolean; today: string; etMin: number }): string | null {
  if (!o.modeOn) return "No buy tonight. Overnight holds are switched off.";
  if (!row.enabled) return "No buy tonight. This stock is switched off.";
  const tonight = row.buy_date === o.today ? row : null;
  if (!tonight || !tonight.state) {
    // an earlier hold still unsold blocks tonight's buy (EARLIER_HOLD_UNSOLD)
    if (row.state && ["HELD", "SALE_QUEUED"].includes(row.state)) return `No buy tonight. ${overnightReasonText("EARLIER_HOLD_UNSOLD")}`;
    if (o.noBuyTonight) return "No buy tonight. You turned it off.";
    if (o.etMin >= 15 * 60 + 50) return "No buy tonight. No order went in by 3:49:30 PM.";
    return "Buys near the 4:00 PM close. The order goes in at 3:59:30 PM.";
  }
  const st = tonight.state;
  if (HELD_STATES.includes(st)) return null;
  if (st === "SKIPPED") return `No buy tonight. ${overnightReasonText(tonight.reason)}`;
  const shares = tonight.qty != null ? `${tonight.qty} shares` : "shares";
  if (st === "BUY_ACCEPTED") {
    return o.etMin < 16 * 60
      ? `Buy for ${shares} was sent near the close. Waiting for Alpaca.`
      : `Waiting for Alpaca to report the buy of ${shares}.`;
  }
  if (BUY_WAITING.includes(st) && tonight.block) {
    return `Not bought yet. ${overnightReasonText(tonight.block)} It keeps checking until 3:59:55 PM.`;
  }
  if (st === "INTENT") return `Buy for ${shares} is prepared. It sends at 3:59:30 PM.`;
  if (st === "BUY_SENT") return `Sending a market buy for ${shares} near the close.`;
  return "Checking tonight's buy.";
}

export interface NoBuyButtonInputs {
  running: boolean;
  modeOn: boolean;
  enabledCount: number;
  tradingDay: boolean;
  active: boolean; // "No overnight buy tonight" is on for today
  tooLate: boolean; // at or after 3:49:30 PM ET
  alreadyStopped: boolean; // a buy tonight was already stopped by the control
  nothingPlanned: boolean; // every switched on stock tonight is skipped or already bought
}

/** Why the "No overnight buy tonight" button (or its undo) cannot be used right now. Null when it can. */
export function noBuyDisabledReason(b: NoBuyButtonInputs): string | null {
  if (!b.running) return "The overnight holds are not running on this robot.";
  if (!b.modeOn || b.enabledCount === 0) return "Overnight holds are switched off, so there is no buy to stop.";
  if (!b.tradingDay) return "The market is closed today, so there is no buy to stop.";
  if (b.tooLate) return "Too late to change tonight. It can only be changed until 3:49:30 PM.";
  if (b.active && b.alreadyStopped) return "Tonight's buy was already stopped and cannot be restarted.";
  if (!b.active && b.nothingPlanned) return "No overnight buy is planned tonight.";
  return null;
}

/** Account card note while holds are held (they are not re-marked until they sell). */
export function overnightBalanceNote(totalAtBuyPrice: number): string {
  return `Includes ${formatMoney(totalAtBuyPrice)} in overnight holds at their buy price. Their real value is known at 9:30 AM.`;
}

/** Red banner while any hold is still unsold after 9:31 AM. */
export function unsoldBannerText(symbols: string[]): string {
  const names = joinNames(symbols.map((s) => `${s} overnight`));
  return symbols.length === 1
    ? `${names} is not sold yet after 9:31 AM, or the robot's book and Alpaca disagree on it. Check the Alpaca app.`
    : `${names} are not sold yet after 9:31 AM, or the robot's book and Alpaca disagree on them. Check the Alpaca app.`;
}

/** The "Right now" sentence part for holds. `saleDate` is the earliest sale day, `pastSale` true once its 9:30 AM passed. */
export function overnightHoldingSentence(count: number, saleDate: string | null, pastSale: boolean): string | null {
  if (count <= 0) return null;
  const what = `${count} overnight ${count === 1 ? "stock" : "stocks"}`;
  if (pastSale) return `Selling ${what} bought at the last close.`;
  const day = dayLabel(saleDate);
  return `Holding ${what} until the 9:30 AM open${day ? ` on ${day}` : ""}.`;
}

/** The Balance card's all-time line, from the full ledger. Null until it has loaded, and when it failed: a line the
 * page cannot back up is left out, never guessed. "Since start" is the account's growth (current minus opening
 * equity); "last trading day" is the newest earlier day that has trades. */
export function ledgerHistoryLine(
  ledger: {
    summary: { current_equity: number; opening_equity: number } | null;
    sessions: { session_date: string; realized_pnl: number; trades_count: number }[];
    error: string | null;
  },
  today: string
): { sinceStart: number; lastDay: { pnl: number; label: string } | null } | null {
  if (ledger.error || !ledger.summary) return null;
  const last = [...ledger.sessions]
    .filter((x) => x.session_date < today && x.trades_count > 0)
    .sort((a, b) => (a.session_date < b.session_date ? 1 : -1))[0];
  return {
    sinceStart: Math.round((ledger.summary.current_equity - ledger.summary.opening_equity) * 100) / 100,
    lastDay: last ? { pnl: last.realized_pnl, label: dayLabel(last.session_date) } : null,
  };
}

// ---------------------------------------------------------------------------
// Needs-a-look (2026-10-04 status strip). One short plain label per thing that already raises a visible alarm
// somewhere on the page. The helpers below are the SAME expressions the alarm components use, so the strip and the
// alarm can never disagree.
// ---------------------------------------------------------------------------

/** Fields of a playbook the alarm helpers read (a structural subset of StrategyState). */
export interface AlarmStrategyLike {
  id: string;
  name?: string;
  orb?: {
    init_error: string | null;
    errors: { alarm?: string; symbol?: string }[];
    alerts?: string[];
    orphans?: { symbol: string; text: string }[];
  } | null;
  tri_engine?: { last_error?: string | null } | null;
}

/** ORB's plain-language alerts (an orphan position is one of them). */
export function orbAlertsOf(s: AlarmStrategyLike): string[] {
  return s.orb?.alerts ?? [];
}

/** "ORB reported a problem" text: its start-up error, or an error that is neither an alert nor an orphan's. */
export function orbProblemOf(s: AlarmStrategyLike): string | null {
  if (!s.orb) return null;
  return (
    s.orb.init_error ||
    (s.orb.errors.some((e) => e.alarm !== "orb_alert" && !(s.orb?.orphans ?? []).some((o) => o.symbol === e.symbol))
      ? "ORB reported a problem; check the paper account."
      : null)
  );
}

/** The tri-engine's last broker error, if any. */
export function triErrorOf(s: AlarmStrategyLike): string | null {
  return s.tri_engine?.last_error ?? null;
}

/** Which kind of playbook a held position belongs to; "none" = not linked to a strategy (HoldingNow's badge). */
export type HoldingKind = "adaptive" | "fixed" | "orb" | "none";

export function holdingKind(id: string | null | undefined): HoldingKind {
  if (id === "orb") return "orb";
  if (id && ADAPTIVE_IDS.includes(id)) return "adaptive";
  if (id && FIXED_PLAN_IDS.includes(id)) return "fixed";
  return "none";
}

/** HoldingNow's "No safety exit set" condition. */
export function hasNoSafetyExit(p: { stop_loss?: number | null }): boolean {
  return (p.stop_loss ?? null) == null;
}

/** HoldingNow's two warnings about a quick trade: "Not linked to a strategy" and "No safety exit set". */
export function positionWarning(p: { stop_loss?: number | null; strategy_id?: string | null }): "no-safety-exit" | "not-linked" | null {
  if (holdingKind(p.strategy_id) === "none") return "not-linked";
  if (hasNoSafetyExit(p)) return "no-safety-exit";
  return null;
}

/** True when the page should say the price feed is down: no feed is connected. An empty list counts as down once
 * data has arrived (the caller only asks after the first frame). */
export function isFeedDown(ingestion: Record<string, string> | null | undefined): boolean {
  const vals = Object.values(ingestion || {});
  return vals.length === 0 || vals.every((v) => v !== "connected");
}

export interface AttentionItem {
  key: string;
  label: string;
}

export interface AttentionInputs {
  connectionState: string;
  feedDown: boolean;
  brokerMismatch: boolean;
  savingProblem: boolean;
  /** Overnight stocks still unsold after 9:31 AM. */
  unsold: string[];
  breakerHit: boolean;
  strategies: AlarmStrategyLike[];
  overnight: {
    initError?: string | null;
    /** Symbols of holds or rows that carry a non-empty needs_look. */
    needsLook: string[];
  };
  /** Today's finished-trades list failed to load (the strip's "Trades today" would read a silent 0). */
  ledgerError: boolean;
  /** The all-time ledger (the Results panel) failed to refresh. */
  resultsError: boolean;
  /** Quick-trade positions (overnight holds excluded). */
  positions: { symbol: string; stop_loss?: number | null; strategy_id?: string | null }[];
}

/** Everything on the page that needs the operator, de-duplicated by key. Fail closed: a condition that throws counts
 * as "needs a look" (key check-failed). A field the backend does not send is NOT an alarm. */
export function collectAttention(i: AttentionInputs): AttentionItem[] {
  const out = new Map<string, string>();
  const add = (key: string, label: string) => { if (!out.has(key)) out.set(key, label); };
  const guard = (fn: () => void) => {
    try {
      fn();
    } catch {
      add("check-failed", "a page check failed");
    }
  };

  guard(() => { if (i.connectionState !== "live") add("connection", "not connected to the robot"); });
  guard(() => { if (i.feedDown) add("feed", "price feed down"); });
  guard(() => { if (i.brokerMismatch) add("mismatch", "positions do not match Alpaca"); });
  guard(() => { if (i.savingProblem) add("saving", "saving problem"); });
  guard(() => { if (i.unsold.length > 0) add("unsold", `${joinNames(i.unsold)} not sold yet`); });
  guard(() => { if (i.breakerHit) add("breaker", "daily loss limit hit"); });
  for (const s of i.strategies) {
    guard(() => {
      const label = s.name || strategyTheme(s.id, s.id).name;
      if (s.orb?.init_error) add("orb-init", "ORB could not start");
      else if (orbProblemOf(s)) add("orb-problem", "ORB reported a problem");
      const orphans = s.orb?.orphans ?? [];
      for (const a of orbAlertsOf(s)) {
        const orphan = orphans.find((o) => o.text === a);
        if (orphan) add(`orb-orphan:${orphan.symbol}`, `${orphan.symbol} left open by ORB`);
        else add("orb-alert", "ORB alert");
      }
      for (const o of orphans) add(`orb-orphan:${o.symbol}`, `${o.symbol} left open by ORB`);
      if (triErrorOf(s)) add(`tri:${s.id}`, `broker issue on ${strategyTheme(s.id, label).name}`);
    });
  }
  guard(() => { if (i.overnight.initError) add("overnight-init", "overnight holds could not start"); });
  guard(() => { for (const sym of i.overnight.needsLook) if (!i.unsold.includes(sym)) add(`overnight-look:${sym}`, `${sym} overnight hold needs a look`); });
  guard(() => { if (i.ledgerError) add("ledger", "today's trades did not load"); });
  guard(() => { if (i.resultsError) add("results", "results did not refresh"); });
  for (const p of i.positions) {
    guard(() => {
      const w = positionWarning(p);
      if (w === "no-safety-exit") add(`unprotected:${p.symbol}`, `${p.symbol} has no safety exit`);
      else if (w === "not-linked") add(`unprotected:${p.symbol}`, `${p.symbol} is not linked to a strategy`);
    });
  }
  return [...out].map(([key, label]) => ({ key, label }));
}

/** The pill's words. */
export function attentionPillText(count: number): string {
  return count === 0 ? "No alarms" : count === 1 ? "1 needs a look" : `${count} need a look`;
}

// ---------------------------------------------------------------------------
// Overnight playbook row and the 3:45 PM handoff (PLAN_2026_10_05_overnight_row_and_handoff.md)
// ---------------------------------------------------------------------------

/** One stock of the overnight payload, the fields the row reads (OvernightRow in types/trading.ts). */
export interface OvnRowLike {
  symbol: string;
  enabled: boolean;
  state: string | null;
  buy_date: string | null;
  sale_date: string | null;
  reason: string | null;
  block: string | null;
  reserved?: boolean;
}

/** One early close of a day trade for a closing buy (payload "x6"). */
export interface OvnX6Like {
  symbol: string;
  date: string;
  qty?: number | null;
  done: boolean;
  strategy_id: string | null;
  filled_at: string | null;
  filled_qty?: number;
}

export interface OvernightRowInputs {
  running: boolean;
  /** mode live */
  modeOn: boolean;
  enabled: string[];
  /** payload "today"; null on a backend without it */
  today: { full_day: boolean; reason: string | null; sale_date: string | null } | null;
  /** fallback when `today` is missing: weekday and not a holiday by the strategies' window */
  tradingDay: boolean;
  todayEt: string;
  etMin: number;
  /** at or after 3:49:30 PM ET today (the page's `tooLate`) */
  tooLate: boolean;
  noBuyActive: boolean;
  rows: OvnRowLike[];
  /** holds held now (not book-only), each with its sale date */
  holds: { symbol: string; saleDate: string | null }[];
  x6: OvnX6Like[];
}

export interface OvnChip { label: string; tone: "sage" | "lavender" | "grey" | "amber"; breathing: boolean }

const LOCKOUT_MIN = 15 * 60 + 45;
const BUY_PREP_MIN = 15 * 60 + 46;
const MARKET_BUY_MIN = 15 * 60 + 59;
const CLOSE_MIN = 16 * 60;
const SALE_QUEUE_MIN = 19 * 60;
const OPEN_MIN = 9 * 60 + 30;
/** Skip reasons that are a choice or the calendar, never a failure: shown grey, not amber. */
const CALM_SKIPS = ["OPERATOR_NO_BUY_TONIGHT", "EARLY_CLOSE", "NOT_TRADING_DAY", "MODE_OFF", "STOCK_OFF"];

export function isCalmSkip(reason: string | null | undefined): boolean {
  return CALM_SKIPS.includes(reason || "");
}

/** Is today a full day the closing buy can happen on (the backend's own calendar when it sends it). */
function fullDay(i: OvernightRowInputs): boolean {
  return i.today ? i.today.full_day : i.tradingDay;
}

function tonightRows(i: OvernightRowInputs): OvnRowLike[] {
  return i.rows.filter((r) => r.enabled && i.enabled.includes(r.symbol) && r.buy_date === i.todayEt);
}

/** "9:30 AM today" / "9:30 AM Tue Oct 6" for a sale date. */
export function saleWhen(saleDate: string | null | undefined, todayEt: string): string {
  if (!saleDate) return "the next 9:30 AM open";
  return saleDate === todayEt ? "9:30 AM today" : `9:30 AM ${dayLabel(saleDate)}`;
}

/** The Overnight row's status chip. Holds come first: a hold over a weekend or with buys off is still held. */
export function overnightChip(i: OvernightRowInputs): OvnChip {
  if (i.holds.length > 0) {
    const first = i.holds.map((h) => h.saleDate).filter((d): d is string => !!d).sort()[0] ?? null;
    if (first && (first < i.todayEt || (first === i.todayEt && i.etMin >= OPEN_MIN))) return { label: "Selling", tone: "amber", breathing: true };
    return { label: `Holding ${i.holds.length} until ${saleWhen(first, i.todayEt)}`, tone: "sage", breathing: true };
  }
  if (!i.running) return { label: "Not running", tone: "grey", breathing: false };
  if (!i.modeOn || i.enabled.length === 0) return { label: "Switched off", tone: "grey", breathing: false };
  if (!fullDay(i)) {
    const r = i.today?.reason;
    if (r === "EARLY_CLOSE") return { label: "No buy today (early close)", tone: "grey", breathing: false };
    if (!r || r === "NOT_TRADING_DAY") return { label: "Market closed", tone: "grey", breathing: false };
    return { label: "No buy today", tone: "amber", breathing: false };
  }
  const t = tonightRows(i);
  if (t.length > 0) {
    const live = t.filter((r) => r.state !== "SKIPPED");
    if (live.length === 0) {
      const calm = t.every((r) => isCalmSkip(r.reason));
      return { label: "No buy tonight", tone: calm ? "grey" : "amber", breathing: false };
    }
    if (live.some((r) => ["IDLE", "INTENT", "BUY_SENT"].includes(r.state || "") && r.block)) {
      return { label: "Not bought yet", tone: "amber", breathing: false };
    }
    if (live.some((r) => r.state === "BUY_ACCEPTED") && i.etMin >= CLOSE_MIN) {
      return { label: "Checking the closing buy", tone: "sage", breathing: true };
    }
    if (live.some((r) => r.state === "INTENT")) {
      return { label: "Buy prepared for 3:59 PM", tone: "sage", breathing: true };
    }
    return { label: "Buying near the close", tone: "sage", breathing: true };
  }
  if (i.noBuyActive) return { label: "No buy tonight", tone: "grey", breathing: false };
  if (i.etMin >= LOCKOUT_MIN + 5) return { label: "No buy tonight", tone: "amber", breathing: false };
  return { label: "Buys near the 4 PM close", tone: "lavender", breathing: false };
}

/** Draw the 3:45 to 4:00 PM handoff stripes on the hours bars: today is a full day and a buy can happen. */
export function showHandoffBand(i: OvernightRowInputs): boolean {
  return i.running && i.modeOn && i.enabled.length > 0 && fullDay(i);
}

/** The note above the playbook rows on the buy day, from 3:45 PM until the buy is booked. Null otherwise. */
export function handoffNote(i: OvernightRowInputs): string | null {
  if (i.etMin < LOCKOUT_MIN) return null;
  const t = tonightRows(i).filter((r) => r.reserved === true && ["IDLE", "INTENT", "BUY_SENT", "BUY_ACCEPTED"].includes(r.state || ""));
  if (t.length === 0) return null;
  const syms = t.map((r) => r.symbol);
  const sale = t.map((r) => r.sale_date).filter((d): d is string => !!d).sort()[0] ?? i.today?.sale_date ?? null;
  const verb = syms.length === 1 ? "is" : "are";
  const them = syms.length === 1 ? "it" : "them";
  return `From 3:45 PM until ${syms.length === 1 ? "it sells" : "they sell"} at ${saleWhen(sale, i.todayEt)}, ${joinNames(syms)} ${verb} saved for Overnight. The day playbooks won't trade ${them}.`;
}

function minutesOfIso(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : etMinutesOfDay(d);
}

/** The note on a day playbook's row when its trade was closed early (X6) for tonight's buy. Today only. */
export function x6NoteFor(strategyId: string, i: OvernightRowInputs): string | null {
  const jobs = i.x6.filter((j) => j.date === i.todayEt && j.strategy_id === strategyId);
  for (const j of jobs) {
    if ((j.filled_qty ?? 0) > 0 && !j.done) {
      if (i.tooLate) continue;
      return `Closing ${j.symbol} now so Overnight can buy it near the close (${j.filled_qty} of ${j.qty ?? "?"} shares closed).`;
    }
    if ((j.filled_qty ?? 0) > 0) {
      const at = j.filled_at ? etTimeLabel(j.filled_at) : "";
      const m = minutesOfIso(j.filled_at);
      const early = m != null && 15 * 60 + 55 - m > 0 ? `, ${15 * 60 + 55 - m} minutes early,` : "";
      return `${j.symbol} closed${at ? ` at ${at}` : ""}${early} so Overnight could buy it near the close.`;
    }
    if (!j.done && i.etMin >= BUY_PREP_MIN && !i.tooLate) return `Closing ${j.symbol} now so Overnight can buy it near the close.`;
  }
  return null;
}

export type StepStatus = "done" | "now" | "next" | "skipped" | "problem";
export interface OvnStep { key: string; time: string; status: StepStatus; text: string }

/** The night the steps describe: tonight's buy day, else the night still held or sold but not released. */
function activeNight(i: OvernightRowInputs): { date: string; rows: OvnRowLike[] } | null {
  const t = tonightRows(i);
  if (t.length > 0) return { date: i.todayEt, rows: t };
  const open = i.rows.filter((r) => r.buy_date && (["HELD", "SALE_QUEUED"].includes(r.state || "") || (r.state === "SOLD" && r.reserved)));
  if (open.length === 0) return null;
  const date = open.map((r) => r.buy_date as string).sort()[0];
  return { date, rows: i.rows.filter((r) => r.buy_date === date) };
}

/** Seven steps of one night, each judged from what the robot did for that night. Null when no night is planned or open. */
export function overnightSteps(i: OvernightRowInputs, nameOf: (strategyId: string) => string): OvnStep[] | null {
  const night = activeNight(i);
  const planning = !night && i.running && i.modeOn && i.enabled.length > 0 && fullDay(i) && i.etMin < LOCKOUT_MIN;
  if (!night && !planning) return null;
  const date = night?.date ?? i.todayEt;
  const rows = night?.rows ?? [];
  const past = date < i.todayEt; // the buy day is over
  const at = (min: number) => past || (date === i.todayEt && i.etMin >= min);
  const live = rows.filter((r) => r.state !== "SKIPPED");
  const skipped = rows.filter((r) => r.state === "SKIPPED");
  const stopped = planning && i.noBuyActive;
  const allSkipped = (rows.length > 0 && live.length === 0) || stopped;
  const skipStatus: StepStatus = allSkipped && (stopped || skipped.every((r) => isCalmSkip(r.reason))) ? "skipped" : "problem";
  const skipText = stopped ? overnightReasonText("OPERATOR_NO_BUY_TONIGHT") : overnightReasonText(skipped[0]?.reason);
  const has = (states: string[]) => live.filter((r) => states.includes(r.state || ""));
  const names = (rs: OvnRowLike[]) => joinNames(rs.map((r) => r.symbol));
  const sale = live.map((r) => r.sale_date).filter((d): d is string => !!d).sort()[0] ?? (planning ? i.today?.sale_date ?? null : null);
  const syms = joinNames(night ? rows.map((r) => r.symbol) : i.enabled);
  const steps: OvnStep[] = [];

  steps.push({ key: "lockout", time: "3:45 PM", status: at(LOCKOUT_MIN) ? "done" : "next", text: `Day playbooks stop opening ${syms}.` });

  const jobs = i.x6.filter((j) => j.date === date);
  const closed = jobs.filter((j) => (j.filled_qty ?? 0) > 0 && j.done);
  const closing = jobs.filter((j) => !j.done);
  let x6: OvnStep;
  if (closed.length > 0) {
    x6 = { key: "x6", time: "3:46 PM", status: "done", text: closed.map((j) => `${j.strategy_id ? `${nameOf(j.strategy_id)}'s ` : ""}${j.symbol} trade was closed early${j.filled_at ? ` at ${etTimeLabel(j.filled_at)}` : ""}.`).join(" ") };
  } else if (closing.length > 0 && date === i.todayEt && !i.tooLate) {
    x6 = { key: "x6", time: "3:46 PM", status: "now", text: `Closing the day trade in ${joinNames(closing.map((j) => j.symbol))}.` };
  } else if (closing.length > 0) {
    x6 = { key: "x6", time: "3:46 PM", status: "problem", text: `The day trade in ${joinNames(closing.map((j) => j.symbol))} was not fully closed in time.` };
  } else if (at(BUY_PREP_MIN) && !allSkipped) {
    x6 = { key: "x6", time: "3:46 PM", status: "done", text: "No day trade needed closing." };
  } else {
    x6 = { key: "x6", time: "3:46 PM", status: allSkipped ? skipStatus : "next", text: allSkipped ? "No early close, no buy tonight." : "A day trade in one of them is closed early." };
  }
  steps.push(x6);

  if (allSkipped) steps.push({ key: "last", time: "3:49:30 PM", status: skipStatus, text: "Nothing to stop tonight." });
  else if (past || i.tooLate) steps.push({ key: "last", time: "3:49:30 PM", status: "done", text: "Too late to stop tonight's buy." });
  else if (at(BUY_PREP_MIN)) steps.push({ key: "last", time: "3:49:30 PM", status: "now", text: "Last moment to tap No overnight buy tonight." });
  else steps.push({ key: "last", time: "3:49:30 PM", status: "next", text: "Last moment to tap No overnight buy tonight." });

  const sent = has(["BUY_SENT", "BUY_ACCEPTED", "HELD", "SALE_QUEUED", "SOLD"]);
  const blocked = live.filter((r) => ["IDLE", "INTENT", "BUY_SENT"].includes(r.state || "") && r.block);
  if (allSkipped) steps.push({ key: "send", time: "3:59:30 PM", status: skipStatus, text: `No buy sent. ${skipText}` });
  else if (blocked.length > 0 && !past) steps.push({ key: "send", time: "3:59:30 PM", status: "problem", text: `${names(blocked)} not sent yet. ${overnightReasonText(blocked[0].block)}` });
  else if (sent.length > 0 && sent.length === live.length) steps.push({ key: "send", time: "3:59:30 PM", status: "done", text: `Market buy sent for ${names(sent)} near the close.` });
  else if (live.length > 0 && at(MARKET_BUY_MIN)) steps.push({ key: "send", time: "3:59:30 PM", status: "now", text: `Sending the market buy for ${names(live)}.` });
  else steps.push({ key: "send", time: "3:59:30 PM", status: "next", text: "The market buy goes in near the close." });

  const bought = has(["HELD", "SALE_QUEUED", "SOLD"]);
  const notBought = live.filter((r) => !bought.includes(r));
  if (allSkipped) steps.push({ key: "bought", time: "4:00 PM", status: skipStatus, text: "No buy tonight." });
  else if (bought.length > 0) steps.push({ key: "bought", time: "4:00 PM", status: "done", text: `Bought ${names(bought)} near the close.${notBought.length > 0 ? ` ${names(notBought)} not bought yet.` : ""}${skipped.length > 0 ? ` ${names(skipped)} skipped.` : ""}` });
  else if (has(["BUY_ACCEPTED"]).length > 0 && (past || i.tooLate)) steps.push({ key: "bought", time: "4:00 PM", status: "now", text: "Waiting for Alpaca to confirm the buy." });
  else steps.push({ key: "bought", time: "4:00 PM", status: "next", text: "Bought near the close." });

  const queued = has(["SALE_QUEUED", "SOLD"]);
  if (allSkipped) steps.push({ key: "queue", time: "7:00 PM", status: skipStatus, text: "No sale to queue." });
  else if (queued.length > 0 && queued.length === bought.length) steps.push({ key: "queue", time: "7:00 PM", status: "done", text: "The sale waits at Alpaca for the open, even if the robot restarts." });
  else if (queued.length > 0) steps.push({ key: "queue", time: "7:00 PM", status: "now", text: `Sale queued for ${names(queued)}. ${names(bought.filter((r) => !queued.includes(r)))} not queued yet.` });
  else if (bought.length > 0 && (past || at(SALE_QUEUE_MIN))) steps.push({ key: "queue", time: "7:00 PM", status: "now", text: "Sending the sale for the open to Alpaca." });
  else steps.push({ key: "queue", time: "7:00 PM", status: "next", text: "The sale for the open goes to Alpaca." });

  const sold = has(["SOLD"]);
  const when = !sale ? "" : sale === i.todayEt ? " today" : ` on ${dayLabel(sale)}`;
  const soldTime = sale && sale !== i.todayEt ? `9:30 AM ${dayLabel(sale).split(" ")[0]}` : "9:30 AM";
  if (allSkipped) steps.push({ key: "sold", time: soldTime, status: skipStatus, text: "Nothing to sell." });
  else if (sold.length > 0 && sold.length === live.length && sold.every((r) => r.reserved === false)) steps.push({ key: "sold", time: soldTime, status: "done", text: `Sold at the open. ${names(sold)} went back to the day playbooks.` });
  else if (sold.length > 0 && sold.length < live.length) steps.push({ key: "sold", time: soldTime, status: "now", text: `Sold ${names(sold)}. ${names(live.filter((r) => !sold.includes(r)))} not sold yet.` });
  else if (sold.length > 0) steps.push({ key: "sold", time: soldTime, status: "now", text: "Sold, waiting for Alpaca to show it flat." });
  else steps.push({ key: "sold", time: soldTime, status: "next", text: `Sold at the open${when}. Then the day playbooks can trade ${night ? "them" : "these stocks"} again.` });
  return steps;
}
