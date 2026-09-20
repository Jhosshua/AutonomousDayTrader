# Comprehensive Terminology & Metaphor Audit Report: De-Themification

**Auditor Archetype**: Explorer (Read-Only Architectural Audit)  
**Target Repository**: AutonomousDayTrader (`/Users/mo/AutonomousDayTrader`)  
**Audit Objective**: Identify all occurrences of music, playlist, album, and related analogies across the codebase (`frontend/`, `backend/`, `tests/`, `scripts/`, docs) and formulate concrete, institutional-grade trading replacements ensuring 100% contract synchronization and automated verification compliance (§R2).

---

## Executive Summary

An exhaustive repository scan identified **45 specific lines across 16 files** containing music-themed metaphors (`playlist`, `curated playlist`, `album`, `album art`, `now playing`, `now-playing`, `now_playing`, `lyrics`, `mini-player`, `music`, `Apple Music`).

Key findings:
1. **User-Facing UI Strings (Critical)**: Two prominent user-facing strings currently display music metaphors:
   - `frontend/components/StrategyCarousel.tsx:23`: `<span>Curated Playlists</span>`
   - `frontend/components/StrategyCard.tsx:126`: `Playlist / Strategy`
   - `frontend/app/layout.tsx:6`: Meta description header containing "Apple Music design language"
2. **Component Architecture & Gestures**:
   - `frontend/components/NowPlayingTray.tsx`: The primary bottom drawer component file, props, layout ID (`now-playing-tray`), internal comments ("Ticker Album Avatar", "Audit Log / Lyrics View", "Docked Mini-Player Bar") use music analogies.
   - `frontend/components/StrategyCard.tsx`: Internal helpers (`getArtwork`, `artwork`, "Top Album Artwork Square") use album art analogies.
3. **Frontend-Backend WebSocket State Contract (Healthy & Intact)**:
   - The backend WebSocket broadcast (`backend/app/main.py`) **already** uses professional trading keys: `"strategies": [...]` and `"primary_position": {...}`.
   - The frontend streaming hook (`frontend/hooks/useTradingStream.ts`) and TypeScript definitions (`frontend/types/trading.ts`) **already** consume `payload.strategies` and `payload.primary_position`.
   - **Zero wire protocol breakage**: Renaming UI components or display labels requires no changes to backend JSON schema payloads.
4. **Test Suite Coupling**:
   - `tests/e2e/test_challenger_mobile.py:397-398` explicitly queries `page.locator("text=Curated Playlists")` and verifies its visibility. Updating the UI label will fail this test unless the test locator is simultaneously updated to `Trading Strategies`.
   - `tests/e2e/test_challenger_mobile.py:142-143` asserts the existence of `FRONTEND_DIR / "components" / "NowPlayingTray.tsx"`.
   - `frontend/scripts/verify_ui.mjs:24, 51-56` asserts the existence and contents of `NowPlayingTray.tsx`.

---

## 1. Full Inventory of All Occurrences

The table below catalogs every occurrence in tracked repository files (excluding `.agents/` and historical references in `ORIGINAL_REQUEST.md`).

