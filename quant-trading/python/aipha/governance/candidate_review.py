"""候选策略适配审查卡数据模型与评审引擎.

四层治理体系的第二层（适配层）核心模块：
- CandidateReviewCard: 候选审查卡数据结构
- AdaptationRisk: 适配风险枚举
- EvidenceLevel: 证据等级 (L1/L2/L3)
- CandidateLayer: 候选所处层级 (知识/适配/验证/组合)
- ReviewVerdict: 审查裁决
- CandidateReviewEngine: 审查引擎（自动化约束检查）
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


class EvidenceLevel(str, Enum):
    """外部知识的证据等级."""

    L1 = "L1"  # 方法论线索，仅证明"值得研究"
    L2 = "L2"  # 参数/规则参考，值得做适配验证
    L3 = "L3"  # 经项目统一回测与执行校验，正式候选


class CandidateLayer(str, Enum):
    """候选所处的治理层级."""

    KNOWLEDGE = "knowledge"      # 知识层：外部线索
    ADAPTATION = "adaptation"    # 适配层：映射与约束检查
    VALIDATION = "validation"    # 验证层：统一回测与执行校验
    PORTFOLIO = "portfolio"      # 组合层：正式纳入组合
    REJECTED = "rejected"        # 已拒绝
    CLOSED = "closed"            # 已关闭（证伪或边际收益下降）


class AdaptationRisk(str, Enum):
    """适配风险等级."""

    LOW = "low"          # 可直接映射，无需修改
    MEDIUM = "medium"    # 需参数调整或数据补充
    HIGH = "high"        # 存在约束冲突或数据缺失
    BLOCKING = "blocking"  # 不可适配，需退回知识层


class ReviewVerdict(str, Enum):
    """审查裁决."""

    PASS = "pass"              # 通过，可进入下一层
    CONDITIONAL = "conditional"  # 有条件通过，需满足补充要求
    FAIL = "fail"              # 未通过，退回上一层
    DEFER = "defer"            # 暂缓，等待外部条件


@dataclass
class AdaptationCheckItem:
    """单项适配检查结果."""

    question: str        # 检查问题
    answer: str          # 回答
    risk: AdaptationRisk  # 风险等级
    detail: str = ""     # 补充说明
    related_cf: str = ""  # 关联的 CF-* binding 结论
    related_dr: str = ""  # 关联的 DR-* 决策规则
    mitigation: str = ""  # 缓解措施（若有）


@dataclass
class CandidateReviewCard:
    """候选策略适配审查卡.

    核心数据结构，记录一个外部策略/方法论从知识层
    到组合层的完整审查轨迹。
    """

    # === 身份信息 ===
    candidate_id: str          # 候选编号 (如 CB-R01, EQ-S03)
    name: str                  # 候选名称
    source: str                # 来源 (集思录/私募/论文/社区)
    source_doc: str = ""       # 来源文档路径
    created_date: str = ""     # 创建日期 YYYY-MM-DD
    updated_date: str = ""     # 最近更新日期

    # === 分类与定位 ===
    asset_type: str = ""       # 资产类型 (cb/a_stock/etf/cross_asset)
    tier: str = ""             # 目标层级 (tier_s/tier_l/both)
    strategy_type: str = ""    # 策略类型 (factor/event/ml/rule/hybrid)

    # === 证据与层级 ===
    evidence_level: str = "L1"       # EvidenceLevel
    current_layer: str = "knowledge"  # CandidateLayer

    # === 外部绩效摘要（声称值，未经项目验证） ===
    claimed_ar: Optional[float] = None  # 声称年化收益 %
    claimed_sr: Optional[float] = None  # 声称夏普比率
    claimed_mdd: Optional[float] = None  # 声称最大回撤 %
    claimed_period: str = ""            # 声称回测/实盘区间
    claimed_capacity: str = ""          # 声称容量

    # === 五项适配检查 ===
    checks: list[dict[str, Any]] = field(default_factory=list)

    # === 审查裁决 ===
    verdict: str = ""           # ReviewVerdict
    verdict_reason: str = ""    # 裁决理由
    reviewer: str = ""          # 审查人/角色
    review_date: str = ""       # 审查日期

    # === 数据依赖 ===
    required_data: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    data_feasibility: str = ""  # 数据可得性评估

    # === 执行约束 ===
    execution_risks: list[str] = field(default_factory=list)
    capacity_estimate: str = ""  # 容量估计
    slippage_sensitivity: str = ""  # 滑点敏感性

    # === 与现有主线的关系 ===
    overlap_with: list[str] = field(default_factory=list)  # 重叠的现有策略
    correlation_estimate: Optional[float] = None  # 预估相关性
    orthogonality_value: str = ""  # 正交性价值评估

    # === 后续行动 ===
    next_actions: list[str] = field(default_factory=list)
    blocking_issues: list[str] = field(default_factory=list)
    close_condition: str = ""   # 结束条件

    # === 审计轨迹 ===
    history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典."""
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateReviewCard:
        """从字典反序列化."""
        return cls(**{k: v for k, v in data.items()
                      if k in cls.__dataclass_fields__})

    @classmethod
    def from_json(cls, json_str: str) -> CandidateReviewCard:
        """从 JSON 字符串反序列化."""
        return cls.from_dict(json.loads(json_str))

    def add_history(self, action: str, detail: str = "") -> None:
        """追加审计轨迹."""
        self.history.append({
            "date": date.today().isoformat(),
            "action": action,
            "detail": detail,
        })
        self.updated_date = date.today().isoformat()

    def promote(self, to_layer: CandidateLayer, reason: str = "") -> None:
        """升级到下一层."""
        old = self.current_layer
        self.current_layer = to_layer.value
        self.add_history(
            f"promote: {old} → {to_layer.value}",
            reason,
        )

    def reject(self, reason: str) -> None:
        """拒绝候选."""
        self.verdict = ReviewVerdict.FAIL.value
        self.verdict_reason = reason
        self.current_layer = CandidateLayer.REJECTED.value
        self.add_history("rejected", reason)

    def close(self, reason: str) -> None:
        """关闭候选（证伪或边际收益下降）."""
        self.current_layer = CandidateLayer.CLOSED.value
        self.add_history("closed", reason)


