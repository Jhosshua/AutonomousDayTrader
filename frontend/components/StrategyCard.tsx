"use client";

import PlaybookIcon from "./gut/PlaybookIcon";
import { useState } from "react";
import { ChevronDown, Sunrise, Waves, Zap, Undo2, CornerDownRight } from "lucide-react";
import { apiBase } from "@/lib/apiBase";
import { StrategyState } from "@/types/trading";
import {
  StrategyLedgerAgg,
  etMinutesOfDay,
  formatSignedMoney,
  isWithinSession,
  rangesToSegments,
  axisPct,
  orbAlertsOf,
  orbProblemOf,
  strategyNoteLine,
  strategyTheme,
  windowToChip,
  formatMoney,
  etTimeLabel,
  trancheName,
  planStatusText,
  trendBlockText,
  triErrorOf,
} from "@/lib/plain";


interface StrategyCardProps {
  strategy: StrategyState;
  ledgerAgg?: StrategyLedgerAgg;
  weekResult?: number;
  showPro: boolean;
  /** Overnight on: the hours bar gets a night part (9:30 to 4 PM on 0 to 88%). */
  nightAxis?: boolean;
  /** Draw the 3:45 to 4:00 PM handoff stripes. */
  handoffBand?: boolean;
  /** X6: this playbook's trade was closed early for tonight's overnight buy. */
  x6Note?: string | null;
}

/** Row grid shared with StrategyTable's column header so the hours bars line up. Phone: name / result /
 * chevron on line 1, status + hours bar on line 2, today's note on line 3. Desktop: name / status / hours bar /
 * result on line 1, today's note under status and bar on line 2 (full width, so long notes stay short). */
export const ROW_GRID =
  "grid grid-cols-[148px_minmax(0,1fr)_auto_16px] gap-x-3 lg:grid-cols-[minmax(0,1fr)_150px_minmax(0,1.2fr)_84px_16px] lg:gap-x-4";
/** Content that sits under the status and hours-bar columns on desktop, full width on phones. */
const UNDER_ROW = "col-span-4 col-start-1 lg:col-span-3 lg:col-start-2";

const CHIP_STYLES: Record<string, { bg: string; fg: string }> = {
  sage: { bg: "#FFFFFF", fg: "#0B5A3C" },
  lavender: { bg: "#FFFFFF", fg: "#4A2AB5" },
  grey: { bg: "#FFFFFF", fg: "#5B6283" },
  amber: { bg: "#FFFFFF", fg: "#8A4B00" },
  terracotta: { bg: "#FFFFFF", fg: "#C2300F" },
};

