"use client";
import { useRef } from "react";
import { useActionButton } from "@/hooks/useActionButton";
import NoBuyTonightButton, { NoBuyProps } from "./NoBuyTonightButton";

export default function ControlsCard({ marketOpen, dayCount, workingOrders, stopped, onFlattenAll, noBuy, tonight }: {
  marketOpen: boolean; dayCount: number; workingOrders: number; stopped?: boolean; onFlattenAll: () => boolean;
  noBuy: NoBuyProps; tonight: { symbol: string; name: string; text: string }[];
}) {
  const latest = useRef({ dayCount, workingOrders, stopped });
  latest.current = { dayCount, workingOrders, stopped };
  const { phase, trigger } = useActionButton({ send: onFlattenAll, requireConfirm: true,
    isDone: () => latest.current.dayCount === 0 });
  return <section id="controls" className="gut-card gut-controls">
    <div className="gut-card-heading"><h2>Controls</h2></div>
    <div><button type="button" data-testid="btn-flatten-all" disabled={!marketOpen || phase === "sending"} onClick={trigger} className="gut-button" title={!marketOpen ? "market closed" : undefined}>{phase === "confirm" ? "Tap again to confirm" : phase === "sending" ? "Closing…" : phase === "done" ? "Day trades closed" : phase === "failed" ? "Didn't go through, try again" : "Close all day trades now"}</button><p className="mt-1 text-xs text-muted">{dayCount} open · also stops new ORB trades today · never touches overnight or slow trades{!marketOpen && " · market closed"}</p></div>
    <NoBuyTonightButton {...noBuy} />
    {tonight.length > 0 && <div className="text-xs text-muted" data-testid="overnight-tonight">{tonight.map(t => <p key={t.symbol} data-testid={`overnight-tonight-${t.symbol}`}><strong>{t.name}.</strong> {t.text}</p>)}</div>}
  </section>;
}
