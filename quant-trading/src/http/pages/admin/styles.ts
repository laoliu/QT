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
#tab-overview .cards{grid-template-columns:minmax(0,1fr)}
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
.event-stream-card{grid-column:1/-1;width:100%;min-width:0}
.event-stream-status{font-size:10px;font-weight:400;color:var(--text-dim);margin-left:auto}
.event-stream-toolbar{display:flex;align-items:center;justify-content:space-between;gap:8px;margin:-2px 0 8px;color:var(--text-dim);font-size:11px}
.event-list{width:100%;min-width:0;background:var(--bg);border:1px solid var(--border);border-radius:6px;max-height:360px;overflow-y:auto}
.event-item{padding:8px 10px;border-bottom:1px solid var(--border);font-size:11px;line-height:1.45}
.event-item:last-child{border-bottom:0}
.event-item:hover{background:rgba(255,255,255,.025)}
.event-item-head{display:flex;align-items:center;gap:7px;min-width:0}
.event-time{color:var(--text-dim);font-size:10px;white-space:nowrap;font-variant-numeric:tabular-nums}
.event-id{color:var(--text-dim);font-size:10px;margin-left:auto;white-space:nowrap}
.event-badge{display:inline-flex;align-items:center;gap:3px;border-radius:10px;padding:2px 7px;font-size:10px;font-weight:600;white-space:nowrap}
.event-badge-engine{background:rgba(188,140,255,.14);color:var(--purple)}
.event-badge-tick{background:rgba(88,166,255,.14);color:var(--accent)}
.event-badge-signal{background:rgba(63,185,80,.14);color:var(--green)}
.event-badge-order,.event-badge-position{background:rgba(210,153,34,.14);color:var(--yellow)}
.event-badge-alert,.event-badge-risk_overlay{background:rgba(248,81,73,.14);color:var(--red)}
.event-badge-pnl,.event-badge-equity,.event-badge-data_sync,.event-badge-sector_flow_anomaly{background:rgba(219,109,40,.14);color:var(--orange)}
.event-summary{margin:5px 0 0 59px;color:var(--text);overflow-wrap:anywhere}
.event-summary strong{font-weight:600;color:#f0f6fc}
.event-fields{display:flex;flex-wrap:wrap;gap:4px 12px;margin:5px 0 0 59px;color:var(--text-dim)}
.event-field{white-space:nowrap}.event-field b{color:var(--text);font-weight:500}
.event-details{margin:6px 0 0 59px}
.event-details summary{color:var(--accent);cursor:pointer;font-size:10px;user-select:none}
.event-details pre{margin-top:5px;padding:7px 8px;background:rgba(0,0,0,.2);border:1px solid var(--border);border-radius:4px;max-height:180px;overflow:auto;color:var(--text-dim);font:10px/1.4 Consolas,Monaco,monospace;white-space:pre-wrap;word-break:break-word}
.event-empty{padding:16px;color:var(--text-dim);font-size:12px;text-align:center}
.json-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;max-height:500px;overflow:auto;font-family:monospace;font-size:11px;white-space:pre-wrap;word-break:break-all}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
.breadcrumb a{color:var(--accent);text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--text-dim);margin:0 8px;user-select:none}
.breadcrumb .current{color:var(--text)}
.chat-message{border:1px solid var(--border);border-radius:12px;padding:12px 16px;margin:0 0 16px;min-width:0;max-width:100%;overflow-wrap:anywhere}
.chat-message-user{background:#142c45;border-color:#274b6d;margin-left:auto;width:fit-content;max-width:88%}
.chat-message-assistant{background:#1a2824;border-color:#2e493c}
.chat-message-role{font-size:12px;font-weight:600;color:var(--green);margin-bottom:8px}
.chat-message-user .chat-message-role{color:var(--accent)}
.chat-message-body{min-width:0;font-size:14px;line-height:1.75}
.chat-message-user .chat-message-body,.chat-message-error .chat-message-body{white-space:pre-wrap}
.chat-message-error{border-color:var(--red)}
.chat-message-error .chat-message-body{color:#ffb4ad}
.chat-markdown>:first-child{margin-top:0}.chat-markdown>:last-child{margin-bottom:0}
.chat-markdown p,.chat-markdown ul,.chat-markdown ol,.chat-markdown blockquote,.chat-markdown pre,.chat-markdown .chat-table-scroll{margin:0 0 12px}
.chat-markdown h1,.chat-markdown h2,.chat-markdown h3,.chat-markdown h4,.chat-markdown h5,.chat-markdown h6{font-weight:650;line-height:1.4;margin:20px 0 10px;color:#e6edf3}
.chat-markdown h1{font-size:22px}.chat-markdown h2{font-size:19px;border-bottom:1px solid var(--border);padding-bottom:6px}.chat-markdown h3{font-size:16px}.chat-markdown h4,.chat-markdown h5,.chat-markdown h6{font-size:14px}
.chat-markdown ul,.chat-markdown ol{padding-left:24px}.chat-markdown li{margin:4px 0}.chat-markdown li>ul,.chat-markdown li>ol{margin-bottom:0}
.chat-markdown strong{font-weight:700;color:#f0f6fc}.chat-markdown a{color:var(--accent);text-decoration:underline}
.chat-markdown blockquote{border-left:3px solid var(--green);padding:2px 12px;color:var(--text-dim)}
.chat-markdown code{font-family:Consolas,Monaco,monospace;font-size:.9em;background:rgba(0,0,0,.24);padding:2px 5px;border-radius:4px;white-space:break-spaces}
.chat-markdown pre{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;overflow-x:auto;max-width:100%}
.chat-markdown pre code{display:block;background:transparent;padding:0;white-space:pre;overflow-wrap:normal}
.chat-table-scroll{overflow-x:auto;max-width:100%;border:1px solid var(--border);border-radius:6px}
.chat-markdown table{width:100%;font-size:13px}.chat-markdown th{background:rgba(0,0,0,.2);color:#e6edf3;font-size:13px;font-weight:600}
.chat-markdown th,.chat-markdown td{padding:8px 12px;min-width:90px;border-right:1px solid var(--border)}
.chat-markdown th:last-child,.chat-markdown td:last-child{border-right:0}.chat-markdown tbody tr:last-child td{border-bottom:0}
.chat-markdown hr{border:0;border-top:1px solid var(--border);margin:18px 0}.chat-markdown img{max-width:100%;height:auto}
@media(max-width:700px){.chat-message{padding:10px 12px}.chat-message-user{max-width:95%}}
@media(max-width:700px){.cards{grid-template-columns:1fr}.kpi-row{flex-direction:column}}
`;
