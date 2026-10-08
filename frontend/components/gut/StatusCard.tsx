"use client";
import { useActionButton } from "@/hooks/useActionButton";
import { AttentionItem, attentionPillText } from "@/lib/plain";
import PlaybookIcon from "./PlaybookIcon";

export interface Alarm { text: string; action?: { label: string; onConfirm: () => boolean } }
function AlarmButton({ action }: { action: NonNullable<Alarm["action"]> }) {
  const { phase, trigger } = useActionButton({ send: action.onConfirm, isDone: () => false, requireConfirm: true });
  return <button type="button" className="gut-button" onClick={trigger} disabled={phase === "sending"}>{phase === "confirm" ? "Tap again to confirm" : phase === "sending" ? "Sending…" : phase === "failed" ? "Didn't go through, try again" : action.label}</button>;
}
export default function StatusCard({ sentence, attention, firstAlarm, clock, lockCountdown, stateIcon, unsoldText, reconnecting }: {
  sentence: string; attention: AttentionItem[]; firstAlarm: Alarm | null; clock: string; lockCountdown: string | null;
  stateIcon: string; unsoldText: string | null; reconnecting: string | null;
}) {
  return <section className="gut-card gut-status" data-testid="status-strip">
    <div className="flex items-start gap-3"><span className={`gut-icon ${attention.length ? "bg-warnbg text-warn" : "bg-ground text-muted"}`}><PlaybookIcon name={attention.length ? "attention" : stateIcon} /></span><p className="font-display text-[17px] font-semibold leading-snug" data-testid="right-now-sentence">{sentence}</p></div>
    <div className="flex flex-wrap items-center gap-2"><span data-testid="attention-pill" data-count={attention.length} className={`gut-pill ${attention.length ? "bg-warnbg text-warn" : "bg-lime text-ink"}`}><PlaybookIcon name={attention.length ? "attention" : "done"} className="h-4 w-4" />{attentionPillText(attention.length)}</span><span className="text-xs text-muted">{clock}</span></div>
    {attention.length > 0 && <div data-testid="attention-list" className="flex flex-col gap-2">
      {firstAlarm && <div className="gut-alarm" data-alarm-key={attention[0].key}>
        <p data-testid={attention[0].key === "unsold" ? "overnight-unsold-banner" : undefined}>{firstAlarm.text}</p>
        {firstAlarm.action && <AlarmButton key={attention[0].key} action={firstAlarm.action} />}
      </div>}
      {attention.length > 1 && <div className="text-xs text-warn">Also needs a look: {attention.slice(1).map(a => a.label).join(" · ")}</div>}
      {unsoldText && attention[0].key !== "unsold" && <p className="text-xs text-warn" data-testid="overnight-unsold-banner">{unsoldText}</p>}
    </div>}
    {reconnecting && <p role="status" className="text-xs text-warn">{reconnecting}</p>}
    {lockCountdown && <a href="#no-buy-tonight" className="gut-lock" data-testid="lock-countdown">{lockCountdown}<span className="block text-xs font-normal">Do nothing and the robot buys as planned. Skip tonight’s buy in Controls ↓</span></a>}
  </section>;
}
