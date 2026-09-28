# ADT "orb" (Opening Range Breakout): end-to-end map and replacement integration points

Repo: `/Users/mo/AutonomousDayTrader` at `c8194eb` (main). Read-only survey done 2026-09-28 ~13:30 ET.
All paths are relative to the repo root. `main.py` means `backend/app/main.py`.

## 0. Headlines (read these first)

1. **The binding size cap is $12,500, not $25,000.** `DynamicAdaptationEngine` is built without `max_alloc_pct`, so it uses the default 0.25 of equity (`strategies/adaptation.py:112`, applied in `calculate_position_size` `:77-80`). The risk engine's $25k (`core/risk.py:380`) and the account's $25k (`core/account.py:166-168`) are backstops. MEMORY.md:254 and :345 confirm that live trades sized to about $12.3k-$12.5k.
2. **The daily loss limit is lower than $1,500 at today's equity.** `daily_loss_limit = min(1500, 2.5% of session-start equity)` (`main.py:421-423`). It is reapplied at restore (`:856`), at the session boundary (`:1387`) and on reset (`:2845`). At about $49.6k equity that is about $1,240. Drawdown is measured from session-start equity, not from the day's peak (`core/risk.py:110`).
3. **ORB stops and targets live only inside the bot.** No stop order rests at Alpaca. A stop is triggered locally by a bar or quote and then sent as a market order. A target is sent as a limit order that waits 4 s and cancels whatever did not fill. If the bot is down, the position has no protection (MEMORY.md:60 "Accepted, not fixed").
4. **The tri-engine is the only path that rests real OCO orders at Alpaca**, and it is hard-wired to TSLA and CDE in about 10 places. A port can copy its pattern, but it cannot just plug into it.
5. **Checkpoint restore raises if the set of strategy ids changes**, and it decodes `SymbolORBState` by its class path. Keep the id `"orb"` or add an explicit migration rule, and deploy after the close.
6. **ORB also scans SPY, QQQ and TSLA** (everything in `WATCHLIST_SYMBOLS`). If ORB takes TSLA before the tri-engine's entry, the tri-engine skips its day with `SYMBOL_ALREADY_COMMITTED` (`core/tri_execution.py:339-340`).
7. **Live today** (GET `/api/decisions?strategy=orb`): 4 signals, 2 orders (NVDA SELL 54, GOOGL SELL 36), 2 blocked by MARKET_FILTER (AAPL SELL, AMZN BUY in a NEUTRAL market with RVOL < 2.2). At 13:30 ET Alpaca held only TSLA -37 (tri-engine), with no mismatch.

---

## 1. `backend/app/strategies/orb.py` (275 lines)

| Item | Value | Where |
|---|---|---|
| Class, id, name | `OpeningRangeBreakoutStrategy`, `"orb"`, "Opening Range Breakout" | `orb.py:78-93` |
| Instantiation | one global `orb_strategy = OpeningRangeBreakoutStrategy()` with defaults, no kwargs | `main.py:94`, listed first in `strategies` at `main.py:136-144` |
| Range | `range_minutes=5`: bars stamped 09:30 to 09:34 ET (`open_bell <= t < 09:35`) | `orb.py:85, 149-158` |
| Range levels | high = max(highs), low = min(lows), midpoint = (H+L)/2 | `orb.py:177-180` |
| Pre-market | bars before 09:30 are ignored entirely | `orb.py:139-141` |
| Late symbol | no opening bars and first bar after 09:45: range marked established and `breakout_fired=True`, so no trade that day | `orb.py:161-167` |
| Missed open (first bar 09:35 to 09:45) | range seeded from that one bar, no signal on that bar | `orb.py:168-176` |
| Signal window | bars before 11:30 ET (`cutoff_time`), one breakout per symbol per day (`breakout_fired`) | `orb.py:137, 182-184, 243` |
| RVOL | bar volume / mean volume of the prior 20 regular-session bars (excluding the current bar). Fallback `baseline_volume=100000` (`set_baseline_volume` is never called anywhere) | `orb.py:186-194` |
| Trigger | `evaluate_orb_signal`: needs RVOL >= `min_rvol` 1.80. BUY if close > range high and CLV >= 0.65. SELL if close < range low and CLV <= 0.35. CLV = (C-L)/(H-L) of the breakout bar | `orb.py:17-62` |
| Range cap | reject if bar range > 2.2 x ATR(14), where ATR comes from prior bars only | `orb.py:208-214` |
| Extension cap | reject if close is more than 1.0 x ATR beyond the range edge | `orb.py:215-218` |
| Stop | range midpoint. If the distance is under $0.05, use max(0.10, ATR). Then `resolve_stop` widens it to the 0.4% floor. A stop wider than 4% is left alone so the risk engine rejects it | `orb.py:220-230`, `strategies/base.py:218-236` |
| Targets on the signal | T1 = 0.8R, T2 = 1.8R from the signal close. **Ignored downstream**: the bracket re-anchors to the fill (section 2.5) | `orb.py:232-241` |
| Order type | MARKET; confidence = min(1, 0.6 + 0.1 x RVOL); `sig.rvol` set (the market filter reads it) | `orb.py:244-257` |
| Research features | range, rvol, clv, atr, caps, stop floor flags via `attach_features` | `orb.py:258-274` |
| `notify_signal_rejected(sym)` | resets `breakout_fired=False` so the symbol can fire again later that morning | `orb.py:119-123` |
| Daily reset | `reset_daily_stats` clears `symbol_states` | `orb.py:111-113` |
| Symbol list | none of its own. ORB runs on every bar whose symbol is in `settings.WATCHLIST_SYMBOLS` (`main.py:2192`): SPY, QQQ, AAPL, NVDA, TSLA, AMD, MSFT, AMZN, META, GOOGL, PLTR, COIN (`config.py:76-79`) | |

