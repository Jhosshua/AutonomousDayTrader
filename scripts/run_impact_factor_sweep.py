"""One frozen Price Impact Factor (dPrice/Volume) profit sweep, reusing Codex's velocity-pivot tests."""
import argparse, itertools, json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.app.microstructure.velocity_pivot import (POLICIES, cash_arrays, block_labels, block_profits,
                                                       exact_block_test, permutation_profit_test)
from scripts.grand_audit_math import aggregate

H = ('100ms', '1s', '5s')
IMPACT = [f'{k}_{h}' for k in ('impact_usd_per_share', 'impact_bps_per_10k_usd', 'abs_impact_bps_per_10k_usd') for h in H]
FEATURES = IMPACT + [f'volume_shares_{h}' for h in H]


def grid(v):
    f = v[np.isfinite(v)]
    return np.unique(np.concatenate([np.percentile(f, np.arange(10, 100, 10), method='lower'), [f.min(), f.max()]]))


def family(x, dates):
    rules, masks, seen = [], [], set()
    def cond(j, op, t):
        v = x[:, j]; return np.isfinite(v) & (v >= t if op == '>=' else v <= t)
    def add(m, c):
        if m.sum() < 10 or len(set(dates[m])) < 3: return
        k = np.packbits(m).tobytes()
        if k in seen: return
        seen.add(k); rules.append(c); masks.append(m)
    singles = {}
    for j, f in enumerate(FEATURES):
        singles[f] = [(dict(feature=f, operator=op, threshold=float(t)), cond(j, op, t)) for t in grid(x[:, j]) for op in ('>=', '<=')]
        for c, m in singles[f]: add(m, [c])
    for f in IMPACT:
        for h in H:
            lows = [(c, m) for c, m in singles[f'volume_shares_{h}'] if c['operator'] == '<=']
            for (a, ma), (b, mb) in itertools.product(singles[f], lows): add(ma & mb, [a, b])
    return rules, np.array(masks)


