"use client";

import React, { useState } from "react";
import { SwingPosition } from "@/types/trading";
import {
  ShieldAlert,
  ArrowUpRight,
  ArrowDownRight,
  Clock,
  AlertOctagon,
  LogOut,
  Sliders,
  CheckCircle,
  AlertTriangle,
  Layers,
} from "lucide-react";

interface ActiveSwingPositionsTableProps {
  positions?: SwingPosition[];
  onExitNextOpen: (symbol: string) => void;
  onExitImmediate: (symbol: string) => void;
  onTightenStop: (symbol: string, newStop: number) => void;
}

export default function ActiveSwingPositionsTable({
  positions = [],
  onExitNextOpen,
  onExitImmediate,
  onTightenStop,
}: ActiveSwingPositionsTableProps) {
  const [confirmEmergencyExit, setConfirmEmergencyExit] = useState<string | null>(null);
  const [tightenInput, setTightenInput] = useState<{ [symbol: string]: string }>({});
  const [showTightenModal, setShowTightenModal] = useState<string | null>(null);

  if (positions.length === 0) {
    return (
      <section className="px-4 py-2" data-testid="active-swing-positions">
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="flex items-center gap-1.5 text-xs font-semibold text-apple-teal uppercase tracking-wider">
              <Layers className="w-3.5 h-3.5 text-apple-teal" />
              <span>Multi-Day Portfolio</span>
            </div>
            <h2 className="text-xl font-bold text-white tracking-tight">Active Swing Positions</h2>
          </div>
          <span className="text-xs text-neutral-400 font-medium">0 / 2 Slots Committed</span>
        </div>

        <div className="p-8 rounded-2xl bg-white/[0.02] border border-white/[0.06] backdrop-blur-2xl text-center">
          <div className="w-12 h-12 rounded-full bg-apple-teal/10 border border-apple-teal/20 flex items-center justify-center mx-auto mb-3 text-apple-teal">
            <Clock className="w-6 h-6" />
          </div>
          <h3 className="text-sm font-bold text-white mb-1">No Active Multi-Day Swing Positions</h3>
          <p className="text-xs text-neutral-400 max-w-md mx-auto">
            The 2-Day Panic Dip engine evaluates candidates at 16:00 ET close. Orders stage overnight and execute at 09:30 ET market open.
          </p>
        </div>
      </section>
    );
  }

  return (
    <section className="px-4 py-2" data-testid="active-swing-positions">
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-semibold text-apple-teal uppercase tracking-wider">
            <Layers className="w-3.5 h-3.5 text-apple-teal" />
            <span>Multi-Day Portfolio</span>
          </div>
          <h2 className="text-xl font-bold text-white tracking-tight">Active Swing Positions</h2>
        </div>
        <span className="text-xs font-semibold text-apple-teal bg-apple-teal/10 px-2.5 py-1 rounded-full border border-apple-teal/20">
          {positions.length} of 2 Slots In Use
        </span>
      </div>

      <div className="space-y-4">
        {positions.map((pos) => {
          const sym = pos.symbol;
          const isProfitable = (pos.unrealized_pnl ?? 0) >= 0;
          const holdingDays = pos.holding_days ?? 0;
          const maxHoldingDays = pos.max_holding_days ?? 5;
          const stopLoss = pos.stop_loss ?? pos.stop_loss_price ?? 0;
          const stopDist = pos.atr_stop_distance ?? (pos.market_price - stopLoss);
          const stopPct = pos.atr_stop_pct ?? (pos.market_price > 0 ? (stopDist / pos.market_price) * 100 : 0);
          const isStagedExit = pos.staged_exit_at_open ?? false;
          const triggers = pos.exit_triggers ?? {
            sma_5_cross: false,
            rsi_70_cross: false,
            time_stop_day_5: false,
            earnings_tomorrow: false,
          };
          const anyExitArmed =
            triggers.sma_5_cross ||
            triggers.rsi_70_cross ||
            triggers.time_stop_day_5 ||
            triggers.earnings_tomorrow;

          return (
            <div
              key={sym}
              className="p-4 sm:p-5 rounded-3xl bg-white/[0.03] border border-white/[0.08] backdrop-blur-2xl shadow-2xl relative overflow-hidden"
              data-testid={`active-swing-row-${sym}`}
            >
              {/* Top Row: Symbol, PnL, Shares */}
              <div className="flex items-start justify-between mb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-2xl font-black text-white tracking-tight">{sym}</span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-apple-teal/20 text-apple-teal border border-apple-teal/30">
                      LONG SWING
                    </span>
                    {isStagedExit && (
                      <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30">
                        EXIT STAGED FOR OPEN
                      </span>
                    )}
                  </div>
                  <div className="text-xs text-neutral-400 mt-0.5">
                    {pos.shares} shares @ ${pos.entry_price?.toFixed(2)} entry • Market: ${pos.market_price?.toFixed(2)}
                  </div>
                </div>

                {/* PnL Display */}
                <div className="text-right">
                  <div
                    className={`text-xl font-bold num-tabular flex items-center justify-end gap-0.5 ${
                      isProfitable ? "text-apple-green" : "text-apple-red"
                    }`}
                  >
                    {isProfitable ? <ArrowUpRight className="w-5 h-5" /> : <ArrowDownRight className="w-5 h-5" />}
                    <span>{isProfitable ? "+" : ""}${Math.abs(pos.unrealized_pnl ?? 0).toFixed(2)}</span>
                  </div>
                  <div
                    className={`text-xs font-semibold num-tabular ${
                      isProfitable ? "text-apple-green/80" : "text-apple-red/80"
                    }`}
                  >
                    {isProfitable ? "+" : ""}
                    {((pos.unrealized_pnl_pct ?? 0) * 100).toFixed(2)}%
                  </div>
                </div>
              </div>

              {/* Middle Section: Metrics Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-4">
                {/* 2.5x ATR Emergency Stop Meter */}
                <div className="p-3 rounded-2xl bg-black/40 border border-white/[0.05]">
                  <div className="flex items-center justify-between text-xs mb-1.5">
                    <span className="text-neutral-400 flex items-center gap-1 font-medium">
                      <ShieldAlert className="w-3.5 h-3.5 text-apple-red" />
                      2.5x ATR Hard Stop
                    </span>
                    <span className="text-white font-bold num-tabular">
                      ${stopLoss.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-[11px] text-neutral-400 mb-1">
                    <span>Safety Buffer:</span>
                    <span className="text-apple-green font-semibold num-tabular">
                      ${stopDist.toFixed(2)} ({stopPct.toFixed(1)}% away)
                    </span>
                  </div>
                  {/* Visual stop distance meter */}
                  <div className="w-full h-1.5 rounded-full bg-white/[0.08] overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all duration-300 ${
                        stopPct < 3.0 ? "bg-apple-red" : stopPct < 6.0 ? "bg-amber-400" : "bg-apple-green"
                      }`}
                      style={{ width: `${Math.min(Math.max(stopPct * 10, 5), 100)}%` }}
                    />
                  </div>
                </div>

                {/* Visual Holding Day Counter: [● ● ○ ○ ○] Day 2 of 5 */}
                <div className="p-3 rounded-2xl bg-black/40 border border-white/[0.05]">
                  <div className="flex items-center justify-between text-xs mb-2">
                    <span className="text-neutral-400 flex items-center gap-1 font-medium">
                      <Clock className="w-3.5 h-3.5 text-apple-purple" />
                      Holding Day Counter
                    </span>
                    <span className="font-bold text-white text-xs num-tabular">
                      Day {holdingDays} of {maxHoldingDays}
                    </span>
                  </div>
                  {/* Step circles */}
                  <div className="flex items-center justify-between gap-1.5 pt-1">
                    {Array.from({ length: maxHoldingDays }).map((_, i) => {
                      const dayNumber = i + 1;
                      const isCompleted = dayNumber <= holdingDays;
                      const isCurrent = dayNumber === holdingDays;
                      return (
                        <div key={dayNumber} className="flex-1 flex flex-col items-center gap-1">
                          <div
                            className={`w-full h-2 rounded-full transition-all ${
                              isCurrent
                                ? "bg-apple-teal ring-2 ring-apple-teal/40 shadow-sm shadow-apple-teal/30"
                                : isCompleted
                                ? "bg-apple-teal/70"
                                : "bg-white/[0.08]"
                            }`}
                          />
                          <span
                            className={`text-[9px] font-semibold ${
                              isCurrent ? "text-apple-teal" : isCompleted ? "text-neutral-300" : "text-neutral-600"
                            }`}
                          >
                            D{dayNumber}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>

              {/* Exit Triggers Checklist */}
              <div className="p-3 rounded-2xl bg-black/40 border border-white/[0.05] mb-4">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[11px] font-semibold text-neutral-400 uppercase tracking-wider">
                    Exit Rule Triggers (Next Open 09:30 ET)
                  </span>
                  {anyExitArmed && (
                    <span className="text-[10px] font-bold text-amber-300 bg-amber-500/20 px-2 py-0.5 rounded-full border border-amber-500/30 animate-pulse">
                      ARMED FOR OPEN EXIT
                    </span>
                  )}
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
                  <div className="flex items-center gap-1.5 p-1.5 rounded-xl bg-white/[0.02]">
                    {triggers.sma_5_cross ? (
                      <CheckCircle className="w-3.5 h-3.5 text-apple-green flex-shrink-0" />
                    ) : (
                      <div className="w-3.5 h-3.5 rounded-full border border-neutral-600 flex-shrink-0" />
                    )}
                    <div className="truncate">
                      <span className="text-[10px] text-neutral-400 block truncate">Rule 7a</span>
                      <span className={triggers.sma_5_cross ? "font-bold text-apple-green text-[11px]" : "text-neutral-400 text-[11px]"}>
                        {triggers.sma_5_cross ? "5-SMA Crossed" : "5-SMA Watch"}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 p-1.5 rounded-xl bg-white/[0.02]">
                    {triggers.rsi_70_cross ? (
                      <CheckCircle className="w-3.5 h-3.5 text-apple-green flex-shrink-0" />
                    ) : (
                      <div className="w-3.5 h-3.5 rounded-full border border-neutral-600 flex-shrink-0" />
                    )}
                    <div className="truncate">
                      <span className="text-[10px] text-neutral-400 block truncate">Rule 7b</span>
                      <span className={triggers.rsi_70_cross ? "font-bold text-apple-green text-[11px]" : "text-neutral-400 text-[11px]"}>
                        {triggers.rsi_70_cross ? "RSI(2) > 70" : "RSI(2) < 70"}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 p-1.5 rounded-xl bg-white/[0.02]">
                    {triggers.time_stop_day_5 ? (
                      <CheckCircle className="w-3.5 h-3.5 text-amber-400 flex-shrink-0" />
                    ) : (
                      <div className="w-3.5 h-3.5 rounded-full border border-neutral-600 flex-shrink-0" />
                    )}
                    <div className="truncate">
                      <span className="text-[10px] text-neutral-400 block truncate">Rule 7c</span>
                      <span className={triggers.time_stop_day_5 ? "font-bold text-amber-300 text-[11px]" : "text-neutral-400 text-[11px]"}>
                        {triggers.time_stop_day_5 ? "Day 5 Time Stop" : `Day ${holdingDays}/5`}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-1.5 p-1.5 rounded-xl bg-white/[0.02]">
                    {triggers.earnings_tomorrow ? (
                      <AlertTriangle className="w-3.5 h-3.5 text-apple-red flex-shrink-0" />
                    ) : (
                      <div className="w-3.5 h-3.5 rounded-full border border-neutral-600 flex-shrink-0" />
                    )}
                    <div className="truncate">
                      <span className="text-[10px] text-neutral-400 block truncate">Rule 4</span>
                      <span className={triggers.earnings_tomorrow ? "font-bold text-apple-red text-[11px]" : "text-neutral-400 text-[11px]"}>
                        {triggers.earnings_tomorrow ? "Earnings Tomorrow" : "No Earnings Veto"}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              {/* Operator Action Controls */}
              <div className="flex flex-wrap items-center gap-2 pt-1 border-t border-white/[0.06]">
                {/* Stage Exit Next Open */}
                <button
                  type="button"
                  onClick={() => onExitNextOpen(sym)}
                  disabled={isStagedExit}
                  className={`flex-1 min-w-[130px] flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl text-xs font-semibold tracking-wide transition-all ${
                    isStagedExit
                      ? "bg-white/[0.05] text-neutral-500 cursor-not-allowed border border-white/[0.05]"
                      : "bg-apple-purple/20 text-apple-purple hover:bg-apple-purple/30 border border-apple-purple/30 active:scale-[0.98]"
                  }`}
                  data-testid={`btn-exit-open-${sym}`}
                >
                  <LogOut className="w-3.5 h-3.5" />
                  <span>{isStagedExit ? "Exit Staged" : "Exit Next Open"}</span>
                </button>

                {/* Tighten Stop Button / Modal Toggle */}
                <button
                  type="button"
                  onClick={() => setShowTightenModal(showTightenModal === sym ? null : sym)}
                  className="flex-1 min-w-[130px] flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl text-xs font-semibold bg-white/[0.06] text-white hover:bg-white/[0.10] border border-white/[0.10] active:scale-[0.98] transition-all"
                  data-testid={`btn-tighten-stop-${sym}`}
                >
                  <Sliders className="w-3.5 h-3.5 text-apple-teal" />
                  <span>Tighten Stop</span>
                </button>

                {/* Emergency Market Exit with Confirmation */}
                {confirmEmergencyExit === sym ? (
                  <div className="flex items-center gap-1.5 flex-1 min-w-[180px]">
                    <button
                      type="button"
                      onClick={() => {
                        onExitImmediate(sym);
                        setConfirmEmergencyExit(null);
                      }}
                      className="flex-1 py-2 px-3 rounded-xl text-xs font-bold bg-apple-red text-white hover:bg-apple-red/90 shadow-lg shadow-apple-red/30 active:scale-[0.98] transition-all"
                      data-testid={`btn-confirm-emergency-${sym}`}
                    >
                      Confirm Liquidate
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmEmergencyExit(null)}
                      className="py-2 px-2.5 rounded-xl text-xs text-neutral-400 hover:text-white bg-white/[0.05]"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setConfirmEmergencyExit(sym)}
                    className="flex-1 min-w-[130px] flex items-center justify-center gap-1.5 py-2 px-3 rounded-xl text-xs font-semibold text-apple-red/90 hover:text-apple-red bg-apple-red/10 hover:bg-apple-red/20 border border-apple-red/20 active:scale-[0.98] transition-all"
                    data-testid={`btn-emergency-exit-${sym}`}
                  >
                    <AlertOctagon className="w-3.5 h-3.5" />
                    <span>Emergency Exit</span>
                  </button>
                )}
              </div>

              {/* Inline Tighten Stop Modal */}
              {showTightenModal === sym && (
                <div className="mt-3 p-3 rounded-2xl bg-black/60 border border-white/[0.12] flex flex-wrap items-center gap-2">
                  <span className="text-xs text-neutral-300 font-medium">New Stop Price:</span>
                  <input
                    type="number"
                    step="0.10"
                    placeholder={`e.g. ${(stopLoss + 1.0).toFixed(2)}`}
                    value={tightenInput[sym] ?? ""}
                    onChange={(e) => setTightenInput({ ...tightenInput, [sym]: e.target.value })}
                    className="w-28 px-2.5 py-1.5 rounded-xl bg-white/[0.08] border border-white/[0.15] text-xs text-white placeholder-neutral-500 focus:outline-none focus:border-apple-teal num-tabular"
                  />
                  <button
                    type="button"
                    onClick={() => {
                      const val = parseFloat(tightenInput[sym] || "0");
                      if (val > 0) {
                        onTightenStop(sym, val);
                        setShowTightenModal(null);
                      }
                    }}
                    className="py-1.5 px-3 rounded-xl text-xs font-semibold bg-apple-teal text-black hover:bg-apple-teal/90 active:scale-[0.98] transition-all"
                  >
                    Apply Stop
                  </button>
                  <button
                    type="button"
                    onClick={() => setShowTightenModal(null)}
                    className="py-1.5 px-2 rounded-xl text-xs text-neutral-400 hover:text-white"
                  >
                    Close
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