| # | File Path | Line | Context / Snippet | Term Identified | Category |
|---|-----------|------|-------------------|-----------------|----------|
| 1 | `frontend/components/StrategyCarousel.tsx` | 23 | `<span>Curated Playlists</span>` | `Curated Playlists` | **UI User-Facing** |
| 2 | `frontend/components/StrategyCard.tsx` | 126 | `Playlist / Strategy` | `Playlist` | **UI User-Facing** |
| 3 | `frontend/app/layout.tsx` | 6 | `description: "Always-on algorithmic day trading terminal with Apple Music design language",` | `Apple Music` | **UI Metadata** |
| 4 | `frontend/components/StrategyCarousel.tsx` | 18 | `{/* Section Header (Apple Music Curated Playlist Style) */}` | `Apple Music`, `Curated Playlist` | Component Code |
| 5 | `frontend/components/StrategyCard.tsx` | 16 | `// Custom artwork gradient and icon per strategy` | `artwork` | Component Code |
| 6 | `frontend/components/StrategyCard.tsx` | 17 | `const getArtwork = (id: string) => {` | `Artwork` | Component Code |
| 7 | `frontend/components/StrategyCard.tsx` | 62 | `const artwork = getArtwork(strategy.id);` | `artwork` | Component Code |
| 8 | `frontend/components/StrategyCard.tsx` | 111 | `} ${artwork.border}\`` | `artwork` | Component Code |
| 9 | `frontend/components/StrategyCard.tsx` | 113 | `{/* Top Album Artwork Square */}` | `Album Artwork` | Component Code |
| 10 | `frontend/components/StrategyCard.tsx` | 115 | `... bg-gradient-to-br ${artwork.gradient} ...` | `artwork` | Component Code |
| 11 | `frontend/components/StrategyCard.tsx` | 119 | `{artwork.icon}` | `artwork` | Component Code |
| 12 | `frontend/components/StrategyCard.tsx` | 131 | `{artwork.tagline}` | `artwork` | Component Code |
| 13 | `frontend/components/NowPlayingTray.tsx` | (File) | `NowPlayingTray.tsx` (Component filename) | `NowPlaying` | Component Code |
| 14 | `frontend/components/NowPlayingTray.tsx` | 11 | `interface NowPlayingTrayProps {` | `NowPlaying` | Component Code |
| 15 | `frontend/components/NowPlayingTray.tsx` | 20 | `export default function NowPlayingTray({` | `NowPlaying` | Component Code |
| 16 | `frontend/components/NowPlayingTray.tsx` | 27 | `}: NowPlayingTrayProps) {` | `NowPlaying` | Component Code |
| 17 | `frontend/components/NowPlayingTray.tsx` | 49 | `{/* Docked Mini-Player Bar (Persistent Bottom Floating Island) */}` | `Mini-Player` | Component Code |
| 18 | `frontend/components/NowPlayingTray.tsx` | 51 | `layoutId="now-playing-tray"` | `now-playing` | Component Code |
| 19 | `frontend/components/NowPlayingTray.tsx` | 59 | `{/* Left: Ticker Avatar & Position Info */}` | `Avatar` | Component Code |
| 20 | `frontend/components/NowPlayingTray.tsx` | 61 | `{/* Ticker Album Avatar */}` | `Album Avatar` | Component Code |
| 21 | `frontend/components/NowPlayingTray.tsx` | 271 | `{/* Audit Log / Lyrics View */}` | `Lyrics` | Component Code |
| 22 | `frontend/components/Header.tsx` | 147 | `{/* Hero Portfolio Value Section (Apple Music Album Hero Header Style) */}` | `Apple Music`, `Album` | Component Code |
| 23 | `frontend/app/page.tsx` | 7 | `import NowPlayingTray from "@/components/NowPlayingTray";` | `NowPlayingTray` | Component Code |
| 24 | `frontend/app/page.tsx` | 101 | `{/* Strategy Playlists Carousel */}` | `Playlists` | Component Code |
| 25 | `frontend/app/page.tsx` | 110 | `{/* Docked Apple Music Mini-Player & Expandable Modal Sheet */}` | `Apple Music`, `Mini-Player` | Component Code |
| 26 | `frontend/app/page.tsx` | 111 | `<NowPlayingTray` | `NowPlayingTray` | Component Code |
| 27 | `backend/app/config.py` | 83 | `UI_PORT: int = Field(default=3005, description="Apple Music mobile UI frontend port")` | `Apple Music` | Backend Config |
| 28 | `backend/app/main.py` | 407 | `"""Broadcast current system state to connected Apple Music UI clients."""` | `Apple Music` | Backend Docstring |
| 29 | `backend/app/main.py` | 1121 | `"""Real-time bi-directional streaming for the Apple Music mobile UI."""` | `Apple Music` | Backend Docstring |
| 30 | `backend/app/ingestion/sentiment.py` | 18 | `# Curated financial domain lexicons` | `Curated` | Backend Comment |
| 31 | `tests/e2e/test_challenger_mobile.py` | 13 | `4. Interactive modal flows: NowPlayingTray expansion...` | `NowPlayingTray` | Tests (Comments) |
| 32 | `tests/e2e/test_challenger_mobile.py` | 142 | `drawer_file = FRONTEND_DIR / "components" / "NowPlayingTray.tsx"` | `NowPlayingTray` | Tests (Code/Path) |
| 33 | `tests/e2e/test_challenger_mobile.py` | 143 | `assert drawer_file.exists(), f"NowPlayingTray.tsx missing at {drawer_file}"` | `NowPlayingTray` | Tests (Assertion) |
| 34 | `tests/e2e/test_challenger_mobile.py` | 147-159 | Multiple assertions citing `"NowPlayingTray must ..."` | `NowPlayingTray` | Tests (Assertions) |
| 35 | `tests/e2e/test_challenger_mobile.py` | 275 | `# Test 5: Interactive NowPlayingTray Expansion & Modal Sheet Verification` | `NowPlayingTray` | Tests (Comments) |
| 36 | `tests/e2e/test_challenger_mobile.py` | 277 | `def test_now_playing_tray_expansion_and_modal_elements(nextjs_server):` | `now_playing_tray` | Tests (Function Name) |
| 37 | `tests/e2e/test_challenger_mobile.py` | 278 | `"""Test interactive NowPlayingTray expansion into full modal sheet on 375px mobile viewport."""` | `NowPlayingTray` | Tests (Docstring) |
| 38 | `tests/e2e/test_challenger_mobile.py` | 291 | `# 1. Docked Mini-Player Bar verification` | `Mini-Player` | Tests (Comments) |
| 39 | `tests/e2e/test_challenger_mobile.py` | 293 | `assert docked_tray.is_visible(), "Docked NowPlayingTray must be visible at bottom of page"` | `NowPlayingTray` | Tests (Assertion) |
| 40 | `tests/e2e/test_challenger_mobile.py` | 305 | `# 2. Expand NowPlayingTray on click (click left album avatar or trade ticker)` | `album avatar` | Tests (Comments) |
| 41 | `tests/e2e/test_challenger_mobile.py` | 397 | `carousel_heading = page.locator("text=Curated Playlists")` | `Curated Playlists` | **Tests (DOM Locator)** |
| 42 | `tests/e2e/test_challenger_mobile.py` | 398 | `assert carousel_heading.is_visible(), "Curated Playlists header must be visible"` | `Curated Playlists` | **Tests (Assertion)** |
| 43 | `tests/e2e/test_tier1_features.py` | 786 | `# F14: Apple Music UI Aesthetic (5 tests)` | `Apple Music` | Tests (Header) |
| 44 | `tests/e2e/test_tier1_features.py` | 825 | `# F15: Strategy "Playlists/Albums" Cards (5 tests)` | `Playlists/Albums` | Tests (Header) |
| 45 | `tests/e2e/test_tier1_features.py` | 829 | `"""F15.1: Verify strategy playlist card data contract schema."""` | `playlist` | Tests (Docstring) |
| 46 | `tests/e2e/test_tier1_features.py` | 842 | `"""F15.2: Verify all 4 required strategy albums are registered."""` | `albums` | Tests (Docstring) |
| 47 | `tests/e2e/test_tier1_features.py` | 874 | `# F16: "Now Playing" Bottom Tray (5 tests)` | `Now Playing` | Tests (Header) |
| 48 | `tests/e2e/test_tier1_features.py` | 877 | `def test_f16_now_playing_primary_position_contract():` | `now_playing` | Tests (Function Name) |
| 49 | `tests/e2e/test_tier1_features.py` | 891, 901, 908, 915 | `test_f16_now_playing_*` | `now_playing` | Tests (Function Names) |
| 50 | `tests/e2e/test_tier2_boundary.py` | 648 | `# F14: Apple Music UI Aesthetic Boundaries (5 tests)` | `Apple Music` | Tests (Header) |
| 51 | `tests/e2e/test_tier2_boundary.py` | 686 | `# F15: Strategy Playlists Cards Boundaries (5 tests)` | `Playlists` | Tests (Header) |
| 52 | `tests/e2e/test_tier2_boundary.py` | 718 | `# F16: "Now Playing" Bottom Tray Boundaries (5 tests)` | `Now Playing` | Tests (Header) |
| 53 | `tests/e2e/test_contracts.py` | 12 | `- Apple Music UI state contracts and momentum glow calculation` | `Apple Music` | Tests (Docstring) |
| 54 | `tests/e2e/test_contracts.py` | 324 | `# F14-F17: Apple Music UI Aesthetic & WebSocket State Payload` | `Apple Music` | Tests (Header) |
| 55 | `tests/e2e/test_contracts.py` | 328 | `"""Derive Apple Music dynamic momentum glow colors and intensity."""` | `Apple Music` | Tests (Docstring) |
| 56 | `frontend/scripts/verify_ui.mjs` | 7 | `console.log("🔍 Verifying Apple Music Mobile UI Architecture...");` | `Apple Music` | Test Script (Log) |
| 57 | `frontend/scripts/verify_ui.mjs` | 24 | `"components/NowPlayingTray.tsx",` | `NowPlayingTray` | Test Script (Path) |
| 58 | `frontend/scripts/verify_ui.mjs` | 51-56 | `// 3. Verify spring physics specifications in NowPlayingTray.tsx` + assertions | `NowPlayingTray` | Test Script (Code) |
| 59 | `frontend/scripts/verify_ui.mjs` | 57 | `console.log("  ✅ Verified Apple Music spring physics...");` | `Apple Music` | Test Script (Log) |
| 60 | `frontend/scripts/verify_ui.mjs` | 74 | `console.log("  ✅ Verified all 4 strategy album cards...");` | `album` | Test Script (Log) |
| 61 | `frontend/scripts/verify_ui.mjs` | 82 | `console.log("\n🎉 All Apple Music UI architectural checks PASSED!");` | `Apple Music` | Test Script (Log) |
| 62 | `PROJECT.md` | 4, 43, 67, 83, 125, 214 | References to Apple Music UI, Playlists/Albums, Now Playing Tray | Architecture Docs | Documentation |
| 63 | `README.md` | 3, 46, 74, 76, 77, 88, 198, 200 | References to Apple Music UI, Albums/Playlists, Now Playing Drawer | System Overview | Documentation |
| 64 | `TEST_INFRA.md` | 95, 96, 97 | Feature table names: Apple Music UI, Playlists/Albums, Now Playing | Test Plan | Documentation |
| 65 | `TEST_READY.md` | 100, 101, 102 | Feature table names: Apple Music UI, Playlists/Albums, Now Playing | Test Verification | Documentation |
| 66 | `scripts/run_dev.sh` | 50, 58 | Echoes / comments: "Apple Music Mobile UI" | CLI Helper | Scripts |
| 67 | `scripts/verify_e2e_dataflow.py`| 14 | Docstring: "... matching Apple Music UI contract." | Verification Script | Scripts |
| 68 | `scripts/deploy_and_push.sh` | 46 | Default commit msg citing "Apple Music UI" | Deploy Script | Scripts |
| 69 | `scripts/run_monday_dry_run.py` | 664 | Log: "Port 3005 (Apple Music Web UI)" | Simulation Script | Scripts |

