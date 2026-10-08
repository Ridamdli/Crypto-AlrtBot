import os
import json
import statistics

reports_dir = 'reports'
results = []

for f in sorted(os.listdir(reports_dir)):
    if f.startswith('research_') and f.endswith('.json') and '_regime_' in f:
        with open(os.path.join(reports_dir, f), 'r') as fp:
            data = json.load(fp)
            results.append(data)

# 1. Regime-aware vs Regime-neutral comparison
print("=" * 80)
print("REGIME-AWARE vs REGIME-NEUTRAL COMPARISON")
print("=" * 80)
print("\n| Symbol | Strategy | ExpR Aware | ExpR Neutral | Diff | PF Aware | PF Neutral | WinR Aware | WinR Neutral | Evals Aware | Evals Neutral |")
print("|--------|----------|------------|--------------|------|----------|------------|------------|--------------|-------------|---------------|")

for r in sorted(results, key=lambda x: (x['symbol'], x['strategy'])):
    m = r['metrics']
    pass

# Group by symbol/strategy
from collections import defaultdict
grouped = defaultdict(dict)
for r in results:
    key = (r['symbol'], r['strategy'])
    grouped[key][r['regime_condition']] = r

for (sym, strat), conds in sorted(grouped.items()):
    if 'regime_aware' in conds and 'regime_neutral' in conds:
        a = conds['regime_aware']
        n = conds['regime_neutral']
        ma = a['metrics']
        mn = n['metrics']
        exp_r_diff = ma.get('expected_r', 0) - mn.get('expected_r', 0)
        pf_diff = ma.get('profit_factor', 0) - mn.get('profit_factor', 0)
        wr_diff = ma.get('win_rate', 0) - mn.get('win_rate', 0)
        evals_diff = a['eval_count'] - n['eval_count']
        print(f'| {sym} | {strat} | {ma.get("expected_r",0):.3f} | {mn.get("expected_r",0):.3f} | {exp_r_diff:+.3f} | {ma.get("profit_factor",0):.2f} | {mn.get("profit_factor",0):.2f} | {ma.get("win_rate",0):.3f} | {mn.get("win_rate",0):.3f} | {a["eval_count"]} | {n["eval_count"]} |')

# 2. Identify identical results (regime filter made no difference)
print("\n\nIDENTICAL RESULTS (Regime filter had no effect):")
identical_count = 0
for (sym, strat), conds in sorted(grouped.items()):
    if 'regime_aware' in conds and 'regime_neutral' in conds:
        a = conds['regime_aware']
        n = conds['regime_neutral']
        if (a['eval_count'] == n['eval_count'] and 
            abs(a['metrics'].get('expected_r',0) - n['metrics'].get('expected_r',0)) < 0.001 and
            abs(a['metrics'].get('profit_factor',0) - n['metrics'].get('profit_factor',0)) < 0.001):
            identical_count += 1
            print(f'  {sym} | {strat} - IDENTICAL (evals={a["eval_count"]})')
print(f"Total identical: {identical_count}/48 pairs")

# 3. Best strategies by symbol (regime-aware)
print("\n\nBEST STRATEGIES BY SYMBOL (Regime-Aware, by Expected R):")
for sym in sorted(set(r['symbol'] for r in results)):
    sym_results = [r for r in results if r['symbol'] == sym and r['regime_condition'] == 'regime_aware']
    sym_results.sort(key=lambda x: x['metrics'].get('expected_r', -999), reverse=True)
    print(f"\n{sym}:")
    for r in sym_results:
        m = r['metrics']
        print(f"  {r['strategy']:25s} ExpR={m.get('expected_r',0):+6.3f} PF={m.get('profit_factor',0):.2f} WR={m.get('win_rate',0):.3f} Evals={r['eval_count']:3d} Sig/Day={r['eval_count']/(r['train_timestamps']/96.0):.2f}")

# 4. Cross-strategy analysis
print("\n\nCROSS-STRATEGY ANALYSIS (Regime-Aware):")
for strat in ['bollinger_atr', 'donchian', 'ema_trend', 'momentum', 'rsi_mean_reversion', 'volume_breakout']:
    strat_results = [r for r in results if r['strategy'] == strat and r['regime_condition'] == 'regime_aware']
    exp_r_vals = [r['metrics'].get('expected_r', 0) for r in strat_results]
    pf_vals = [r['metrics'].get('profit_factor', 0) for r in strat_results]
    wr_vals = [r['metrics'].get('win_rate', 0) for r in strat_results]
    eval_counts = [r['eval_count'] for r in strat_results]
    pos_exp = sum(1 for v in exp_r_vals if v > 0)
    pos_pf = sum(1 for v in pf_vals if v > 1)
    print(f"\n{strat}:")
    print(f"  Symbols tested: {len(strat_results)}")
    print(f"  Exp R: mean={statistics.mean(exp_r_vals):.3f}, median={statistics.median(exp_r_vals):.3f}, min={min(exp_r_vals):.3f}, max={max(exp_r_vals):.3f}")
    print(f"  Profit Factor: mean={statistics.mean(pf_vals):.2f}, median={statistics.median(pf_vals):.2f}, min={min(pf_vals):.2f}, max={max(pf_vals):.2f}")
    print(f"  Win Rate: mean={statistics.mean(wr_vals):.3f}, median={statistics.median(wr_vals):.3f}")
    print(f"  Positive Exp R: {pos_exp}/{len(strat_results)} symbols")
    print(f"  PF > 1: {pos_pf}/{len(strat_results)} symbols")
    print(f"  Evals range: {min(eval_counts)} - {max(eval_counts)}")

# 5. Signal frequency analysis
print("\n\nSIGNAL FREQUENCY ANALYSIS (Regime-Aware):")
for strat in ['bollinger_atr', 'donchian', 'ema_trend', 'momentum', 'rsi_mean_reversion', 'volume_breakout']:
    strat_results = [r for r in results if r['strategy'] == strat and r['regime_condition'] == 'regime_aware']
    sig_per_day = [r['eval_count'] / (r['train_timestamps'] / 96.0) for r in strat_results]
    print(f"\n{strat}:")
    print(f"  Signals/day: mean={statistics.mean(sig_per_day):.2f}, median={statistics.median(sig_per_day):.2f}, range={min(sig_per_day):.2f}-{max(sig_per_day):.2f}")

# 6. Count strategies with positive expected R
print("\n\nSTRATEGIES WITH POSITIVE EXPECTED R (Regime-Aware):")
positive_strats = defaultdict(list)
for r in results:
    if r['regime_condition'] == 'regime_aware' and r['metrics'].get('expected_r', 0) > 0:
        positive_strats[r['strategy']].append((r['symbol'], r['metrics'].get('expected_r', 0)))

for strat, symbols in sorted(positive_strats.items()):
    print(f"  {strat}:")
    for sym, exp_r in sorted(symbols, key=lambda x: -x[1]):
        print(f"    {sym}: ExpR={exp_r:.3f}")