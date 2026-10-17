/**
 * ST Restructuring Tracking Page — ST重整跟踪
 *
 * Visualizes ST stocks through the restructuring lifecycle:
 *  - Stage pipeline overview (PRE-0 → REMOVED)
 *  - By-stage grouped stock lists with risk badges
 *  - Highlight: MAIN-6 (executing plan) and MAIN-7 (completed)
 *  - Manual scan trigger + single-stock deep-track
 *
 * Data fetched from `/quant/admin/api/st-restructuring?action=...`
 */

export function getSTRestructuringPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ST重整跟踪 — Quant Trading 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🏗️</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.container{max-width:1400px;margin:0 auto;padding:24px}

/* KPIs */
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 18px;text-align:center;min-width:100px;cursor:pointer;transition:border-color .2s}
.kpi:hover{border-color:var(--accent)}
.kpi.active{border-color:var(--accent);box-shadow:0 0 8px rgba(88,166,255,.2)}
.kpi-val{font-size:22px;font-weight:700}
.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}

/* Pipeline */
.pipeline{display:flex;gap:2px;margin-bottom:24px;overflow-x:auto;padding-bottom:8px}
.pipe-step{flex:1;min-width:80px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 6px;text-align:center;font-size:11px;position:relative;transition:all .2s;cursor:pointer}
.pipe-step:hover{border-color:var(--accent)}
.pipe-step .step-id{font-weight:700;color:var(--text-dim);font-size:10px}
.pipe-step .step-label{margin-top:2px;line-height:1.3}
.pipe-step .step-count{font-size:16px;font-weight:700;margin-top:4px;color:var(--accent)}
.pipe-step.highlight-exec{border-color:var(--yellow);background:rgba(210,153,34,.08)}
.pipe-step.highlight-done{border-color:var(--green);background:rgba(63,185,80,.08)}
.pipe-step.highlight-removed{border-color:var(--purple);background:rgba(188,140,255,.08)}
.pipe-step.active{border-color:var(--accent);box-shadow:0 0 8px rgba(88,166,255,.25)}
.pipe-arrow{display:flex;align-items:center;color:var(--text-dim);font-size:14px;padding:0 2px}

/* Controls */
.controls{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap;align-items:center}
.btn{padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg2);color:var(--text);cursor:pointer;font-size:13px;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn:disabled{opacity:.5;cursor:not-allowed}
.input{padding:7px 12px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;width:160px}
.input:focus{outline:none;border-color:var(--accent)}
select.input{width:auto;min-width:120px}

