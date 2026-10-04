"use client";

export type TradingMode = "intraday" | "swing";

interface SegmentedModeToggleProps {
  mode: TradingMode;
  onModeChange: (mode: TradingMode) => void;
}

export default function SegmentedModeToggle({ mode, onModeChange }: SegmentedModeToggleProps) {
  const tabClass = (on: boolean) =>
    `min-h-[44px] flex-1 lg:flex-none rounded-full px-4 sm:px-5 text-sm font-semibold transition-colors ${
      on ? "bg-darkcard text-white shadow" : "bg-transparent text-[#2A3150] hover:bg-white/60"
    }`;

  return (
    <nav
      className="flex w-full items-center gap-1 rounded-full border border-line bg-white/75 p-1 lg:w-auto"
      data-testid="segmented-mode-toggle"
    >
      <button
        type="button"
        onClick={() => onModeChange("intraday")}
        aria-pressed={mode === "intraday"}
        className={tabClass(mode === "intraday")}
        data-testid="mode-tab-intraday"
      >
        <span className="sm:hidden">Quick trades</span>
        <span className="hidden sm:inline">Quick trades (same day)</span>
      </button>
      <button
        type="button"
        onClick={() => onModeChange("swing")}
        aria-pressed={mode === "swing"}
        className={tabClass(mode === "swing")}
        data-testid="mode-tab-swing"
      >
        <span className="sm:hidden">Slow trades</span>
        <span className="hidden sm:inline">Slow trades (a few days)</span>
      </button>
    </nav>
  );
}
