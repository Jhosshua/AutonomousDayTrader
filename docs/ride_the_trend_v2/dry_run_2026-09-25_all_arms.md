# Ride the Trend v2 dry run, 2026-09-25 (all arms)

Replayed 7176 bars, 2,804,461 trade prints and 1,948,463 quotes for AAPL, NVDA, AMD, MSFT, AMZN, META, GOOGL, PLTR, COIN in 32.7 s, simulation mode, no broker, no network.

## Funnel (every setup transition and rejection)
- CHASED: 2
- FEATURE_UNAVAILABLE: 315
- HIGH_VOLUME_PULLBACK: 1
- IMPULSE: 101
- NEW_EXTREME: 3
- NO_TOUCH: 5
- PULLBACK: 4
- PVR_NOT_THIN: 3
- RESUMING: 4
- RESUMPTION_MEASURED: 8
- RESUMPTION_TOO_OLD: 1
- SLOPE_TOO_SLOW: 6
- TOUCH_TOO_EARLY: 7
- WINDOW_CLOSED: 4

## Signals (0) and what the bot decided

## Brackets

Account: {'equity': 49772.55, 'realized_pnl': -227.45, 'positions_open': 0}. Trades by strategy: {'orb': 3}.
Decision summary: {'signals_today': 0, 'orders_today': 0, 'blocked_today': 0, 'top_block_reason': None, 'top_block_text': None, 'blocked_by_reason': {}}.
Data layers on the card: {"required": true, "layer1_ticks": {"live": true, "trades_seen": 2804461}, "layer2_book": {"live": true, "quotes_seen": 1947716, "depth": "top_of_book_nbbo"}, "layer3_timestamps": {"source": "exchange_nanoseconds", "velocity_window_s": 60, "raw_prints": 1117967}, "layer4_macro": {"calendar_loaded": true, "error": null, "regime": "spy_qqq_vwap_ema+vix"}}

## Part-2 diagnostics (measures recorded on every resumption evaluation)
- Resumption evaluations: 8; impulses that pre-empted a live setup: 1
- Feasible entry price interval (slope vs chase cap): {'True': 1, 'False': 7}
- Would fail if enforced: {'IMPULSE_DELTA': 7, 'RESUMPTION_DELTA': 4, 'ROLLING_DELTA': 4, 'SECTOR_AGAINST': 1}; unavailable: {'RESUMPTION_DELTA': 4}
- Quote-classified share of volume 09:45-11:30: {'AAPL': {'quote': 0.411, 'classified': 1.0, 'complete': True}, 'NVDA': {'quote': 0.439, 'classified': 1.0, 'complete': True}, 'AMD': {'quote': 0.244, 'classified': 1.0, 'complete': True}, 'MSFT': {'quote': 0.38, 'classified': 1.0, 'complete': True}, 'AMZN': {'quote': 0.37, 'classified': 1.0, 'complete': True}, 'META': {'quote': 0.38, 'classified': 1.0, 'complete': True}, 'GOOGL': {'quote': 0.297, 'classified': 1.0, 'complete': True}, 'PLTR': {'quote': 0.33, 'classified': 1.0, 'complete': True}, 'COIN': {'quote': 0.283, 'classified': 1.0, 'complete': True}}
  - 10:13 META LONG age 1 slope 0.10 chase -0.52 feasible True imp 0.1274026045387109 pull -0.21774237266857452 res -0.19482835488185465 roll -0.0747857935534589 vel 0.09970319253668854 book 0.056910569105691054 regime_failed ['SECTOR_AGAINST'] unavailable []
  - 10:28 COIN SHORT age 1 slope 0.88 chase 0.73 feasible False imp -0.3397387623672388 pull -0.05222236905863178 res None roll -0.053455789465815855 vel 0.7621963460130987 book -0.1276595744680851 regime_failed [] unavailable []
  - 10:40 AMD SHORT age 1 slope 0.36 chase 0.95 feasible False imp -0.10787716413972274 pull -0.06573785517873511 res None roll -0.09511646520781891 vel 0.4670532089755021 book 0.024390243902439025 regime_failed [] unavailable []
  - 10:41 AMD SHORT age 2 slope 0.50 chase 1.43 feasible False imp -0.10787716413972274 pull -0.06573785517873511 res None roll -0.09988447130239808 vel 0.37613660211498123 book 0.12698412698412698 regime_failed [] unavailable []
  - 10:42 AMD SHORT age 3 slope 0.63 chase 2.09 feasible False imp -0.10787716413972274 pull -0.06573785517873511 res None roll -0.11159246413155746 vel 0.7231035408231408 book 0.26436781609195403 regime_failed [] unavailable []
  - 11:13 AMZN LONG age 1 slope 0.03 chase 0.43 feasible False imp -0.3615231894244879 pull -0.22422744992919869 res -0.05541850969717591 roll -0.3470717169190457 vel -0.019257425826678783 book 0.034482758620689655 regime_failed [] unavailable []
  - 11:14 AMZN LONG age 2 slope 0.09 chase 0.49 feasible False imp -0.3615231894244879 pull -0.22422744992919869 res -0.10793966631216623 roll -0.34531766901936023 vel 0.31420693366239 book 0.1958762886597938 regime_failed [] unavailable []
  - 11:15 AMZN LONG age 3 slope 0.24 chase 0.74 feasible False imp -0.3615231894244879 pull -0.22422744992919869 res -0.11882619860879104 roll -0.34052343964746745 vel 0.5566908746952561 book -0.046153846153846156 regime_failed [] unavailable []

## Invariants
PASS: all invariants held.
