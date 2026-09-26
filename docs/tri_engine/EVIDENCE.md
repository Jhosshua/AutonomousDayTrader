# TSLA / CDE Morning Plan: build evidence (2026-09-25/26)

Follow-up review and fixes: [REVIEW_2026-09-26.md](REVIEW_2026-09-26.md).
The updated implementation passes 791 backend tests and all 29 synthetic replay
cases. The original build evidence below is retained as historical context.

Source: `/Users/mo/multi_stock_edge_lab_3yr/EXECUTION_PLAN.md`, frozen as
`SOURCE_EXECUTION_PLAN.md` (sha256 `f89a762e...642bb`, checked at startup).
Two arms on the existing Alpaca **paper** account, no shadow period:
`tsla_asymmetric_dual` (Tesla Morning Plan) and `cde_asymmetric_dual` (Coeur Morning Plan).
The old `tsla_or15_retest` takes no new entries (`OR15_NEW_ENTRIES = False`).

## Operator decisions (2026-09-25)

| Question | Decision | Why it was asked |
|---|---|---|
| Long entry order | **Market at T+2** (both sides) | Plan table says "passive limit at OR high", but its numbers came from a T+2 raw-open fill. A limit at the high fills mostly on losers. |
| TSLA vs older arms | **First come, first served** | ORB/VWAP/News/MR also trade TSLA. Once the plan has a signal or position it owns the symbol. |
| Opening range | **As written: 09:30-09:44** | The research built its range (and QQQ VWAP) from 04:00 pre-market bars. See "Finding" below. |
| Position cap | **Size by 0.75% risk, only buying power limits it** | The $25k per-position cap refused 92 of 146 real 2026 TSLA plan trades. Other arms keep the cap. |

Made by me, flagged to the operator:
- Daily loss stop is **account-wide**: min($1,500, 2.5% of session-start equity). Rebuilt on restart.
- The 1.50% combined cap counts only this plan's TSLA+CDE risk.
- Fill slippage: actual risk up to 1.5x the 0.75% budget is kept and logged; beyond that, or a fill through the stop, closes the trade.
- Missing minute bars after 09:45 are skipped like the research loop (31 of 314 CDE days have one). A missing 09:30-09:44 bar skips the day.
- A partial entry fill is kept as a smaller trade; the rest is canceled 5 s after T+2.
- A relay feed outage does not close a trade (stop/target rest at Alpaca, time exits run on the clock).
- Tight-stop shorts that need more buying power than the account has are shrunk to fit.

## Finding: the plan's statistics describe a different opening range

`research/run_full_dual_audit.py` loads files that start at 04:00 ET and takes
`reg[minute_et < "09:45"]` as the opening range, so OR high/low and QQQ VWAP
include pre-market. It also allows shorts on the 11:00 bar ("prior to 11:00" in the text).

`scripts/tri_research_parity.py --emulate-research` copies those three quirks into a
replay of the LIVE signal code: **565/565 TSLA sessions match** the research's saved
signals (381 trades, 184 no-trade). Without the quirks (what runs live): 247 match,
211 differ. So the live logic equals the research apart from the range definition.

In-sample results with the research's own fill simulator and the plan's exits
(`scripts/tri_variant_backtest.py`, normal = 6 bps round trip):

| Variant | TSLA 1.5R / 180m | TSLA 2R / 240m | CDE 2R / 180m |
|---|---|---|---|
| As researched (pre-market range) | 381 tr, 55.1%, +31.0R, p=.023 | 381 tr, 52.2%, +34.4R, p=.025 | 140 tr, 55.0%, +18.8R, p=.031 |
| **As written (live)** | 448 tr, 52.9%, **+44.1R**, p=.019 | 448 tr, 46.4%, +26.1R, **p=.13** | 178 tr, 48.3%, +12.5R, **p=.19** |

The pre-market row reproduces the plan's TSLA table exactly. No script for the CDE
numbers exists (only `megacap_midcap_intraday_edge_lab/artifacts/cde_dual_2.0r_audit.json`,
189 trades); neither variant reproduces 189. All rows are in-sample on the data the rules
were chosen from; treat them as an upper bound.

## Requirement to evidence

| Requirement | Evidence |
|---|---|
| Signal rules (OR, retest, breakdown, QQQ VWAP, ATR, windows, one setup/day) | parity 565/565 with quirks emulated; `test_tri_signals.py`; fidelity attacker replayed 146 days vs a pandas port: 146/146 |
| Entry at T+2, stops, targets from actual fill, 50/50 TSLA split, 180/240/180 holds, 15:55 flat | `scripts/tri_real_day_replay.py`: real 2026 TSLA days through `main.handle_bar_event`: **145/146** exits identical to the research simulator (the 146th: both skip, T+2 open beyond stop). CDE 2024-25: **178 identical, 56 = research refuses days with a missing holding bar**, 0 unexplained |
| Synthetic edge cases (target/stop/both-hit/gap/time/half-day, both sides, both symbols, coexistence with all arms, no-signal, noon freeze) | `scripts/run_tri_engine_dry_run.py`: 29/29 PASS, `DRY_RUN_EVIDENCE.json` |
| Broker lifecycle (real `AlpacaBroker` over mock HTTP) | `test_tri_broker_lifecycle.py` 26 tests: both sides, native OCO per tranche, cancel races, restart, partial fills, stuck cancel, lost POST reply, 429 blips, quote-storm save/poll counts, sizing |
| Upgrade from the deployed 5-strategy checkpoint | old code (git HEAD worktree) wrote a checkpoint; new code restored it, saved, restored again |
| Startup file hash in the image | Dockerfile now copies `docs/tri_engine/SOURCE_EXECUTION_PLAN.md`; image-layout hash check passed |
| Backend suite | 773 passed; 1 pre-existing failure `test_swing_unresolved_entry_reuses_one_alpaca_order_and_late_fill_gets_stop` hard-codes 2026-09-25 (fails on HEAD too) |
| Dashboard | `ui_audit/*.png`: 7 states x desktop 1366 / mobile 390, no horizontal overflow; plain wording, R numbers only under "Show pro words" |

## Reviews

1. Claude CLI attack of the plan: `CLAUDE_PLAN_CRITIQUE.md` (7 P0, all addressed).
2. Codex execution review E1-E6 (`REVIEW_EXECUTION.md`): all fixed with tests.
3. Rule-fidelity attacker: code matches plan; found the pre-market range (independently),
   the $25k cap, transient-partial kill. Fixed or decided above.
4. Execution attacker: save-on-every-quote, lost-reply forgotten shares, 429 closes trade,
   leaked event key freezes tri, request bursts, feed-reconnect flatten. All fixed with tests.
5. Second-round attacker on the fixes: stuck-cancel partial never protected, unrelated broker
   shares stall entry forever. Both fixed with tests (mutation-checked).

## Known limits (not fixed)

- Alpaca OCO cannot guarantee stop-first on a bar touching both levels; live can differ from replay there.
- If Alpaca rejects a close after its OCO was canceled, the bot retries and now
  shows a broker warning on the strategy card. Broker refusal can still delay an exit.
- New tri-engine protection uses GTC and is retained outside regular market
  hours while a market exit is unavailable. This preserves resting orders for
  the next session; it does not enable overnight execution or guarantee a stop price.
- Two OCOs splitting one TSLA position were not tested against real Alpaca; if the second is refused the plan closes both halves (`PROTECTION_REJECTED`).
- Nothing here proves a real session fill, 3 trades a week, or future profit.
