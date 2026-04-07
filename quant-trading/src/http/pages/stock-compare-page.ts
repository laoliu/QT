/**
 * Stock Comparison Page — 股票对比
 *
 * Side-by-side comparison of multiple stocks:
 * - Price performance overlay chart
 * - Fundamental metrics comparison table
 * - Technical indicators comparison
 *
 * Served at `/quant/admin/stock-compare`
 */

export function getStockCompareHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>股票对比 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border)}
.breadcrumb a{color:var(--accent)}
main{max-width:1200px;margin:0 auto;padding:20px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.input-row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.input-row input{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:8px 12px;color:var(--text);font-size:14px;width:120px}
.input-row input:focus{outline:none;border-color:var(--accent)}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.chip{display:inline-flex;align-items:center;gap:4px;background:var(--bg3);border:1px solid var(--border);border-radius:16px;padding:4px 12px;font-size:12px;margin:4px}
.chip .remove{cursor:pointer;color:var(--text-dim);font-size:14px}
.chip .remove:hover{color:var(--red)}
.chart-area{position:relative;height:300px;background:var(--bg3);border-radius:6px;overflow:hidden;margin:12px 0}
.chart-area svg{width:100%;height:100%}
.legend{display:flex;gap:16px;flex-wrap:wrap;margin-top:8px;font-size:12px}
.legend-item{display:flex;align-items:center;gap:4px}
.legend-dot{width:12px;height:3px;border-radius:2px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap;text-align:right}
td:first-child{text-align:left;font-weight:600;color:var(--accent)}
tr:hover{background:var(--bg3)}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.period-btns{display:flex;gap:4px;margin:8px 0}
.period-btns button{background:var(--bg3);border:1px solid var(--border);color:var(--text-dim);padding:4px 12px;border-radius:4px;cursor:pointer;font-size:12px}
.period-btns button:hover,.period-btns button.active{color:var(--accent);border-color:var(--accent)}
</style>
</head>
<body>
<header>
  <h1>⚖️ 股票对比</h1>
  <div class="header-right">
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 股票对比</div>

<main>
<!-- Stock input -->
<div class="card">
  <h3>🔍 添加对比股票 (最多5只)</h3>
  <div class="input-row">
    <input type="text" id="symbolInput" placeholder="输入代码，如 600519" onkeydown="if(event.key==='Enter')addStock()">
    <button class="btn btn-primary" onclick="addStock()">+ 添加</button>
    <button class="btn" onclick="clearStocks()">清空</button>
  </div>
  <div id="chipArea" style="margin-top:8px"></div>
</div>

<!-- Performance Chart -->
<div class="card">
  <h3>📈 涨跌幅对比</h3>
  <div class="period-btns">
    <button onclick="setPeriod(20)" id="p20">20日</button>
    <button onclick="setPeriod(60)" class="active" id="p60">60日</button>
    <button onclick="setPeriod(120)" id="p120">120日</button>
    <button onclick="setPeriod(250)" id="p250">年线</button>
  </div>
  <div class="chart-area" id="chartArea"><svg id="chartSvg"></svg></div>
  <div class="legend" id="legend"></div>
</div>

<!-- Fundamental Comparison -->
<div class="card">
  <h3>📊 基本面对比</h3>
  <div style="overflow-x:auto">
    <div id="fundamentalTable" class="loading">请添加股票</div>
  </div>
</div>

<!-- Technical Comparison -->
<div class="card">
  <h3>📉 技术指标对比</h3>
  <div style="overflow-x:auto">
    <div id="technicalTable" class="loading">请添加股票</div>
  </div>
</div>
</main>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
var stocks=[];
var stockData={};
var period=60;
var COLORS=['#58a6ff','#3fb950','#f85149','#d29922','#bc8cff'];

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function pct(v){return v!=null?(v>=0?'+':'')+Number(v).toFixed(2)+'%':'-'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function addStock(){
  var input=document.getElementById('symbolInput');
  var sym=input.value.trim();
  if(!sym){toast('请输入股票代码');return}
  if(stocks.length>=5){toast('最多5只');return}
  if(stocks.indexOf(sym)>=0){toast('已添加');return}
  stocks.push(sym);
  input.value='';
  renderChips();
  loadStockData(sym);
}

function removeStock(sym){
  stocks=stocks.filter(function(s){return s!==sym});
  delete stockData[sym];
  renderChips();
  renderAll();
}

function clearStocks(){
  stocks=[];stockData={};
  renderChips();
  renderAll();
}

function renderChips(){
  var html='';
  stocks.forEach(function(s,i){
    html+='<span class="chip" style="border-color:'+COLORS[i%COLORS.length]+'">'+
      '<span style="color:'+COLORS[i%COLORS.length]+'">'+esc(s)+'</span>'+
      (stockData[s]?' ('+esc(stockData[s].name||'')+')':'')+
      ' <span class="remove" onclick="removeStock(\\''+s+'\\')">✕</span></span>';
  });
  document.getElementById('chipArea').innerHTML=html;
}

function setPeriod(p){
  period=p;
  document.querySelectorAll('.period-btns button').forEach(function(b){b.classList.remove('active')});
  document.getElementById('p'+p).classList.add('active');
  renderAll();
}

function loadStockData(sym){
  // Fetch quote + klines
  Promise.all([
    fetch(B+'/quant/admin/api/stock-compare?action=quote&symbol='+sym).then(function(r){return r.json()}).catch(function(){return{}}),
    fetch(B+'/quant/admin/api/stock-compare?action=klines&symbol='+sym+'&limit=250').then(function(r){return r.json()}).catch(function(){return{}})
  ]).then(function(results){
    stockData[sym]=Object.assign({},results[0],{klines:results[1].klines||results[1].data||[]});
    renderChips();
    renderAll();
  });
}

function renderAll(){
  renderChart();
  renderFundamentals();
  renderTechnicals();
}

function renderChart(){
  var svg=document.getElementById('chartSvg');
  var rect=svg.getBoundingClientRect();
  var W=rect.width||800,H=300;
  svg.setAttribute('viewBox','0 0 '+W+' '+H);

  if(stocks.length===0){
    svg.innerHTML='<text x="'+W/2+'" y="'+H/2+'" fill="var(--text-dim)" text-anchor="middle" font-size="14">添加股票后显示对比图表</text>';
    document.getElementById('legend').innerHTML='';
    return;
  }

  // Compute normalized returns for each stock
  var series=[];
  stocks.forEach(function(sym,i){
    var d=stockData[sym];
    if(!d||!d.klines||!d.klines.length)return;
    var kl=d.klines.slice(-period);
    if(kl.length<2)return;
    var base=kl[0].close||kl[0][4]||1;
    var returns=kl.map(function(k){return((k.close||k[4]||base)/base-1)*100});
    series.push({sym:sym,returns:returns,color:COLORS[i%COLORS.length]});
  });

  if(!series.length){
    svg.innerHTML='<text x="'+W/2+'" y="'+H/2+'" fill="var(--text-dim)" text-anchor="middle" font-size="14">暂无K线数据</text>';
    return;
  }

  var allVals=[];
  series.forEach(function(s){s.returns.forEach(function(v){allVals.push(v)})});
  var minV=Math.min.apply(null,allVals);
  var maxV=Math.max.apply(null,allVals);
  var range=maxV-minV||1;
  var pad=20;

  var paths='';
  // Zero line
  var zeroY=H-pad-((0-minV)/range*(H-2*pad));
  paths+='<line x1="0" y1="'+zeroY+'" x2="'+W+'" y2="'+zeroY+'" stroke="var(--border)" stroke-dasharray="4,4"/>';

  series.forEach(function(s){
    var maxLen=s.returns.length;
    var pts=s.returns.map(function(v,j){
      var x=pad+(maxLen>1?(j/(maxLen-1)*(W-2*pad)):W/2);
      var y=H-pad-((v-minV)/range*(H-2*pad));
      return x.toFixed(1)+','+y.toFixed(1);
    }).join(' ');
    paths+='<polyline points="'+pts+'" fill="none" stroke="'+s.color+'" stroke-width="2"/>';
    // End label
    var lastV=s.returns[s.returns.length-1];
    var lastY=H-pad-((lastV-minV)/range*(H-2*pad));
    paths+='<text x="'+(W-pad+4)+'" y="'+lastY+'" fill="'+s.color+'" font-size="11" dominant-baseline="middle">'+
      (lastV>=0?'+':'')+lastV.toFixed(1)+'%</text>';
  });

  // Y axis labels
  var steps=5;
  for(var i=0;i<=steps;i++){
    var v=minV+(range/steps*i);
    var y=H-pad-((v-minV)/range*(H-2*pad));
    paths+='<text x="2" y="'+y+'" fill="var(--text-dim)" font-size="10" dominant-baseline="middle">'+v.toFixed(1)+'%</text>';
  }

  svg.innerHTML=paths;

  // Legend
  var leg='';
  series.forEach(function(s){
    var d=stockData[s.sym]||{};
    leg+='<div class="legend-item"><div class="legend-dot" style="background:'+s.color+'"></div>'+esc(s.sym)+' '+ esc(d.name||'')+'</div>';
  });
  document.getElementById('legend').innerHTML=leg;
}

function renderFundamentals(){
  if(!stocks.length){document.getElementById('fundamentalTable').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">请添加股票</div>';return}

  var metrics=[
    {key:'price',label:'最新价'},
    {key:'changePct',label:'涨跌幅',fmt:pct},
    {key:'pe',label:'PE(TTM)'},
    {key:'pb',label:'PB'},
    {key:'marketCap',label:'总市值(亿)',transform:function(v){return v?(v/1e8).toFixed(2):'-'}},
    {key:'volume',label:'成交量(万手)',transform:function(v){return v?(v/1e4).toFixed(0):'-'}},
    {key:'turnoverRate',label:'换手率%'},
    {key:'eps',label:'每股收益'},
    {key:'roe',label:'ROE%'},
    {key:'debtRatio',label:'资产负债率%'},
  ];

  var h='<table><tr><th>指标</th>';
  stocks.forEach(function(s,i){
    var d=stockData[s]||{};
    h+='<th style="color:'+COLORS[i%COLORS.length]+'">'+esc(s)+' '+esc(d.name||'')+'</th>';
  });
  h+='</tr>';

  metrics.forEach(function(m){
    h+='<tr><td>'+m.label+'</td>';
    stocks.forEach(function(s){
      var d=stockData[s]||{};
      var v=d[m.key];
      if(m.transform)h+='<td>'+m.transform(v)+'</td>';
      else if(m.fmt)h+='<td>'+m.fmt(v)+'</td>';
      else h+='<td>'+num(v)+'</td>';
    });
    h+='</tr>';
  });
  h+='</table>';
  document.getElementById('fundamentalTable').innerHTML=h;
}

function renderTechnicals(){
  if(!stocks.length){document.getElementById('technicalTable').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">请添加股票</div>';return}

  var metrics=[
    {key:'ma5',label:'MA5'},
    {key:'ma10',label:'MA10'},
    {key:'ma20',label:'MA20'},
    {key:'ma60',label:'MA60'},
    {key:'rsi',label:'RSI(14)'},
    {key:'macd',label:'MACD'},
    {key:'kdj_k',label:'KDJ-K'},
    {key:'kdj_d',label:'KDJ-D'},
    {key:'boll_upper',label:'BOLL上轨'},
    {key:'boll_lower',label:'BOLL下轨'},
  ];

  var h='<table><tr><th>指标</th>';
  stocks.forEach(function(s,i){
    h+='<th style="color:'+COLORS[i%COLORS.length]+'">'+esc(s)+'</th>';
  });
  h+='</tr>';

  metrics.forEach(function(m){
    h+='<tr><td>'+m.label+'</td>';
    stocks.forEach(function(s){
      var d=stockData[s]||{};
      var tech=d.technicals||d;
      h+='<td>'+num(tech[m.key])+'</td>';
    });
    h+='</tr>';
  });
  h+='</table>';
  document.getElementById('technicalTable').innerHTML=h;
}

// Pre-populate from URL params
var params=new URLSearchParams(window.location.search);
var initSymbols=(params.get('symbols')||'').split(',').filter(Boolean);
initSymbols.forEach(function(s){
  if(stocks.length<5){stocks.push(s.trim())}
});
if(stocks.length){
  renderChips();
  stocks.forEach(function(s){loadStockData(s)});
}
</script>
</body>
</html>`;
}
