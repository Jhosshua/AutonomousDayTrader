"""backend/app/ingestion/sentiment.py
High-speed deterministic financial lexicon sentiment scorer and catalyst classifier.
"""
from __future__ import annotations
import math
import re
from typing import Dict, List, Set, Tuple

from backend.app.models.events import CatalystCategory


class FinancialSentimentScorer:
    """
    Sub-millisecond lexicon-based financial NLP sentiment and catalyst classifier.
    Produces polarity S in [-1.0, 1.0], confidence C in [0.0, 1.0], and categorized catalyst bucket.
    """

    # Curated financial domain lexicons
    BULLISH_KEYWORDS: Dict[str, float] = {
        # Earnings & Guidance
        "record earnings": 1.0,
        "beat estimates": 0.9,
        "beats estimates": 0.9,
        "blowout quarter": 1.0,
        "raised guidance": 0.95,
        "raises guidance": 0.95,
        "upgrades guidance": 0.9,
        "tops revenue": 0.85,
        "profit surges": 0.9,
        "record revenue": 0.95,
        "record profit": 0.95,
        # Regulatory & Biotech
        "fda approval": 1.0,
        "fda approves": 1.0,
        "breakthrough therapy": 0.9,
        "patent granted": 0.8,
        "cleared by fda": 0.95,
        # Commercial & Corporate
        "massive partnership": 0.85,
        "multibillion dollar deal": 0.9,
        "contract win": 0.8,
        "shares surge": 0.8,
        "shares soar": 0.85,
        "stock climbs": 0.6,
        "share buyback": 0.75,
        "boosts dividend": 0.7,
        "buy rating": 0.75,
        "upgraded to buy": 0.85,
        "upgrades to overweight": 0.8,
        # Single keywords
        "beat": 0.6,
        "beats": 0.6,
        "beating": 0.6,
        "exceed": 0.6,
        "exceeds": 0.6,
        "exceeded": 0.6,
        "succeeds": 0.5,
        "surge": 0.6,
        "surges": 0.6,
        "soar": 0.7,
        "soars": 0.7,
        "growth": 0.4,
        "profit": 0.4,
        "profitable": 0.4,
        "acquisition": 0.5,
        "awarded": 0.5,
        "outperform": 0.6,
        "bullish": 0.6,
        "dividend": 0.3,
        "rally": 0.5,
    }

    BEARISH_KEYWORDS: Dict[str, float] = {
        # Earnings & Guidance
        "missed estimates": -0.9,
        "misses estimates": -0.9,
        "lowered guidance": -0.95,
        "lowers guidance": -0.95,
        "slashes forecast": -1.0,
        "cuts forecast": -0.9,
        "profit drops": -0.85,
        "revenue sinks": -0.85,
        "disappointing results": -0.8,
        # Regulatory, Legal & Accounting
        "sec investigation": -1.0,
        "sec probe": -1.0,
        "subpoena": -0.9,
        "fraud investigation": -1.0,
        "class action": -0.7,
        "lawsuit": -0.6,
        "fda rejection": -1.0,
        "fda rejects": -1.0,
        "clinical trial failure": -1.0,
        "clinical hold": -0.95,
        "halted trading": -0.9,
        # Financial Distress
        "bankruptcy": -1.0,
        "chapter 11": -1.0,
        "default": -0.9,
        "debt restructuring": -0.7,
        "layoffs": -0.6,
        "slashes jobs": -0.7,
        "shares plunge": -0.85,
        "shares tumble": -0.85,
        "stock drops": -0.6,
        "downgraded to sell": -0.9,
        "downgraded to underweight": -0.8,
        # Single keywords
        "miss": -0.6,
        "misses": -0.6,
        "plunge": -0.7,
        "plunges": -0.7,
        "tumble": -0.7,
        "tumbles": -0.7,
        "decline": -0.4,
        "warning": -0.5,
        "loss": -0.4,
        "losses": -0.4,
        "drop": -0.4,
        "drops": -0.4,
        "bearish": -0.6,
        "rejection": -0.8,
        "fraud": -1.0,
    }

    NEGATIONS: Set[str] = {
        "not", "no", "never", "without", "fails", "failed", "failing",
        "unable", "denies", "denied", "rejected", "cancels", "cancelled"
    }

    INTENSIFIERS: Set[str] = {
        "significantly", "massively", "substantially", "hugely", "drastically",
        "record", "unprecedented", "blowout", "sharp", "sharply"
    }

    DIMINISHERS: Set[str] = {
        "slightly", "modestly", "partially", "marginally", "somewhat"
    }

    def __init__(self) -> None:
        self._token_re = re.compile(r"[a-z0-9\-\']+")

    def score(self, headline: str, summary: str = "") -> Tuple[float, float, CatalystCategory]:
        """Calculates normalized sentiment S in [-1, 1], confidence C in [0, 1], and catalyst category."""
        text = f"{headline} {summary}".lower()
        tokens = self._token_re.findall(text)
        if not tokens:
            return 0.0, 0.0, CatalystCategory.NEUTRAL

        raw_score = 0.0
        matches = 0

        # 1. Multi-word phrase matching (highest specificity)
        for phrase, weight in self.BULLISH_KEYWORDS.items():
            if " " in phrase and phrase in text:
                raw_score += weight * 1.5
                matches += 2

        for phrase, weight in self.BEARISH_KEYWORDS.items():
            if " " in phrase and phrase in text:
                raw_score += weight * 1.5
                matches += 2

        # 2. Token-level matching with negation & intensifier context
        for idx, token in enumerate(tokens):
            w = 0.0
            if token in self.BULLISH_KEYWORDS and " " not in token:
                w = self.BULLISH_KEYWORDS[token]
            elif token in self.BEARISH_KEYWORDS and " " not in token:
                w = self.BEARISH_KEYWORDS[token]

            if w != 0.0:
                # Look back up to 3 tokens for negation, intensifiers, diminishers
                lookback = tokens[max(0, idx - 3) : idx]
                is_negated = any(t in self.NEGATIONS for t in lookback)
                is_intensified = any(t in self.INTENSIFIERS for t in lookback)
                is_diminished = any(t in self.DIMINISHERS for t in lookback)

                mod = 1.0
                if is_intensified:
                    mod *= 1.35
                if is_diminished:
                    mod *= 0.60
                if is_negated:
                    mod *= -0.80  # Flip sign and scale

                raw_score += w * mod
                matches += 1

        if matches == 0:
            return 0.0, 0.0, CatalystCategory.NEUTRAL

        # Normalize score via tanh soft-clipping
        final_score = max(-1.0, min(1.0, math.tanh(raw_score / 2.0)))
        confidence = min(1.0, matches / 2.0)

        category = self._classify_category(text, final_score)
        return round(final_score, 4), round(confidence, 4), category

    def _classify_category(self, text: str, score: float) -> CatalystCategory:
        """Categorize into specific trading catalyst buckets."""
        if any(k in text for k in ("fda", "biotech", "clinical", "drug", "phase 3", "trial")):
            return CatalystCategory.FDA_APPROVAL if score > 0 else CatalystCategory.FDA_REJECTION
        if any(k in text for k in ("earnings", "eps", "quarter", "revenue", "sales", "profit")):
            return CatalystCategory.EARNINGS_BEAT if score > 0 else CatalystCategory.EARNINGS_MISS
        if any(k in text for k in ("guidance", "outlook", "forecast")):
            return CatalystCategory.GUIDANCE_RAISE if score > 0 else CatalystCategory.GUIDANCE_CUT
        if any(k in text for k in ("sec", "probe", "investigation", "subpoena", "lawsuit", "fraud")):
            return CatalystCategory.LEGAL_INVESTIGATION
        if any(k in text for k in ("upgrade", "downgrade", "target price", "pt")):
            return CatalystCategory.ANALYST_UPGRADE if score > 0 else CatalystCategory.ANALYST_DOWNGRADE
        if any(k in text for k in ("partner", "partnership", "deal", "contract", "merger", "acquisition")):
            return CatalystCategory.PARTNERSHIP_CONTRACT

        return CatalystCategory.GENERAL_CATALYST if abs(score) >= 0.5 else CatalystCategory.NEUTRAL


# Global singleton scorer instance
sentiment_scorer = FinancialSentimentScorer()
