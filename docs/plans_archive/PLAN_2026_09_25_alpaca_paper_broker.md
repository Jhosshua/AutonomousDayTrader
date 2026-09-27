# Plan 2026-09-25: execute on the real Alpaca paper account

## Goal
Starting Friday 2026-09-25 open, every trade the bot makes is a real order on
Alpaca paper account PA3CSVDZMMPY. Market data stays on AlpacaRelay (bars,
quotes, trades, news, VIX, REST backfill). The SQLite ledger, trade history,
session summaries, checkpoints and dashboard stay as they are.

Starting point (checked 00:42 ET): bot flat, equity $50,018.45, no working
orders. Alpaca: equity $50,018.45, cash $50,018.45, 0 positions, 0 open orders,
multiplier 4, shorting enabled.

## Design: the fill choke point
Every fill in the codebase goes through `ExecutionEngine._execute_fill`
(`process_quote`, `process_bar`, `_flatten_symbol`, and 4 direct calls in
`swing_panic_dip.py`). Trigger logic stays local (the bot decides when a stop,
target, entry or flatten happens, same as today). In broker mode,
`_execute_fill` sends a real order to Alpaca, waits for the result, and books
Alpaca's real `filled_qty` and `filled_avg_price` instead of the simulated
price.

Why not route native stop/target orders to Alpaca: Alpaca allows only one
open sell order per position (error 40310000), and brackets here have a stop
plus two targets working at once. Local triggers avoid that conflict.

### New module `backend/app/core/broker.py`
- `AlpacaBroker` (sync `httpx.Client`, base URL `https://paper-api.alpaca.markets`).
  Refuses to start if the URL is not the paper URL.
- `execute(order, qty, side, ref_price) -> (filled_qty, avg_price)`:
  - MARKET order, TIF day, `client_order_id = f"{order.id}-{attempt}"`.
    Entries that were LIMIT orders locally are sent as LIMIT at the local
    limit price (never pay more than the strategy's limit).
  - Poll `GET /v2/orders/{id}` every 0.2 s up to 6 s.
  - Filled -> return real qty/price. Partially filled at timeout -> cancel the
    rest, return what filled. Nothing filled -> cancel, raise `BrokerNoFill`.
  - HTTP reject (403/422, e.g. not shortable, insufficient qty) -> `BrokerReject`.
- Fee booked locally = 0 (Alpaca paper charges no regulatory fees), so local
  equity tracks Alpaca equity.

### Engine changes (`engine.py`)
- `ExecutionEngine.broker: Optional[AlpacaBroker] = None`. None = old
  simulator (tests, replay, simulation mode all unchanged).
- In broker mode `_execute_fill` calls the broker first; uses real qty/price;
  slippage recorded as real price minus trigger price.
- `process_quote` / `process_bar` catch broker errors: the order stays working
  and is retried after a 5 s backoff. An ENTRY that is hard-rejected is
  cancelled locally (bracket released via existing reconcile path). Exits keep
  retrying with backoff (never silently dropped) and log ERROR.
- In broker mode the 10% bar participation cap is skipped (the broker fills
  the real quantity).
- Swing direct calls already sit in try/except; a broker error leaves the
  position untouched and logs the error.

### Reconciliation (`main.py`)
- Background task every 30 s (and at startup before any trade): compare Alpaca
  positions (symbol, signed qty) with the local book, and Alpaca equity with
  local equity.
- Position mismatch seen on 2 checks in a row with no broker call in flight ->
  `broker_mismatch` set -> new entries blocked (decision reason
  `BROKER_MISMATCH`), exits still allowed. Shown on /health and dashboard.
- Equity drift shown on /health (`broker.equity_drift`), alert-only.
- Startup: if Alpaca has positions the local book does not know about (or vice
  versa), entries stay blocked until they match.

### Config
- `BROKER_MODE` (`simulated` default | `alpaca_paper`), `ALPACA_API_KEY`,
  `ALPACA_SECRET_KEY`, `ALPACA_BASE_URL` (default paper). Keys only in Railway
  env, never in git.
- `simulation_mode` (replay) always forces the simulator.

### UI / labels
- /health gets `broker: {mode, account_number, equity, drift, mismatch, last_error}`.
- Dashboard header shows "Alpaca paper account PA3CSVDZMMPY" instead of
  "virtual"; search frontend for "virtual", "simulated", "$50,000" labels.

## Known trade-offs (accepted)
- No stop sits at Alpaca. If the bot is down, positions are unprotected until
  it restarts (same as today's simulator; restart restores stops).
- Each real fill blocks the event loop up to ~6 s (sync HTTP). Fills are a
  handful per day.
- Crash between Alpaca fill and checkpoint -> local book misses a position.
  Reconciliation detects it and blocks entries; operator fixes by hand.

## Tests
- Unit: broker happy path, partial fill, timeout cancel, reject, paper-URL
  guard (httpx MockTransport, no network).
- Engine in broker mode: stop trigger books Alpaca price; reject keeps exit
  working; entry reject releases bracket.
- Full existing suite must pass unchanged (broker None).
- Live smoke on the paper account after deploy: /health shows broker ok,
  account number and equity match.

## Deploy
Set Railway vars, push main (auto redeploy), verify /health, logs, positions.
