The plan isn't ready to build yet. Seven things in today's code would break it or make it trade wrongly on day one. The dedicated tranche design is the right call, with some conditions. I read the code only: no edits, no orders sent, and I made no choice on passive limit vs T+2 market.

## P0: breaks on day one

1. **Too many Alpaca requests.** Right now `_poll_protection` checks each order leg once a second (`or15_execution.py:120-122,287-292`, driven by `main.py:2388`). That's about 120 requests a minute for one protection pair. With three pairs (TSLA T1, TSLA T2, CDE) it's about 360 a minute, plus the settle loop (`engine.py:343-358`). Alpaca paper allows about 200 a minute. Once it starts refusing, protection, exits, entries and sync all fail together.
   - **Fix:** fetch each pair once with `nested=true`, every 3 to 5 seconds. Check right away only when an exit is due.
2. **The size caps reject orders instead of shrinking them.** At $49.7k, 0.75% risk is $372. But one position is capped near $24.85k (`main.py:78`, `account.py:165-167`, the notional cap in `risk.py`). A TSLA short, with its stop at the range midpoint, usually wants more than that. `main.py:526` then rejects the order, and a rejected setup uses up the day.
   - **Fix:** have the controller shrink the size to fit the cap first (TSLA rounded down to an even number) and record the real risk. Raising the cap instead is your call, since it affects every strategy.
3. **Risk shortcuts only recognize OR15 by name.** The code checks for the exact name `"tsla_or15_retest"` in about 20 places: `engine.py:268,274,494,560,619,713,735,749`, `main.py:411,448,450,870,921,1052,1095,1153,1820,1865` and `risk.py`. Without that name, the new arms get resized by VIX, fail the stop-distance limits, hit the 3-position limit, or get refused by the "exactly 1 TSLA share, long only" rule at `main.py:450`.
   - **Fix:** one list of fixed-rule owners, used in all those places.
4. **Short trades break recovery.**
   - Recovery only looks for emergency closes that sell (`engine.py:307`), so a short's buy-back is never found after a restart.
   - `before_submit` treats every non-buy as an exit (`or15_execution.py:48-53`), so a short entry would skip the T+2 timing and quote checks.
   - Only buy entries get a stable order id (`engine.py:268`), and the protection order is hardcoded to sell (`broker.py:166`).
   - **Fix:** decide by owner and by entry vs exit, never by buy vs sell.
5. **The code assumes one share.** `engine.py:313` stops after the first fill ("one whole share completes this exit"). `or15_execution.py:203` treats any quantity other than 1 as invalid. `_run_exit` closes the whole position with one exit id. Each tranche needs its own exit ids and must keep going until its own remaining shares reach 0.
6. **Adding CDE to the watchlist would let ORB, VWAP, Mean Reversion and News trade it** (`main.py:1862`). Subscribe to CDE through a separate stream list instead.
7. **The app won't start after deploy.** Restore only accepts going from 4 to 5 strategies (`runtime_state.py:231-234`), and production's saved state has 5. Going to 7 fails. Add explicit 5→7 and 4→7 paths, and keep OR15's version and source hash unchanged, because restore checks them (`:238-240`).

## P1: fidelity, risk and timing

8. **TSLA is shared.** ORB can hold TSLA from about 09:35, and the new arm would then lose its setup (`or15_execution.py:35-37`, `:143`). You need to decide whether TSLA and CDE are reserved for these arms from 09:30.
9. **The risk limits are vague.**
   - Does the 1.5% combined cap count other strategies' positions? If it does, it's always full.
   - Is the 2.5% loss stop ($1,242, stricter than today's $1,500) for the whole account or only these arms? Does swing P&L count?
   - Which price sets the size (the T+2 ask, as `or15_execution.py:101` uses today)?
10. **Cent rounding distorts CDE risk.** CDE stops can be only a few cents from entry, so rounding one cent can change R by 10 to 20%. Size from the rounded broker stop, and write down the rounding direction for each side.
11. **The short rules have gaps. These need your call, and I haven't guessed.**
    - Does "before 11:00" mean the bar's start time or the decision time?
    - Does a breakdown that fails the QQQ check use up the day?
    - If a long is waiting for its retest and a short breakdown fires, which one wins?
12. **Missing minutes.** Alpaca sends no bar for a minute with no trades. Today, one gap skips the whole session (`tsla_or15_retest.py:156,232`), and a single QQQ gap kills both arms. Check how complete CDE's minute data is in the lab data.
13. **Blocking calls.** One entry can freeze the app for up to about 6 seconds (`broker.py:263-265`). TSLA and CDE often signal on the same bar, so the second one can miss its 5-second window. A failed T1 exit also pauses T2's exit, because the backoff is per symbol (`engine.py:188`).
14. **Short checks.** Alpaca paper only offers the `shortable` and `easy_to_borrow` flags (no borrow lookup), and short-sale restriction days can reject shorts. If a short fills above its stop, abort and flatten it.
15. **End of day paths.** The 15:50 cleanup, the emergency sweep, the circuit breaker and manual flatten (`main.py:1178-1202, 2100-2160, 3039-3136`) all have OR15-only branches. They need matching branches for the new arms.

## If you choose passive limits

The current code can't leave an entry order waiting. `submit_and_settle` cancels it after about 4 seconds, the settle loop cancels any order not marked as resting every 5 seconds, and `before_submit` only allows T+2 plus 5 seconds. Supporting it would need resting-order handling, protecting partial fills while the rest is still waiting, and a firm cancel at the cutoff. The market-at-T+2 option reuses what already exists.

## The tranche design

I agree with a dedicated lifecycle. The generic bracket can't do this:
- It moves the stop to breakeven after target 1 (`bracket.py:423-433`), which the source forbids.
- It allows one bracket per symbol and has its own split rule.
- Any finished close order marks the whole trade done (`main.py:1070-1073`), so a T1 time exit would close T2's books too.
- It only supports longs.

You also need two separate protection pairs, because Alpaca won't let one stop plus two targets reserve more shares than you hold.

Conditions:
- **Track each tranche separately:** planned, filled and remaining shares, its target, its deadline, its protection ids and its exit ids. Work out remaining shares from actual fills, never from the order's status alone.
- **Partial protection fills:** I'm not sure how Alpaca handles a protection pair that partly fills and then ends. Test it on paper with 2 to 4 shares, including two same-side pairs on one symbol, before turning this on.
- **Odd or partial entries:** you need to set a rule. One option is T1 = half rounded down, T2 = the rest, logged as an exception. A 1-share fill needs its own rule.
- **Ledger:** write one row per trade with a tranche list, using the existing row format (`main.py:949-1000`), plus per-tranche stats, since the source treats 1.5R and 2R as separate strategies.
- **Research hooks and the UI** (`main.py:845-871`, `HoldingNow.tsx`) currently assume one bracket and Tesla wording, so both need updating.

The full critique with every line reference is in `/Users/mo/.claude/plans/read-plan-2026-09-25-tri-engine-md-and-d-jaunty-peach.md`. I didn't write to `docs/tri_engine/CLAUDE_PLAN_CRITIQUE.md` (it's empty) because this was review only.
