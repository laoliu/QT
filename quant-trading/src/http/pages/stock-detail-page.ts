/**
 * Stock Detail Page — single-stock view with candlestick chart, quote, order book, technicals.
 *
 * Served at `/quant/admin/stock?symbol=600519`
 * Self-contained HTML SPA, communicates with `/quant/admin/api/stock-detail`.
 */

export function getStockDetailPageHtml(baseUrl: string, apiToken?: string): string {
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>个股详情</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#1a1a2e;--card:#16213e;--border:#0f3460;--text:#e0e0e0;--text-dim:#8a8a9a;
--green:#00c853;--red:#ff1744;--yellow:#ffd600;--blue:#448aff;--orange:#ff9100;}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,system-ui,sans-serif;font-size:14px;padding:12px}
a{color:var(--blue);text-decoration:none}
.back{display:inline-block;margin-bottom:8px;font-size:13px}
.header{display:flex;align-items:baseline;gap:12px;margin-bottom:12px;flex-wrap:wrap}
.header h1{font-size:22px;font-weight:700}
.header .code{color:var(--text-dim);font-size:15px}
.header .price{font-size:28px;font-weight:700;margin-left:auto}
.header .change{font-size:16px;padding:2px 8px;border-radius:4px}
.up{color:var(--red)}.down{color:var(--green)}
.up-bg{background:rgba(255,23,68,.15);color:var(--red)}
.down-bg{background:rgba(0,200,83,.15);color:var(--green)}
.grid{display:grid;grid-template-columns:1fr 280px;gap:10px}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:10px}
.card h3{font-size:14px;margin-bottom:8px;color:var(--text-dim)}
canvas{width:100%;height:300px;display:block;border-radius:4px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:4px 6px;text-align:right;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500}
td:first-child,th:first-child{text-align:left}
.quote-grid{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;font-size:13px}
.quote-grid dt{color:var(--text-dim)}
.quote-grid dd{text-align:right;font-variant-numeric:tabular-nums}
.depth-row{display:flex;align-items:center;font-size:12px;margin:1px 0;height:22px;position:relative}
.depth-bar{position:absolute;top:0;height:100%;border-radius:2px;opacity:.2}
.depth-bar.bid{right:0;background:var(--red)}
.depth-bar.ask{right:0;background:var(--green)}
.depth-label{position:relative;z-index:1;display:flex;width:100%;justify-content:space-between;padding:0 6px}
.signal-list{list-style:none;font-size:12px}
.signal-list li{padding:3px 0;border-bottom:1px solid var(--border)}
.signal-list li:last-child{border:none}
.rec-badge{display:inline-block;padding:4px 16px;border-radius:6px;font-size:18px;font-weight:700}
.rec-green{background:rgba(0,200,83,.2);color:var(--green);border:1px solid var(--green)}
.rec-red{background:rgba(255,23,68,.2);color:var(--red);border:1px solid var(--red)}
.rec-yellow{background:rgba(255,214,0,.2);color:var(--yellow);border:1px solid var(--yellow)}
.loading{text-align:center;padding:60px;color:var(--text-dim);font-size:16px}
.toolbar{display:flex;gap:6px;margin-bottom:8px;align-items:center;flex-wrap:wrap}
.toolbar input{background:var(--card);border:1px solid var(--border);color:var(--text);padding:4px 8px;border-radius:4px;width:100px}
.toolbar button{background:var(--blue);color:#fff;border:none;padding:5px 14px;border-radius:4px;cursor:pointer;font-size:13px}
.toolbar button:hover{opacity:.85}
.ma-legend{font-size:11px;display:flex;gap:10px;margin-top:4px}
.ma-legend span{display:flex;align-items:center;gap:3px}
.ma-legend .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.vol-up{fill:var(--red)}.vol-down{fill:var(--green)}
#error-msg{color:var(--red);text-align:center;padding:40px}
.tf-btn{background:var(--card);border:1px solid var(--border);color:var(--text-dim);padding:5px 12px;border-radius:4px;cursor:pointer;font-size:13px;font-weight:500;transition:all .15s}
.tf-btn:hover{border-color:var(--blue);color:var(--text);background:rgba(68,138,255,.15)}
.tf-btn.active{background:#1565c0;color:#fff;border-color:#42a5f5;box-shadow:0 0 8px rgba(66,165,245,.5);font-weight:700}
.ma-toggle{display:inline-flex;align-items:center;gap:3px;cursor:pointer;font-size:11px;padding:2px 6px;border-radius:3px;border:1px solid transparent;transition:all .15s;user-select:none}
.ma-toggle:hover{border-color:var(--border)}
.ma-toggle.off{opacity:.4}
.ma-toggle .dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.info-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:10px}
.fin-table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums}
.fin-table th,.fin-table td{padding:5px 8px;text-align:right;border-bottom:1px solid var(--border)}
.fin-table th{color:var(--text-dim);font-weight:500;position:sticky;top:0;background:var(--card)}
.fin-table td:first-child,.fin-table th:first-child{text-align:left}
.fin-table tr:hover{background:rgba(255,255,255,.03)}
.holder-bar{height:6px;border-radius:3px;background:var(--blue);display:inline-block;vertical-align:middle;margin-left:6px}
.news-item{padding:8px 0;border-bottom:1px solid var(--border)}
.news-item:last-child{border:none}
.news-item .news-title{font-size:13px;font-weight:500;margin-bottom:3px;display:flex;align-items:center;gap:6px}
.news-item .news-title a{color:var(--text)}
.news-item .news-title a:hover{color:var(--blue)}
.news-item .news-meta{font-size:11px;color:var(--text-dim)}
.news-item .news-summary{font-size:12px;color:var(--text-dim);margin-top:3px;line-height:1.5}
.src-badge{display:inline-block;font-size:10px;padding:1px 6px;border-radius:3px;font-weight:600;white-space:nowrap;flex-shrink:0}
.src-eastmoney{background:rgba(255,145,0,.2);color:#ff9100;border:1px solid rgba(255,145,0,.4)}
.src-caixin{background:rgba(0,200,83,.2);color:#00c853;border:1px solid rgba(0,200,83,.4)}
.src-global{background:rgba(68,138,255,.2);color:#448aff;border:1px solid rgba(68,138,255,.4)}
.src-cls{background:rgba(233,30,99,.2);color:#e91e63;border:1px solid rgba(233,30,99,.4)}
.src-ths{background:rgba(156,39,176,.2);color:#ce93d8;border:1px solid rgba(156,39,176,.4)}
.src-sina{background:rgba(244,67,54,.2);color:#ef5350;border:1px solid rgba(244,67,54,.4)}
.src-research{background:rgba(255,214,0,.2);color:#ffd600;border:1px solid rgba(255,214,0,.4)}
.rpt-item{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px}
.rpt-item:last-child{border:none}
.rpt-rating{font-size:11px;padding:2px 8px;border-radius:4px;font-weight:700;white-space:nowrap}
.rpt-buy{background:rgba(255,23,68,.15);color:var(--red);border:1px solid rgba(255,23,68,.3)}
.rpt-hold{background:rgba(255,214,0,.15);color:var(--yellow);border:1px solid rgba(255,214,0,.3)}
.rpt-sell{background:rgba(0,200,83,.15);color:var(--green);border:1px solid rgba(0,200,83,.3)}
.moat-score{font-size:30px;font-weight:700;line-height:1}
.moat-bar{height:8px;background:rgba(255,255,255,.08);border-radius:999px;overflow:hidden;margin:8px 0 10px}
.moat-bar-fill{height:100%;background:linear-gradient(90deg,var(--yellow),var(--orange),var(--red));border-radius:999px}
.moat-pill-row{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.moat-pill{display:inline-block;padding:3px 8px;border-radius:999px;font-size:11px;border:1px solid var(--border);background:rgba(68,138,255,.12);color:#b8d4ff}
.moat-factor{padding:6px 0;border-bottom:1px solid var(--border);font-size:12px}
.moat-factor:last-child{border-bottom:none}
.moat-factor-head{display:flex;justify-content:space-between;gap:8px;font-weight:600}
.moat-meta{font-size:11px;color:var(--text-dim);margin-top:2px;line-height:1.5}
.rpt-info{flex:1;min-width:0}
.rpt-title{font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rpt-meta{font-size:11px;color:var(--text-dim);margin-top:2px}
.section-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
@media(max-width:900px){.section-grid{grid-template-columns:1fr}}
.breadcrumb{background:var(--bg);padding:6px 12px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
.breadcrumb a{color:var(--blue);text-decoration:none}
.breadcrumb a:hover{text-decoration:underline}
.breadcrumb .sep{color:var(--text-dim);margin:0 8px;user-select:none}
.breadcrumb .current{color:var(--text)}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${baseUrl}/quant/">🏠 首页</a><span class="sep">/</span><a href="${baseUrl}/quant/admin/ui">🔧 管理面板</a><span class="sep">/</span><span class="current" id="bc-stock">📈 个股详情</span></div>
<div class="toolbar">
  <input type="text" id="sym-input" placeholder="输入代码" />
  <button onclick="loadDetail()">查询</button>
  <button onclick="refreshQuote()" style="background:var(--green)">🔄 刷新报价</button>
  <span style="margin-left:12px;border-left:1px solid var(--border);padding-left:12px"></span>
  <button class="tf-btn active" data-tf="1d" onclick="switchTimeframe('1d')">日线</button>
  <button class="tf-btn" data-tf="1w" onclick="switchTimeframe('1w')">周线</button>
  <button class="tf-btn" data-tf="1M" onclick="switchTimeframe('1M')">月线</button>
  <button class="tf-btn" data-tf="1Q" onclick="switchTimeframe('1Q')">季线</button>
  <button class="tf-btn" data-tf="6M" onclick="switchTimeframe('6M')">半年线</button>
  <button class="tf-btn" data-tf="1Y" onclick="switchTimeframe('1Y')">年线</button>
</div>
<div id="main-content"><div class="loading">请输入股票代码后点击查询</div></div>

<script>
var BASE='${baseUrl}';
var _T=${JSON.stringify(apiToken ?? "")};
var currentSymbol='';
var klineData=[];
var currentInterval='1d';
var allKlineData=[];  // full data for MA computation
var maToggles={ma5:true,ma10:true,ma20:true,ma60:false,ma120:false,ma250:false};
var _loadSeq=0; // race-condition guard: only latest request renders
var _drawToken=0; // cancel stale chart-draw polls
var _abortCtrl=null; // AbortController for in-flight fetch

function api(path,signal){
  var h={};
  if(_T)h['Authorization']='Bearer '+_T;
  var opts={credentials:'include',headers:h,cache:'no-store'};
  if(signal)opts.signal=signal;
  return fetch(BASE+path,opts).then(function(r){return r.json()});
}

function esc(s){var d=document.createElement('div');d.textContent=String(s||'');return d.innerHTML}

function fmtNum(n,decimals){
  if(n==null||isNaN(n))return '-';
  return Number(n).toFixed(decimals===undefined?2:decimals);
}
function fmtVol(v){
  if(!v)return '-';
  if(v>=1e8)return (v/1e8).toFixed(2)+'亿';
  if(v>=1e4)return (v/1e4).toFixed(0)+'万';
  return v.toLocaleString();
}
function fmtAmount(a){
  if(!a)return '-';
  if(a>=1e8)return (a/1e8).toFixed(2)+'亿';
  if(a>=1e4)return (a/1e4).toFixed(0)+'万';
  return a.toFixed(0);
}
function arr(v){return Array.isArray(v)?v:[]}

function loadDetail(){
  var sym=document.getElementById('sym-input').value.trim();
  if(!sym)return;
  // Normalize: strip SH/SZ/sh/sz prefix (e.g. "SZ002131" → "002131")
  var m=sym.match(/^(?:SH|SZ|sh|sz)(\d{6})$/);
  if(m){sym=m[1];document.getElementById('sym-input').value=sym}
  currentSymbol=sym;
  // Cancel previous in-flight request + chart polling
  if(_abortCtrl)try{_abortCtrl.abort()}catch(e){}
  _abortCtrl=new AbortController();
  var signal=_abortCtrl.signal;
  ++_drawToken;
  klineData=[]; allKlineData=[];
  document.getElementById('bc-stock').textContent='📈 '+sym;
  document.getElementById('main-content').innerHTML='<div class="loading">⏳ 加载中…</div>';
  var daysMap={'1d':300,'1w':200,'1M':120,'1Q':80,'6M':60,'1Y':40};
  var days=daysMap[currentInterval]||300;
  var seq=++_loadSeq;
  api('/quant/admin/api/stock-detail?symbol='+encodeURIComponent(sym)+'&kline_interval='+currentInterval+'&kline_days='+days,signal).then(function(d){
    if(seq!==_loadSeq)return;
    if(d.error){document.getElementById('main-content').innerHTML='<div id="error-msg">❌ '+esc(d.error)+'</div>';return}
    klineData=d.klines||[];
    allKlineData=klineData;
    renderDetail(d);
  }).catch(function(e){
    if(e.name==='AbortError')return; // intentionally cancelled
    if(seq!==_loadSeq)return;
    document.getElementById('main-content').innerHTML='<div id="error-msg">❌ 请求失败: '+esc(e.message)+'</div>';
  });
}

function switchTimeframe(tf){
  currentInterval=tf;
  document.querySelectorAll('.tf-btn').forEach(function(b){b.classList.toggle('active',b.getAttribute('data-tf')===tf)});
  if(currentSymbol) loadDetail();
}

function toggleMA(key){
  maToggles[key]=!maToggles[key];
  var el=document.querySelector('.ma-toggle[data-ma="'+key+'"]');
  if(el) el.classList.toggle('off',!maToggles[key]);
  drawKlineChart();
}

function refreshQuote(){
  if(!currentSymbol)return;
  loadDetail();
}

function renderDetail(d){
  var q=d.quote||{};
  var isUp=q.change>=0;
  var cls=isUp?'up':'down';
  var bgCls=isUp?'up-bg':'down-bg';
  var sign=isUp?'+':'';
  var name=d.name||d.symbol||'';

  var html='';
  // Header
  html+='<div class="header">';
  html+='<h1>'+esc(name)+'</h1>';
  html+='<span class="code">'+esc(d.symbol)+'</span>';
  html+='<span class="price '+cls+'">'+fmtNum(q.price)+'</span>';
  html+='<span class="change '+bgCls+'">'+sign+fmtNum(q.change)+' ('+sign+fmtNum(q.changePct)+'%)</span>';
  html+='</div>';

  html+='<div class="grid">';

  // ── Left column ──
  html+='<div>';
  // Chart
  var intervalLabels={'1d':'日线','1w':'周线','1M':'月线','1Q':'季线','6M':'半年线','1Y':'年线'};
  var intervalLabel=intervalLabels[currentInterval]||'日线';
  html+='<div class="card"><h3>📈 K 线图 ('+intervalLabel+')';
  if(d.klineSource==='bridge') html+=' <span style="font-size:11px;padding:2px 6px;background:rgba(88,166,255,.15);color:var(--accent);border-radius:4px;margin-left:6px">⚡ 迅投</span>';
  html+='</h3><canvas id="kline-canvas" width="800" height="300"></canvas>';
  html+='<div class="ma-legend">';
  var maConfigs=[
    {key:'ma5',label:'MA5',color:'#ffd600'},
    {key:'ma10',label:'MA10',color:'#448aff'},
    {key:'ma20',label:'月线 MA20',color:'#e040fb'},
    {key:'ma60',label:'季线 MA60',color:'#ff9100'},
    {key:'ma120',label:'半年线 MA120',color:'#00e5ff'},
    {key:'ma250',label:'年线 MA250',color:'#ff5252'}
  ];
  maConfigs.forEach(function(m){
    var offCls=maToggles[m.key]?'':'off';
    html+='<span class="ma-toggle '+offCls+'" data-ma="'+m.key+'" onclick="toggleMA(&quot;'+m.key+'&quot;)">';
    html+='<span class="dot" style="background:'+m.color+'"></span>'+m.label+'</span>';
  });
  html+='</div></div>';

  // Volume chart
  html+='<div class="card"><h3>📊 成交量</h3><canvas id="vol-canvas" width="800" height="100"></canvas></div>';

  // Technicals
  var t=d.technicals||{};
  if(t.recommendation){
    var recCls='rec-'+t.recColor;
    html+='<div class="card"><h3>📋 技术分析</h3>';
    html+='<div style="text-align:center;margin:8px 0"><span class="rec-badge '+recCls+'">'+esc(t.recommendation)+'</span>';
    html+='<div style="margin-top:6px;font-size:12px;color:var(--text-dim)">多方: '+t.bullish+' / 空方: '+t.bearish+'</div></div>';
    html+='<div class="quote-grid" style="margin-top:8px">';
    html+='<dt>MA5</dt><dd>'+fmtNum(t.ma5)+'</dd>';
    html+='<dt>MA10</dt><dd>'+fmtNum(t.ma10)+'</dd>';
    html+='<dt>MA20</dt><dd>'+fmtNum(t.ma20)+'</dd>';
    html+='<dt>MA60</dt><dd>'+fmtNum(t.ma60)+'</dd>';
    html+='<dt>RSI(14)</dt><dd>'+fmtNum(t.rsi14)+'</dd>';
    var mc=t.macd||{};
    html+='<dt>MACD DIF</dt><dd>'+fmtNum(mc.dif,4)+'</dd>';
    html+='<dt>MACD DEA</dt><dd>'+fmtNum(mc.dea,4)+'</dd>';
    html+='<dt>MACD柱</dt><dd>'+fmtNum(mc.histogram,4)+'</dd>';
    html+='</div>';
    if(t.signals&&t.signals.length){
      html+='<h3 style="margin-top:10px">🔍 信号</h3><ul class="signal-list">';
      t.signals.forEach(function(s){html+='<li>• '+esc(s)+'</li>'});
      html+='</ul>';
    }
    html+='</div>';
  }
  html+='</div>';

  // ── Right column ──
  html+='<div>';

  // Quote details
  html+='<div class="card"><h3>💹 行情数据</h3><div class="quote-grid">';
  html+='<dt>今开</dt><dd class="'+(q.open>=q.prevClose?'up':'down')+'">'+fmtNum(q.open)+'</dd>';
  html+='<dt>昨收</dt><dd>'+fmtNum(q.prevClose)+'</dd>';
  html+='<dt>最高</dt><dd class="up">'+fmtNum(q.high)+'</dd>';
  html+='<dt>最低</dt><dd class="down">'+fmtNum(q.low)+'</dd>';
  html+='<dt>成交量</dt><dd>'+fmtVol(q.volume)+'</dd>';
  html+='<dt>成交额</dt><dd>'+fmtAmount(q.amount)+'</dd>';
  html+='<dt>换手率</dt><dd>'+fmtNum(q.turnoverRate)+'%</dd>';
  html+='<dt>量比</dt><dd>'+fmtNum(q.volumeRatio)+'</dd>';
  html+='<dt>振幅</dt><dd>'+fmtNum(q.amplitude)+'%</dd>';
  html+='<dt>市盈率</dt><dd>'+fmtNum(q.pe)+'</dd>';
  html+='<dt>市净率</dt><dd>'+fmtNum(q.pb)+'</dd>';
  html+='<dt>总市值</dt><dd>'+fmtAmount(q.totalMarketCap)+'</dd>';
  html+='<dt>流通市值</dt><dd>'+fmtAmount(q.circulatingMarketCap)+'</dd>';
  html+='<dt>52周最高</dt><dd>'+fmtNum(q.high52w)+'</dd>';
  html+='<dt>52周最低</dt><dd>'+fmtNum(q.low52w)+'</dd>';
  html+='</div></div>';

  // Order book / depth — always show 5 ask + 5 bid rows
  var dp=d.depth||{};
  var asks=(dp.asks||[]).slice(0,5);
  var bids=(dp.bids||[]).slice(0,5);
  // Pad to 5 rows
  while(asks.length<5) asks.push({price:0,volume:0});
  while(bids.length<5) bids.push({price:0,volume:0});
  var maxVol=1;
  asks.concat(bids).forEach(function(x){if(x.volume>maxVol)maxVol=x.volume});
  html+='<div class="card"><h3>📖 盘口 (买卖五档)</h3>';
  // Asks (reversed so ask5 on top, ask1 at bottom)
  var asksRev=asks.slice().reverse();
  asksRev.forEach(function(a,i){
    var level=asks.length-i;
    if(a.price>0){
      var pct=(a.volume/maxVol)*100;
      var pCls=a.price>=(q.prevClose||0)?'up':'down';
      html+='<div class="depth-row"><div class="depth-bar ask" style="width:'+pct+'%"></div>';
      html+='<div class="depth-label"><span>卖'+level+'</span><span class="'+pCls+'">'+fmtNum(a.price)+'</span><span>'+fmtVol(a.volume)+'</span></div></div>';
    }else{
      html+='<div class="depth-row"><div class="depth-label"><span>卖'+level+'</span><span style="color:var(--text-dim)">—</span><span>—</span></div></div>';
    }
  });
  html+='<div style="border-top:2px solid var(--border);margin:2px 0"></div>';
  bids.forEach(function(b,i){
    if(b.price>0){
      var pct=(b.volume/maxVol)*100;
      var pCls=b.price>=(q.prevClose||0)?'up':'down';
      html+='<div class="depth-row"><div class="depth-bar bid" style="width:'+pct+'%"></div>';
      html+='<div class="depth-label"><span>买'+(i+1)+'</span><span class="'+pCls+'">'+fmtNum(b.price)+'</span><span>'+fmtVol(b.volume)+'</span></div></div>';
    }else{
      html+='<div class="depth-row"><div class="depth-label"><span>买'+(i+1)+'</span><span style="color:var(--text-dim)">—</span><span>—</span></div></div>';
    }
  });
  html+='</div>';

  html+='</div>'; // right col
  html+='</div>'; // grid

  // ── Full-width sections below the grid ──

  // Company info + business
  var ci=d.companyInfo||{};
  var biz=d.business||{};
  if(ci.industry||ci.name||biz.mainBusiness){
    html+='<div class="section-grid" style="margin-top:10px">';
    html+='<div class="card"><h3>🏢 公司概况</h3><div class="quote-grid">';
    if(ci.name) html+='<dt>公司名称</dt><dd>'+esc(ci.name)+'</dd>';
    if(ci.industry) html+='<dt>所属行业</dt><dd>'+esc(ci.industry)+'</dd>';
    if(ci.market) html+='<dt>所属市场</dt><dd>'+esc(ci.market)+'</dd>';
    if(ci.listDate) html+='<dt>上市日期</dt><dd>'+esc(ci.listDate)+'</dd>';
    if(ci.legalRep) html+='<dt>法人代表</dt><dd>'+esc(ci.legalRep)+'</dd>';
    if(ci.regCapital) html+='<dt>注册资金</dt><dd>'+fmtVol(ci.regCapital*10000)+'Ԫ</dd>';
    if(ci.website) html+='<dt>官方网站</dt><dd>'+esc(ci.website)+'</dd>';
    html+='</div>';
    if(ci.mainBiz) html+='<div style="margin-top:8px;font-size:12px"><span style="color:var(--text-dim)">主营业务：</span>'+esc(ci.mainBiz)+'</div>';
    if(biz.products) html+='<div style="margin-top:4px;font-size:12px"><span style="color:var(--text-dim)">产品类型：</span>'+esc(biz.products)+'</div>';
    if(biz.scope) html+='<div style="margin-top:4px;font-size:12px"><span style="color:var(--text-dim)">经营范围：</span>'+esc(biz.scope)+'</div>';
    html+='</div>';

    // Shareholders
    var sh=d.shareholders||{};
    if(sh.holders&&sh.holders.length){
      html+='<div class="card"><h3>👥 十大股东 <span style="font-size:11px;color:var(--text-dim);font-weight:400">('+esc(sh.date)+')</span></h3>';
      if(sh.totalCount) html+='<div style="font-size:11px;color:var(--text-dim);margin-bottom:6px">股东总数: '+sh.totalCount.toLocaleString()+' | 户均持股: '+fmtVol(sh.avgShares)+'</div>';
      html+='<table class="fin-table"><tr><th>股东名称</th><th>持股比例</th><th>持股数量</th></tr>';
      sh.holders.forEach(function(h){
        var ratio=h.ratio?fmtNum(h.ratio)+'%':'-';
        var barW=h.ratio?(h.ratio/sh.holders[0].ratio*60):0;
        html+='<tr><td style="text-align:left">'+esc(h.name)+'</td>';
        html+='<td>'+ratio+'<span class="holder-bar" style="width:'+barW+'px"></span></td>';
        html+='<td>'+fmtVol(h.shares)+'</td></tr>';
      });
      html+='</table></div>';
    }else{
      html+='<div></div>';
    }
    html+='</div>';
  }

  // Financial summary
  var fin=d.financials||{};
  if(fin.items&&fin.items.length){
    html+='<div class="card" style="margin-top:10px"><h3>📊 财务摘要</h3>';
    html+='<div style="overflow-x:auto"><table class="fin-table">';
    html+='<tr><th>指标</th>';
    (fin.periods||[]).forEach(function(p){
      var yr=p.substring(0,4);
      var mo=p.substring(4,6);
      var label=yr+'.'+mo;
      html+='<th>'+label+'</th>';
    });
    html+='</tr>';
    fin.items.forEach(function(item){
      html+='<tr><td>'+esc(item.name)+'</td>';
      (fin.periods||[]).forEach(function(p){
        var v=item[p];
        if(v==null){html+='<td>-</td>';return}
        var cls='';
        if(item.name.indexOf('利润')>=0||item.name.indexOf('收益')>=0){
          cls=v>=0?'up':'down';
        }
        // Format large numbers
        var txt;
        if(Math.abs(v)>=1e8) txt=(v/1e8).toFixed(2)+'亿';
        else if(Math.abs(v)>=1e4) txt=(v/1e4).toFixed(0)+'万';
        else if(item.name.indexOf('率')>=0) txt=fmtNum(v)+'%';
        else txt=fmtNum(v);
        html+='<td class="'+cls+'">'+txt+'</td>';
      });
      html+='</tr>';
    });
    html+='</table></div></div>';
  }

  var moat=d.techMoat||null;
  if(moat&&moat.status==='ok'){
    var moatScore=Number(moat.moatScore||0);
    var moatBar=Math.max(0,Math.min(100,moatScore*10));
    html+='<div class="card" style="margin-top:10px">';
    html+='<h3>🧱 技术壁垒证据链 <span style="font-size:11px;color:var(--text-dim);font-weight:400">新闻/研报交叉验证</span></h3>';
    html+='<div class="section-grid">';
    html+='<div>';
    html+='<div class="moat-score">'+fmtNum(moatScore,1)+'/10</div>';
    html+='<div class="moat-bar"><div class="moat-bar-fill" style="width:'+moatBar+'%"></div></div>';
    html+='<div class="moat-meta">评级：'+esc(moat.moatGrade||'-')+' · 置信度：'+esc(moat.confidenceLevel||'-')+'</div>';
    if(moat.interpretation) html+='<div class="moat-meta" style="margin-top:8px">'+esc(moat.interpretation)+'</div>';
    html+='</div>';
    html+='<div>';
    html+='<div class="moat-meta">证据层</div>';
    html+='<div class="moat-pill-row">';
    arr(moat.evidenceChain&&moat.evidenceChain.announce).slice(0,6).forEach(function(item){html+='<span class="moat-pill">宣传: '+esc(item)+'</span>'});
    arr(moat.evidenceChain&&moat.evidenceChain.verify_hints).slice(0,6).forEach(function(item){html+='<span class="moat-pill">验证: '+esc(item)+'</span>'});
    arr(moat.evidenceChain&&moat.evidenceChain.competitor_mentions).slice(0,4).forEach(function(item){html+='<span class="moat-pill">竞对: '+esc(item)+'</span>'});
    arr(moat.evidenceChain&&moat.evidenceChain.supply_chain).slice(0,4).forEach(function(item){html+='<span class="moat-pill">供应链: '+esc(item)+'</span>'});
    html+='</div>';
    html+='</div>';
    html+='</div>';
    if(arr(moat.factors).length){
      html+='<div style="margin-top:10px">';
      arr(moat.factors).forEach(function(f){
        html+='<div class="moat-factor">';
        html+='<div class="moat-factor-head"><span>'+esc(f.factor||'-')+'</span><span>+'+fmtNum(Number(f.score||0),2)+'</span></div>';
        if(f.detail) html+='<div class="moat-meta">'+esc(f.detail)+'</div>';
        html+='</div>';
      });
      html+='</div>';
    }
    html+='</div>';
  }

  // News (multi-source)
  var news=d.news||[];
  var research=d.research||[];
  var channelLabels={eastmoney:'东方财富',caixin:'财新',global:'全球快讯',cls:'财联社',ths:'同花顺',sina:'新浪',research:'研报'};
  var channelCls={eastmoney:'src-eastmoney',caixin:'src-caixin',global:'src-global',cls:'src-cls',ths:'src-ths',sina:'src-sina',research:'src-research'};
  if(news.length||research.length){
    html+='<div class="section-grid" style="margin-top:10px">';
    // Left: Related news
    if(news.length){
      html+='<div class="card"><h3>📰 相关新闻 <span style="font-size:11px;color:var(--text-dim);font-weight:400">('+news.length+'条·多源聚合)</span></h3>';
      news.forEach(function(n){
        var ch=n.channel||'eastmoney';
        var badge='<span class="src-badge '+( channelCls[ch]||'src-eastmoney')+'">'+(channelLabels[ch]||esc(n.source))+'</span>';
        html+='<div class="news-item">';
        html+='<div class="news-title">';
        if(n.url) html+='<a href="'+esc(n.url)+'" target="_blank">'+esc(n.title)+'</a>';
        else html+=esc(n.title);
        html+=badge;
        html+='</div>';
        html+='<div class="news-meta">';
        if(n.time) html+=esc(n.time)+' · ';
        html+=esc(n.source);
        html+='</div>';
        if(n.content) html+='<div class="news-summary">'+esc(n.content)+'</div>';
        html+='</div>';
      });
      html+='</div>';
    }
    // Right: Research reports
    if(research.length){
      html+='<div class="card"><h3>📊 机构研报 <span style="font-size:11px;color:var(--text-dim);font-weight:400">('+research.length+'ƪ)</span></h3>';
      research.forEach(function(r){
        var ratingCls='rpt-hold';
        if(r.rating&&(r.rating.indexOf('买')>=0||r.rating.indexOf('增')>=0)) ratingCls='rpt-buy';
        else if(r.rating&&(r.rating.indexOf('卖')>=0||r.rating.indexOf('减')>=0)) ratingCls='rpt-sell';
        html+='<div class="rpt-item">';
        html+='<span class="rpt-rating '+ratingCls+'">'+esc(r.rating||'-')+'</span>';
        html+='<div class="rpt-info">';
        html+='<div class="rpt-title">';
        if(r.url) html+='<a href="'+esc(r.url)+'" target="_blank">'+esc(r.title)+'</a>';
        else html+=esc(r.title);
        html+='</div>';
        html+='<div class="rpt-meta">'+esc(r.source)+' · '+esc(r.time)+'</div>';
        html+='</div></div>';
      });
      html+='</div>';
    }
    html+='</div>';
  }

  document.getElementById('main-content').innerHTML=html;

  // Draw charts after DOM update — poll until canvas has valid layout dimensions
  var _myToken=_drawToken; // snapshot: if token changes, a newer load started
  var _drawAttempt=0;
  function _tryDrawCharts(){
    if(_myToken!==_drawToken)return; // cancelled — newer load in progress
    var c=document.getElementById('kline-canvas');
    if(!c||!klineData.length){if(++_drawAttempt<30)setTimeout(_tryDrawCharts,100);return}
    var r=c.getBoundingClientRect();
    if(r.width<10||r.height<10){if(++_drawAttempt<30)setTimeout(_tryDrawCharts,100);return}
    try{drawKlineChart()}catch(e){console.error('drawKlineChart error:',e)}
    try{drawVolChart()}catch(e){console.error('drawVolChart error:',e)}
  }
  setTimeout(_tryDrawCharts,0); // defer to next microtask to let browser complete layout
}

/* ══════ CANDLESTICK CHART (Canvas) ══════ */
function drawKlineChart(){
  var canvas=document.getElementById('kline-canvas');
  if(!canvas||!klineData.length)return;
  var ctx=canvas.getContext('2d');
  if(!ctx)return;
  var dpr=window.devicePixelRatio||1;
  var rect=canvas.getBoundingClientRect();
  var W=rect.width||canvas.width/dpr||800;
  var H=rect.height||canvas.height/dpr||300;
  canvas.width=W*dpr;
  canvas.height=H*dpr;
  ctx.scale(dpr,dpr);
  var bars=klineData.slice(-60); // show last 60 bars
  var n=bars.length;
  if(!n)return;

  var pad={top:10,right:60,bottom:20,left:4};
  var chartW=W-pad.left-pad.right;
  var chartH=H-pad.top-pad.bottom;
  var barW=chartW/n;
  var bodyW=Math.max(barW*0.6,2);

  // Price range
  var lo=Infinity,hi=-Infinity;
  bars.forEach(function(b){if(b.low<lo)lo=b.low;if(b.high>hi)hi=b.high});

  // Compute MAs
  var allCloses=klineData.map(function(b){return b.close});
  var maLines=[
    {key:'ma5',period:5,color:'#ffd600'},
    {key:'ma10',period:10,color:'#448aff'},
    {key:'ma20',period:20,color:'#e040fb'},
    {key:'ma60',period:60,color:'#ff9100'},
    {key:'ma120',period:120,color:'#00e5ff'},
    {key:'ma250',period:250,color:'#ff5252'}
  ];
  var maData={};
  maLines.forEach(function(m){
    if(maToggles[m.key]){
      var fullMA=calcMA(allCloses,m.period);
      maData[m.key]=fullMA.slice(-60);
    }
  });

  // Extend price range for visible MAs
  Object.keys(maData).forEach(function(k){
    maData[k].forEach(function(v){if(v!==null){if(v<lo)lo=v;if(v>hi)hi=v}});
  });

  var range=hi-lo||1;
  lo-=range*0.02;hi+=range*0.02;
  range=hi-lo;

  function yPos(v){return pad.top+chartH*(1-(v-lo)/range)}

  // Background
  ctx.fillStyle='#0d1117';ctx.fillRect(0,0,W,H);

  // Grid lines
  ctx.strokeStyle='rgba(255,255,255,0.06)';ctx.lineWidth=1;
  for(var g=0;g<5;g++){
    var gy=pad.top+chartH*g/4;
    ctx.beginPath();ctx.moveTo(pad.left,gy);ctx.lineTo(W-pad.right,gy);ctx.stroke();
    var gv=hi-range*g/4;
    ctx.fillStyle='#888';ctx.font='10px system-ui';ctx.textAlign='left';
    ctx.fillText(gv.toFixed(2),W-pad.right+4,gy+3);
  }

  // Candlesticks
  bars.forEach(function(b,i){
    var x=pad.left+i*barW+barW/2;
    var isUp=b.close>=b.open;
    ctx.strokeStyle=isUp?'#ff1744':'#00c853';
    ctx.fillStyle=isUp?'#ff1744':'#00c853';

    // Wick
    ctx.beginPath();
    ctx.moveTo(x,yPos(b.high));
    ctx.lineTo(x,yPos(b.low));
    ctx.lineWidth=1;ctx.stroke();

    // Body
    var top=yPos(Math.max(b.open,b.close));
    var bot=yPos(Math.min(b.open,b.close));
    var bh=Math.max(bot-top,1);
    if(isUp){
      ctx.strokeRect(x-bodyW/2,top,bodyW,bh);
    }else{
      ctx.fillRect(x-bodyW/2,top,bodyW,bh);
    }

    // Date labels (every ~10 bars)
    if(i%10===0){
      var dt=new Date(b.timestamp);
      var lbl=(dt.getMonth()+1)+'/'+dt.getDate();
      ctx.fillStyle='#666';ctx.font='9px system-ui';ctx.textAlign='center';
      ctx.fillText(lbl,x,H-4);
    }
  });

  // MA lines
  maLines.forEach(function(m){
    if(maToggles[m.key] && maData[m.key]){
      drawMALine(ctx,maData[m.key],bars,pad,barW,lo,range,chartH,m.color);
    }
  });
}

function drawMALine(ctx,ma,bars,pad,barW,lo,range,chartH,color){
  ctx.strokeStyle=color;ctx.lineWidth=1.2;ctx.beginPath();
  var started=false;
  for(var i=0;i<ma.length;i++){
    if(ma[i]===null)continue;
    var x=pad.left+i*barW+barW/2;
    var y=pad.top+chartH*(1-(ma[i]-lo)/range);
    if(!started){ctx.moveTo(x,y);started=true}else{ctx.lineTo(x,y)}
  }
  ctx.stroke();
}

function calcMA(data,period){
  var result=[];
  for(var i=0;i<data.length;i++){
    if(i<period-1){result.push(null);continue}
    var sum=0;for(var j=0;j<period;j++)sum+=data[i-j];
    result.push(sum/period);
  }
  return result;
}

/* ══════ VOLUME CHART ══════ */
function drawVolChart(){
  var canvas=document.getElementById('vol-canvas');
  if(!canvas||!klineData.length)return;
  var ctx=canvas.getContext('2d');
  if(!ctx)return;
  var dpr=window.devicePixelRatio||1;
  var rect=canvas.getBoundingClientRect();
  var W=rect.width||canvas.width/dpr||800;
  var H=rect.height||canvas.height/dpr||100;
  canvas.width=W*dpr;
  canvas.height=H*dpr;
  ctx.scale(dpr,dpr);
  var bars=klineData.slice(-60);
  var n=bars.length;if(!n)return;

  var pad={top:4,right:60,bottom:16,left:4};
  var chartW=W-pad.left-pad.right;
  var chartH=H-pad.top-pad.bottom;
  var barW=chartW/n;
  var bodyW=Math.max(barW*0.6,2);

  var maxVol=1;
  bars.forEach(function(b){if(b.volume>maxVol)maxVol=b.volume});

  ctx.fillStyle='#0d1117';ctx.fillRect(0,0,W,H);

  bars.forEach(function(b,i){
    var x=pad.left+i*barW+barW/2;
    var isUp=b.close>=b.open;
    ctx.fillStyle=isUp?'rgba(255,23,68,0.7)':'rgba(0,200,83,0.7)';
    var h=chartH*(b.volume/maxVol);
    ctx.fillRect(x-bodyW/2,pad.top+chartH-h,bodyW,h);
  });

  // Y axis labels
  ctx.fillStyle='#888';ctx.font='10px system-ui';ctx.textAlign='left';
  ctx.fillText(fmtVol(maxVol),W-pad.right+4,pad.top+10);
  ctx.fillText('0',W-pad.right+4,H-pad.bottom);
}

// Init: check URL params
(function(){
  var params=new URLSearchParams(window.location.search);
  var sym=params.get('symbol');
  if(sym){
    document.getElementById('sym-input').value=sym;
    loadDetail();
  }
})();
</script>
</body>
</html>`;
}
