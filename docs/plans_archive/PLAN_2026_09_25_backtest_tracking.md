# Plan 2026-09-25: record what we need to backtest and tune the knobs

Status: BUILT 2026-09-25 (M1-M4 in commits 0e6fe3e, c7eaa3c, d1d0ca6). See MEMORY.md for attacks, verification and accepted limits. Offline scorer and strategy-internal candidate sampling remain future work.

## Goal

Once the bot has enough real Alpaca paper trades, the operator wants to look back
and decide knob changes (stop width, target R, volume thresholds, trend filter,
hours, VIX sizing). Today the saved data cannot answer those questions. This plan
adds the missing records. It does **not** change any trading decision.

## What is saved today (verified in code and on production 2026-09-25 16:51 ET)

- `completed_trades` row (`main.py:_completed_trade_record`): trade_id, session_date,
  symbol, side, strategy_id, opened_at, closed_at, quantity, avg_entry_price,
  avg_exit_price, realized_pnl, fees, exit_reason, fill_legs. Production has 31
  trades (vwap_pullback 20, orb 3, mean_reversion 2 in the latest 25).
- Decision log (`core/decisions.py`): strategy, symbol, side, price, outcome,
  200-char detail. Kept as the **last 300 only** inside the checkpoint, so older
  records are overwritten.
- Session summary: per-strategy counts and blocked_by_reason counts.

What is lost:
1. The bracket knows `initial_stop_price`, `r_distance`, targets, target R, but the
   trade row drops them when it is saved.
2. `peak_price_since_entry` only moves after target 1 is hit (trailing logic,
   `core/bracket.py:516`), so it is **not** a max-favourable-excursion. There is no
   max-adverse-excursion at all.
3. The original signal (planned entry, raw stop, raw targets, confidence, reason,
   rvol, volume_surge, catalyst_sentiment) is not saved with the trade.
4. Blocked signals disappear after 300 records and never carry their stop/targets,
   so they cannot be scored later.
5. No trade or signal says which knob values, code version, VIX level or market
   trend state it was made under.
6. Swing trades appear to go through `swing_panic_dip.py` exit paths, not
   `_record_completed_bracket`, so they may never reach `completed_trades`
   (production history shows no `swing_panic_dip` rows). **Verify before building.**

## The changes

### Part 1. Richer trade rows (highest value)

1. Add optional fields to `BracketOrder` (all default `None` or `{}` so older
   checkpoints restore cleanly):
   - `entry_context: Dict[str, Any] = {}` (the signal snapshot, Part 3)
   - `mfe_price`, `mae_price`: best and worst price since the entry fill
   - `mfe_at`, `mae_at`: timestamps
   - `signal_entry_price`, `signal_stop_price`, `adapted_stop_price`,
     `requested_qty`
2. Update MFE/MAE on **every** bar for the bracket symbol while status is ACTIVE or
   TARGET_1_HIT, using bar high/low, in the same bar handler that already manages
   brackets. Separate from `peak_price_since_entry`; do not touch trailing logic.
   Seed both with the average entry fill price on the entry fill.
   Known limit: 1-minute bars, so intrabar order of high vs low is unknown on the
   entry bar and the exit bar. Record `mfe_mae_source: "1m_bars"`.
3. Extend `_completed_trade_record` with:
   - `initial_stop_price`, `r_distance` (per share, from the bracket at fill),
     `target_1_price`, `target_2_price`, `target_1_r`, `target_2_r`
   - `signal_entry_price`, `entry_slippage` (fill minus signal, signed so positive
     means worse), `entry_slippage_r`
   - `mfe_price`, `mae_price`, `mfe_r`, `mae_r`, `mfe_at`, `mae_at`
   - `realized_r` = realized_pnl / (r_distance * quantity)
   - `hold_minutes`, `exit_legs` (each exit leg with its reason: stop, T1, T2,
     trail, flatten, news, manual)
   - `requested_qty`, `filled_qty`
   - `entry_context` (Part 3)
   - `record_version: 2`
   Old rows keep `record_version` missing (= 1). Nothing rewrites old rows.

### Part 2. Permanent signal table

1. New SQLite table `signal_events`, append-only, created with
   `CREATE TABLE IF NOT EXISTS` (no migration of old tables, schema_version stays 2
   unless the store requires a bump; to be checked):
   `signal_id TEXT PRIMARY KEY, session_date TEXT, created_at TEXT, strategy_id,
   symbol, outcome, payload TEXT`.
2. Every `_record_decision` call also appends a full row to an in-memory
   `pending_signal_records` dict, flushed inside the same `save_checkpoint`
   transaction as trades (same pattern as `pending_trade_records`), and removed
   after a successful commit.
   Payload: time, strategy, symbol, side, order_type, outcome, detail, signal
   entry/stop/T1/T2, adapted stop (when computed), qty requested and authorised
   (when computed), confidence, reason, rvol, volume_surge, catalyst_sentiment,
   `entry_context`, and `bracket_id` when SUBMITTED.
