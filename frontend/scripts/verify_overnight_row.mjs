// Unit checks for the Overnight playbook row and the 3:45 PM handoff (PLAN_2026_10_05_overnight_row_and_handoff.md).
// Run: node --experimental-strip-types scripts/verify_overnight_row.mjs   (wired into `npm test`)
// Pure helpers in lib/plain.ts: axisPct, rangesToSegments, overnightChip, showHandoffBand, handoffNote, x6NoteFor, overnightSteps.
// Every expected sentence is written out here from the plan, not read back from the code.
import assert from "node:assert";
import {
  axisPct, rangesToSegments, sessionPct, overnightChip, showHandoffBand, handoffNote, x6NoteFor, overnightSteps,
} from "../lib/plain.ts";

let n = 0;
const check = (cond, msg) => { assert(cond, msg); n += 1; console.log(`  ok   ${msg}`); };
const eq = (a, b, msg) => { assert.deepStrictEqual(a, b, `${msg}: got ${JSON.stringify(a)}`); n += 1; console.log(`  ok   ${msg}`); };

const MON = "2026-10-05", TUE = "2026-10-06", FRI = "2026-10-02", SAT = "2026-10-03";
const T = (h, m) => h * 60 + m;
const row = (symbol, o = {}) => ({ symbol, enabled: true, state: null, buy_date: null, sale_date: null, reason: null, block: null, reserved: false, ...o });
const base = (o = {}) => ({
  running: true, modeOn: true, enabled: ["NVDA", "IREN", "HUT"],
  today: { full_day: true, reason: null, sale_date: TUE }, tradingDay: true,
  todayEt: MON, etMin: T(10, 0), tooLate: false, noBuyActive: false,
  rows: [row("NVDA", { state: "SOLD", buy_date: FRI, sale_date: MON }), row("IREN", { state: "SOLD", buy_date: FRI, sale_date: MON }), row("HUT", { state: "SOLD", buy_date: FRI, sale_date: MON })],
  holds: [], x6: [], ...o,
});
const tonight = (state, o = {}) => ["NVDA", "IREN", "HUT"].map((s) => row(s, { state, buy_date: MON, sale_date: TUE, reserved: true, ...o }));
const names = (sid) => ({ vwap_pullback: "Ride the Trend", orb: "Opening Range Breakout" }[sid] || sid);
const chip = (o) => overnightChip(base(o));

// --- axis -------------------------------------------------------------------------------------------
eq(axisPct(T(9, 30), true), 0, "axis: 9:30 at 0%");
eq(axisPct(T(16, 0), true), 88, "axis: 4 PM at 88% with the night part");
eq(axisPct(T(16, 0), false), 100, "axis: 4 PM at 100% without it (old layout unchanged)");
eq(axisPct(T(12, 0)), sessionPct(T(12, 0)), "axis: default is sessionPct");
const seg = rangesToSegments([["09:35", "11:00"]], true)[0];
check(Math.abs(seg.left - 1.128) < 0.01 && Math.abs(seg.left + seg.width - 20.31) < 0.01, "axis: ORB 9:35 to 11:00 spans 1.1% to 20.3% on the night axis");
eq(rangesToSegments([["09:30", "16:00"]])[0], { left: 0, width: 100 }, "axis: segments without night unchanged");

// --- chip ---------------------------------------------------------------------------------------------
eq(chip({}).label, "Buys at the 4 PM close", "chip: morning of a full day");
eq(chip({}).tone, "lavender", "chip: waiting tone is calm");
check(!chip({}).label.includes("3:46"), "chip: never says 3:46 (the buy is at the close)");
eq(chip({ noBuyActive: true }), { label: "No buy tonight", tone: "grey", breathing: false }, "chip: operator turned the buy off is grey");
eq(chip({ etMin: T(15, 47), rows: tonight("BUY_SENT") }).label, "Buying at the close", "chip: 3:47 PM buy sent");
eq(chip({ etMin: T(15, 47), rows: tonight("IDLE", { block: "BROKER_MISMATCH" }) }), { label: "Not bought yet", tone: "amber", breathing: false }, "chip: blocked buy is amber");
eq(chip({ etMin: T(16, 1), rows: tonight("BUY_ACCEPTED") }).label, "Checking the closing buy", "chip: accepted after 4 PM");
eq(chip({ etMin: T(15, 46), rows: tonight("SKIPPED", { reason: "OPERATOR_NO_BUY_TONIGHT", reserved: false }) }).tone, "grey", "chip: all skipped by the operator is grey");
eq(chip({ etMin: T(15, 50), rows: tonight("SKIPPED", { reason: "BUY_REFUSED", reserved: false }) }).tone, "amber", "chip: all skipped by a failure is amber");
eq(chip({ today: { full_day: false, reason: "EARLY_CLOSE", sale_date: null } }), { label: "No buy today (early close)", tone: "grey", breathing: false }, "chip: early close day");
eq(chip({ todayEt: SAT, today: { full_day: false, reason: "NOT_TRADING_DAY", sale_date: null } }).label, "Market closed", "chip: weekend with nothing held");
eq(chip({ etMin: T(16, 30), holds: [{ symbol: "NVDA", saleDate: TUE }, { symbol: "IREN", saleDate: TUE }] }).label, "Holding 2 until 9:30 AM Tue Oct 6", "chip: holding names the sale day");
eq(chip({ todayEt: SAT, today: { full_day: false, reason: "NOT_TRADING_DAY", sale_date: null }, holds: [{ symbol: "NVDA", saleDate: MON }] }).label, "Holding 1 until 9:30 AM Mon Oct 5", "chip: holds beat Market closed on a weekend");
eq(chip({ modeOn: false, holds: [{ symbol: "NVDA", saleDate: MON }], etMin: T(9, 0) }).label, "Holding 1 until 9:30 AM today", "chip: holds beat Switched off");
eq(chip({ holds: [{ symbol: "NVDA", saleDate: MON }], etMin: T(9, 40) }).label, "Selling", "chip: past 9:30 with a hold left");
eq(chip({ running: false }).label, "Not running", "chip: not running");
eq(chip({ enabled: [] }).label, "Switched off", "chip: nothing enabled");
eq(chip({ today: null, tradingDay: true }).label, "Buys at the 4 PM close", "chip: backend without `today` falls back to the trading day flag");

