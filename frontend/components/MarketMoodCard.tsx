"use client";

import { Gauge } from "lucide-react";
import { MarketContext } from "@/types/trading";
import {
  LEVEL_CHIP,
  LEVEL_COLORS,
  PHASE_PLAIN,
  TREND_WORD,
  hmLabel,
  moodHeadline,
  pctOfUsual,
  trendEffect,
} from "@/lib/plain";

interface MarketMoodCardProps {
  context: MarketContext;
  /** Strategy-window trading_day flag (false on weekends and holidays), undefined until known. */
  tradingDay?: boolean;
}

const GREY = { bg: "#E6EAF5", ink: "#3F4663" };

function stopPhrase(mult: number | null | undefined): string | null {
  if (mult == null) return null;
  if (mult > 1.001) return `safety exits ${Math.round((mult - 1) * 100)}% farther away`;
  if (mult < 0.999) return `safety exits ${Math.round((1 - mult) * 100)}% closer`;
  return "safety exits at the usual distance";
}

function Tile({ label, children, testid }: { label: string; children: React.ReactNode; testid: string }) {
  return (
    <div className="rounded-lg bg-[#F1F3FB] px-3 py-1.5 text-[13px] leading-snug text-ink" data-testid={testid}>
      <div className="text-[10px] font-semibold uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 flex flex-col gap-0.5">{children}</div>
    </div>
  );
}

function cap(word: string): string {
  return word.charAt(0).toUpperCase() + word.slice(1);
}

function tierRange(lower: number | null, upper: number | null): string {
  if (lower == null && upper != null) return `Under ${upper}`;
  if (upper == null && lower != null) return `${lower} and above`;
  return `${lower} to ${upper}`;
}

/** One compact block that says how the robot is adapting to the market right now. Observation only. */
export default function MarketMoodCard({ context, tradingDay }: MarketMoodCardProps) {
  const head = moodHeadline(context, tradingDay);
  const regime = (context.vix_regime || "").toUpperCase();
  const level = LEVEL_COLORS[regime] ?? GREY;
  const trend = context.market_trend ? context.market_trend.toUpperCase() : null;
  const inMidday = context.time_phase === "MIDDAY_CHOP";
  const midday = context.midday ? `${hmLabel(context.midday.start)} to ${hmLabel(context.midday.end)}` : null;
  const sizing = context.sizing_multiplier ?? null;
  const stale = context.vix_stale === true;
  const known = head.tiles;
  const tiers = context.vix_tiers && context.vix_tiers.length > 0 ? context.vix_tiers : null;

  return (
    <section className="relative flex flex-col gap-2.5 rounded-[22px] border border-line bg-white p-3 sm:px-4" data-testid="market-mood">
      <div className="flex items-start gap-3">
        <span
          className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-lg"
          style={{ background: known ? level.bg : GREY.bg, color: known ? level.ink : GREY.ink }}
          aria-hidden="true"
        >
          <Gauge className="h-4 w-4" />
        </span>
        <div className="min-w-0 lg:pr-[110px]">
          <h2 className="font-display text-lg font-semibold leading-tight text-ink">How the robot adapts</h2>
          <p className="mt-0.5 text-[13px] leading-snug text-ink" data-testid="mood-headline">{head.text}</p>
          {head.note && <p className="text-[13px] leading-snug text-muted" data-testid="mood-note">{head.note}</p>}
        </div>
      </div>

      {head.tiles && (
        <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-3" data-testid="mood-tiles">
          <Tile label="Fear gauge" testid="mood-tile-fear">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-base font-semibold tabular-nums">{context.vix != null ? context.vix.toFixed(1) : "No reading"}</span>
              <span className="rounded-full px-2.5 py-0.5 text-xs font-semibold" style={{ background: level.bg, color: level.ink }} data-testid="mood-level-chip">
                {LEVEL_CHIP[regime] ?? "Unclear"}
              </span>
            </div>
            {stale ? (
              <div>{sizing != null && sizing < 1 ? "Not updating, sizes stay at the cautious setting" : "Not updating, sizes held at normal"}</div>
            ) : (
              <div>
                {sizing != null ? `Trades risk ${pctOfUsual(sizing)} of usual` : "Sizes trades for the mood"}
                {stopPhrase(context.stop_multiplier) ? `, ${stopPhrase(context.stop_multiplier)}` : ""}
              </div>
            )}
          </Tile>
          <Tile label="Time of day" testid="mood-tile-time">
            <div className="text-base font-semibold">{PHASE_PLAIN[context.time_phase] ?? context.time_phase}</div>
            <div>
              {inMidday && midday
                ? `Midday ${midday}: new trades are half size`
                : midday
                  ? `Full size now. Midday ${midday} trades are half size.`
                  : inMidday
                    ? "New trades are half size"
                    : "Full size now"}
            </div>
          </Tile>
          <Tile label="Market direction" testid="mood-tile-trend">
            <div className="text-base font-semibold">{trend ? cap(TREND_WORD[trend] ?? "unclear") : "Not reported"}</div>
            <div>{trendEffect(trend)}</div>
          </Tile>
        </div>
      )}

      <details className="group" data-testid="mood-details">
        {/* Desktop: the toggle sits in the card's top-right corner so a closed disclosure takes no line of its own. */}
        <summary className="flex min-h-[44px] cursor-pointer items-center text-sm font-semibold text-ink lg:absolute lg:right-4 lg:top-2">See the rules</summary>
        <div className="flex flex-col gap-2 pb-1 text-sm text-ink">
          {tiers && (
            <table className="w-full border-separate border-spacing-y-1 text-left" data-testid="mood-tier-table">
              <thead>
                <tr className="text-xs font-semibold uppercase tracking-wide text-muted">
                  <th className="pr-2 font-semibold">Fear gauge</th>
                  <th className="pr-2 font-semibold">Level</th>
                  <th className="font-semibold">Then</th>
                </tr>
              </thead>
              <tbody>
                {tiers.map((t) => {
                  const now = t.name.toUpperCase() === regime;
                  return (
                    <tr key={t.name} className={now ? "font-semibold" : ""} style={now ? { background: (LEVEL_COLORS[t.name.toUpperCase()] ?? GREY).bg } : undefined} data-current={now ? "true" : "false"}>
                      <td className="rounded-l-lg py-1.5 pl-2 pr-2 tabular-nums">{tierRange(t.lower, t.upper)}</td>
                      <td className="pr-2">{LEVEL_CHIP[t.name.toUpperCase()] ?? t.name}{now ? " (now)" : ""}</td>
                      <td className="rounded-r-lg pr-2">risk {pctOfUsual(t.sizing)}, safety exit {t.stop}x as far</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {context.max_concurrent_positions != null && (
            <p>At most {context.max_concurrent_positions} quick trades at once (Tesla/Coeur count, ORB does not).</p>
          )}
          <p>If the fear gauge stops updating, the robot never sizes above normal.</p>
          {context.notional_cap_pct != null && <p>Each trade can use at most {context.notional_cap_pct}% of the account.</p>}
        </div>
      </details>
    </section>
  );
}
