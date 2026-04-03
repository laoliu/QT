# coding: utf-8
"""
技术壁垒证据链分析引擎 (Tech Moat Evidence Chain Analysis Engine)

从新闻、研报、行业分析中提取并验证技术壁垒的真实性。

核心逻辑：
  1. 宣传信号（Announcement） - 从新闻标题/摘要提取"技术突破"声明
  2. 证据验证（Verification） - 检查是否有市占率/产量/认证等第三方指标
  3. 竞对对标（Competitor Adoption） - 是否被行业竞对/龙头采用
  4. 供应链验证（Supply Chain） - 是否进入头部客户供应链

最终输出：
  - moatScore: 0-10 分，越高说明壁垒越真实可验证
  - evidenceChain: 按层级展示证据
  - confidence: 可信度评分
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any
from datetime import datetime

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_json(filename: str) -> dict:
    path = os.path.join(_DATA_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _extract_evidence_from_news(
    news_titles: list[str],
    symbol: str,
    company_name: str,
    signals_config: dict,
) -> dict:
    """从新闻标题中提取技术壁垒证据"""
    
    evidence = {
        "announce": [],      # 宣传信号：找到的关键词
        "verify_hints": [],   # 验证线索：出现过的验证词
        "competitor_mentions": [],  # 竞对提及
        "supply_chain": [],   # 供应链信号
    }
    
    tech_keywords = signals_config.get("tech_keywords", {})
    
    # 遍历所有新闻标题
    for title in news_titles:
        title_lower = title.lower()
        
        # 查找宣传信号（announce_keywords）
        for category, cat_info in tech_keywords.items():
            announce_kws = cat_info.get("announce_keywords", [])
            for kw in announce_kws:
                if kw in title_lower:
                    if kw not in evidence["announce"]:
                        evidence["announce"].append(kw)
        
        # 查找验证指标（verify_metrics）
        for category, cat_info in tech_keywords.items():
            verify_kws = cat_info.get("verify_metrics", [])
            for kw in verify_kws:
                if kw in title_lower:
                    if kw not in evidence["verify_hints"]:
                        evidence["verify_hints"].append(kw)
        
        # 查找竞对提及（competitor_signals）
        for category, cat_info in tech_keywords.items():
            comp_kws = cat_info.get("competitor_signals", [])
            for kw in comp_kws:
                if kw in title_lower:
                    if kw not in evidence["competitor_mentions"]:
                        evidence["competitor_mentions"].append(kw)
        
        # 查找供应链信号（supply_chain_signals）
        for category, cat_info in tech_keywords.items():
            supply_kws = cat_info.get("supply_chain_signals", [])
            for kw in supply_kws:
                if kw in title_lower:
                    if kw not in evidence["supply_chain"]:
                        evidence["supply_chain"].append(kw)
    
    return evidence


def _calculate_moat_score(evidence: dict, signals_config: dict) -> tuple[float, dict]:
    """
    根据证据链计算护城河分数。
    
    记分规则：
    - 仅有宣传信号: 1-2分
    - 有验证指标: +2-3分
    - 竞对采用: +3-4分  
    - 供应链验证: +3-5分
    - 多项同时满足: 额外加分
    """
    
    score = 0.0
    factors = []
    
    # 基础分：有宣传信号
    if evidence.get("announce"):
        announce_count = len(evidence["announce"])
        announce_score = min(2 * announce_count / 5, 2)  # 最多 2分
        score += announce_score
        factors.append({
            "factor": "宣传信号",
            "detail": f"找到 {announce_count} 个关键词",
            "score": round(announce_score, 2)
        })
    
    # 验证指标分：如果有市占率/产量等第三方数据
    if evidence.get("verify_hints"):
        verify_count = len(evidence["verify_hints"])
        verify_score = min(3 * verify_count / 5, 3)  # 最多 3分
        score += verify_score
        factors.append({
            "factor": "验证指标",
            "detail": f"发现 {verify_count} 个验证线索（市占率/产量/认证等）",
            "score": round(verify_score, 2)
        })
    
    # 竞对采用分：被行业龙头用了
    if evidence.get("competitor_mentions"):
        comp_count = len(evidence["competitor_mentions"])
        comp_score = min(4 * comp_count / 5, 4)  # 最多 4分
        score += comp_score
        factors.append({
            "factor": "竞对采用",
            "detail": f"被 {comp_count} 个龙头整合商采用验证",
            "score": round(comp_score, 2)
        })
    
    # 供应链分：进入了主流供应链
    if evidence.get("supply_chain"):
        supply_count = len(evidence["supply_chain"])
        supply_score = min(5 * supply_count / 5, 5)  # 最多 5分
        score += supply_score
        factors.append({
            "factor": "供应链验证",
            "detail": f"进入 {supply_count} 个主流供应链环节",
            "score": round(supply_score, 2)
        })
    
    # 综合分数修正：避免超过 10分
    final_score = min(score, 10.0)
    
    # 根据组合情况额外加权
    total_evidence_types = sum([
        len(evidence.get("announce", [])) > 0,
        len(evidence.get("verify_hints", [])) > 0,
        len(evidence.get("competitor_mentions", [])) > 0,
        len(evidence.get("supply_chain", [])) > 0,
    ])
    if total_evidence_types >= 3:
        final_score = min(final_score + 0.5, 10.0)
        factors.append({
            "factor": "多层证据",
            "detail": f"同时满足 {total_evidence_types} 层证据链",
            "score": 0.5
        })
    
    return final_score, factors


def _build_confidence_grade(score: float) -> str:
    """将分数转换为文字评级"""
    if score >= 8:
        return "极强（竞对验证）"
    elif score >= 6:
        return "较强（供应链验证）"
    elif score >= 4:
        return "中等（多源验证）"
    elif score >= 2:
        return "较弱（部分验证）"
    else:
        return "很弱（仅宣传信号）"


def analyze_tech_moat(config: dict) -> dict:
    """
    分析某只股票的技术壁垒可信度。
    
    Input config:
      {
        "action": "analyze",
        "symbol": "688008",
        "company_name": "澜起科技",
        "news_titles": ["...", "..."],
        "lookback_days": 30
      }
    
    Output:
      {
        "status": "ok",
        "symbol": "688008",
        "name": "澜起科技",
        "moatScore": 7.8,
        "moatGrade": "较强（供应链验证）",
        "confidenceLevel": "HIGH",
        "evidenceChain": {
          "announce": ["芯片设计", "工艺突破", ...],
          "verify_hints": ["市占率", "产量", ...],
          "competitor_mentions": ["被NVIDIA应用", ...],
          "supply_chain": ["苹果供应链", ...]
        },
        "factors": [...],
        "interpretation": "该公司的技术壁垒已在多个层面得到验证..."
      }
    """
    
    symbol = config.get("symbol", "")
    company_name = config.get("company_name", "")
    news_titles = config.get("news_titles", [])
    
    if not symbol or not news_titles:
        return {
            "status": "error",
            "message": "symbol 和 news_titles 为必需字段"
        }
    
    # 加载信号配置表
    signals_config = _load_json("tech_moat_signals.json")
    
    # 1) 从新闻提取证据
    evidence = _extract_evidence_from_news(
        news_titles,
        symbol,
        company_name,
        signals_config
    )
    
    # 2) 计算护城河分数
    score, factors = _calculate_moat_score(evidence, signals_config)
    
    # 3) 生成可信度等级
    grade = _build_confidence_grade(score)
    
    # 4) 生成文字解释
    if score >= 8:
        interpretation = (
            f"该公司的技术壁垒已在竞对采用和供应链层面得到充分验证。"
            f"发现了 {len(evidence['announce'])} 个宣传信号，"
            f"{len(evidence['verify_hints'])} 个验证指标，"
            f"{len(evidence['competitor_mentions'])} 个竞对采用案例，"
            f"{len(evidence['supply_chain'])} 个供应链验证。"
            f"这说明该技术壁垒是市场真实认可的。"
        )
    elif score >= 6:
        interpretation = (
            f"该公司的技术壁垒具有较强的真实性，已进入主流供应链验证阶段。"
            f"部分竞对已采用，具有较高可信度。建议关注持续的市场份额提升。"
        )
    elif score >= 4:
        interpretation = (
            f"该公司存在技术创新，但还需要更多第三方验证。"
            f"建议关注产量、市占率等后续数据的释放。"
        )
    elif score >= 2:
        interpretation = (
            f"该公司主要是基于舆论宣传，缺乏有力的第三方验证。"
            f"强烈建议人工深入调研，核实是否真实存在技术壁垒。"
        )
    else:
        interpretation = (
            f'未发现足够的技术壁垒证据。该公司的"技术突破"宣传缺乏支撑数据。'
            f"建议降低预期或深入尽调。"
        )
    
    return {
        "status": "ok",
        "action": "analyze",
        "symbol": symbol,
        "name": company_name,
        "analyzeDate": datetime.now().strftime("%Y-%m-%d"),
        "moatScore": round(score, 2),
        "moatGrade": grade,
        "confidenceLevel": (
            "VERY_HIGH" if score >= 8 else
            "HIGH" if score >= 6 else
            "MEDIUM" if score >= 4 else
            "LOW" if score >= 2 else
            "VERY_LOW"
        ),
        "evidenceChain": evidence,
        "factors": factors,
        "interpretation": interpretation,
        "newsCount": len(news_titles),
    }


def compare_moats(config: dict) -> dict:
    """
    对比多只股票的技术壁垒真实性。
    
    Input config:
      {
        "action": "compare",
        "candidates": [
          {
            "symbol": "688008",
            "name": "澜起科技",
            "news_titles": [...]
          },
          {
            "symbol": "300750",
            "name": "宁德时代",
            "news_titles": [...]
          }
        ]
      }
    """
    
    candidates = config.get("candidates", [])
    results = []
    
    for cand in candidates:
        result = analyze_tech_moat({
            "symbol": cand.get("symbol"),
            "company_name": cand.get("name"),
            "news_titles": cand.get("news_titles", []),
        })
        if result.get("status") == "ok":
            results.append(result)
    
    # 按 moatScore 排序
    results.sort(key=lambda x: -x.get("moatScore", 0))
    
    return {
        "status": "ok",
        "action": "compare",
        "count": len(results),
        "results": results,
    }


# ── Main ──

ACTIONS = {
    "analyze": lambda cfg: analyze_tech_moat(cfg),
    "compare": lambda cfg: compare_moats(cfg),
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "analyze")
    
    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({
            "status": "error",
            "message": f"Unknown action: {action}"
        }, ensure_ascii=False))
        return
    
    try:
        result = handler(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        import traceback
        print(json.dumps({
            "status": "error",
            "message": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
