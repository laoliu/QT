/**
 * CSS styles for the Event Catalyst page.
 */
export function getEventCatalystStyles(): string {
  return `
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.header-right a{color:var(--accent);text-decoration:none}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border)}
.breadcrumb a{color:var(--accent)}
.tabs{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);padding:0 12px;overflow-x:auto}
.tab{padding:9px 14px;cursor:pointer;font-size:13px;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .2s;white-space:nowrap;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
main{max-width:1200px;margin:0 auto;padding:20px}
.tab-content{display:none}
.tab-content.active{display:block}

.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px;overflow:hidden;min-width:0}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.cards-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;margin-bottom:16px}
.card .table-scroll{overflow-x:auto;margin:0 -16px;padding:0 16px}

.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:110px}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}

.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.badge-orange{background:rgba(219,109,40,.15);color:var(--orange)}

.score-bar{display:flex;align-items:center;gap:8px}
.score-track{flex:1;height:6px;background:var(--bg3);border-radius:3px;overflow:hidden}
.score-fill{height:6px;border-radius:3px;transition:width .3s}

.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-danger{background:rgba(248,81,73,.15);border-color:var(--red);color:var(--red)}

.input{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:7px 12px;color:var(--text);font-size:13px}
.input:focus{outline:none;border-color:var(--accent)}
.input::placeholder{color:var(--text-dim)}

.loading{text-align:center;padding:40px;color:var(--text-dim)}
.spinner{display:inline-block;width:20px;height:20px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.signal-tag{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;margin:2px 4px 2px 0;background:var(--bg3);border:1px solid var(--border);color:var(--text-dim)}
.signal-tag.hot{background:rgba(248,81,73,.1);border-color:var(--red);color:var(--red)}
.signal-tag.flow{background:rgba(63,185,80,.1);border-color:var(--green);color:var(--green)}
.signal-tag.scarcity{background:rgba(188,140,255,.1);border-color:var(--purple);color:var(--purple)}

.param-row{display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.param-row label{font-size:12px;color:var(--text-dim);min-width:80px}

.catalyst-chip{display:inline-flex;align-items:center;gap:4px;background:var(--bg3);border:1px solid var(--border);border-radius:16px;padding:3px 10px;font-size:11px;margin:2px}
.catalyst-chip .arrow{font-weight:700}
.catalyst-chip .arrow.up{color:var(--red)}
.catalyst-chip .arrow.down{color:var(--green)}

.history-item{display:flex;align-items:center;gap:10px;padding:8px 12px;border-bottom:1px solid var(--border);cursor:pointer;transition:background .15s;font-size:13px}
.history-item:hover{background:var(--bg3)}
.history-item.active{background:var(--bg3);border-left:3px solid var(--accent)}
.history-item .hi-version{font-weight:600;min-width:120px}
.history-item .hi-meta{color:var(--text-dim);font-size:12px;flex:1}
.history-item .hi-stats{display:flex;gap:8px;font-size:12px}
.history-item .hi-del{opacity:0;color:var(--red);cursor:pointer;padding:2px 6px;border-radius:4px;font-size:12px;border:1px solid transparent;transition:all .15s}
.history-item:hover .hi-del{opacity:1}
.history-item .hi-del:hover{border-color:var(--red);background:rgba(248,81,73,.1)}

.mover-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px;margin-bottom:10px;transition:border-color .2s}
.mover-card:hover{border-color:var(--accent)}
.mover-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}
.mover-symbol{font-size:16px;font-weight:700}
.mover-name{font-size:12px;color:var(--text-dim);margin-left:8px}
.mover-change{font-size:18px;font-weight:700;font-variant-numeric:tabular-nums}
.mover-themes{display:flex;flex-wrap:wrap;gap:4px;margin-top:6px}
.mover-news{font-size:12px;color:var(--text-dim);margin-top:6px;line-height:1.5}

.empty-state{text-align:center;padding:60px 20px;color:var(--text-dim)}
.empty-state .icon{font-size:48px;margin-bottom:12px}
.empty-state h3{font-size:16px;color:var(--text);margin-bottom:8px}

@media(max-width:700px){
  .cards-row{grid-template-columns:1fr}
  .kpi-row{flex-direction:column}
  table{font-size:12px}
  th,td{padding:5px 6px}
  .param-row{flex-direction:column;align-items:flex-start;gap:6px}
}
`;
}
