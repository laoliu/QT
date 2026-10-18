# Experimental models

The default A-share research API supports LightGBM LambdaRank and the
ElasticNet baseline through `aipha.research.ResearchPipeline`.

This directory holds the opt-in GRU, GAT, TRA, MASTER Transformer, HIST,
DoubleEnsemble and multi-model ensemble implementations. Import the specific
module when running an experiment; importing this package loads none of them.
The deep models require their existing optional PyTorch/PyG dependencies.

`MLStockSelector` still requires `allow_experimental_models=True` at
construction and again at load time for historical experimental artifacts.
The canonical manifest loader and default API do not accept these model types.
Moving implementations here does not certify their training methodology or
make their artifacts compatible with canonical PIT/feature manifests.

Old `aipha.models.<module>` paths remain module aliases for existing Python
callers and serialized class qualifiers, including private wrapper classes.
They resolve to the same module object as the new path, so callers do not get
two incompatible copies of a class or separate monkeypatch state. New
serialization records the implementation's new module path. Old model files
are not rewritten or deleted by importing either path.

The offline boundary test checks default model listing, the strategy registry
and selector opt-in without permitting any experimental implementation or
optional deep-learning/XGBoost import. It also reads legacy-qualified pickle
configurations and a fitted DoubleEnsemble selector, checking identical
predictions and unchanged source bytes. Tests for optional models skip when
their dependencies are unavailable; that does not establish those models'
runtime compatibility on the current machine.
