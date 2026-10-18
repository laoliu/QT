"""Legacy import/pickle alias; use aipha.research for canonical ML research."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("aipha.experiments.models.legacy_lgbm_ranker")
