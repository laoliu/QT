/**
 * Stock Analysis Panel (UI-5) — 个股综合分析面板
 *
 * 0507040505: 基本面 + 技术面 + 资金流向 + 情绪指标
 */
export function getStockAnalysisPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 个股分析</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>93</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px;align-items:center}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:var(--accent);color:#000;border-color:var(--accent)}
.search-box{display:flex;gap:8px;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
.search-box input{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:8px 12px;color:var(--text);font-size:14px;width:200px}
.search-box input:focus{outline:none;border-color:var(--accent)}
main{padding:20px 24px}
.stock-header{display:flex;align-items:baseline;gap:16px;margin-bottom:16px}
.stock-name{font-size:22px;font-weight:700}
.stock-code{font-size:14px;color:var(--text-dim)}
.stock-price{font-size:28px;font-weight:700;margin-left:auto}
.stock-change{font-size:14px;margin-left:8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:10px;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:10px;text-align:center}
.kpi-val{font-size:14px;font-weight:700}.kpi-label{font-size:10px;color:var(--text-dim);margin-top:2px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px;margin-bottom:16px}
.card h2{margin:0 0 12px;font-size:14px;display:flex;align-items:center;gap:6px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:8px 6px;border-bottom:1px solid var(--border);text-align:left}
th{color:var(--text-dim);font-size:10px}tr:hover{background:rgba(255,255,255,.02)}
.up{color:var(--green)}.down{color:var(--red)}
.gauge{position:relative;height:24px;background:var(--bg3);border-radius:12px;overflow:hidden;margin:4px 0}
.gauge-fill{height:100%;border-radius:12px;transition:width .5s}
.gauge-label{position:absolute;right:8px;top:4px;font-size:10px;font-weight:700}
.section-title{font-size:13px;font-weight:600;color:var(--text-dim);margin:20px 0 10px;border-bottom:1px solid var(--border);padding-bottom:6px}
.empty{padding:40px;text-align:center;color:var(--text-dim)}
.signal-badge{display:inline-block;padding:3px 8px;border-radius:10px;font-size:11px;font-weight:600}
.signal-buy{background:rgba(63,185,80,.2);color:var(--green)}.signal-sell{background:rgba(248,81,73,.2);color:var(--red)}
.signal-neutral{background:rgba(139,148,158,.2);color:var(--text-dim)}
@media(max-width:768px){.grid2,.grid3{grid-template-columns:1fr}.stock-header{flex-wrap:wrap}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>93 个股分析</span></div>
<header>
  <h1>93 1005</h1>
  <div class="actions">
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<div class="search-box">
  <input type="text" id="symbol-input" placeholder="输入股票代码 如 000001" onkeydown="if(event.key==='Enter')analyze()">
  <button class="btn btn-primary" onclick="analyze()">分析</button>
  <span id="loading" style="display:none;font-size:12px;color:var(--text-dim)">加载中...</span>
</div>
<main id="main-content">
  <div class="empty" id="placeholder">071303</div>
</main>

<script>
const B='${b}';
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v,d=2){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:d,maximumFractionDigits:d}):'-'}
function pctStr(v){if(v==null)return'-';const n=Number(v);return(n>=0?'+':'')+n.toFixed(2)+'%'}

async function callTool(name,args){
  try{
    const res=await fetch(B+'/quant/api/tools/'+name,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(args)});
    if(!res.ok)return null;
    return await res.json();
  }catch(e){return null}
}