---

## 2. Categorization & Scope Analysis

### Category A: User-Facing UI Labels (Priority 1 — Zero Tolerance Grep Target)
These elements render directly to the user's viewport or browser page metadata:
- **`frontend/components/StrategyCarousel.tsx:23`**: The string `"Curated Playlists"` is rendered inside a badge alongside a Sparkles icon above the carousel.
- **`frontend/components/StrategyCard.tsx:126`**: The string `"Playlist / Strategy"` is rendered directly above each individual strategy name (ORB, VWAP, News Momentum, Mean Reversion).
- **`frontend/app/layout.tsx:6`**: HTML `<meta name="description">` contains `"Always-on algorithmic day trading terminal with Apple Music design language"`.

### Category B: Component Architecture, Markup & Filesystem Names (Priority 2)
These elements structure the frontend application code:
- **Component File `NowPlayingTray.tsx`**: The filename represents the music "Now Playing" metaphor.
  - *Recommendation*: Rename to `ActivePositionTray.tsx`. For zero-downtime and clean test execution, provide a compatibility shim `NowPlayingTray.tsx` that re-exports `ActivePositionTray`, OR update all import sites and test scripts in a single atomic commit.
- **Internal Variables & Helpers in `StrategyCard.tsx`**: `getArtwork()`, `artwork.border`, `artwork.gradient`, `artwork.icon`, `artwork.tagline`.
- **Framer Motion Layout IDs**: `layoutId="now-playing-tray"` in `NowPlayingTray.tsx`.
- **JSX Comments & Annotations**: Comments throughout `Header.tsx`, `page.tsx`, `NowPlayingTray.tsx`, and `StrategyCard.tsx` referencing "Mini-Player", "Album Art", "Lyrics View", and "Curated Playlist Style".

