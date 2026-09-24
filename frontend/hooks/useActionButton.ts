"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * F3: shared action-feedback state machine for operator action buttons.
 *
 *   idle -> confirm (destructive only, 4s, cleared if the underlying thing disappears)
 *         -> sending (disabled, no duplicate sends)
 *         -> done (verified against live state) | failed ("Didn't go through, try again" after 10s)
 *
 * This never changes what gets sent - callers still call the exact same flattenPosition,
 * flattenAll, tightenStop, swingExitNextOpen, swingExitImmediate and swingTightenStop functions
 * from useTradingStream with the same payloads. This hook only manages UI feedback around that call.
 */
export type ActionPhase = "idle" | "confirm" | "sending" | "done" | "failed";

export interface UseActionButtonOptions {
  /** Actually dispatch the action. Return false if it could not even be sent (e.g. no transport). */
  send: () => boolean;
  /** Poll this to decide the action has verifiably completed (position gone, stop changed, exit staged, ...). */
  isDone: () => boolean;
  /** True once the underlying position/order has disappeared - clears a pending confirm too. */
  isGone?: () => boolean;
  requireConfirm?: boolean;
  confirmTimeoutMs?: number;
  verifyTimeoutMs?: number;
}

export function useActionButton({
  send,
  isDone,
  isGone,
  requireConfirm = false,
  confirmTimeoutMs = 4000,
  verifyTimeoutMs = 10000,
}: UseActionButtonOptions) {
  const [phase, setPhase] = useState<ActionPhase>("idle");
  const confirmTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const verifyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);
  const settleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearAll = useCallback(() => {
    if (confirmTimer.current) clearTimeout(confirmTimer.current);
    if (verifyTimer.current) clearTimeout(verifyTimer.current);
    if (pollTimer.current) clearInterval(pollTimer.current);
    if (settleTimer.current) clearTimeout(settleTimer.current);
    confirmTimer.current = null;
    verifyTimer.current = null;
    pollTimer.current = null;
    settleTimer.current = null;
  }, []);

  useEffect(() => clearAll, [clearAll]);

  // If the thing being acted on disappears while we're mid-confirm, drop back to idle silently.
  useEffect(() => {
    if (phase === "confirm" && isGone?.()) {
      clearAll();
      setPhase("idle");
    }
  }, [phase, isGone, clearAll]);

  const reset = useCallback(() => {
    clearAll();
    setPhase("idle");
  }, [clearAll]);

  const trigger = useCallback(() => {
    if (phase === "sending") return; // never send twice
    if (requireConfirm && phase !== "confirm") {
      clearAll();
      setPhase("confirm");
      confirmTimer.current = setTimeout(() => setPhase("idle"), confirmTimeoutMs);
      return;
    }
    clearAll();
    setPhase("sending");
    const dispatched = send();
    if (!dispatched) {
      setPhase("failed");
      settleTimer.current = setTimeout(() => setPhase("idle"), 3000);
      return;
    }
    pollTimer.current = setInterval(() => {
      if (isDone()) {
        clearAll();
        setPhase("done");
        settleTimer.current = setTimeout(() => setPhase("idle"), 3000);
      }
    }, 400);
    verifyTimer.current = setTimeout(() => {
      if (pollTimer.current) clearInterval(pollTimer.current);
      pollTimer.current = null;
      setPhase((current) => {
        if (current !== "sending") return current;
        settleTimer.current = setTimeout(() => setPhase("idle"), 4000);
        return "failed";
      });
    }, verifyTimeoutMs);
  }, [phase, requireConfirm, confirmTimeoutMs, verifyTimeoutMs, send, isDone, clearAll]);

  return { phase, trigger, reset };
}
