"use client";

import { useEffect, useState } from "react";
import { LineChart } from "lucide-react";
import { etParts } from "@/lib/plain";
import type { BrokerInfo } from "@/types/trading";

interface HeaderProps {
  isConnected: boolean;
  broker?: BrokerInfo;
  showPro: boolean;
  onTogglePro: (next: boolean) => void;
}

const PRO_STORAGE_KEY = "daytrader.showPro";

function readStoredPro(): boolean | null {
  try {
    const raw = window.localStorage.getItem(PRO_STORAGE_KEY);
    return raw === null ? null : raw === "1";
  } catch {
    return null;
  }
}

export function useProWordsToggle(): [boolean, (next: boolean) => void] {
  const [showPro, setShowPro] = useState(false);
  useEffect(() => {
    const stored = readStoredPro();
    if (stored !== null) setShowPro(stored);
  }, []);
  const set = (next: boolean) => {
    setShowPro(next);
    try {
      window.localStorage.setItem(PRO_STORAGE_KEY, next ? "1" : "0");
    } catch {
      // localStorage unavailable (private mode, blocked storage) - the toggle still works
      // for this page view, it just won't persist.
    }
  };
  return [showPro, set];
}

function nowLabel(): string {
  const now = new Date();
  const { hour, minute } = etParts(now);
  const weekday = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", weekday: "short" }).format(now);
  const month = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", month: "short" }).format(now);
  const day = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", day: "numeric" }).format(now);
  const h12 = hour % 12 === 0 ? 12 : hour % 12;
  const ampm = hour >= 12 ? "PM" : "AM";
  return `${weekday}, ${month} ${day} · ${h12}:${String(minute).padStart(2, "0")} ${ampm}`;
}

function accountLabel(broker?: BrokerInfo): string {
  if (!broker) return "";
  if (broker.mode === "alpaca_paper") {
    return `Alpaca paper account${broker.account_number ? ` ${broker.account_number}` : ""}. Real orders, practice money.`;
  }
  return "Practice account. The trades are simulated, no real orders.";
}

export default function Header({ isConnected, broker, showPro, onTogglePro }: HeaderProps) {
  const [clock, setClock] = useState<string>("");
  useEffect(() => {
    setClock(nowLabel());
    const id = setInterval(() => setClock(nowLabel()), 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="rise relative flex flex-wrap items-center justify-between gap-4">
      <div className="flex items-center gap-3">
        <div
          className="bob flex h-11 w-11 sm:h-12 sm:w-12 items-center justify-center rounded-2xl shadow-lg"
          style={{ background: "linear-gradient(135deg, #D98B5F, #C47A88 55%, #8189C4)" }}
        >
          <LineChart className="h-6 w-6 text-white" strokeWidth={1.8} aria-hidden="true" />
        </div>
        <div className="flex flex-col gap-0.5">
          <div className="font-display text-xl sm:text-2xl font-semibold tracking-tight text-ink">Day Trader</div>
          <div className="text-xs sm:text-sm text-muted" data-testid="account-label">{accountLabel(broker)}</div>
        </div>
      </div>

      <div className="flex items-center gap-2 sm:gap-3">
        <div className="flex items-center gap-2 rounded-full border border-line bg-white/80 px-3 sm:px-4 py-2 text-xs sm:text-sm">
          <span className="relative inline-block h-2.5 w-2.5" aria-hidden="true">
            {isConnected && (
              <span className="ping2 absolute inset-0 rounded-full" style={{ background: "#5E9A7A" }} />
            )}
            <span
              className="absolute inset-0 rounded-full"
              style={{ background: isConnected ? "#5E9A7A" : "#A7A2B8" }}
            />
          </span>
          <span className="font-semibold" style={{ color: isConnected ? "#3F7D5C" : "#5D5A73" }}>
            {isConnected ? "Running" : "Reconnecting"}
          </span>
          {clock && <span className="hidden sm:inline text-muted">{clock}</span>}
        </div>
        <button
          type="button"
          data-testid="pro-words-toggle"
          aria-pressed={showPro}
          onClick={() => onTogglePro(!showPro)}
          className="min-h-[44px] rounded-full border px-4 text-xs sm:text-sm font-semibold transition-colors"
          style={
            showPro
              ? { borderColor: "#4A5190", background: "#EEEFF7", color: "#3E4478" }
              : { borderColor: "#E2D6C2", background: "rgba(255,255,255,0.8)", color: "#3E3A57" }
          }
        >
          {showPro ? "Hide pro words" : "Show pro words"}
        </button>
      </div>
    </header>
  );
}
