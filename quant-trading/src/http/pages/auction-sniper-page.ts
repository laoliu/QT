/**
 * Auction Sniper Page — 竞价抢筹狙击面板
 *
 * 检测集合竞价（9:15-9:25）期间主力异动量大单抢筹行为。
 * Tabs: 实时监控, 信号列表, 统计回顾
 * Data fetched from `/quant/admin/api/auction-sniper?action=...`
 */

export function getAuctionSniperPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>竞价抢筹狙击 — Quant Trading 量化</title>
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
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border)}
.breadcrumb a{color:var(--accent)}
.tabs{display:flex;gap:0;background:var(--bg2);border-bottom:1px solid var(--border);padding:0 12px;overflow-x:auto}
.tab{padding:9px 14px;cursor:pointer;font-size:13px;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .2s;white-space:nowrap;user-select:none}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
main{max-width:1200px;margin:0 auto;padding:20px}
.tab-content{display:none}
.tab-content.active{display:block}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px;overflow:hidden}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
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
.badge-strong{background:rgba(248,81,73,.15);color:var(--red)}
.badge-moderate{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-weak{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-none{background:rgba(139,148,158,.15);color:var(--text-dim)}
.badge-confirmed{background:rgba(63,185,80,.15);color:var(--green)}
.badge-failed{background:rgba(248,81,73,.15);color:var(--red)}
.score-bar{display:inline-block;width:60px;height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;vertical-align:middle;margin-left:4px}
.score-bar-fill{height:100%;border-radius:4px;transition:width .3s}
.signal-list{font-size:12px;color:var(--green);line-height:1.6}
.risk-list{font-size:12px;color:var(--yellow);line-height:1.6}
.btn{padding:6px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:rgba(88,166,255,.15);border-color:var(--accent);color:var(--accent)}
.btn-danger{background:rgba(248,81,73,.1);border-color:var(--red);color:var(--red)}
.loading{text-align:center;padding:40px;color:var(--text-dim)}
.error{color:var(--red);padding:16px;background:rgba(248,81,73,.1);border-radius:8px;margin-bottom:16px}
.empty{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.status-dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px}
.status-dot.active{background:var(--green);animation:pulse 2s infinite}
.status-dot.idle{background:var(--text-dim)}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.detail-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin:12px 0}
.detail-item{background:var(--bg3);border-radius:6px;padding:8px 12px}
.detail-item .dl{font-size:11px;color:var(--text-dim)}
.detail-item .dv{font-size:15px;font-weight:600;margin-top:2px}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0);pointer-events:auto}
.expand-row{display:none}
.expand-row.open{display:table-row}
.expand-btn{cursor:pointer;user-select:none;color:var(--accent)}
.rule-box{background:var(--bg2);border-left:3px solid var(--accent);padding:10px 14px;margin-bottom:8px;font-size:12px;color:var(--text-dim)}
.rule-box strong{color:var(--text)}
.param-group{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px}
.param-group label{font-size:12px;color:var(--text-dim)}
.param-group input,.param-group select{background:var(--bg3);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;font-size:12px}
</style>
</head>
<body>
<header>
  <h1>🎯 竞价抢筹狙击</h1>
  <div class="header-right">
    <span id="monitor-status"><span class="status-dot idle"></span>未启动</span>
    <button class="btn btn-primary" id="btn-auto" onclick="toggleAutoCollect()">⏱️ 自动采集</button>
    <button class="btn btn-primary" onclick="doScan()">📡 实时扫描</button>
    <button class="btn" onclick="loadSignals()">🔄 刷新</button>
    <a href="${b}/quant/admin/lianban">连板分析</a>
    <a href="${b}/quant/">← 首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/admin">首页</a> / 竞价抢筹狙击</div>

<div class="tabs">
  <div class="tab active" data-tab="signals">🎯 信号列表</div>
  <div class="tab" data-tab="detail">📊 详细分析</div>
  <div class="tab" data-tab="rules">📖 策略说明</div>
