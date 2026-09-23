"use client";

import React from "react";
import { SwingCandidate } from "@/types/trading";
import { Sparkles, CheckCircle2, XCircle, AlertTriangle, Flame, Calendar, Info } from "lucide-react";

interface SwingCandidateWatchlistProps {
  candidates?: SwingCandidate[];
}

const DEFAULT_CANDIDATE_SYMBOLS = ["LRCX", "KLAC", "MU", "AMD", "GS"];

export default function SwingCandidateWatchlist({ candidates = [] }: SwingCandidateWatchlistProps) {
  // If no candidates loaded yet, build fallback placeholders for the 5 certified stocks
  const displayCandidates: Partial<SwingCandidate>[] =
    candidates.length > 0
      ? candidates
      : DEFAULT_CANDIDATE_SYMBOLS.map((s) => ({
          symbol: s,
          price: 0,
          sma_200: 0,
          sma_200_pass: false,
          rs_stock_60d: 0,
          rs_qqq_60d: 0,
          rs_pass: false,
          rsi_2: 50.0,
          rsi_pass: false,
          earnings_blackout: false,
          earnings_date: null,
          daily_atr_14: 0,
          status: "WATCHING",
        }));

  return (
    <section className="px-4 py-2" data-testid="swing-candidate-watchlist">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-semibold text-apple-teal uppercase tracking-wider">
            <Sparkles className="w-3.5 h-3.5 text-apple-teal" />
            <span>Certified Universe</span>
          </div>
          <h2 className="text-xl font-bold text-white tracking-tight">
            5-Stock Candidate Watchlist
          </h2>
        </div>
        <div className="text-right">
          <span className="text-xs text-neutral-400 font-medium block">
            Scan: 16:00 ET Close
          </span>
          <span className="text-[10px] text-neutral-500">
            Rules 1–4 Qualification
          </span>
        </div>
      </div>

      {/* Desktop Table View (sm and larger) */}
      <div className="hidden sm:block overflow-hidden rounded-2xl bg-white/[0.02] border border-white/[0.06] backdrop-blur-2xl shadow-xl">
        <table className="w-full text-left border-collapse">
          <thead>
            <tr className="border-b border-white/[0.06] text-[11px] font-semibold text-neutral-400 uppercase tracking-wider bg-white/[0.02]">
              <th className="py-3 px-4">Symbol / Price</th>
              <th className="py-3 px-3">Rule 1: 200 SMA</th>
              <th className="py-3 px-3">Rule 2: 60d RS vs QQQ</th>
              <th className="py-3 px-3">Rule 3: RSI(2) Dip</th>
              <th className="py-3 px-3">Rule 4: Earnings</th>
              <th className="py-3 px-3">ATR(14)</th>
              <th className="py-3 px-4 text-right">Status</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04] text-xs">
            {displayCandidates.map((cand) => {
              const sym = cand.symbol || "UNKNOWN";
              const price = cand.price ?? cand.close ?? 0;
              const sma200 = cand.sma_200 ?? 0;
              const sma200Pass = cand.sma_200_pass ?? cand.above_200_sma ?? false;
              const rsStock = cand.rs_60d_stock ?? cand.rs_stock_60d ?? 0;
              const rsQqq = cand.rs_60d_qqq ?? cand.rs_qqq_60d ?? 0;
              const rsPass = cand.rs_pass ?? cand.relative_strength_ok ?? false;
              const rsi2 = cand.rsi_2 ?? 50;
              const rsiPass = cand.rsi_pass ?? cand.panic_trigger ?? rsi2 < 10.0;
              const blackout = cand.earnings_blackout ?? false;
              const earningsDate = cand.earnings_date ?? cand.next_earnings_date;
              const atr = cand.atr_14 ?? cand.daily_atr_14 ?? 0;
              const status = cand.status || "WATCHING";

              return (
                <tr
                  key={sym}
                  className="hover:bg-white/[0.03] transition-colors duration-150"
                  data-testid={`candidate-row-${sym}`}
                >
                  {/* Symbol & Price */}
                  <td className="py-3 px-4">
                    <div className="font-bold text-white text-sm tracking-wide">
                      {sym}
                    </div>
                    <div className="text-[11px] text-neutral-400 num-tabular">
                      ${price.toFixed(2)}
                    </div>
                  </td>

                  {/* Rule 1: 200 SMA Floor */}
                  <td className="py-3 px-3">
                    <div className="flex items-center gap-1.5">
                      {sma200Pass ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-apple-green flex-shrink-0" />
                      ) : (
                        <XCircle className="w-3.5 h-3.5 text-apple-red flex-shrink-0" />
                      )}
                      <div className="text-[11px]">
                        <span className={sma200Pass ? "text-apple-green font-semibold" : "text-neutral-400"}>
                          ${price.toFixed(1)} &gt; ${sma200.toFixed(1)}
                        </span>
                      </div>
                    </div>
                  </td>

                  {/* Rule 2: 60d RS vs QQQ */}
                  <td className="py-3 px-3">
                    <div className="flex items-center gap-1.5">
                      {rsPass ? (
                        <CheckCircle2 className="w-3.5 h-3.5 text-apple-green flex-shrink-0" />
                      ) : (
                        <XCircle className="w-3.5 h-3.5 text-neutral-500 flex-shrink-0" />
                      )}
                      <span className="text-[11px] num-tabular">
                        <span className={rsPass ? "text-white font-medium" : "text-neutral-400"}>
                          {rsStock >= 0 ? `+${rsStock.toFixed(1)}%` : `${rsStock.toFixed(1)}%`}
                        </span>
                        <span className="text-neutral-500 text-[10px] ml-1">
                          vs {rsQqq >= 0 ? `+${rsQqq.toFixed(1)}%` : `${rsQqq.toFixed(1)}%`}
                        </span>
                      </span>
                    </div>
                  </td>

                  {/* Rule 3: RSI(2) Panic Dip (< 10.0) */}
                  <td className="py-3 px-3">
                    {rsiPass ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse">
                        <Flame className="w-3 h-3 text-amber-400" />
                        {rsi2.toFixed(1)} [PANIC DIP]
                      </span>
                    ) : (
                      <span className="text-neutral-300 text-[11px] num-tabular font-medium">
                        {rsi2.toFixed(1)}
                      </span>
                    )}
                  </td>

                  {/* Rule 4: Earnings Blackout */}
                  <td className="py-3 px-3">
                    {blackout ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-apple-red/20 text-apple-red border border-apple-red/30">
                        <AlertTriangle className="w-3 h-3" />
                        BLACKOUT
                      </span>
                    ) : (
                      <div className="flex items-center gap-1 text-[11px] text-neutral-400">
                        <Calendar className="w-3 h-3 text-neutral-500" />
                        <span>{earningsDate ? earningsDate : "Clear"}</span>
                      </div>
                    )}
                  </td>

                  {/* ATR(14) */}
                  <td className="py-3 px-3 text-[11px] text-neutral-400 num-tabular">
                    ${atr.toFixed(2)}
                  </td>

                  {/* Status Badge */}
                  <td className="py-3 px-4 text-right">
                    <span
                      className={`inline-block px-2.5 py-1 rounded-full text-[10px] font-bold tracking-wider uppercase ${
                        status === "QUALIFIED"
                          ? "bg-apple-green/20 text-apple-green border border-apple-green/40 shadow-sm shadow-apple-green/20"
                          : status === "STAGED"
                          ? "bg-apple-purple/20 text-apple-purple border border-apple-purple/30"
                          : status === "ACTIVE"
                          ? "bg-apple-teal/20 text-apple-teal border border-apple-teal/30"
                          : status === "BLOCKED"
                          ? "bg-apple-red/20 text-apple-red border border-apple-red/30"
                          : "bg-white/[0.04] text-neutral-400 border border-white/[0.06]"
                      }`}
                    >
                      {status}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Mobile Stacked Card View (390px responsive) */}
      <div className="sm:hidden space-y-2.5">
        {displayCandidates.map((cand) => {
          const sym = cand.symbol || "UNKNOWN";
          const price = cand.price ?? cand.close ?? 0;
          const sma200 = cand.sma_200 ?? 0;
          const sma200Pass = cand.sma_200_pass ?? cand.above_200_sma ?? false;
          const rsStock = cand.rs_60d_stock ?? cand.rs_stock_60d ?? 0;
          const rsQqq = cand.rs_60d_qqq ?? cand.rs_qqq_60d ?? 0;
          const rsPass = cand.rs_pass ?? cand.relative_strength_ok ?? false;
          const rsi2 = cand.rsi_2 ?? 50;
          const rsiPass = cand.rsi_pass ?? cand.panic_trigger ?? rsi2 < 10.0;
          const blackout = cand.earnings_blackout ?? false;
          const status = cand.status || "WATCHING";

          return (
            <div
              key={sym}
              className="p-3 rounded-2xl bg-white/[0.03] border border-white/[0.06] backdrop-blur-xl"
              data-testid={`candidate-card-mobile-${sym}`}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-base font-bold text-white">{sym}</span>
                  <span className="text-xs text-neutral-400 num-tabular">${price.toFixed(2)}</span>
                </div>
                <span
                  className={`px-2 py-0.5 rounded-full text-[9px] font-bold tracking-wider uppercase ${
                    status === "QUALIFIED"
                      ? "bg-apple-green/20 text-apple-green border border-apple-green/40"
                      : status === "STAGED"
                      ? "bg-apple-purple/20 text-apple-purple border border-apple-purple/30"
                      : status === "ACTIVE"
                      ? "bg-apple-teal/20 text-apple-teal border border-apple-teal/30"
                      : "bg-white/[0.04] text-neutral-400 border border-white/[0.06]"
                  }`}
                >
                  {status}
                </span>
              </div>

              {/* Grid of checks */}
              <div className="grid grid-cols-2 gap-1.5 text-[11px]">
                <div className="flex items-center gap-1.5 bg-black/30 p-1.5 rounded-lg">
                  {sma200Pass ? (
                    <CheckCircle2 className="w-3 h-3 text-apple-green flex-shrink-0" />
                  ) : (
                    <XCircle className="w-3 h-3 text-neutral-500 flex-shrink-0" />
                  )}
                  <span className="text-neutral-300 truncate">
                    &gt; 200 SMA (${sma200.toFixed(0)})
                  </span>
                </div>

                <div className="flex items-center gap-1.5 bg-black/30 p-1.5 rounded-lg">
                  {rsPass ? (
                    <CheckCircle2 className="w-3 h-3 text-apple-green flex-shrink-0" />
                  ) : (
                    <XCircle className="w-3 h-3 text-neutral-500 flex-shrink-0" />
                  )}
                  <span className="text-neutral-300 truncate">
                    60d RS ({rsStock >= 0 ? `+${rsStock.toFixed(0)}%` : `${rsStock.toFixed(0)}%`})
                  </span>
                </div>

                <div className="flex items-center gap-1.5 bg-black/30 p-1.5 rounded-lg">
                  {rsiPass ? (
                    <Flame className="w-3 h-3 text-amber-400 flex-shrink-0" />
                  ) : (
                    <Info className="w-3 h-3 text-neutral-500 flex-shrink-0" />
                  )}
                  <span className={rsiPass ? "text-amber-300 font-bold" : "text-neutral-300"}>
                    RSI(2): {rsi2.toFixed(1)} {rsiPass && "🔥"}
                  </span>
                </div>

                <div className="flex items-center gap-1.5 bg-black/30 p-1.5 rounded-lg">
                  {blackout ? (
                    <AlertTriangle className="w-3 h-3 text-apple-red flex-shrink-0" />
                  ) : (
                    <CheckCircle2 className="w-3 h-3 text-apple-green flex-shrink-0" />
                  )}
                  <span className={blackout ? "text-apple-red font-bold" : "text-neutral-300"}>
                    {blackout ? "Earnings Blackout" : "Earnings Clear"}
                  </span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