# ============================================================
# 适配检查引擎
# ============================================================

# 五项标准适配检查问题
ADAPTATION_QUESTIONS = [
    "是否符合当前资产类型与交易制度边界？",
    "是否需要仓库中不存在或不可靠的数据？",
    "是否触犯已确认的 binding 结论 (CF-*)？",
    "是否与现有主线高度重叠（日收益相关 > 0.75）？",
    "是否存在明显的执行/容量不可行性？",
]


class CandidateReviewEngine:
    """候选策略适配审查引擎.

    自动化执行五项适配检查，并基于 binding 结论
    和决策规则做约束对照。
    """

    def __init__(
        self,
        binding_conclusions: Optional[dict[str, dict]] = None,
        decision_rules: Optional[dict[str, dict]] = None,
    ) -> None:
        self._bindings = binding_conclusions or {}
        self._rules = decision_rules or {}

    def check_asset_boundary(
        self, card: CandidateReviewCard,
    ) -> AdaptationCheckItem:
        """Q1: 是否符合资产与制度边界."""
        asset = card.asset_type.lower() if card.asset_type else ""
        supported = {"cb", "convertible_bond", "a_stock", "cn_equity", "etf"}
        if not asset:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[0],
                answer="未指定资产类型",
                risk=AdaptationRisk.HIGH,
                detail="candidate.asset_type 为空",
            )
        if asset not in supported:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[0],
                answer=f"资产类型 '{asset}' 不在支持范围",
                risk=AdaptationRisk.BLOCKING,
                detail=f"当前支持: {sorted(supported)}",
            )
        return AdaptationCheckItem(
            question=ADAPTATION_QUESTIONS[0],
            answer=f"资产类型 '{asset}' 在支持范围内",
            risk=AdaptationRisk.LOW,
        )

    def check_data_availability(
        self, card: CandidateReviewCard,
    ) -> AdaptationCheckItem:
        """Q2: 是否需要不存在的数据."""
        if card.missing_data:
            risk = (
                AdaptationRisk.BLOCKING
                if len(card.missing_data) >= 3
                else AdaptationRisk.HIGH
            )
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[1],
                answer=f"缺失 {len(card.missing_data)} 项数据",
                risk=risk,
                detail=", ".join(card.missing_data),
                mitigation="需评估数据补全路径与成本",
            )
        if card.required_data:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[1],
                answer="所需数据已确认可得",
                risk=AdaptationRisk.LOW,
            )
        return AdaptationCheckItem(
            question=ADAPTATION_QUESTIONS[1],
            answer="数据需求未明确",
            risk=AdaptationRisk.MEDIUM,
            detail="required_data 与 missing_data 均为空",
        )

    def check_binding_violations(
        self, card: CandidateReviewCard,
    ) -> AdaptationCheckItem:
        """Q3: 是否触犯 binding 结论."""
        violations: list[str] = []
        asset = card.asset_type.lower() if card.asset_type else ""

        # CB 容量约束 CF-001
        if asset in ("cb", "convertible_bond"):
            if card.claimed_capacity:
                try:
                    cap_val = float(
                        card.claimed_capacity
                        .replace("万", "").replace(",", "").strip()
                    )
                    if cap_val > 200:
                        violations.append(
                            f"CF-001: CB 容量 {cap_val}万 > 200万上限"
                        )
                except (ValueError, AttributeError):
                    pass

        # 声称 AR 过高需要额外审查
        if card.claimed_ar and card.claimed_ar > 40:
            violations.append(
                f"CF-015 风险: 声称 AR={card.claimed_ar}% 过高，"
                "需排除 IC Trap"
            )

        if violations:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[2],
                answer=f"发现 {len(violations)} 项潜在违反",
                risk=AdaptationRisk.HIGH,
                detail="; ".join(violations),
                related_cf=", ".join(
                    v.split(":")[0] for v in violations
                ),
            )
        return AdaptationCheckItem(
            question=ADAPTATION_QUESTIONS[2],
            answer="未发现明显 binding 违反",
            risk=AdaptationRisk.LOW,
        )

    def check_overlap(
        self, card: CandidateReviewCard,
    ) -> AdaptationCheckItem:
        """Q4: 是否与现有主线高度重叠."""
        if card.correlation_estimate is not None:
            if card.correlation_estimate > 0.75:
                return AdaptationCheckItem(
                    question=ADAPTATION_QUESTIONS[3],
                    answer=(
                        f"预估相关性 {card.correlation_estimate:.2f} > 0.75"
                    ),
                    risk=AdaptationRisk.HIGH,
                    detail=f"重叠策略: {card.overlap_with}",
                    related_dr="DR-COMBO-001",
                    mitigation="需证明组合层净增益或降低权重",
                )
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[3],
                answer=(
                    f"预估相关性 {card.correlation_estimate:.2f} ≤ 0.75"
                ),
                risk=AdaptationRisk.LOW,
            )
        if card.overlap_with:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[3],
                answer="有潜在重叠但相关性未量化",
                risk=AdaptationRisk.MEDIUM,
                detail=f"重叠策略: {card.overlap_with}",
            )
        return AdaptationCheckItem(
            question=ADAPTATION_QUESTIONS[3],
            answer="未发现明显重叠",
            risk=AdaptationRisk.LOW,
        )

    def check_execution_feasibility(
        self, card: CandidateReviewCard,
    ) -> AdaptationCheckItem:
        """Q5: 是否存在执行/容量不可行性."""
        risks = card.execution_risks or []
        blocking = [r for r in risks if "blocking" in r.lower()]
        if blocking:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[4],
                answer=f"{len(blocking)} 项阻塞性执行风险",
                risk=AdaptationRisk.BLOCKING,
                detail="; ".join(blocking),
                related_dr="DR-EXEC-001",
            )
        if risks:
            return AdaptationCheckItem(
                question=ADAPTATION_QUESTIONS[4],
                answer=f"{len(risks)} 项执行风险（非阻塞）",
                risk=AdaptationRisk.MEDIUM,
                detail="; ".join(risks),
            )
        return AdaptationCheckItem(
            question=ADAPTATION_QUESTIONS[4],
            answer="未发现明显执行风险",
            risk=AdaptationRisk.LOW,
        )

    def run_adaptation_review(
        self, card: CandidateReviewCard,
    ) -> CandidateReviewCard:
        """执行完整的五项适配审查，更新卡片."""
        checks = [
            self.check_asset_boundary(card),
            self.check_data_availability(card),
            self.check_binding_violations(card),
            self.check_overlap(card),
            self.check_execution_feasibility(card),
        ]
        card.checks = [asdict(c) for c in checks]

        # 自动裁决
        blocking = [c for c in checks if c.risk == AdaptationRisk.BLOCKING]
        high = [c for c in checks if c.risk == AdaptationRisk.HIGH]

        if blocking:
            card.verdict = ReviewVerdict.FAIL.value
            card.verdict_reason = (
                f"{len(blocking)} 项阻塞性风险: "
                + "; ".join(c.answer for c in blocking)
            )
        elif len(high) >= 2:
            card.verdict = ReviewVerdict.FAIL.value
            card.verdict_reason = (
                f"{len(high)} 项高风险（≥2 即失败）: "
                + "; ".join(c.answer for c in high)
            )
        elif high:
            card.verdict = ReviewVerdict.CONDITIONAL.value
            card.verdict_reason = (
                f"1 项高风险需缓解: {high[0].answer}"
            )
        else:
            card.verdict = ReviewVerdict.PASS.value
            card.verdict_reason = "五项检查均为低/中风险"

        card.review_date = date.today().isoformat()
        card.add_history(
            f"adaptation_review: {card.verdict}",
            card.verdict_reason,
        )
        return card


