"""Legacy import/pickle alias; implementation is in aipha.experiments.models."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("aipha.experiments.models.gru_ranker")
