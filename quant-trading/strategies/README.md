# Unified strategy catalog

`catalog.json` is the cross-language metadata index for every strategy exposed by
QT. A strategy implementation may remain in TypeScript, Backtrader, vn.py or
AIPHA; the catalog records the stable ID, runtime, market, timeframe, data
requirements, output format and lifecycle state used by the UI and routing
layer.

The runtime registry enriches legacy TypeScript definitions from this index and
the Python bridge returns the same fields for Python-only strategies through
`GET /quant/api/strategy-catalog`.

`aipha-ml-ranking` is the canonical low-frequency A-share research route. Its
output is `target_position`; all other legacy signal strategies remain available
as compatibility or experimental implementations until they are migrated and
validated against the canonical contract.
