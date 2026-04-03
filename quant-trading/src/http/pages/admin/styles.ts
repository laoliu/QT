/** Admin Management Page — CSS styles */
export const ADMIN_CSS = `
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.header-right a{color:var(--accent);text-decoration:none}
.header-right a:hover{text-decoration:underline}
.tabs{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);padding:0 24px;overflow-x:auto}
.tab{padding:9px 16px;cursor:pointer;font-size:13px;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .2s;white-space:nowrap;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none;padding:16px 24px}
.tab-content.active{display:block}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;margin-bottom:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px}
.card h3{font-size:13px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:4px;vertical-align:middle}
.dot-green{background:var(--green)}.dot-red{background:var(--red)}.dot-yellow{background:var(--yellow)}
.btn{display:inline-flex;align-items:center;gap:5px;padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:12px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-sm{padding:3px 8px;font-size:11px}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-danger{background:#da3633;border-color:#f85149;color:#fff}
.btn-danger:hover{background:#f85149}
.btn-group{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:11px}
tr:hover{background:var(--bg3)}
.info-list{list-style:none}
.info-list li{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid var(--border);font-size:12px}
.info-list li:last-child{border-bottom:none}
.info-list .label{color:var(--text-dim)}.info-list .value{font-weight:500}
.badge{display:inline-block;padding:2px 7px;border-radius:12px;font-size:11px;font-weight:500}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.toast{position:fixed;bottom:20px;right:20px;background:var(--bg2);border:1px solid var(--border);padding:10px 18px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .3s;z-index:999}
.toast.show{opacity:1}
.param-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px;margin-top:6px}
.param-item{background:var(--bg);padding:4px 8px;border-radius:4px;font-size:11px}
.param-item .pname{color:var(--accent)}.param-item .pval{color:var(--text-dim)}
.search-box{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:7px 12px;color:var(--text);font-size:13px;width:100%;max-width:300px;margin-bottom:12px}
.search-box:focus{outline:none;border-color:var(--accent)}
input[type=text],input[type=number],select{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:5px 8px;color:var(--text);font-size:12px}
input:focus,select:focus{outline:none;border-color:var(--accent)}
.form-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.form-row label{font-size:12px;color:var(--text-dim);min-width:50px}
.kpi-row{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px 20px;min-width:120px;flex:1;text-align:center}
.kpi .kpi-val{font-size:22px;font-weight:700}
.kpi .kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}
.log-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:10px;max-height:300px;overflow-y:auto;font-family:monospace;font-size:11px;line-height:1.5}
.log-info{color:var(--accent)}.log-warning{color:var(--yellow)}.log-critical{color:var(--red)}.log-signal{color:var(--green)}
.json-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;max-height:500px;overflow:auto;font-family:monospace;font-size:11px;white-space:pre-wrap;word-break:break-all}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
.breadcrumb a{color:var(--accent);text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--text-dim);margin:0 8px;user-select:none}
.breadcrumb .current{color:var(--text)}
@media(max-width:700px){.cards{grid-template-columns:1fr}.kpi-row{flex-direction:column}}
`;
