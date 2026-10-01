# SPY turn of month and COIN bitcoin follow, live implementation plan

Revision 2, 2026-09-30. Three read only subagents attacked revision 1 from research fidelity, execution safety, and operator security angles. Their findings are resolved below before implementation.

## 1. Goal

Add both operator selected rules to AutonomousDayTrader. They place real orders on the existing Alpaca paper account on the first eligible session after deployment.

There is no shadow mode, trial period, observation period, one share smoke trade, delayed activation, or size ramp. Safety comes from deterministic tests, durable intents, one broker owner per symbol, idempotent broker identities, exact model calculations, reconciliation, and the existing account loss breaker.

## 2. Honest evidence label

SPY turn of month was not a pre registered strategy. It is a live translation of a post holdout atlas pattern selected after both years and multiple calendar slices were viewed. The 72 historical observations had a gross mean of 20.8 basis points and gross t of 2.39. After the study's 6 basis point auction cost, the mean was 14.8 basis points, wins were 62.5%, and t was 1.70. It is labelled `post hoc atlas pattern, not preregistered` everywhere.

COIN bitcoin follow is frozen research config `F2_btc` index 11. It failed the design gate with t 0.88. It looked good only after the later year was inspected. It is labelled `post holdout selection, design gate failed` everywhere.

Neither strategy may be called validated, proven, confirmed, or profitable.

## 3. Operator decisions

| ID | Decision | Reason |
|---|---|---|
| D1 | Build both rules | Operator selected both |
| D2 | Live immediately | Explicit instruction and standing ruling |
| D3 | SPY target size is 20% of fresh Alpaca equity. COIN target size is 10%. Each requested notional is capped at $25,000 using the reference price | Best judgement after the user delegated decisions. These are permanent risk overlays, not backtested sizing and not ramps |
| D4 | Whole shares | Auction orders and shorts cannot depend on fractional shares |
| D5 | No strategy stop or target. The existing account loss breaker can close them early | Matches the rules while retaining the account emergency |
| D6 | SPY owns its symbol from the durable OPG intent until release. COIN owns from before the first 09:30 event until no signal or release | Prevents another strategy from touching the shares |
| D7 | A pre existing position, order, bracket, reservation, or fractional broker quantity causes a skip and broker mismatch. The controller never takes over another strategy | Prevents position merging or flipping |
| D8 | No new API route and no new dashboard layout | Avoids expanding the existing unauthenticated write surface or bypassing mockup approval. State is verified through bounded structured logs and existing broker reads during deployment |
| D9 | Every operational suppression is recorded after the research decision | Separates the model signal from broker, risk, data, and ownership feasibility |
| D10 | Unknown state with exposure causes recovery halt, not strategy off | An unread state cannot strand a position |

The percentages and $25,000 limit are request sizing targets. A market or auction fill can gap beyond them. Actual fill notional is recorded and any overage is alerted. A strict fill cap would require a limit order and would no longer match market on open or market at 09:36.

## 4. Exact SPY rule

1. Eligible sessions are the last NYSE session of a calendar month and the first two NYSE sessions of the next month.
2. Early close sessions remain eligible because the historical atlas included them.
3. Eligibility uses a complete Alpaca calendar window containing the prior and next boundary month. The local 2026 to 2027 calendar must agree on trading day and close time.
4. Beginning at 19:05 ET on the prior session, submit one market on open buy with time in force `opg`. A startup before 09:27 submits the same durable intent with the same client id.
5. Size is `floor(min(0.20 * fresh Alpaca equity, 25000, usable buying power) / reference price)`. Reference price is the latest validated SPY regular session close fetched through the existing relay capability. It is a sizing target, not a fill cap.
6. An ambiguous OPG attempt is resolved only under the same client id. A 404 after an ambiguous write is not permission for a distinct order. A fallback market entry is allowed only when the OPG is definitely terminal with no live quantity, all fills are booked, the broker position is reconciled, and the time is 09:30 through 09:31. The fallback is labelled an execution deviation and is excluded from official open comparisons.
7. After the entry order is terminal and every fill is booked, immediately submit a closing auction exit for the exact owned shares. This broker held order protects the close through an application restart.
8. Exit timing is derived from the agreed session close. `cls` must be accepted before close minus 10 minutes. A definitely refused close order may use a plain market exit at close minus 30 seconds. An accepted or ambiguous close order is never replaced while live.
9. A continuous close fallback or next open recovery is labelled an execution deviation and keeps its actual fill price and reason.
10. Release requires local strategy quantity zero, broker quantity zero, every saved order terminal, no worker able to write, and no open order with this strategy's client id prefix.