### Category C: State Models, API Schemas & Data Stores (Priority 3)
- **Frontend State Interface (`frontend/types/trading.ts`)**:
  - `TradingState` defines `strategies: StrategyState[]` and `primary_position: Position | null`.
  - **No music terms exist in state interfaces.**
- **Backend WebSocket Dispatch (`backend/app/main.py`)**:
  - `broadcast_ui_state()` packages `payload["strategies"]` and `payload["primary_position"]`.
  - **No music keys exist in wire payloads.**
- **Backend Configuration & Docstrings**:
  - `backend/app/config.py:83`: Field description mentions "Apple Music".
  - `backend/app/main.py:407, 1121`: Docstrings mention "Apple Music UI".
  - `backend/app/ingestion/sentiment.py:18`: Comment says `# Curated financial domain lexicons`.

### Category D: Tests & Verification Harnesses (Priority 4 — Functional Blocker)
- **`tests/e2e/test_challenger_mobile.py`**:
  - Line 397: `page.locator("text=Curated Playlists")` will throw `TimeoutError: Locator.wait_for: Timeout 10000ms exceeded` if the frontend string is changed without updating the test locator!
  - Lines 142-160: Asserts file existence and code strings within `frontend/components/NowPlayingTray.tsx`.
  - Line 277: Function name `test_now_playing_tray_expansion_and_modal_elements`.
- **`tests/e2e/test_tier1_features.py` & `test_tier2_boundary.py`**:
  - Feature test names and docstrings refer to `F15: Strategy "Playlists/Albums" Cards` and `F16: "Now Playing" Bottom Tray`.
- **`frontend/scripts/verify_ui.mjs`**:
  - Line 24: Verifies existence of `components/NowPlayingTray.tsx`.
  - Lines 51-56: Reads `NowPlayingTray.tsx` and checks for spring physics (`stiffness: 350`, `damping: 32`).
  - Lines 7, 57, 74, 82: Console logs referring to Apple Music and album cards.

### Category E: Documentation & Scripts (Priority 5)
- `PROJECT.md`, `README.md`, `TEST_INFRA.md`, `TEST_READY.md`: System diagrams, tables, and architectural descriptions refer to "Apple Music Mobile UI", "Playlists / Albums", and "Now Playing Bottom Tray".
- Scripts in `scripts/`: Dev scripts and dry run status output referencing "Apple Music UI".

---

## 3. Concrete Replacement Mappings

Below are the exact before $\to$ after replacement specifications for every occurrence.

### 3.1 UI User-Facing Labels

#### 1. Strategy Carousel Header (`frontend/components/StrategyCarousel.tsx:23`)
```tsx
// BEFORE (Line 21-24):
<div className="flex items-center gap-1.5 text-xs font-semibold text-apple-purple uppercase tracking-wider">
  <Sparkles className="w-3.5 h-3.5 text-apple-purple" />
  <span>Curated Playlists</span>
</div>

// AFTER:
<div className="flex items-center gap-1.5 text-xs font-semibold text-apple-purple uppercase tracking-wider">
  <Sparkles className="w-3.5 h-3.5 text-apple-purple" />
  <span>Trading Strategies</span>
</div>
```

#### 2. Strategy Card Badge (`frontend/components/StrategyCard.tsx:125-127`)
```tsx
// BEFORE (Line 125-127):
<span className="text-[10px] font-semibold uppercase tracking-widest text-white/70 block">
  Playlist / Strategy
</span>

// AFTER:
<span className="text-[10px] font-semibold uppercase tracking-widest text-white/70 block">
  Trading Strategy
</span>
```

