# historical configs archive

本目录用于存放有明确阶段背景、主要服务于历史里程碑或阶段性评审、且当前仓库内无运行代码直接引用的配置文件。

归档原则：

- 优先归档带有阶段编号、里程碑编号或一次性试验背景的历史配置
- 迁移前先检查 `scripts/`、`src/`、`tests/` 中是否存在直接文件路径引用
- 若 `docs/` 中存在引用，迁移后需同步更新文档路径
- 仍可能被当前运行入口使用的配置，不应归档到此目录

2026-03-28 已归档：

- `cross_asset_combo_r71.yml`
- `cross_asset_combo.yml`
- `candidate_pool.yml`

说明：

- `cross_asset_combo_r71.yml` 属于 R71 阶段性跨资产组合升级配置，当前仅保留审计与文档追溯价值。
- `cross_asset_combo.yml` 属于早期跨资产组合方案配置，当前仓库内无运行代码直接读取，主要用于历史评审与方案追溯。
- `candidate_pool.yml` 属于 R58-R99 时代的因子/策略/组合备选池注册表（691 行），无运行代码引用，仅文档追溯。