## 5. Exact COIN rule

The model is research config `F2_btc`, `mode=follow`, `at=09:35`, `k=1.0`, `exit=close`. It uses no beta, no COIN return term, and no 60 session regression.

1. Reserve COIN before relay subscriptions and before any 09:30 event can reach an existing strategy.
2. Reconstruct prior normalized bitcoin moves far enough back to obtain the last 20 finite values, with at least 15. The oldest move includes its preceding stock session anchor.
3. For each move, target the prior stock session's last regular bar start, 15:59 on a full day or 12:59 on an early close, and the current session's 09:34 bar start.
4. Port the research endpoint selector exactly. Stable sort BTC bars by naive Eastern ordinal minute. Select the rightmost bar starting at or before the target and no more than four start minutes older. Use that bar's close.
5. Compute elapsed hours from naive Eastern civil minute keys, not UTC elapsed time. `zmove = (decision_close / prior_anchor_close minus 1) / sqrt(elapsed_hours)`.
6. Sigma is the sample standard deviation with `ddof=1` of the last 20 finite prior zmoves, today excluded. Require at least 15 values, finite sigma, and sigma strictly above zero.
7. Today's model signal is long when `zmove > sigma`, short when `zmove < negative sigma`, and no trade otherwise.
8. For a short, apply the exact historical Rule 201 screen. Skip when the preceding session low is at or below 90% of the close two sessions back, or today's low through the completed 09:34 COIN bar is at or below 90% of the preceding close. Alpaca tradable, shortable, and easy to borrow are separate execution checks.
9. The research model entry is the raw 09:36 open, then 09:37 or 09:38 if missing. Live dispatch occurs once at the earliest allowed 09:36 instant. Actual broker time and price are separate from the model entry. A dispatch after 09:36:05 skips rather than chases.
10. Size is `floor(min(0.10 * fresh Alpaca equity, 25000, usable buying power) / fresh COIN price)`. Fresh means a validated quote received within two seconds or a completed bar within 90 seconds. Restored prices are never fresh.
11. After terminal entry and fill booking, submit the close order immediately. Early close timing, close fallback, unresolved recovery, and release rules match SPY.
12. The historical config used a hindsight full session data completeness condition. Live trading cannot know that at 09:35. The causal model omits that condition and reports this as an unavoidable research limitation. The parity audit must show whether it changed any historical signal.

## 6. Operational gates after the model decision

The model decision is always saved before these gates.

1. Expected Alpaca paper host and account `PA3CSVDZMMPY`.
2. Durable persistence healthy and an actual checkpoint revision advanced before exposure.
3. Broker comparison clear.
4. Controller state readable and internally consistent.
5. Account and risk status permit entry. SPY's prior evening OPG is for the next session, but persistence, broker match, ownership, buying power, and account identity still apply. An active or pending day one position reserves the full remaining daily loss budget against unrelated new entries because it has no strategy stop. The other operator selected day one rule may still enter only while the breaker is armed and all aggregate exposure gates pass.
6. Atomic symbol ownership across account positions, engine orders, brackets, Swing, TRI, OR15, ORB, overnight, and this controller.
7. Aggregate exposure and pending entry reservations. SPY and COIN consume ordinary intraday position slots. The pending SPY notional is included before its fill. Overnight sizing at 15:46 includes any day strategy shares not yet exited.
8. Fresh sizing price and positive whole share quantity.
9. For COIN short only, Rule 201 plus broker tradable, shortable, and easy to borrow.
10. No older unreleased lifecycle in the same symbol. Recovery always wins over a new day.

