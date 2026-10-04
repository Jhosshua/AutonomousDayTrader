// Palette and contrast check for the bright operator dashboard (PLAN_2026_10_04, sections 1 and 11.5).
//
//  1. Every colour (#hex, rgb(), rgba()) in frontend/app|components|lib|hooks must be listed in the "new" columns
//     of docs/bright_dashboard/palette_map.md, or be on its keep list. Anything else (the old muted palette, or a
//     colour somebody made up) fails.
//  2. Every text/background pair the page actually uses must reach WCAG contrast (4.5:1 for text, 3:1 for graphics).
//     Token values are read from tailwind.config.js and plain.ts, so a changed value is checked, not a copy of it.
//
// Run: node scripts/verify_palette.mjs        (wired into `npm test`)
// Self-test: node scripts/verify_palette.mjs --self-test   (proves both checks can fail)
import fs from "node:fs";
import path from "node:path";
import assert from "node:assert";

const FRONTEND = path.resolve(import.meta.dirname, "..");
const MAP = path.resolve(FRONTEND, "../docs/bright_dashboard/palette_map.md");
const COLOUR_RE = /#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|rgba?\([^)]*\)/g;

const norm = (c) => (c.toLowerCase().startsWith("rgb") ? c.toLowerCase().replace(/\s+/g, "") : c.toLowerCase());

/** Allowed colours: every cell under a header that starts with "new", plus the fenced keep list. */
export function readAllowed(mdText) {
  const allowed = new Set();
  const lines = mdText.split("\n");
  let newCols = null;
  let inKeep = false;
  let inFence = false;
  for (const line of lines) {
    if (line.startsWith("## Keep")) inKeep = true;
    else if (line.startsWith("## ")) inKeep = false;
    if (inKeep && line.trim().startsWith("```")) { inFence = !inFence; continue; }
    if (inKeep && inFence) { for (const m of line.match(COLOUR_RE) ?? []) allowed.add(norm(m)); continue; }
    if (!line.trim().startsWith("|")) { newCols = null; continue; }
    const cells = line.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
    if (newCols === null) {
      newCols = cells.map((c, i) => (/^new\b/i.test(c) ? i : -1)).filter((i) => i >= 0);
      continue;
    }
    if (cells.every((c) => /^-+$/.test(c))) continue;
    for (const i of newCols) for (const m of cells[i]?.match(COLOUR_RE) ?? []) allowed.add(norm(m));
  }
  return allowed;
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else if (/\.(tsx?|css|mjs|js)$/.test(e.name)) out.push(full);
  }
  return out;
}

export function findStrays(allowed, files) {
  const stray = [];
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    text.split("\n").forEach((line, i) => {
      for (const m of line.match(COLOUR_RE) ?? []) if (!allowed.has(norm(m))) stray.push(`${path.relative(FRONTEND, file)}:${i + 1} ${m}`);
    });
  }
  return stray;
}

