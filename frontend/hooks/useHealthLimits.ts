"use client";

import { useEffect, useState } from "react";
import { apiBase } from "@/lib/apiBase";

export interface HealthLimits {
  maxDailyLossDollars: number | null;
  baseTradeRiskPct: number | null;
}

const POLL_MS = 60000;

/** F10: daily-limit copy reads live off /health (limits.max_daily_loss_dollars,
 * limits.base_trade_risk_pct) so it always reflects the deployed risk config, never a
 * hardcoded number. Falls back to hiding the number if the fetch fails. */
export function useHealthLimits(): HealthLimits {
  const [limits, setLimits] = useState<HealthLimits>({ maxDailyLossDollars: null, baseTradeRiskPct: null });

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const res = await fetch(`${apiBase()}/health`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        setLimits({
          maxDailyLossDollars: data?.limits?.max_daily_loss_dollars ?? null,
          baseTradeRiskPct: data?.limits?.base_trade_risk_pct ?? null,
        });
      } catch {
        // keep last-known limits (or the initial null, which callers must handle by hiding it)
      }
    };
    void load();
    const id = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return limits;
}
