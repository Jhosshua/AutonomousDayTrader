# SUPERSEDED 2026-09-28: operator wanted ORB rewritten INSIDE ADT, not a sidecar. See PLAN_2026_09_28_orb_rules_match_orbstraddle.md

# Plan: ADT's general ORB is replaced by ORBStraddle's execution (2026-09-28, draft v3 after Codex attack rounds 1 and 2)

Operator request (2026-09-28 13:40 ET): "I want number one, orb the general one, to execute exactly like
orbstraddle robot, so the general orb strategy would be replaced with the way orbstraddle executes the
strategy." Operator said: make the best decisions, call them out, no questions.

Evidence used: ORBStraddle live spec (`docs/orb_replacement/orbstraddle_spec.md`, from ORBStraddle
HEAD 71b001f + live Railway vars) and the ADT ORB integration map (`docs/orb_replacement/adt_orb_map.md`,
ADT HEAD c8194eb).

## 1. What "exactly like ORBStraddle" means (the target behaviour)

ORBStraddle today (rules `adaptive-v1.6.0-flow-rules`), in one paragraph: scan the top 250 non-fund US
stocks by prior-day dollar volume; opening range from SIP trade prints 09:30:00-09:35:00; a break is any
price-eligible print beyond the range at/after 09:35:05 with a fresh deep quote and SOFI |z| >= 2; candle
rule (green = long only, red = short only), delta >= 0.05 its way, velocity burst, SPY + sector ETF macro
veto; the whole wave sits out if the board is one-sided (<25% or >75% shorts) or SPY/QQQ/news data is
incomplete; per card spread <= 8 bps, RVOL >= 2.2, ATR >= 2%, stop 0.5-7.5%; no-news picks must be a
failed gap (gap >= 0.8% against the break, not a mega cap); utility model picks at most 2; one primary
decision at ~09:38:30, secondary checks every minute 09:45-10:15; entry = Alpaca market BRACKET (day),
stop = range extreme -/+ 0.25 ATR (at least 0.5%), target +0.75R from the fresh price, no-chase 0.33 of
the stop distance; software exits: fast-fail -0.40R, breakeven after +0.75R, clawback (peak >= 0.60R,
give back 0.25R), absorption exit; flatten 11:00; sizing 2.0% of day-start equity on the first trade,
2.5% day risk budget (so the 2nd trade gets at most 0.5%, then nothing), 3% own-P&L daily halt, 4 slots.

ADT's ORB today is a different strategy (5-min BARS, RVOL vs 20 bars, CLV, local stops, T1/T2 + ATR
trail, 09:30-11:30 entries, flatten 15:55, $12.5k cap, ~1% risk). Nothing of it survives.

## 2. Decision: run ORBStraddle's own code as a sidecar on ADT's account, not a port

**Decided:** deploy the ORBStraddle codebase, unchanged, as a second Railway service `adt-orb` in the
ADT Railway project, trading ADT's Alpaca paper account PA3CSVDZMMPY with its own client-order-id prefix
`adto`. ADT stops generating ORB signals. This is the pattern JhoshuaStraddle and JhoshuaStraddleLarge
already use (same code, different env), and JhoshuaStraddleLarge already shares an account with
another bot (JhoshuaThesis), so shared-account operation is proven live: ORBStraddle's `ownership.py`
books only fills of its own order ids, its occupied check skips any symbol the account already holds or
has an order on, its exits sell exactly its own quantity, and it has its own Alpaca request budget.

**Rejected: porting ORBStraddle into ADT's strategy framework.** 15,766 lines with module globals, its
own state files, supervisor thread, REST scanner of 250 symbols, news, and bracket orders ADT's broker
layer cannot place (ADT has no bracket orders, no replace, local stops only). A port is weeks of work
and would drift from ORBStraddle on day one, which fails "exactly like".
**Rejected: vendoring the ORBStraddle modules into ADT's process.** Same drift, plus top-level module
names (`config`, `core`, `signals`) and module-level state inside ADT's event loop.