## 7. Trust model

Trust Boundaries: AlpacaRelay BTC and stock responses, stock websocket events, Alpaca account, asset, calendar, order and position responses, SQLite checkpoint, wall clock.

Data Classification: GREEN public market prices and calendar. YELLOW private paper account balances, positions, order ids and execution history. No PII.

Attack Surface: No new public route. Existing fixed TLS connections to Alpaca paper and AlpacaRelay. The new code receives existing broker and relay capabilities through dependency injection. It does not retrieve a credential from an environment variable, create a credential, log one, or construct a caller controlled URL.

Key Risks: malformed or stale bitcoin data, duplicate order after an ambiguous write, symbol ownership race, close failure leaving unintended exposure, restart while a worker can still write.

Controls: production relay origin pin, fixed path, finite price and timestamp validation, response body and row budgets, deterministic client ids, strict durable write before entry, cumulative fill watermarks, one shared owner predicate, recovery priority, broker held close orders, shutdown write fence, bounded sanitized logs.

The existing repository has pre existing unauthenticated write endpoints, wildcard CORS, and environment sourced broker credentials. This change does not add routes, widen endpoint actions, or add credential retrieval. Those system wide issues are recorded as existing security debt and are not silently represented as fixed by this strategy change.

## 8. Architecture

### 8.1 Pure rules

Create `backend/app/core/day_one_schedule.py` with strategy ids, execution policy, timing relative to official close, calendar eligibility, research BTC endpoint selection, zmove, sigma, Rule 201, sizing target, and JSON state validation. No I/O or wall clock.

### 8.2 Controller

Create `backend/app/core/day_one_execution.py` with independent SPY and COIN lifecycles and one worker job per lifecycle. Legal phases are `IDLE`, `RESERVED`, `ENTRY_INTENT`, `ENTRY_PENDING`, `HELD`, `EXIT_PENDING`, `RECOVERY`, `DONE`, `SKIPPED`, and `RECOVERY_HALT`.

Every broker mutation is `mutation intent`, then an actual durable revision advance, then broker write, then client id lookup, then cumulative fill booking, then durable checkpoint. The durable intent stores role, deterministic client id, target order id, maximum remaining quantity, predecessor status and fill watermark. If persistence is unavailable, risk reduction may use only an identity that was already durably precommitted.

Each role has an immutable target quantity. Later attempts use target minus cumulative booked fills. No second order can exist while any prior attempt is nonterminal or ambiguous.

Unknown transport outcomes, HTTP 408, 409, 425, 429, and 5xx remain ambiguous. Only deterministic validation and explicit broker refusals are final.

### 8.3 Integration

Create `backend/app/core/day_one_integration.py` modelled on the established overnight integration.

It provides account admission, relay reads through the fixed existing capability, booking, completed trade rows, shared ownership, startup reconciliation, broker comparison preparation, health for internal logs, and an ORB occupancy reason.

Booking preconditions are strict. Entry needs no position or the exact same strategy and side for a partial. Exit needs the exact strategy, exact side, and quantity no greater than owned shares. A contradiction enters recovery halt and does not call account fill logic.

### 8.4 Main wiring

