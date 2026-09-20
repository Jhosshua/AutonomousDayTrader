import fs from "node:fs";
import path from "node:path";
import assert from "node:assert";

const FRONTEND_DIR = path.resolve(import.meta.dirname, "..");

console.log("🔍 Verifying Apple Music Mobile UI Architecture...");

// 1. Verify file inventory
const requiredFiles = [
  "package.json",
  "tsconfig.json",
  "tailwind.config.js",
  "postcss.config.js",
  "app/layout.tsx",
  "app/page.tsx",
  "app/globals.css",
  "types/trading.ts",
  "hooks/useTradingStream.ts",
  "components/AmbientBackground.tsx",
  "components/Header.tsx",
  "components/StrategyCard.tsx",
  "components/StrategyCarousel.tsx",
  "components/NowPlayingTray.tsx",
  "components/LiveChart.tsx",
  "components/ManualControls.tsx",
  "components/ExecutionLog.tsx",
];

for (const file of requiredFiles) {
  const fullPath = path.join(FRONTEND_DIR, file);
  assert(fs.existsSync(fullPath), `Missing required UI file: ${file}`);
  const content = fs.readFileSync(fullPath, "utf8");
  assert(content.length > 50, `File appears truncated or empty: ${file}`);
  console.log(`  ✅ Verified ${file} (${content.length} bytes)`);
}

// 2. Verify design tokens in tailwind.config.js and globals.css
const tailwindConfig = fs.readFileSync(path.join(FRONTEND_DIR, "tailwind.config.js"), "utf8");
assert(tailwindConfig.includes("#000000"), "Missing true obsidian black token in tailwind config");
assert(tailwindConfig.includes("#0a0a0c"), "Missing elevated obsidian surface token in tailwind config");
assert(tailwindConfig.includes("#30d158"), "Missing Apple green token in tailwind config");
assert(tailwindConfig.includes("#ff453a"), "Missing Apple red token in tailwind config");
console.log("  ✅ Verified Tailwind design tokens and Apple palette");

const globalsCss = fs.readFileSync(path.join(FRONTEND_DIR, "app/globals.css"), "utf8");
assert(globalsCss.includes("backdrop-filter: blur(24px)"), "Missing glassmorphism blur in globals.css");
assert(globalsCss.includes("tabular-nums"), "Missing tabular-nums utility in globals.css");
console.log("  ✅ Verified CSS glassmorphism & Apple typographic rules");

// 3. Verify spring physics specifications in NowPlayingTray.tsx
const nowPlaying = fs.readFileSync(path.join(FRONTEND_DIR, "components/NowPlayingTray.tsx"), "utf8");
assert(nowPlaying.includes("stiffness: 350"), "Missing stiffness: 350 in NowPlayingTray spring config");
assert(nowPlaying.includes("damping: 32"), "Missing damping: 32 in NowPlayingTray spring config");
assert(nowPlaying.includes("onFlattenPosition"), "Missing onFlattenPosition in NowPlayingTray");
assert(nowPlaying.includes("onTightenStop"), "Missing onTightenStop in NowPlayingTray");
console.log("  ✅ Verified Apple Music spring physics (stiffness: 350, damping: 32)");

// 4. Verify WebSocket URL and actions in useTradingStream.ts
const streamHook = fs.readFileSync(path.join(FRONTEND_DIR, "hooks/useTradingStream.ts"), "utf8");
assert(streamHook.includes("ws://127.0.0.1:8005/ws/ui"), "Missing default ws://127.0.0.1:8005/ws/ui in hook");
assert(streamHook.includes("FLATTEN_POSITION"), "Missing FLATTEN_POSITION action handling");
assert(streamHook.includes("FLATTEN_ALL"), "Missing FLATTEN_ALL action handling");
assert(streamHook.includes("TIGHTEN_STOP"), "Missing TIGHTEN_STOP action handling");
console.log("  ✅ Verified WebSocket client actions & port 8005 synchronization");

// 5. Verify 4 strategies in StrategyCarousel.tsx & StrategyCard.tsx
const carousel = fs.readFileSync(path.join(FRONTEND_DIR, "components/StrategyCarousel.tsx"), "utf8");
const card = fs.readFileSync(path.join(FRONTEND_DIR, "components/StrategyCard.tsx"), "utf8");
assert(card.includes("orb"), "Missing ORB strategy styling");
assert(card.includes("vwap_pullback"), "Missing VWAP Pullback strategy styling");
assert(card.includes("news_momentum"), "Missing News Momentum strategy styling");
assert(card.includes("mean_reversion"), "Missing Mean Reversion strategy styling");
console.log("  ✅ Verified all 4 strategy album cards (ORB, VWAP, News, Mean Reversion)");

// 6. Verify safe UI port 3005 in package.json
const pkgJson = JSON.parse(fs.readFileSync(path.join(FRONTEND_DIR, "package.json"), "utf8"));
assert(pkgJson.scripts.dev.includes("3005"), "dev script must run on safe port 3005");
assert(pkgJson.scripts.start.includes("3005"), "start script must run on safe port 3005");
console.log("  ✅ Verified UI safe port 3005 allocation (avoiding host port 3000 collision)");

console.log("\n🎉 All Apple Music UI architectural checks PASSED!");
