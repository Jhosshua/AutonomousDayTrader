"use client";
import { useEffect, useState } from "react";
import { freshnessLabel } from "@/lib/gut";
import BrandMark from "./BrandMark";

export default function TopBar({ lastUpdated, connectionState, marketOpen }: { lastUpdated: Date | null; connectionState: string; marketOpen: boolean }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const age = lastUpdated ? (now - lastUpdated.getTime()) / 1000 : NaN;
  const stale = !lastUpdated || connectionState !== "live" || age > 60;
  return <header className="gut-topbar">
    <div className="flex min-w-0 items-center gap-2.5"><BrandMark /><h1 className="font-display text-xl font-bold tracking-tight">Cobalt Ledger</h1></div>
    <div className={`gut-freshness ${!lastUpdated ? "bg-white text-muted" : stale ? "bg-warnbg text-warn" : "bg-white text-gain"}`} data-testid="freshness-pill" data-market-open={marketOpen}>
      <span className="h-2 w-2 shrink-0 rounded-full bg-current" aria-hidden="true" />{freshnessLabel(age, connectionState)}
    </div>
  </header>;
}
