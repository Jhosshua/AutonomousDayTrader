# Integrated Multi-Day Swing Trading Dry Run Report

**Strategy**: 2-Day Panic Dip (Connors RSI-2)
**Date Generated**: 2026-09-23T22:46:39.332344+00:00
**Status**: PASS
**Simulation Duration**: 0.015 seconds

## Quantitative Rule Certifications
| Rule | Description | Certified |
|------|-------------|-----------|
| Rule 1 | Macro Floor: Close > 200-day SMA | ✅ PASS |
| Rule 2 | Market Leadership: 60d RS >= QQQ | ✅ PASS |
| Rule 3 | Panic Trigger: Daily RSI(2) < 10.0 | ✅ PASS |
| Rule 4 | Earnings Blackout: 48h blackout & next-day exit | ✅ PASS |
| Rule 5 | Sizing: $25,000/slot, max 2 concurrent positions | ✅ PASS |
| Rule 6 | Emergency Stop: 2.5x ATR below fill | ✅ PASS |
| Rule 7a | Exit: Prior Close > 5-day SMA | ✅ PASS |
| Rule 7b | Exit: Prior RSI(2) > 70.0 | ✅ PASS |
| Rule 7c | Exit: 5-day time stop | ✅ PASS |
| Isolation | 15:45-15:58 ET Intraday Flattening Exemption | ✅ PASS |
| Risk | Shared $50,000 Account Pool Coordination | ✅ PASS |
| UI | Next.js Obsidian Dark Serialization Parity | ✅ PASS |

## Account & Execution Summary
- **Initial Balance**: $50,000.00
- **Ending Equity**: $52,953.81
- **Realized PnL**: +$2,953.81
- **Completed Trades**: 2
- **Open Positions at End**: 0

## Trade Ledger
```json
[
  {
    "symbol": "LRCX",
    "shares": 36,
    "price": 720.5,
    "realized_pnl": 1565.26,
    "reason": "5_SMA_CROSS_CLOSE_719.00_SMA_690.60"
  },
  {
    "symbol": "KLAC",
    "shares": 37,
    "price": 723.0,
    "realized_pnl": 2071.24,
    "reason": "5_SMA_CROSS_CLOSE_722.00_SMA_691.20"
  }
]
```

## Port Hygiene Audit
```json
{
  "all_ports_free": true,
  "details": {
    "3005": "CLEAN (FREE)",
    "8000": "CLEAN (FREE)",
    "8005": "CLEAN (FREE)",
    "8080": "CLEAN (FREE)"
  }
}
```

## Complete Verification Output
```json
{
  "status": "PASS",
  "simulation_only": true,
  "strategy": "2-Day Panic Dip (Connors RSI-2)",
  "duration_seconds": 0.015,
  "days_simulated": 6,
  "certified_symbols": [
    "LRCX",
    "KLAC",
    "MU",
    "AMD",
    "GS"
  ],
  "rules_verified": {
    "rule_1_macro_floor_200_sma": true,
    "rule_2_relative_strength_60d_qqq": true,
    "rule_3_panic_dip_rsi2_under_10": true,
    "rule_4_earnings_blackout_and_exit_veto": true,
    "rule_5_market_open_sizing_25k_max_2_slots": true,
    "rule_6_emergency_stop_loss_2_5_atr": true,
    "rule_7a_take_profit_5_sma_cross": true,
    "rule_7b_take_profit_rsi2_over_70": true,
    "rule_7c_time_exit_5_trading_days": true,
    "flattening_exemption_15_58_overnight_hold": true,
    "shared_margin_50k_account_pool": true,
    "ui_state_serialization_parity": true
  },
  "account": {
    "initial_equity": 50000.0,
    "final_equity": 52953.81,
    "cash": 27953.81,
    "realized_pnl": 2953.81,
    "unrealized_pnl": 0.0,
    "open_positions": 0
  },
  "trade_ledger": [
    {
      "symbol": "LRCX",
      "shares": 36,
      "price": 720.5,
      "realized_pnl": 1565.26,
      "reason": "5_SMA_CROSS_CLOSE_719.00_SMA_690.60"
    },
    {
      "symbol": "KLAC",
      "shares": 37,
      "price": 723.0,
      "realized_pnl": 2071.24,
      "reason": "5_SMA_CROSS_CLOSE_722.00_SMA_691.20"
    }
  ],
  "port_hygiene": {
    "all_ports_free": true,
    "details": {
      "3005": "CLEAN (FREE)",
      "8000": "CLEAN (FREE)",
      "8005": "CLEAN (FREE)",
      "8080": "CLEAN (FREE)"
    }
  }
}
```