async function analyze(){
  const sym=document.getElementById('symbol-input').value.trim();
  if(!sym)return;
  document.getElementById('loading').style.display='inline';
  document.getElementById('placeholder').style.display='none';

  // Parallel fetch all data
  const [quote,fund,tech,sr,sentiment]=await Promise.all([
    callTool('stock_quote',{symbol:sym}),
    callTool('fund_flow_analysis',{symbol:sym,action:'summary'}),
    callTool('technical_indicator',{symbol:sym,indicators:['MA','RSI','MACD','KDJ','BOLL']}),
    callTool('support_resistance',{symbol:sym,action:'detect'}),
    callTool('market_sentiment_index',{action:'current'}),
  ]);

  document.getElementById('loading').style.display='none';

  let html='';

  // Stock header
  const q=quote||{};
  const chg=Number(q.changePercent||q.pct_chg||0);
  const chgCls=chg>=0?'up':'down';
  html+='<div class="stock-header">';
  html+='<span class="stock-name">'+esc(q.name||q.stockName||sym)+'</span>';
  html+='<span class="stock-code">'+esc(sym)+'</span>';
  html+='<span class="stock-price '+chgCls+'">'+num(q.price||q.close||q.current)+'</span>';
  html+='<span class="stock-change '+chgCls+'">'+pctStr(chg)+'</span>';
  html+='</div>';

  // Quote KPIs
  html+='<div class="kpis">';
  const kpis=[
    ['开盘',q.open],['最高',q.high],['最低',q.low],['00',q.volume!=null?(Number(q.volume)/10000).toFixed(0)+'万手':null],
    ['00',q.amount!=null?(Number(q.amount)/100000000).toFixed(2)+'亿':null],['换手率',q.turnoverRate!=null?Number(q.turnoverRate).toFixed(2)+'%':null],
    ['PE',q.pe!=null?Number(q.pe).toFixed(1):null],['PB',q.pb!=null?Number(q.pb).toFixed(2):null],
  ];
  for(const [label,val] of kpis){
    html+='<div class="kpi"><div class="kpi-val">'+esc(val!=null?val:'-')+'</div><div class="kpi-label">'+label+'</div></div>';
  }
  html+='</div>';

  // Two columns
  html+='<div class="grid2">';

  // Technical indicators
  html+='<div class="card"><h2>94 技术指标</h2>';
  if(tech){
    html+='<table><tr><th>08</th><th>05</th><th>02</th></tr>';
    const indicators=tech.indicators||tech.data||[];
    if(Array.isArray(indicators)){
      for(const ind of indicators){
        const sig=ind.signal||'neutral';
        const sigCls=sig.includes('buy')?'signal-buy':sig.includes('sell')?'signal-sell':'signal-neutral';
        const sigText=sig.includes('buy')?'看多':sig.includes('sell')?'看空':'中性';
        html+='<tr><td>'+esc(ind.name||ind.indicator)+'</td><td>'+esc(ind.value||ind.display||'-')+'</td>';
        html+='<td><span class="signal-badge '+sigCls+'">'+sigText+'</span></td></tr>';
      }
    }
    html+='</table>';
  }else{html+='<div class="empty">技术指标加载失败</div>'}
  html+='</div>';

  // Fund flow
  html+='<div class="card"><h2>90 资金流向</h2>';
  if(fund){
    html+='<table><tr><th>类型</th><th>流入</th><th>流出</th><th>净流入</th></tr>';
    const flows=fund.flows||fund.data||[];
    if(Array.isArray(flows)){
      for(const f of flows){
        const net=Number(f.netInflow||f.net||0);
        const cls=net>=0?'up':'down';
        html+='<tr><td>'+esc(f.type||f.label||'-')+'</td><td>'+num(f.inflow||0)+'</td><td>'+num(f.outflow||0)+'</td>';
        html+='<td class="'+cls+'">'+num(net)+'</td></tr>';
      }
    }
    const netTotal=Number(fund.netInflow||fund.total_net||0);
    const nCls=netTotal>=0?'up':'down';
    html+='<tr style="font-weight:700"><td>00</td><td></td><td></td><td class="'+nCls+'">'+num(netTotal)+'</td></tr>';
    html+='</table>';
  }else{html+='<div class="empty">资金流向加载失败</div>'}
  html+='</div>';

  html+='</div>'; // end grid2

  // Support/Resistance
  html+='<div class="card"><h2>99 01/λ</h2>';
  if(sr&&sr.levels){
    html+='<table><tr><th>类型</th><th>λ</th><th>06</th></tr>';
    for(const lv of sr.levels){
      html+='<tr><td>'+esc(lv.type==='support'?'01':'阻力')+'</td><td>'+num(lv.price)+'</td>';
      html+='<td>';
      const str=Math.round((lv.strength||0.5)*5);
      for(let i=0;i<5;i++)html+='<span style="color:'+(i<str?'var(--yellow)':'var(--bg3)')+'">★</span>';
      html+='</td></tr>';
    }
    html+='</table>';
  }else{html+='<div class="empty">支撑阻力加载失败</div>'}
  html+='</div>';

  // Market sentiment
  html+='<div class="card"><h2>9115 市场情绪</h2>';
  if(sentiment){
    const score=Number(sentiment.score||50);
    const color=score>=60?'var(--green)':score<=40?'var(--red)':'var(--yellow)';
    html+='<div class="gauge"><div class="gauge-fill" style="width:'+score+'%;background:'+color+'"></div><div class="gauge-label">'+score.toFixed(0)+'</div></div>';
    html+='<div style="font-size:12px;color:var(--text-dim);margin-top:4px">'+esc(sentiment.mood||'')+'</div>';
  }else{html+='<div class="empty">情绪指标加载失败</div>'}
  html+='</div>';

  document.getElementById('main-content').innerHTML=html;
}

// Auto-analyze if URL has symbol param
const urlSym=new URLSearchParams(location.search).get('symbol');
if(urlSym){document.getElementById('symbol-input').value=urlSym;analyze()}
</script>
</body>
</html>`;
}
