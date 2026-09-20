"use client";

import { useTradingStream } from "@/hooks/useTradingStream";
import AmbientBackground from "@/components/AmbientBackground";
import Header from "@/components/Header";
import StrategyCarousel from "@/components/StrategyCarousel";
import ActivePositionTray from "@/components/ActivePositionTray";
import ExecutionLog from "@/components/ExecutionLog";
import { ShieldCheck, Activity, Terminal, AlertCircle } from "lucide-react";

export default function Home() {
  const {
    state,
    isConnected,
    lastError,
    flattenPosition,
    flattenAll,
    tightenStop,
  } = useTradingStream();

  return (
    <main className="relative min-h-screen bg-black text-white pb-32 overflow-x-hidden selection:bg-apple-purple/30">
      {/* Dynamic Ambient Momentum Background Glow */}
      <AmbientBackground
        dailyPnl={state.account.daily_pnl}
        isCircuitBroken={state.account.is_circuit_broken}
      />

      <div className="max-w-4xl mx-auto space-y-4">
        {/* Top Header Section */}
        <Header
          account={state.account}
          marketContext={state.market_context}
          ingestion={state.ingestion}
          isConnected={isConnected}
        />

        {/* Status / Alert Banner if any connection issue */}
        {!isConnected && lastError && (
          <div className="mx-4 p-3 rounded-2xl bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs flex items-center gap-2 backdrop-blur-md">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            <span>
              Offline or Reconnecting to backend stream on Port 8005. Displaying synchronized cache.
            </span>
          </div>
        )}

        {/* Risk & Regime Telemetry Bar */}
        <section className="px-4">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
              <span className="text-neutral-400 text-[10px] uppercase tracking-wider block">
                Risk Engine
              </span>
              <div className="flex items-center gap-1.5 mt-0.5">
                <ShieldCheck className="w-3.5 h-3.5 text-apple-green" />
                <span className="font-bold text-white text-xs">
                  {state.account.risk_level || "NORMAL"} (1.0% Base / 2.0% Hard)
                </span>
              </div>
            </div>

            <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
              <span className="text-neutral-400 text-[10px] uppercase tracking-wider block">
                Drawdown Limit
              </span>
              <div className="flex items-center gap-1.5 mt-0.5">
                <Activity className="w-3.5 h-3.5 text-apple-teal" />
                <span className="font-bold text-white text-xs num-tabular">
                  ${state.account.daily_drawdown.toFixed(2)} / $1,500
                </span>
              </div>
            </div>

            <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
              <span className="text-neutral-400 text-[10px] uppercase tracking-wider block">
                Overnight Policy
              </span>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="w-2 h-2 rounded-full bg-apple-green inline-block" />
                <span className="font-bold text-white text-xs">
                  Zero Overnight (15:55 MOC)
                </span>
              </div>
            </div>

            <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl">
              <span className="text-neutral-400 text-[10px] uppercase tracking-wider block">
                Active Positions
              </span>
              <div className="flex items-center gap-1.5 mt-0.5">
                <Terminal className="w-3.5 h-3.5 text-apple-purple" />
                <span className="font-bold text-white text-xs num-tabular">
                  {state.positions_count} Open • {state.working_orders_count} Working
                </span>
              </div>
            </div>
          </div>
        </section>

        {/* Trading Strategies Carousel */}
        <StrategyCarousel strategies={state.strategies} />

        {/* Execution & Audit Feed Section */}
        <section className="px-4 pt-2">
          <ExecutionLog records={state.recent_activity} maxItems={15} />
        </section>
      </div>

      {/* Docked Active Position Tray & Expandable Modal Sheet */}
      <ActivePositionTray
        position={state.primary_position}
        recentActivity={state.recent_activity}
        isConnected={isConnected}
        onFlattenPosition={flattenPosition}
        onFlattenAll={flattenAll}
        onTightenStop={tightenStop}
      />
    </main>
  );
}
