"""backend/app/strategies/__init__.py
AutonomousDayTrader Intraday Trading Strategies and Dynamic Self-Adaptation Engine.
"""
from backend.app.strategies.base import (
    Strategy,
    SignalEvent,
    StrategyStatus,
    calculate_anchored_vwap,
    calculate_vwap_bands,
    calculate_atr,
    calculate_ema,
    calculate_sma,
    calculate_zscore,
    calculate_rsi,
    calculate_rvol,
)
from backend.app.strategies.orb import OpeningRangeBreakoutStrategy, evaluate_orb_signal
from backend.app.strategies.vwap_pullback import VWAPPullbackStrategy
from backend.app.strategies.news_momentum import NewsMomentumStrategy, score_news_sentiment
from backend.app.strategies.mean_reversion import MeanReversionStrategy, evaluate_mean_reversion_zscore
from backend.app.strategies.adaptation import (
    DynamicAdaptationEngine,
    TimeOfDayPhase,
    get_vix_regime,
    get_time_of_day_phase,
)

__all__ = [
    "Strategy",
    "SignalEvent",
    "StrategyStatus",
    "calculate_anchored_vwap",
    "calculate_vwap_bands",
    "calculate_atr",
    "calculate_ema",
    "calculate_sma",
    "calculate_zscore",
    "calculate_rsi",
    "calculate_rvol",
    "OpeningRangeBreakoutStrategy",
    "evaluate_orb_signal",
    "VWAPPullbackStrategy",
    "NewsMomentumStrategy",
    "score_news_sentiment",
    "MeanReversionStrategy",
    "evaluate_mean_reversion_zscore",
    "DynamicAdaptationEngine",
    "TimeOfDayPhase",
    "get_vix_regime",
    "get_time_of_day_phase",
]
