"use client";

import { useState } from "react";
import { motion, AnimatePresence, PanInfo, useDragControls } from "framer-motion";
import { ChevronDown, Shield, Square, Layers, Activity } from "lucide-react";
import { Position, AuditRecord } from "@/types/trading";
import LiveChart from "./LiveChart";
import ManualControls from "./ManualControls";
import ExecutionLog from "./ExecutionLog";

export interface ActivePositionTrayProps {
  position: Position | null;
  recentActivity: AuditRecord[];
  isConnected: boolean;
  onFlattenPosition: (symbol: string) => boolean;
  onFlattenAll: () => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}

function safeFixed(val: number | null | undefined, digits: number = 2): string {
  if (val == null || isNaN(val)) return "—";
  return Number(val).toFixed(digits);
}

function safeLocale(val: number | null | undefined, minDigits: number = 2, maxDigits: number = 2): string {
  if (val == null || isNaN(val)) return "—";
  return Number(val).toLocaleString("en-US", { minimumFractionDigits: minDigits, maximumFractionDigits: maxDigits });
}

export default function ActivePositionTray({
  position,
  recentActivity,
  isConnected,
  onFlattenPosition,
  onFlattenAll,
  onTightenStop,
}: ActivePositionTrayProps) {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);
  const dragControls = useDragControls();

  const isPositive = (position?.unrealized_pnl ?? 0) >= 0;
  const pnlSign = isPositive ? "+" : "";

  // Spring transition physics specified in requirements
  const springConfig = {
    type: "spring" as const,
    stiffness: 350,
    damping: 32,
  };

  const handleDragEnd = (_: any, info: PanInfo) => {
    // If dragging down with sufficient distance or velocity, dismiss
    if (info.offset.y > 100 || info.velocity.y > 400) {
      setIsExpanded(false);
    }
  };

  return (
    <>
      {/* Docked Active Position Bar (Persistent Bottom Floating Island) */}
      <motion.div
        layoutId="active-position-tray"
        transition={springConfig}
        className="fixed bottom-4 left-4 right-4 z-40 max-w-xl mx-auto"
      >
        <div
          onClick={() => setIsExpanded(true)}
          className="cursor-pointer select-none rounded-2xl bg-[#0e0e14]/90 backdrop-blur-2xl border border-white/[0.12] p-3 shadow-2xl flex items-center justify-between hover:bg-[#151520]/95 transition-colors group"
        >
          {/* Left: Ticker Symbol Badge & Position Info */}
          <div className="flex items-center space-x-3 min-w-0">
            {/* Ticker Symbol Badge */}
            <div className="relative w-11 h-11 rounded-xl bg-gradient-to-br from-indigo-500/40 via-purple-600/30 to-apple-green/30 border border-white/15 flex items-center justify-center font-bold text-white shadow-inner flex-shrink-0">
              {position ? (
                <span className="text-xs tracking-wider">{position.symbol}</span>
              ) : (
                <Layers className="w-5 h-5 text-neutral-400" />
              )}
              {position && (
                <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-apple-green animate-pulse" />
              )}
            </div>

            {/* Position Details */}
            <div className="min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="font-bold text-sm text-white tracking-tight truncate">
                  {position ? position.symbol : "No Active Position"}
                </span>
                {position && (
                  <span
                    className={`text-[10px] font-bold px-1.5 py-0.2 rounded border ${
                      position.side === "LONG"
                        ? "text-apple-green bg-apple-green/10 border-apple-green/20"
                        : "text-apple-red bg-apple-red/10 border-apple-red/20"
                    }`}
                  >
                    {position.side} {position.shares}sh
                  </span>
                )}
              </div>

              <div className="text-xs text-neutral-400 truncate">
                {position ? (
                  <span>
                    Entry: ${safeFixed(position.entry_price)} • Live: ${safeFixed(position.market_price)}
                  </span>
                ) : (
                  <span>Ready for signals • Cash preservation</span>
                )}
              </div>
            </div>
          </div>

          {/* Right: Unrealized PnL & Quick Action Buttons */}
          <div className="flex items-center space-x-2.5 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            {position && (
              <div
                className={`text-right num-tabular px-2.5 py-1 rounded-xl border text-xs font-bold ${
                  isPositive
                    ? "text-apple-green bg-apple-green/15 border-apple-green/30"
                    : "text-apple-red bg-apple-red/15 border-apple-red/30"
                }`}
              >
                <div>
                  {pnlSign}${position.unrealized_pnl != null ? safeFixed(Math.abs(position.unrealized_pnl)) : "—"}
                </div>
                <div className="text-[10px] opacity-80">
                  {pnlSign}{position.unrealized_pnl_pct != null ? safeFixed(position.unrealized_pnl_pct * 100) : "—"}%
                </div>
              </div>
            )}

            {/* Quick Mini Actions */}
            {position && (
              <div className="flex items-center space-x-1">
                <button
                  title="Lock Breakeven Stop"
                  onClick={() => onTightenStop(position.symbol, position.entry_price)}
                  disabled={!isConnected}
                  className="p-2 rounded-xl bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-amber-400 transition-colors disabled:opacity-40 disabled:pointer-events-none"
                >
                  <Shield className="w-4 h-4" />
                </button>
                <button
                  title="Emergency Flatten"
                  onClick={() => onFlattenPosition(position.symbol)}
                  disabled={!isConnected}
                  className="p-2 rounded-xl bg-apple-red/15 hover:bg-apple-red/25 border border-apple-red/30 text-apple-red transition-colors disabled:opacity-40 disabled:pointer-events-none"
                >
                  <Square className="w-4 h-4 fill-current" />
                </button>
              </div>
            )}
          </div>
        </div>
      </motion.div>

      {/* Expanded Full-Screen Modal Sheet */}
      <AnimatePresence>
        {isExpanded && (
          <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center">
            {/* Backdrop Blur Overlay */}
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setIsExpanded(false)}
              className="absolute inset-0 bg-black/80 backdrop-blur-2xl"
            />

            {/* Modal Sheet Content */}
            <motion.div
              drag="y"
              dragListener={false}
              dragControls={dragControls}
              dragConstraints={{ top: 0 }}
              dragElastic={{ top: 0, bottom: 0.5 }}
              onDragEnd={handleDragEnd}
              initial={{ y: "100%" }}
              animate={{ y: 0 }}
              exit={{ y: "100%" }}
              transition={springConfig}
              className="relative w-full max-w-2xl max-h-[92vh] overflow-y-auto no-scrollbar rounded-t-[32px] sm:rounded-[32px] bg-[#0c0c12] border border-white/10 shadow-2xl p-5 sm:p-6 space-y-5"
            >
              {/* Drag Handle Bar */}
              <div
                onPointerDown={(e) => dragControls.start(e)}
                className="w-12 h-1.5 bg-white/25 rounded-full mx-auto cursor-grab active:cursor-grabbing mb-2 touch-none"
              />

              {/* Modal Header */}
              <div className="flex items-start justify-between">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold uppercase tracking-widest text-apple-purple">
                      Active Primary Trade
                    </span>
                    {position?.strategy_id && (
                      <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/10 text-neutral-300 uppercase font-semibold">
                        {position.strategy_id}
                      </span>
                    )}
                  </div>

                  <h2 className="text-2xl font-black text-white tracking-tight mt-0.5">
                    {position ? `${position.symbol} • ${position.side} ${position.shares} Shares` : "No Open Position"}
                  </h2>
                  <p className="text-xs text-neutral-400">
                    Real-time bracket management & deterministic execution
                  </p>
                </div>

                <button
                  onClick={() => setIsExpanded(false)}
                  className="p-2 rounded-full bg-white/[0.06] hover:bg-white/[0.12] text-neutral-400 hover:text-white transition-colors"
                >
                  <ChevronDown className="w-5 h-5" />
                </button>
              </div>

              {/* Primary PnL and Price Glance */}
              {position && (
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                  <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                    <span className="text-[11px] text-neutral-400 block">Unrealized PnL</span>
                    <span
                      className={`text-lg font-bold num-tabular ${
                        isPositive ? "text-apple-green" : "text-apple-red"
                      }`}
                    >
                      {pnlSign}${position.unrealized_pnl != null ? safeFixed(Math.abs(position.unrealized_pnl)) : "—"}
                    </span>
                  </div>

                  <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                    <span className="text-[11px] text-neutral-400 block">Entry Price</span>
                    <span className="text-lg font-bold text-white num-tabular">
                      ${safeFixed(position.entry_price)}
                    </span>
                  </div>

                  <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                    <span className="text-[11px] text-neutral-400 block">Current Price</span>
                    <span className="text-lg font-bold text-white num-tabular">
                      ${safeFixed(position.market_price)}
                    </span>
                  </div>

                  <div className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06]">
                    <span className="text-[11px] text-neutral-400 block">Market Value</span>
                    <span className="text-lg font-bold text-neutral-200 num-tabular">
                      ${safeLocale(position.market_value)}
                    </span>
                  </div>
                </div>
              )}

              {/* Real-time Candlestick & Bracket Chart */}
              {position ? (
                <div>
                  <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-400 mb-2 flex items-center gap-1.5">
                    <Activity className="w-3.5 h-3.5 text-apple-teal" /> Live Ticker & Bracket Levels
                  </h3>
                  <LiveChart position={position} height={260} />
                </div>
              ) : (
                <div className="h-48 rounded-2xl bg-black/40 border border-white/[0.06] flex items-center justify-center text-xs text-neutral-500">
                  Awaiting signal breakout or position entry...
                </div>
              )}

              {/* Manual Intervention Controls */}
              <div>
                <h3 className="text-xs font-bold uppercase tracking-wider text-neutral-400 mb-2">
                  Manual Intervention Controls
                </h3>
                <ManualControls
                  position={position}
                  isConnected={isConnected}
                  onFlattenPosition={onFlattenPosition}
                  onFlattenAll={onFlattenAll}
                  onTightenStop={onTightenStop}
                />
              </div>

              {/* Execution Audit Log View */}
              <div>
                <ExecutionLog records={recentActivity} maxItems={8} />
              </div>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </>
  );
}