</div>

<main>
<!-- Tab 1: 信号列表 -->
<div class="tab-content active" id="tab-signals">
  <div class="param-group">
    <label>最低分数:</label>
    <input type="number" id="minScore" value="35" min="0" max="100" step="5" style="width:60px">
    <label>股票代码 (逗号分隔，留空扫全市场):</label>
    <input type="text" id="scanSymbols" placeholder="600519,000001" style="width:200px">
    <label>采集间隔(秒):</label>
    <input type="number" id="pollInterval" value="20" min="5" max="120" step="5" style="width:60px">
    <button class="btn btn-primary" onclick="doScan()">扫描</button>
  </div>
  <!-- Auto-collect status bar -->
  <div id="collect-bar" style="display:none;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-bottom:12px">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px">
      <div><span class="status-dot active"></span><strong id="collect-phase">采集中</strong> <span id="collect-info" style="font-size:12px;color:var(--text-dim)"></span></div>
      <div style="display:flex;gap:8px;align-items:center">
        <span id="collect-timer" style="font-size:13px;font-variant-numeric:tabular-nums"></span>
        <button class="btn" onclick="analyzeCollected()" id="btn-analyze" style="display:none">🔍 分析快照</button>
        <button class="btn btn-danger" onclick="toggleAutoCollect()">⏹ 停止</button>
      </div>
    </div>
    <div style="margin-top:8px;font-size:12px;color:var(--text-dim);max-height:160px;overflow-y:auto" id="collect-log"></div>
  </div>
  <div class="kpi-row" id="kpi-area"></div>
  <div class="card">
    <h3>🎯 抢筹信号</h3>
    <div id="signals-area"><div class="empty">点击"实时扫描"或"刷新"获取数据</div></div>
  </div>
</div>

<!-- Tab 2: 详细分析 -->
<div class="tab-content" id="tab-detail">
  <div id="detail-area"><div class="empty">在信号列表中点击股票查看详细分析</div></div>
</div>

<!-- Tab 3: 策略说明 -->
<div class="tab-content" id="tab-rules">
  <div class="card">
    <h3>📖 竞价异动量大单抢筹 — 策略说明</h3>
    <div class="rule-box"><strong>核心思路：</strong>集合竞价期间（9:15-9:25），主力在早期保持隐蔽（价格稳定），在最后1分钟（9:24-9:25）突然大量买入拉升价格，形成"异动量大单抢筹"。</div>
    <div class="rule-box"><strong>策略要素：</strong>竞价有主力异动量大单抢筹（隐蔽抢筹）+ 开盘后点火拉升 = 大概率大涨</div>
    <div class="rule-box"><strong>检测维度 1 — 早期稳定性 (20%)：</strong>9:15-9:24 价格波动范围。波动 &lt;1% 为极佳（主力隐蔽良好），&lt;2% 为良好。</div>
    <div class="rule-box"><strong>检测维度 2 — 尾部跳涨 (35%)：</strong>9:24-9:25 价格突变。跳涨 ≥3% 为强烈，≥1.5% 为中等。这是最重要的信号。</div>
    <div class="rule-box"><strong>检测维度 3 — 成交额骤增 (30%)：</strong>尾部成交额 vs 早期成交额。比率 ≥5 倍为强烈，≥3 倍为中等。绝对金额 ≥500万为大单。</div>
    <div class="rule-box"><strong>检测维度 4 — 委托不平衡 (15%)：</strong>买一/卖一委托量比。比率 ≥3 为强烈买压。</div>
    <div class="rule-box"><strong>信号等级：</strong>总分 ≥75 为强烈（🔴），≥55 为中等（🟡），≥35 为弱（🔵），&lt;35 不触发。</div>
    <div class="rule-box"><strong>开盘确认：</strong>9:31后检查开盘价 ≥ 竞价成交价 且 当前价 &gt; 开盘价 → 点火拉升确认。</div>
    <div class="rule-box"><strong>风险提示：</strong>跳空过高（≥7%）追高风险大；成交额不足可能为虚假信号；早期波动大可能不是典型隐蔽抢筹。</div>
  </div>