Where `notify_signal_rejected` is called (all in `main.py`): arbitration lost `:2209-2210`, target error `:1821-1822`, admission denied `:1839-1840`, risk denied `:1869-1870`, zero qty `:1875-1876`, engine reject `:1926-1927`, dead entry (Alpaca refused or cancelled, no fill) `:1239-1240`. Paths that do **not** reset the lock: `BAD_PRICE` (`:1764-1766`) and `DUPLICATE` (`:1771-1788`).

Per-symbol state is the dataclass `SymbolORBState` (`orb.py:65-75`). It is checkpointed by class path (section 5).

---

## 2. Signal path for an orb signal

### 2.1 `handle_bar_event` (`main.py:2107-2266`)
1. Durable event key (`:2114`), session boundary check (`:2121`), tri/OR15 controllers see the bar first (`:2122-2129`).
2. Price cache, feed mark, `market_filter.on_bar` for SPY/QQQ (`:2132-2133`), `regime_feed.on_bar` (`:2134-2135`), `market_history` capped at 120 bars (`:2136-2145`), `adaptation_engine.update_clock` (`:2146`).
3. Flattening check (`:2185-2187`).
4. Strategies run `on_bar` only if the symbol is in `WATCHLIST_SYMBOLS`, skipping `FIXED_IDS` (`:2189-2201`).
5. `adaptation_engine.arbitrate_signals`: one signal per symbol per bar. Priority is news 40 > orb 30 > vwap 20 > mean_reversion 10, then confidence (`adaptation.py:100-105, 260-274`). Losers are logged `ARBITRATION_LOST` (`main.py:2203-2212`).
6. Research excursions (`:2216-2218`), then `engine.process_bar` matches working orders (local stops and targets) (`:2220-2231`).
7. Risk state and breaker (`:2234-2242`), ATR trail update (`:2245-2261`), checkpoint (`:2263`), tri/OR15 tick (`:2264-2265`).

### 2.2 `execute_strategy_signal` (`main.py:1730-1929`)
1. "EXIT"/"CONTRADICTION" in the reason means a news exit, not used by ORB (`:1736-1759`).
2. `BAD_PRICE` (`:1764`). `DUPLICATE`: an active bracket or a working entry already exists on the symbol (`:1770-1788`).
3. `adapted_stop = adaptation_engine.calculate_adapted_stop(signal)` (`:1810`).
4. `_intraday_target_overrides` returns (None, None, None) for orb, so the bracket uses fill-anchored R targets (`:1682-1692`).
5. Committed portfolio for the INTRADAY arm: filled positions plus working entries plus PENDING_ENTRY brackets (`:224-295`, called `:1824`).
6. `evaluate_signal_admission` (`:1828-1841`). Outcome codes come from `classify_adaptation_reason` (`core/decisions.py:44-53`).
7. `risk_engine.evaluate_order_request` (`:1846-1877`). qty = min(admission qty, authorized qty).
8. `engine.create_order` (MARKET, `stop_price=adapted_stop`, `estimated_price=signal close`) and `submit_order` (`:1882-1892`). `submit_order` runs `account.can_afford` then `pre_trade_risk_validator` (`core/engine.py:427-466`, `main.py:439-587`).
9. If ACCEPTED: record `SUBMITTED`, `bracket_manager.create_bracket(bracket_id="brk_<order>", target_1_r=0.8, target_2_r=1.8, runner_policy="TARGET")` (`:1893-1916`), `entry_order_to_bracket` link (`:1917`), research `open_bracket` (`:1920-1921`). The entry then fills **on the same bar** through `engine.process_bar` (`:1922-1924`). With Alpaca attached, that is a real market order (section 6).

