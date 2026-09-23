"""backend/tests/unit/test_sentiment.py
Unit tests for FinancialSentimentScorer and regex word boundary categorization.
Certifies prevention of substring collisions (e.g. 'sector' -> 'sec').
"""
import pytest
from backend.app.ingestion.sentiment import FinancialSentimentScorer, sentiment_scorer
from backend.app.models.events import CatalystCategory


def test_sector_does_not_falsely_trigger_sec_legal_investigation():
    """Verify 'sector' does not match 'sec' and falsely categorize as LEGAL_INVESTIGATION."""
    headline = "Apple Leads Tech Sector Rally After Strong Demand"
    score, confidence, category = sentiment_scorer.score(headline)

    assert category != CatalystCategory.LEGAL_INVESTIGATION
    assert category in (CatalystCategory.GENERAL_CATALYST, CatalystCategory.NEUTRAL)
    assert score > 0.0, "Headline should be moderately bullish due to 'rally' and 'strong demand'"


def test_contract_window_does_not_falsely_trigger_contract_win():
    """Verify 'contract window' does not match 'contract win' multi-word phrase."""
    headline = "Contract window closed today"
    score, confidence, category = sentiment_scorer.score(headline)

    assert category != CatalystCategory.PARTNERSHIP_CONTRACT
    assert score == 0.0, "Headline without sentiment keywords should score 0.0"


def test_legitimate_sec_probe_categorization():
    """Verify legitimate SEC probe headline properly categorizes as LEGAL_INVESTIGATION."""
    headline = "SEC probe finds CEO misconduct and accounting fraud"
    score, confidence, category = sentiment_scorer.score(headline)

    assert category == CatalystCategory.LEGAL_INVESTIGATION
    assert score < -0.5, "Negative sentiment on legal fraud probe"


def test_earnings_and_guidance_categorization():
    """Verify earnings beat and guidance raise categorization."""
    beat_headline = "Reports record earnings and beats estimates"
    score_beat, conf_beat, cat_beat = sentiment_scorer.score(beat_headline)
    assert cat_beat == CatalystCategory.EARNINGS_BEAT
    assert score_beat > 0.5

    raise_headline = "Company raises guidance for full year outlook"
    score_raise, conf_raise, cat_raise = sentiment_scorer.score(raise_headline)
    assert cat_raise == CatalystCategory.GUIDANCE_RAISE
    assert score_raise > 0.5


def test_sentiment_score_bounds():
    """Verify sentiment scores stay bounded in [-1.0, 1.0] and confidence in [0.0, 1.0]."""
    test_cases = [
        "Record earnings blowout beat profit surges raised guidance partner deal",
        "Bankruptcy fraud investigation probe lawsuit default downgraded slash cut",
        "",
        "The market is open today",
    ]
    scorer = FinancialSentimentScorer()
    for text in test_cases:
        score, confidence, cat = scorer.score(text)
        assert -1.0 <= score <= 1.0
        assert 0.0 <= confidence <= 1.0
        assert isinstance(cat, CatalystCategory)
