"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Shield, Square, Check, RefreshCw } from "lucide-react";
import { Position } from "@/types/trading";

interface ManualControlsProps {
  position: Position | null;
  onFlattenPosition: (symbol: string) => void;
  onFlattenAll: () => void;
  onTightenStop: (symbol: string, newStop: number) => void;
}

export default function ManualControls({
  position,
  onFlattenPosition,
  onFlattenAll,
  onTightenStop,
}: ManualControlsProps) {
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);
  const [confirmFlatten, setConfirmFlatten] = useState<boolean>(false);
  const [confirmFlattenAll, setConfirmFlattenAll] = useState<boolean>(false);

  const showFeedback = (msg: string) => {
    setActionFeedback(msg);
    setTimeout(() => setActionFeedback(null), 3500);
  };

  const handleTightenBreakeven = () => {
    if (!position) return;
    const breakeven = position.entry_price;
    onTightenStop(position.symbol, breakeven);
    showFeedback(`Stop tightened to breakeven ($${breakeven.toFixed(2)})`);
  };

  const handleTightenHalfProfit = () => {
    if (!position) return;
    const entry = position.entry_price;
    const current = position.market_price;
    const targetStop =
      position.side === "SHORT"
        ? entry - (entry - current) * 0.5
        : entry + (current - entry) * 0.5;
    const newStop = Number(targetStop.toFixed(2));
    onTightenStop(position.symbol, newStop);
    showFeedback(`Stop tightened to +50% profit lock ($${newStop.toFixed(2)})`);
  };

  const handleExecuteFlatten = () => {
    if (!position) return;
    onFlattenPosition(position.symbol);
    setConfirmFlatten(false);
    showFeedback(`Flatten order dispatched for ${position.symbol}`);
  };

  const handleExecuteFlattenAll = () => {
    onFlattenAll();
    setConfirmFlattenAll(false);
    showFeedback("EMERGENCY FLATTEN ALL executed across portfolio");
  };

  if (!position) {
    return (
      <div className="p-4 rounded-2xl bg-white/[0.03] border border-white/[0.06] text-center">
        <span className="text-xs text-neutral-400">No active positions to control</span>
        <div className="mt-2">
          <button
            onClick={() => setConfirmFlattenAll(true)}
            className="px-4 py-2 rounded-xl bg-white/[0.05] hover:bg-apple-red/20 text-neutral-400 hover:text-apple-red border border-white/10 text-xs font-semibold transition-colors"
          >
            Flatten All Portfolios
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Toast feedback */}
      {actionFeedback && (
        <motion.div
          initial={{ opacity: 0, y: 5 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          className="p-2.5 rounded-xl bg-apple-green/20 border border-apple-green/40 text-apple-green text-xs font-semibold flex items-center justify-center gap-2 backdrop-blur-md"
        >
          <Check className="w-3.5 h-3.5" />
          <span>{actionFeedback}</span>
        </motion.div>
      )}

      {/* Primary Actions Grid */}
      <div className="grid grid-cols-2 gap-2">
        {/* Tighten to Breakeven */}
        <motion.button
          whileTap={{ scale: 0.96 }}
          onClick={handleTightenBreakeven}
          className="flex items-center justify-center gap-2 p-3 rounded-2xl bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-amber-300 text-xs font-bold transition-all shadow-sm"
        >
          <Shield className="w-4 h-4 text-amber-400" />
          <span>Lock Breakeven</span>
        </motion.button>

        {/* Trail 50% Profit */}
        <motion.button
          whileTap={{ scale: 0.96 }}
          onClick={handleTightenHalfProfit}
          className="flex items-center justify-center gap-2 p-3 rounded-2xl bg-apple-green/15 hover:bg-apple-green/25 border border-apple-green/30 text-apple-green text-xs font-bold transition-all shadow-sm"
        >
          <RefreshCw className="w-4 h-4 text-apple-green" />
          <span>Trail +50% Gain</span>
        </motion.button>
      </div>

      {/* Emergency Flatten Active Trade */}
      {!confirmFlatten ? (
        <motion.button
          whileTap={{ scale: 0.97 }}
          onClick={() => setConfirmFlatten(true)}
          className="w-full py-3 px-4 rounded-2xl bg-apple-red/15 hover:bg-apple-red/25 border border-apple-red/30 text-apple-red text-xs font-bold flex items-center justify-center gap-2 transition-all"
        >
          <Square className="w-4 h-4 fill-current" />
          <span>Flatten {position.symbol} ({position.shares} shares @ ${position.market_price.toFixed(2)})</span>
        </motion.button>
      ) : (
        <div className="p-3 rounded-2xl bg-apple-red/25 border border-apple-red/50 space-y-2">
          <div className="text-xs font-bold text-white text-center">
            Confirm immediate market exit for {position.symbol}?
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleExecuteFlatten}
              className="flex-1 py-2 rounded-xl bg-apple-red text-white text-xs font-bold hover:bg-red-600 transition-colors shadow-lg shadow-red-950"
            >
              Confirm Exit
            </button>
            <button
              onClick={() => setConfirmFlatten(false)}
              className="flex-1 py-2 rounded-xl bg-white/10 text-neutral-300 text-xs font-semibold hover:bg-white/20 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Emergency Kill Switch (Flatten All) */}
      {!confirmFlattenAll ? (
        <button
          onClick={() => setConfirmFlattenAll(true)}
          className="w-full py-2 text-[11px] text-neutral-500 hover:text-apple-red transition-colors text-center block"
        >
          Emergency: Flatten all open orders & positions
        </button>
      ) : (
        <div className="p-3 rounded-2xl bg-neutral-900 border border-apple-red/40 space-y-2">
          <div className="text-[11px] text-neutral-300 text-center">
            Purge working brackets and market-liquidate all open positions?
          </div>
          <div className="flex gap-2">
            <button
              onClick={handleExecuteFlattenAll}
              className="flex-1 py-1.5 rounded-xl bg-apple-red text-white text-[11px] font-bold"
            >
              Purge All
            </button>
            <button
              onClick={() => setConfirmFlattenAll(false)}
              className="flex-1 py-1.5 rounded-xl bg-white/10 text-neutral-400 text-[11px]"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