### 2.3 `evaluate_signal_admission` (`strategies/adaptation.py:276-324`)
- **Market filter** (`core/market_filter.py:259-335`). Trend comes from SPY and QQQ anchored VWAP (from 09:30) plus EMA 9/21, with a 0.03% deadband (`:102-114, 189-243`). In the first 3 bars it is green or red from the open (`:214-224`). It is UNKNOWN if either index has no bars or is older than 120 s, which fails closed (`:192-212, 300-302`).
  - NEUTRAL: orb is allowed only if `signal.rvol >= 2.20` (`:308-311`). ORB's own floor is 1.80, so a NEUTRAL tape silently needs 2.2.
  - BULLISH: orb longs only. BEARISH: orb shorts only (`:317-321`).
- **Phase gate**: orb is allowed only in OPEN_VOLATILITY_FLUSH (09:30-10:00) and TREND_CONTINUATION (10:00-11:30) (`adaptation.py:42-58, 194-198`). This duplicates ORB's own 11:30 cutoff.
- **Concurrency**: reject if the symbol is not already committed and committed count >= 3 (`:310-312`).
- **Sizing** (`:241-258, 61-80`): risk $ = equity x 1% x VIX sizing multiplier x (0.5 in MIDDAY_CHOP, never reached by orb); shares = min(risk$/stop distance, **0.25 x equity / price**).
- VIX regimes (`models/events.py:31-48`): VIX < 15 LOW (size 1.20, stop 0.85); < 25 NORMAL (1.00, 1.00); < 35 ELEVATED (0.70, 1.40); otherwise CRISIS (0.35, 2.00). A stale or fallback VIX clamps sizing to at most 1.00 (`adaptation.py:136-153`, `main.py:2404-2415`).
- **`calculate_adapted_stop`** (`adaptation.py:218-239`): stop distance x VIX stop multiplier, re-floored to 0.4% by `resolve_stop`. A raw stop wider than 4% is never tightened. `stop_is_final=True` bypasses this; ORB does not set it.

### 2.4 Risk engine (`core/risk.py`, intraday branch `:271-409`)
Config built at `main.py:82-88` from `config.py:143-148`: starting equity, $1,500 (then overridden by `daily_loss_limit`), 1% risk, `max_position_equity_pct = 25000/INITIAL_CASH` (= 0.5 at the default $50k), max 3 positions.
Checks in order:
- breaker halted or drawdown >= limit (`:175-185`)
- remaining loss budget (`:188-198`)
- 15:45 entry lockout (`:275-284`)
- concurrency 3 (`:287-296`)
- at most 2 positions per sector, with a hard-coded sector map where Index is exempt (`:70-87, 298-321`)
- stop geometry (`:324-338`)
- **stop distance must be 0.4% to 4.0%**, exempt only for `{"tsla_or15_retest","tsla_asymmetric_dual","cde_asymmetric_dual"}` (`:340-363`)
- risk $ = min($1,000, equity x min(2%, 1% x VIX multiplier), remaining budget), in WARNING mode flat 1% (`:369-376`)
- qty = min(risk qty, notional qty net of existing exposure, buying-power qty) (`:378-388`). Tri ids skip the notional cap (`:383-384`).

The breaker is also evaluated on every quote (`main.py:2339-2347`). Tripping it liquidates intraday positions (`main.py:1257-1285`).

### 2.5 Bracket (`core/bracket.py`)
- `create_bracket` (`:115-212`): T1 = entry ± 0.8R, T2 = ± 1.8R, T1 qty = floor(qty/2), T2 qty = the rest. Synthetic child ids `stop_/t1_/t2_<bracket>`. `runner_policy="TARGET"` gives a T2 limit; `"TRAIL_ONLY"` gives none (used by vwap v2).
- `activate_bracket_on_fill` (`:214-326`): **re-anchors R and both targets to the actual fill price**, sizes to the filled qty, and emits STOP + LIMIT T1 (+ LIMIT T2). `_apply_bracket_directive` (`main.py:952-1020`) turns these into engine orders.
- T1 fill (`:409-452`): status TARGET_1_HIT, stop moves to breakeven + max($0.04, 0.05% of entry) (`:106-113`), stop qty shrinks.
- ATR trail (`:491-554`): **only after TARGET_1_HIT**. Peak minus 1.5 x ATR, where ATR is the mean true range of the last 14 bars from `market_history` (`main.py:889-914`). Monotonic. Applied by editing `engine.working_orders[oid].stop_price` (`main.py:2253-2257`).
- A stop fill cancels the targets (`:357-407`). Partial stop fills shrink the targets.

