/** Investment Planner — page-specific CSS (dark theme). */
export const PLANNER_CSS = `
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.header-right a{color:var(--accent);text-decoration:none}
.header-right a:hover{text-decoration:underline}
.mode-select{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:4px 10px;color:var(--text);font-size:13px;font-weight:600;cursor:pointer;outline:none;transition:border-color .2s}
.mode-select:focus{border-color:var(--accent)}
.mode-select option{background:var(--bg2);color:var(--text)}
.mode-paper .mode-select{border-color:var(--green);color:var(--green)}
.mode-live .mode-select{border-color:var(--red);color:var(--red)}
.tabs{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);padding:0 12px;overflow-x:auto;align-items:flex-end}
.tab-group{display:flex;gap:0;align-items:flex-end;position:relative}
.tab-group+.tab-group{margin-left:2px}
.tab-group+.tab-group::before{content:'';display:block;width:1px;height:20px;background:var(--border);margin:0 4px;align-self:center}
.tab-group-label{font-size:11px;color:var(--text-dim);opacity:.7;position:absolute;top:1px;left:50%;transform:translateX(-50%);white-space:nowrap;pointer-events:none;letter-spacing:.5px}
.tab{padding:9px 14px;cursor:pointer;font-size:13px;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .2s;white-space:nowrap;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab.tab-sim{color:var(--text-dim)}
.tab.tab-sim:hover{color:#d29922}
.tab.tab-sim.active{color:#d29922;border-bottom-color:#d29922}
.tab.tab-live{color:var(--text-dim)}
.tab.tab-live:hover{color:#3fb950}
.tab.tab-live.active{color:#3fb950;border-bottom-color:#3fb950}
.tab-content{display:none;padding:16px 24px}
.tab-content.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;margin-bottom:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px}
.card h3{font-size:13px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-sm{padding:3px 8px;font-size:11px}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-danger{background:#da3633;border-color:#f85149;color:#fff}
.btn-danger:hover{background:#f85149}
.btn-warning{background:var(--orange);border-color:var(--orange);color:#fff}
.btn-group{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:11px}
tr:hover{background:var(--bg3)}
.badge{display:inline-block;padding:2px 7px;border-radius:12px;font-size:11px;font-weight:500}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.badge-orange{background:rgba(219,109,40,.15);color:var(--orange)}
.inst-row-selected{background:rgba(88,166,255,.12);outline:1px solid rgba(88,166,255,.4)}
.kpi-row{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px 20px;min-width:120px;flex:1;text-align:center}
.kpi .kpi-val{font-size:22px;font-weight:700}
.kpi .kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
input[type=text],input[type=number],select{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:5px 8px;color:var(--text);font-size:12px}
input:focus,select:focus{outline:none;border-color:var(--accent)}
.form-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.form-row label{font-size:12px;color:var(--text-dim);min-width:70px}
.toast{position:fixed;bottom:20px;right:20px;background:var(--bg2);border:1px solid var(--border);padding:10px 18px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .3s;z-index:999}
.toast.show{opacity:1}
.signal-card{border-left:4px solid var(--yellow);margin-bottom:8px}
.signal-card.urgent{border-left-color:var(--red)}
.signal-card.important{border-left-color:var(--orange)}
.milestone-bar{height:6px;background:var(--bg3);border-radius:3px;margin:4px 0;overflow:hidden}
.milestone-fill{height:100%;background:var(--green);border-radius:3px;transition:width .3s}
.rules-list{list-style:none}
.rules-list li{padding:8px 0;border-bottom:1px solid var(--border);font-size:12px;display:flex;gap:8px;align-items:flex-start}
.rules-list li:last-child{border-bottom:none}
.rules-icon{font-size:14px;flex-shrink:0}
.progress-ring{width:80px;height:80px;margin:0 auto 8px}
/* Backtest styles */
.bt-chart{width:100%;height:280px;position:relative;background:var(--bg);border:1px solid var(--border);border-radius:6px;overflow:hidden;margin:8px 0}
.bt-chart canvas{width:100%;height:100%}
.bt-legend{display:flex;gap:16px;flex-wrap:wrap;font-size:11px;margin-top:6px}
.bt-legend-item{display:flex;align-items:center;gap:4px}
.bt-legend-dot{width:10px;height:10px;border-radius:2px}
.bt-compare-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px;margin-bottom:16px}
.bt-metric{display:flex;justify-content:space-between;padding:4px 0;font-size:12px;border-bottom:1px solid var(--border)}
.bt-metric:last-child{border-bottom:none}
.bt-metric-label{color:var(--text-dim)}
.bt-bar{height:18px;border-radius:3px;min-width:2px;transition:width .4s}
.bt-bar-wrap{display:flex;align-items:center;gap:8px;margin:4px 0}
.bt-monthly{display:flex;gap:2px;flex-wrap:wrap;align-items:flex-end;height:100px}
.bt-monthly-bar{width:24px;border-radius:2px 2px 0 0;transition:height .3s}
.spinner-sm{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
.breadcrumb a{color:var(--accent);text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--text-dim);margin:0 8px;user-select:none}
.breadcrumb .current{color:var(--text)}
.acc-top3-help-wrap{position:relative;display:inline-flex;align-items:center}
.acc-top3-help-panel{position:absolute;top:calc(100% + 6px);left:0;min-width:360px;max-width:min(92vw,620px);background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:10px 12px;box-shadow:0 10px 28px rgba(0,0,0,.35);z-index:50;color:var(--text);font-size:12px;line-height:1.55;opacity:0;transform:translateY(-2px);transition:opacity .16s ease,transform .16s ease;pointer-events:none}
.acc-top3-help-panel.is-open{opacity:1;transform:translateY(0);pointer-events:auto}
.acc-top3-help-table{width:100%;border-collapse:collapse;margin-top:6px;font-size:11px}
.acc-top3-help-table th,.acc-top3-help-table td{padding:4px 6px;border:1px solid var(--border);white-space:nowrap}
.acc-top3-help-table th{color:var(--text-dim);font-weight:500;text-align:left;background:rgba(128,128,128,.08)}
.acc-top3-help-table tr.current{background:rgba(88,166,255,.12)}
.acc-top3-help-table tr.current td:first-child{color:var(--accent);font-weight:600}
/* ── Strategy chips ── */
.chip{display:inline-flex;align-items:center;gap:4px;padding:5px 12px;border-radius:16px;font-size:12px;background:var(--bg3);border:1px solid var(--border);cursor:pointer;user-select:none;transition:all .15s;line-height:1.4}
.chip:hover{border-color:var(--accent);background:rgba(88,166,255,.08)}
.chip.selected{background:rgba(88,166,255,.18);border-color:var(--accent);color:var(--accent);font-weight:500}
.chip .chip-tag{font-size:10px;padding:1px 5px;border-radius:8px;background:rgba(188,140,255,.15);color:var(--purple);margin-left:2px}
.chip.chip-rec::before{content:'★';margin-right:2px;font-size:10px;color:var(--yellow)}

/* ── Strategy category group ── */
.strat-group{margin-bottom:14px}
.strat-group-title{font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:6px;display:flex;align-items:center;gap:6px;padding-bottom:4px;border-bottom:1px solid var(--border)}
.strat-group-title .strat-group-icon{font-size:14px}
.strat-group-title .strat-group-count{font-size:10px;padding:1px 6px;border-radius:8px;background:var(--bg3);color:var(--text-dim)}
.strat-group-chips{display:flex;flex-wrap:wrap;gap:6px;padding:4px 0}

/* ── Portfolio backtest form improvements ── */
.pbt-form-section{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:14px 16px;margin-bottom:12px}
.pbt-form-section h4{font-size:13px;margin:0 0 10px;display:flex;align-items:center;gap:6px}
.pbt-input-group{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;align-items:end}
.pbt-input-group .pbt-field{display:flex;flex-direction:column;gap:3px}
.pbt-input-group .pbt-field label{font-size:11px;color:var(--text-dim)}
.pbt-input-group .pbt-field input{width:100%}
.pbt-period-btns{display:flex;gap:4px;align-items:center;margin-top:auto}
.pbt-period-btns .btn{padding:3px 10px;font-size:11px;border-radius:12px}
.pbt-period-btns .btn.active{background:rgba(88,166,255,.2);border-color:var(--accent);color:var(--accent)}
.pbt-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px solid var(--border)}
.pbt-actions .btn-primary{padding:7px 20px;font-size:13px;border-radius:8px;font-weight:600}
.pbt-hint{font-size:12px;color:var(--text-dim);margin-top:10px;padding:10px 12px;background:var(--bg);border-radius:6px;border-left:3px solid var(--yellow);line-height:1.6}
.pbt-search{background:var(--bg3);border:1px solid var(--border);border-radius:16px;padding:4px 12px;font-size:12px;color:var(--text);width:200px;transition:border-color .15s}
.pbt-search:focus{border-color:var(--accent);outline:none}
.up{color:var(--green)}.down{color:var(--red)}
@media(max-width:700px){.cards{grid-template-columns:1fr}.kpi-row{flex-direction:column}.bt-compare-grid{grid-template-columns:1fr}.pbt-input-group{grid-template-columns:1fr}}
@media(max-width:700px){.acc-top3-help-panel{left:auto;right:0;min-width:280px}}
@media(prefers-reduced-motion:reduce){.acc-top3-help-panel{transition:none;transform:none}.acc-top3-help-panel.is-open{transform:none}}
`;