# ============================================================
# 验证层验收矩阵
# ============================================================

@dataclass
class ValidationCheckItem:
    """验证层单项检查结果."""

    category: str       # 检查类别
    item: str           # 检查项
    required: bool      # 是否必需
    passed: Optional[bool] = None  # 是否通过
    value: str = ""     # 实际值
    threshold: str = "" # 门槛值
    note: str = ""      # 备注


# Tier S (≤1000万) 门槛
TIER_S_THRESHOLDS = {
    "ar_min": 20.0,         # 年化收益 ≥20%
    "mdd_max": 30.0,        # 最大回撤 ≤30%
    "sr_min": 0.8,          # 夏普 ≥0.8
    "calmar_min": 0.6,      # 卡尔马 ≥0.6
    "wf_oos_is_decay": 0.5, # IS→OOS 衰减 ≤50%
    "pbo_max": 0.50,        # PBO < 0.50
    "param_stability": 0.3, # 核心参数±20%内夏普变化≤30%
}

# Tier L (≥1亿) 门槛
TIER_L_THRESHOLDS = {
    "ar_min": 15.0,
    "mdd_max": 25.0,
    "sr_min": 0.8,
    "calmar_min": 0.6,
    "wf_oos_is_decay": 0.5,
    "pbo_max": 0.50,
    "param_stability": 0.3,
}


