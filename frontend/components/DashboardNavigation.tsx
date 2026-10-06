"use client";

export type DashboardPage = "today" | "history";
export type DashboardDestination = "today" | "holdings" | "history" | "plans" | "controls";

interface DashboardNavigationProps {
  page: DashboardPage;
  onNavigate: (destination: DashboardDestination) => void;
}

const ITEMS: ReadonlyArray<{ id: DashboardDestination; label: string }> = [
  { id: "today", label: "Today" },
  { id: "holdings", label: "Holdings" },
  { id: "history", label: "History" },
  { id: "plans", label: "Plans" },
  { id: "controls", label: "Controls" },
];

export default function DashboardNavigation({ page, onNavigate }: DashboardNavigationProps) {
  return (
    <nav aria-label="Dashboard" className="w-full">
      <div className="grid grid-cols-5 rounded-xl border border-line bg-white p-1">
        {ITEMS.map((item) => {
          const selected =
            item.id === "history"
              ? page === "history"
              : item.id === "today" && page === "today";
          return (
            <button
              key={item.id}
              type="button"
              onClick={() => onNavigate(item.id)}
              aria-current={selected ? "page" : undefined}
              className={`min-h-[44px] min-w-0 rounded-lg px-1 text-xs font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-darkcard sm:px-3 sm:text-sm ${
                selected ? "bg-darkcard text-white" : "text-muted hover:bg-ground hover:text-ink"
              }`}
            >
              {item.label}
            </button>
          );
        })}
      </div>
    </nav>
  );
}