1. Construct `day_one` beside `overnight`.
2. Add plain JSON checkpoint key `day_one`. An old checkpoint gets an empty state. A future unread state with any ownership envelope causes global recovery halt.
3. Restore the minimal ownership envelope before session boundary liquidation. Build, reconcile all client ids, book fills, then perform the first broker comparison.
4. Reserve COIN before relay clients and event replay can deliver 09:30 data.
5. Tick on the runtime clock. Pass only SPY and COIN bars and quotes to its fresh price and Rule 201 cache.
6. Replace the single engine order guard assignment with a composite that preserves overnight and day one guards.
7. Add day one occupancy under ORB's existing lock. A check and claim is one critical operation.
8. Add one `is_day_one` predicate to session boundary, generic flattening phases, loss breaker, broker settlement, and position serialization. Generic code never sends an order for these shares.
9. The loss breaker calls controller `request_exit` once and excludes the shares from generic liquidation. The emergency transition is durably saved. An accepted CLS is cancelled and required terminal before a market exit. Raced fills are booked, signed broker inventory is read, and only the remainder is submitted. An ambiguous cancel sends no second exit and enters recovery halt.
10. Do not route the existing public manual endpoints to the new controller in this release. They cannot cancel or close these positions. This avoids expanding an unauthenticated write surface. The account breaker and automated recovery remain active.
11. Before broker comparison, reconcile controller reads and verify local strategy, side, and quantity, not only aggregate symbol quantity.
12. Shutdown uses separate fences. It closes exposure increasing writes first, drains and reconciles entries, and permits only precommitted CLS or emergency reduction through an exit only gate. The final write fence closes only after every discovered fill has a broker held exit or a durable unresolved recovery identity. It then applies completed outcomes and permits the final checkpoint.

## 9. Close and recovery

A closing auction order is submitted immediately after the entry is terminal and its fills are booked. It normally remains accepted until the auction. It is not treated as failed before the close.

When a close order is definitely refused, a market fallback intent may be prepared. Before sending, the prior exit must be terminal, all raced fills booked, broker inventory read, and only the remaining strategy quantity sent. The worker checks the wall clock immediately before the POST.

If shares remain after close, recovery depends on phase.

1. Before 09:28, queue OPG for the next open.
2. From 09:28 to 09:30, wait and send market after the open.
3. During regular trading, send market after all older exits are terminal.
4. After the close, queue the next OPG beginning at 19:05.

Recovery blocks the next SPY or COIN lifecycle. Release requires both books flat, all identities terminal, no open matching order, and no worker that can write.

## 10. State and trade records

Client ids include strategy, session, role, and attempt. Examples are `adt-tom-spy-20261001-entry-1` and `adt-btc-coin-20261001-exit-1`.

State contains a version independent ownership envelope with symbol, strategy, side, session, target quantity, entry quantity, exit quantity, and all client ids. Runtime validation enforces one lifecycle per symbol, legal roles and sides, one nonterminal attempt per role, nondecreasing fill watermarks, local position ownership, and released means fully flat.

Completed trade rows are written only after entry quantity equals exit quantity, all orders are terminal, and both books are flat. Rows include model decision, operational gates, actual legs, actual PnL, execution deviation, and the honest evidence label.

Logs are bounded JSON records with event name, correlation id, strategy, session, role, side, quantity, status, and sanitized error class. They never include headers, credentials, full account number, full response bodies, or unrestricted exception strings.

## 11. Research parity

Generate a committed golden fixture independently from the frozen study.

SPY fixture contains all 72 dates, both early close dates, official open and close, gross return, 6 basis point model cost, and net return. It asserts 37 observations in year one and 35 in year two.

COIN fixture locks config index 11 and every historical date's selected anchor and decision bar start and close, elapsed civil hours, zmove, finite history count, sigma, threshold result, Rule 201 result, direction, model entry bar and raw open, official close, model cost, and net return.

A separate causal audit removes the full day completeness condition and reports any changed signal. Beta and COIN close return mutations must not change a follow decision.

## 12. Validation