@dataclass
class ValidationMatrix:
    """验证层验收矩阵.

    统一验收框架：所有候选不因来源不同而改变标准。
    覆盖 P2 配置核验、P9 分段验证、P10 五原则、统计验证、
    执行压力测试、容量与滑点灵敏度。
    """

    candidate_id: str
    tier: str = "tier_s"  # tier_s | tier_l
    validation_date: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    overall_passed: Optional[bool] = None
    fail_reasons: list[str] = field(default_factory=list)

    def get_thresholds(self) -> dict[str, float]:
        """获取当前 Tier 的门槛."""
        if self.tier == "tier_l":
            return TIER_L_THRESHOLDS
        return TIER_S_THRESHOLDS

    def build_checklist(self) -> list[ValidationCheckItem]:
        """构建标准验收清单."""
        t = self.get_thresholds()
        return [
            # --- P2 配置核验 ---
            ValidationCheckItem("P2", "佣金率与市场一致", True),
            ValidationCheckItem("P2", "印花税率与时变模型一致", True),
            ValidationCheckItem("P2", "滑点模型与参数已指定", True),
            ValidationCheckItem("P2", "成交模型类型正确", True),
            ValidationCheckItem("P2", "Universe 规则完整", True),
            ValidationCheckItem("P2", "数据源与复权方式正确", True),
            # --- P4 绩效门槛 ---
            ValidationCheckItem(
                "P4", f"年化收益 ≥ {t['ar_min']}%", True,
                threshold=f"{t['ar_min']}%",
            ),
            ValidationCheckItem(
                "P4", f"最大回撤 ≤ {t['mdd_max']}%", True,
                threshold=f"{t['mdd_max']}%",
            ),
            ValidationCheckItem(
                "P4", f"夏普比率 ≥ {t['sr_min']}", True,
                threshold=f"{t['sr_min']}",
            ),
            ValidationCheckItem(
                "P4", f"卡尔马比率 ≥ {t['calmar_min']}", True,
                threshold=f"{t['calmar_min']}",
            ),
            # --- P8 统计验证 ---
            ValidationCheckItem(
                "P8", f"PBO < {t['pbo_max']}", True,
                threshold=f"{t['pbo_max']}",
            ),
            ValidationCheckItem("P8", "DSR 显著 (p < 0.05)", True),
            ValidationCheckItem("P8", "HLZ 校正后 t_adj > 2.0", True),
            ValidationCheckItem(
                "P8",
                f"WF OOS/IS 衰减 ≤ {t['wf_oos_is_decay']*100}%",
                True,
                threshold=f"{t['wf_oos_is_decay']*100}%",
            ),
            # --- P9 分段验证 ---
            ValidationCheckItem(
                "P9", "全量期绩效已报告", True,
            ),
            ValidationCheckItem(
                "P9", "制度变更后分段绩效已报告", True,
            ),
            ValidationCheckItem(
                "P9", "剔除最高年后 SR > 0.6", True,
            ),
            ValidationCheckItem(
                "P9", "近3年滚动SR ≥ 全量期SR × 40%", True,
            ),
            # --- P10 五原则 ---
            ValidationCheckItem(
                "P10", "原则A: 经济理论支撑", True,
            ),
            ValidationCheckItem(
                "P10", "原则B: 持续稳定规律", True,
            ),
            ValidationCheckItem(
                "P10", "原则C: 主动风险有统计依据", True,
            ),
            ValidationCheckItem(
                "P10", "原则D: 容量与执行可行性", True,
            ),
            ValidationCheckItem(
                "P10", "原则E: 组合分散化约束", True,
            ),
            # --- 执行压力测试 ---
            ValidationCheckItem(
                "EXEC", "滑点 2x 后 SR > 0.8", True,
            ),
            ValidationCheckItem(
                "EXEC", "滑点 4x 后 SR > 0.6", True,
            ),
            ValidationCheckItem(
                "EXEC", "资金档扩展测试已完成", True,
            ),
            ValidationCheckItem(
                "EXEC", "容量瓶颈标的已识别", True,
            ),
            ValidationCheckItem(
                "EXEC", "执行失败率 < 5%", True,
            ),
            # --- 实验可复现 ---
            ValidationCheckItem(
                "P16", "实验指纹已记录", True,
            ),
            ValidationCheckItem(
                "P16", "配置可复跑", True,
            ),
        ]

    def evaluate(
        self, results: list[ValidationCheckItem],
    ) -> bool:
        """评估验收矩阵，返回是否整体通过."""
        self.items = [asdict(r) for r in results]
        self.validation_date = date.today().isoformat()
        self.fail_reasons = []

        for r in results:
            if r.required and r.passed is False:
                self.fail_reasons.append(
                    f"[{r.category}] {r.item}: {r.value or '未通过'}"
                )

        # 未评估的必需项也视为失败
        unevaluated = [
            r for r in results
            if r.required and r.passed is None
        ]
        if unevaluated:
            for r in unevaluated:
                self.fail_reasons.append(
                    f"[{r.category}] {r.item}: 未评估"
                )

        self.overall_passed = len(self.fail_reasons) == 0
        return self.overall_passed

    def to_dict(self) -> dict[str, Any]:
        """序列化."""
        return asdict(self)