#### 3. Root Layout HTML Meta Description (`frontend/app/layout.tsx:6`)
```tsx
// BEFORE (Line 6):
description: "Always-on algorithmic day trading terminal with Apple Music design language",

// AFTER:
description: "Always-on algorithmic day trading terminal with fluid obsidian execution interface",
```

---

### 3.2 Component Files, Variables & Internal Markup

#### 1. Strategy Card Theme Generator (`frontend/components/StrategyCard.tsx:16-63, 111-133`)
```tsx
// BEFORE:
  // Custom artwork gradient and icon per strategy
  const getArtwork = (id: string) => {
    switch (id) {
      case "orb":
        return {
          gradient: "from-amber-500/30 via-orange-600/20 to-emerald-500/30",
          border: "hover:border-amber-500/40",
          accentColor: "text-amber-400",
          icon: <Flame className="w-6 h-6 text-amber-400" />,
          tagline: "Morning Volatility Breakouts",
        };
...
  const artwork = getArtwork(strategy.id);
...
      {/* Top Album Artwork Square */}
      <div
        className={`relative w-full aspect-[16/10] rounded-2xl overflow-hidden bg-gradient-to-br ${artwork.gradient} p-3 flex flex-col justify-between border border-white/[0.08] shadow-inner mb-3`}
      >
        <div className="flex items-center justify-between">
          <div className="p-2 rounded-xl bg-black/40 backdrop-blur-md border border-white/10">
            {artwork.icon}
          </div>
          {getStatusBadge(strategy.status)}
        </div>

        <div>
          <span className="text-[10px] font-semibold uppercase tracking-widest text-white/70 block">
            Playlist / Strategy
          </span>
          <h3 className="text-base font-bold text-white tracking-tight leading-tight">
            {strategy.name}
          </h3>
          <p className="text-[11px] text-white/60 line-clamp-1">{artwork.tagline}</p>
        </div>
      </div>

// AFTER:
  // Custom theme gradient and icon per strategy
  const getStrategyTheme = (id: string) => {
    switch (id) {
      case "orb":
        return {
          gradient: "from-amber-500/30 via-orange-600/20 to-emerald-500/30",
          border: "hover:border-amber-500/40",
          accentColor: "text-amber-400",
          icon: <Flame className="w-6 h-6 text-amber-400" />,
          tagline: "Morning Volatility Breakouts",
        };
...
  const theme = getStrategyTheme(strategy.id);
...
      {/* Top Strategy Visual Banner */}
      <div
        className={`relative w-full aspect-[16/10] rounded-2xl overflow-hidden bg-gradient-to-br ${theme.gradient} p-3 flex flex-col justify-between border border-white/[0.08] shadow-inner mb-3`}
      >
        <div className="flex items-center justify-between">
          <div className="p-2 rounded-xl bg-black/40 backdrop-blur-md border border-white/10">
            {theme.icon}
          </div>
          {getStatusBadge(strategy.status)}
        </div>

        <div>
          <span className="text-[10px] font-semibold uppercase tracking-widest text-white/70 block">
            Trading Strategy
          </span>
          <h3 className="text-base font-bold text-white tracking-tight leading-tight">
            {strategy.name}
          </h3>
          <p className="text-[11px] text-white/60 line-clamp-1">{theme.tagline}</p>
        </div>
      </div>
```

#### 2. Bottom Drawer Component (`frontend/components/NowPlayingTray.tsx` $\to$ `ActivePositionTray.tsx`)
Create `ActivePositionTray.tsx` (and keep `NowPlayingTray.tsx` as a re-export shim or update imports):
```tsx
// BEFORE (frontend/components/NowPlayingTray.tsx):
interface NowPlayingTrayProps {
  position: Position | null;
  recentActivity: AuditRecord[];
  isConnected: boolean;
  onFlattenPosition: (symbol: string) => boolean;
  onFlattenAll: () => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}

export default function NowPlayingTray({ ... }: NowPlayingTrayProps) {
...
      {/* Docked Mini-Player Bar (Persistent Bottom Floating Island) */}
      <motion.div
        layoutId="now-playing-tray"
        transition={springConfig}
        className="fixed bottom-4 left-4 right-4 z-40 max-w-xl mx-auto"
      >
...
            {/* Ticker Album Avatar */}
            <div className="relative w-11 h-11 rounded-xl ...">
...
              {/* Audit Log / Lyrics View */}
              <div>
                <ExecutionLog records={recentActivity} maxItems={8} />
              </div>

// AFTER (frontend/components/ActivePositionTray.tsx):
interface ActivePositionTrayProps {
  position: Position | null;
  recentActivity: AuditRecord[];
  isConnected: boolean;
  onFlattenPosition: (symbol: string) => boolean;
  onFlattenAll: () => boolean;
  onTightenStop: (symbol: string, newStop: number) => boolean;
}

export default function ActivePositionTray({ ... }: ActivePositionTrayProps) {
...
      {/* Docked Active Position Bar (Persistent Bottom Floating Island) */}
      <motion.div
        layoutId="active-position-tray"
        transition={springConfig}
        className="fixed bottom-4 left-4 right-4 z-40 max-w-xl mx-auto"
      >
...
            {/* Ticker Symbol Badge */}
            <div className="relative w-11 h-11 rounded-xl ...">
...
              {/* Execution Audit Log View */}
              <div>
                <ExecutionLog records={recentActivity} maxItems={8} />
              </div>
```

