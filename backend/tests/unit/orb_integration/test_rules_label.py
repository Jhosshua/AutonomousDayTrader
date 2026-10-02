"""The ORB rules label on /api/orb and closed-trade rows is read from the live orbs config (PARITY_MANIFEST.json),
never a literal, and spells the deal rule out the way ORBStraddle's page does ("deal on")."""
from backend.app.core.orb_integration import rules_label
from backend.app.strategies.orbs import config as orbs_config


def test_rules_label_matches_the_pinned_manifest():
    label = rules_label()
    manifest = orbs_config.load_manifest()
    assert label["rules_version"] == manifest["effective"]["RULES_VERSION"] == "adaptive-v1.7.1-deal-rule"
    assert label["source_commit"] == manifest["source"]["commit"] == "05d370d"
    # the same keys ORBStraddle's /api/state `rules` dict carries, all on (operator ruling 2026-10-02 14:45 ET)
    assert label["flags"] == {"candle": True, "delta": True, "velocity": True, "macro": True, "deal": True}
    assert label["text"] == "ORBStraddle adaptive-v1.7.1-deal-rule (@05d370d), deal on"


def test_rules_label_reads_the_live_config_not_a_literal(monkeypatch):
    monkeypatch.setattr(orbs_config, "DEAL_RULE", False)
    label = rules_label()
    assert label["flags"]["deal"] is False
    assert label["text"].endswith(", deal off")
    monkeypatch.setattr(orbs_config, "DEAL_RULE", True)
    assert rules_label()["text"].endswith(", deal on")