3. `signal_id` = strategy + symbol + signal timestamp + outcome + a sequence, so a
   replayed bar after a crash does not double-insert (INSERT OR IGNORE).
4. The 300-record in-memory log stays as is for the dashboard.
5. Offline only (not in the bot): `scripts/score_signals.py` pulls 1-minute SIP
   bars from the relay `/data` proxy and scores each blocked signal as if it had
   been taken with its planned stop/targets (first touch wins, same-bar
   stop-and-target counted as a loss, time exit at the strategy's hour end). This
   is what lets us judge filters. Build later, when there is data.

### Part 3. Settings and market snapshot (`entry_context`)

Built once per signal by a new `_build_entry_context(signal, bar)` in `main.py`,
wrapped in try/except (a failure records `{"error": "..."}` and trading goes on):
- `code_revision`: `RAILWAY_GIT_COMMIT_SHA` env var, else `"unknown"`
- `strategy_params`: new `BaseStrategy.tuning_params()` returning the constructor
  knobs (ORB: range minutes, min_rvol, min_clv, target R's; VWAP: EMA periods,
  cooldown, target R's; Mean Reversion: min_rr_ratio, volume multiple; News:
  sentiment threshold, volume multiple, TTL). Explicit per-strategy list, not a
  `__dict__` dump (runtime memory like `symbol_states` must not leak in).
- `adaptation`: VIX value, VIX regime, VIX stale flag, sizing multiplier, current
  market phase
- `market_filter`: SPY/QQQ trend label as the filter saw it
- `risk`: account equity, day P&L so far, open intraday positions count, breaker
  state
- `time`: minutes since 09:30 ET, weekday
- `symbol`: bar close, bar volume, ATR used by the adapted stop (if available)
- `broker_mode`: simulated or alpaca_paper (so replay/test rows are never mixed
  with real ones)

### Part 4. Swing and TSLA OR15 (verify, then match)

- Confirm whether swing round trips reach `completed_trades`. If not, add a swing
  trade record at the final exit fill with the same core fields (entry fill, exit
  fill, initial stop, ATR, MFE/MAE from daily bars, exit reason, entry_context
  from the close scan). Separate commit.
- TSLA OR15 already goes through brackets; its trade row just gains the Part 1
  fields. Its own `session_record()` stays unchanged.

### Part 5. Getting the data out

- `GET /api/research/trades?since=YYYY-MM-DD` and
  `GET /api/research/signals?since=YYYY-MM-DD`, read-only, paged, same auth as the
  other APIs.
- `scripts/export_research.py` pulls both from production into CSV/Parquet under
  a gitignored `research_data/`.

## Safety rules

- No trading decision changes. Every new line in the hot path is wrapped so a
  recording failure logs and continues. A test proves an exception in context
  building still submits the order.
- New BracketOrder fields must have defaults (lesson from the 09-24 restore bug).
  Test: restore a checkpoint written by the current code, trade closes, row saved.
- Signal rows live in their own table, not in the checkpoint payload, so the
  checkpoint does not grow per signal.
- `broker_mode` and `aggregate_only` let analysis drop simulator and legacy rows.
- Deploy only while the book is flat and outside market hours (push = redeploy).

## Tests

- Unit: trade row contains every new field for a long and a short; `realized_r`
  sign correct; MFE/MAE for long vs short; MFE/MAE ignore bars before the entry fill.
- Unit: every `_record_decision` outcome writes one signal row; replaying the same
  bar does not duplicate; failure in `_build_entry_context` does not block entry.
- Restore: old checkpoint (no new bracket fields) restores, closes a trade, saves
  a v2 row with the new fields as null where unknown.
- Mutation check: stop updating MAE on bars and confirm a test fails.
- Full suite + Monday replay + E2E runner as usual; replay rows must show
  `broker_mode: simulated`.

## Rejected

- Reconstructing MFE/MAE later from relay bars only: possible, but the bot already
  sees every bar, and live recording avoids timestamp alignment traps we hit on
  ORBStraddle. The offline scorer is still needed for blocked signals.
- Logging every strategy-internal "no setup" check per bar: very high volume;
  deferred (same as the existing "no setup funnel" item).
- Putting stops at Alpaca to measure slippage on stops: out of scope.

## Honest limit

With about 1 to 3 trades a day, a knob comparison needs months of data to mean
anything. This plan makes the data exist; it does not make it enough.

---

## Codex attack, 2026-09-25 (read-only, gpt-6-astra xhigh, ~140k tokens)

Verdict: **revise before building.** 2 P0, 13 P1, 1 P2. Repo untouched by the
review (plan md5 and git status identical before/after). Both P0s spot-checked
true in code: a failed checkpoint locks out entries and cancels opening orders
(`main.py:648`); production calls `execute_strategy_signal(sig)` with no bar
(`main.py:1794`).

| # | Sev | Finding | Plan change |
|---|-----|---------|-------------|
| 1 | P0 | Putting signal rows in the checkpoint transaction means a research write failure or slow SQLite locks out trading. | Research rows go to their **own** table written **outside** `save_checkpoint`, best-effort, bounded queue, counted gaps (`research_dropped` on /health). Never flips `persistence_healthy`. |
| 2 | P0 | `_record_completed_bracket` marks recorded + updates stats before building the row; OR15 `completed()` runs after. An enrichment exception could lose the row or block OR15 closing. | Base row built exactly as today; enrichment in a separate try block that returns nulls. Test: forced enrichment failure leaves every state transition identical. |
| 3 | P1 | Normal Alpaca fills carry the bar/quote time, not broker time. | Record signal time, submit time, broker fill time and booking time separately, each with its source. |
| 4 | P1 | MFE/MAE on ACTIVE/TARGET_1_HIT misses exit bars and quote-driven exits; restarts could fake a full MFE. | Track by real exposure (first entry fill to last exit fill). Flag `coverage: full/partial`, carry last bar through restart, mark boundary bars ambiguous. |
| 5 | P1 | `r_distance * quantity` wrong with partial/late fills; `min(entry, exit)` hides imbalance. | Separate proposed / authorised / submitted / filled / exited qty; R from the filled entry and the stop actually in force; flag qty mismatch. |
| 6 | P1 | "Raw stop" is already floored at 0.4%; adaptation uses VIX, not ATR; ORB/News targets are replaced by fill-anchored ones. | Record structural stop, floored stop, adapted stop, fill risk; configured vs effective targets and fallback flags. |
| 7 | P1 | Exit reason cannot be rebuilt at close (breakeven and trail share one stop order; flatten reason dropped). | Capture exit intent + stop regime when each exit order is created; add to existing `fill_legs`; small stop-change history. |
| 8 | P1 | Passing a bar to `execute_strategy_signal` would trigger an extra `process_bar` and change execution. | Pass context in a new observation-only argument. Never pass `bar`. |
| 9 | P1 | Sequence + outcome is not a replay-safe id. | id from source bar/news event + strategy + symbol + side + ordinal; outcome is a separate field. |
| 10 | P1 | ORB and Mean Reversion reject most setups **inside** the strategy, so the table only sees late-stage blocks. VWAP starts cooldown even on blocked signals. | Claim narrowed to "admission filters" (trend, risk, sizing, arbitration). Internal filters tuned by full replays from saved bar data. |
| 11 | P1 | "First touch, exit at hour end" scores a different strategy (real one scales out, trails, flattens 15:55). | Offline scorer must reuse the real bracket logic; blocked-signal scores are diagnostics, not P&L. |
| 12 | P1 | Snapshot misses the values each strategy actually decides on. | Per-strategy snapshot of values used in the decision: ORB range/CLV/extension/ATR; VWAP bands/EMAs/volume ratio; MR z/RSI/volume/wick; News catalyst id, age, sentiment source; plus quote bid/ask and age. |
| 13 | P1 | Swing trades never reach `completed_trades` (confirmed). A final-exit hook is not enough. | Swing round-trip id + accumulator from scan through every fill. Separate milestone. |
| 14 | P1 | OR15 has its own path and already stores version/hash/risk. | Instrument its own path; don't call T+2 drift "slippage". Plan's "no trade has code version" was wrong for OR15. |
| 15 | P1 | `aggregate_only=False` on v1 rows, so it can't exclude legacy; Alpaca fees booked 0 = unknown. | Top-level `execution_mode`, `record_version`, `fees_known` on every row, independent of snapshot success. |
| 16 | P2 | Paged trade API already exists. | Add `since` to `/api/trades`; only add a signals export. Defer Parquet and the scorer. |

## Revised build order (not started)

1. **M1 Trade row v2 (intraday + OR15):** failure-isolated enrichment (P0 #2), qty
   ladder, stop ladder, effective targets, timestamps with source, provenance,
   exit intent on fill legs. Highest value, smallest risk.
2. **M2 MFE/MAE by exposure** with coverage flags and restart carry-over.
3. **M3 Research signal table** outside the checkpoint (P0 #1), stable ids,
   per-strategy decision snapshot, `/api/research/signals`.
4. **M4 Swing round trips** into the ledger.
5. Later: offline scorer reusing real bracket logic; strategy-internal candidate
   sampling.

Each milestone: tests incl. forced-failure tests, full suite + replay, Codex
re-attack of the diff, deploy only flat and after hours.
