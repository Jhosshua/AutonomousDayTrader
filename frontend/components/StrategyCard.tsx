"use client";

import { motion } from "framer-motion";
import { Zap, Activity, Flame, Waves, Trophy, ArrowUpRight } from "lucide-react";
import { StrategyState } from "@/types/trading";

interface StrategyCardProps {
  strategy: StrategyState;
  onSelect?: (strategy: StrategyState) => void;
  isSelected?: boolean;
}

export default function StrategyCard({ strategy, onSelect, isSelected }: StrategyCardProps) {
  const isPositive = strategy.daily_pnl >= 0;

  // Custom theme gradient and icon per strategy
  const getStrategyTheme = (id: string) => {
    switch (id) {
      case "orb":
        return {
          gradient: "from-amber-500/30 via-orange-600/20 to-emerald-500/30",
          border: "hover:border-amber-500/40",
          accentColor: "text-amber-400",
          icon: <Flame className="w-6 h-6 text-amber-400" />,
          tagline: "Morning Volatility Breakouts",
        };
      case "vwap_pullback":
        return {
          gradient: "from-cyan-500/30 via-blue-600/20 to-indigo-500/30",
          border: "hover:border-cyan-500/40",
          accentColor: "text-cyan-400",
          icon: <Waves className="w-6 h-6 text-cyan-400" />,
          tagline: "Anchored VWAP Continuation",
        };
      case "news_momentum":
        return {
          gradient: "from-fuchsia-500/30 via-purple-600/20 to-pink-500/30",
          border: "hover:border-fuchsia-500/40",
          accentColor: "text-fuchsia-400",
          icon: <Zap className="w-6 h-6 text-fuchsia-400" />,
          tagline: "Benzinga Sentiment Catalysts",
        };
      case "mean_reversion":
        return {
          gradient: "from-indigo-500/30 via-violet-600/20 to-teal-500/30",
          border: "hover:border-indigo-500/40",
          accentColor: "text-indigo-400",
          icon: <Activity className="w-6 h-6 text-indigo-400" />,
          tagline: "2.5-Sigma Exhaustion Fades",
        };
      default:
        return {
          gradient: "from-neutral-700/30 via-neutral-800/20 to-neutral-900/30",
          border: "hover:border-white/20",
          accentColor: "text-neutral-400",
          icon: <Activity className="w-6 h-6 text-neutral-400" />,
          tagline: "Algorithmic Execution",
        };
    }
  };

  const theme = getStrategyTheme(strategy.id);

  const getStatusBadge = (status: string) => {
    switch (status?.toUpperCase()) {
      case "ACTIVE":
      case "LIVE":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider bg-apple-green/15 text-apple-green border border-apple-green/30">
            <span className="w-1.5 h-1.5 rounded-full bg-apple-green animate-pulse" />
            LIVE
          </span>
        );
      case "ARMED":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider bg-apple-orange/15 text-apple-orange border border-apple-orange/30">
            <span className="w-1.5 h-1.5 rounded-full bg-apple-orange" />
            ARMED
          </span>
        );
      case "STANDBY":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider bg-apple-purple/15 text-apple-purple border border-apple-purple/30">
            STANDBY
          </span>
        );
      case "COOLDOWN":
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider bg-neutral-800 text-neutral-400 border border-white/10">
            COOLDOWN
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold tracking-wider bg-neutral-800 text-neutral-400 border border-white/10">
            {status}
          </span>
        );
    }
  };

  return (
    <motion.div
      whileHover={{ y: -4, transition: { duration: 0.2 } }}
      whileTap={{ scale: 0.97 }}
      onClick={() => onSelect?.(strategy)}
      className={`relative cursor-pointer flex-shrink-0 w-64 md:w-72 rounded-3xl p-4 transition-all duration-300 backdrop-blur-xl border ${
        isSelected
          ? "bg-white/[0.08] border-white/30 shadow-2xl shadow-apple-purple/20 ring-1 ring-white/20"
          : "bg-white/[0.03] border-white/[0.08] hover:bg-white/[0.06]"
      } ${theme.border}`}
    >
      {/* Top Strategy Visual Banner */}
      <div
        className={`relative w-full aspect-[16/10] rounded-2xl overflow-hidden bg-gradient-to-br ${theme.gradient} p-3 flex flex-col justify-between border border-white/[0.08] shadow-inner mb-3`}
      >
        <div className="flex items-center justify-between">
          <div className="p-2 rounded-xl bg-black/40 backdrop-blur-md border border-white/10">
            {theme.icon}
          </div>
          {getStatusBadge(strategy.status)}
        </div>

        <div>
          <span className="text-[10px] font-semibold uppercase tracking-widest text-white/70 block">
            Trading Strategy
          </span>
          <h3 className="text-base font-bold text-white tracking-tight leading-tight">
            {strategy.name}
          </h3>
          <p className="text-[11px] text-white/60 line-clamp-1">{theme.tagline}</p>
        </div>
      </div>

      {/* Performance Metrics Row */}
      <div className="space-y-2">
        <div className="flex items-baseline justify-between">
          <span className="text-xs text-neutral-400">Today&apos;s PnL</span>
          <span
            className={`text-sm font-bold num-tabular ${
              isPositive ? "text-apple-green" : "text-apple-red"
            }`}
          >
            {isPositive ? "+" : ""}${strategy.daily_pnl.toFixed(2)}
          </span>
        </div>

        <div className="grid grid-cols-3 gap-1 pt-2 border-t border-white/[0.06] text-center">
          <div className="bg-white/[0.02] py-1 px-1.5 rounded-lg">
            <span className="text-[10px] text-neutral-400 block">Win Rate</span>
            <span className="text-xs font-semibold text-neutral-200 num-tabular">
              {(strategy.win_rate * 100).toFixed(1)}%
            </span>
          </div>

          <div className="bg-white/[0.02] py-1 px-1.5 rounded-lg">
            <span className="text-[10px] text-neutral-400 block">Trades</span>
            <span className="text-xs font-semibold text-neutral-200 num-tabular">
              {strategy.trades_count}
            </span>
          </div>

          <div className="bg-white/[0.02] py-1 px-1.5 rounded-lg">
            <span className="text-[10px] text-neutral-400 block flex items-center justify-center gap-0.5">
              <Trophy className="w-2.5 h-2.5 text-amber-400" />
              Sharpe
            </span>
            <span className="text-xs font-semibold text-neutral-200 num-tabular">
              {strategy.sharpe == null ? "—" : strategy.sharpe.toFixed(2)}
            </span>
          </div>
        </div>
      </div>

      {/* Footer subtle action */}
      <div className="mt-3 pt-2 border-t border-white/[0.04] flex items-center justify-between text-[11px] text-neutral-400">
        <span className="text-[10px]">Tap for inspector</span>
        <ArrowUpRight className="w-3.5 h-3.5 opacity-60 group-hover:opacity-100 transition-opacity" />
      </div>
    </motion.div>
  );
}
