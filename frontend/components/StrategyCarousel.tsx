"use client";

import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Sparkles, X, Shield, Activity, BarChart2 } from "lucide-react";
import { StrategyState } from "@/types/trading";
import StrategyCard from "./StrategyCard";

interface StrategyCarouselProps {
  strategies: StrategyState[];
}

export default function StrategyCarousel({ strategies }: StrategyCarouselProps) {
  const [selectedStrategy, setSelectedStrategy] = useState<StrategyState | null>(null);

  return (
    <section className="py-3">
      {/* Section Header (Apple Music Curated Playlist Style) */}
      <div className="px-4 mb-3 flex items-center justify-between">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-semibold text-apple-purple uppercase tracking-wider">
            <Sparkles className="w-3.5 h-3.5 text-apple-purple" />
            <span>Curated Playlists</span>
          </div>
          <h2 className="text-xl font-bold text-white tracking-tight">Active Strategies</h2>
        </div>
        <span className="text-xs text-neutral-400 font-medium">4 Models Deployed</span>
      </div>

      {/* Horizontal Snap Carousel */}
      <div className="flex space-x-4 overflow-x-auto px-4 pb-2 pt-1 no-scrollbar snap-x snap-mandatory">
        {strategies.map((strat) => (
          <div key={strat.id} className="snap-center">
            <StrategyCard
              strategy={strat}
              isSelected={selectedStrategy?.id === strat.id}
              onSelect={(s) => setSelectedStrategy(s)}
            />
          </div>
        ))}
      </div>

      {/* Strategy Detail Modal / Sheet */}
      <AnimatePresence>
        {selectedStrategy && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-xl"
            onClick={() => setSelectedStrategy(null)}
          >
            <motion.div
              initial={{ scale: 0.95, y: 20 }}
              animate={{ scale: 1, y: 0 }}
              exit={{ scale: 0.95, y: 20 }}
              transition={{ type: "spring", stiffness: 350, damping: 30 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-md rounded-3xl bg-[#0f0f15] border border-white/10 p-6 shadow-2xl space-y-4"
            >
              <div className="flex items-start justify-between">
                <div>
                  <span className="text-xs font-semibold uppercase tracking-widest text-apple-purple">
                    Strategy Inspector
                  </span>
                  <h3 className="text-xl font-bold text-white">{selectedStrategy.name}</h3>
                  <p className="text-xs text-neutral-400 mt-0.5">{selectedStrategy.subtitle}</p>
                </div>
                <button
                  onClick={() => setSelectedStrategy(null)}
                  className="p-2 rounded-full bg-white/[0.06] hover:bg-white/[0.12] text-neutral-400 hover:text-white transition-colors"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>

              <div className="p-3.5 rounded-2xl bg-white/[0.03] border border-white/[0.06] text-xs text-neutral-300 leading-relaxed">
                {selectedStrategy.description ||
                  "Intraday model calibrated for institutional flow and dynamic regime adaptation."}
              </div>

              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="p-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                  <span className="text-neutral-400 text-[11px] block">Session Realized PnL</span>
                  <span
                    className={`text-base font-bold num-tabular ${
                      selectedStrategy.daily_pnl >= 0 ? "text-apple-green" : "text-apple-red"
                    }`}
                  >
                    {selectedStrategy.daily_pnl >= 0 ? "+" : ""}${selectedStrategy.daily_pnl.toFixed(2)}
                  </span>
                </div>
                <div className="p-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                  <span className="text-neutral-400 text-[11px] block">Win Rate</span>
                  <span className="text-base font-bold text-neutral-200 num-tabular">
                    {(selectedStrategy.win_rate * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="p-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                  <span className="text-neutral-400 text-[11px] block">Execution Count</span>
                  <span className="text-base font-bold text-neutral-200 num-tabular">
                    {selectedStrategy.trades_count} Orders
                  </span>
                </div>
                <div className="p-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                  <span className="text-neutral-400 text-[11px] block">Sharpe Ratio</span>
                  <span className="text-base font-bold text-neutral-200 num-tabular">
                    {selectedStrategy.sharpe?.toFixed(2) || "2.10"}
                  </span>
                </div>
              </div>

              <div className="space-y-2 pt-2 border-t border-white/[0.06] text-xs">
                <div className="flex items-center justify-between text-neutral-400">
                  <span className="flex items-center gap-1.5">
                    <Shield className="w-3.5 h-3.5 text-apple-green" /> Risk Allocation
                  </span>
                  <span className="text-neutral-200 font-medium">1.0% ($500 Max Risk / Trade)</span>
                </div>
                <div className="flex items-center justify-between text-neutral-400">
                  <span className="flex items-center gap-1.5">
                    <Activity className="w-3.5 h-3.5 text-apple-blue" /> Exit Protocols
                  </span>
                  <span className="text-neutral-200 font-medium">1.5R Scale / 2.5R Trail</span>
                </div>
                <div className="flex items-center justify-between text-neutral-400">
                  <span className="flex items-center gap-1.5">
                    <BarChart2 className="w-3.5 h-3.5 text-apple-orange" /> Overnight Status
                  </span>
                  <span className="text-neutral-200 font-medium">Auto-Flatten 15:55 ET</span>
                </div>
              </div>

              <button
                onClick={() => setSelectedStrategy(null)}
                className="w-full py-2.5 rounded-2xl bg-white/10 hover:bg-white/15 text-white font-semibold text-xs tracking-wide transition-colors"
              >
                Close Inspector
              </button>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </section>
  );
}