1. Pure calendar tests for every month in 2026 and 2027, early closes, partial boundary months, and Alpaca disagreement.
2. Full research parity fixture.
3. Daylight saving weekend elapsed hour tests.
4. Last 20 finite history, 15 value minimum, zero sigma, no lookahead, fallback endpoint bars, stable duplicate handling.
5. Exact Rule 201 tests including the 2025-04-07 research skip.
6. Response origin, content type, 64 KiB per body, aggregate budget, fixed page and row limits, nonfinite prices, stale timestamps, and deadline tests.
7. OPG, market, CLS, partial, refused, ambiguous, cancel race, duplicate id, fallback, early close, and long and short exits.
8. Crash and restore at every exposure transition.
9. Cross strategy ownership and composite guard tests proving overnight is still protected.
10. Fractional broker quantity and wrong local strategy recovery halt tests.
11. Loss breaker and session boundary delegation tests.
12. Consecutive eligible SPY days with unresolved recovery.
13. Completed trade row idempotency.
14. Shutdown while each worker write is in flight.
15. Integrated deterministic two day run with SPY, COIN, and all existing overnight holds.
16. Backend full suite, frontend tests and build. No visible UI change means no screenshot change.
17. Read only security scan and semantic diff review. Fix blockers and highs until ship.

## 13. Deployment

Do not use `scripts/deploy_and_push.sh`. It stages unrelated files and pushes. Git push is prohibited in this session.

Build and commit locally. Require a clean tree. Export the exact commit with `git archive` into a temporary directory. Compute the runtime day one source SHA256 over the fixed file list and set the nonsecret Railway value `DAY_ONE_BUILD_REVISION` to that digest without triggering a separate deployment. Production admission and health require an exact match with the digest recomputed from the uploaded files. Use the already linked Railway CLI to upload that clean directory to the explicit production service. Record the deployment id and digest. This direct deployment is not on `origin/main`, so the next GitHub triggered deployment can replace it. Record a push freeze in MEMORY.md until the commit reaches the remote through an operator controlled path.

Use the established safe window 19:15 to 08:50 ET. Do not deploy from 18:50 to 19:15 because overnight opening sales queue at 19:00. Oct 1, 2026 is eligible. A deployment after 19:15 must perform every account, calendar, ownership, persistence, broker match, price, buying power, and exposure check before creating the SPY OPG intent.

Post deployment reads must prove health, persistence, expected paper account, no broker mismatch, overnight unchanged, exactly one SPY OPG client id, and no COIN order before its 09:35 decision. Without adding a public route, startup emits one bounded deployment attestation after reconciliation containing runtime revision, checkpoint revision, expected account match, broker match, an overnight state hash and count, day one client id counts by symbol and role, and recovery status. Pair it with read only broker order and position queries. A queued order is an external side effect explicitly authorized by the user's live deployment request.

## 14. Rollback

Rollback is two stage. First deploy an exit only build retaining state decoding, lookup, fill booking, ownership, alerts, and exits while refusing new entries. Wait until both books are flat and every matching broker order is terminal. Only then may code without this controller deploy.

## 15. Attack findings and resolution

