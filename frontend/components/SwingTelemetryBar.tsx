"use client";

import React from "react";
import { SwingEngineState } from "@/types/trading";
import { Moon, ShieldCheck, Layers, Clock } from "lucide-react";

interface SwingTelemetryBarProps {
  swingState?: SwingEngineState;
}

export default function SwingTelemetryBar({ swingState }: SwingTelemetryBarProps) {
  const activeSlots = swingState?.active_slots_used ?? 0;
  const maxSlots = swingState?.max_slots ?? 2;
  const slotNotional = swingState?.slot_notional ?? 25000;
  const usedNotional = activeSlots * slotNotional;
  const maxNotional = maxSlots * slotNotional;
  const status = swingState?.status ?? "STANDBY";

  return (
    <section className="px-4" data-testid="swing-telemetry">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
        {/* Tile 1: Strategy Identity */}
        <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
          <span className="text-neutral-400 text-[10px] uppercase tracking-wider block font-semibold">
            Swing Strategy
          </span>
          <div className="flex items-center gap-1.5 mt-0.5">
            <Moon className="w-3.5 h-3.5 text-apple-teal flex-shrink-0" />
            <span className="font-bold text-white text-xs truncate">
              2-Day Panic Dip
            </span>
          </div>
          <span className="text-[10px] text-neutral-500 block truncate mt-0.5">
            Connors RSI-2 • 5 Stocks
          </span>
        </div>

        {/* Tile 2: Slot Utilization */}
        <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
          <span className="text-neutral-400 text-[10px] uppercase tracking-wider block font-semibold">
            Slot Utilization
          </span>
          <div className="flex items-center gap-1.5 mt-0.5">
            <Layers className="w-3.5 h-3.5 text-apple-purple flex-shrink-0" />
            <span className="font-bold text-white text-xs num-tabular">
              {activeSlots} of {maxSlots} Slots Used
            </span>
          </div>
          <div className="flex items-center gap-1.5 mt-1">
            <div className="flex-1 h-1.5 rounded-full bg-white/[0.08] overflow-hidden flex gap-0.5">
              {Array.from({ length: maxSlots }).map((_, i) => (
                <div
                  key={i}
                  className={`flex-1 h-full rounded-sm transition-colors ${
                    i < activeSlots ? "bg-apple-purple" : "bg-white/[0.06]"
                  }`}
                />
              ))}
            </div>
            <span className="text-[9px] text-neutral-400 num-tabular">
              ${usedNotional.toLocaleString()} / ${maxNotional.toLocaleString()}
            </span>
          </div>
        </div>

        {/* Tile 3: Overnight Policy Exemption Badge */}
        <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
          <span className="text-neutral-400 text-[10px] uppercase tracking-wider block font-semibold">
            Holding Policy
          </span>
          <div className="flex items-center gap-1.5 mt-0.5">
            <ShieldCheck className="w-3.5 h-3.5 text-apple-green flex-shrink-0" />
            <span className="font-bold text-apple-green text-xs truncate">
              OVERNIGHT EXEMPT
            </span>
          </div>
          <span className="text-[10px] text-neutral-400 block truncate mt-0.5">
            Multi-Day Hold (15:58 Safe)
          </span>
        </div>

        {/* Tile 4: Execution Timing & Scanner Status */}
        <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
          <span className="text-neutral-400 text-[10px] uppercase tracking-wider block font-semibold">
            Scanner & Execution
          </span>
          <div className="flex items-center gap-1.5 mt-0.5">
            <span className="w-2 h-2 rounded-full bg-apple-teal animate-pulse inline-block flex-shrink-0" />
            <span className="font-bold text-white text-xs truncate">
              {status === "ACTIVE" ? "ACTIVE HOLDING" : "ARMED (16:00 Close)"}
            </span>
          </div>
          <div className="flex items-center gap-1 text-[10px] text-neutral-400 mt-0.5">
            <Clock className="w-2.5 h-2.5 text-neutral-500" />
            <span className="truncate">09:30 Open Execution</span>
          </div>
        </div>
      </div>
    </section>
  );
}