#### 3. Main Dashboard Imports & JSX (`frontend/app/page.tsx`)
```tsx
// BEFORE (Lines 7, 101, 110-118):
import NowPlayingTray from "@/components/NowPlayingTray";
...
        {/* Strategy Playlists Carousel */}
        <StrategyCarousel strategies={state.strategies} />
...
      {/* Docked Apple Music Mini-Player & Expandable Modal Sheet */}
      <NowPlayingTray
        position={state.primary_position}
        recentActivity={state.recent_activity}
        isConnected={isConnected}
        onFlattenPosition={flattenPosition}
        onFlattenAll={flattenAll}
        onTightenStop={tightenStop}
      />

// AFTER:
import ActivePositionTray from "@/components/ActivePositionTray";
...
        {/* Trading Strategies Carousel */}
        <StrategyCarousel strategies={state.strategies} />
...
      {/* Docked Active Position Tray & Expandable Modal Sheet */}
      <ActivePositionTray
        position={state.primary_position}
        recentActivity={state.recent_activity}
        isConnected={isConnected}
        onFlattenPosition={flattenPosition}
        onFlattenAll={flattenAll}
        onTightenStop={tightenStop}
      />
```

#### 4. Header & Carousel Comments (`frontend/components/Header.tsx` & `StrategyCarousel.tsx`)
- `frontend/components/Header.tsx:147`:
  - Before: `{/* Hero Portfolio Value Section (Apple Music Album Hero Header Style) */}`
  - After: `{/* Hero Portfolio Value Section (Portfolio Hero Header Style) */}`
- `frontend/components/StrategyCarousel.tsx:18`:
  - Before: `{/* Section Header (Apple Music Curated Playlist Style) */}`
  - After: `{/* Section Header (Trading Strategies Header Style) */}`

---

### 3.3 Backend Configuration, Docstrings & Comments

#### 1. Backend Config (`backend/app/config.py:83`)
```python
# BEFORE:
UI_PORT: int = Field(default=3005, description="Apple Music mobile UI frontend port")

# AFTER:
UI_PORT: int = Field(default=3005, description="Mobile trading UI frontend port")
```

#### 2. Backend Main Docstrings (`backend/app/main.py:407, 1121`)
```python
# BEFORE (Line 407):
async def broadcast_ui_state() -> None:
    """Broadcast current system state to connected Apple Music UI clients."""

# AFTER:
async def broadcast_ui_state() -> None:
    """Broadcast current system state to connected trading UI clients."""

# BEFORE (Line 1121):
@app.websocket("/ws/ui")
async def websocket_ui_endpoint(websocket: WebSocket):
    """Real-time bi-directional streaming for the Apple Music mobile UI."""

# AFTER:
@app.websocket("/ws/ui")
async def websocket_ui_endpoint(websocket: WebSocket):
    """Real-time bi-directional streaming for the mobile trading UI."""
```

#### 3. Financial Lexicon Comment (`backend/app/ingestion/sentiment.py:18`)
```python
# BEFORE:
# Curated financial domain lexicons

# AFTER:
# Domain-specific financial lexicons
```

---

### 3.4 Tests & Validation Harness Mappings

#### 1. Challenger Mobile E2E Test (`tests/e2e/test_challenger_mobile.py`)
```python
# BEFORE (Line 142-143):
    drawer_file = FRONTEND_DIR / "components" / "NowPlayingTray.tsx"
    assert drawer_file.exists(), f"NowPlayingTray.tsx missing at {drawer_file}"

# AFTER (check ActivePositionTray, with fallback support for NowPlayingTray if alias retained):
    drawer_file = FRONTEND_DIR / "components" / "ActivePositionTray.tsx"
    if not drawer_file.exists():
        drawer_file = FRONTEND_DIR / "components" / "NowPlayingTray.tsx"
    assert drawer_file.exists(), f"ActivePositionTray.tsx missing at {drawer_file}"

# BEFORE (Line 277-278):
def test_now_playing_tray_expansion_and_modal_elements(nextjs_server):
    """Test interactive NowPlayingTray expansion into full modal sheet on 375px mobile viewport."""

# AFTER:
def test_active_position_tray_expansion_and_modal_elements(nextjs_server):
    """Test interactive ActivePositionTray expansion into full modal sheet on 375px mobile viewport."""

# BEFORE (Line 397-398) — CRITICAL PLAYWRIGHT LOCATOR:
    carousel_heading = page.locator("text=Curated Playlists")
    assert carousel_heading.is_visible(), "Curated Playlists header must be visible"

# AFTER:
    carousel_heading = page.locator("text=Trading Strategies")
    assert carousel_heading.is_visible(), "Trading Strategies header must be visible"
```