function OrbOrphanResolve({ symbol }: { symbol: string }) {
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  const resolve = async () => {
    if (!armed) {
      setArmed(true);
      setMsg(`Only after you closed ${symbol} and cancelled ORB's orders on it at Alpaca. Tap again to confirm.`);
      return;
    }
    setArmed(false);
    setBusy(true);
    try {
      const res = await fetch(`${apiBase()}/api/orb/resolve-orphan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbol }),
      });
      const body = await res.json().catch(() => ({}));
      setMsg(res.ok ? `${symbol} cleared from the bot's book.` : String(body.detail || "Refused."));
    } catch {
      setMsg("Could not reach the bot. Try again.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="mt-2">
      <button type="button" onClick={resolve} disabled={busy} data-testid="orb-resolve-orphan"
        className="min-h-[44px] rounded-lg border border-[#C2300F]/60 bg-white px-3 py-1.5 text-xs font-semibold text-[#C2300F]">
        {busy ? "Checking Alpaca…" : armed ? `Confirm: clear ${symbol}` : `I closed ${symbol} at Alpaca: clear it`}
      </button>
      {msg && <div className="mt-1 text-xs font-normal">{msg}</div>}
    </div>
  );
}

/** The night part of an hours bar (after 4 PM to the next 9:30 AM, not to scale). */
export function NightPart() {
  return (
    <span
      className="absolute inset-y-0 right-0 block rounded-r-full"
      style={{ left: `${axisPct(16 * 60, true)}%`, background: "#DFE3F0" }}
      data-testid="night-part"
      aria-hidden="true"
    />
  );
}

/** The 3:45 to 4:00 PM handoff stripes on an hours bar (night axis). */
export function HandoffBand() {
  const left = axisPct(15 * 60 + 45, true);
  return (
    <span
      className="absolute -top-1 -bottom-1 block rounded-sm"
      style={{ left: `${left}%`, width: `${axisPct(16 * 60, true) - left}%`, background: "repeating-linear-gradient(135deg, #8A91B0 0 2px, transparent 2px 5px)" }}
      data-testid="handoff-band"
      aria-hidden="true"
    />
  );
}

export default function StrategyCard({ strategy, ledgerAgg, weekResult, showPro, nightAxis = false, handoffBand = false, x6Note = null }: StrategyCardProps) {
  const [open, setOpen] = useState(false);
  const theme = strategyTheme(strategy.id, strategy.name);
  const win = strategy.window;
  const baseChip = windowToChip(win);
  // ORB's off/shadow states arrive as a long blocker or a generic "limited" state; keep the chip short and true.
  const chip = strategy.orb?.mode === "off" && !["MANAGING", "MARKET_CLOSED", "PAUSED"].includes(win?.state ?? "")
    ? { label: "Switched off", tone: "grey" as const, breathing: false }
    : strategy.orb?.mode === "shadow" && win?.state === "LIMITED"
      ? { label: "Watching only (shadow)", tone: "sage" as const, breathing: true }
      : baseChip;
  const chipStyle = CHIP_STYLES[chip.tone] || CHIP_STYLES.grey;
  const resting = win?.state === "DONE_FOR_DAY" || win?.state === "PAUSED" || win?.state === "MARKET_CLOSED";

  const segments = rangesToSegments(win?.ranges, nightAxis);
  const nowMin = etMinutesOfDay();
  const showNow = (win?.trading_day ?? true) && isWithinSession(nowMin);
  const nowLeft = axisPct(nowMin, nightAxis);

  // ORB: the bottom line includes its open (unrealized) P&L, not only closed trades
  const orbOpen = strategy.orb ? (strategy.orb.unrealized_pnl ?? 0) : 0;
  const pnl = (ledgerAgg?.realized_pnl ?? strategy.daily_pnl ?? 0) + orbOpen;
  const tradesCount = (ledgerAgg?.trades_count ?? strategy.trades_count ?? 0) + (strategy.orb?.open_trades.length ?? 0);
  const pnlColor = pnl > 0 ? "#0A7D53" : pnl < 0 ? "#C2300F" : "#5B6283";

  // F9: an early-close day note must surface even when there's already a signals/orders lead.
  const earlyCloseNote = win?.notes?.find((n) => n.toLowerCase().includes("early"));
  // ORB: its current step is already in the ORB box above, and its decision rows (sat out, shadow,
  // no decision) are not "chances skipped", so the note only counts real trades.
  const orbOrders = strategy.decisions?.orders_today ?? 0;
  const note = strategy.orb
    ? (orbOrders > 0 ? `${orbOrders} ORB trade${orbOrders === 1 ? "" : "s"} today.` : "No ORB trades today.")
    : strategyNoteLine(
    strategy.decisions,
    earlyCloseNote ? `${win?.market_text ?? ""} ${earlyCloseNote}`.trim() : win?.market_text,
    win?.notes?.[0]
  );

  // Alarms render OUTSIDE the collapsible details: never hidden behind a click.
  // (the same helpers feed the status strip's "needs a look" list, so the two can never disagree)
  const orbAlerts = orbAlertsOf(strategy);
  const orbProblem = orbProblemOf(strategy);
  const triError = triErrorOf(strategy);
  const hasAlarm = orbAlerts.length > 0 || !!orbProblem || !!triError;
  // Live status that must not hide behind a click either (plan section 8, P1).
  const orb = strategy.orb;
  const trendOff = strategy.id === "vwap_pullback" && strategy.mode === "off";
  const trendAddonsOff = strategy.id === "vwap_pullback" && !strategy.addons_enforced;
  const or15Holding = strategy.or15?.phase === "HOLDING";
  const hasStatus = !!orb?.step || (orb?.open_trades.length ?? 0) > 0 || trendOff || trendAddonsOff || or15Holding || !!x6Note;
  const detailsId = `strategy-details-${strategy.id}`;

  return (
    <article className="border-t border-line" data-testid={`strategy-row-${strategy.id}`}>
      <h3>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={open ? detailsId : undefined}
        data-testid="strategy-row-toggle"
        className={`${ROW_GRID} min-h-[52px] w-full items-center gap-y-1 px-4 py-2 text-left transition-colors hover:bg-[#F6F8FE] focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-[#2B4BFF]`}
      >
        <span className="col-span-2 col-start-1 row-start-1 flex min-w-0 items-center gap-2.5 lg:col-span-1">
          <span
            className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg"
            style={{ background: theme.bar, opacity: resting ? 0.6 : 1 }}
            aria-hidden="true"
          >
            <PlaybookIcon name={strategy.id} className="h-5 w-5 text-white" />
          </span>
          <span className="font-display text-[15px] font-semibold leading-tight" style={{ color: theme.ink }} data-testid="strategy-name">
            {theme.name}
          </span>
        </span>

        <span
          className="col-start-1 row-start-2 inline-flex items-center gap-1.5 justify-self-start rounded-full px-2.5 py-1 text-xs font-semibold lg:col-start-2 lg:row-start-1"
          style={{ background: theme.tint, color: chipStyle.fg }}
          data-testid="window-badge"
        >
          <span
            className={chip.breathing ? "breathe inline-block h-2 w-2 rounded-full" : "inline-block h-2 w-2 rounded-full"}
            style={{ background: resting || chip.tone === "grey" ? "#8A91B0" : theme.bar }}
          />
          {chip.label}
        </span>

        <span className="relative col-span-3 col-start-2 row-start-2 block h-2 rounded-full lg:col-span-1 lg:col-start-3 lg:row-start-1" style={{ background: theme.track }} data-testid="strategy-window">
          {segments.map((seg, i) => (
            <span
              key={i}
              className="grow absolute inset-y-0 block rounded-md"
              style={{ left: `${seg.left}%`, width: `${seg.width}%`, background: theme.bar, opacity: resting ? 0.5 : 1 }}
            />
          ))}
          {nightAxis && <NightPart />}
          {handoffBand && <HandoffBand />}
          {showNow && (
            <span
              className="breathe absolute -top-1 block h-4 w-[3px] rounded-sm"
              style={{ left: `${nowLeft}%`, background: "#0E1330" }}
            />
          )}
        </span>

        <span className={`${UNDER_ROW} ${open ? "" : "line-clamp-2"} row-start-3 text-[13px] leading-snug text-[#2A3150] lg:row-start-2`} data-testid="strategy-decisions">
          {note}
        </span>

        <span className="col-start-3 row-start-1 text-right text-sm font-bold tabular-nums lg:col-start-4" style={{ color: pnlColor }} data-testid="strategy-pnl">
          {tradesCount > 0 ? formatSignedMoney(pnl) : "$0"}
          {weekResult != null && <span className="block text-[10px] font-medium text-muted" data-testid="strategy-week-result">7d {formatSignedMoney(weekResult)}</span>}
        </span>

        <ChevronDown
          className="col-start-4 row-start-1 h-4 w-4 text-muted transition-transform lg:col-start-5"
          style={{ transform: open ? "rotate(180deg)" : undefined }}
          aria-hidden="true"
        />
      </button>
      </h3>

      {hasStatus && (
        <div className={`${ROW_GRID} px-4 pb-2.5`}>
        <div className={`${UNDER_ROW} flex flex-col gap-1.5 text-[13px] leading-snug text-[#2A3150]`} data-testid="strategy-status">
          {x6Note && <div className="font-semibold" data-testid="x6-note">{x6Note}</div>}
          {trendOff && <div className="font-semibold">New entries switched off</div>}
          {trendAddonsOff && <div>Extra flow, spread and prior-volume checks switched off.</div>}
          {or15Holding && <div>{strategy.or15?.mode === "offline_raw_open" ? "Fixed safety exit and target in this replay." : strategy.or15?.protection_confirmed ? "Safety exit and target held at the broker." : "Confirming protection with the broker."}</div>}
          {orb?.step && <div>{orb.step}</div>}
          {orb?.open_trades.map((t) => (
            <div key={t.symbol} className="rounded-lg px-3 py-1.5" style={{ background: theme.tint, color: theme.ink }}>
              <span className="font-semibold">{t.direction === "long" ? "Bought" : "Sold short"} {t.symbol} · {Math.abs(t.qty)} shares</span>
              <div>Safety exit {t.stop != null ? formatMoney(t.stop) : "unknown"} · target {t.target != null ? formatMoney(t.target) : "none"} (held at Alpaca)</div>
              {t.r != null && <div>Now {t.r >= 0 ? "+" : ""}{t.r.toFixed(2)}× its risk{t.breakeven_locked ? " · stop moved to the entry" : ""}</div>}
              {t.exit_requested && <div className="font-semibold">Closing: {t.exit_requested}</div>}
            </div>
          ))}
        </div>
        </div>
      )}

      {hasAlarm && (
        <div className={`${ROW_GRID} px-4 pb-3`}>
        <div className={`${UNDER_ROW} flex flex-col gap-2`}>
          {orbAlerts.map((a) => {
            const orphan = (strategy.orb?.orphans ?? []).find((o) => o.text === a);
            return (
              <div key={a} role="alert" className="break-words rounded-lg border border-[#C2300F]/60 bg-white p-2 text-sm font-semibold text-[#C2300F]" data-testid="orb-alert">
                {a}
                {orphan && <OrbOrphanResolve symbol={orphan.symbol} />}
              </div>
            );
          })}
          {orbProblem && (
            <div role="alert" className="break-words rounded-lg border border-[#C2300F]/30 bg-white p-2 text-sm text-[#C2300F]" data-testid="orb-init-error">
              {orbProblem}
              {showPro && (strategy.orb?.errors.length ?? 0) > 0 && <div className="mt-1 text-xs">{strategy.orb?.errors.map((e) => e.alarm || e.kind).join(", ")}</div>}
            </div>
          )}
          {triError && (
            <div role="alert" className="break-words rounded-lg border border-[#C2300F]/30 bg-white p-2 text-sm text-[#C2300F]" data-testid="tri-engine-broker-issue">
              Broker issue: check the paper account. Order management will keep retrying.
              {showPro && <div className="mt-1 text-xs">{triError}</div>}
            </div>
          )}
        </div>
        </div>
      )}

      {open && (
        <div id={detailsId} className="grid gap-3 px-4 pb-4 pt-1 lg:grid-cols-2 lg:pl-[54px]" data-testid="strategy-details">
          <div className="flex flex-col gap-2">
            <div className="text-[11px] font-semibold uppercase tracking-wide" style={{ color: theme.ink }}>How it works</div>
            <p className="text-sm leading-relaxed text-[#2A3150]">{theme.what}</p>
            {showPro && (
              <div className="flex flex-col gap-1 self-start rounded-lg px-2.5 py-1.5 text-xs font-semibold" style={{ background: theme.tint, color: theme.ink }}>
                <span>Pro name: {strategy.name}</span>
                <span className="font-normal">
                  Win rate: {ledgerAgg ? `${Math.round((ledgerAgg.wins / Math.max(1, ledgerAgg.trades_count)) * 100)}%` : `${Math.round((strategy.win_rate ?? 0) * 100)}%`}
                </span>
                {win?.blockers && win.blockers.length > 0 && <span className="font-normal">Blocked by: {win.blockers.join(", ")}</span>}
              </div>
            )}
          </div>
          <div className="flex flex-col gap-2">
            {strategy.id === "vwap_pullback" && (
              <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={{ background: theme.tint, color: theme.ink }} data-testid="trend-details">
                {strategy.mode !== "off" && <div className="font-semibold">Paper account · Morning entries</div>}
                {strategy.addons_enforced && <div>Flow, spread and prior-volume checks enforced.</div>}
                {win?.notes?.slice(1).map((line) => <div key={line} className="mt-1">{line}</div>)}
                <details className="mt-2">
                  <summary className="flex min-h-[44px] cursor-pointer items-center font-semibold">Latest refused setup by stock</summary>
                  {Object.keys(strategy.last_block_by_symbol || {}).length === 0 ? (
                    <div className="mt-1">No refused setups recorded this session.</div>
                  ) : (
                    <ul className="mt-2 space-y-2">
                      {Object.entries(strategy.last_block_by_symbol || {}).sort(([a], [b]) => a.localeCompare(b)).map(([symbol, block]) => (
                        <li key={symbol}><span className="font-semibold">{symbol}</span> · {etTimeLabel(block.bar)} ET<br />{trendBlockText(block.event)}</li>
                      ))}
                    </ul>
                  )}
                </details>
              </div>
            )}
            {strategy.tri_engine && (
              <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={{ background: theme.tint, color: theme.ink }} data-testid="tri-engine-details">
                <div className="font-semibold">{strategy.tri_engine.mode === "offline_raw_open" ? "Offline replay" : "Paper account"} · {strategy.tri_engine.quantity ? `${strategy.tri_engine.quantity} shares` : "Size follows the risk budget"}</div>
                <div>{strategy.tri_engine.symbol === "TSLA" ? "Buys until 11:30 AM · Bets on a drop until 11 AM" : "Buys until noon · Bets on a drop until 11:30 AM"} ET</div>
                <div>{strategy.tri_engine.symbol === "TSLA"
                  ? (showPro ? "Half at 1.5R / 3 hours · Half at 2R / 4 hours" : "Half aims for 1.5× its risk within 3 hours, half for 2× within 4 hours")
                  : (showPro ? "Full position at 2R / 3 hours" : "Aims for 2× its risk within 3 hours")}</div>
                {strategy.tri_engine.risk_budget != null && <div>Risk budget: {formatMoney(strategy.tri_engine.risk_budget)}</div>}
                {strategy.tri_engine.tranches.map((t, i, all) => (
                  <div key={t.id} className="mt-2 border-t pt-2" style={{ borderColor: theme.track }}>
                    <span className="font-semibold">{showPro ? `${t.target_r}R part` : trancheName(i, all.length)} · {t.qty - t.closed_qty} of {t.qty} shares open</span>
                    <div>{strategy.tri_engine?.side === "SHORT" ? "Buys back" : "Sells"} at {formatMoney(t.target)} or at {etTimeLabel(t.exit_due)} ET</div>
                  </div>
                ))}
                {showPro && strategy.tri_engine.reason && <div className="mt-2 break-words">{planStatusText(strategy.tri_engine.reason)}</div>}
              </div>
            )}
            {strategy.orb && (
              <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={{ background: theme.tint, color: theme.ink }} data-testid="orb-details">
                <div className="font-semibold">{strategy.orb.mode_text}</div>
                <div>{strategy.orb.hours} ET</div>
                {(strategy.orb.realized_pnl !== 0 || strategy.orb.unrealized_pnl !== 0) && (
                  <div className="mt-2">Today: {formatSignedMoney(strategy.orb.realized_pnl)} closed{strategy.orb.open_trades.length > 0 ? `, ${formatSignedMoney(strategy.orb.unrealized_pnl)} open` : ""}</div>
                )}
              </div>
            )}
            {strategy.or15 && (
              <div className="rounded-xl px-3 py-2 text-sm leading-relaxed" style={{ background: theme.tint, color: theme.ink }} data-testid="or15-details">
                <span className="font-semibold">1 share · {strategy.or15.mode === "offline_raw_open" ? "Offline replay" : "Paper account"}</span>
                <div>Watches 9:45–11:30 AM ET</div>
                {showPro && strategy.or15.reason && <div className="break-words">{planStatusText(strategy.or15.reason)}</div>}
              </div>
            )}
          </div>
        </div>
      )}
    </article>
  );
}