def run(out):
    base = out.parent
    panel = [json.loads(l) for l in open(base / 'final_velocity_pivot_v1/candidate_panel.jsonl')]
    feats = {r['candidate_id']: r for r in map(json.loads, open(out / 'impact_features.jsonl'))}
    x = np.array([[feats[r['candidate_id']]['x'][f] if feats[r['candidate_id']]['x'][f] is not None else np.nan for f in FEATURES] for r in panel])
    dates = np.array([r['session_date'] for r in panel]); blocks = block_labels(dates)
    old = np.array([bool(r.get('old_study')) for r in panel])
    rules, masks = family(x, dates)
    print('RULES', len(rules), 'old-study candidates', old.sum(), 'dates old', sorted(set(dates[old])), flush=True)
    units, valid = cash_arrays(panel)
    profits, eligible, names = block_profits(masks, units, valid, blocks)
    primary = exact_block_test(profits, eligible)
    secondary = permutation_profit_test(masks, units, valid, dates, draws=9999, seed=2026092799)
    total = profits.sum(axis=3)  # rule x policy x scenario, price units (1e-4 USD)
    passing = eligible & (primary['adjusted_p'] < .05) & (secondary['adjusted_p'] < .05) & (total.min(axis=2) > 0)

    def prof(i, p):
        chosen = [r for r, y in zip(panel, masks[i]) if y]
        return dict(rule=rules[i], policy=POLICIES[p], selected=len(chosen), sessions=sorted({r['session_date'] for r in chosen}),
                    net_usd_full_match=total[i, p, 0] / 1e4, net_usd_zero_match=total[i, p, 1] / 1e4,
                    primary_adj_p=float(primary['adjusted_p'][i, p]), primary_nominal_p=float(primary['nominal_p'][i, p]),
                    secondary_adj_p=float(secondary['adjusted_p'][i, p]), secondary_nominal_p=float(secondary['nominal_p'][i, p]),
                    economics={str(a): aggregate([r['outcomes'][f'{POLICIES[p]}|{a}'] for r in chosen], len(panel)) for a in (10000, 0)},
                    selected_ids=[r['candidate_id'] for r in chosen])

    # walk-forward: choose on old-study dates only, score on the rest
    known = np.where(valid, units, 0).astype(float)
    elig_old = masks[:, old].astype(float) @ (~valid[old].all(axis=2)).astype(float) == 0
    elig_new = masks[:, ~old].astype(float) @ (~valid[~old].all(axis=2)).astype(float) == 0
    old_net = (masks[:, old].astype(float) @ known[old].reshape(old.sum(), -1)).reshape(len(rules), len(POLICIES), 2).min(axis=2)
    new_net = (masks[:, ~old].astype(float) @ known[~old].reshape((~old).sum(), -1)).reshape(len(rules), len(POLICIES), 2)
    by_policy = {}
    for p, pol in enumerate(POLICIES):
        ids = np.flatnonzero(eligible[:, p])
        best_ev = max(ids, key=lambda i: (primary['observed'][i, p], secondary['observed'][i, p]))
        best_pr = max(ids, key=lambda i: (secondary['observed'][i, p], primary['observed'][i, p]))
        wf_ids = np.flatnonzero(elig_old[:, p] & elig_new[:, p] & (masks[:, old].sum(1) > 0))
        wf = max(wf_ids, key=lambda i: old_net[i, p])
        by_policy[pol] = dict(eligible_rules=int(len(ids)), strongest_evidence=prof(best_ev, p), maximum_profit=prof(best_pr, p),
                              qualifying=int(passing[:, p].sum()),
                              walk_forward=dict(rule=rules[wf], in_sample_net_usd_worst=old_net[wf, p] / 1e4,
                                                out_of_sample_net_usd=[new_net[wf, p, 0] / 1e4, new_net[wf, p, 1] / 1e4],
                                                in_sample_n=int(masks[wf, old].sum()), out_of_sample_n=int(masks[wf, ~old].sum())),
                              baseline_all_candidates={str(a): aggregate([r['outcomes'][f'{pol}|{a}'] for r in panel], len(panel)) for a in (10000, 0)})
    wf_ok = {pol: min(by_policy[pol]['walk_forward']['out_of_sample_net_usd']) > 0 for pol in POLICIES}
    go = [prof(i, p) for i, p in np.argwhere(passing) if wf_ok[POLICIES[p]]]
    rep = dict(status='COMPLETE', rules=len(rules), candidates=len(panel), blocks=names, enumerations=primary['enumerations'],
               permutations=9999, by_policy=by_policy, significant_rule_policy_pairs=int(passing.sum()),
               verdict='GO_IMPACT_SIGNAL_PASSES' if go else 'NO_GO_IMPACT_FACTOR_DOES_NOT_WORK_ON_THIS_DATA', qualifying=go)
    (out / 'impact_report.json').write_text(json.dumps(rep, indent=2, default=float) + '\n')
    np.save(out / 'rule_masks.npy', masks); (out / 'rule_family.json').write_text(json.dumps(rules) + '\n')
    for pol, d in by_policy.items():
        print(pol, 'rules', d['eligible_rules'], '| max-profit', round(d['maximum_profit']['net_usd_full_match'], 2), round(d['maximum_profit']['net_usd_zero_match'], 2),
              'n', d['maximum_profit']['selected'], 'adj p', round(d['maximum_profit']['primary_adj_p'], 3), round(d['maximum_profit']['secondary_adj_p'], 3),
              '| best-evidence adj p', round(d['strongest_evidence']['primary_adj_p'], 3), round(d['strongest_evidence']['secondary_adj_p'], 3),
              '| WF oos', d['walk_forward']['out_of_sample_net_usd'], flush=True)
    print('VERDICT', rep['verdict'])


if __name__ == '__main__':
    a = argparse.ArgumentParser(); a.add_argument('--output', type=Path, required=True); run(a.parse_args().output)
