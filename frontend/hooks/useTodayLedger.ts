"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { RecoveredSessionSummary, TradeHistoryResponse, TradeRecord } from "@/types/trading";
import { apiBase } from "@/lib/apiBase";
import { dedupeTrades, etDateKey } from "@/lib/plain";

export interface TodayLedgerState {
  items: TradeRecord[];
  recoveredSessions: RecoveredSessionSummary[];
  summary: TradeHistoryResponse["summary"] | null;
  loading: boolean;
  error: string | null;
}

const PAGE_LIMIT = 100;
// Hard safety cap: even at 100/page this covers 2,000 trades in one session before giving up,
// far more than a single trading day could plausibly produce.
const MAX_PAGES = 20;
const RETRY_BACKOFF_MS = 5000;
const DATE_POLL_MS = 30000;

/**
 * F4: fetches /api/trades?range=today, draining next_cursor until null, deduping by trade_id.
 * Refetches on ledger_revision change, ET calendar-date change, reconnect, and (with backoff)
 * after a failure. A generation counter discards any response superseded by a newer request.
 */
export function useTodayLedger(ledgerRevision: number, isConnected: boolean): TodayLedgerState {
  const [state, setState] = useState<TodayLedgerState>({
    items: [],
    recoveredSessions: [],
    summary: null,
    loading: true,
    error: null,
  });
  const generationRef = useRef(0);
  const retryTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastDateKeyRef = useRef<string>(etDateKey());

  const load = useCallback(async () => {
    const myGeneration = ++generationRef.current;
    if (retryTimeoutRef.current) {
      clearTimeout(retryTimeoutRef.current);
      retryTimeoutRef.current = null;
    }
    setState((prev) => ({ ...prev, loading: true }));
    try {
      let cursor: string | null | undefined;
      let allItems: TradeRecord[] = [];
      let recovered: RecoveredSessionSummary[] = [];
      let summary: TradeHistoryResponse["summary"] | null = null;
      let pages = 0;
      do {
        const params = new URLSearchParams({ range: "today", limit: String(PAGE_LIMIT) });
        if (cursor) params.set("cursor", cursor);
        const res = await fetch(`${apiBase()}/api/trades?${params.toString()}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`Trades request failed (${res.status})`);
        const data = (await res.json()) as TradeHistoryResponse;
        if (myGeneration !== generationRef.current) return; // superseded by a newer request
        allItems = allItems.concat(data.items);
        recovered = data.recovered_sessions;
        summary = data.summary;
        cursor = data.next_cursor;
        pages += 1;
      } while (cursor && pages < MAX_PAGES);

      if (myGeneration !== generationRef.current) return;
      setState({
        items: dedupeTrades(allItems),
        recoveredSessions: recovered,
        summary,
        loading: false,
        error: null,
      });
    } catch (err) {
      if (myGeneration !== generationRef.current) return;
      setState((prev) => ({
        ...prev,
        loading: false,
        error: err instanceof Error ? err.message : "Couldn't load today's trades",
      }));
      retryTimeoutRef.current = setTimeout(() => {
        void load();
      }, RETRY_BACKOFF_MS);
    }
  }, []);

  // Initial load + refetch whenever the durable ledger advances.
  useEffect(() => {
    void load();
  }, [load, ledgerRevision]);

  useEffect(
    () => () => {
      if (retryTimeoutRef.current) clearTimeout(retryTimeoutRef.current);
    },
    []
  );

  // Refetch on reconnect (WS was down, came back up).
  const wasConnectedRef = useRef(isConnected);
  useEffect(() => {
    if (isConnected && !wasConnectedRef.current) {
      void load();
    }
    wasConnectedRef.current = isConnected;
  }, [isConnected, load]);

  // Refetch when the ET calendar date rolls over (session boundary), even with no other trigger.
  useEffect(() => {
    const id = setInterval(() => {
      const key = etDateKey();
      if (key !== lastDateKeyRef.current) {
        lastDateKeyRef.current = key;
        void load();
      }
    }, DATE_POLL_MS);
    return () => clearInterval(id);
  }, [load]);

  return state;
}
