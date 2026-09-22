"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  AlertTriangle,
  ChevronRight,
  Database,
  History,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import {
  PersistenceStatus,
  RecoveredSessionSummary,
  TradeHistoryResponse,
  TradeRecord,
} from "@/types/trading";

type HistoryRange = "today" | "7d" | "all";

interface TradeHistoryProps {
  ledgerRevision: number;
  streamPersistence: PersistenceStatus;
}

function apiBase(): string {
  if (typeof window === "undefined") return "";
  if (window.location.port === "3005") {
    return `${window.location.protocol}//${window.location.hostname}:8005`;
  }
  return "";
}

function money(value: number, signed = false): string {
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`.replace("$-", "-$");
}

function formatTime(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : parsed.toLocaleString("en-US", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

export default function TradeHistory({ ledgerRevision, streamPersistence }: TradeHistoryProps) {
  const [range, setRange] = useState<HistoryRange>("7d");
  const [data, setData] = useState<TradeHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<TradeRecord | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const closeButtonRef = useRef<HTMLButtonElement | null>(null);
  const dialogOpenerRef = useRef<HTMLElement | null>(null);

  const load = useCallback(async (cursor?: string, append = false) => {
    append ? setLoadingMore(true) : setLoading(true);
    try {
      const params = new URLSearchParams({ range, limit: "25" });
      if (cursor) params.set("cursor", cursor);
      const response = await fetch(`${apiBase()}/api/trades?${params.toString()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`History request failed (${response.status})`);
      const next = (await response.json()) as TradeHistoryResponse;
      setData((previous) => append && previous
        ? { ...next, items: [...previous.items, ...next.items] }
        : next);
      setError(null);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "History unavailable");
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [range]);

  useEffect(() => {
    void load();
  }, [load, ledgerRevision]);

  useEffect(() => {
    if (!selected) return;
    dialogOpenerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null);
      if (event.key === "Tab") {
        event.preventDefault();
        closeButtonRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    closeButtonRef.current?.focus();
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      dialogOpenerRef.current?.focus();
    };
  }, [selected]);

  const persistence = data?.persistence || streamPersistence;
  const durable = persistence.status === "durable";
  const disabled = persistence.status === "disabled";
  const summary = data?.summary;

  return (
    <section className="px-4 pt-2" aria-labelledby="trade-history-title">
      <div className="rounded-3xl bg-white/[0.03] border border-white/[0.08] p-4 backdrop-blur-xl space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-1.5 text-[10px] font-semibold text-apple-purple uppercase tracking-wider">
              <Database className="w-3.5 h-3.5" /> Account Ledger
            </div>
            <h2 id="trade-history-title" className="text-lg font-bold text-white tracking-tight">Trade History</h2>
          </div>
          <div className={`px-2.5 py-1.5 rounded-full border text-[10px] font-semibold flex items-center gap-1.5 ${
            durable
              ? "bg-apple-green/10 border-apple-green/20 text-apple-green"
              : "bg-amber-500/10 border-amber-500/20 text-amber-300"
          }`}>
            {durable ? <ShieldCheck className="w-3 h-3" /> : <AlertTriangle className="w-3 h-3" />}
            {durable ? "Durable" : disabled ? "Disabled" : "Recovery Halt"}
          </div>
        </div>

        <div className="flex items-center justify-between gap-3">
          <div className="inline-flex p-1 rounded-xl bg-black/30 border border-white/[0.06]" aria-label="History range">
            {(["today", "7d", "all"] as HistoryRange[]).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={range === value}
                onClick={() => setRange(value)}
                className={`px-3 py-1.5 rounded-lg text-[10px] font-bold uppercase tracking-wider focus:outline-none focus:ring-2 focus:ring-apple-purple/60 ${
                  range === value ? "bg-white/10 text-white" : "text-neutral-500 hover:text-neutral-300"
                }`}
              >
                {value === "7d" ? "7 Days" : value}
              </button>
            ))}
          </div>
          <span className="text-[10px] text-neutral-500 num-tabular" title={persistence.last_checkpoint_at || undefined}>
            {persistence.last_checkpoint_at ? `Saved ${formatTime(persistence.last_checkpoint_at)}` : "No checkpoint"}
          </span>
        </div>

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <SummaryTile label="Equity" value={summary ? money(summary.current_equity) : "—"} />
          <SummaryTile
            label="Realized P&L"
            value={summary ? `${summary.realized_pnl >= 0 ? "+" : "−"}$${Math.abs(summary.realized_pnl).toFixed(2)}` : "—"}
            tone={summary && summary.realized_pnl < 0 ? "negative" : "positive"}
          />
          <SummaryTile label="Closed Trades" value={summary ? String(summary.trades_count) : "—"} />
          <SummaryTile label="Recorded Fees" value={summary ? (summary.fees_known ? money(summary.fees) : "Unavailable") : "—"} />
        </div>

        <div aria-live="polite" className="space-y-2">
          {error && (
            <div className="p-3 rounded-2xl bg-amber-500/10 border border-amber-500/20 text-amber-200 text-xs flex items-center justify-between gap-3">
              <span>{error}. Previously loaded ledger data is retained.</span>
              <button type="button" onClick={() => void load()} aria-label="Retry trade history" className="p-1.5 rounded-lg hover:bg-white/10 focus:ring-2 focus:ring-amber-300">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {data?.recovered_sessions.map((session) => (
            <RecoveredSession key={session.session_date} session={session} />
          ))}

          {loading && !data ? (
            <div className="py-8 text-center text-xs text-neutral-500">Loading durable ledger…</div>
          ) : data && data.items.length > 0 ? (
            <div className="space-y-2 max-h-80 overflow-y-auto no-scrollbar pr-1">
              {data.items.map((trade, index) => (
                <motion.button
                  type="button"
                  key={trade.trade_id}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.18, delay: Math.min(index, 8) * 0.02 }}
                  onClick={() => setSelected(trade)}
                  className="w-full p-2.5 rounded-2xl bg-white/[0.02] hover:bg-white/[0.05] border border-white/[0.04] grid grid-cols-[1fr_auto] sm:grid-cols-[1fr_1.2fr_1fr_auto] items-center gap-3 text-left focus:outline-none focus:ring-2 focus:ring-apple-purple/50"
                >
                  <div>
                    <div className="flex items-center gap-1.5">
                      <span className="font-bold text-white text-xs">{trade.symbol}</span>
                      <span className="text-[9px] uppercase tracking-wider text-neutral-500">{trade.side}</span>
                    </div>
                    <time className="text-[10px] text-neutral-500" dateTime={trade.closed_at}>{formatTime(trade.closed_at)}</time>
                  </div>
                  <div className="hidden sm:block text-[10px] text-neutral-400 num-tabular">
                    ${trade.avg_entry_price.toFixed(2)} → ${trade.avg_exit_price.toFixed(2)} · {trade.quantity} sh
                  </div>
                  <div className="hidden sm:block text-[10px] text-neutral-500 uppercase tracking-wider">
                    {trade.strategy_id.replaceAll("_", " ")}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`text-xs font-bold num-tabular ${trade.realized_pnl >= 0 ? "text-apple-green" : "text-apple-red"}`}>
                      {trade.realized_pnl >= 0 ? "Profit +" : "Loss −"}${Math.abs(trade.realized_pnl).toFixed(2)}
                    </span>
                    <ChevronRight className="w-3.5 h-3.5 text-neutral-600" />
                  </div>
                </motion.button>
              ))}
            </div>
          ) : !data?.recovered_sessions.length ? (
            <div className="py-8 text-center text-xs text-neutral-500">
              No completed trades in this range.
            </div>
          ) : null}

          {data?.next_cursor && (
            <button
              type="button"
              disabled={loadingMore}
              onClick={() => void load(data.next_cursor || undefined, true)}
              className="w-full py-2 rounded-xl bg-white/[0.04] hover:bg-white/[0.08] border border-white/[0.06] text-[11px] font-semibold text-neutral-300 disabled:opacity-50 focus:ring-2 focus:ring-apple-purple/50"
            >
              {loadingMore ? "Loading…" : "Load older trades"}
            </button>
          )}
        </div>
      </div>

      <AnimatePresence>
        {selected && (
          <motion.div
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/75 backdrop-blur-xl sm:p-4"
            onClick={() => setSelected(null)}
          >
            <motion.div
              role="dialog" aria-modal="true" aria-labelledby="trade-detail-title"
              initial={{ y: 40, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: 40, opacity: 0 }}
              transition={{ type: "spring", stiffness: 350, damping: 32 }}
              onClick={(event) => event.stopPropagation()}
              className="w-full sm:max-w-lg rounded-t-3xl sm:rounded-3xl bg-[#0f0f15] border border-white/10 p-5 shadow-2xl max-h-[85vh] overflow-y-auto no-scrollbar"
            >
              <div className="flex items-start justify-between gap-3">
                <div>
                  <span className="text-[10px] uppercase tracking-widest text-apple-purple">Durable Trade Record</span>
                  <h3 id="trade-detail-title" className="text-xl font-bold text-white">{selected.symbol} · {selected.side}</h3>
                  <p className="text-xs text-neutral-500">{selected.trade_id}</p>
                </div>
                <button ref={closeButtonRef} type="button" onClick={() => setSelected(null)} aria-label="Close trade details" className="p-2 rounded-full bg-white/[0.06] hover:bg-white/[0.12] focus:ring-2 focus:ring-apple-purple/60">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="grid grid-cols-2 gap-2 mt-4 text-xs">
                <Detail label="Entry" value={`$${selected.avg_entry_price.toFixed(4)}`} />
                <Detail label="Exit" value={`$${selected.avg_exit_price.toFixed(4)}`} />
                <Detail label="Quantity" value={`${selected.quantity} shares`} />
                <Detail label="Realized" value={`${selected.realized_pnl >= 0 ? "+" : "−"}$${Math.abs(selected.realized_pnl).toFixed(2)}`} />
                <Detail label="Strategy" value={selected.strategy_id.replaceAll("_", " ")} />
                <Detail label="Exit Reason" value={selected.exit_reason.replaceAll("_", " ")} />
              </div>
              <div className="mt-4 pt-4 border-t border-white/[0.06] space-y-2">
                <h4 className="text-xs font-bold text-white">Execution legs</h4>
                {selected.fill_legs?.map((fill) => (
                  <div key={fill.fill_id} className="p-2.5 rounded-xl bg-white/[0.03] border border-white/[0.06] flex items-center justify-between gap-2 text-[10px]">
                    <span className="text-neutral-300">{fill.side} {fill.qty} @ ${fill.price.toFixed(4)}</span>
                    <time className="text-neutral-500" dateTime={fill.timestamp}>{formatTime(fill.timestamp)}</time>
                  </div>
                ))}
              </div>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}

function SummaryTile({ label, value, tone }: { label: string; value: string; tone?: "positive" | "negative" }) {
  return (
    <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
      <span className="text-[10px] uppercase tracking-wider text-neutral-400 block">{label}</span>
      <span className={`text-sm font-bold num-tabular ${tone === "negative" ? "text-apple-red" : tone === "positive" ? "text-apple-green" : "text-white"}`}>{value}</span>
    </div>
  );
}

function RecoveredSession({ session }: { session: RecoveredSessionSummary }) {
  return (
    <div className="p-3 rounded-2xl bg-apple-purple/10 border border-apple-purple/20 text-xs">
      <div className="flex items-center justify-between gap-3">
        <span className="flex items-center gap-1.5 font-semibold text-white"><History className="w-3.5 h-3.5 text-apple-purple" /> {session.session_date} recovered summary</span>
        <span className={`font-bold num-tabular ${session.realized_pnl >= 0 ? "text-apple-green" : "text-apple-red"}`}>{session.realized_pnl >= 0 ? "+" : "−"}${Math.abs(session.realized_pnl).toFixed(2)}</span>
      </div>
      <p className="text-[10px] text-neutral-400 mt-1 leading-relaxed">
        {session.trades_count} trades · {money(session.opening_equity)} → {money(session.closing_equity)} · Aggregate recovery only; the lost container did not preserve individual executions.
      </p>
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06]"><span className="text-[10px] uppercase tracking-wider text-neutral-500 block">{label}</span><span className="font-semibold text-neutral-200 num-tabular">{value}</span></div>;
}