// ---- contrast ----
const lum = (hex) => {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
export const ratio = (a, b) => {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

function tokens() {
  const cfg = fs.readFileSync(path.join(FRONTEND, "tailwind.config.js"), "utf8");
  const t = {};
  for (const m of cfg.matchAll(/^\s+(ground|ink|muted|line|darkcard|gain|gainbg|loss|lossbg|lime|warn|warnbg|lavender|sage|terracotta):\s*"(#[0-9A-Fa-f]{6})"/gm)) t[m[1]] = m[2];
  return t;
}

function themes() {
  const src = fs.readFileSync(path.join(FRONTEND, "lib/plain.ts"), "utf8");
  const start = src.indexOf("export const STRATEGY_THEMES");
  const end = src.indexOf("export const NEUTRAL_THEME");
  const block = src.slice(start, end);
  const out = {};
  const entryRe = /^ {2}(\w+): \{([\s\S]*?)\n {2}\},/gm;
  for (const m of block.matchAll(entryRe)) {
    const f = {};
    for (const k of ["band", "ink", "tint", "bar", "track"]) f[k] = new RegExp(`${k}: "(#[0-9A-Fa-f]{6})"`).exec(m[2])?.[1];
    out[m[1]] = f;
  }
  const neutral = /export const NEUTRAL_THEME[\s\S]*?\};/.exec(src)[0];
  const n = {};
  for (const k of ["band", "ink", "tint", "bar", "track"]) n[k] = new RegExp(`${k}: "(#[0-9A-Fa-f]{6})"`).exec(neutral)?.[1];
  out.__neutral = n;
  const fear = {};
  for (const m of src.matchAll(/^\s+(LOW|NORMAL|ELEVATED|CRISIS): \{ bg: "(#[0-9A-Fa-f]{6})", ink: "(#[0-9A-Fa-f]{6})" \}/gm)) fear[m[1]] = { bg: m[2], ink: m[3] };
  out.__fear = fear;
  return out;
}

const WHITE = "#FFFFFF";
// Values that are inline in components (not tokens): listed in docs/bright_dashboard/palette_map.md
const INLINE = { soft: "#2A3150", tile: "#F1F3FB", hover: "#F6F8FE", dkgreen: "#0B5A3C", lavInk: "#4A2AB5", lavTint: "#F0EBFF", accent: "#2B4BFF", warnBorderBg: "#FFF4DB" };

export function contrastPairs() {
  const t = tokens();
  assert(t.ink && t.ground && t.darkcard && t.gain && t.loss && t.warn && t.lime, "tokens missing in tailwind.config.js");
  const P = [];
  const add = (name, fg, bg, min = 4.5) => P.push({ name, fg, bg, min });
  for (const bg of [WHITE, t.ground, INLINE.tile, INLINE.hover]) add(`ink on ${bg}`, t.ink, bg);
  for (const bg of [WHITE, t.ground, INLINE.tile]) add(`muted on ${bg}`, t.muted, bg);
  for (const bg of [WHITE, INLINE.tile, INLINE.hover]) add(`soft text on ${bg}`, INLINE.soft, bg);
  for (const bg of [WHITE, t.gainbg, t.ground]) add(`gain on ${bg}`, t.gain, bg);
  for (const bg of [WHITE, t.lossbg, t.ground]) add(`loss on ${bg}`, t.loss, bg);
  add("warn on warnbg", t.warn, t.warnbg);
  add("warn on white", t.warn, WHITE);
  add("muted on gainbg", t.muted, t.gainbg);
  add("dark green on gainbg", INLINE.dkgreen, t.gainbg);
  add("ink on warnbg", t.ink, t.warnbg);
  // status strip (plan 11.2): white and lime on the accent, nothing translucent
  add("strip: white on accent", WHITE, t.darkcard);
  add("strip: lime on accent", t.lime, t.darkcard);
  add("strip pill: ink on lime", t.ink, t.lime);
  add("warn pill: warn on warnbg", t.warn, t.warnbg);
  add("action button: white on ink", WHITE, t.ink);
  add("confirm button: white on loss", WHITE, t.loss);
  add("save button: white on gain", WHITE, t.gain);
  add("link: accent on ground", t.darkcard, t.ground);
  add("link: accent on white", t.darkcard, WHITE);
  add("selected tab: white on accent", WHITE, t.darkcard);
  add("lavender ink on lavender tint", INLINE.lavInk, INLINE.lavTint);
  add("lavender ink on white", INLINE.lavInk, WHITE);
  const th = themes();
  for (const [id, f] of Object.entries(th)) {
    if (id === "__fear") {
      for (const [lvl, c] of Object.entries(f)) add(`fear ${lvl}: ink on bg`, c.ink, c.bg);
      continue;
    }
    const name = id === "__neutral" ? "neutral theme" : `theme ${id}`;
    for (const k of ["band", "ink", "tint", "bar", "track"]) assert(f[k], `${name} is missing ${k}`);
    add(`${name}: ink on white`, f.ink, WHITE);
    add(`${name}: ink on tint`, f.ink, f.tint);
    add(`${name}: ink on band`, f.ink, f.band);
    add(`${name}: icon white on bar`, WHITE, f.bar, 3);
    add(`${name}: bar on white (hours bar)`, f.bar, WHITE, 3);
  }
  return P;
}

function main() {
  console.log("Palette and contrast check (bright operator dashboard)...");
  const allowed = readAllowed(fs.readFileSync(MAP, "utf8"));
  assert(allowed.size > 40, `palette_map.md produced only ${allowed.size} allowed colours`);
  const files = ["app", "components", "lib", "hooks"].flatMap((d) => walk(path.join(FRONTEND, d)));
  const stray = findStrays(allowed, files);
  assert.strictEqual(stray.length, 0, `colours not in palette_map.md (${stray.length}):\n  ${stray.slice(0, 25).join("\n  ")}`);
  console.log(`  ok: ${files.length} files, every colour is in the palette map (${allowed.size} allowed values)`);

  const pairs = contrastPairs();
  const bad = pairs.filter((p) => ratio(p.fg, p.bg) < p.min);
  assert.strictEqual(bad.length, 0, `contrast too low:\n  ${bad.map((p) => `${p.name} ${p.fg} on ${p.bg} = ${ratio(p.fg, p.bg).toFixed(2)} (< ${p.min})`).join("\n  ")}`);
  console.log(`  ok: ${pairs.length} text and graphic pairs meet their contrast minimum`);
  const tight = pairs.map((p) => ({ ...p, r: ratio(p.fg, p.bg) })).sort((a, b) => a.r / a.min - b.r / b.min).slice(0, 3);
  for (const p of tight) console.log(`     tightest: ${p.name} ${p.r.toFixed(2)} (min ${p.min})`);
}

function selfTest() {
  // The checks must be able to fail: an old-palette value is a stray, a weak pair is rejected.
  const allowed = readAllowed(fs.readFileSync(MAP, "utf8"));
  const tmp = path.join(FRONTEND, ".palette_selftest.tsx");
  fs.writeFileSync(tmp, 'const a = "#8F4424"; const b = "rgba(1, 2, 3, 0.5)"; const c = "#0E1330";\n');
  try {
    const stray = findStrays(allowed, [tmp]);
    assert(stray.some((s) => s.includes("#8F4424")), "self-test: old palette value was not flagged");
    assert(stray.some((s) => s.includes("rgba(1, 2, 3, 0.5)")), "self-test: unknown rgba was not flagged");
    assert(!stray.some((s) => s.includes("#0E1330")), "self-test: allowed colour was flagged");
  } finally {
    fs.rmSync(tmp, { force: true });
  }
  assert(ratio("#A7A2B8", "#FFFFFF") < 4.5, "self-test: weak pair not weak");
  assert(ratio("#0E1330", "#FFFFFF") > 15, "self-test: strong pair not strong");
  console.log("  ok: self-test, both checks can fail");
}

if (process.argv.includes("--self-test")) selfTest();
else {
  main();
  selfTest();
}