### 2.6 How stops execute: locally, then a market order
- The stop and targets are local `Order`s in `engine.working_orders`. They are matched on **every quote** (`engine.process_quote`, `core/engine.py:551-606`: STOP SELL triggers when bid <= stop) and on every bar (`process_bar`, `:608-676`: STOP SELL triggers when low <= stop). Stops are evaluated before targets.
- On a trigger, `_execute_fill` calls `_broker_execute` (`core/engine.py:678-716, 220-299`). It sends `broker.submit_and_settle(symbol, side, qty, cid, limit)`. STOP children have no limit, so they go out as a **market** order. LIMIT targets go out as a limit order at the target price. It then waits up to 4 s and cancels the remainder (`core/broker.py:282-295`).
- Exits are capped at `broker.position_qty(symbol)` (`engine.py:257-267`). Client id `adt-<order>-<attempt>` (`:269-276`). `_broker_gate` blocks real orders outside 09:30 to the close, and blocks entries on a mismatch (`main.py:426-436`).
- Consequence: a gap through the stop fills at market after the bot sees the quote. Each real fill blocks the event loop up to about 6 s of synchronous HTTP (MEMORY.md:60).

### 2.7 End of day (`core/flattening.py:73-177`, `main.py:2434-2548`)
- 15:45 entry lockout. 15:50 purge of non-protective working orders (stops kept). **15:55 market liquidation** of every intraday position except tri, OR15 and swing (`main.py:2461-2485`, `_flatten_symbol` `:1244-1254`). 15:58 audit sweep.
- The runtime clock loop drives this even without bars (`main.py:2707-2746`).
- The schedule is fixed in ET. The tri-engine handles early closes itself (`session_bounds - 5 min`). The generic flattener has no early-close logic.
- Session boundary: leftover intraday positions are liquidated, brackets cleared, `reset_daily_stats` called on every strategy (`main.py:1287-1428`).

---

## 3. Tri-engine (`strategies/tri_engine.py` + `core/tri_execution.py`): broker-side OCO path

**Scope:** `AsymmetricDualStrategy("TSLA")` and `("CDE")` (`main.py:129`). The constructor raises for any other symbol (`tri_engine.py:35-37`). Ids `tsla_asymmetric_dual` and `cde_asymmetric_dual`, `TRI_IDS`, `FIXED_IDS = TRI_IDS | {"tsla_or15_retest"}` (`tri_engine.py:22-25`). VERSION and SOURCE_SHA256 are checked at restore (`:26-28`).

**Signal:**
- 15-minute OR from all fifteen 09:30-09:44 bars. A missing bar skips the day (`:170-184`). Each stock bar is paired with the same-minute QQQ bar and a close-weighted QQQ VWAP (`:187-205`).
- Long: a prior breakout, a retest near the OR high, low >= mid, a green candle, close >= OR high, QQQ >= VWAP (`:213-220`).
- Short: close < OR low before the cutoff with QQQ < VWAP (`:221-223`).
- One signal per day. The entry is due at T+2 minutes (`:234-247`). Stop: long uses OR low, short uses OR mid (`:95-97`).
- The strategy is **not** run by the generic loop (`main.py:2149, 2195` skip `FIXED_IDS`). `tri_controller.on_bar` feeds it (`tri_execution.py:215-231`).

**Execution (`TriExecutionController`):**
- Network work runs in a 4-thread pool, keyed and rate-limited by `_io` (`:119-153`). State changes happen on the event loop. `tick()` runs on every bar, every quote and every 1 s clock tick (`main.py:2265, 2358, 2736`; `:266-289`).
- **Entry intent** (`_prepare_entry` `:330-400`):
  - gates: session and cutoff, operator pause, symbol already committed, broker and SIP verified
  - size: min(0.75% x session equity, 1.5% combined cap minus open risk) / risk, bounded by buying power, with a binary search on `can_afford` (`:354-367`)
  - creates a local MARKET order with `execution_policy = strategy_id`, cid `adt-tri-{SYM}-{YYYY-MM-DD}-entry`
  - `native[order.id]` holds {cid, id, role, status, booked_qty, booked_notional, terminal}
  - **checkpoints before any POST**. If that checkpoint fails, it cancels and skips (`:381-399`)
