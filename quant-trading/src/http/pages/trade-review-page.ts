/**
 * Trade Review Page (UI-7) — 复盘 K 1700
 *
 * 00 lightweight-charts 0505 K 线并标注买卖点
 */
export function getTradeReviewPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 复盘 K 线</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>97</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px;align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
input,select{background:var(--bg3);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px}
main{padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:15px}
#chart{width:100%;height:450px}
.form-row{display:flex;gap:10px;align-items:center;margin-bottom:16px;flex-wrap:wrap}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid var(--border);text-align:left}th{color:var(--text-dim);font-size:11px}
.buy-marker{color:var(--green)}.sell-marker{color:var(--red)}
.empty{padding:24px;text-align:center;color:var(--text-dim)}
.legend{display:flex;gap:16px;margin-bottom:8px;font-size:12px}
.legend span{display:flex;align-items:center;gap:4px}
@media(max-width:640px){#chart{height:300px}.form-row{flex-direction:column}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>97 复盘 K 线</span></div>
<header>
  <h1>97 复盘 K 1700</h1>
  <div class="actions">
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <div class="form-row">
    <input id="symbol" placeholder="输入股票代码" value="">
    <select id="period">
      <option value="60">近60天</option>
      <option value="120" selected>近120天</option>
      <option value="250">05</option>
    </select>
    <button class="btn" onclick="loadChart()">加载</button>
  </div>

  <div class="card">
    <h2 id="chart-title">K 00</h2>
    <div class="legend">
      <span><span style="color:var(--green)">▲</span> 买入</span>
      <span><span style="color:var(--red)">▼</span> 卖出</span>
    </div>
    <div id="chart"><div class="empty">请输入股票代码后点击加载</div></div>
  </div>

  <div class="card">
    <h2>0604</h2>
    <div id="trades-table"><div class="empty">请先加载 K 线</div></div>
  </div>
</main>

<script src="https://unpkg.com/lightweight-charts@4.1.0/dist/lightweight-charts.standalone.production.js"></script>
<script>
const B='${b}';
let chart=null,candleSeries=null,volumeSeries=null;
let buyMarkers=[],sellMarkers=[];

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}

function createChart(){
  const container=document.getElementById('chart');
  container.innerHTML='';
  chart=LightweightCharts.createChart(container,{
    width:container.clientWidth,
    height:450,
    layout:{background:{type:'solid',color:'#161b22'},textColor:'#c9d1d9'},
    grid:{vertLines:{color:'#21262d'},horzLines:{color:'#21262d'}},
    timeScale:{borderColor:'#30363d'},
    rightPriceScale:{borderColor:'#30363d'},
    crosshair:{mode:0},
  });
  candleSeries=chart.addCandlestickSeries({
    upColor:'#3fb950',downColor:'#f85149',
    borderUpColor:'#3fb950',borderDownColor:'#f85149',
    wickUpColor:'#3fb950',wickDownColor:'#f85149',
  });
  volumeSeries=chart.addHistogramSeries({
    color:'rgba(88,166,255,0.3)',
    priceFormat:{type:'volume'},
    priceScaleId:'volume',
  });
  chart.priceScale('volume').applyOptions({scaleMargins:{top:0.85,bottom:0}});
  window.addEventListener('resize',()=>{chart.applyOptions({width:container.clientWidth})});
}

async function loadChart(){
  const symbol=document.getElementById('symbol').value.trim();
  if(!symbol)return;
  const days=parseInt(document.getElementById('period').value);

  try{
    // Fetch klines
    const end=new Date().toISOString().slice(0,10);
    const start=new Date(Date.now()-days*86400000).toISOString().slice(0,10);
    const kRes=await fetch(B+'/quant/api/klines?symbol='+symbol+'&start='+start+'&end='+end+'&interval=1d');
    if(!kRes.ok)throw new Error('K170002');
    const kData=await kRes.json();
    const bars=kData.klines||kData||[];

    if(bars.length===0){
      document.getElementById('chart').innerHTML='<div class="empty">无 K 线数据</div>';
      return;
    }

    if(!chart)createChart();

    const candleData=bars.map(b=>({
      time:b.date||b.timestamp,
      open:b.open,high:b.high,low:b.low,close:b.close,
    }));
    const volumeData=bars.map(b=>({
      time:b.date||b.timestamp,
      value:b.volume||0,
      color:b.close>=b.open?'rgba(63,185,80,0.3)':'rgba(248,81,73,0.3)',
    }));

    candleSeries.setData(candleData);
    volumeSeries.setData(volumeData);

    document.getElementById('chart-title').textContent=symbol+' K 00 ('+bars.length+'天)';

    // Fetch trades for this symbol
    const tRes=await fetch(B+'/quant/api/orders?symbol='+symbol+'&limit=100').catch(()=>null);
    let trades=[];
    if(tRes&&tRes.ok){
      const tData=await tRes.json();
      trades=Array.isArray(tData)?tData:(tData.orders||[]);
    }

    // Add markers
    const markers=[];
    for(const t of trades){
      const time=t.filledTime||t.createTime||'';
      const dateStr=time.slice(0,10);
      if(!dateStr)continue;
      const isBuy=t.side==='buy'||t.side==='cover';
      markers.push({
        time:dateStr,
        position:isBuy?'belowBar':'aboveBar',
        color:isBuy?'#3fb950':'#f85149',
        shape:isBuy?'arrowUp':'arrowDown',
        text:(isBuy?'买':'卖')+' '+num(t.filledPrice||t.price),
      });
    }
    if(markers.length>0){
      markers.sort((a,b)=>a.time<b.time?-1:a.time>b.time?1:0);
      candleSeries.setMarkers(markers);
    }

    chart.timeScale().fitContent();

    // Render trades table
    if(trades.length===0){
      document.getElementById('trades-table').innerHTML='<div class="empty">10000604</div>';
    }else{
      let h='<table><tr><th>02</th><th>方向</th><th>18</th><th>数量</th><th>07</th><th>策略</th></tr>';
      for(const t of trades){
        const pnl=t.netPnl||0;
        const pnlCol=pnl>0?'var(--green)':pnl<0?'var(--red)':'var(--text)';
        h+='<tr><td>'+esc((t.filledTime||t.createTime||'').slice(0,19))+'</td>';
        h+='<td class="'+(t.side==='buy'?'buy-marker':'sell-marker')+'">'+esc(t.side)+'</td>';
        h+='<td>'+num(t.filledPrice||t.price)+'</td><td>'+t.quantity+'</td>';
        h+='<td style="color:'+pnlCol+'">'+(pnl?num(pnl):'-')+'</td>';
        h+='<td>'+esc(t.strategyId||'-')+'</td></tr>';
      }
      h+='</table>';
      document.getElementById('trades-table').innerHTML=h;
    }
  }catch(e){
    document.getElementById('chart').innerHTML='<div class="empty">'+esc(e.message)+'</div>';
  }
}

// Auto-load if symbol in URL params
const params=new URLSearchParams(location.search);
if(params.get('symbol')){
  document.getElementById('symbol').value=params.get('symbol');
  loadChart();
}
</script>
</body>
</html>`;
}
