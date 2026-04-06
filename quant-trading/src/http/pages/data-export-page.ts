/**
 * Data Export Page — 数据导出中心 (FR-7)
 *
 * Self-contained SPA for managing data exports:
 *  - Trigger exports (portfolio, trades, signals, orders, equity, journal,
 *    backtest, screen, klines, watchlist) in CSV or JSON format
 *  - Batch export multiple datasets at once
 *  - List / download / delete exported files
 *  - Clean all exported files
 *
 * Uses the `/quant/api/data-export/*` HTTP API endpoints.
 */

export function getDataExportPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>数据导出中心 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
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
main{max-width:1000px;margin:0 auto;padding:20px}

.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:6px}

.form-row{display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap}
.form-row label{font-size:13px;color:var(--text-dim);min-width:70px}
.form-row select,.form-row input{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-size:13px}
.form-row select:focus,.form-row input:focus{outline:none;border-color:var(--accent)}
.form-row input::placeholder{color:var(--text-dim)}

.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-danger{background:var(--bg3);border-color:var(--red);color:var(--red)}
.btn-danger:hover{background:var(--red);color:#fff}
.btn-sm{padding:4px 10px;font-size:12px}

table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover td{background:var(--bg3)}

.checkbox-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:8px;margin-bottom:12px}
.checkbox-grid label{display:flex;align-items:center;gap:6px;font-size:13px;cursor:pointer;padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;transition:all .15s}
.checkbox-grid label:hover{border-color:var(--accent)}
.checkbox-grid input[type="checkbox"]{accent-color:var(--accent)}

.kpi-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-bottom:20px}
.kpi{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center}
.kpi-val{font-size:18px;font-weight:700}
.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}

.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0);pointer-events:auto}

.empty-state{text-align:center;padding:40px 20px;color:var(--text-dim);font-size:14px}

.spinner{display:inline-block;width:14px;height:14px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;animation:spin .6s linear infinite;vertical-align:middle;margin-right:4px}
@keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
<header>
  <h1>📤 数据导出中心</h1>
  <div class="header-right">
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 数据导出</div>

<main>

<!-- KPI summary -->
<div class="kpi-row" id="kpiRow">
  <div class="kpi"><div class="kpi-val" id="kpiTotal">-</div><div class="kpi-label">已导出文件</div></div>
  <div class="kpi"><div class="kpi-val" id="kpiCsv">-</div><div class="kpi-label">CSV 文件</div></div>
  <div class="kpi"><div class="kpi-val" id="kpiJson">-</div><div class="kpi-label">JSON 文件</div></div>
  <div class="kpi"><div class="kpi-val" id="kpiSize">-</div><div class="kpi-label">总大小</div></div>
</div>

<!-- Single export -->
<div class="card">
  <h3>📋 单项导出</h3>
  <div class="form-row">
    <label>数据类型</label>
    <select id="exportAction">
      <option value="portfolio">持仓 (portfolio)</option>
      <option value="trades">交易记录 (trades)</option>
      <option value="signals">交易信号 (signals)</option>
      <option value="orders">订单历史 (orders)</option>
      <option value="equity">权益曲线 (equity)</option>
      <option value="journal">交易笔记 (journal)</option>
      <option value="backtest">回测结果 (backtest)</option>
      <option value="screen">选股结果 (screen)</option>
      <option value="klines">K线数据 (klines)</option>
      <option value="watchlist">自选股 (watchlist)</option>
      <option value="alerts">告警记录 (alerts)</option>
      <option value="hitrate">信号命中率 (hitrate)</option>
      <option value="performance">绩效报告 (performance)</option>
    </select>
  </div>
  <div class="form-row">
    <label>导出格式</label>
    <select id="exportFormat">
      <option value="csv">CSV (Excel 兼容)</option>
      <option value="json">JSON</option>
    </select>
  </div>
  <div class="form-row" id="symbolRow">
    <label>标的代码</label>
    <input id="exportSymbol" placeholder="如 600519 (klines/signals/trades 可选)" style="width:160px">
  </div>
  <div class="form-row" id="dateRow">
    <label>起始日期</label>
    <input id="exportStart" type="date" style="width:160px">
    <label style="min-width:auto;margin-left:8px">结束日期</label>
    <input id="exportEnd" type="date" style="width:160px">
  </div>
  <div class="form-row" id="backtestIdRow" style="display:none">
    <label>回测ID</label>
    <input id="exportBacktestId" placeholder="留空则导出所有回测" style="width:240px">
  </div>
  <div class="form-row" id="screenIdRow" style="display:none">
    <label>选股ID</label>
    <input id="exportScreenId" placeholder="留空则导出最近一次" style="width:240px">
  </div>
  <div class="form-row" id="intervalRow" style="display:none">
    <label>K线周期</label>
    <select id="exportInterval">
      <option value="1d">日线</option>
      <option value="1w">周线</option>
      <option value="1M">月线</option>
      <option value="60m">60分钟</option>
      <option value="30m">30分钟</option>
      <option value="15m">15分钟</option>
    </select>
  </div>
  <div class="form-row" id="categoryRow" style="display:none">
    <label>分类</label>
    <input id="exportCategory" placeholder="如 watchlist / ideas / shortlist" style="width:200px">
  </div>
  <div class="form-row">
    <button class="btn btn-primary" id="btnExport" onclick="doExport()">导出</button>
  </div>
