/** SVG paths copied from docs/gut_dashboard/Icons.dc.html. */
const PATHS: Record<string, string> = {
  "orb": "M13 10H4.5A1.5 1.5 0 0 0 3 11.5v7A1.5 1.5 0 0 0 4.5 20h7a1.5 1.5 0 0 0 1.5-1.5V15M6 16l3-3 2 1.5L20 5M15 5h5v5",
  "vwap_pullback": "M3 17L17 10.8M3 11c2.5-4 4.5-4.5 6-2.5s2 4.5 3 4.5c2 0 4-5 8-9M15 4h5v5",
  "news_momentum": "M4 10h3l7-4v12l-7-4H4a1 1 0 0 1-1-1v-2a1 1 0 0 1 1-1zM7 14l1.2 5H10M19 6l-2.5 4.5H20L17.5 15",
  "mean_reversion": "M3 17h3l3.5-12L12 13M14 7c3.5 0 6 3 6 8M17.5 12.5L20 15l2.5-2.5",
  "tsla_asymmetric_dual": "M9 12H4.5A1.5 1.5 0 0 0 3 13.5v6A1.5 1.5 0 0 0 4.5 21h6a1.5 1.5 0 0 0 1.5-1.5V16M6 17L19 4M14 4h5v5M11.5 8.5h3v3M18 13l-2 3.5h3L17 20",
  "cde_asymmetric_dual": "M9 12H4.5A1.5 1.5 0 0 0 3 13.5v6A1.5 1.5 0 0 0 4.5 21h6a1.5 1.5 0 0 0 1.5-1.5V16M6 17L19 4M14 4h5v5M11.5 8.5h3v3M17.5 14l2.5 3-2.5 3-2.5-3z",
  "overnight": "M14.65 14.31A5.85 5.85 0 1 1 8.29 7.95 4.55 4.55 0 0 0 14.65 14.31zM11 4.5c4.5-2 9 0 10 5.5M18.6 7.6L21 10l2-2.6",
  "running": "M2 12h4l2.5-6 4 12 2.5-6H18M20.5 12h.01",
  "paused": "M21 12a9 9 0 1 1-4.5-7.8M10 9v6M14 9v6",
  "closed": "M18.85 12.87A7.65 7.65 0 1 1 10.53 4.55 5.95 5.95 0 0 0 18.85 12.87zM19 2.5v4M17 4.5h4",
  "attention": "M4 5.5L7 3M20 5.5L17 3M7.5 19l-1.5 2M16.5 19l1.5 2M12 9.5V13M12 16h.01M19 13a7 7 0 1 1-14 0 7 7 0 0 1 14 0z",
  "flat": "M3 13.5V18a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-4.5M3 13.5h5l1.5 2h5l1.5-2h5M5.5 13.5L7.5 5h9l2 8.5M10 9.5h4",
  "done": "M21 12a9 9 0 1 1-9-9M8 12l3 3 9-9"
};
export default function PlaybookIcon({ name, className = "h-5 w-5" }: { name: string; className?: string }) {
  const key = name.startsWith("overnight") ? "overnight" : name === "tsla_or15_retest" ? "tsla_asymmetric_dual" : name;
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={PATHS[key] ?? PATHS.flat} /></svg>;
}
