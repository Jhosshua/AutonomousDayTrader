# @steered SNARE-2 2026-09-30
"""Regenerate the committed SPY turn-of-month and COIN bitcoin-follow parity fixture.

NOT FOR DEPLOYMENT. This reads the gitignored frozen study data under
research/ten_stock_daytrade_2026_09_30 and writes no credentials or market data rows beyond the
small decision oracle needed by tests. Run from the repository root with the study virtualenv.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "research" / "ten_stock_daytrade_2026_09_30"
sys.path.insert(0, str(RESEARCH))

import atlas  # noqa: E402
import core  # noqa: E402
import fam  # noqa: E402

OUT = ROOT / "backend" / "tests" / "fixtures" / "day_one_research_parity.json"


def date_of(day: int) -> date:
    return date(day // 10000, day // 100 % 100, day % 100)


def selected_btc(B: fam.BTC, day: int, target_minute: int) -> dict | None:
    target = B.key_of(day, target_minute)
    j = int(np.searchsorted(B.key, target, side="right")) - 1
    if j < 0 or B.key[j] < target - 4:
        return None
    selected = int(B.key[j])
    return {
        "target_minute": target_minute,
        "selected_minute": selected % 1440,
        "selected_day": date.fromordinal(selected // 1440).isoformat(),
        "close": round(float(B.c[j]), 8),
    }


def spy_rows() -> list[dict]:
    tp = core.load("SPY")
    selected = atlas.tom_days(core.DAYS[core.DAYS >= core.WIN_START])
    rows = []
    for d, day in enumerate(core.DAYS):
        if int(day) not in selected or not (core.WIN_START <= day <= core.HOLD_END) or not tp.ok[d]:
            continue
        gross = float(tp.close[d] / tp.open_[d] - 1)
        rows.append({
            "date": date_of(int(day)).isoformat(),
            "close_time": "13:00" if int(day) in core.EARLY_CLOSES else "16:00",
            "open": round(float(tp.open_[d]), 6),
            "close": round(float(tp.close[d]), 6),
            "gross": round(gross, 10),
            "model_cost": 0.0006,
            "net": round(gross - 0.0006, 10),
        })
    return rows


def coin_rows() -> list[dict]:
    tp = core.load("COIN")
    B = fam.btc()
    f = fam.btc_features(tp, 935)
    cfg = {"mode": "follow", "at": 935, "k": 1.0, "exit": "close"}
    d0, d1 = core.day_index(core.DAYS, core.WIN_START, core.HOLD_END)
    trades = fam.run_F2_btc(tp, cfg, d0, d1)
    trade_by_day = {int(row[0]): row for row in trades}
    rows = []
    for d in range(d0, d1 + 1):
        day = int(core.DAYS[d])
        anchor_target = 779 if tp.end[d - 1] == 210 else 959
        anchor = selected_btc(B, int(core.DAYS[d - 1]), anchor_target)
        decision = selected_btc(B, day, 574)
        zmove = float(f["mz"][d]) if math.isfinite(f["mz"][d]) else None
        sigma = float(f["sig_mz"][d]) if math.isfinite(f["sig_mz"][d]) else None
        raw_side = 1 if zmove is not None and sigma is not None and sigma > 0 and zmove > sigma else (
            -1 if zmove is not None and sigma is not None and sigma > 0 and zmove < -sigma else 0
        )
        ssr = bool(raw_side < 0 and core.ssr_blocks_short(tp, d, fam.bar_closing_at(935)))
        e = core.fill_index(tp.O, d, fam.bar_closing_at(935), int(tp.end[d])) if raw_side else -1
        tr = trade_by_day.get(d)
        rows.append({
            "date": date_of(day).isoformat(),
            "prior_session": date_of(int(core.DAYS[d - 1])).isoformat(),
            "anchor": anchor,
            "decision": decision,
            "elapsed_hours": None if anchor is None or decision is None else round(float(
                (B.key_of(day, 574) - B.key_of(int(core.DAYS[d - 1]), anchor_target)) / 60.0
            ), 8),
            "zmove": None if zmove is None else round(zmove, 12),
            "history_count": min(20, sum(math.isfinite(x) for x in f["mz"][:d])),
            "sigma": None if sigma is None else round(sigma, 12),
            "threshold_side": raw_side,
            "ssr_blocked": ssr,
            "trade_side": 0 if tr is None else int(tr[1]),
            "entry_bar": None if tr is None else int(tr[8]),
            "model_entry": None if tr is None else round(float(tr[2]), 6),
            "official_close": round(float(tp.close[d]), 6),
            "net": None if tr is None else round(float(tr[4]), 10),
            "full_session_ok": bool(tp.ok[d]),
        })
    return rows


def main() -> None:
    spy = spy_rows()
    coin = coin_rows()
    payload = {
        "version": 1,
        "source": "research/ten_stock_daytrade_2026_09_30",
        "spy": {
            "evidence": "post hoc atlas pattern, not preregistered",
            "year1_count": sum(r["date"] <= "2025-09-30" for r in spy),
            "year2_count": sum(r["date"] >= "2025-10-01" for r in spy),
            "rows": spy,
        },
        "coin": {
            "evidence": "post holdout selection, design gate failed",
            "config_index": 11,
            "params": {"mode": "follow", "at": 935, "k": 1.0, "exit": "close"},
            "initial_history": [round(float(x), 12) for x in fam.btc_features(core.load("COIN"), 935)["mz"][:core.day_index(core.DAYS, core.WIN_START, core.HOLD_END)[0]] if math.isfinite(x)][-20:],
            "rows": coin,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=1) + "\n")
    print(f"wrote {OUT.relative_to(ROOT)}: SPY {len(spy)}, COIN days {len(coin)}, COIN trades {sum(r['trade_side'] != 0 for r in coin)}")


if __name__ == "__main__":
    main()
