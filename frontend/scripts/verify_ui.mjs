import fs from "node:fs";
import path from "node:path";
import assert from "node:assert";

const FRONTEND_DIR = path.resolve(import.meta.dirname, "..");

console.log("🔍 Verifying plain-language dashboard architecture (2026-09-24 redesign, bright palette 2026-10-04)...");

// 1. Verify file inventory (post-redesign contract).
const requiredFiles = [
  "package.json",
  "tsconfig.json",
  "tailwind.config.js",
  "postcss.config.js",
  "app/layout.tsx",
  "app/page.tsx",
  "app/globals.css",
  "types/trading.ts",
  "lib/plain.ts",
  "lib/apiBase.ts",
  "hooks/useTradingStream.ts",
  "hooks/useTodayLedger.ts",
  "hooks/useActionButton.ts",
  "hooks/useHealthLimits.ts",
  "components/Header.tsx",
  "components/BalanceCard.tsx",
  "components/RightNowCard.tsx",
  "components/SegmentedModeToggle.tsx",
  "components/StrategyCard.tsx",
  "components/StrategyTable.tsx",
  "components/HoldingNow.tsx",
  "components/MarketMoodCard.tsx",
  "components/ResultsPanel.tsx",
  "components/SafetyCard.tsx",
  "components/ExecutionLog.tsx",
  "components/SwingTelemetryBar.tsx",
  "components/SwingCandidateWatchlist.tsx",
  "components/ActiveSwingPositionsTable.tsx",
];

for (const file of requiredFiles) {
  const fullPath = path.join(FRONTEND_DIR, file);
  assert(fs.existsSync(fullPath), `Missing required UI file: ${file}`);
  const content = fs.readFileSync(fullPath, "utf8");
  assert(content.length > 50, `File appears truncated or empty: ${file}`);
  console.log(`  ✅ Verified ${file} (${content.length} bytes)`);
}

// 1b. The old dark-theme-only components must be gone (nothing imports them any more).
const deletedFiles = [
  "components/AmbientBackground.tsx",
  "components/ActivePositionTray.tsx",
  "components/LiveChart.tsx",
  "components/ManualControls.tsx",
  "components/StrategyCarousel.tsx", // replaced by StrategyTable (2026-09-29 compact dashboard)
  "components/RecentTrades.tsx", // replaced by ResultsPanel (2026-10-04 bright dashboard)
  "components/TradeHistory.tsx", // the history drawer is now the always-visible Results panel
];
for (const file of deletedFiles) {
  assert(!fs.existsSync(path.join(FRONTEND_DIR, file)), `Old dark-theme component should be deleted: ${file}`);
}
console.log("  ✅ Verified old dark-theme-only components were removed");

// 2. Verify the bright light theme tokens in tailwind.config.js and globals.css (2026-10-04 replaced the muted palette).
const tailwindConfig = fs.readFileSync(path.join(FRONTEND_DIR, "tailwind.config.js"), "utf8");
for (const token of ["#EEF1FA", "#0E1330", "#5B6283", "#2B4BFF", "#0A7D53", "#C2300F", "#C6F432", "#8A4B00"]) {
  assert(tailwindConfig.includes(token), `Missing light-theme token ${token} in tailwind config`);
}
console.log("  ✅ Verified bright design tokens in tailwind.config.js");

const globalsCss = fs.readFileSync(path.join(FRONTEND_DIR, "app/globals.css"), "utf8");
assert(globalsCss.includes("prefers-reduced-motion"), "Missing prefers-reduced-motion block in globals.css (F14)");
assert(globalsCss.includes("@keyframes breathe"), "Missing breathe keyframe in globals.css");
assert(!globalsCss.includes("@keyframes drift"), "The blurred drift blobs were removed (2026-10-04): no drift keyframe expected");
assert(globalsCss.includes("tabular-nums"), "Missing tabular-nums utility in globals.css");
console.log("  ✅ Verified reduced-motion block and keyframes");