- **Entry poll** (`_entry_poll` `:402-467`):
  - looks up by id or cid. When unknown, it checks asset tradable and shortable/ETB, the entry window, quote freshness and `_broker_gate`, records `broker_qty_before`, then calls `broker.submit` (market).
  - cancels on the grace deadline or a partial fill
  - if the id is missing but Alpaca's share count changed, it sets `broker_mismatch`
  - `_book` books cumulative fill deltas into the ledger (`:469-492`)
- **Tranches** (`_open_tranches` `:516-577`): TSLA is split floor/ceil into (1.5R, 180 min) and (2R, 240 min). CDE is one (2R, 180 min). `exit_due = min(entry + hold, close - 5 min)`. The fill-risk tolerance is 1.5x the budget (`:34, 554-560`).
- **Protection** (`_manage_tranche` `:599-675`):
  - first `find_by_client_id(protection_cid)`. If absent, `broker.submit_oco(sym, remaining, cid "adt-tri-{SYM}-{date}-t{n}", stop, target, side, time_in_force="gtc")`. Prices are rounded away from the fill (`broker_price` `:88-91`).
  - legs parsed from `group["legs"]`, bound to local STOP/LIMIT orders with `native` ids
  - `protection_confirmed` is set, then checkpoint `TRI_PROTECTION_BOUND`
  - after that, the nested order is polled every 5 s (1 s while exiting), and fills on either leg are booked
  - a hard OCO reject becomes `PROTECTION_REJECTED`, which forces an exit
- **Time exit / cancel-replace** (`:312-322, 652-700`): at `exit_due`, `exit_reason = TIME_LIMIT/FORCED_FLAT`. The poll sends `request_cancel(target leg id)` (cancelling the OCO parent cancels both legs) until both legs are terminal. Then `_close_tranche` sends a market close with cid `{protection_cid}-x{k}`, only after checking that the Alpaca position covers the qty. **There is no PATCH or replace**: the path is cancel, wait until terminal, then market.
- **Completion** (`_maybe_complete` `:732-767`): writes a trade row into `pending_trade_records` and research `trades` directly. It **bypasses `research_tracker`**, so there is no MFE/MAE.
- **Restart reconciliation:**
  - the checkpoint holds the strategy `__dict__` (tranches, native ids)
  - `validate_tri_state` (`tri_execution.py:41-85`) re-checks phases, fixed levels, deadlines, native ids and that tranche qty equals the account position. Any drift raises `PersistenceError`.
  - every POST is re-found by cid before resending (`:409, 608-610, 689`)
  - an active tri trade at the session boundary requests `SESSION_RECOVERY` exits (`main.py:1303-1305`)
- **Other hard-wiring a port must mirror:**
  - `engine._broker_execute` refuses `TRI_IDS` orders (`engine.py:224-225`)
  - `process_bar` and `process_quote` skip `FIXED_IDS` (`:566, 625`)
  - concentration cap off (`:450`), risk exemptions (`risk.py:342, 383`)
  - validator ownership/reservation (`main.py:460-506`)
  - flatten, breaker and manual-flatten skip `tri_controller.owns` (`main.py:1268, 2469, 2503, 3472, 3538, 3617`)
  - fills routed to `tri_controller.on_fill` (`main.py:1159-1161`)
  - validate_tri_state hard-codes the hold minutes and target R per symbol (`:63-66`)

---

## 4. Data available inside ADT

| Feed | What | Where |
|---|---|---|
| 1-min bars | AlpacaRelay WS `bars`, `BarEvent`. No bar in a minute with no trades | `ingestion/stock_ws.py`, `main.py:2107` |
| Quotes (NBBO) | every quote goes to `tick_tape.on_quote`, then `engine.process_quote` | `main.py:2302-2359` |
| Trades (SIP prints) | `tick_tape.on_trade` and the latest price | `main.py:2749-2762` |
| Tick tape | per-second buckets with 1 h retention (`keep_seconds=3600`). `delta(sym,t0,t1)` (buy/sell aggressor volume, delta, ratio, classified share, completeness), `rolling_delta`, `velocity(window_s=60)` (price change per second), `session_delta`, `spread_stats`, `book_imbalance(window_s=30)` (NBBO top-of-book only; Alpaca has no depth). Every query returns `complete` and must be refused if incomplete. Global `TickTape()` at `main.py:111` | `core/tick_tape.py:1-25, 145, 482-704` |
| Regime feed | bars only for `REGIME_SYMBOLS` = XLK, XLC, XLY, XLF, UUP, SHY, IEF (`config.py:159-162`). Sector map AAPL/NVDA/AMD/MSFT/PLTR→XLK, META/GOOGL→XLC, AMZN→XLY, COIN→XLF. `direction()`, `wallclock_return()`, `evaluate()` | `core/regime_feed.py:33-38, 119-207`, `main.py:114` |
| Market filter | SPY/QQQ anchored VWAP and EMA trend, `get_current_trend(asof)`. Rebuilt from REST at startup | `core/market_filter.py`, `main.py:2605-2621` |
| VIX | REST poll of relay `/vix` every 5 s, stale after 300 s | `ingestion/vix_client.py`, `config.py:138-140`, `main.py:2393-2431` |
| News | WS subscribes to `news: ["*"]` (all symbols). Kept in `recent_news` (last 50) | `ingestion/news_ws.py:125`, `main.py:2362-2390` |
| Volume profiles | prior 5 sessions per watchlist symbol, from relay REST history | `core/volume_profile.py`, `main.py:117-119, 1960-2017` |
| Macro calendar | FOMC/CPI blackouts (used by vwap only) | `core/macro_calendar.py` |
| Relay REST bars | `_fetch_relay_bars(symbol,start,end)` for backfills | `main.py:1932-1958` |