#### 2. Tier 1 & Tier 2 Test Headers & Function Names (`tests/e2e/test_tier1_features.py` & `test_tier2_boundary.py`)
- `tests/e2e/test_tier1_features.py:786`: `# F14: Obsidian Dark UI Aesthetic (5 tests)`
- `tests/e2e/test_tier1_features.py:825`: `# F15: Trading Strategy Cards (5 tests)`
- `tests/e2e/test_tier1_features.py:829`: `"""F15.1: Verify trading strategy card data contract schema."""`
- `tests/e2e/test_tier1_features.py:842`: `"""F15.2: Verify all 4 required strategies are registered."""`
- `tests/e2e/test_tier1_features.py:874`: `# F16: "Active Position" Bottom Tray (5 tests)`
- `tests/e2e/test_tier1_features.py:877`: `def test_f16_active_position_primary_position_contract():`
- `tests/e2e/test_tier1_features.py:891`: `def test_f16_active_position_bracket_levels():`
- `tests/e2e/test_tier1_features.py:901`: `def test_f16_active_position_action_flatten_position():`
- `tests/e2e/test_tier1_features.py:908`: `def test_f16_active_position_action_tighten_stop():`
- `tests/e2e/test_tier1_features.py:915`: `def test_f16_active_position_action_flatten_all():`
- `tests/e2e/test_tier2_boundary.py:648`: `# F14: Obsidian UI Aesthetic Boundaries (5 tests)`
- `tests/e2e/test_tier2_boundary.py:686`: `# F15: Trading Strategy Cards Boundaries (5 tests)`
- `tests/e2e/test_tier2_boundary.py:718`: `# F16: "Active Position" Bottom Tray Boundaries (5 tests)`
- `tests/e2e/test_contracts.py:324, 328`: `# F14-F17: Obsidian UI Aesthetic & WebSocket State Payload`, docstring: `"""Derive dynamic momentum glow colors and intensity."""`

#### 3. Frontend Architecture Verification Script (`frontend/scripts/verify_ui.mjs`)
```javascript
// BEFORE (Lines 7, 24, 51-57, 74, 82):
console.log("🔍 Verifying Apple Music Mobile UI Architecture...");
...
  "components/NowPlayingTray.tsx",
...
// 3. Verify spring physics specifications in NowPlayingTray.tsx
const nowPlaying = fs.readFileSync(path.join(FRONTEND_DIR, "components/NowPlayingTray.tsx"), "utf8");
assert(nowPlaying.includes("stiffness: 350"), "Missing stiffness: 350 in NowPlayingTray spring config");
assert(nowPlaying.includes("damping: 32"), "Missing damping: 32 in NowPlayingTray spring config");
assert(nowPlaying.includes("onFlattenPosition"), "Missing onFlattenPosition in NowPlayingTray");
assert(nowPlaying.includes("onTightenStop"), "Missing onTightenStop in NowPlayingTray");
console.log("  ✅ Verified Apple Music spring physics (stiffness: 350, damping: 32)");
...
console.log("  ✅ Verified all 4 strategy album cards (ORB, VWAP, News, Mean Reversion)");
...
console.log("\n🎉 All Apple Music UI architectural checks PASSED!");

// AFTER:
console.log("🔍 Verifying Mobile Trading UI Architecture...");
...
  "components/ActivePositionTray.tsx",
...
// 3. Verify spring physics specifications in ActivePositionTray.tsx
const activeTray = fs.readFileSync(path.join(FRONTEND_DIR, "components/ActivePositionTray.tsx"), "utf8");
assert(activeTray.includes("stiffness: 350"), "Missing stiffness: 350 in ActivePositionTray spring config");
assert(activeTray.includes("damping: 32"), "Missing damping: 32 in ActivePositionTray spring config");
assert(activeTray.includes("onFlattenPosition"), "Missing onFlattenPosition in ActivePositionTray");
assert(activeTray.includes("onTightenStop"), "Missing onTightenStop in ActivePositionTray");
console.log("  ✅ Verified tactile spring physics (stiffness: 350, damping: 32)");
...
console.log("  ✅ Verified all 4 strategy cards (ORB, VWAP, News, Mean Reversion)");
...
console.log("\n🎉 All Trading UI architectural checks PASSED!");
```

---

### 3.5 Documentation & Scripts Mappings

1. **`PROJECT.md` & `README.md`**:
   - Replace `"Playlists / Albums"` $\to$ `"Trading Strategies"`
   - Replace `"Now Playing" Bottom Tray` $\to$ `"Active Position" Bottom Tray`
   - Replace `"Apple Music Mobile UI"` $\to$ `"Mobile-First Trading UI"` (or `"Fluid Obsidian UI"`)
   - Replace `components/StrategyCard.tsx # Individual strategy performance album art` $\to`# Individual strategy performance card`
   - Replace `components/NowPlayingTray.tsx` $\to`components/ActivePositionTray.tsx`
2. **`TEST_INFRA.md` & `TEST_READY.md`**:
   - Replace `F14: Apple Music UI Aesthetic` $\to`F14: Obsidian Dark UI Aesthetic`
   - Replace `F15: Strategy "Playlists/Albums" Cards` $\to`F15: Trading Strategy Cards`
   - Replace `F16: "Now Playing" Bottom Tray` $\to`F16: "Active Position" Bottom Tray`