**Deploy source decided:** the `adt-orb` service is connected to the SAME GitHub repo and branch as
ORBStraddle (`Jhosshua/ORBStraddle`, `main`), so any ORBStraddle change ships to ADT's ORB too and the
two run the same code (verified by commit SHA, section 6.4). Cost: a push to ORBStraddle restarts both (the existing push=redeploy rule already
forbids session-hour pushes there).

## 3. Settings for `adt-orb` (copy ORBStraddle's live vars, change only identity)

Copied exactly from ORBStraddle's live Railway vars: ORBS_MODE=live, ORBS_TRADE_BASE=paper, DECIDER,
FREEZE 09:38, RISK_PCT 2.0, candle rule on, DELTA/VELOCITY/MACRO/ABSORPTION rules on, STREAM shadow,
THESIS_GATE=no, CARD_SOURCE file, relay base/ws/token, every unset default left unset (MAX_OPEN_SLOTS 4,
MAX_DAY_RISK_PCT 2.5, DAILY_LOSS_HALT 3.0, cutoff 10:15, flatten 11:00).
Changed: ORBS_KEY/SECRET = ADT's paper keys; ORBS_ACCOUNT_ALLOWLIST=PA3CSVDZMMPY;
ORBS_COID_PREFIX=adto (opt prefix adtoo); ORBS_BOT_NAME=ADT-ORB; ORBS_OPERATOR_NAME kept;
ORBS_REQUEST_BUDGET_PER_MIN / EXIT_RESERVE set from ADT's measured peak (section 5.7); new `ORBS_EXCLUDE_SYMBOLS=TSLA,CDE` (section 4). A fresh, empty volume
mounted at `/app/state`. Autopilot armed through the dashboard flow (section 6.5).

