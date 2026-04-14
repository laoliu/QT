"""Deep analysis of all mined factor versions."""
import json, re, numpy as np
from pathlib import Path

for ver in ['auto_mining_v5', 'auto_mining_v6']:
    data = json.loads(Path(f'output/{ver}/mutation_kb.json').read_text('utf-8'))
    passed = [e for e in data if e.get('passed')]
    if not passed:
        continue
    print(f'=== {ver} ({len(passed)} passed) ===')
    
    icirs = [abs(e['icir']) for e in passed]
    print(f'ICIR: min={min(icirs):.3f}, median={np.median(icirs):.3f}, max={max(icirs):.3f}')
    print(f'  ICIR >= 1.0: {sum(1 for x in icirs if x >= 1.0)}/{len(icirs)}')
    
    field_counts = {}
    for e in passed:
        for m in re.finditer(r'\$(\w+)', e['expression']):
            f = m.group(1)
            field_counts[f] = field_counts.get(f, 0) + 1
    total_fields = sum(field_counts.values())
    top_fields = sorted(field_counts.items(), key=lambda x: -x[1])[:8]
    print(f'Field dist ({total_fields} refs):')
    for f, c in top_fields:
        print(f'  {f}: {c} ({c/total_fields:.0%})')
    
    methods = {}
    for e in passed:
        m = e.get('method', 'unknown')
        methods[m] = methods.get(m, 0) + 1
    print(f'Method dist: {dict(sorted(methods.items(), key=lambda x: -x[1]))}')
    
    post_ics = [e.get('post_ic', 0) for e in passed if e.get('post_ic') is not None]
    if post_ics:
        pos_pct = sum(1 for x in post_ics if x > 0) / len(post_ics)
        print(f'Post-IC (2022+): mean={np.mean(post_ics):.4f}, pos%={pos_pct:.0%}')
    
    fits = [e.get('fitness', 0) for e in passed if e.get('fitness')]
    if fits:
        print(f'Fitness: min={min(fits):.3f}, median={np.median(fits):.3f}, max={max(fits):.3f}')
    
    print(f'\nTop-10 by |ICIR|:')
    sorted_passed = sorted(passed, key=lambda e: abs(e['icir']), reverse=True)[:10]
    for i, e in enumerate(sorted_passed):
        expr_short = e['expression'][:65]
        post = e.get('post_ic', 0)
        fit = e.get('fitness', 0)
        print(f'  {i+1}. ICIR={e["icir"]:+.3f} post={post:+.4f} fit={fit:.3f} {expr_short}')
    
    # IC correlation matrix among top factors
    print(f'\nField category breakdown (passed):')
    price_f = ['close', 'open', 'high', 'low']
    vol_f = ['volume', 'amount']
    cats = {'pure_price': 0, 'pure_volume': 0, 'pure_fund': 0, 
            'price+volume': 0, 'price+fund': 0, 'vol+fund': 0, 'cross3': 0}
    for e in passed:
        fields = set(m.group(1) for m in re.finditer(r'\$(\w+)', e['expression']))
        has_price = bool(fields & set(price_f))
        has_vol = bool(fields & set(vol_f))
        has_fund = bool(fields - set(price_f) - set(vol_f))
        key = 'cross3' if sum([has_price, has_vol, has_fund]) >= 3 else \
              'price+volume' if has_price and has_vol else \
              'price+fund' if has_price and has_fund else \
              'vol+fund' if has_vol and has_fund else \
              'pure_price' if has_price else \
              'pure_volume' if has_vol else 'pure_fund'
        cats[key] = cats.get(key, 0) + 1
    for k, v in sorted(cats.items(), key=lambda x: -x[1]):
        if v > 0:
            print(f'  {k}: {v} ({v/len(passed):.0%})')
    print()