</div>
</main>

<div class="toast" id="toast"></div>

<script>
var B="${b}";
var allSignals=[];
var selectedSymbol=null;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/<|>/g,function(m){return m==='<'?'&lt;':'&gt;'}).replace(/"/g,'&quot;')}
function pct(v){return v!=null?(v>=0?'+':'')+Number(v).toFixed(2)+'%':'-'}
function pctColor(v){return v>0?'var(--green)':v<0?'var(--red)':'var(--text)'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function api(path){
  return fetch(B+path).then(function(r){
    if(!r.ok)throw new Error(r.status+' '+r.statusText);
    return r.json();
  });
}

function signalBadge(type){
  var map={strong:'强烈 🔴',moderate:'中等 🟡',weak:'弱 🔵',none:'无'};
  return '<span class="badge badge-'+(type||'none')+'">'+(map[type]||type||'无')+'</span>';
}

function confirmedBadge(v){
  if(v===true)return '<span class="badge badge-confirmed">✅ 已确认</span>';
  if(v===false)return '<span class="badge badge-failed">❌ 未确认</span>';
  return '<span class="badge badge-none">⏳ 待确认</span>';
}

function scoreBar(score){
  var color=score>=75?'var(--red)':score>=55?'var(--yellow)':score>=35?'var(--accent)':'var(--text-dim)';
  return '<span style="font-weight:700;color:'+color+'">'+score.toFixed(1)+'</span>'
    +'<span class="score-bar"><span class="score-bar-fill" style="width:'+Math.min(100,score)+'%;background:'+color+'"></span></span>';
}

function renderKpis(signals){
  var strong=0,moderate=0,weak=0,confirmed=0;
  signals.forEach(function(s){
    if(s.signal_type==='strong')strong++;
    else if(s.signal_type==='moderate')moderate++;
    else if(s.signal_type==='weak')weak++;
    if(s.post_open_confirmed===true)confirmed++;
  });
  var el=document.getElementById('kpi-area');
  el.innerHTML=''
    +'<div class="kpi"><div class="label">总信号</div><div class="value">'+signals.length+'</div></div>'
    +'<div class="kpi"><div class="label">强烈 🔴</div><div class="value" style="color:var(--red)">'+strong+'</div></div>'
    +'<div class="kpi"><div class="label">中等 🟡</div><div class="value" style="color:var(--yellow)">'+moderate+'</div></div>'
    +'<div class="kpi"><div class="label">弱 🔵</div><div class="value" style="color:var(--accent)">'+weak+'</div></div>'
    +'<div class="kpi"><div class="label">已确认 ✅</div><div class="value" style="color:var(--green)">'+confirmed+'</div></div>';
}

function renderSignals(signals){
  allSignals=signals;
  renderKpis(signals);
  var el=document.getElementById('signals-area');
  if(!signals||signals.length===0){
    el.innerHTML='<div class="empty">暂无信号。竞价时间（9:15-9:25）扫描效果最佳。</div>';
    return;
  }
  var html='<div style="overflow-x:auto"><table><thead><tr>'
    +'<th>代码</th><th>名称</th><th>信号</th><th>总分</th>'
    +'<th>尾部跳涨</th><th>尾部成交额</th><th>跳空%</th>'
    +'<th>确认</th><th>操作</th>'
    +'</tr></thead><tbody>';
  signals.forEach(function(s,i){
    html+='<tr style="cursor:pointer" onclick="showDetail('+i+')">'
      +'<td style="font-weight:600">'+esc(s.symbol)+'</td>'
      +'<td>'+esc(s.name)+'</td>'
      +'<td>'+signalBadge(s.signal_type)+'</td>'
      +'<td>'+scoreBar(s.score||0)+'</td>'
      +'<td style="color:'+pctColor(s.late_price_jump)+'">'+pct(s.late_price_jump)+'</td>'
      +'<td>'+(s.late_amount!=null?s.late_amount.toFixed(0)+'万':'-')+'</td>'
      +'<td style="color:'+pctColor(s.gap_up_pct)+'">'+pct(s.gap_up_pct)+'</td>'
      +'<td>'+confirmedBadge(s.post_open_confirmed)+'</td>'
      +'<td><button class="btn" style="padding:2px 8px;font-size:11px" onclick="event.stopPropagation();showDetail('+i+')">详情</button></td>'
      +'</tr>';
  });
  html+='</tbody></table></div>';
  el.innerHTML=html;
}

function showDetail(idx){
  var s=allSignals[idx];
  if(!s)return;
  selectedSymbol=s.symbol;
  // Switch to detail tab
  document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
  document.querySelectorAll('.tab-content').forEach(function(t){t.classList.remove('active')});
  document.querySelector('[data-tab="detail"]').classList.add('active');
  document.getElementById('tab-detail').classList.add('active');

  var el=document.getElementById('detail-area');
  var html='<div class="card"><h3>'+esc(s.name)+' ('+esc(s.symbol)+') '+signalBadge(s.signal_type)+'</h3>';

  // Score overview
  html+='<div class="detail-grid">'
    +'<div class="detail-item"><div class="dl">总分</div><div class="dv">'+scoreBar(s.score||0)+'</div></div>'
    +'<div class="detail-item"><div class="dl">昨收价</div><div class="dv">'+(s.prev_close||'-')+'</div></div>'
    +'<div class="detail-item"><div class="dl">竞价成交价</div><div class="dv">'+(s.final_auction_price||'-')+'</div></div>'
    +'<div class="detail-item"><div class="dl">跳空高开</div><div class="dv" style="color:'+pctColor(s.gap_up_pct)+'">'+pct(s.gap_up_pct)+'</div></div>'
    +'</div>';

  // Sub-scores
  html+='<h3 style="margin-top:16px">📊 四维评分</h3>';
  html+='<div class="detail-grid">';
  var dims=[
    {label:'早期稳定性 (20%)',key:'early_stability_score',sub:'波动范围: '+(s.early_change_range!=null?s.early_change_range.toFixed(2)+'%':'-')},
    {label:'尾部跳涨 (35%)',key:'late_surge_score',sub:'跳涨: '+pct(s.late_price_jump)},
    {label:'成交额骤增 (30%)',key:'volume_spike_score',sub:'倍率: '+(s.late_volume_ratio!=null?s.late_volume_ratio.toFixed(1)+'x':'-')+' / 金额: '+(s.late_amount!=null?s.late_amount.toFixed(0)+'万':'-')},
    {label:'委托不平衡 (15%)',key:'order_imbalance_score',sub:'买卖比'},
  ];
  dims.forEach(function(d){
    var v=s[d.key]||0;
    var color=v>=80?'var(--green)':v>=50?'var(--yellow)':v>=30?'var(--accent)':'var(--text-dim)';
    html+='<div class="detail-item">'
      +'<div class="dl">'+d.label+'</div>'
      +'<div class="dv" style="color:'+color+'">'+v.toFixed(1)
      +' <span class="score-bar"><span class="score-bar-fill" style="width:'+Math.min(100,v)+'%;background:'+color+'"></span></span>'
      +'</div>'
      +'<div style="font-size:11px;color:var(--text-dim);margin-top:2px">'+d.sub+'</div>'
      +'</div>';
  });
  html+='</div>';

  // Signals
  if(s.signals&&s.signals.length>0){
    html+='<h3 style="margin-top:16px">✅ 检测信号</h3><div class="signal-list">';
    s.signals.forEach(function(sig){html+='<div>• '+esc(sig)+'</div>'});
    html+='</div>';
  }

  // Risk notes
  if(s.risk_notes&&s.risk_notes.length>0){
    html+='<h3 style="margin-top:16px">⚠️ 风险提示</h3><div class="risk-list">';
    s.risk_notes.forEach(function(n){html+='<div>• '+esc(n)+'</div>'});
    html+='</div>';
  }

  // Post-open confirmation
  html+='<h3 style="margin-top:16px">🔥 开盘确认</h3>';
  html+='<div class="detail-grid">'
    +'<div class="detail-item"><div class="dl">状态</div><div class="dv">'+confirmedBadge(s.post_open_confirmed)+'</div></div>'
    +'<div class="detail-item"><div class="dl">开盘价</div><div class="dv">'+(s.open_price||'-')+'</div></div>'
    +'<div class="detail-item"><div class="dl">盘中最高</div><div class="dv">'+(s.post_open_high||'-')+'</div></div>'
    +'<div class="detail-item"><div class="dl">开盘后涨幅</div><div class="dv" style="color:'+pctColor(s.post_open_change_pct)+'">'+pct(s.post_open_change_pct)+'</div></div>'
    +'</div>';

  html+='</div>';
  el.innerHTML=html;
}

// ── Auto-collect state ──────────────────────────────────────
var _collectTimer=null;
var _collectTimerDisplay=null;
var _collectBatches=[];
var _collectCount=0;
var _collectStartTime=null;
var _collecting=false;

function toggleAutoCollect(){
  if(_collecting){stopAutoCollect();return;}
  _collecting=true;
  _collectBatches=[];
  _collectCount=0;
  _collectStartTime=Date.now();

  var btnAuto=document.getElementById('btn-auto');
  btnAuto.textContent='⏹ 停止采集';
  btnAuto.className='btn btn-danger';

  var bar=document.getElementById('collect-bar');
  bar.style.display='block';
  document.getElementById('collect-phase').textContent='采集中';
  document.getElementById('collect-info').textContent='';
  document.getElementById('collect-log').innerHTML='';
  document.getElementById('btn-analyze').style.display='none';
  updateMonitorStatus('active','采集中');

  collectSnapshot();

  var intervalSec=parseInt(document.getElementById('pollInterval').value)||20;
  _collectTimer=setInterval(function(){
    collectSnapshot();
  }, intervalSec*1000);
  _collectTimerDisplay=setInterval(updateCollectTimer,1000);

  toast('开始自动采集，每 '+intervalSec+' 秒轮询一次');
  appendCollectLog('▶ 开始自动采集 (间隔 '+intervalSec+'s)');
}

function stopAutoCollect(){
  _collecting=false;
  if(_collectTimer){clearInterval(_collectTimer);_collectTimer=null;}
  if(_collectTimerDisplay){clearInterval(_collectTimerDisplay);_collectTimerDisplay=null;}

  var btnAuto=document.getElementById('btn-auto');
  btnAuto.textContent='⏱️ 自动采集';
  btnAuto.className='btn btn-primary';

  document.getElementById('collect-phase').textContent='采集完成';
  if(_collectBatches.length>=2){
    document.getElementById('btn-analyze').style.display='inline-block';
    updateMonitorStatus('idle','就绪 ('+_collectBatches.length+'批)');
    toast('采集完成，共 '+_collectBatches.length+' 批快照，自动分析中…');
    appendCollectLog('⏹ 采集结束，共 '+_collectBatches.length+' 批');
    analyzeCollected();
  }else{
    updateMonitorStatus('idle','快照不足');
    toast('快照不足（至少需要2批），请延长采集时间');
    appendCollectLog('⚠ 快照不足，至少需要2批');
  }
}

function collectSnapshot(){
  var syms=document.getElementById('scanSymbols').value.trim();
  var path='/quant/admin/api/auction-sniper?action=snapshot';
  if(syms)path+='&symbols='+encodeURIComponent(syms);

  api(path).then(function(d){
    if(d.error){appendCollectLog('❌ '+d.error);return;}
    var snaps=d.snapshots||[];
    if(snaps.length===0){appendCollectLog('⚠ 本次未获取到快照数据');return;}
    _collectBatches.push(snaps);
    _collectCount+=snaps.length;
    var now=new Date();
    var ts=('0'+now.getHours()).slice(-2)+':'+('0'+now.getMinutes()).slice(-2)+':'+('0'+now.getSeconds()).slice(-2);
    appendCollectLog('📸 '+ts+' 第'+_collectBatches.length+'批 — '+snaps.length+' 只股票');
    document.getElementById('collect-info').textContent='已采集 '+_collectBatches.length+' 批 / '+_collectCount+' 条快照';
  }).catch(function(e){
    appendCollectLog('❌ 请求失败: '+e.message);
  });
}

function analyzeCollected(){
  if(_collectBatches.length<2){toast('快照不足，至少需要采集2批');return;}
  var minScore=parseInt(document.getElementById('minScore').value)||35;
  var syms=document.getElementById('scanSymbols').value.trim();
  var symbols=syms?syms.split(',').map(function(s){return s.trim()}).filter(Boolean):[];

  document.getElementById('signals-area').innerHTML='<div class="loading">正在分析 '+_collectBatches.length+' 批快照…</div>';
  toast('正在分析累积快照数据…');

  fetch(B+'/quant/admin/api/auction-sniper?action=analyze',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({snapshots:_collectBatches,symbols:symbols,min_score:minScore})
  }).then(function(r){return r.json()}).then(function(d){
    if(d.error){
      document.getElementById('signals-area').innerHTML='<div class="error">'+esc(d.error)+'</div>';
      return;
    }
    var signals=d.signals||[];
    renderSignals(signals);
    toast('分析完成，发现 '+signals.length+' 个信号');
    appendCollectLog('✅ 分析完成: '+signals.length+' 个信号');
  }).catch(function(e){
    document.getElementById('signals-area').innerHTML='<div class="error">分析失败: '+esc(e.message)+'</div>';
  });
}

function updateCollectTimer(){
  if(!_collectStartTime)return;
  var elapsed=Math.floor((Date.now()-_collectStartTime)/1000);
  var m=Math.floor(elapsed/60);
  var s=elapsed%60;
  document.getElementById('collect-timer').textContent=('0'+m).slice(-2)+':'+('0'+s).slice(-2);
}

function updateMonitorStatus(cls,text){
  document.getElementById('monitor-status').innerHTML='<span class="status-dot '+cls+'"></span>'+esc(text);
}

function appendCollectLog(msg){
  var el=document.getElementById('collect-log');
  if(!el)return;
  el.innerHTML+='<div>'+esc(msg)+'</div>';
  while(el.children.length>20)el.removeChild(el.children[0]);
  el.scrollTop=el.scrollHeight;
}

function doScan(){
  var minScore=parseInt(document.getElementById('minScore').value)||35;
  var syms=document.getElementById('scanSymbols').value.trim();
  var path='/quant/admin/api/auction-sniper?action=scan&min_score='+minScore;
  if(syms)path+='&symbols='+encodeURIComponent(syms);

  document.getElementById('signals-area').innerHTML='<div class="loading">扫描中，请稍候...</div>';
  toast('正在扫描竞价数据...');

  api(path).then(function(d){
    if(d.error){
      document.getElementById('signals-area').innerHTML='<div class="error">'+esc(d.error)+'</div>';
      return;
    }
    var signals=d.signals||[];
    renderSignals(signals);
    toast('扫描完成，发现 '+signals.length+' 个信号');
  }).catch(function(e){
    document.getElementById('signals-area').innerHTML='<div class="error">请求失败: '+esc(e.message)+'</div>';
  });
}

function loadSignals(){
  doScan();
}

// Tab switching
document.querySelectorAll('.tab').forEach(function(tab){
  tab.addEventListener('click',function(){
    document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('active')});
    document.querySelectorAll('.tab-content').forEach(function(t){t.classList.remove('active')});
    tab.classList.add('active');
    document.getElementById('tab-'+tab.dataset.tab).classList.add('active');
  });
});
</script>
</body>
</html>`;
}
