"""Run structural variants of the live rules; report per period (A_IS 2024-01..2025-03, B_VAL 2025-04..2025-12, C_HOLD 2026)."""
import json, sys, math, numpy as np
from multiprocessing import Pool
import sim

V = {
 "V0_baseline": ({}, {}),
 "V1_no_costs": ({}, {"slip_bps":0,"stop_slip_bps":0}),
 "V2_costs_3bps": ({}, {"slip_bps":3,"stop_slip_bps":3}),
 "V3_no_scaleout_all_at_T1": ({}, {"scale_out":False}),
 "V4_single_target_2R": ({}, {"scale_out":False,"t1_r_override":2.0}),
 "V5_T1_1R_T2_2R": ({}, {"t1_r_override":1.0,"t2_r_override":2.0}),
 "V6_T1_1.5R_T2_3R": ({}, {"t1_r_override":1.5,"t2_r_override":3.0}),
 "V7_no_breakeven_after_T1": ({}, {"breakeven_after_t1":False}),
 "V8_no_trail_after_T1": ({}, {"trail_after_t1":False}),
 "V9_trail_3ATR": ({}, {"trail_atr_mult":3.0}),
 "V10_trail_1ATR": ({}, {"trail_atr_mult":1.0}),
 "V11_timestop_30": ({}, {"time_stop_min":30}),
 "V12_timestop_60": ({}, {"time_stop_min":60}),
 "V13_timestop_20_underwater": ({}, {"time_stop_min":20,"time_stop_only_if_underwater":True}),
 "V14_morning_only": ({}, {"allow_afternoon":False}),
 "V15_afternoon_only": ({}, {"allow_morning":False}),
 "V16_no_index_filter": ({}, {"use_index_filter":False}),
 "V17_no_vix_adapt": ({}, {"use_vix":False}),
 "V18_stop_1ATR": ({}, {"stop_atr_mult":1.0}),
 "V19_stop_2ATR": ({}, {"stop_atr_mult":2.0}),
 "V20_stopband_1std": ({"stop_band":1.0}, {}),
 "V21_stopband_0.25std": ({"stop_band":0.25}, {}),
 "V22_wick_AND_volume": ({"require_both":True}, {}),
 "V23_no_prior_zone_carry": ({"allow_prior_zone":False}, {}),
 "V24_cooldown_30": ({"cooldown_min":30}, {}),
 "V25_cooldown_60": ({"cooldown_min":60}, {}),
 "V26_long_only": ({"long_only":True}, {}),
 "V27_short_only": ({"short_only":True}, {}),
 "V28a_skip_calm_std<0.3%": ({"min_std_pct":0.003}, {}),
 "V28b_only_calm_std<0.22%": ({"max_std_pct":0.0022}, {}),
 "V29_wick_0.5": ({"wick_min":0.5}, {}),
 "V30_vol_1.5x": ({"vol_min_ratio":1.5}, {}),
 "V31_ema_gap_0.1%": ({"ema_gap_min_pct":0.001}, {}),
 "V32_zone_tight_0.1_0.15": ({"zone_lo":0.1,"zone_hi":0.15}, {}),
 "V33_zone_wide_0.4_0.6": ({"zone_lo":0.4,"zone_hi":0.6}, {}),
 "V34_tp_bands_1.5_3": ({"tp1_band":1.5,"tp2_band":3.0}, {}),
 "V35_tp1_band_2": ({"tp1_band":2.0,"tp2_band":3.0}, {}),
 "V36a_no_ETFs": ({}, {"symbols":["AAPL","NVDA","TSLA","AMD","MSFT","AMZN","META","GOOGL","PLTR","COIN"]}),
 "V37_trade_midday_too": ({}, {"phase_block_midday":False}),
 "V38_same_bar_target_first": ({}, {"same_bar_stop_first":False}),
 "V39_target_needs_through": ({}, {"target_needs_through":True}),
 "V40_max_positions_1": ({}, {"max_positions":1}),
 "V41_min_bars_100": ({"min_bars":100}, {}),
 "V45_be_buffer_0": ({}, {"be_buffer_mult":0.0}),
 "V46_trail_from_start": ({}, {"trail_from_start":True}),
 "V47_stop_slip_3": ({}, {"stop_slip_bps":3}),
 "V48_ema_9_21": ({"ema_fast":9,"ema_slow":21,"min_bars":21}, {}),
 "V49_ema_50_200": ({"ema_fast":50,"ema_slow":200,"min_bars":200}, {}),
 "V50_limit_close_wait1": ({}, {"entry_mode":"limit_close","limit_wait_bars":1}),
 "V51_limit_close_wait3": ({}, {"entry_mode":"limit_close","limit_wait_bars":3}),
 "V52_limit_vwap_wait3": ({}, {"entry_mode":"limit_vwap","limit_wait_bars":3}),
 "V53_limit_vwap_wait5": ({}, {"entry_mode":"limit_vwap","limit_wait_bars":5}),
 "V54_limit_vwap_wait10": ({}, {"entry_mode":"limit_vwap","limit_wait_bars":10}),
 "V55_limit_vwap5_morning": ({}, {"entry_mode":"limit_vwap","limit_wait_bars":5,"allow_afternoon":False}),
 "V57_first_signal_only": ({"first_signal_only":True}, {}),
 "V58_max_2nd_signal": ({"max_nth_signal":2}, {}),
 "V59_day_move_0.5%": ({"min_day_move_pct":0.005}, {}),
 "V60_day_move_1%": ({"min_day_move_pct":0.01}, {}),
 "V61_vwap_slope_0.1%": ({"min_vwap_slope_pct":0.001}, {}),
 "V62_vwap_slope_0.2%": ({"min_vwap_slope_pct":0.002}, {}),
 "V63_bars_since_cross_20": ({"min_bars_since_cross":20}, {}),
 "V64_bars_since_cross_45": ({"min_bars_since_cross":45}, {}),
 "V65_trend_combo_move1%_slope0.1%_cross20": ({"min_day_move_pct":0.01,"min_vwap_slope_pct":0.001,"min_bars_since_cross":20}, {}),
 "V66_combo_plus_limit_vwap5": ({"min_day_move_pct":0.01,"min_vwap_slope_pct":0.001,"min_bars_since_cross":20}, {"entry_mode":"limit_vwap","limit_wait_bars":5}),
 "V67_forced_combo_limitclose3_morning_max2": ({"max_nth_signal":2}, {"entry_mode":"limit_close","limit_wait_bars":3,"allow_afternoon":False}),
 "V68_forced_combo_limitvwap5_morning_max2": ({"max_nth_signal":2}, {"entry_mode":"limit_vwap","limit_wait_bars":5,"allow_afternoon":False}),
 "V69_no_fridays_proxy_max2_morning": ({"max_nth_signal":2}, {"allow_afternoon":False}),
 "V56_limit_vwap5_noscaleout_2R": ({}, {"entry_mode":"limit_vwap","limit_wait_bars":5,"scale_out":False,"t1_r_override":2.0}),
}

