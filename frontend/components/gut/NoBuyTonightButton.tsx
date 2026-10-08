"use client";
import { useEffect, useRef, useState } from "react";
export interface NoBuyProps {
  noBuyActive: boolean;
  disabledReason: string | null;
  summary: string | null;
  onSetNoBuy: (on: boolean) => Promise<{ ok: boolean; message: string }>;
}
const REPLY_MS = 8000;
/** Extracted from OvernightHolds: two taps to skip, one to undo, and the server's 409 reply. */
export default function NoBuyTonightButton({ noBuyActive, disabledReason, summary, onSetNoBuy }: NoBuyProps) {
  const [confirming, setConfirming] = useState(false);
  const [sending, setSending] = useState(false);
  const [reply, setReply] = useState<{ ok: boolean; text: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const replyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
    if (replyTimer.current) clearTimeout(replyTimer.current);
  }, []);

  const disabled = sending || disabledReason != null;

  const click = async () => {
    if (disabled) return;
    const on = !noBuyActive;
    // stopping a buy asks for a second tap, like the other write buttons; turning it back on does not
    if (on && !confirming) {
      setConfirming(true);
      timer.current = setTimeout(() => setConfirming(false), 4000);
      return;
    }
    if (timer.current) clearTimeout(timer.current);
    setConfirming(false);
    setSending(true);
    const res = await onSetNoBuy(on).catch(() => ({ ok: false, message: "Could not reach the robot. Try again." }));
    setSending(false);
    setReply({ ok: res.ok, text: res.message || (res.ok ? "Saved." : "Didn't go through, try again.") });
    // the reply answers one tap, so it goes away on its own instead of lingering into later states
    if (replyTimer.current) clearTimeout(replyTimer.current);
    replyTimer.current = setTimeout(() => setReply(null), REPLY_MS);
  };

  const label = sending ? "Saving…" : confirming ? "Tap again to confirm" : noBuyActive ? "Turn tonight's buy back on" : "Skip tonight's overnight buy";

  return (
      <div id="no-buy-tonight" className="flex scroll-mt-4 flex-col gap-2" data-testid="overnight-control">
        <div className="min-w-0 text-xs leading-snug text-muted">
          {noBuyActive && <div data-testid="overnight-no-buy-on">Tonight&apos;s buy is off. Holds already bought still sell at the next open.</div>}
          {!noBuyActive && summary && <div data-testid="overnight-summary">{summary}</div>}
          {disabledReason && <div className="text-xs text-muted" data-testid="overnight-no-buy-reason">{disabledReason}</div>}
          {reply && (
            <div role="status" className="text-xs font-semibold" style={{ color: reply.ok ? "#0A7D53" : "#C2300F" }} data-testid="overnight-no-buy-reply">
              {reply.text}
            </div>
          )}
        </div>
        <button
          type="button"
          onClick={click}
          disabled={disabled}
          data-testid="btn-no-buy-tonight"
          className="gut-button"
        >
          {label}
        </button>
      </div>
  );
}
