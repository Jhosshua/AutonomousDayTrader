import type { TradingState } from "@/types/trading";
import type { HealthLimits } from "@/hooks/useHealthLimits";
import { formatMoney, moodHeadline } from "@/lib/plain";
import ExecutionLog from "@/components/ExecutionLog";
import SwingCandidateWatchlist from "@/components/SwingCandidateWatchlist";
import SwingTelemetryBar from "@/components/SwingTelemetryBar";

export default function ProDetails({ state, limits, tradingDay }: { state: TradingState; limits: HealthLimits; tradingDay: boolean }) {
  const brokerCount = state.broker?.alpaca_positions ? Object.keys(state.broker.alpaca_positions).length : null;
  const mood = moodHeadline(state.market_context, tradingDay);
  return <details className="gut-card gut-pro" data-testid="pro-details">
    <summary className="gut-summary text-muted" data-testid="pro-words-toggle"><span className="gut-chevron" aria-hidden="true">›</span>Pro details</summary>
    <div className="flex min-w-0 flex-col gap-2 pb-3 text-xs text-muted">
      <p data-testid="account-label">{state.broker?.mode === "alpaca_paper" ? "Alpaca paper" : "Practice"} account {state.broker?.account_number ?? "number unavailable"}{state.broker?.mode === "alpaca_paper" ? " · real orders, practice money" : " · simulated trades"}</p>
      <p>{state.broker?.mismatch ? "Broker and robot disagree" : "Broker and robot agree"} · robot {state.all_positions.length} positions · broker {brokerCount ?? "count unavailable"}</p>
      <p>Feeds: {Object.entries(state.ingestion).map(([feed, status]) => `${feed} ${status}`).join(" · ") || "none connected"}</p>
      <p>VIX {state.market_context.vix?.toFixed(1) ?? "unavailable"} · {mood.text}</p>
      <div data-testid="risk-telemetry"><p>Daily loss stop {limits.maxDailyLossDollars == null ? "unavailable" : formatMoney(limits.maxDailyLossDollars)} · used {formatMoney(state.account.risk_drawdown ?? state.account.daily_drawdown)}</p><p>The daily loss limit covers day trades only. Overnight holds have no stop and sell at the next open.</p></div>
      <p>ORB rules: {state.strategies.find(s => s.id === "orb")?.orb?.rules ?? "not reported"}</p>
      <p>Research log: {limits.research?.written ?? "unavailable"} rows · {limits.research?.errors ?? "unavailable"} errors{limits.research?.pending != null && ` · ${limits.research.pending} pending`}</p>
      <ExecutionLog records={state.recent_activity} maxItems={20} />
      {state.swing && <><SwingCandidateWatchlist candidates={state.swing.candidates} /><SwingTelemetryBar swingState={state.swing} showPro /></>}
    </div>
  </details>;
}
