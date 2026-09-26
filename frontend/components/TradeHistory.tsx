"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { AlertTriangle, ChevronRight, RefreshCw, ShieldCheck, X } from "lucide-react";
import {
  PersistenceStatus,
  TradeHistoryResponse,
  TradeRecord,
} from "@/types/trading";
import { apiBase } from "@/lib/apiBase";
import { companyName, dedupeTrades, formatMoney, formatSignedMoney, historyDateLabel, strategyTheme } from "@/lib/plain";

type HistoryRange = "today" | "yesterday" | "7d" | "all";

interface TradeHistoryProps {
  open: boolean;
  onClose: () => void;
  ledgerRevision: number;
  streamPersistence: PersistenceStatus;
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) + " ET";
}

export default function TradeHistory({ open, onClose, ledgerRevision, streamPersistence }: TradeHistoryProps) {
  const [range, setRange] = useState<HistoryRange>("7d");
  const [data, setData] = useState<TradeHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<TradeRecord | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const requestGeneration = useRef(0);
  const reduceMotion = useReducedMotion();

  const load = useCallback(async (cursor?: string, append = false) => {
    const generation = ++requestGeneration.current;
    append ? setLoadingMore(true) : setLoading(true);
    try {
      const params = new URLSearchParams({ range, limit: "100" });
      if (cursor) params.set("cursor", cursor);
      const response = await fetch(`${apiBase()}/api/trades?${params.toString()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`History request failed (${response.status})`);
      const next = (await response.json()) as TradeHistoryResponse;
      if (generation !== requestGeneration.current) return;
      setData((previous) => (append && previous ? { ...next, items: dedupeTrades([...previous.items, ...next.items]) } : next));
      setError(null);
    } catch (requestError) {
      if (generation !== requestGeneration.current) return;
      setError(requestError instanceof Error ? requestError.message : "History unavailable");
    } finally {
      if (generation === requestGeneration.current) {
        setLoading(false);
        setLoadingMore(false);
      }
    }
  }, [range]);

  useEffect(() => {
    if (open) void load();
    return () => { requestGeneration.current += 1; };
  }, [load, ledgerRevision, open]);

  useEffect(() => {
    if (!open) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") (selected ? setSelected(null) : onClose());
    };
    window.addEventListener("keydown", onKeyDown);
    closeButtonRef.current?.focus();
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, selected, onClose]);

  if (!open) return null;

  const persistence = data?.persistence || streamPersistence;
  const durable = persistence.status === "durable";
  const disabled = persistence.status === "disabled";
  const summary = data?.summary;
  const sessions = new Map((data?.sessions ?? data?.recovered_sessions ?? []).map((session) => [session.session_date, session]));
  const tradesByDay = new Map<string, TradeRecord[]>();
  for (const trade of dedupeTrades(data?.items ?? []).sort((a, b) => Date.parse(b.closed_at) - Date.parse(a.closed_at))) {
    const trades = tradesByDay.get(trade.session_date) ?? [];
    trades.push(trade);
    tradesByDay.set(trade.session_date, trades);
  }
  const days = [...new Set([...sessions.keys(), ...tradesByDay.keys()])].sort().reverse();

  return (
    <AnimatePresence>
      <motion.div
        role="dialog"
        aria-modal="true"
        aria-labelledby="trade-history-title"
        initial={reduceMotion ? false : { opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={reduceMotion ? undefined : { opacity: 0 }}
        className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/40 sm:p-4"
        onClick={onClose}
      >
        <motion.div
          initial={reduceMotion ? false : { y: 40, opacity: 0 }}
          animate={{ y: 0, opacity: 1 }}
          exit={reduceMotion ? undefined : { y: 40, opacity: 0 }}
          onClick={(e) => e.stopPropagation()}
          className="w-full sm:max-w-2xl max-h-[90vh] overflow-y-auto rounded-t-[28px] sm:rounded-[28px] border border-line bg-ground p-5 sm:p-7"
        >
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 id="trade-history-title" className="font-display text-xl sm:text-2xl font-semibold text-ink">
                Trade history
              </h2>
              <span
                className="mt-1 inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold"
                style={durable ? { background: "#E4EFE7", color: "#2F6B4C" } : { background: "#F6E3DA", color: "#8F4424" }}
              >
                {durable ? <ShieldCheck className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
                {durable ? "Saving normally" : disabled ? "Saving is off" : "Saving problem"}
              </span>
            </div>
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="rounded-full border border-line p-2 hover:bg-white"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="mt-4 flex items-center justify-between gap-3">
            <div className="inline-flex rounded-xl border border-line bg-white p-1" aria-label="History range">
              {(["today", "yesterday", "7d", "all"] as HistoryRange[]).map((value) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={range === value}
                  onClick={() => { if (range !== value) { setData(null); setSelected(null); setRange(value); } }}
                  className={`min-h-[36px] rounded-lg px-3 text-xs font-bold uppercase tracking-wide ${
                    range === value ? "bg-darkcard text-white" : "text-muted hover:text-ink"
                  }`}
                >
                  {value === "7d" ? "7 days" : value === "today" ? "Today" : value === "yesterday" ? "Yesterday" : "All"}
                </button>
              ))}
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 sm:grid-cols-4 gap-2">
            <Tile label="Balance" value={summary ? formatMoney(summary.current_equity) : "—"} />
            <Tile
              label="Result"
              value={summary ? formatSignedMoney(summary.realized_pnl) : "—"}
              tone={summary && summary.realized_pnl < 0 ? "loss" : "gain"}
            />
            <Tile label="Trades" value={summary ? String(summary.trades_count) : "—"} />
            <Tile label="Fees" value={summary ? (summary.fees_known ? formatMoney(summary.fees) : "Unavailable") : "—"} />
          </div>

          <div aria-live="polite" className="mt-4 space-y-2">
            {error && (
              <div className="flex items-center justify-between gap-3 rounded-2xl border p-3 text-sm" style={{ borderColor: "#EFD8C5", background: "#FAF0E6", color: "#7A3E1D" }}>
                <span>{error}. Previously loaded trades are still shown.</span>
                <button type="button" onClick={() => void load()} aria-label="Retry" className="rounded-lg p-1.5 hover:bg-white">
                  <RefreshCw className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            {loading && !data ? (
              <div className="py-8 text-center text-sm text-muted">Loading…</div>
            ) : days.length > 0 ? (
              <div className="space-y-3" data-testid="history-days">
                {days.map((day) => {
                  const session = sessions.get(day);
                  const trades = tradesByDay.get(day) ?? [];
                  const count = session?.trades_count ?? trades.length;
                  const pnl = session?.realized_pnl ?? trades.reduce((sum, trade) => sum + trade.realized_pnl, 0);
                  return (
                    <details key={day} open={range === "today" || range === "yesterday"} className="rounded-2xl border border-line bg-white p-3">
                      <summary className="cursor-pointer text-sm font-semibold text-ink">
                        <span>{historyDateLabel(day)}</span>
                        <span className="mt-1 flex items-center justify-between pl-4 text-xs font-normal text-muted">
                          <span>{count} finished {count === 1 ? "trade" : "trades"}{session?.aggregate_only ? " · daily total only" : ""}</span>
                          <span className="font-bold tabular-nums" style={{ color: pnl < 0 ? "#8F4424" : "#2F6B4C" }}>{formatSignedMoney(pnl)}</span>
                        </span>
                      </summary>
                      {session?.aggregate_only ? (
                        <p className="mt-3 text-xs text-muted">The daily total was recovered. Individual trade details are unavailable.</p>
                      ) : count === 0 ? (
                        <p className="mt-3 text-xs text-muted">No finished trades on this day.</p>
                      ) : null}
                      <div className="mt-3 space-y-1.5">
                        {trades.map((trade) => (
                          <button
                            type="button"
                            key={trade.trade_id}
                            onClick={() => setSelected(trade)}
                            className="grid w-full grid-cols-[1fr_auto] sm:grid-cols-[1fr_1fr_auto] items-center gap-3 rounded-2xl border border-line bg-white p-3 text-left hover:bg-[#FAF8F2]"
                          >
                            <div>
                              <div className="text-sm font-semibold text-ink">
                                {companyName(trade.symbol)} <span className="text-xs font-medium text-muted">{trade.symbol}</span>
                              </div>
                              <time className="text-xs text-muted" dateTime={trade.closed_at}>{formatTime(trade.closed_at)}</time>
                            </div>
                            <div className="hidden sm:block text-xs text-muted">{strategyTheme(trade.strategy_id, trade.strategy_id).name}</div>
                            <div className="flex items-center gap-1.5">
                              <span
                                className="rounded-full px-2.5 py-1 text-xs font-bold tabular-nums"
                                style={trade.realized_pnl >= 0 ? { background: "#E4EFE7", color: "#2F6B4C" } : { background: "#F6E3DA", color: "#8F4424" }}
                              >
                                {formatSignedMoney(trade.realized_pnl)}
                              </span>
                              <ChevronRight className="h-3.5 w-3.5 text-muted" />
                            </div>
                          </button>
                        ))}
                      </div>
                      {!session?.aggregate_only && count > trades.length && <p className="mt-3 text-xs text-muted">More trades from this day are available. Use “Load older trades” below.</p>}
                    </details>
                  );
                })}
              </div>
            ) : (
              <div className="py-8 text-center text-sm text-muted">No finished trades in this range.</div>
            )}

            {data?.next_cursor && (
              <button
                type="button"
                disabled={loadingMore}
                onClick={() => void load(data.next_cursor || undefined, true)}
                className="min-h-[44px] w-full rounded-xl border border-line bg-white text-sm font-semibold text-[#3E3A57] disabled:opacity-50"
              >
                {loadingMore ? "Loading…" : "Load older trades"}
              </button>
            )}
          </div>

          <AnimatePresence>
            {selected && (
              <motion.div
                initial={reduceMotion ? false : { opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={reduceMotion ? undefined : { opacity: 0 }}
                className="fixed inset-0 z-[60] flex items-end sm:items-center justify-center bg-black/50 sm:p-4"
                onClick={() => setSelected(null)}
              >
                <div
                  role="dialog"
                  aria-modal="true"
                  onClick={(e) => e.stopPropagation()}
                  className="w-full sm:max-w-lg rounded-t-3xl sm:rounded-3xl border border-line bg-white p-5"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <h3 className="text-lg font-bold text-ink">{companyName(selected.symbol)} &middot; {selected.side === "LONG" ? "bet it goes up" : "bet it goes down"}</h3>
                      <p className="text-xs text-muted">{selected.symbol}</p>
                    </div>
                    <button type="button" onClick={() => setSelected(null)} aria-label="Close trade details" className="rounded-full border border-line p-2">
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                  <div className="mt-4 grid grid-cols-2 gap-2 text-sm">
                    <Detail label={selected.side === "SHORT" ? "Sold at" : "Bought at"} value={formatMoney(selected.avg_entry_price)} />
                    <Detail label={selected.side === "SHORT" ? "Bought back at" : "Sold at"} value={formatMoney(selected.avg_exit_price)} />
                    <Detail label="Shares" value={`${selected.quantity}`} />
                    <Detail label="Result" value={formatSignedMoney(selected.realized_pnl)} />
                    <Detail label="Playbook" value={strategyTheme(selected.strategy_id, selected.strategy_id).name} />
                    <Detail label="Why it exited" value={selected.exit_reason.replaceAll("_", " ")} />
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </motion.div>
      </motion.div>
    </AnimatePresence>
  );
}

function Tile({ label, value, tone }: { label: string; value: string; tone?: "gain" | "loss" }) {
  return (
    <div className="rounded-2xl border border-line bg-white p-3">
      <span className="block text-xs text-muted">{label}</span>
      <span className="tabular-nums text-sm font-bold" style={tone === "loss" ? { color: "#8F4424" } : tone === "gain" ? { color: "#2F6B4C" } : { color: "#1D1A33" }}>
        {value}
      </span>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-line bg-[#FAF8F2] p-3">
      <span className="block text-xs text-muted">{label}</span>
      <span className="font-semibold text-ink">{value}</span>
    </div>
  );
}
