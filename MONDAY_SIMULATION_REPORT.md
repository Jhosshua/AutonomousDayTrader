# Monday Market Open Integrated Dry Run

This report is a deterministic replay through the production ingestion, execution, bracket, and UI serialization pathways. It is not a live market scan and does not certify real-account fills.

```json
{
  "status": "PASS",
  "simulation_only": true,
  "fixture": "tests/e2e/fixtures/monday_open_session.json",
  "events_processed": 62,
  "event_bus_errors": 0,
  "duration_seconds": 1.095,
  "account": {
    "equity": 49961.26,
    "cash": 49961.26,
    "realized_pnl": -38.74,
    "unrealized_pnl": 0.0,
    "fees_paid": 1.42,
    "open_positions": 0,
    "working_orders": 0,
    "status": "ACTIVE"
  },
  "orders": {
    "created": 17,
    "filled": 9,
    "rejected": 0,
    "details": [
      {
        "symbol": "NVDA",
        "side": "BUY",
        "qty": 100,
        "strategy_id": "orb",
        "estimated_price": 124.95,
        "stop_price": 124.2,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "NVDA",
        "side": "SELL",
        "qty": 50,
        "strategy_id": "orb",
        "estimated_price": null,
        "stop_price": 125.0208,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "NVDA",
        "side": "SELL",
        "qty": 50,
        "strategy_id": "orb",
        "estimated_price": null,
        "stop_price": null,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "NVDA",
        "side": "SELL",
        "qty": 50,
        "strategy_id": "orb",
        "estimated_price": null,
        "stop_price": null,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "TSLA",
        "side": "BUY",
        "qty": 57,
        "strategy_id": "news_momentum",
        "estimated_price": 218.1,
        "stop_price": 214.78,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "TSLA",
        "side": "SELL",
        "qty": 57,
        "strategy_id": "news_momentum",
        "estimated_price": null,
        "stop_price": 214.78,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "TSLA",
        "side": "SELL",
        "qty": 28,
        "strategy_id": "news_momentum",
        "estimated_price": null,
        "stop_price": null,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "TSLA",
        "side": "SELL",
        "qty": 29,
        "strategy_id": "news_momentum",
        "estimated_price": null,
        "stop_price": null,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "TSLA",
        "side": "SELL",
        "qty": 57,
        "strategy_id": "NEWS_CONTRADICTION",
        "estimated_price": null,
        "stop_price": null,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "BUY",
        "qty": 83,
        "strategy_id": "vwap_pullback",
        "estimated_price": 150.2,
        "stop_price": 149.3589,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 83,
        "strategy_id": "vwap_pullback",
        "estimated_price": null,
        "stop_price": 149.8,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 41,
        "strategy_id": "vwap_pullback",
        "estimated_price": null,
        "stop_price": null,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 42,
        "strategy_id": "vwap_pullback",
        "estimated_price": null,
        "stop_price": null,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "BUY",
        "qty": 72,
        "strategy_id": "orb",
        "estimated_price": 153.6,
        "stop_price": 148.77,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 72,
        "strategy_id": "orb",
        "estimated_price": null,
        "stop_price": 151.7,
        "status": "FILLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 36,
        "strategy_id": "orb",
        "estimated_price": null,
        "stop_price": null,
        "status": "CANCELLED",
        "reject_reason": null
      },
      {
        "symbol": "AAPL",
        "side": "SELL",
        "qty": 36,
        "strategy_id": "orb",
        "estimated_price": null,
        "stop_price": null,
        "status": "CANCELLED",
        "reject_reason": null
      }
    ]
  },
  "relay_statuses": {
    "stock": "connected",
    "news": "connected",
    "vix": "connected"
  },
  "vix": 26.5,
  "strategies": [
    {
      "id": "orb",
      "name": "Opening Range Breakout",
      "status": "ACTIVE",
      "daily_pnl": 14.21,
      "win_rate": 0.5,
      "trades_count": 2,
      "sharpe": 0.05
    },
    {
      "id": "vwap_pullback",
      "name": "VWAP Trend Pullback & Continuation",
      "status": "ACTIVE",
      "daily_pnl": -42.51,
      "win_rate": 0.0,
      "trades_count": 1,
      "sharpe": 0.0
    },
    {
      "id": "news_momentum",
      "name": "Catalyst News Momentum Breakout",
      "status": "ACTIVE",
      "daily_pnl": -10.44,
      "win_rate": 0.0,
      "trades_count": 1,
      "sharpe": 0.0
    },
    {
      "id": "mean_reversion",
      "name": "Statistical Mean Reversion / Exhaustion Fades",
      "status": "ACTIVE",
      "daily_pnl": 0.0,
      "win_rate": 0.0,
      "trades_count": 0,
      "sharpe": 0.0
    }
  ],
  "ui": {
    "state_updates": 66,
    "last_has_all_positions": true,
    "last_equity": 49961.26,
    "last_positions_count": 0
  }
}
```