// 3. Fonts: @fontsource npm packages, never next/font/google (F15).
const pkgJson = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, "package.json"), "utf8"));
assert(pkgJson.dependencies["@fontsource/bricolage-grotesque"], "Missing @fontsource/bricolage-grotesque dependency (F15)");
assert(!pkgJson.dependencies["@fontsource/fraunces"], "Fraunces was replaced by Bricolage Grotesque (2026-10-04)");
assert(pkgJson.dependencies["@fontsource/instrument-sans"], "Missing @fontsource/instrument-sans dependency (F15)");
const layout = fs.readFileSync(path.join(FRONTEND_DIR, "app/layout.tsx"), "utf8");
assert(layout.includes("@fontsource/bricolage-grotesque"), "layout.tsx must import @fontsource/bricolage-grotesque");
assert(!layout.includes("fraunces"), "layout.tsx must not import Fraunces any more");
assert(layout.includes("@fontsource/instrument-sans"), "layout.tsx must import @fontsource/instrument-sans");
assert(!layout.includes('from "next/font'), "layout.tsx must NOT import from next/font (F15)");
assert(!/maximumScale\s*:/.test(layout), "layout.tsx must not set a maximumScale value (F16)");
assert(!/userScalable\s*:/.test(layout), "layout.tsx must not set a userScalable value (F16)");
console.log("  ✅ Verified offline-safe fonts (F15) and pinch-zoom left enabled (F16)");

// 4. Verify WebSocket URL and action payloads in useTradingStream.ts are UNCHANGED.
const streamHook = fs.readFileSync(path.join(FRONTEND_DIR, "hooks/useTradingStream.ts"), "utf8");
assert(streamHook.includes("ws://127.0.0.1:8005/ws/ui"), "Missing default ws://127.0.0.1:8005/ws/ui in hook");
for (const action of [
  "FLATTEN_POSITION",
  "FLATTEN_ALL",
  "TIGHTEN_STOP",
  "SWING_EXIT_NEXT_OPEN",
  "SWING_EXIT_IMMEDIATE",
  "SWING_TIGHTEN_STOP",
]) {
  assert(streamHook.includes(action), `Missing ${action} action handling in useTradingStream.ts`);
}
console.log("  ✅ Verified WebSocket action payloads are unchanged (port 8005, all 6 actions)");

