// Unit checks for the status strip's needs-a-look list (collectAttention in lib/plain.ts).
// Run: node --experimental-strip-types scripts/verify_attention.mjs   (wired into `npm test`)
// The browser harness (scripts/verify_bright_dashboard.py) proves the page shows what this list says; this file
// proves the list itself: each source, de-duplication, optional fields, fail closed.
import assert from "node:assert";
import { collectAttention, attentionPillText, isFeedDown, orbProblemOf } from "../lib/plain.ts";

const calmInputs = () => ({
  connectionState: "live",
  feedDown: false,
  brokerMismatch: false,
  savingProblem: false,
  unsold: [],
  breakerHit: false,
  strategies: [
    { id: "orb", orb: { init_error: null, errors: [], alerts: [], orphans: [] } },
    { id: "tsla_asymmetric_dual", tri_engine: { last_error: null } },
    { id: "mean_reversion" },
  ],
  overnight: { initError: null, needsLook: [] },
  ledgerError: false,
  resultsError: false,
  positions: [{ symbol: "MSFT", stop_loss: 399.5, strategy_id: "mean_reversion" }],
});
const keys = (i) => collectAttention(i).map((a) => a.key);
let n = 0;
const check = (cond, msg) => { assert(cond, msg); n += 1; console.log(`  ok   ${msg}`); };

check(keys(calmInputs()).length === 0, "calm inputs: no alarms");
check(attentionPillText(0) === "No alarms" && attentionPillText(1) === "1 needs a look" && attentionPillText(3) === "3 need a look", "pill words");

// one source at a time
const one = {
  connection: (i) => { i.connectionState = "reconnecting"; },
  "connection (stale)": (i) => { i.connectionState = "stale"; },
  feed: (i) => { i.feedDown = true; },
  mismatch: (i) => { i.brokerMismatch = true; },
  saving: (i) => { i.savingProblem = true; },
  unsold: (i) => { i.unsold = ["NVDA"]; },
  breaker: (i) => { i.breakerHit = true; },
  "orb-init": (i) => { i.strategies[0].orb.init_error = "boom"; },
  "orb-problem": (i) => { i.strategies[0].orb.errors = [{ alarm: "orb_breaker", symbol: "AAA" }]; },
  "orb-alert": (i) => { i.strategies[0].orb.alerts = ["ORB alert"]; },
  "orb-orphan:RKLB": (i) => { i.strategies[0].orb.orphans = [{ symbol: "RKLB", text: "ORB holds RKLB" }]; i.strategies[0].orb.alerts = ["ORB holds RKLB"]; },
  "tri:tsla_asymmetric_dual": (i) => { i.strategies[1].tri_engine.last_error = "broker said no"; },
  "overnight-init": (i) => { i.overnight.initError = "no key"; },
  "overnight-look:IREN": (i) => { i.overnight.needsLook = ["IREN"]; },
  ledger: (i) => { i.ledgerError = true; },
  results: (i) => { i.resultsError = true; },
  "unprotected:AMD (no stop)": (i) => { i.positions = [{ symbol: "AMD", stop_loss: null, strategy_id: "mean_reversion" }]; },
  "unprotected:AMD (not linked)": (i) => { i.positions = [{ symbol: "AMD", stop_loss: 10, strategy_id: undefined }]; },
};
for (const [name, mutate] of Object.entries(one)) {
  const i = calmInputs();
  mutate(i);
  const got = collectAttention(i);
  check(got.length === 1, `${name}: exactly one item (${got.map((g) => g.key).join(",")})`);
  check(got[0].label.length > 3 && !got[0].label.includes("—"), `${name}: has a plain label ("${got[0].label}")`);
}

// de-dup: an orphan that also raises an ORB alert and an ORB error counts once
{
  const i = calmInputs();
  i.strategies[0].orb.orphans = [{ symbol: "RKLB", text: "ORB holds RKLB" }];
  i.strategies[0].orb.alerts = ["ORB holds RKLB"];
  i.strategies[0].orb.errors = [{ alarm: "orb_alert", symbol: "RKLB" }, { alarm: "orphan", symbol: "RKLB" }];
  check(keys(i).length === 1, "an orphan that also raises an ORB alert and error counts once");
  i.strategies[0].orb.orphans.push({ symbol: "ZZZ", text: "ORB holds ZZZ" });
  check(keys(i).length === 2, "two different orphans count twice");
  i.positions = [{ symbol: "AMD", stop_loss: null, strategy_id: undefined }, { symbol: "AMD", stop_loss: null, strategy_id: undefined }];
  check(keys(i).filter((k) => k === "unprotected:AMD").length === 1, "the same position twice counts once");
}

// an unsold hold that also needs a look counts once; a different stock's needs-look still counts
{
  const i = calmInputs();
  i.unsold = ["IREN"];
  i.overnight.needsLook = ["IREN"];
  check(keys(i).length === 1 && keys(i)[0] === "unsold", "an unsold hold that also needs a look counts once");
  i.overnight.needsLook = ["IREN", "HUT"];
  check(keys(i).length === 2, "another stock's needs-a-look still counts");
}

// optional fields the backend may not send are NOT alarms
{
  const i = calmInputs();
  i.strategies = [{ id: "mean_reversion" }, { id: "orb", orb: { init_error: null, errors: [] } }];
  i.overnight = { needsLook: [] };
  check(keys(i).length === 0, "absent optional fields (alerts, orphans, tri_engine, init_error) are not alarms");
}

// fail closed: a thrown error inside a condition counts as "needs a look"
{
  const i = calmInputs();
  i.strategies = [null];
  const got = collectAttention(i);
  check(got.length === 1 && got[0].key === "check-failed", "a condition that throws becomes check-failed, never calm");
  const j = calmInputs();
  j.positions = [null];
  check(collectAttention(j).some((a) => a.key === "check-failed"), "a broken position row becomes check-failed");
}

// feed rule: an empty list is down; all-connected is up; one connected is up
check(isFeedDown({}) === true && isFeedDown(null) === true, "an empty feed list counts as down");
check(isFeedDown({ stock: "connected", news: "connected" }) === false, "all feeds connected is not down");
check(isFeedDown({ stock: "unconfigured", news: "unconfigured" }) === true, "no feed connected is down");
check(isFeedDown({ stock: "connected", news: "disconnected" }) === false, "one feed connected is not down (same as the banner before)");

// ORB "reported a problem": an alert-kind or an orphan's own error is not a second problem
check(orbProblemOf({ id: "orb", orb: { init_error: null, errors: [{ alarm: "orb_alert", symbol: "A" }], orphans: [] } }) === null, "an alert-kind error is not a 'reported a problem'");
check(orbProblemOf({ id: "orb", orb: { init_error: null, errors: [{ alarm: "x", symbol: "A" }], orphans: [{ symbol: "A", text: "t" }] } }) === null, "an orphan's own error is not a 'reported a problem'");
check(orbProblemOf({ id: "orb", orb: { init_error: null, errors: [{ alarm: "x", symbol: "B" }], orphans: [{ symbol: "A", text: "t" }] } }) !== null, "any other error is");

// negative control: the checks above can fail
{
  const i = calmInputs();
  i.feedDown = true;
  assert.throws(() => assert(keys(i).length === 0, "should be calm"), "negative control: a calm expectation fails on an alarm");
  n += 1;
  console.log("  ok   negative control: the calm check fails when an alarm is on");
}
console.log(`\n${n} checks passed`);
