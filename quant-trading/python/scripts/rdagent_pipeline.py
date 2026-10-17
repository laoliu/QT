#!/usr/bin/env python
"""RD-Agent 因子挖掘管道对接脚本。

职责:
1. 读取 RD-Agent fin_factor 输出目录中的因子
2. 通过 ingest_rdagent_output() 导入因子候选
3. 通过 IC Trap Guard 5 阶段管道验证
4. 导出验证报告和通过验证的因子列表
5. (可选) 直接调用 DeepSeek API 生成新因子并验证

用法:
    # 从 RD-Agent 输出导入并验证
    python scripts/rdagent_pipeline.py --input /path/to/rdagent/output

    # 直接 LLM 挖掘模式 (需要 DEEPSEEK_API_KEY)
    python scripts/rdagent_pipeline.py --mode llm --direction 价量动量

    # 手动表达式验证
    python scripts/rdagent_pipeline.py --mode manual --expr "cs_rank(ts_mean($close, 20))"
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from aipha.utils.paths import project_path

from aipha.factors.llm_factor_miner import (
    CampaignResult,
    CandidateSource,
    FactorCandidate,
    LLMFactorMiner,
    MiningConfig,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("rdagent_pipeline")


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    p = argparse.ArgumentParser(
        description="RD-Agent 因子挖掘管道对接",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--mode", choices=["rdagent", "llm", "manual"], default="rdagent",
        help="运行模式: rdagent=导入输出, llm=直接挖掘, manual=手动验证",
    )
    p.add_argument("--input", "-i", default="", help="RD-Agent 输出目录路径")
    p.add_argument("--direction", default="价量动量", help="LLM 挖掘方向")
    p.add_argument("--n-factors", type=int, default=10, help="LLM 每轮生成因子数")
    p.add_argument("--expr", default="", help="手动模式: 因子表达式")
    p.add_argument("--name", default="manual_factor", help="手动模式: 因子名称")
    p.add_argument(
        "--output", "-o", default="output/rdagent_pipeline",
        help="结果输出目录",
    )
    p.add_argument("--api-key", default="", help="DeepSeek API Key (覆盖环境变量)")
    return p.parse_args()


def run_rdagent_ingest(miner: LLMFactorMiner, input_dir: str) -> CampaignResult:
    """模式 1: 从 RD-Agent 输出目录导入并验证。"""
    logger.info("=== RD-Agent 输出导入模式 ===")
    logger.info("输入目录: %s", input_dir)

    if not Path(input_dir).exists():
        logger.error("目录不存在: %s", input_dir)
        sys.exit(1)

    # 导入因子候选
    candidates = miner.ingest_rdagent_output(input_dir)
    if not candidates:
        logger.warning("未从 RD-Agent 输出中发现因子候选")
        # 尝试递归查找 factor.py
        factor_files = list(Path(input_dir).rglob("factor.py"))
        logger.info("递归搜索发现 %d 个 factor.py 文件", len(factor_files))
        for fp in factor_files[:20]:
            expr = miner._extract_expression_from_rdagent(fp)
            if expr:
                candidates.append(FactorCandidate(
                    name=fp.parent.name,
                    expression=expr,
                    source=CandidateSource.RDAGENT,
                    metadata={"dir": str(fp.parent)},
                ))

    logger.info("共发现 %d 个因子候选", len(candidates))
    if not candidates:
        logger.error("无可验证的因子候选, 退出")
        sys.exit(1)

    # 运行验证战役 (无因子值时仅做解析+AST去重)
    result = miner.run_campaign(candidates, campaign_id="rdagent_ingest")
    return result


def run_llm_mining(miner: LLMFactorMiner, direction: str, n_factors: int) -> CampaignResult:
    """模式 2: 直接 LLM 挖掘模式。"""
    logger.info("=== 直接 LLM 挖掘模式 ===")
    logger.info("方向: %s, 数量: %d", direction, n_factors)

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key or api_key.startswith("YOUR_"):
        logger.error(
            "DEEPSEEK_API_KEY 未设置. "
            "请设置环境变量或通过 --api-key 参数传入."
        )
        sys.exit(1)

    # 生成 prompt
    prompt = miner.generate_mining_prompt(direction=direction, n_factors=n_factors)
    logger.info("Prompt 长度: %d 字符", len(prompt))

    # 调用 DeepSeek API
    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
        )
        logger.info("调用 DeepSeek API (deepseek-chat)...")
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "system", "content": "你是量化因子研究专家。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=4096,
        )
        llm_text = response.choices[0].message.content or ""
        logger.info("LLM 返回 %d 字符", len(llm_text))
    except Exception as e:
        logger.error("DeepSeek API 调用失败: %s", e)
        sys.exit(1)

    # 解析 LLM 响应
    candidates = miner.parse_llm_response(llm_text)
    logger.info("解析出 %d 个因子候选", len(candidates))

    # 保存原始 LLM 响应
    llm_log_dir = project_path("output/rdagent_pipeline/llm_responses")
    llm_log_dir.mkdir(parents=True, exist_ok=True)
    import time
    ts = int(time.time())
    with open(llm_log_dir / f"response_{ts}.txt", "w", encoding="utf-8") as f:
        f.write(f"# Direction: {direction}\n# N: {n_factors}\n\n")
        f.write(llm_text)
    logger.info("LLM 响应已保存: %s", llm_log_dir / f"response_{ts}.txt")

    # 运行验证 (无因子值时仅做解析+AST去重)
    result = miner.run_campaign(candidates, campaign_id=f"llm_{direction}_{ts}")
    return result


def run_manual_validation(miner: LLMFactorMiner, name: str, expr: str) -> CampaignResult:
    """模式 3: 手动表达式验证。"""
    logger.info("=== 手动表达式验证模式 ===")
    logger.info("名称: %s, 表达式: %s", name, expr)

    candidates = [
        FactorCandidate(
            name=name,
            expression=expr,
            source=CandidateSource.MANUAL,
        )
    ]
    result = miner.run_campaign(candidates, campaign_id="manual")
    return result


def save_results(miner: LLMFactorMiner, output_dir: str) -> None:
    """保存验证结果和报告。"""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 导出报告 JSON
    report_path = out / "mining_report.json"
    report = miner.export_report(str(report_path))

    # 导出通过验证的因子列表
    passed = miner.get_passed_factors()
    passed_path = out / "passed_factors.json"
    with open(passed_path, "w", encoding="utf-8") as f:
        json.dump(passed, f, ensure_ascii=False, indent=2)

    logger.info("="*50)
    logger.info("验证结果摘要:")
    logger.info("  总候选: %d", report["total_candidates"])
    logger.info("  通过验证: %d", report["total_passed"])
    logger.info("  通过率: %.1f%%",
                report["total_passed"] / max(report["total_candidates"], 1) * 100)
    logger.info("  报告: %s", report_path)
    logger.info("  通过因子: %s", passed_path)
    logger.info("="*50)

    if passed:
        logger.info("通过验证的因子:")
        for p in passed:
            logger.info("  [%s] %s = %s", p["source"], p["name"], p["expression"])
    else:
        logger.warning("本轮无因子通过验证")


def main() -> None:
    """主入口。"""
    args = parse_args()

    # 设置 API Key (命令行覆盖环境变量)
    if args.api_key:
        os.environ["DEEPSEEK_API_KEY"] = args.api_key

    # 初始化挖掘器
    config = MiningConfig()
    if args.input:
        config.rdagent_output_dir = args.input
    miner = LLMFactorMiner(config)

    # 根据模式执行
    if args.mode == "rdagent":
        if not args.input:
            logger.error("rdagent 模式需要 --input 参数指定输出目录")
            sys.exit(1)
        run_rdagent_ingest(miner, args.input)
    elif args.mode == "llm":
        run_llm_mining(miner, args.direction, args.n_factors)
    elif args.mode == "manual":
        if not args.expr:
            logger.error("manual 模式需要 --expr 参数指定表达式")
            sys.exit(1)
        run_manual_validation(miner, args.name, args.expr)

    # 保存结果
    save_results(miner, args.output)


if __name__ == "__main__":
    main()
