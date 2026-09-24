"use client";

import { Check, Minus, X } from "lucide-react";
import { SwingCandidate } from "@/types/trading";
import { companyName, formatMoney, shortDate } from "@/lib/plain";

interface SwingCandidateWatchlistProps {
  candidates?: SwingCandidate[];
  loading?: boolean;
}

type CheckState = "pass" | "fail" | "unknown";

interface PlainCheck {
  state: CheckState;
  label: string;
  detail: string;
}

function buildChecks(c: SwingCandidate): PlainCheck[] {
  const sma200Pass = c.sma_200_pass ?? c.above_200_sma ?? false;
  const rsPass = c.rs_pass ?? c.relative_strength_ok ?? false;
  const rsStock = c.rs_60d_stock ?? c.rs_stock_60d ?? 0;
  const rsQqq = c.rs_60d_qqq ?? c.rs_qqq_60d ?? 0;
  const rsiPass = c.rsi_pass ?? c.panic_trigger ?? false;
  const earningsDate = c.next_earnings_date ?? c.earnings_date ?? null;

  // F11: earnings check has three states - pass, fail, unknown - and never passes on missing data.
  let earningsState: CheckState;
  let earningsDetail: string;
  if (!earningsDate) {
    earningsState = "unknown";
    earningsDetail = "No date on file";
  } else if (c.earnings_blackout) {
    earningsState = "fail";
    earningsDetail = `Report soon: ${shortDate(earningsDate)}`;
  } else {
    earningsState = "pass";
    earningsDetail = `Next one ${shortDate(earningsDate)}`;
  }

  return [
    {
      state: sma200Pass ? "pass" : "fail",
      label: "Long-term uptrend",
      detail: sma200Pass ? "Yes, well above its usual price" : "Not yet, below its usual price",
    },
    {
      state: rsPass ? "pass" : "fail",
      label: "Beating the market",
      detail: `${rsStock >= 0 ? "Up" : "Down"} ${Math.abs(rsStock).toFixed(1)}% vs market ${rsQqq >= 0 ? "+" : ""}${rsQqq.toFixed(1)}% (2 months)`,
    },
    {
      state: rsiPass ? "pass" : "fail",
      label: "Had a sudden drop",
      detail: rsiPass ? "Yes, a sharp 2-day drop" : "Not yet, it is still climbing",
    },
    { state: earningsState, label: "No earnings report soon", detail: earningsDetail },
  ];
}

function CheckIcon({ state }: { state: CheckState }) {
  if (state === "pass") return <Check className="h-3.5 w-3.5" aria-hidden="true" />;
  if (state === "fail") return <X className="h-3.5 w-3.5" aria-hidden="true" />;
  return <Minus className="h-3.5 w-3.5" aria-hidden="true" />;
}

const BANDS = [
  "linear-gradient(90deg, #8FB8A0, #7E9CC8)",
  "linear-gradient(90deg, #7E9CC8, #8189C4)",
  "linear-gradient(90deg, #8189C4, #A884B5)",
  "linear-gradient(90deg, #A884B5, #D8A0A8)",
];

export default function SwingCandidateWatchlist({ candidates = [], loading = false }: SwingCandidateWatchlistProps) {
  return (
    <section className="flex flex-col gap-4" data-testid="swing-candidate-watchlist">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-display text-2xl sm:text-3xl font-semibold text-ink">Companies it is watching</h2>
        <div className="hidden sm:block text-sm text-muted">It only buys when all 4 checks turn green.</div>
      </div>

      {loading && candidates.length === 0 ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-64 animate-pulse rounded-[26px] border border-line bg-white/60" />
          ))}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          {candidates.map((cand, i) => {
            const checks = buildChecks(cand);
            const passed = checks.filter((c) => c.state === "pass").length;
            return (
              <article
                key={cand.symbol}
                className="rise hover-card flex flex-col gap-4 overflow-hidden rounded-[26px] border border-line bg-white"
                style={{ animationDelay: `${120 + i * 80}ms` }}
                data-testid={`candidate-card-${cand.symbol}`}
              >
                <div className="h-2.5" style={{ background: BANDS[i % BANDS.length] }} />
                <div className="flex flex-col gap-3.5 px-5 pb-5">
                  <div className="flex items-baseline justify-between gap-2">
                    <div className="flex flex-col gap-0.5">
                      <span className="font-display text-lg sm:text-xl font-semibold text-ink">{companyName(cand.symbol)}</span>
                      <span className="text-xs text-muted">
                        {cand.symbol} &middot; {formatMoney(cand.price ?? cand.close ?? 0)}
                        {cand.date ? ` · as of ${shortDate(cand.date)}` : ""}
                      </span>
                    </div>
                    <span
                      className="shrink-0 whitespace-nowrap rounded-full px-2.5 py-1 text-sm font-bold"
                      style={passed >= 3 ? { background: "#E4EFE7", color: "#2F6B4C" } : { background: "#EEEFF7", color: "#3E4478" }}
                    >
                      {passed} of 4
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full" style={{ background: "#F3ECDF" }}>
                    <div className="grow h-full rounded-full" style={{ width: `${passed * 25}%`, background: "linear-gradient(90deg, #5E9A7A, #7E9CC8)" }} />
                  </div>
                  <div className="flex flex-col gap-2.5">
                    {checks.map((chk) => (
                      <div key={chk.label} className="flex items-start gap-2.5 text-sm leading-snug">
                        <span
                          className="mt-0.5 flex h-[22px] w-[22px] flex-shrink-0 items-center justify-center rounded-full"
                          style={
                            chk.state === "pass"
                              ? { background: "#5E9A7A", color: "#FFFFFF" }
                              : chk.state === "fail"
                              ? { background: "#F3ECDF", color: "#5D5A73" }
                              : { background: "#F3ECDF", color: "#5D5A73" }
                          }
                        >
                          <CheckIcon state={chk.state} />
                        </span>
                        <span className="flex flex-col">
                          <span className="font-semibold text-ink">{chk.label}</span>
                          <span className="text-muted">{chk.detail}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