// --- band and note ----------------------------------------------------------------------------------------
check(showHandoffBand(base()), "band: full day, buys on");
check(!showHandoffBand(base({ today: { full_day: false, reason: "EARLY_CLOSE", sale_date: null } })), "band: not on an early close day");
check(!showHandoffBand(base({ modeOn: false })), "band: not when switched off");
eq(handoffNote(base({ etMin: T(15, 40), rows: tonight("IDLE") })), null, "note: none before 3:45 PM");
eq(handoffNote(base({ etMin: T(15, 47), rows: tonight("BUY_SENT") })),
  "From 3:45 PM until they sell at 9:30 AM Tue Oct 6, NVDA, IREN and HUT are saved for Overnight. The day playbooks won't trade them.",
  "note: 3:47 PM, three stocks, sale day named");
const mixed = [row("NVDA", { state: "BUY_ACCEPTED", buy_date: MON, sale_date: TUE, reserved: true }), row("IREN", { state: "SKIPPED", buy_date: MON, reason: "BUY_REFUSED" }), row("HUT", { state: "BUY_SENT", buy_date: MON, sale_date: TUE, reserved: true })];
eq(handoffNote(base({ etMin: T(15, 48), rows: mixed })),
  "From 3:45 PM until they sell at 9:30 AM Tue Oct 6, NVDA and HUT are saved for Overnight. The day playbooks won't trade them.",
  "note: a skipped stock is left out (reserved only)");
eq(handoffNote(base({ etMin: T(15, 48), rows: [row("NVDA", { state: "BUY_SENT", buy_date: MON, sale_date: TUE, reserved: true })] })),
  "From 3:45 PM until it sells at 9:30 AM Tue Oct 6, NVDA is saved for Overnight. The day playbooks won't trade it.", "note: one stock");
eq(handoffNote(base({ etMin: T(16, 5), rows: tonight("HELD") })), null, "note: gone once the buy is booked (the chip and holds box say it)");
eq(handoffNote(base({ etMin: T(9, 0), rows: tonight("SALE_QUEUED", { buy_date: FRI, sale_date: MON }) })), null, "note: never on the morning after (no future tense for the past)");

// --- X6 note ------------------------------------------------------------------------------------------
const job = (o = {}) => ({ symbol: "NVDA", date: MON, done: true, strategy_id: "vwap_pullback", filled_at: "2026-10-05T15:46:20-04:00", filled_qty: 20, ...o });
eq(x6NoteFor("vwap_pullback", base({ etMin: T(15, 47), x6: [job()] })), "NVDA closed at 3:46 PM, 9 minutes early, so Overnight could buy it at the close.", "x6: filled note");
eq(x6NoteFor("orb", base({ etMin: T(15, 47), x6: [job()] })), null, "x6: only on the playbook that owned the trade");
eq(x6NoteFor("vwap_pullback", base({ todayEt: TUE, x6: [job()] })), null, "x6: yesterday's close is not shown today");
eq(x6NoteFor("vwap_pullback", base({ etMin: T(15, 46), x6: [job({ done: false, filled_qty: 0, filled_at: null })] })), "Closing NVDA now so Overnight can buy it at the close.", "x6: closing now");
eq(x6NoteFor("vwap_pullback", base({ etMin: T(15, 52), tooLate: true, x6: [job({ done: false, filled_qty: 0, filled_at: null })] })), null, "x6: 'now' never sticks after 3:49:30 PM");
eq(x6NoteFor("vwap_pullback", base({ etMin: T(15, 47), x6: [job({ filled_qty: 0, filled_at: null })] })), null, "x6: done with nothing filled says nothing");
eq(x6NoteFor("vwap_pullback", base({ etMin: T(15, 58), x6: [job({ filled_at: "2026-10-05T15:57:00-04:00" })] })), "NVDA closed at 3:57 PM so Overnight could buy it at the close.", "x6: no negative minutes");