**Sizing decided: exactly ORBStraddle's** (2.0% of ADT's day-start account equity, about $990 risk on a
~$49.7k account; 2.5% day budget; 3% own halt). Called out: this is roughly 2x ADT's normal ~1% per
trade and ORB loses its $12.5k cap (ORBStraddle's cap is 150% of equity per name).

## 4. ORBStraddle code changes (all default no-op, siblings unaffected)

a. `ORBS_EXCLUDE_SYMBOLS` (comma list, default empty). **Decided (Codex #5):** the symbols stay on the
   scanned board (so the regime's short fraction is identical to ORBStraddle's) but are removed from the
   ELIGIBLE pool before pick 1 and pick 2 are chosen, so the next candidate is promoted exactly as if the
   name had failed a gate; audit row reason `excluded_symbol`; execute() refuses them too as a backstop.
   Set to `TSLA,CDE` for ADT because ADT's morning plans trade them 09:45-12:00. The only deviation from
   "exactly"; called out.
b. **Read-only `GET /api/own_book`** (Codex #1, #2): the bot's own signed quantity per symbol straight
   from `ownership.py` (the book ORBStraddle itself trusts: parents AND broker-created UUID legs, partial
   fills, carried positions, rollovers), plus symbols with an own open order or an intent written but not
   yet terminal, `broken` flag, `as_of`, and the deployed commit (`RAILWAY_GIT_COMMIT_SHA`). No broker
   call inside the request (served from the persisted book).
c. **Orphan promotion checks the own book (Codex #6):** stale supervision becomes an orphan only if the own
   book still shows own quantity for that symbol; an account position with zero own quantity (ADT's, or
   the operator's) is not ours. Fixes a latent bug for JhoshuaStraddleLarge too. Test: stale row + foreign
   position = no orphan, entries not blocked.
d. `/api/state` shows `code_revision` (Codex #10).

## 5. ADT changes (repo AutonomousDayTrader)

1. **ORB entries off.** New setting `ORB_NEW_ENTRIES=False` (mirrors `OR15_NEW_ENTRIES`): the `orb`
   strategy keeps its id and checkpoint state (restore raises on id-set changes) but emits no signal.
2. **External book = the sidecar's own book (Codex #1, #2; replaces the v1 "net prefixed fills" idea,
   which missed UUID bracket legs and carried positions).** Every 30 s with the positions check, ADT reads
   `adt-orb /api/own_book` (2 s timeout).
3. **Mismatch check, per symbol (Codex "could freeze ADT all day"):** for every symbol, expected Alpaca qty
   = ADT local + sidecar own qty.
   - Symbol ADT holds and the numbers disagree: global mismatch, all ADT entries blocked (today's rule).
   - Symbol ADT does not hold: difference is attributed to the sidecar/operator; ADT blocks entries on
     that symbol only (`EXTERNAL_OWNED`), alarms if the sidecar's book disagrees with Alpaca.
   - Sidecar unreachable or `broken`: every symbol ADT does not hold that has an Alpaca position or open
     non-ADT order is blocked for ADT; ADT keeps trading everything else. Never a whole-day freeze.
4. **Occupied symbols in the final submit path (Codex #3).** One shared check in ADT's broker submit
   path used by every ADT arm (strategies, tri, OR15, swing): refuse if the symbol has a sidecar own qty,
   sidecar open order/intent, or any Alpaca position/open order ADT cannot attribute to itself, using a
   fresh Alpaca positions + open-orders read and a fresh own_book read (<= 5 s old). Residual race
   (both bots pass their checks within the same ~1 s before either order is visible) is accepted and
   called out: ORBStraddle enters only 09:38:30-10:15, at most 2 names a day, from a 250-name board;
   ADT's arms rarely enter in that window on the same name, and the per-symbol mismatch alarm catches it.
5. **Risk (Codex #4).** Each bot keeps its own limits (ADT $1,242 on its own ledger; ORB 2.5% day risk
   budget and 3% own halt). **New account-level guard in ADT:** if Alpaca account equity falls 5% below
   its day-start `last_equity`, ADT stops new entries and flattens its own positions for the day, and
   raises an alarm naming the sidecar's P&L. ADT cannot stop the sidecar (called out; the sidecar's own
   2.5% budget already bounds it).
6. **ORB card = the new engine** (unchanged from v1): reads the sidecar's `/api/state` + `/api/own_book`
   server-side, plain status, P&L from the sidecar's own ledger, "not reachable" never green, old ORB
   text removed everywhere in the UI.
7. **Request budget (Codex #12):** measure ADT's real Alpaca requests per minute from today's logs at the
   busiest minute; set the sidecar's budget so ADT peak + sidecar budget + reserve <= 180/min (e.g.
   90 + 40 if ADT peaks under 50). Not "exactly" (it only paces requests); called out.

## 6. Verification before the first armed morning

1. ORBStraddle: offline suite `tests/run_offline.py` green; with the vars unset the board, picks and
   orders are byte-identical on the recorded 09-22..09-28 sessions; own_book tests; orphan test.
2. ADT shared-account harness (Codex #13), fake Alpaca + fake sidecar, covering: bracket stop fill and
   target fill with UUID legs, partial fills, a carried sidecar position (uncleared, and closed by the
   08:00 sweep), sidecar unreachable/broken, same-symbol attempts by both bots, ADT exits/purge/flatten/
   breaker never touching sidecar orders, the 5% account guard, ORB silent with the flag. Full ADT suite.
3. Fresh, empty, dedicated volume for `adt-orb` (Codex #8); inspect book, supervision, orphans,
   runtime.env and `JSL_KILL` (the kill switch's real name) before arming.
4. Both services report the same commit SHA before arming (Codex #10). Sizing baseline: confirm
   Alpaca `last_equity` is valid for PA3CSVDZMMPY (Codex #11), else preflight fails and nothing trades.
5. Arm through the authenticated dashboard flow (`{"on": true}` with the session token; the server
   computes the fingerprint), then read back `armed`, destination and account (Codex #9).
6. **Decided: armed live (paper) for Tuesday 09-29, no watch-only morning.** Codex #13 recommended one
   unarmed observation morning. Overruled because it is a paper account and the risky parts
   (reconciliation of real fills, ownership) are only exercised by real fills; the harness above covers
   them offline first. Called out.
7. Morning check at ~10:20 ET: `adt-orb` board vs ORBStraddle's (same cards; picks may differ only by
   TSLA/CDE, occupied symbols and equity), ADT per-symbol mismatch clean, own_book matches Alpaca.

## 7. Rollback (Codex #7)

Disarm `adt-orb` autopilot from its dashboard: no new entries, but its supervisor keeps managing any open
position to its exits / 11:00 flatten. Never stop the service with an own position or working order open;
if it must go now, flatten its own positions from its dashboard, confirm flat in own_book and Alpaca, then
stop. ADT `ORB_NEW_ENTRIES=True` brings the old ORB back.

## 8. Order of work (tonight, after 16:00 ET)

1. ORBStraddle changes 4a-4d + tests, push after the close (redeploys ORBStraddle; its session is over).
2. ADT changes + harness + full suite, push (ADT redeploys; restart-safe).
3. Create `adt-orb` (same repo/branch), fresh volume, vars, verify SHA/state/baseline, arm.
4. Record in both repos' MEMORY.md and the memory index.

## 9. Codex attack log

Round 1 (13 findings: 2 P0, 7 P1, 4 P2), all accepted except #13 (watch-only morning, overruled in 6.6).
Raw review: `docs/orb_replacement/codex_attack_round1.md`.

Round 2 (`docs/orb_replacement/codex_attack_round2.md`): 8 of 13 FIXED, #3/#4/#12 PARTIAL, #13 NOT FIXED
(deliberate). Two rounds is the stopping rule; round-2 findings are now acceptance criteria (section 10).

## 10. Acceptance criteria added by round 2 (all must pass before arming)

1. **Freshness contract.** `/api/own_book.reconciled_at` = last SUCCESSFUL broker reconciliation (not the
   file write time). The sidecar reconciles own orders every 5 s from 09:30 to 11:05 ET. ADT treats a
   book older than 15 s, or `broken`, as unreachable (5.3 rules).
2. **Reservations visible.** The whole execution batch is written as reservations BEFORE the first POST;
   `/api/own_book` lists every nonterminal reservation and every pending/unanswered own order (even with
   zero known fill) and ADT treats all of them as occupied. Each is terminalized on reject/cancel/fill.
3. **`UNATTRIBUTED` is not "external".** A position on a symbol ADT does not hold that does NOT match
   the sidecar's book is labelled `UNATTRIBUTED`, blocks the symbol, and raises a local-book alarm.
4. **One real choke point.** The ownership check runs immediately before every actual entry POST in ADT:
   `engine._broker_execute` (strategies, OR15, swing) and `tri_execution` submit. Test proves each arm hits it.
5. **Atomic reservation, only when it matters.** New sidecar `POST /api/reserve {symbol, side}` (shared
   secret header `X-ADT-Token`), taken under the SAME lock the sidecar holds for its own pre-POST
   occupied check; TTL 60 s; the sidecar refuses symbols ADT reserved. ADT must hold a reservation for
   any entry between 09:35:00 and 10:16:00 ET (the only time the sidecar can enter). Sidecar unreachable
   in that window = ADT skips entries (at most ~40 min); outside it no reservation is needed. This closes
   the race instead of accepting it.
6. **Shared latched halt.** ADT captures Alpaca account equity at 09:30 (so overnight swing moves do not
   count). At -5% from that, ADT latches its halt (flattens its own intraday positions; swing stays, as
   today, called out) AND calls sidecar `POST /api/halt` (same secret): the sidecar disarms, flattens only
   its own positions, and latches for the day. Both halts are visible on both dashboards.
7. **Request budget re-measured with the new checks on** (per-entry positions/orders/own_book reads and
   the 5 s reconciliation), bursts included; sidecar budget set so the measured ADT peak + sidecar budget
   + exit reserve <= 180/min.
8. **Arming gate (replaces 6.6).** Armed for Tuesday 09-29 ONLY if items 1-7 and section 6 all pass
   tonight. If anything is not done, the sidecar runs UNARMED on 09-29 (scans, decides, records what it
   would have done) and is armed the next evening after the checks pass. Codex's watch-only advice
   is therefore taken whenever the build is incomplete, and overruled only when every check passes.
