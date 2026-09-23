"use client";

import React from "react";
import { motion } from "framer-motion";
import { Zap, Moon } from "lucide-react";

export type TradingMode = "intraday" | "swing";

interface SegmentedModeToggleProps {
  mode: TradingMode;
  onModeChange: (mode: TradingMode) => void;
  intradayPositionsCount?: number;
  swingPositionsCount?: number;
}

export default function SegmentedModeToggle({
  mode,
  onModeChange,
  intradayPositionsCount = 0,
  swingPositionsCount = 0,
}: SegmentedModeToggleProps) {
  return (
    <div className="px-4 overflow-hidden">
      <div
        data-testid="segmented-mode-toggle"
        className="relative bg-obsidian-900/90 border border-white/[0.08] p-1 rounded-2xl flex items-center backdrop-blur-2xl shadow-2xl overflow-hidden"
      >
        {/* Intraday Day Trader Button */}
        <button
          type="button"
          onClick={() => onModeChange("intraday")}
          className={`relative z-10 flex-1 min-w-0 flex items-center justify-center gap-1.5 py-2.5 px-2 rounded-xl text-xs font-semibold tracking-wide transition-colors duration-200 ${
            mode === "intraday" ? "text-white" : "text-neutral-400 hover:text-white"
          }`}
          data-testid="mode-tab-intraday"
        >
          <Zap
            className={`w-3.5 h-3.5 flex-shrink-0 transition-colors ${
              mode === "intraday" ? "text-apple-purple" : "text-neutral-500"
            }`}
          />
          <span className="truncate">Intraday Day Trader</span>
          {intradayPositionsCount > 0 && (
            <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-apple-purple/20 text-apple-purple border border-apple-purple/30 flex-shrink-0">
              {intradayPositionsCount}
            </span>
          )}
        </button>

        {/* Swing Mean-Reversion Button */}
        <button
          type="button"
          onClick={() => onModeChange("swing")}
          className={`relative z-10 flex-1 min-w-0 flex items-center justify-center gap-1.5 py-2.5 px-2 rounded-xl text-xs font-semibold tracking-wide transition-colors duration-200 ${
            mode === "swing" ? "text-white" : "text-neutral-400 hover:text-white"
          }`}
          data-testid="mode-tab-swing"
        >
          <Moon
            className={`w-3.5 h-3.5 flex-shrink-0 transition-colors ${
              mode === "swing" ? "text-apple-teal" : "text-neutral-500"
            }`}
          />
          <span className="truncate">Swing Mean-Reversion</span>
          {swingPositionsCount > 0 ? (
            <span className="ml-1 px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-apple-teal/20 text-apple-teal border border-apple-teal/30 flex-shrink-0">
              {swingPositionsCount} Held
            </span>
          ) : (
            <span className="hidden sm:inline-block px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-white/[0.05] text-neutral-400 flex-shrink-0">
              5 Stocks
            </span>
          )}
        </button>

        {/* Framer Motion Sliding Pill Backdrop */}
        <motion.div
          layoutId="segmentedActivePill"
          transition={{ type: "spring", stiffness: 450, damping: 35 }}
          className="absolute inset-y-1 rounded-xl bg-white/[0.10] border border-white/[0.12] shadow-inner"
          style={{
            left: mode === "intraday" ? "0.25rem" : "50%",
            right: mode === "intraday" ? "50%" : "0.25rem",
          }}
        />
      </div>
    </div>
  );
}