# ============================================================
# 组合层准入与退出规则
# ============================================================

@dataclass
class PortfolioEntryRule:
    """组合层单项准入规则."""

    rule_id: str
    description: str
    threshold: str
    hard_gate: bool = True  # True=阻断, False=告警


# 组合层准入规则集
PORTFOLIO_ENTRY_RULES: list[PortfolioEntryRule] = [
    PortfolioEntryRule(
        "PE-001",
        "候选必须通过验证层（ValidationMatrix.overall_passed=True）",
        "overall_passed == True",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-002",
        "与组合内任意策略日收益相关 < 0.75",
        "max_pair_corr < 0.75",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-003",
        "组合平均相关系数 < 0.60",
        "avg_pair_corr < 0.60",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-004",
        "单策略风险贡献 ≤ 50%",
        "max_risk_contribution <= 0.50",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-005",
        "容量估算 ≥ 分配金额",
        "capacity >= allocation",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-006",
        "组合最大回撤恢复时间 ≤ 12 个月",
        "max_recovery_months <= 12",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-007",
        "CVaR(95%) < 3.0%",
        "cvar_95 < 0.03",
        hard_gate=True,
    ),
    PortfolioEntryRule(
        "PE-008",
        "CB 同资产策略相关 < 0.65 且 Jaccard ≤ 0.30",
        "cb_pair_corr < 0.65, jaccard <= 0.30",
        hard_gate=True,
    ),
]


