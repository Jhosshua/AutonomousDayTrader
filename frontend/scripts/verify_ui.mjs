import fs from "node:fs";
import path from "node:path";
import assert from "node:assert";

const FRONTEND_DIR = path.resolve(import.meta.dirname, "..");

console.log("🔍 Verifying plain-language dashboard architecture (2026-09-24 redesign)...");

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
  "components/StrategyCarousel.tsx",
  "components/HoldingNow.tsx",
  "components/RecentTrades.tsx",
  "components/SafetyCard.tsx",
  "components/ExecutionLog.tsx",
  "components/TradeHistory.tsx",
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
];
for (const file of deletedFiles) {
  assert(!fs.existsSync(path.join(FRONTEND_DIR, file)), `Old dark-theme component should be deleted: ${file}`);
}
console.log("  ✅ Verified old dark-theme-only components were removed");

// 2. Verify the new "muted palette" light theme tokens in tailwind.config.js and globals.css.
const tailwindConfig = fs.readFileSync(path.join(FRONTEND_DIR, "tailwind.config.js"), "utf8");
for (const token of ["#F7F3EC", "#1D1A33", "#5D5A73", "#2E3244", "#2F6B4C", "#8F4424"]) {
  assert(tailwindConfig.includes(token), `Missing light-theme token ${token} in tailwind config`);
}
console.log("  ✅ Verified light 'muted palette' design tokens in tailwind.config.js");

const globalsCss = fs.readFileSync(path.join(FRONTEND_DIR, "app/globals.css"), "utf8");
assert(globalsCss.includes("prefers-reduced-motion"), "Missing prefers-reduced-motion block in globals.css (F14)");
assert(globalsCss.includes("@keyframes breathe"), "Missing breathe keyframe in globals.css");
assert(globalsCss.includes("@keyframes drift"), "Missing drift keyframe in globals.css");
assert(globalsCss.includes("tabular-nums"), "Missing tabular-nums utility in globals.css");
console.log("  ✅ Verified reduced-motion block and mockup-matched keyframes");

// 3. Fonts: @fontsource npm packages, never next/font/google (F15).
const pkgJson = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, "package.json"), "utf8"));
assert(pkgJson.dependencies["@fontsource/fraunces"], "Missing @fontsource/fraunces dependency (F15)");
assert(pkgJson.dependencies["@fontsource/instrument-sans"], "Missing @fontsource/instrument-sans dependency (F15)");
const layout = fs.readFileSync(path.join(FRONTEND_DIR, "app/layout.tsx"), "utf8");
assert(layout.includes("@fontsource/fraunces"), "layout.tsx must import @fontsource/fraunces");
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
};
for (const [testid, file] of Object.entries(testidLocations)) {
  const content = fs.readFileSync(path.join(FRONTEND_DIR, file), "utf8");
  assert(content.includes(`data-testid="${testid}"`), `Missing data-testid="${testid}" in ${file}`);
}
console.log("  ✅ Verified all required data-testids are present on their new elements");

// 7. Verify all 4 strategy ids are themed in lib/plain.ts and referenced by StrategyCard.tsx.
const plainLib = fs.readFileSync(path.join(FRONTEND_DIR, "lib/plain.ts"), "utf8");
for (const id of ["orb", "vwap_pullback", "news_momentum", "mean_reversion"]) {
  assert(plainLib.includes(`${id}:`), `Missing strategy theme for ${id} in lib/plain.ts`);
}
const card = fs.readFileSync(path.join(FRONTEND_DIR, "components/StrategyCard.tsx"), "utf8");
assert(card.includes("strategyTheme"), "StrategyCard.tsx must use strategyTheme() from lib/plain.ts");
console.log("  ✅ Verified all 4 strategy themes (Morning Breakout, Ride the Trend, Big News, Snap Back)");

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