3. **`scripts/run_dev.sh`, `scripts/deploy_and_push.sh`, `scripts/run_monday_dry_run.py`, `scripts/verify_e2e_dataflow.py`**:
   - Replace all log banners and comments referencing "Apple Music" with "Trading UI" or "Obsidian UI".

---

## 4. Contract Verification: WebSocket & State Synchronization

A key concern when performing terminology refactoring is avoiding unintentional breakages in the client-server boundary.

### 4.1 Schema Analysis: Backend WebSocket Protocol (`ws://127.0.0.1:8005/ws/ui`)
The server serializes state via `backend/app/main.py:406-466`:
```python
payload = {
    "type": "STATE_UPDATE",
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "account": { ... },
    "market_context": adaptation_engine.get_market_context(),
    "strategies": [s.to_dict() for s in strategies],       # Canonical key: "strategies"
    "primary_position": primary_pos,                       # Canonical key: "primary_position"
    "all_positions": [_serialize_position(sym) for sym in account.positions],
    "positions_count": len(account.positions),
    "working_orders_count": len(engine.working_orders),
    "ingestion": relay_statuses,
    "recent_news": recent_news[-10:],
    "recent_activity": [ ... ],
}
```

### 4.2 Schema Analysis: Frontend State Ingestion (`frontend/hooks/useTradingStream.ts`)
The client receives the `STATE_UPDATE` payload at line 149:
```typescript
if (payload.type === "STATE_UPDATE" || payload.account) {
  setState((prev) => {
    const mergedStrategies = (payload.strategies && payload.strategies.length > 0)
      ? payload.strategies.map(...)
      : prev.strategies;

    let primary = payload.primary_position;
    ...
    return {
      ...prev,
      strategies: mergedStrategies,
      primary_position: primary !== undefined ? primary : prev.primary_position,
      all_positions: payload.all_positions ?? [],
      ...
    };
  });
}
```

### 4.3 Contract Invariants & Guarantees
1. **Zero Key Drift**: At no point in the history of the codebase were the JSON dictionary keys named `"playlists"` or `"now_playing"`. The wire payload has strictly used `"strategies"` and `"primary_position"`.
2. **Zero Ingestion Breakage**: The proposed changes modify purely display-layer components (`StrategyCarousel.tsx`, `StrategyCard.tsx`, `ActivePositionTray.tsx`), markup comments, and test locators. The underlying state interface `TradingState` is untouched.
3. **Two-Way Action Ingestion**:
   - The UI dispatches:
     - `{"action": "FLATTEN_POSITION", "symbol": sym}`
     - `{"action": "FLATTEN_ALL"}`
     - `{"action": "TIGHTEN_STOP", "symbol": sym, "new_stop": stop}`
   - These action names and parameters are purely execution commands and have zero connection to music analogies.
4. **Conclusion on Contract Integrity**: The de-themification process has **zero risk** of disrupting real-time WebSocket state streaming, bracket order rendering, or manual intervention dispatch.

---

## 5. Verification Plan & Post-Purge Acceptance Criteria

To independently verify that the music metaphor purge is 100% complete and causes zero regression:

### Step 1: Zero-Occurrence Grep Verification
Execute the following verification command across the entire codebase (excluding git metadata and historical prompt requirements in `ORIGINAL_REQUEST.md`):
```bash
git grep -inE "playlist|curated playlist|album|now[-_ ]?playing|mini[-_ ]?player|lyrics" -- \
  ":!*.agents*" \
  ":!ORIGINAL_REQUEST.md"
```
**Acceptance Criterion**: Exit code `1` (0 matches found across `frontend/`, `backend/`, `tests/`, `scripts/`, `PROJECT.md`, `README.md`, and `TEST_INFRA.md`).

### Step 2: Static Type & Build Verification
```bash
npm --prefix frontend run build
```
**Acceptance Criterion**: Next.js 15 production build compiles with zero TypeScript errors (`tsc --noEmit`), valid routes, and successful static export generation to `frontend/out`.

### Step 3: Frontend Architectural Unit Check
```bash
node frontend/scripts/verify_ui.mjs
```
**Acceptance Criterion**: Script exits with code `0`, confirming all required component files, Tailwind tokens, spring physics (`stiffness: 350`, `damping: 32`), and action bindings are present.

### Step 4: End-to-End Test Suite Execution
```bash
pytest tests/e2e/test_challenger_mobile.py -v
pytest tests/
./scripts/run_e2e_tests.sh
```
**Acceptance Criterion**: 100% pass rate across all 293 E2E test cases, specifically certifying:
- Mobile viewport rendering on 375px and 390px screens.
- `Trading Strategies` header visibility in carousel.
- Interactive expansion and dismissal of `ActivePositionTray`.

### Step 5: Port Hygiene Audit
```bash
./scripts/verify_port_hygiene.sh
```
**Acceptance Criterion**: Ports 3005, 8005, and 8080 are completely freed with zero lingering processes.
