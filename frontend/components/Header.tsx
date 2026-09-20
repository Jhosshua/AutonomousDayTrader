"use client";

import { motion } from "framer-motion";
import { TrendingUp, TrendingDown, Radio, AlertTriangle, ShieldCheck } from "lucide-react";
import { AccountState, MarketContext } from "@/types/trading";

interface HeaderProps {
  account: AccountState;
  marketContext: MarketContext;
  isConnected: boolean;
}

export default function Header({ account, marketContext, isConnected }: HeaderProps) {
  const isPositive = account.daily_pnl >= 0;
  const pnlSign = isPositive ? "+" : "";

  // Regime color mapping
  const getRegimeColor = (regime: string) => {
    switch (regime?.toUpperCase()) {
      case "LOW":
        return "text-apple-blue bg-apple-blue/10 border-apple-blue/20";
      case "NORMAL":
        return "text-apple-green bg-apple-green/10 border-apple-green/20";
      case "ELEVATED":
        return "text-apple-orange bg-apple-orange/10 border-apple-orange/20";
      case "CRISIS":
        return "text-apple-red bg-apple-red/10 border-apple-red/20";
      default:
        return "text-neutral-400 bg-neutral-800/40 border-neutral-700/40";
    }
  };

  const getPhaseDisplay = (phase: string) => {
    switch (phase?.toUpperCase()) {
      case "PRE_MARKET":
        return "Pre-Market";
      case "OPEN_FLUSH":
        return "Open Flush (09:30–10:00)";
      case "TREND":
        return "Trend Continuation (10:00–11:30)";
      case "MIDDAY_CHOP":
        return "Midday Chop (11:30–14:00)";
      case "POWER_HOUR":
        return "Power Hour (15:00–16:00)";
      case "EOD_FLATTEN":
        return "EOD Auto-Flattening (15:55)";
      default:
        return phase || "Session Live";
    }
  };

  return (
    <header className="w-full pt-safe px-4 pt-4 pb-2 transition-colors">
      {/* Top Status Capsule / Dynamic Island Bar */}
      <div className="flex items-center justify-between py-1.5 px-3 rounded-full bg-white/[0.04] border border-white/[0.08] backdrop-blur-xl mb-3 text-xs">
        {/* WebSocket Connection Status */}
        <div className="flex items-center space-x-2">
          <span className="relative flex h-2 w-2">
            {isConnected ? (
              <>
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-apple-green opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-apple-green"></span>
              </>
            ) : (
              <>
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-apple-orange opacity-75"></span>
                <span className="relative inline-flex rounded-full h-2 w-2 bg-apple-orange"></span>
              </>
            )}
          </span>
          <span className="text-neutral-300 font-medium tracking-wide flex items-center gap-1">
            <Radio className="w-3 h-3 text-neutral-400" />
            {isConnected ? "LIVE STREAM" : "RECONNECTING"}
          </span>
        </div>

        {/* VIX Print Pill */}
        <div className="flex items-center space-x-2">
          <div
            className={`px-2 py-0.5 rounded-full border text-[11px] font-semibold tracking-wider uppercase ${getRegimeColor(
              marketContext.vix_regime
            )}`}
          >
            VIX {marketContext.vix?.toFixed(2) || "18.25"} • {marketContext.vix_regime || "NORMAL"}
          </div>
        </div>

        {/* Market Phase Badge */}
        <div className="hidden sm:flex items-center text-neutral-400 text-[11px]">
          <span className="w-1.5 h-1.5 rounded-full bg-neutral-400 mr-1.5" />
          {getPhaseDisplay(marketContext.time_phase)}
        </div>
      </div>

      {/* Circuit Breaker Alert Banner */}
      {account.is_circuit_broken && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-3 p-3 rounded-2xl bg-apple-red/20 border border-apple-red/40 backdrop-blur-xl flex items-center space-x-3 text-apple-red"
        >
          <AlertTriangle className="w-5 h-5 flex-shrink-0 animate-bounce" />
          <div>
            <div className="text-xs font-bold uppercase tracking-wider">Circuit Breaker Engaged</div>
            <div className="text-[11px] text-neutral-300">
              Maximum daily loss limit ($1,500) reached. New orders blocked, auto-liquidation enforced.
            </div>
          </div>
        </motion.div>
      )}

      {/* Hero Portfolio Value Section (Apple Music Album Hero Header Style) */}
      <div className="py-2">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-widest text-neutral-400">
            Portfolio Equity
          </span>
          <div className="flex items-center gap-1 text-[11px] text-neutral-400">
            <ShieldCheck className="w-3.5 h-3.5 text-apple-green" />
            <span>4:1 Day Trading Buying Power</span>
          </div>
        </div>

        <div className="flex flex-col sm:flex-row sm:items-baseline justify-between mt-1 gap-1">
          {/* Total Equity Big Number */}
          <motion.div
            key={account.equity}
            initial={{ opacity: 0.8, scale: 0.99 }}
            animate={{ opacity: 1, scale: 1 }}
            className="text-4xl font-bold tracking-tight text-white num-tabular"
          >
            ${account.equity.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </motion.div>

          {/* Daily PnL Badge */}
          <div
            className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-semibold num-tabular backdrop-blur-lg border ${
              isPositive
                ? "text-apple-green bg-apple-green/15 border-apple-green/30"
                : "text-apple-red bg-apple-red/15 border-apple-red/30"
            }`}
          >
            {isPositive ? <TrendingUp className="w-4 h-4" /> : <TrendingDown className="w-4 h-4" />}
            <span>
              {pnlSign}${Math.abs(account.daily_pnl).toFixed(2)} ({pnlSign}
              {account.daily_pnl_pct.toFixed(2)}%) Today
            </span>
          </div>
        </div>

        {/* Sub-metrics: Cash & Buying Power Row */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mt-3 pt-2 border-t border-white/[0.06] text-xs">
          <div>
            <span className="text-neutral-400 text-[11px] block">Cash Balance</span>
            <span className="font-semibold text-neutral-200 num-tabular">
              ${account.cash.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
          <div>
            <span className="text-neutral-400 text-[11px] block">Day Trading Buying Power</span>
            <span className="font-semibold text-neutral-200 num-tabular">
              ${account.buying_power.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </div>
          <div className="hidden sm:block">
            <span className="text-neutral-400 text-[11px] block">Market Phase</span>
            <span className="font-semibold text-neutral-200">
              {getPhaseDisplay(marketContext.time_phase)}
            </span>
          </div>
        </div>
      </div>
    </header>
  );
}
