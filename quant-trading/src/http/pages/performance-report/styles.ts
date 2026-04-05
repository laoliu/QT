/**
 * Performance Report Page — styles.
 */
import {
  THEME_CSS_VARS,
  BASE_CSS_RESET,
  HEADER_CSS,
  TOAST_CSS,
  KPI_CSS,
} from "../_shared.js";

export const PERFORMANCE_REPORT_CSS = `
${THEME_CSS_VARS}
${BASE_CSS_RESET}
${HEADER_CSS}
${TOAST_CSS}
${KPI_CSS}
.breadcrumb{background:var(--bg2);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
.breadcrumb a{color:var(--accent);text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--text-dim);margin:0 8px;user-select:none}
.breadcrumb .current{color:var(--text)}
.container{max-width:1400px;margin:0 auto;padding:16px 24px}
.section{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.section h2{font-size:14px;color:var(--text-dim);text-transform:uppercase;letter-spacing:.5px;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.positive{color:var(--green)}.negative{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);color:var(--text-dim);font-weight:500}
td{padding:6px 8px;border-bottom:1px solid var(--border)}
tr:hover td{background:rgba(88,166,255,0.04)}
.empty{color:var(--text-dim);text-align:center;padding:24px}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:500}
.badge-green{background:rgba(63,185,80,0.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,0.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,0.15);color:var(--yellow)}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid-3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
@media(max-width:900px){.grid-2,.grid-3{grid-template-columns:1fr}}
.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
.controls label{font-size:13px;color:var(--text-dim)}
.controls select,.controls input{padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
.controls .btn{background:none;border:1px solid var(--border);color:var(--text);padding:4px 12px;border-radius:6px;cursor:pointer;font-size:12px}
.controls .btn:hover{border-color:var(--accent);color:var(--accent)}
.controls .btn-primary{background:var(--accent);border-color:var(--accent);color:#fff}
.chart-container{position:relative}
.chart-container svg{width:100%;height:180px}
.chart-label{font-size:11px;fill:var(--text-dim)}
.chart-line{fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.chart-area{opacity:0.1}
.chart-grid{stroke:var(--border);stroke-width:0.5}
.bar-pos{fill:var(--green);opacity:0.8}
.bar-neg{fill:var(--red);opacity:0.8}
.pnl-bar{cursor:pointer}
.pnl-bar:hover{opacity:1}
.tab-bar{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:12px}
.tab-btn{background:none;border:none;color:var(--text-dim);padding:8px 16px;font-size:13px;cursor:pointer;border-bottom:2px solid transparent}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
.progress-bar{background:var(--bg3);border-radius:4px;height:6px;overflow:hidden;margin-top:4px}
.progress-bar-fill{height:100%;border-radius:4px;transition:width .3s}
`;
