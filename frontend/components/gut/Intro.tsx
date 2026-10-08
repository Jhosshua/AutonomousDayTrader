"use client";
import { useEffect, useRef, useState } from "react";
import { etDateKey, STRATEGY_THEMES } from "@/lib/plain";
import BrandMark from "./BrandMark";

export default function Intro({ hasReceivedData, attentionCount, playbookCount }: { hasReceivedData: boolean; attentionCount: number; playbookCount: number }) {
  const [visible, setVisible] = useState(false);
  const checked = useRef(false);
  useEffect(() => {
    if (!hasReceivedData || checked.current) return;
    checked.current = true;
    if (attentionCount > 0 || new URLSearchParams(window.location.search).get("intro") === "off" || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const date = etDateKey();
    try {
      if (window.localStorage.getItem("cobaltIntroDate") === date) return;
      window.localStorage.setItem("cobaltIntroDate", date);
    } catch { /* Storage denied: still usable for this page load. */ }
    setVisible(true);
  }, [hasReceivedData, attentionCount]);
  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => setVisible(false), 1200);
    return () => clearTimeout(timer);
  }, [visible]);
  // An alarm arriving during the animation immediately reveals the page.
  if (!visible || attentionCount > 0) return null;
  return <div className="gut-intro" data-testid="intro" aria-hidden="true">
    <BrandMark className="h-32 w-32" />
    <div className="flex gap-2.5">{["orb", "vwap_pullback", "news_momentum", "mean_reversion", "tsla_asymmetric_dual", "cde_asymmetric_dual", "overnight_nvda"].map((id, i) => <span key={id} className="gut-intro-dot" style={{ background: STRATEGY_THEMES[id].bar, animationDelay: `${.20 + i * .03}s` }} />)}</div>
    <div className="gut-intro-word font-display text-[34px] font-extrabold">Cobalt Ledger</div>
    <p>{playbookCount} playbooks · 1 practice account</p>
    <button type="button" className="gut-intro-skip" onClick={() => setVisible(false)} tabIndex={-1}>Skip</button>
  </div>;
}
