import type { AccountState, TradeHistoryResponse } from "@/types/trading";
import { formatMoney, formatSignedMoney } from "@/lib/plain";
type Summary = TradeHistoryResponse["summary"] | null;
const dollars = (n: number) => formatMoney(n).replace(/\.00$/, "");
export default function MoneyTiles({ account, today, week, todayError, weekError }: { account: AccountState; today: Summary; week: Summary; todayError: boolean; weekError: boolean }) {
  const opening = account.daily_starting_equity ?? account.equity - account.daily_pnl;
  const change = account.equity - opening;
  const pct = opening ? change / opening * 100 : null;
  return <section className="gut-money" aria-label="Account and finished trades">
    <div className="gut-card"><h2>Account</h2><div className="gut-number">{dollars(account.equity)}</div><p>{dollars(account.cash)} cash</p></div>
    <div className="gut-card"><h2>Account change today</h2><div className={`gut-number ${change < 0 ? "text-loss" : change > 0 ? "text-gain" : "text-ink"}`}>{formatSignedMoney(change).replace(/\.00$/, "")}</div><p>{pct == null ? "Opening equity unavailable" : `${pct > 0 ? "+" : ""}${pct.toFixed(2)}% of opening`}</p><p>Finished trades: {today ? `${formatSignedMoney(today.realized_pnl)} · ${today.trades_count}` : todayError ? "unavailable" : "loading…"}</p></div>
    <div className="gut-card"><h2>Finished trades, 7 days</h2><div className={`gut-number ${week && week.realized_pnl < 0 ? "text-loss" : week && week.realized_pnl > 0 ? "text-gain" : "text-ink"}`}>{week ? formatSignedMoney(week.realized_pnl).replace(/\.00$/, "") : "—"}</div><p>{week ? `${week.trades_count} trades · ${week.wins} won` : weekError ? "Unavailable" : "Loading…"}</p></div>
  </section>;
}