**Streamed universe:** `StockWebSocketClient` subscribes bars+quotes+trades for WATCHLIST ∪ SWING_SYMBOLS (LRCX, KLAC, MU, AMD, GS) ∪ {QQQ benchmark, TSLA, CDE, QQQ}, plus bars only for REGIME_SYMBOLS (`stock_ws.py:41-42, 193-206`). `WATCHLIST_SYMBOLS` is an env-configurable pydantic list (`config.py:76-79`), read at import time.

**Runtime changes:** `stock_ws_client.subscribe(bars=, quotes=, trades=)` sends a live subscribe and adds the symbols to `self.symbols`, so they survive a reconnect (`stock_ws.py:103-123`). Nothing calls it today. But the strategy loop only runs on symbols in `settings.WATCHLIST_SYMBOLS` (`main.py:2192`), and sectors and profiles are keyed off the same list. So a new ORB universe needs either an env change plus a redeploy, or code that checks a mutable set. I did not verify whether the relay caps subscription counts. It is a separate service (AlpacaRelay, see the memory note "400s ANY unknown query param"). Treat that as an open question.

---

## 5. Checkpoint, research, decisions, cards, API

**Checkpoint/restore** (`core/runtime_state.py`, `main.py:696-886`):
- `capture` stores `strategies: {id: strategy.__dict__}` (`:125`). Values are encoded with class paths, and decoding only allows `backend.app.*` classes (`core/persistence.py:29, 40-130`). Dataclasses are rebuilt with `cls(**fields)`. **Renaming or removing `SymbolORBState`, or one of its init fields, breaks decoding of any checkpoint that still holds ORB symbol state.**
- **Strategy-set rule** (`runtime_state.py:229-239`): `saved_ids` must equal `deployed_ids`, except two whitelisted upgrades (legacy `{orb, vwap_pullback, news_momentum, mean_reversion}` plus OR15, plus the tri ids). Removing `"orb"`, adding a new id such as `orb_straddle`, or splitting ORB into per-symbol ids makes this raise `PersistenceError("Persisted strategy set does not match this deployment")`. Startup then fails when `PERSISTENCE_REQUIRED`. Fix: keep the `"orb"` id, or add a new `known_addition` clause in the same commit.
- Restore merges: constructor kwargs come from code, and everything else from the checkpoint overwrites the fresh instance (`:250-255`). Old attributes such as `symbol_states` will reappear on a new class that keeps the id `orb`.
- `validate_runtime_state` (`:297-392`) requires every active bracket to have a working local stop equal to the position size. A port that uses native OCO instead of `bracket_manager` must be exempted the way tri is (`:347-351`), or it fails validation.
- Safest deploy window: after 16:00. The session reset clears `symbol_states` (`orb.py:111-113`), and no orb position can exist then. Railway redeploys on push to main.

**Decision log** (`core/decisions.py`): `_record_decision(signal, outcome, detail, stages)` (`main.py:1583-1602`) writes `decision_log` (capped at 300 per session, with outcome texts `:19-41`) and a research `signals` row. `/api/decisions?limit=&strategy=` (`main.py:3207-3215`). It is checkpointed under `decisions`.