| IDs | Findings in plain words | Resolution |
|---|---|---|
| RF01, RF18, RF20 | SPY was not a tested strategy and its net statistics were misstated | Label corrected, gross and model net separated, no validation claim |
| RF02, RF03, EXE015 | Early close days were in history but missing live timing | Early closes eligible, every deadline derives from session close |
| RF04 to RF08 | Bitcoin endpoint, price field, civil elapsed hours, duplicates, and sigma differed from research | Exact selector, close price, civil minute key, stable rightmost selection, `ddof=1`, finite and positive sigma frozen |
| RF09, RF10 | Day one history and beta behavior were unclear | Reconstruct at least 15 and up to 20 finite moves. Follow uses no beta or COIN return |
| RF11, F16 | Historical Rule 201 short screen was missing | Exact prior day and current day screen added before broker feasibility |
| RF12 to RF14 | Model entry and exit prices were being confused with broker fallbacks | Model and actual fills separate. Every fallback is an execution deviation |
| RF15, RF16 | Historical full day data gate is not causal and operational filters are not research filters | Causal audit added. Research decision saved before operational disposition |
| RF17, EXE027, F18 | Sizing was not researched and a market fill cannot guarantee the cap | Permanent risk overlay labelled. Percent and cap are reference price targets. Actual drift alerted |
| RF19 | Fixture was too weak | Full date, endpoint, signal, Rule 201, fill, close, cost and net fixtures specified |
| RF21 | Month boundaries could be omitted | Complete boundary calendar and explicit historical counts required |
| EXE001, F17, F19 | Direct controller could bypass risk and double reserve account room | Central admission and aggregate pending notional reservation added |
| EXE002, F21 | Checkpoint could say true without writing | Entry requires actual revision advance and strict durability callback |
| EXE003, EXE004, F20 | Startup, boundary and generic flatten could race controller shares | Shared ownership predicate and restore envelope precede every generic path |
| EXE005 to EXE009, F13, F14 | Partial, ambiguous and consecutive day orders could double, oversell or wash trade | Immutable target, cumulative fills, terminal predecessor, recovery priority and strict release |
| EXE010, EXE011 | COIN and ORB could race and the single engine guard could lose overnight protection | Early atomic reserve under ORB lock and composite guard |
| EXE012, EXE013, EXE014, EXE023, EXE024 | Aggregate positions and unread state could hide wrong ownership | Strict booking and cross state validation, fractional conflict, recovery halt |
| EXE016 to EXE018 | Close order was too late and fallback too optimistic | CLS immediately after entry, dynamic deadlines, phase based recovery |
| EXE019, EXE020, F26 | Shutdown and rollback could remove the only owner | Write fence, bounded drain, exit only rollback stage |
| EXE021 | Flatten all could miss queued entry | No manual route expansion in this release. Automated breaker and recovery own exits |
| EXE022, F01 to F04 | Existing APIs and environment secrets violate current security standards | No new or widened route and no new credential retrieval. Existing debt recorded, not claimed fixed |
| EXE025 | Existing price cache has no freshness | Dedicated timestamped nonrestored price cache |
| EXE026 | Paper account identity was not rechecked | Expected paper host and account gate before every entry |
| EXE028, F22 | Controller fills could disappear from trade history | Idempotent aggregate trade row required |
| EXE029, F08 to F12, F23 | State and alerts could be invisible | No public data expansion. Structured deployment logs and broker reads verify state. UI redesign deferred to approved mockups |
| F05, F06 | Relay URL and response budgets were vague | Production origin pinned, fixed path, strict byte, row, call and time budgets |
| F07 | Mode and settings were undefined | Always live code constants for this operator selected release, exit only behavior retained during halt |
| F15 | Tomorrow's startup order needs gates before write | Every gate moves before durable intent and POST |
| F24, F25 | Deployment without push needs an exact clean artifact and can be replaced later | `git archive`, explicit Railway upload, revision check, recorded push freeze |
| F27 | Logs could leak broker data | Bounded structured allowlisted fields and redaction tests |
| V2 1 | Exit and cancellation writes were not durably precommitted | Every broker mutation now needs a durable revision. Risk reduction can use only a precommitted identity if saving fails |
| V2 2 | Shutdown could discover a fill after closing all writes | Separate entry and exit fences keep precommitted close protection available until every entry is reconciled |
| V2 3 | Loss breaker did not define cancellation of a resting CLS | Durable emergency transition, terminal cancel, raced fill booking, inventory read, then one remainder order |
| V2 4 | No stop positions did not reserve loss budget | Active or pending day one exposure reserves the remaining budget against unrelated entries |
| V2 5 | Deployment proof could not attest local state | Bounded startup attestation plus read only broker queries |
| R00 | Automated gates scanner unavailable | Manual attack completed. SNARE static rules applied |

## 16. Final review outcome

The first implementation review reproduced two P0 and ten P1 failures. They were fixed with 48 additional regression checks. The second review found one remaining P0 and two P1 failures. Rejected or unsent exits no longer count as protection, COIN reservation now checks brackets and staged Swing entries, and production admission plus health now require the exact source digest uploaded to Railway. The final blocker review returned `SHIP`. The security recheck returned `PASS` with no introduced blocker or high finding.
