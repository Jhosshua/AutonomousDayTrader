import type { ResultDay } from "@/lib/gut";
import { formatSignedMoney } from "@/lib/plain";
export default function ResultsBars({ days, total, onOpenHistory, loading, error }: { days: ResultDay[]; total: number | null; onOpenHistory: () => void; loading: boolean; error: string | null }) {
  const max = Math.max(1, ...days.map(d => Math.abs(d.pnl ?? 0)));
  return <section className="gut-card gut-results" data-testid="results-bars">
    <div className="gut-card-heading"><h2>Results</h2><span>finished trades · last 7 days</span></div>
    {error && <p className="text-xs text-warn">Results did not refresh. {total == null ? "Waiting for recorded results." : "Showing the last recorded results."}</p>}
    {total == null ? <div className="gut-chart-loading" role="status">{loading ? "Loading finished trades…" : "Finished trades unavailable right now."}</div> : <div className="gut-chart" style={{ gridTemplateColumns: `repeat(${Math.max(1, days.length)}, minmax(0, 1fr))` }}>
      {days.map(d => <div key={d.date} className="gut-bar-column" data-testid={`result-day-${d.date}`} data-pnl={d.pnl ?? "unknown"}>
        <div className={`gut-bar-label ${d.pnl == null || d.pnl === 0 ? "text-muted" : d.pnl > 0 ? "text-gain" : "text-loss"}`}>{d.pnl == null ? "?" : d.pnl === 0 ? "$0" : formatSignedMoney(d.pnl).replace(/\.00$/, "")}</div>
        <div className={`gut-bar ${d.pnl == null || d.pnl === 0 ? "bg-line" : d.pnl > 0 ? "bg-gain" : "bg-loss"}`} style={{ height: `${Math.max(4, Math.abs(d.pnl ?? 0) / max * 78)}px` }} aria-label={`${d.label}: ${d.pnl == null ? "result unknown" : formatSignedMoney(d.pnl)}, ${d.trades} trades`} />
        <span className="gut-day-label">{d.label}</span>
      </div>)}
    </div>}
    <div className="flex flex-wrap items-center justify-between gap-2 text-xs"><span className="text-muted">{total == null ? "Total unavailable" : `${formatSignedMoney(total)} finished`}</span><button type="button" onClick={onOpenHistory} className="min-h-[32px] font-semibold text-darkcard" data-testid="open-full-history">Open full history →</button></div>
  </section>;
}
