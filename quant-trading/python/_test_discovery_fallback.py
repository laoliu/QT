import json, sys
sys.path.insert(0, '.')
from scripts.stock_discovery import _load_universe_from_spot
recs = _load_universe_from_spot('csi300')
print('Records:', len(recs))
if recs:
    print('Sample:', recs[0])
    print('Has _from_cache:', recs[0].get('_from_cache'))
