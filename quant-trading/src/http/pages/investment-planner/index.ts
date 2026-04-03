/**
 * Investment Planner Page — self-contained SPA for investment planning,
 * stock screening, portfolio monitoring, and trade signal alerts.
 *
 * Re-exports from the investment-planner/ subdirectory.
 * The page is split into logical sections under pages/investment-planner/;
 * this barrel re-exports the public API unchanged to keep admin-routes.ts
 * import paths clean after the directory move.
 */
export { getInvestmentPlannerPageHtml } from "./full-page.js";