/* Stock grid */
.stock-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:12px;margin-top:16px}
.stock-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px;transition:border-color .2s}
.stock-card:hover{border-color:var(--accent)}
.stock-card .stock-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.stock-card .stock-code{font-weight:700;font-size:15px}
.stock-card .stock-name{color:var(--text-dim);font-size:13px;margin-left:6px}
.stock-card .stage-badge{padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;white-space:nowrap}
.badge-pre{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-main{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-exec{background:rgba(210,153,34,.25);color:var(--yellow);border:1px solid var(--yellow)}
.badge-done{background:rgba(63,185,80,.2);color:var(--green);border:1px solid var(--green)}
.badge-removed{background:rgba(188,140,255,.2);color:var(--purple);border:1px solid var(--purple)}
.badge-unknown{background:var(--bg3);color:var(--text-dim)}
.stock-card .meta{font-size:12px;color:var(--text-dim);margin-top:6px;line-height:1.6}
.stock-card .meta span{margin-right:12px}
.risk-tag{display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:600}
.risk-delisting{background:rgba(248,81,73,.2);color:var(--red)}
.risk-delisting_risk{background:rgba(248,81,73,.12);color:var(--red)}
.risk-st{background:rgba(210,153,34,.15);color:var(--yellow)}
.risk-normal{background:rgba(63,185,80,.1);color:var(--green)}

/* Announcements */
.ann-list{margin-top:8px;max-height:120px;overflow-y:auto;font-size:12px}
.ann-item{padding:3px 0;border-bottom:1px solid var(--border);display:flex;gap:8px}
.ann-item:last-child{border-bottom:none}
.ann-date{color:var(--text-dim);white-space:nowrap;flex-shrink:0}
.ann-title{color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ann-title a{color:var(--text)}
.ann-title a:hover{color:var(--accent)}

/* Section */
.section{margin-bottom:24px}
.section-title{font-size:15px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.count-badge{background:var(--accent);color:#fff;border-radius:10px;padding:1px 8px;font-size:12px;font-weight:600}

/* Loading / Error */
.loading{color:var(--text-dim);font-style:italic;padding:20px;text-align:center}
.error{color:var(--red);font-size:13px;padding:12px;background:rgba(248,81,73,.08);border:1px solid rgba(248,81,73,.2);border-radius:6px;margin:12px 0}

/* Toast */
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

/* Detail modal */
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;justify-content:center;align-items:center}
.modal-overlay.active{display:flex}
.modal{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:700px;width:95%;max-height:80vh;overflow-y:auto}
.modal h2{font-size:16px;margin-bottom:16px;display:flex;align-items:center;justify-content:space-between}
.modal .close-btn{cursor:pointer;font-size:18px;color:var(--text-dim);background:none;border:none}
.modal .close-btn:hover{color:var(--text)}
.timeline{position:relative;padding-left:24px;margin:12px 0}
.timeline::before{content:'';position:absolute;left:8px;top:0;bottom:0;width:2px;background:var(--border)}
.tl-item{position:relative;margin-bottom:12px;padding-left:16px}
.tl-item::before{content:'';position:absolute;left:-20px;top:4px;width:10px;height:10px;border-radius:50%;background:var(--border);border:2px solid var(--bg2)}
.tl-item.tl-current::before{background:var(--accent);border-color:var(--accent)}
.tl-item .tl-date{font-size:11px;color:var(--text-dim)}
.tl-item .tl-stage{font-size:13px;font-weight:600}
.tl-item .tl-title{font-size:12px;color:var(--text-dim);margin-top:2px}

@media(max-width:600px){
  .pipeline{flex-wrap:wrap}
  .stock-grid{grid-template-columns:1fr}
  .kpi-row{justify-content:center}
}
</style>
</head>
<body>
<header>
  <h1>🏗️ ST重整跟踪</h1>
  <div class="header-right">
    <span id="lastUpdate" style="color:var(--text-dim);font-size:12px"></span>
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="container">

  <!-- KPI row -->
  <div class="kpi-row" id="kpiRow">
    <div class="kpi active" onclick="filterStage('all')" data-filter="all">
      <div class="kpi-val" id="kpiTotal">--</div>
      <div class="kpi-label">跟踪总数</div>
    </div>
    <div class="kpi" onclick="filterStage('pre')" data-filter="pre">
      <div class="kpi-val" id="kpiPre">--</div>
      <div class="kpi-label">预重整阶段</div>
    </div>
    <div class="kpi" onclick="filterStage('main')" data-filter="main">
      <div class="kpi-val" id="kpiMain">--</div>
      <div class="kpi-label">正式重整</div>
    </div>
    <div class="kpi" onclick="filterStage('MAIN-6')" data-filter="MAIN-6" style="border-color:var(--yellow)">
      <div class="kpi-val" id="kpiExec" style="color:var(--yellow)">--</div>
      <div class="kpi-label">执行中</div>
    </div>
    <div class="kpi" onclick="filterStage('MAIN-7')" data-filter="MAIN-7" style="border-color:var(--green)">
      <div class="kpi-val" id="kpiDone" style="color:var(--green)">--</div>
      <div class="kpi-label">执行完毕</div>
    </div>
    <div class="kpi" onclick="filterStage('REMOVED')" data-filter="REMOVED" style="border-color:var(--purple)">
      <div class="kpi-val" id="kpiRemoved" style="color:var(--purple)">--</div>
      <div class="kpi-label">已摘帽</div>
    </div>
  </div>

  <!-- Stage pipeline -->
  <div class="section-title">📋 重整生命周期</div>
  <div class="pipeline" id="pipeline"></div>

  <!-- Controls -->
  <div class="controls">
    <button class="btn btn-primary" id="btnScan" onclick="doScan()">🔍 立即扫描</button>
    <select class="input" id="selDays">
      <option value="30">近30天</option>
      <option value="60">近60天</option>
      <option value="90" selected>近90天</option>
      <option value="180">近180天</option>
      <option value="365">近1年</option>
    </select>
    <input class="input" id="txtSymbol" placeholder="代码查询 如600165">
    <button class="btn" onclick="doTrack()">📌 深度跟踪</button>
    <span id="scanStatus" style="font-size:12px;color:var(--text-dim)"></span>
  </div>

  <!-- Stock list -->
  <div class="section">
    <div class="section-title" id="listTitle">📊 跟踪列表</div>
    <div id="stockList" class="loading">加载中…</div>
  </div>
</div>

<!-- Detail modal -->
<div class="modal-overlay" id="detailModal">
  <div class="modal">
    <h2><span id="modalTitle">个股详情</span><button class="close-btn" onclick="closeModal()">✕</button></h2>
    <div id="modalBody">加载中…</div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var B="${b}";
var allRecords={};
var currentFilter="all";

var STAGE_ORDER=["PRE-0","PRE-1","PRE-2","PRE-3","PRE-4","PRE-5","PRE-6",
  "MAIN-1","MAIN-2","MAIN-3","MAIN-4","MAIN-5","MAIN-6","MAIN-7","REMOVED"];
var STAGE_LABELS={
  "PRE-0":"预重整申请","PRE-1":"法院受理预重整","PRE-2":"管理人指定",
  "PRE-3":"债权申报","PRE-4":"招募投资人","PRE-5":"投资协议签署",
  "PRE-6":"预重整报告提交",
  "MAIN-1":"法院裁定受理","MAIN-2":"正式债权申报","MAIN-3":"重整计划草案",
  "MAIN-4":"分组表决","MAIN-5":"法院批准计划","MAIN-6":"执行重整计划",
  "MAIN-7":"执行完毕","REMOVED":"撤销风险警示","UNKNOWN":"未知"
};

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function apiCall(action,extra){
  var url=B+"/quant/admin/api/st-restructuring?action="+action;
  if(extra)Object.keys(extra).forEach(function(k){url+="&"+k+"="+encodeURIComponent(extra[k])});
  return fetch(url).then(function(r){if(!r.ok)throw new Error(r.status+" "+r.statusText);return r.json()});
}

function badgeClass(stage){
  if(stage==="MAIN-6")return"badge-exec";
  if(stage==="MAIN-7")return"badge-done";
  if(stage==="REMOVED")return"badge-removed";
  if(stage.startsWith("MAIN-"))return"badge-main";
  if(stage.startsWith("PRE-"))return"badge-pre";
  return"badge-unknown";
}
function riskClass(r){
  if(r==="delisting")return"risk-delisting";
  if(r==="delisting_risk")return"risk-delisting_risk";
  if(r==="st")return"risk-st";
  return"risk-normal";
}
function riskLabel(r){
  if(r==="delisting")return"退市";
  if(r==="delisting_risk")return"*ST退市风险";
  if(r==="st")return"ST";
  return"正常";
}

function renderPipeline(stageSummary){
  var el=document.getElementById("pipeline");
  var html="";
  for(var i=0;i<STAGE_ORDER.length;i++){
    var s=STAGE_ORDER[i];
    var cnt=stageSummary[s]||0;
    var cls="pipe-step";
    if(s==="MAIN-6")cls+=" highlight-exec";
    else if(s==="MAIN-7")cls+=" highlight-done";
    else if(s==="REMOVED")cls+=" highlight-removed";
    html+='<div class="'+cls+'" onclick="filterStage(\\''+s+'\\')" data-stage="'+s+'">';
    html+='<div class="step-id">'+s+'</div>';
    html+='<div class="step-label">'+esc(STAGE_LABELS[s]||s)+'</div>';
    html+='<div class="step-count">'+cnt+'</div>';
    html+='</div>';
    if(i<STAGE_ORDER.length-1)html+='<div class="pipe-arrow">→</div>';
  }
  el.innerHTML=html;
}

function renderKPIs(data){
  var records=data.records||{};
  var total=Object.keys(records).length;
  var preCnt=0,mainCnt=0,execCnt=0,doneCnt=0,removedCnt=0;
  for(var k in records){
    var s=records[k].current_stage||"";
    if(s.startsWith("PRE-"))preCnt++;
    else if(s==="MAIN-6")execCnt++;
    else if(s==="MAIN-7")doneCnt++;
    else if(s==="REMOVED")removedCnt++;
    else if(s.startsWith("MAIN-"))mainCnt++;
  }
  mainCnt+=execCnt+doneCnt; // main total includes exec and done
  document.getElementById("kpiTotal").textContent=total;
  document.getElementById("kpiPre").textContent=preCnt;
  document.getElementById("kpiMain").textContent=mainCnt;
  document.getElementById("kpiExec").textContent=execCnt;
  document.getElementById("kpiDone").textContent=doneCnt;
  document.getElementById("kpiRemoved").textContent=removedCnt;
}

function renderStockCard(code,r){
  var stage=r.current_stage||"UNKNOWN";
  var label=STAGE_LABELS[stage]||r.stage_label||stage;
  var anns=r.announcements||[];
  var html='<div class="stock-card" data-stage="'+esc(stage)+'" onclick="showDetail(\\''+esc(code)+'\\')">';
  html+='<div class="stock-header">';
  html+='<div><span class="stock-code">'+esc(code)+'</span><span class="stock-name">'+esc(r.name)+'</span></div>';
  html+='<span class="stage-badge '+badgeClass(stage)+'">'+esc(stage+" "+label)+'</span>';
  html+='</div>';
  html+='<div class="meta">';
  html+='<span>⏱ '+r.days_in_process+'天</span>';
  if(r.risk_level)html+='<span class="risk-tag '+riskClass(r.risk_level)+'">'+riskLabel(r.risk_level)+'</span>';
  if(r.last_updated)html+='<span>更新: '+esc(r.last_updated)+'</span>';
  html+='</div>';
  if(anns.length>0){
    html+='<div class="ann-list">';
    var show=anns.slice(0,3);
    for(var i=0;i<show.length;i++){
      var a=show[i];
      html+='<div class="ann-item">';
      html+='<span class="ann-date">'+esc((a.date||"").slice(0,10))+'</span>';
      html+='<span class="ann-title" title="'+esc(a.title)+'">'+esc(a.title)+'</span>';
      html+='</div>';
    }
    if(anns.length>3)html+='<div style="font-size:11px;color:var(--text-dim);padding-top:4px">还有'+(anns.length-3)+'条公告…</div>';
    html+='</div>';
  }
  html+='</div>';
  return html;
}

function renderStockList(){
  var el=document.getElementById("stockList");
  var sorted=Object.keys(allRecords).map(function(k){return{code:k,data:allRecords[k]}});

  // Filter
  if(currentFilter!=="all"){
    sorted=sorted.filter(function(item){
      var s=item.data.current_stage||"";
      if(currentFilter==="pre")return s.startsWith("PRE-");
      if(currentFilter==="main")return s.startsWith("MAIN-")||s==="REMOVED";
      return s===currentFilter;
    });
  }

  // Sort: MAIN-7 first, then MAIN-6, then by stage order desc
  sorted.sort(function(a,b){
    var ai=STAGE_ORDER.indexOf(a.data.current_stage);
    var bi=STAGE_ORDER.indexOf(b.data.current_stage);
    if(ai===-1)ai=-1;if(bi===-1)bi=-1;
    return bi-ai;
  });

  if(sorted.length===0){
    el.innerHTML='<div class="loading">没有符合条件的记录</div>';
    return;
  }

  var title=currentFilter==="all"?"全部":"";
  if(currentFilter==="pre")title="预重整阶段";
  else if(currentFilter==="main")title="正式重整";
  else if(STAGE_LABELS[currentFilter])title=STAGE_LABELS[currentFilter];
  document.getElementById("listTitle").innerHTML='📊 '+esc(title)+' <span class="count-badge">'+sorted.length+'</span>';

  var html='<div class="stock-grid">';
  for(var i=0;i<sorted.length;i++){
    html+=renderStockCard(sorted[i].code,sorted[i].data);
  }
  html+='</div>';
  el.innerHTML=html;
}

function filterStage(stage){
  currentFilter=stage;
  // Update KPI active state
  document.querySelectorAll('.kpi').forEach(function(k){
    k.classList.toggle('active',k.getAttribute('data-filter')===stage);
  });
  // Update pipeline active state
  document.querySelectorAll('.pipe-step').forEach(function(p){
    p.classList.toggle('active',p.getAttribute('data-stage')===stage);
  });
  renderStockList();
}

function loadStatus(){
  document.getElementById("stockList").innerHTML='<div class="loading">加载中…</div>';
  apiCall("scan",{days:document.getElementById("selDays").value}).then(function(data){
    if(data.error){
      document.getElementById("stockList").innerHTML='<div class="error">'+esc(data.error)+'</div>';
      return;
    }
    allRecords=data.records||{};
    renderKPIs(data);
    // Build stage summary
    var summary={};
    for(var k in allRecords){
      var s=allRecords[k].current_stage||"UNKNOWN";
      summary[s]=(summary[s]||0)+1;
    }
    renderPipeline(summary);
    renderStockList();
    document.getElementById("lastUpdate").textContent="更新: "+new Date().toLocaleTimeString();
  }).catch(function(e){
    document.getElementById("stockList").innerHTML='<div class="error">加载失败: '+esc(e.message)+'</div>';
  });
}

function doScan(){
  var btn=document.getElementById("btnScan");
  var status=document.getElementById("scanStatus");
  btn.disabled=true;btn.textContent="扫描中…";
  status.textContent="正在扫描公告…";
  var days=document.getElementById("selDays").value;
  apiCall("scan",{days:days,force:"1"}).then(function(data){
    if(data.error){
      status.textContent="扫描失败: "+data.error;
      status.style.color="var(--red)";
    }else{
      allRecords=data.records||{};
      renderKPIs(data);
      var summary={};
      for(var k in allRecords){var s=allRecords[k].current_stage||"UNKNOWN";summary[s]=(summary[s]||0)+1}
      renderPipeline(summary);
      renderStockList();
      status.textContent="扫描完成，共"+Object.keys(allRecords).length+"只";
      status.style.color="var(--green)";
      document.getElementById("lastUpdate").textContent="更新: "+new Date().toLocaleTimeString();
      toast("扫描完成 "+Object.keys(allRecords).length+" 只ST股");
    }
  }).catch(function(e){
    status.textContent="扫描失败: "+e.message;
    status.style.color="var(--red)";
  }).finally(function(){
    btn.disabled=false;btn.textContent="🔍 立即扫描";
  });
}

function doTrack(){
  var sym=document.getElementById("txtSymbol").value.trim();
  if(!sym){toast("请输入股票代码");return}
  showDetail(sym);
}

function showDetail(code){
  var modal=document.getElementById("detailModal");
  var body=document.getElementById("modalBody");
  var title=document.getElementById("modalTitle");
  modal.classList.add("active");
  title.textContent=code+" 重整详情";
  body.innerHTML='<div class="loading">加载中…</div>';

  apiCall("track",{symbol:code}).then(function(data){
    if(data.error){body.innerHTML='<div class="error">'+esc(data.error)+'</div>';return}
    var r=data.record||data;
    title.textContent=esc(r.code+" "+r.name)+" — "+esc(STAGE_LABELS[r.current_stage]||r.stage_label||r.current_stage);

    var html='<div class="meta" style="margin-bottom:16px">';
    html+='<span>当前阶段: <b class="stage-badge '+badgeClass(r.current_stage)+'">'+esc(r.current_stage+" "+(STAGE_LABELS[r.current_stage]||""))+'</b></span><br>';
    html+='<span>持续天数: <b>'+r.days_in_process+'</b> 天</span> ';
    if(r.risk_level)html+='<span class="risk-tag '+riskClass(r.risk_level)+'">'+riskLabel(r.risk_level)+'</span> ';
    if(r.first_seen)html+='<span>首次发现: '+esc(r.first_seen)+'</span> ';
    html+='</div>';

    // Stage timeline
    var history=r.stage_history||[];
    if(history.length>0){
      html+='<div class="section-title">📅 阶段时间线</div>';
      html+='<div class="timeline">';
      for(var i=history.length-1;i>=0;i--){
        var h=history[i];
        var isCurrent=(h.stage===r.current_stage);
        html+='<div class="tl-item'+(isCurrent?' tl-current':'')+'">';
        html+='<div class="tl-date">'+esc(h.date)+'</div>';
        html+='<div class="tl-stage">'+esc(h.stage)+" "+(STAGE_LABELS[h.stage]||h.label||"")+'</div>';
        html+='<div class="tl-title">'+esc(h.title)+'</div>';
        html+='</div>';
      }
      html+='</div>';
    }

    // All announcements
    var anns=r.announcements||[];
    if(anns.length>0){
      html+='<div class="section-title" style="margin-top:16px">📰 公告列表 ('+anns.length+')</div>';
      html+='<div class="ann-list" style="max-height:300px">';
      for(var j=0;j<anns.length;j++){
        var a=anns[j];
        html+='<div class="ann-item">';
        html+='<span class="ann-date">'+esc((a.date||"").slice(0,10))+'</span>';
        if(a.url){
          html+='<span class="ann-title"><a href="'+esc(a.url)+'" target="_blank" rel="noopener">'+esc(a.title)+'</a></span>';
        }else{
          html+='<span class="ann-title">'+esc(a.title)+'</span>';
        }
        html+='</div>';
      }
      html+='</div>';
    }

    // Link to stock detail
    html+='<div style="margin-top:16px;text-align:right">';
    html+='<a href="'+B+'/quant/admin/stock?symbol='+esc(r.code)+'" target="_blank" class="btn btn-primary" style="text-decoration:none;display:inline-block">📈 查看行情详情</a>';
    html+='</div>';

    body.innerHTML=html;
  }).catch(function(e){
    body.innerHTML='<div class="error">查询失败: '+esc(e.message)+'</div>';
  });
}

function closeModal(){
  document.getElementById("detailModal").classList.remove("active");
}
document.getElementById("detailModal").addEventListener("click",function(e){
  if(e.target===this)closeModal();
});
document.addEventListener("keydown",function(e){if(e.key==="Escape")closeModal()});

// Enter key on symbol input
document.getElementById("txtSymbol").addEventListener("keydown",function(e){if(e.key==="Enter")doTrack()});

// Initial load
loadStatus();
</script>
</body>
</html>`;
}
