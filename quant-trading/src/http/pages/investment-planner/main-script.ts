/**
 * Investment Planner — main application script (browser-side JS).
 *
 * Assembles all JS sections into a single script string.
 * The `baseUrlJson` parameter should be `JSON.stringify(baseUrl)`.
 *
 * Sections (in load order — order matters for function/variable references):
 *   js-state.ts          — global state, constants, presets (~230 lines)
 *   js-acc-presets.ts     — accumulation Top3 preset help (~134 lines)
 *   js-utils.ts           — esc/num/pct/api/toast helpers (~38 lines)
 *   js-acc-forms.ts       — accumulation threshold forms (~180 lines)
 *   js-tab-init.ts        — tab activation + strategy dropdown (~103 lines)
 *   js-plan.ts            — plan generation/rendering/history (~382 lines)
 *   js-inst-presets.ts    — institution threshold presets (~209 lines)
 *   js-institution.ts     — institution scanning/Top3/detail (~1003 lines)
 *   js-screening.ts       — screening/rebalance/signals (~440 lines)
 *   js-backtest.ts        — backtesting + chart drawing (~908 lines)
 *   js-optimizer.ts       — portfolio optimization (~746 lines)
 *   js-daily-pick.ts      — daily picks + recommend + execute (~699 lines)
 *   js-engine.ts          — engine/conditional/breakout (~597 lines)
 *   js-acc-scan.ts        — accumulation scan/render (~335 lines)
 */

import { getAccFormsJs } from "./js-acc-forms.js";
import { getAccPresetsJs } from "./js-acc-presets.js";
import { getAccScanJs } from "./js-acc-scan.js";
import { getAddPositionJs } from "./js-add-position.js";
import { getBacktestJs } from "./js-backtest.js";
import { getDailyPickJs } from "./js-daily-pick.js";
import { getEngineJs } from "./js-engine.js";
import { getInstPresetsJs } from "./js-inst-presets.js";
import { getInstitutionJs } from "./js-institution.js";
import { getOptimizerJs } from "./js-optimizer.js";
import { getPlanJs } from "./js-plan.js";
import { getPortfolioBacktestJs } from "./js-portfolio-backtest.js";
import { getScreeningJs } from "./js-screening.js";
import { getStateJs } from "./js-state.js";
import { getTabInitJs } from "./js-tab-init.js";
import { getTradePlanJs } from "./js-trade-plan.js";
import { getUtilsJs } from "./js-utils.js";

/**
 * Return the complete main script JS as a single string.
 * All sections are concatenated in dependency order.
 */
export function getPlannerMainScript(baseUrlJson: string): string {
  return [
    `var B = ${baseUrlJson};`,
    getStateJs(),
    getAccPresetsJs(),
    getUtilsJs(),
    getAccFormsJs(),
    getTabInitJs(),
    getTradePlanJs(),
    getPlanJs(),
    getInstPresetsJs(),
    getInstitutionJs(),
    getScreeningJs(),
    getBacktestJs(),
    getOptimizerJs(),
    getDailyPickJs(),
    getAddPositionJs(),
    getEngineJs(),
    getAccScanJs(),
    getPortfolioBacktestJs(),
    `/* auto-load today plan on page init */
try{loadTodayPlan();}catch(e){console.warn('[init] loadTodayPlan',e);}`,
  ].join("\n");
}