@dataclass
class PortfolioExitRule:
    """组合层退出规则."""

    rule_id: str
    description: str
    trigger: str
    action: str


# 组合层退出规则集
PORTFOLIO_EXIT_RULES: list[PortfolioExitRule] = [
    PortfolioExitRule(
        "PX-001",
        "滚动12月SR连续 < 0 超6月",
        "rolling_12m_sr < 0 for 6 consecutive months",
        "强制审查，考虑退出",
    ),
    PortfolioExitRule(
        "PX-002",
        "近3年滚动SR低于全量期SR的40%",
        "rolling_3y_sr < full_sr * 0.40",
        "风险降级，权重减半",
    ),
    PortfolioExitRule(
        "PX-003",
        "结构性衰减证据（非政策周期性）",
        "structural_decay_confirmed == True",
        "退出组合 (DR-DECAY-001)",
    ),
    PortfolioExitRule(
        "PX-004",
        "容量不足（瓶颈标的流动性持续恶化）",
        "capacity < allocation * 0.5",
        "降权或退出",
    ),
    PortfolioExitRule(
        "PX-005",
        "与新增策略相关性过高（> 0.75）",
        "new_pair_corr > 0.75",
        "保留更优策略，退出较弱一方",
    ),
    PortfolioExitRule(
        "PX-006",
        "连续3月执行失败率 > 10%",
        "exec_fail_rate > 0.10 for 3 months",
        "暂停并排查执行约束",
    ),
]


@dataclass
class PortfolioGateResult:
    """组合层准入检查结果."""

    candidate_id: str
    check_date: str = ""
    entry_checks: list[dict[str, Any]] = field(default_factory=list)
    all_passed: bool = False
    block_reasons: list[str] = field(default_factory=list)

    def check_entry(
        self,
        max_pair_corr: float,
        avg_pair_corr: float,
        max_risk_contribution: float,
        capacity: float,
        allocation: float,
        max_recovery_months: int,
        cvar_95: float,
        cb_pair_corr: Optional[float] = None,
        jaccard: Optional[float] = None,
        validation_passed: bool = False,
    ) -> bool:
        """执行组合层准入检查."""
        self.check_date = date.today().isoformat()
        self.block_reasons = []
        checks = {}

        # PE-001
        checks["PE-001"] = validation_passed
        if not validation_passed:
            self.block_reasons.append("PE-001: 验证层未通过")

        # PE-002
        checks["PE-002"] = max_pair_corr < 0.75
        if not checks["PE-002"]:
            self.block_reasons.append(
                f"PE-002: max_pair_corr={max_pair_corr:.3f} ≥ 0.75"
            )

        # PE-003
        checks["PE-003"] = avg_pair_corr < 0.60
        if not checks["PE-003"]:
            self.block_reasons.append(
                f"PE-003: avg_pair_corr={avg_pair_corr:.3f} ≥ 0.60"
            )

        # PE-004
        checks["PE-004"] = max_risk_contribution <= 0.50
        if not checks["PE-004"]:
            self.block_reasons.append(
                f"PE-004: risk_contrib={max_risk_contribution:.3f} > 0.50"
            )

        # PE-005
        checks["PE-005"] = capacity >= allocation
        if not checks["PE-005"]:
            self.block_reasons.append(
                f"PE-005: capacity={capacity:.0f} < allocation={allocation:.0f}"
            )

        # PE-006
        checks["PE-006"] = max_recovery_months <= 12
        if not checks["PE-006"]:
            self.block_reasons.append(
                f"PE-006: recovery={max_recovery_months}月 > 12月"
            )

        # PE-007
        checks["PE-007"] = cvar_95 < 0.03
        if not checks["PE-007"]:
            self.block_reasons.append(
                f"PE-007: CVaR(95%)={cvar_95:.4f} ≥ 0.03"
            )

        # PE-008 (仅 CB 相关)
        if cb_pair_corr is not None:
            passed_008 = cb_pair_corr < 0.65
            if jaccard is not None:
                passed_008 = passed_008 and jaccard <= 0.30
            checks["PE-008"] = passed_008
            if not passed_008:
                self.block_reasons.append(
                    f"PE-008: cb_corr={cb_pair_corr:.3f}, "
                    f"jaccard={jaccard}"
                )

        self.entry_checks = [
            {"rule": k, "passed": v} for k, v in checks.items()
        ]
        self.all_passed = len(self.block_reasons) == 0
        return self.all_passed