**Research** (`core/research_tracker.py`, `core/research.py`):
- rows `signals`, `trades`, `setups` go to SQLite next to the state DB (`main.py:605-613`)
- the tracker lifecycle is bracket-centric: `record_signal` `:231`, `open_bracket` `:259`, `on_activation` `:309`, `on_fill` `:366`, `on_bar` (MFE/MAE) `:401`, `note_stop_change` `:432`, `complete_bracket` `:507`
- `/api/research/{trades|signals|setups}` (`main.py:3230-3258`)
- a port that does not use `bracket_manager` gets no automatic MFE/MAE, the same as tri today

**Strategy cards** (`core/trading_windows.py`, `main.py:1605-1679`):
- generic `strategy_window()` uses `adaptation_engine.is_strategy_permitted` phases (`trading_windows.py:25-32, 77-86, 116-`), plus ORB-specific text: note `:37` and NEUTRAL text `:100, 202-203`
- fixed strategies supply their own `window()` (`tri_engine.py:293-328`)
- `/api/strategies` returns `_strategy_cards()` (`main.py:3184-3187`), and `/api/tri-engine` exists (`:3190-3196`)
- labels to update on a swap: `orb.py:83-84` name, `trading_windows.py:37, 100`, `market_filter.py:276-311` text, and the frontend (not surveyed)

---

## 6. Broker (`core/broker.py`, 298 lines)
- **Paper only**: raises for any base URL other than `https://paper-api.alpaca.markets` (`:25, 87-89`).
- `submit(symbol, side, qty, client_order_id, limit_price=None)`: **market or limit DAY only** (`:216-259`). A duplicate or lost POST is re-found by client id.
- `submit_and_settle` = submit, wait 4 s, `cancel_and_settle` (`:282-295`). This is how every generic ADT order executes. Nothing rests.
- `submit_oco(symbol, qty, cid, stop, target, side, time_in_force day|gtc)`: an exit OCO, limit parent plus stop leg (`:175-214`).
- `request_cancel` (non-blocking, `:166-173`), `cancel_and_settle`, `get_order(nested=true)`, `find_by_client_id`, `position_qty`, `get_positions`, `get_account`, `get_asset`, `sync`.
- **Not supported**: `order_class=bracket`, OTO, stop or stop-limit **entry** orders, trailing_stop, PATCH replace, fractional qty, extended hours. If ORBStraddle needs resting stop-entry orders on both sides of the range, that needs new broker code. I have not read ORBStraddle's code, so I don't know whether it does.
- **Reconciliation**: every 5 s, orders with broker ids are settled (late fills booked). Every 30 s, positions and equity are compared, and any difference sets `broker_state.mismatch`, which blocks all entries (`main.py:320-400`, `_broker_gate :426-436`, validator `:486-494`).

