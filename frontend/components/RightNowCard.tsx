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
    <div className="rise hover-card relative flex flex-col gap-3 overflow-hidden rounded-[22px] bg-darkcard p-4 text-white sm:p-5 lg:flex-row lg:items-center lg:gap-5" style={{ animationDelay: "160ms" }}>
      <div className="flex min-w-0 flex-col gap-1 lg:flex-1">
        <div className="relative text-xs" style={{ color: "#C9CCD9" }}>
          Right now
        </div>
        <div className="relative font-display text-lg font-medium leading-snug sm:text-xl" data-testid="right-now-sentence">
          {sentence}
        </div>
      </div>

      <div className="relative grid grid-cols-3 gap-2 lg:w-[330px] lg:flex-shrink-0">
        <div className="rounded-xl border px-3 py-1.5" style={{ background: "rgba(255,255,255,0.1)", borderColor: "rgba(255,255,255,0.14)" }}>
          <div className="text-[11px]" style={{ color: "#C9CCD9" }}>Trades today</div>
          <div className="tabular-nums text-xl font-semibold" style={{ color: "#F2C9A0" }}>{tradesToday}</div>
        </div>
        <div className="rounded-xl border px-3 py-1.5" style={{ background: "rgba(255,255,255,0.1)", borderColor: "rgba(255,255,255,0.14)" }}>
          <div className="text-[11px]" style={{ color: "#C9CCD9" }}>Won vs lost</div>
          <div className="tabular-nums text-xl font-semibold">
            <span style={{ color: "#A9D3BC" }}>{wins}</span>{" "}
            <span className="text-xs font-medium" style={{ color: "#C9CCD9" }}>vs</span>{" "}
            <span style={{ color: "#E8B09E" }}>{losses}</span>
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full" style={{ background: wins + losses === 0 ? "rgba(255,255,255,0.18)" : "#E8B09E" }}>
            {wins + losses > 0 && <div className="grow h-full rounded-full" style={{ width: `${winPct}%`, background: "#A9D3BC" }} />}
          </div>
        </div>
        <div className="rounded-xl border px-3 py-1.5" style={{ background: "rgba(255,255,255,0.1)", borderColor: "rgba(255,255,255,0.14)" }}>
          <div className="text-[11px]" style={{ color: "#C9CCD9" }}>{countdownLabel}</div>
          <div className="tabular-nums text-xl font-semibold" style={{ color: "#BCCBEA" }}>{countdownValue}</div>
        </div>
      </div>
    </div>
  );
}