</div>

<!-- Batch export -->
<div class="card">
  <h3>📦 批量导出</h3>
  <p style="font-size:13px;color:var(--text-dim);margin-bottom:12px">勾选需要导出的数据类型，一键批量导出（适合盘后复盘）</p>
  <div class="checkbox-grid" id="batchGrid">
    <label><input type="checkbox" value="portfolio" checked> 持仓</label>
    <label><input type="checkbox" value="trades" checked> 交易记录</label>
    <label><input type="checkbox" value="signals" checked> 交易信号</label>
    <label><input type="checkbox" value="orders"> 订单历史</label>
    <label><input type="checkbox" value="equity"> 权益曲线</label>
    <label><input type="checkbox" value="journal"> 交易笔记</label>
    <label><input type="checkbox" value="backtest"> 回测结果</label>
    <label><input type="checkbox" value="screen"> 选股结果</label>
    <label><input type="checkbox" value="watchlist"> 自选股</label>
    <label><input type="checkbox" value="alerts"> 告警记录</label>
    <label><input type="checkbox" value="hitrate"> 信号命中率</label>
    <label><input type="checkbox" value="performance"> 绩效报告</label>
  </div>
  <div class="form-row">
    <label>导出格式</label>
    <select id="batchFormat">
      <option value="csv">CSV (Excel 兼容)</option>
      <option value="json">JSON</option>
    </select>
  </div>
  <div class="form-row">
    <button class="btn btn-primary" id="btnBatch" onclick="doBatch()">批量导出</button>
  </div>
  <div id="batchResult" style="display:none;margin-top:12px;font-size:13px"></div>
</div>

<!-- File list -->
<div class="card">
  <h3>📁 已导出文件
    <span style="flex:1"></span>
    <button class="btn btn-sm" onclick="refreshFiles()">刷新</button>
    <button class="btn btn-sm btn-danger" style="margin-left:6px" onclick="cleanAll()">清理全部</button>
  </h3>
  <div id="fileList"><div class="empty-state">加载中...</div></div>
</div>

</main>

<div class="toast" id="toast"></div>

<script>
var B='${b}';

function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function api(path,opts){
  opts=opts||{};
  var headers=opts.body?{'Content-Type':'application/json'}:{};
  var fo=Object.assign({},opts,{headers:headers});
  return fetch(B+path,fo).then(function(r){
    if(!r.ok) return r.json().catch(function(){return {}}).then(function(body){throw new Error(body.message||r.status+' '+r.statusText)});
    return r.json();
  }).catch(function(e){toast('❌ '+e.message);throw e});
}

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

function formatSize(bytes){
  if(bytes<1024)return bytes+'B';
  if(bytes<1048576)return (bytes/1024).toFixed(1)+'KB';
  return (bytes/1048576).toFixed(1)+'MB';
}

// Toggle conditional fields based on action
document.getElementById('exportAction').addEventListener('change',function(){
  var v=this.value;
  document.getElementById('backtestIdRow').style.display=v==='backtest'?'flex':'none';
  document.getElementById('screenIdRow').style.display=v==='screen'?'flex':'none';
  document.getElementById('intervalRow').style.display=v==='klines'?'flex':'none';
  document.getElementById('categoryRow').style.display=v==='watchlist'?'flex':'none';
});

