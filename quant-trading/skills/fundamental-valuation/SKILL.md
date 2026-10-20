---
name: fundamental-valuation
description: "分析三张报表、财务健康、杜邦分解、估值、安全边际、护城河或同行财务对比时使用。"
allowed-tools: ["market_data","cfo_assistant","value_investing","financial_calculator","earnings_calendar","tech_moat"]
metadata: {"display_name":"价值投资与财报分析","category":"股票研究","quant-trading":{"emoji":"📊","requires":{"plugins":["quant-trading"]}}}
---

# 价值投资与财报分析
确认财报期、公告日期和币种。先 market_data(action=fundamentals)，再按需 cfo_assistant(action=three_statements)、cfo_assistant(action=dupont)、cfo_assistant(action=red_flags)、cfo_assistant(action=peer_compare)。
价值判断用 value_investing(action=comprehensive)。估值计算使用 financial_calculator(action=dcf)、financial_calculator(action=pe_valuation) 或 financial_calculator(action=compare)，参数由数据或用户提供，明确折现率、增长率、可比口径及敏感性。
输出经营质量、现金流与利润差异、负债风险、估值区间及假设。预测与已披露事实分列；缺少历史公告可见时间时不声称已完成 PIT 验证。