// --- steps --------------------------------------------------------------------------------------------
const st = (o) => overnightSteps(base(o), names);
const status = (o) => (st(o) || []).map((s) => s.status).join(",");
eq(st({ etMin: T(16, 30), rows: [] }), null, "steps: nothing planned or open after the close");
eq(status({ etMin: T(10, 0) }), "next,next,next,next,next,next,next", "steps: morning plan, all next");
eq(status({ noBuyActive: true }), "next,skipped,skipped,skipped,skipped,skipped,skipped", "steps: operator stopped tonight's buy before 3:45");
eq(status({ etMin: T(15, 47), rows: tonight("BUY_SENT"), x6: [job()] }), "done,done,done,now,next,next,next", "steps: 3:47 PM with an early close");
eq(status({ etMin: T(15, 47), rows: tonight("BUY_ACCEPTED") }), "done,done,done,now,next,next,next", "steps: one Now at a time (accepted before 3:49:30)");
eq(status({ etMin: T(15, 55), tooLate: true, rows: tonight("BUY_ACCEPTED") }), "done,done,done,done,now,next,next", "steps: waiting for the close after 3:49:30");
const s347 = st({ etMin: T(15, 47), rows: tonight("BUY_SENT"), x6: [job()] });
eq(s347[1].text, "Ride the Trend's NVDA trade was closed early at 3:46 PM.", "steps: early close names the playbook");
eq(s347[2].text, "Buy sent for NVDA, IREN and HUT at the closing price.", "steps: buy sent");
eq(st({ etMin: T(15, 47), rows: tonight("BUY_SENT") })[1].text, "No day trade needed closing.", "steps: no early close needed");
eq(status({ etMin: T(16, 30), tooLate: true, rows: tonight("HELD") }), "done,done,done,done,done,next,next", "steps: 4:30 PM bought");
eq(status({ etMin: T(19, 10), tooLate: true, rows: tonight("SALE_QUEUED") }), "done,done,done,done,done,done,next", "steps: 7:10 PM sale queued");
const morning = { todayEt: TUE, etMin: T(9, 0), today: { full_day: true, reason: null, sale_date: "2026-10-07" }, rows: tonight("SALE_QUEUED") };
eq(status(morning), "done,done,done,done,done,done,next", "steps: next morning reads last night, not today's clock");
eq(st(morning)[6].text, "Sold at the open today. Then the day playbooks can trade them again.", "steps: sale today");
eq(st(morning)[6].time, "9:30 AM", "steps: sale time today");
eq(st({ etMin: T(16, 30), tooLate: true, rows: tonight("HELD") })[6].time, "9:30 AM Tue", "steps: sale time names the day");
eq(status({ ...morning, etMin: T(9, 31), rows: tonight("SOLD", { reserved: true }) }), "done,done,done,done,done,done,now", "steps: sold but not released yet");
eq(st({ ...morning, etMin: T(9, 31), rows: tonight("SOLD", { reserved: true }) })[6].text, "Sold, waiting for Alpaca to show it flat.", "steps: waiting for flat");
eq(status({ ...morning, etMin: T(9, 35), rows: tonight("SOLD", { reserved: false }) }), "next,next,next,next,next,next,next", "steps: once released, the steps show tonight's plan");
const weekend = { todayEt: SAT, today: { full_day: false, reason: "NOT_TRADING_DAY", sale_date: null }, rows: tonight("SALE_QUEUED", { buy_date: FRI, sale_date: MON }) };
eq(st(weekend)[6].text, "Sold at the open on Mon Oct 5. Then the day playbooks can trade them again.", "steps: weekend names Monday");
eq(status({ etMin: T(15, 46), rows: tonight("SKIPPED", { reason: "OPERATOR_NO_BUY_TONIGHT", reserved: false }) }), "done,skipped,skipped,skipped,skipped,skipped,skipped", "steps: operator skip is grey skipped");
eq(status({ etMin: T(15, 50), tooLate: true, rows: tonight("SKIPPED", { reason: "BUY_REFUSED", reserved: false }) }), "done,problem,problem,problem,problem,problem,problem", "steps: refused buy is a problem");
eq(st({ etMin: T(15, 50), tooLate: true, rows: tonight("SKIPPED", { reason: "BUY_REFUSED", reserved: false }) })[2].text, "No buy sent. Alpaca refused the buy.", "steps: refusal reason in plain words");
eq(st({ etMin: T(15, 47), rows: tonight("IDLE", { block: "BROKER_MISMATCH" }) })[2].status, "problem", "steps: blocked send is a problem");
eq(st({ today: { full_day: false, reason: "EARLY_CLOSE", sale_date: null } }), null, "steps: early close day with nothing open shows no steps");

// --- negative control: the detectors can fail -------------------------------------------------------
let caught = false;
try { eq(chip({}).label, "Buys at 3:46 PM", "negative control"); } catch { caught = true; }
check(caught, "negative control: a wrong expected chip fails");

console.log(`\nverify_overnight_row: ${n} passed`);