// Single export
function doExport(){
  var btn=document.getElementById('btnExport');
  btn.disabled=true;
  btn.innerHTML='<span class="spinner"></span>导出中...';
  var action=document.getElementById('exportAction').value;
  var format=document.getElementById('exportFormat').value;
  var body={action:action,format:format};
  var sym=document.getElementById('exportSymbol').value.trim();
  if(sym)body.symbol=sym;
  var sd=document.getElementById('exportStart').value;
  if(sd)body.startDate=sd;
  var ed=document.getElementById('exportEnd').value;
  if(ed)body.endDate=ed;
  if(action==='backtest'){var bid=document.getElementById('exportBacktestId').value.trim();if(bid)body.backtestId=bid}
  if(action==='screen'){var sid=document.getElementById('exportScreenId').value.trim();if(sid)body.screenId=sid}
  if(action==='klines'){body.interval=document.getElementById('exportInterval').value}
  if(action==='watchlist'){var cat=document.getElementById('exportCategory').value.trim();if(cat)body.category=cat}

  api('/quant/api/data-export/export',{method:'POST',body:JSON.stringify(body)}).then(function(d){
    toast('✅ '+d.message);
    refreshFiles();
  }).finally(function(){btn.disabled=false;btn.textContent='导出'});
}

// Batch export
function doBatch(){
  var btn=document.getElementById('btnBatch');
  btn.disabled=true;
  btn.innerHTML='<span class="spinner"></span>导出中...';
  var checks=document.querySelectorAll('#batchGrid input[type=checkbox]:checked');
  var types=[];
  checks.forEach(function(c){types.push(c.value)});
  if(types.length===0){toast('请至少选择一种数据类型');btn.disabled=false;btn.textContent='批量导出';return}
  var format=document.getElementById('batchFormat').value;

  api('/quant/api/data-export/export',{method:'POST',body:JSON.stringify({action:'batch',batchTypes:types,format:format})}).then(function(d){
    toast('✅ '+d.message);
    var el=document.getElementById('batchResult');
    el.style.display='block';
    var html='<strong>批量导出结果:</strong><br>';
    (d.results||[]).forEach(function(r){
      html+='• '+esc(r.dataset)+': '+r.rows+' 条 → '+esc(r.filename)+'<br>';
    });
    el.innerHTML=html;
    refreshFiles();
  }).finally(function(){btn.disabled=false;btn.textContent='批量导出'});
}

// File list
function refreshFiles(){
  api('/quant/api/data-export/list').then(function(d){
    var files=d.files||[];
    updateKpi(files);
    var el=document.getElementById('fileList');
    if(files.length===0){el.innerHTML='<div class="empty-state">暂无导出文件</div>';return}
    var html='<table><thead><tr><th>文件名</th><th>大小</th><th>创建时间</th><th>操作</th></tr></thead><tbody>';
    files.forEach(function(f){
      html+='<tr><td>'+esc(f.filename)+'</td><td>'+formatSize(f.size)+'</td><td>'+esc(f.createdAt.replace('T',' ').slice(0,19))+'</td>';
      html+='<td><a href="'+B+'/quant/api/data-export/download?file='+encodeURIComponent(f.filename)+'" class="btn btn-sm" style="margin-right:4px">下载</a>';
      html+='<button class="btn btn-sm btn-danger" onclick="deleteFile(\\''+esc(f.filename)+'\\')">删除</button></td></tr>';
    });
    html+='</tbody></table>';
    el.innerHTML=html;
  });
}

function updateKpi(files){
  document.getElementById('kpiTotal').textContent=files.length;
  var csvCount=0,jsonCount=0,totalSize=0;
  files.forEach(function(f){
    if(f.filename.endsWith('.csv'))csvCount++;
    if(f.filename.endsWith('.json'))jsonCount++;
    totalSize+=f.size;
  });
  document.getElementById('kpiCsv').textContent=csvCount;
  document.getElementById('kpiJson').textContent=jsonCount;
  document.getElementById('kpiSize').textContent=formatSize(totalSize);
}

function deleteFile(name){
  if(!confirm('确认删除: '+name+'?'))return;
  api('/quant/api/data-export/delete?file='+encodeURIComponent(name),{method:'DELETE'}).then(function(d){
    toast('✅ '+d.message);
    refreshFiles();
  });
}

function cleanAll(){
  if(!confirm('确认清理所有导出文件?'))return;
  api('/quant/api/data-export/clean',{method:'POST'}).then(function(d){
    toast('✅ '+d.message);
    refreshFiles();
  });
}

// Init
refreshFiles();
</script>
</body>
</html>`;
}