// 5. Never use window.confirm/alert/prompt anywhere in components/hooks/app.
const scannedDirs = ["app", "components", "hooks", "lib"];
for (const dir of scannedDirs) {
  const full = path.join(FRONTEND_DIR, dir);
  if (!fs.existsSync(full)) continue;
  for (const file of fs.readdirSync(full)) {
    if (!file.endsWith(".ts") && !file.endsWith(".tsx")) continue;
    const content = fs.readFileSync(path.join(full, file), "utf8");
    assert(!/window\.(confirm|alert|prompt)\(/.test(content), `${dir}/${file} must not use window.confirm/alert/prompt`);
  }
}
console.log("  ✅ Verified no window.confirm/alert/prompt usage");

// 6. Verify data-testids are present on their equivalent new elements (rule 6 of the plan).
const testidLocations = {
  "risk-telemetry": "components/SafetyCard.tsx",
  "segmented-mode-toggle": "components/SegmentedModeToggle.tsx",
  "mode-tab-intraday": "components/SegmentedModeToggle.tsx",
  "mode-tab-swing": "components/SegmentedModeToggle.tsx",
  "strategy-window": "components/StrategyCard.tsx",
  "strategy-decisions": "components/StrategyCard.tsx",
  "window-badge": "components/StrategyCard.tsx",
  "swing-telemetry": "components/SwingTelemetryBar.tsx",
  "swing-candidate-watchlist": "components/SwingCandidateWatchlist.tsx",
  "swing-schedule": "components/SwingTelemetryBar.tsx",
  "active-swing-positions": "components/ActiveSwingPositionsTable.tsx",
  "market-mood": "components/MarketMoodCard.tsx",
  // 2026-10-04 bright dashboard: Results replaces the "What it did today" list and the history drawer
  "results-panel": "components/ResultsPanel.tsx",
  "results-tab-day": "components/ResultsPanel.tsx",
  "results-tab-playbook": "components/ResultsPanel.tsx",
  "results-group": "components/ResultsPanel.tsx",
  "results-trade": "components/ResultsPanel.tsx",
  "trade-detail": "components/ResultsPanel.tsx",
  // 2026-09-29 compact dashboard: playbooks are rows in one table
  "strategy-table": "components/StrategyTable.tsx",
  "strategy-row-toggle": "components/StrategyCard.tsx",
  "strategy-details": "components/StrategyCard.tsx",
  "strategy-status": "components/StrategyCard.tsx",
  "orb-init-error": "components/StrategyCard.tsx",
  "tri-engine-broker-issue": "components/StrategyCard.tsx",
  // 2026-10-05 overnight row and the 3:45 PM handoff (PLAN_2026_10_05_overnight_row_and_handoff.md)
  "strategy-row-overnight": "components/OvernightPlaybookRow.tsx",
  "overnight-steps": "components/OvernightPlaybookRow.tsx",
  "overnight-holds-link": "components/OvernightPlaybookRow.tsx",
  "handoff-note": "components/StrategyTable.tsx",
  "axis-labels": "components/StrategyTable.tsx",
  "night-legend": "components/StrategyTable.tsx",
  "handoff-band": "components/StrategyCard.tsx",
  "night-part": "components/StrategyCard.tsx",
  "x6-note": "components/StrategyCard.tsx",
};
for (const [testid, file] of Object.entries(testidLocations)) {
  const content = fs.readFileSync(path.join(FRONTEND_DIR, file), "utf8");
  // a testid is either a literal attribute or a `testid: "..."` entry that a component maps into the attribute
  assert(content.includes(`data-testid="${testid}"`) || content.includes(`testid: "${testid}"`), `Missing data-testid="${testid}" in ${file}`);
}
console.log("  ✅ Verified all required data-testids are present on their new elements");

// 7. Verify all five strategy ids are themed and fixed OR15 controls are present.
const plainLib = fs.readFileSync(path.join(FRONTEND_DIR, "lib/plain.ts"), "utf8");
for (const id of ["orb", "vwap_pullback", "news_momentum", "mean_reversion", "tsla_or15_retest"]) {
  assert(plainLib.includes(`${id}:`), `Missing strategy theme for ${id} in lib/plain.ts`);
}
const card = fs.readFileSync(path.join(FRONTEND_DIR, "components/StrategyCard.tsx"), "utf8");
assert(card.includes("strategyTheme"), "StrategyCard.tsx must use strategyTheme() from lib/plain.ts");
assert(card.includes('data-testid="or15-details"') && card.includes("Offline replay"), "OR15 must distinguish replay from paper");
assert(card.includes('data-testid="orb-details"') && card.includes("held at Alpaca"), "ORB card must show its mode, step and bracket trades");
assert(card.includes('data-testid="orb-alert"'), "ORB card must show ORB's plain-language alerts (orphan positions)");
assert(plainLib.includes('case "NO_TRADE_TODAY"') && card.includes("amber:"), "ORB's no-trade-today state must be amber, never 'watching'");
assert(card.includes("orbOpen"), "ORB card bottom line must include its open P&L");
assert(card.includes('data-testid="orb-resolve-orphan"') && card.includes("/api/orb/resolve-orphan"), "ORB orphan alert must offer the audited resolve action");
assert(plainLib.includes("Opening Range Breakout (ORBStraddle rules)"), "ORB card must use the ORBStraddle-rules name");
const holding = fs.readFileSync(path.join(FRONTEND_DIR, "components/HoldingNow.tsx"), "utf8");
assert(holding.includes("!position.fixed_protection") && holding.includes("Safety exit stays fixed"), "OR15 must disable stop movement");
console.log("  ✅ Verified all five strategy themes and OR15 fixed protection controls");

// 8. Verify plain-language copy replaced jargon in the swing and safety components (F10).
const safetyCard = fs.readFileSync(path.join(FRONTEND_DIR, "components/SafetyCard.tsx"), "utf8");
assert(safetyCard.includes("Close all quick trades now"), "SafetyCard must use the B1 button copy 'Close all quick trades now'");
assert(!safetyCard.includes("Stop everything"), "SafetyCard must not use the old 'Stop everything' copy (B1 changed the semantics)");
console.log("  ✅ Verified B1 button copy change (never 'stop everything')");

// 9. Verify no horizontal-scroll-risk full-bleed elements remain from the old design.
const globals = globalsCss;
assert(globals.includes("overflow-x: hidden"), "globals.css must keep overflow-x: hidden on body");
console.log("  ✅ Verified overflow-x guard on body");

// 10. Verify safe UI port 3005 in package.json (unchanged infra contract).
assert(pkgJson.scripts.dev.includes("3005"), "dev script must run on safe port 3005");
assert(pkgJson.scripts.start.includes("3005"), "start script must run on safe port 3005");
console.log("  ✅ Verified UI safe port 3005 allocation (avoiding host port 3000 collision)");

console.log("\n🎉 All plain-language dashboard architectural checks PASSED!");
