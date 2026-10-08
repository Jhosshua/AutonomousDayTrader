import type { ComponentProps } from "react";
import type { Position } from "@/types/trading";
import type { HoldView } from "@/components/OvernightHolds";
import HoldingNow from "@/components/HoldingNow";
import ActiveSwingPositionsTable from "@/components/ActiveSwingPositionsTable";
import { formatMoney, formatSignedMoney, holdLine } from "@/lib/plain";
import { overnightPriceIsStale } from "@/lib/gut";
import PlaybookIcon from "./PlaybookIcon";

export default function Holdings({ day, overnightPositions, holds, swing, marketOpen, etMin, tradingDay, mismatch }: {
  day: ComponentProps<typeof HoldingNow>; overnightPositions: Position[]; holds: HoldView[];
  swing: ComponentProps<typeof ActiveSwingPositionsTable>; marketOpen: boolean; etMin: number; tradingDay: boolean; mismatch: boolean;
}) {
  const count = day.positions.length + overnightPositions.length + (swing.positions?.length ?? 0);
  return <section id="holdings" className="gut-card gut-holdings" data-testid="gut-holdings" data-count={count}>
    <div className="gut-card-heading"><h2>Holding now</h2><span>{count} open{overnightPositions.length > 0 && ` · ${overnightPositions.length} overnight`}</span></div>
    {mismatch && <div className="gut-alarm" data-testid="broker-mismatch"><strong>Broker and robot disagree.</strong> The robot&apos;s positions don&apos;t match the Alpaca account. New trades are paused until they match.</div>}
    {count === 0 && !mismatch && <div className="flex items-center gap-3 py-3 text-muted"><span className="gut-icon bg-ground"><PlaybookIcon name="flat" /></span><p className="font-display font-semibold">Holding nothing</p></div>}
    <HoldingNow {...day} marketOpen={marketOpen} compact />
    <div id="overnight-holds" data-testid="overnight-holds">
      {overnightPositions.length > 0 && <p className="my-1 text-xs leading-snug text-muted">Overnight stocks sell at the 9:30 open and carry no stop, by design. {!marketOpen && "Prices update when the market opens. "}{overnightPositions.some(p => ["IREN", "HUT"].includes(p.symbol)) && "IREN and HUT are both bitcoin miners and move together."}</p>}
      {overnightPositions.map(p => {
        const h = holds.find(h => h.symbol === p.symbol);
        const stale = overnightPriceIsStale(p, etMin, tradingDay);
        return <details key={p.symbol} className="gut-overnight-row" data-testid={`overnight-hold-${p.symbol}`}>
          <summary><span className="gut-icon bg-ink text-white"><PlaybookIcon name="overnight" /></span><span className="min-w-0 flex-1"><strong className="font-display">{p.symbol}</strong><span className="ml-2 text-xs text-muted">{p.shares} sh</span><span className="block text-xs text-muted">bought {formatMoney(p.entry_price)}{stale && " · price at 9:30"}</span></span>{!stale && <span className="text-right text-xs"><strong className={p.unrealized_pnl < 0 ? "text-loss" : "text-gain"}>{formatSignedMoney(p.unrealized_pnl)}</strong><span className="block text-muted">now {formatMoney(p.market_price)}</span></span>}<span className="gut-chevron text-muted" aria-hidden="true">›</span></summary>
          <div className="pb-2 text-xs leading-relaxed text-muted"><span className="font-semibold" data-testid="overnight-strategy">{p.symbol} overnight</span><p data-testid="overnight-hold-line">{holdLine(h ?? { symbol: p.symbol, shares: p.shares, buyPrice: p.entry_price, saleDate: null, nights: null })}</p></div>
        </details>;
      })}
    </div>
    <ActiveSwingPositionsTable {...swing} marketOpen={marketOpen} compact />
  </section>;
}
