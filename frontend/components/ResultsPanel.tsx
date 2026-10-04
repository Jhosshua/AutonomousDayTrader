"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ChevronDown, ShieldCheck, X } from "lucide-react";
import { PersistenceStatus, RecoveredSessionSummary, TradeRecord } from "@/types/trading";
import type { LedgerState } from "@/hooks/useTodayLedger";
import { companyName, etTimeLabel, formatMoney, formatSignedMoney, historyDateLabel, strategyTheme } from "@/lib/plain";

type Grouping = "day" | "playbook";

/** Days shown before "Show older days" (today is always shown on top of these). */
const RECENT_DAYS = 5;
/** Trades shown in an open group before "Show all N". */
const FIRST_TRADES = 6;

interface Group {
  key: string;
  title: string;
  sub: string;
  total: number;
  dot: string;
  trades: TradeRecord[];
  /** Total number of trades the group claims (can be more than the loaded rows). */
  count: number;
  aggregateOnly: boolean;
  isToday: boolean;
}

interface ResultsPanelProps {
  ledger: LedgerState;
  /** ET date key of today ("YYYY-MM-DD"). */
  today: string;
  streamPersistence: PersistenceStatus;
}

const round2 = (n: number) => Math.round(n * 100) / 100;
const trades = (n: number) => `${n} finished ${n === 1 ? "trade" : "trades"}`;

function sortNewestFirst(items: TradeRecord[]): TradeRecord[] {
  return [...items].sort((a, b) => Date.parse(b.closed_at) - Date.parse(a.closed_at));
}

/** One group per day: every session with trades, plus today even when it has none. Newest first. */
export function groupByDay(items: TradeRecord[], sessions: RecoveredSessionSummary[], today: string): Group[] {
  const byDay = new Map<string, TradeRecord[]>();
  for (const t of sortNewestFirst(items)) byDay.set(t.session_date, [...(byDay.get(t.session_date) ?? []), t]);
  const sess = new Map(sessions.map((s) => [s.session_date, s]));
  const days = new Set<string>([...byDay.keys(), ...sess.keys(), today]);
  const out: Group[] = [];
  for (const day of [...days].sort().reverse()) {
    const rows = byDay.get(day) ?? [];
    const s = sess.get(day);
    const count = s?.trades_count ?? rows.length;
    if (count === 0 && day !== today) continue;
    const total = s?.realized_pnl ?? round2(rows.reduce((sum, t) => sum + t.realized_pnl, 0));
    out.push({
      key: day,
      title: historyDateLabel(day),
      sub: `${trades(count)}${s?.aggregate_only ? " · daily total only" : ""}`,
      total,
      dot: total < 0 ? "#C2300F" : total > 0 ? "#0A7D53" : "#8A91B0",
      trades: rows,
      count,
      aggregateOnly: !!s?.aggregate_only,
      isToday: day === today,
    });
  }
  return out;
}

/** One group per playbook: the loaded trades plus the per-playbook aggregates of recovered days. Best first. */
export function groupByPlaybook(items: TradeRecord[], recovered: RecoveredSessionSummary[]): Group[] {
  const byId = new Map<string, { rows: TradeRecord[]; aggTrades: number; aggPnl: number }>();
  const slot = (id: string) => byId.get(id) ?? { rows: [], aggTrades: 0, aggPnl: 0 };
  for (const t of items) {
    const g = slot(t.strategy_id);
    g.rows.push(t);
    byId.set(t.strategy_id, g);
  }
  for (const s of recovered) {
    for (const [id, agg] of Object.entries(s.strategies ?? {})) {
      const g = slot(id);
      g.aggTrades += agg.trades_count;
      g.aggPnl += agg.realized_pnl;
      byId.set(id, g);
    }
  }
  const out: Group[] = [];
  for (const [id, g] of byId) {
    const theme = strategyTheme(id, id);
    const wins = g.rows.filter((t) => t.realized_pnl > 0).length;
    const losses = g.rows.filter((t) => t.realized_pnl < 0).length;
    const count = g.rows.length + g.aggTrades;
    out.push({
      key: id,
      title: theme.name,
      sub: `${count} ${count === 1 ? "trade" : "trades"} · ${wins} won, ${losses} lost${g.aggTrades > 0 ? ` · ${g.aggTrades} only as daily totals` : ""}`,
      total: round2(g.rows.reduce((sum, t) => sum + t.realized_pnl, 0) + g.aggPnl),
      dot: theme.bar,
      trades: sortNewestFirst(g.rows),
      count,
      aggregateOnly: false,
      isToday: false,
    });
  }
  return out.sort((a, b) => b.total - a.total);
}

function totalColour(total: number): string {
  return total < 0 ? "#C2300F" : total > 0 ? "#0A7D53" : "#5B6283";
}

