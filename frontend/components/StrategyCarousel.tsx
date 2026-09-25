"use client";

import { StrategyState } from "@/types/trading";
import { StrategyLedgerAgg } from "@/lib/plain";
import StrategyCard from "./StrategyCard";

interface StrategyCarouselProps {
  strategies: StrategyState[];
  ledgerByStrategy: Record<string, StrategyLedgerAgg>;
  showPro: boolean;
}

/** The registered playbooks, using the same cards on desktop and mobile. */
export default function StrategyCarousel({ strategies, ledgerByStrategy, showPro }: StrategyCarouselProps) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-display text-2xl sm:text-3xl font-semibold text-ink">The {strategies.length} ways it trades</h2>
        <div className="hidden sm:block text-sm text-muted">Colored bar = hours it may trade. Dark line = now.</div>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 sm:gap-[18px]">
        {strategies.map((s, i) => (
          <StrategyCard key={s.id} strategy={s} ledgerAgg={ledgerByStrategy[s.id]} showPro={showPro} delayMs={i * 80} />
        ))}
      </div>
    </section>
  );
}
