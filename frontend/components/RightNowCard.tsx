"use client";

interface RightNowCardProps {
  sentence: string;
  tradesToday: number;
  wins: number;
  losses: number;
  countdownLabel: string;
  countdownValue: string;
}

export default function RightNowCard({
  sentence,
  tradesToday,
  wins,
  losses,
  countdownLabel,
  countdownValue,
}: RightNowCardProps) {
  const total = Math.max(1, wins + losses);
  const winPct = (wins / total) * 100;

  return (
    <div className="rise hover-card relative flex flex-col gap-5 overflow-hidden rounded-[28px] bg-darkcard p-6 sm:p-7 text-white" style={{ animationDelay: "160ms" }}>
      <div className="drift pointer-events-none absolute -right-20 -top-28 h-72 w-72 rounded-full opacity-50 blur-3xl" style={{ background: "#C47A88" }} />
      <div className="drift2 pointer-events-none absolute -left-16 -bottom-32 h-72 w-72 rounded-full opacity-40 blur-3xl" style={{ background: "#6E9C82" }} />

      <div className="relative text-sm" style={{ color: "#C9CCD9" }}>
        Right now
      </div>
      <div className="relative font-display text-2xl sm:text-3xl font-medium leading-snug" data-testid="right-now-sentence">
        {sentence}
      </div>

      <div className="relative mt-auto grid grid-cols-3 gap-2 sm:gap-3">
        <div className="rounded-2xl border p-3 sm:p-4" style={{ background: "rgba(255,255,255,0.1)", borderColor: "rgba(255,255,255,0.14)" }}>
          <div className="text-xs" style={{ color: "#C9CCD9" }}>Trades today</div>
          <div className="tabular-nums text-2xl sm:text-3xl font-semibold" style={{ color: "#F2C9A0" }}>{tradesToday}</div>
        </div>
        <div className="rounded-2xl border p-3 sm:p-4" style={{ background: "rgba(255,255,255,0.1)", borderColor: "rgba(255,255,255,0.14)" }}>
          <div className="text-xs" style={{ color: "#C9CCD9" }}>Won vs lost</div>
          <div className="tabular-nums text-2xl sm:text-3xl font-semibold">
            <span style={{ color: "#A9D3BC" }}>{wins}</span>{" "}
            <span className="text-sm font-medium" style={{ color: "#C9CCD9" }}>vs</span>{" "}
            <span style={{ color: "#E8B09E" }}>{losses}</span>
          </div>
          <div className="mt-1.5 h-1.5 overflow-hidden rounded-full" style={{ background: wins + losses === 0 ? "rgba(255,255,255,0.18)" : "#E8B09E" }}>
            {wins + losses > 0 && <div className="grow h-full rounded-full" style={{ width: `${winPct}%`, background: "#A9D3BC" }} />}
          </div>
        </div>
        <div className="rounded-2xl border p-3 sm:p-4" style={{ background: "rgba(255,255,255,0.1)", borderColor: "rgba(255,255,255,0.14)" }}>
          <div className="text-xs" style={{ color: "#C9CCD9" }}>{countdownLabel}</div>
          <div className="tabular-nums text-2xl sm:text-3xl font-semibold" style={{ color: "#BCCBEA" }}>{countdownValue}</div>
        </div>
      </div>
    </div>
  );
}