/** Two halves around a centre tick. Width is |total| over the largest |total| shown (never below 1 dollar), at
 * least 2 px when not zero. Decorative: the signed number beside it carries the meaning. */
function DivergingBar({ total, max, index }: { total: number; max: number; index: number }) {
  const pct = total === 0 ? 0 : Math.max(2, (Math.abs(total) / Math.max(1, max)) * 100);
  const loss = total < 0;
  return (
    <span className="relative block h-2 w-full" aria-hidden="true" data-testid="results-bar" data-total={total}>
      <span className="absolute inset-y-0 left-0 w-1/2 overflow-hidden rounded-l-full bg-line" />
      <span className="absolute inset-y-0 right-0 w-1/2 overflow-hidden rounded-r-full bg-line" />
      {total !== 0 && (
        <span
          className={`${loss ? "grow-r" : "grow-l"} absolute inset-y-0 block ${loss ? "right-1/2 rounded-l-full" : "left-1/2 rounded-r-full"}`}
          style={{
            width: `max(2px, ${pct / 2}%)`,
            background: loss ? "#C2300F" : "#0A7D53",
            animationDelay: `${400 + index * 40}ms`,
          }}
        />
      )}
      <span className="absolute inset-y-[-2px] left-1/2 w-px -translate-x-1/2" style={{ background: "#5B6283" }} />
    </span>
  );
}