def per_period(trades):
    out = {}
    for name, lo, hi in (("A", "2024-01-01", "2025-03-31"), ("B", "2025-04-01", "2025-12-31"), ("C", "2026-01-01", "2026-12-31"), ("ALL", "2024-01-01", "2026-12-31")):
        t = [x for x in trades if lo <= x.date <= hi]
        if not t:
            out[name] = dict(n=0); continue
        r = np.array([x.r for x in t]); p = sum(x.pnl for x in t)
        out[name] = dict(n=len(t), mean_r=round(float(r.mean()), 3), se=round(float(r.std(ddof=1) / math.sqrt(len(r))), 3), pnl=round(p))
    return out

def one(item):
    name, (sp, ep) = item
    tr = sim.run(sim.SigParams(**sp), sim.ExecParams(**ep))
    return name, per_period(tr)

if __name__ == "__main__":
    names = sys.argv[1:] or list(V)
    with Pool(6) as pool:
        res = dict(pool.map(one, [(n, V[n]) for n in names]))
    import os
    prev = json.load(open("data/variants.json")) if os.path.exists("data/variants.json") else {}
    prev.update(res); json.dump(prev, open("data/variants.json", "w"), indent=1)
    print(f"{'variant':32s} {'ALL n':>6s} {'meanR':>7s} {'se':>6s} {'pnl':>8s} | {'A meanR':>8s} {'B meanR':>8s} {'C meanR':>8s}")
    for n in names:
        r = res[n]
        print(f"{n:32s} {r['ALL']['n']:6d} {r['ALL']['mean_r']:7.3f} {r['ALL']['se']:6.3f} {r['ALL']['pnl']:8.0f} | {r['A'].get('mean_r',0):8.3f} {r['B'].get('mean_r',0):8.3f} {r['C'].get('mean_r',0):8.3f}")