**Alpaca "one sell order per position"** (memory `reference_alpaca_one_sell_order_per_position.md`): shares are held per account. A second sell order beyond `qty_available` is refused with 403 `40310000`.
- Today ORB has **no conflict**, because nothing rests. The ADT book is one position per symbol (`account.positions` keyed by symbol). Duplicate-entry checks (`main.py:1770-1788`) plus arbitration stop two intraday strategies from holding the same symbol. Swing reservation blocks the overlap between intraday and swing (AMD is in both lists) (`main.py:195-220, 527-529`).
- **A ported ORB that rests an OCO at Alpaca creates the conflict inside ADT itself.** Every generic market exit would be refused while the OCO holds the shares: 15:55 AUTO_FLATTEN (`main.py:2476-2485`), 15:58 sweep, CIRCUIT_BREAKER (`:1257-1285`), NEWS_CONTRADICTION (`:1736-1758`), manual flatten, SESSION_BOUNDARY_LIQUIDATION. The exit cap in `_broker_execute` checks `position_qty`, not `qty_available` (`engine.py:259-267`).
- Tri avoids this because every one of those paths skips `tri_controller.owns(sym)` and calls `request_exit`, which cancels the OCO first and then closes at market. A port must do the same.
- Several OCOs on one symbol are fine as long as their quantities sum to at most the position (TSLA's two tranches). The OCO parent id changes on every replace, so always re-query rather than cache.

---

## 7. Tests
- **Layout**: `backend/tests/unit/*.py`, `backend/tests/*.py`, `backend/tests/stress/*.py`, `tests/e2e/*.py` (+ `tests/e2e/runner.py`). `pytest.ini` sets `pythonpath = .`. There is no conftest. With no `.env` and a clean shell, settings default to simulated broker, persistence off and no relay token.
- **ORB tests**:
  - `backend/tests/unit/test_strategies.py:133,166,205,223,236,455,583,609,635`
  - `test_empirical_stress_m2.py:81-228`, `test_empirical_stress_m2_2.py:226`
  - `test_remediation_r3.py:395` (late-arrival gating)
  - `test_research_recording.py:254` (features)
  - `test_alpaca_broker.py:284` (cancelled entry releases the lock)
  - `test_adaptation.py`, `test_market_filter.py`, `test_bracket.py`, `test_trailing_atr.py`
  - stress: `test_challenger_causality_empirical.py:215`, `test_challenger_r3_remediation.py:608`, `test_challenger_r6_remediation.py:238,274`, `test_challenger_r4_anti_hallucination.py:154,175`
  - e2e: `tests/e2e/test_tier1_features.py:533-569,1086`, `test_tier4_scenarios.py:49`, `test_challenger_bracket_2.py:88-694`
  - Tri tests: `test_tri_signals.py`, `test_tri_broker_lifecycle.py`, `test_tri_e2e.py`
  - Checkpoint: `test_persistence.py` (incl. `test_restore_from_older_checkpoint_keeps_current_strategy_settings`, MEMORY.md:80)
- **Run** (from the repo root):
  - `python -m pytest -q --ignore-glob='*test_microstructure_*' backend/tests`
  - `python -m pytest -q tests/e2e --ignore=tests/e2e/test_challenger_mobile.py`. That file starts the frontend with `npm run start`.
  - The `test_microstructure_*` files are untracked output of the other session; exclude them.
- **Results today**: 874 passed (backend) and 308 passed (e2e without the mobile suite), no failures, no ports left listening, no files changed. MEMORY.md:483-484 mentions "1 pre-existing date-bound swing test fails on HEAD". **It did not fail today.** Either it was fixed, or the date condition does not trigger on 2026-09-28. I did not track it down.

---

## 8. Account-level limits (as coded)

| Limit | Value | Where |
|---|---|---|
| Max concurrent intraday positions | 3 (checked in adaptation and risk; swing has its own 2) | `config.py:148`, `adaptation.py:310-312`, `risk.py:287-296` |
| Daily loss | min($1,500, 2.5% of session-start equity), about $1,240 at $49.6k. Breaker halts and liquidates intraday. Checked on bars and quotes | `main.py:421-423, 856, 1387, 2234-2242, 2339-2347`, `risk.py:93-129` |
| Per-trade risk | 1% of equity x VIX multiplier (0.35 to 1.20), capped at 2% and $1,000 and the remaining loss budget | `config.py:146`, `adaptation.py:74`, `risk.py:369-376` |
| Single position notional | **effective $12,500** (0.25 x equity, adaptation); risk engine 0.5 x equity; account min($50k, $25k) | `adaptation.py:112, 77-80`; `risk.py:380`; `account.py:166-168` |
| Sector | at most 2 per sector (Index exempt) | `risk.py:48, 298-321` |
| Stop distance | 0.4% to 4.0% (tri and OR15 exempt) | `risk.py:49-50, 340-363`, `base.py:218` |
| Tri combined risk | 1.5% of session-start equity, 0.75% each | `tri_execution.py:204-213, 355` |
| Buying power | 4:1 DTBP | `config.py:144`, `account.py` |

---

## 9. Checklist of what a replacement ORB must plug into
1. Instance and registration: `main.py:94, 136-144` (`strategies`, `strategy_map`). Id rule in `runtime_state.py:229-239`.
2. Where it runs: the generic `on_bar` loop (`main.py:2192-2212`, with arbitration), or a dedicated controller like tri (`main.py:2122-2129, 2265, 2320, 2358, 2736`) plus a `FIXED_IDS`-style skip.
3. Admission: the market filter orb rules (`market_filter.py:308-321`), phase gate (`adaptation.py:194-198`), priority 30 (`adaptation.py:102`). Remove or keep deliberately.
4. Stop/size: the 0.4% floor and 4% ceiling, VIX stop scaling (set `stop_is_final` to bypass), the $12.5k cap, sector limit.
5. Exits: either the local bracket (targets re-anchored to the fill, T1/T2 at 0.8R/1.8R, BE plus trail after T1), or native OCO. With native OCO, add ownership skips to every generic exit path (section 6) and to `validate_runtime_state`.
6. Fills routing: `_reconcile_fills` (`main.py:1152-1221`), trade record (`_completed_trade_record` `:1023-1074` or its own), research.
7. Restart: durable client ids before POST, re-find by cid, state validation.
8. Rejection hook: `notify_signal_rejected` call sites (section 1).
9. UI and labels: name, cards, `trading_windows.py` texts, `/api/strategies`, the frontend (not surveyed).
10. Conflicts: TSLA (tri), AMD (swing), SPY/QQQ (index instruments currently traded by orb).