function TradeRow({ trade, byDay, onOpen }: { trade: TradeRecord; byDay: boolean; onOpen: (t: TradeRecord, el: HTMLElement) => void }) {
  const theme = strategyTheme(trade.strategy_id, trade.strategy_id);
  const won = trade.realized_pnl >= 0;
  const isLong = trade.side === "LONG";
  return (
    <button
      type="button"
      onClick={(e) => onOpen(trade, e.currentTarget)}
      data-testid="results-trade"
      data-trade-id={trade.trade_id}
      className="grid min-h-[44px] w-full grid-cols-[32px_minmax(0,1fr)_auto] items-center gap-2.5 border-t border-line px-3 py-1 text-left transition-colors hover:bg-[#F6F8FE] focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[#2B4BFF]"
    >
      <div className="flex h-8 w-8 items-center justify-center rounded-xl text-base font-bold" style={{ background: theme.track, color: theme.ink }} aria-hidden="true">
        {isLong ? "↑" : "↓"}
      </div>
      <div className="flex min-w-0 flex-col">
        <div className="text-sm font-semibold text-ink">
          {companyName(trade.symbol)} <span className="text-xs font-medium text-muted">{trade.symbol}</span>
        </div>
        <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs text-muted">
          {byDay ? (
            <span className="rounded-md px-2 py-0.5 text-xs font-semibold" style={{ background: theme.tint, color: theme.ink }}>
              {theme.name}
            </span>
          ) : (
            <span className="whitespace-nowrap">{historyDateLabel(trade.session_date)}</span>
          )}
          <span className="whitespace-nowrap">{isLong ? "bet it goes up" : "bet it goes down"}</span>
          <time className="tabular-nums" dateTime={trade.closed_at}>{etTimeLabel(trade.closed_at)}</time>
        </div>
      </div>
      <div
        className="whitespace-nowrap rounded-full px-2.5 py-1 text-sm font-bold tabular-nums"
        style={won ? { background: "#E9F8F0", color: "#0A7D53" } : { background: "#FFEFEA", color: "#C2300F" }}
      >
        {won ? "+" : "-"}
        {formatMoney(Math.abs(trade.realized_pnl))}
      </div>
    </button>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-line bg-[#F6F8FE] p-3">
      <span className="block text-xs text-muted">{label}</span>
      <span className="font-semibold text-ink">{value}</span>
    </div>
  );
}

/** The trade detail, a native modal dialog: focus trap and Escape come with it. */
function TradeDialog({ trade, onClose }: { trade: TradeRecord; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement | null>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      data-testid="trade-detail"
      aria-labelledby="trade-detail-title"
      onClose={onClose}
      onClick={(e) => { if (e.target === ref.current) ref.current?.close(); }}
      className="m-auto w-[min(32rem,calc(100vw-24px))] rounded-3xl border border-line bg-white p-5 text-ink backdrop:bg-black/50"
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 id="trade-detail-title" className="text-lg font-bold text-ink">{companyName(trade.symbol)} &middot; {trade.side === "LONG" ? "bet it goes up" : "bet it goes down"}</h3>
          <p className="text-xs text-muted">{trade.symbol}</p>
        </div>
        <button
          type="button"
          onClick={() => ref.current?.close()}
          aria-label="Close trade details"
          className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-full border border-line"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
        <Detail label={trade.side === "SHORT" ? "Sold at" : "Bought at"} value={formatMoney(trade.avg_entry_price)} />
        <Detail label={trade.side === "SHORT" ? "Bought back at" : "Sold at"} value={formatMoney(trade.avg_exit_price)} />
        <Detail label="Shares" value={`${trade.quantity}`} />
        <Detail label="Result" value={formatSignedMoney(trade.realized_pnl)} />
        <Detail label="Playbook" value={strategyTheme(trade.strategy_id, trade.strategy_id).name} />
        <Detail label="Why it exited" value={trade.exit_reason.replaceAll("_", " ")} />
      </div>
    </dialog>
  );
}

export default function ResultsPanel({ ledger, today, streamPersistence }: ResultsPanelProps) {
  const [grouping, setGrouping] = useState<Grouping>("day");
  // null = nobody has touched a row in this grouping: the default group is open
  const [openKeys, setOpenKeys] = useState<Set<string> | null>(null);
  const [showAllKeys, setShowAllKeys] = useState<Set<string>>(new Set());
  const [showOlder, setShowOlder] = useState(false);
  const [selected, setSelected] = useState<TradeRecord | null>(null);
  const returnFocus = useRef<HTMLElement | null>(null);

  const { items, sessions, recoveredSessions, summary, loading, error, truncated } = ledger;

  const groups = useMemo(
    () => (grouping === "day" ? groupByDay(items, sessions.length ? sessions : recoveredSessions, today) : groupByPlaybook(items, recoveredSessions)),
    [grouping, items, sessions, recoveredSessions, today]
  );

  const defaultKey = useMemo(() => {
    if (grouping === "playbook") return groups[0]?.key ?? null;
    const todayGroup = groups.find((g) => g.isToday && g.count > 0);
    return todayGroup?.key ?? groups.find((g) => g.count > 0)?.key ?? null;
  }, [grouping, groups]);

  const isOpen = (key: string) => (openKeys ? openKeys.has(key) : key === defaultKey);
  const toggle = (key: string) => {
    const next = new Set(openKeys ?? (defaultKey ? [defaultKey] : []));
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setOpenKeys(next);
  };
  const switchTo = (g: Grouping) => {
    if (g === grouping) return;
    setGrouping(g);
    setOpenKeys(null);
    setShowAllKeys(new Set());
    setShowOlder(false);
  };
  const close = () => {
    setSelected(null);
    returnFocus.current?.focus();
  };

  // By day: today plus the newest few days with trades; the rest behind "Show older days".
  let visible = groups;
  let hiddenDays = 0;
  if (grouping === "day") {
    const todayG = groups.filter((g) => g.isToday);
    const rest = groups.filter((g) => !g.isToday);
    visible = showOlder ? groups : [...todayG, ...rest.slice(0, RECENT_DAYS)];
    hiddenDays = rest.length - Math.min(rest.length, RECENT_DAYS);
  }
  const max = Math.max(0, ...visible.map((g) => Math.abs(g.total)));

  const wins = items.filter((t) => t.realized_pnl > 0).length;
  const losses = items.filter((t) => t.realized_pnl < 0).length;
  const dailyOnly = summary && summary.trades_count > items.length ? summary.trades_count - items.length : 0;

  const durable = streamPersistence.status === "durable";
  const disabled = streamPersistence.status === "disabled";
  const firstLoad = loading && items.length === 0 && sessions.length === 0;
  const empty = !loading && items.length === 0 && sessions.length === 0 && recoveredSessions.length === 0;

  return (
    <section className="rise flex flex-col rounded-[22px] border border-line bg-white" style={{ animationDelay: "300ms" }} data-testid="results-panel">
      <div className="flex flex-col gap-0.5 px-4 pt-1.5">
        <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
            <h2 className="font-display text-lg font-semibold text-ink">Results</h2>
            <span
              className="inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
              style={durable ? { background: "#E9F8F0", color: "#0A7D53" } : { background: "#FFF4DB", color: "#8A4B00" }}
            >
              {durable ? <ShieldCheck className="h-3 w-3" aria-hidden="true" /> : <AlertTriangle className="h-3 w-3" aria-hidden="true" />}
              {durable ? "Saving normally" : disabled ? "Saving is off" : "Saving problem"}
            </span>
          </div>
          <div className="inline-flex rounded-xl border border-line bg-white" role="group" aria-label="Group results by">
            {([
              { id: "day", label: "By day", testid: "results-tab-day" },
              { id: "playbook", label: "By playbook", testid: "results-tab-playbook" },
            ] as { id: Grouping; label: string; testid: string }[]).map(({ id: g, label, testid }) => (
              <button
                key={g}
                type="button"
                aria-pressed={grouping === g}
                onClick={() => switchTo(g)}
                data-testid={testid}
                className={`min-h-[44px] rounded-lg px-2.5 text-[13px] font-semibold transition-colors ${grouping === g ? "bg-darkcard text-white" : "text-muted hover:text-ink"}`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="text-xs text-muted" data-testid="results-summary">
          {items.length > 0 && (
            <span>
              {trades(items.length)} · {wins} won, {losses} lost{dailyOnly > 0 ? ` · ${dailyOnly} more on daily-total-only days` : ""}
            </span>
          )}
        </div>
      </div>

      <div aria-live="polite" className="mt-1 flex flex-col pb-1">
        {error && (
          <div className="px-4 py-1.5 text-xs text-muted" data-testid="results-error">Couldn&apos;t refresh results. Showing the last ones loaded.</div>
        )}
        {firstLoad ? (
          <div className="px-4 py-4 text-center text-sm text-muted">Loading results…</div>
        ) : empty ? (
          <div className="px-4 py-4 text-sm text-muted">No finished trades yet.</div>
        ) : (
          <>
            {truncated && (
              <div className="px-4 pb-1 text-xs text-muted" data-testid="results-truncated">Playbook totals cover the most recent 2,000 trades. Day totals are complete.</div>
            )}
            {visible.map((g, index) => {
              const open = isOpen(g.key);
              const rows = showAllKeys.has(g.key) ? g.trades : g.trades.slice(0, FIRST_TRADES);
              const panelId = `results-panel-${grouping}-${g.key}`;
              return (
                <div key={g.key} className="border-t border-line first:border-t-0" data-testid="results-group" data-key={g.key}>
                  <h3>
                    <button
                      type="button"
                      onClick={() => toggle(g.key)}
                      aria-expanded={open}
                      aria-controls={open ? panelId : undefined}
                      className="grid min-h-[44px] w-full grid-cols-[10px_minmax(0,1fr)_72px_auto_16px] items-center gap-x-2.5 px-4 py-1 text-left transition-colors hover:bg-[#F6F8FE] focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[#2B4BFF] sm:grid-cols-[10px_minmax(0,1fr)_96px_auto_16px]"
                    >
                      <span className="h-2.5 w-2.5 rounded-full" style={{ background: g.dot }} aria-hidden="true" />
                      <span className="min-w-0">
                        <span className="block text-sm font-semibold text-ink">{g.title}</span>
                        <span className="block text-xs text-muted">{g.sub}</span>
                      </span>
                      <DivergingBar total={g.total} max={max} index={index} />
                      <span className="text-right text-sm font-bold tabular-nums" style={{ color: totalColour(g.total) }} data-testid="results-total">
                        {g.total === 0 ? formatMoney(0) : formatSignedMoney(g.total)}
                      </span>
                      <ChevronDown className="h-4 w-4 text-muted transition-transform duration-200" style={{ transform: open ? "rotate(180deg)" : undefined }} aria-hidden="true" />
                    </button>
                  </h3>
                  {open && (
                    <div id={panelId} className="open-in">
                      {grouping === "day" && g.aggregateOnly ? (
                        <p className="px-4 pb-3 text-xs text-muted">The daily total was recovered. Individual trade details are unavailable.</p>
                      ) : grouping === "day" && g.isToday && g.count === 0 ? (
                        <p className="px-4 pb-3 text-xs text-muted">No finished trades today.</p>
                      ) : (
                        <>
                          {rows.map((t) => (
                            <TradeRow key={t.trade_id} trade={t} byDay={grouping === "day"} onOpen={(tr, el) => { returnFocus.current = el; setSelected(tr); }} />
                          ))}
                          {g.trades.length > FIRST_TRADES && !showAllKeys.has(g.key) && (
                            <button
                              type="button"
                              onClick={() => setShowAllKeys(new Set([...showAllKeys, g.key]))}
                              className="min-h-[44px] w-full border-t border-line px-4 text-left text-sm font-semibold text-darkcard hover:bg-[#F6F8FE]"
                            >
                              Show all {g.trades.length}
                            </button>
                          )}
                          {grouping === "day" && g.count > g.trades.length && (
                            <p className="border-t border-line px-4 py-2 text-xs text-muted">Older trades from this day are not loaded.</p>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {grouping === "day" && hiddenDays > 0 && !showOlder && (
              <button
                type="button"
                onClick={() => setShowOlder(true)}
                className="min-h-[44px] w-full border-t border-line px-4 text-left text-sm font-semibold text-darkcard hover:bg-[#F6F8FE]"
              >
                Show older days
              </button>
            )}
          </>
        )}
      </div>
      {selected && <TradeDialog trade={selected} onClose={close} />}
    </section>
  );
}
