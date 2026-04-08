/**
 * Sentiment Dashboard Page — 市场情绪面板
 *
 * Sections: 市场宽度 (breadth), 两融数据 (margin), 北向资金 (northbound)
 * Data fetched from `/quant/admin/api/sentiment-dashboard?action=...`
 */

export function getSentimentDashboardHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>市场情绪面板 — OpenClaw 量化</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between}
header h1{font-size:18px;font-weight:600}
.container{max-width:1200px;margin:0 auto;padding:24px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(350px,1fr));gap:16px;margin-top:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px}
.card h2{font-size:15px;margin-bottom:12px;color:var(--accent)}
.loading{color:var(--text-dim);font-style:italic}
.kpi{font-size:24px;font-weight:700;margin:8px 0}
.kpi.green{color:var(--green)}.kpi.red{color:var(--red)}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500}
.error{color:var(--red);font-size:13px}
</style>
</head>
<body>
<header>
  <h1>📊 市场情绪面板</h1>
  <a href="${b}/quant/">← 返回首页</a>
</header>
<div class="container">
  <div class="grid">
    <div class="card" id="card-breadth">
      <h2>📈 市场宽度</h2>
      <div class="loading" id="breadth-content">加载中…</div>
    </div>
    <div class="card" id="card-margin">
      <h2>💰 两融数据</h2>
      <div class="loading" id="margin-content">加载中…</div>
    </div>
    <div class="card" id="card-northbound">
      <h2>🌏 北向资金</h2>
      <div class="loading" id="northbound-content">加载中…</div>
    </div>
  </div>
</div>
<script>
var B="${b}";
function api(action){
  return fetch(B+"/quant/admin/api/sentiment-dashboard?action="+action)
    .then(function(r){return r.json()});
}
function fmtVal(v){
  if(v==null)return'-';
  if(typeof v==='number'){
    if(Math.abs(v)>=1e8)return(v/1e8).toFixed(2)+'亿';
    if(Math.abs(v)>=1e4)return(v/1e4).toFixed(2)+'万';
    return v.toLocaleString();
  }
  if(typeof v==='string')return v;
  return JSON.stringify(v);
}
function fmtYi(v){
  if(v==null)return'-';
  if(typeof v==='number')return v.toFixed(2)+'亿';
  return String(v);
}
function fmtPct(v){
  if(v==null)return'-';
  return Number(v).toFixed(2)+'%';
}
var LABELS={
  advancers:'上涨家数',decliners:'下跌家数',unchanged:'平盘',total:'总数',
  advanceRatio:'上涨比例',limitUp:'涨停',limitDown:'跌停',
  realLimitUp:'真实涨停',realLimitDown:'真实跌停',
  suspended:'停牌',activity:'活跃度',
  balance:'融资余额',buyAmount:'融资买入额',shortBalance:'融券余额',
  shortSellAmount:'融券卖出额',collateralValue:'担保物总价值',
  avgGuaranteeRatio:'平均维持担保比例',investors:'负债投资者数',
  tradingInvestors:'参与交易投资者数',brokerCount:'证券公司数',
  date:'日期',action:'',historyCount:''
};
function renderBreadth(el,d){
  if(d.error){el.innerHTML='<div class="error">'+d.error+'</div>';return;}
  var keys=['advancers','decliners','unchanged','limitUp','realLimitUp','limitDown','realLimitDown','advanceRatio','activity','suspended','total'];
  var html='<div style="margin-bottom:8px;font-size:12px;color:var(--text-dim)">'+( d.date||'')+'</div>';
  html+='<div style="display:flex;gap:12px;flex-wrap:wrap">';
  keys.forEach(function(k){
    if(d[k]==null)return;
    var v=d[k];
    var color='';
    if(k==='advancers'||k==='limitUp'||k==='realLimitUp')color='color:var(--red)';
    if(k==='decliners'||k==='limitDown'||k==='realLimitDown')color='color:var(--green)';
    if(k==='advanceRatio')v=(v*100).toFixed(1)+'%';
    html+='<div style="background:var(--bg3);border-radius:6px;padding:10px 14px;min-width:100px"><div style="font-size:11px;color:var(--text-dim)">'+(LABELS[k]||k)+'</div><div class="kpi" style="font-size:20px;'+color+'">'+v+'</div></div>';
  });
  html+='</div>';
  el.innerHTML=html;
}
function renderMargin(el,d){
  if(d.error){el.innerHTML='<div class="error">'+d.error+'</div>';return;}
  var rows=[
    ['日期', d.date],
    ['融资余额', fmtYi(d.balance)],
    ['融资买入额', fmtYi(d.buyAmount)],
    ['融券余额', fmtYi(d.shortBalance)],
    ['融券卖出额', fmtYi(d.shortSellAmount)],
    ['担保物总价值', fmtYi(d.collateralValue)],
    ['平均维持担保比例', d.avgGuaranteeRatio!=null?fmtPct(d.avgGuaranteeRatio):'-'],
    ['负债投资者数', fmtVal(d.investors)],
    ['券商数量', d.brokerCount||'-'],
  ];
  var html='<table>';
  rows.forEach(function(r){if(r[1]!=null&&r[1]!=='-'&&r[1]!=='')html+='<tr><th>'+r[0]+'</th><td>'+r[1]+'</td></tr>';});
  html+='</table>';
  // Trend table
  var hist=d.history||[];
  if(hist.length>1){
    html+='<div style="margin-top:14px;font-size:12px;color:var(--text-dim)">近期趋势</div>';
    html+='<table><thead><tr><th>日期</th><th>融资余额(亿)</th><th>融资买入(亿)</th><th>维保比例%</th></tr></thead><tbody>';
    hist.forEach(function(r){
      html+='<tr><td>'+r.date+'</td><td>'+fmtYi(r.balance)+'</td><td>'+fmtYi(r.buyAmount)+'</td><td>'+(r.avgGuaranteeRatio!=null?fmtPct(r.avgGuaranteeRatio):'-')+'</td></tr>';
    });
    html+='</tbody></table>';
  }
  el.innerHTML=html;
}
function renderNorthbound(el,d){
  if(d.error){el.innerHTML='<div class="error">'+d.error+'</div>';return;}
  var rt=d.realtime||{};
  var html='<div style="margin-bottom:8px;font-size:12px;color:var(--text-dim)">'+( rt.date||'')+' 北向 (单位: 亿元)</div>';
  html+='<div style="display:flex;gap:12px;flex-wrap:wrap">';
  [{k:'hk2sh',n:'沪股通'},{k:'hk2sz',n:'深股通'},{k:'northTotal',n:'北向合计'}].forEach(function(item){
    var v=rt[item.k];
    if(v==null)v=0;
    var color=v>0?'color:var(--red)':v<0?'color:var(--green)':'';
    html+='<div style="background:var(--bg3);border-radius:6px;padding:10px 14px;min-width:120px"><div style="font-size:11px;color:var(--text-dim)">'+item.n+'</div><div class="kpi" style="font-size:18px;'+color+'">'+fmtYi(v)+'</div></div>';
  });
  html+='</div>';
  // Note about data policy
  if(d.note){html+='<div style="margin-top:8px;padding:8px 12px;background:var(--bg3);border-radius:4px;font-size:12px;color:var(--yellow)">⚠️ '+d.note+'</div>';}
  // Southbound
  var sb=d.southbound;
  if(sb){
    html+='<div style="margin-top:12px;display:flex;gap:12px;flex-wrap:wrap">';
    [{k:'sh2hk',n:'港股通(沪)'},{k:'sz2hk',n:'港股通(深)'},{k:'southTotal',n:'南向合计'}].forEach(function(item){
      var v=sb[item.k];if(v==null)v=0;
      var color=v>0?'color:var(--red)':v<0?'color:var(--green)':'';
      html+='<div style="background:var(--bg3);border-radius:6px;padding:8px 12px;min-width:110px"><div style="font-size:11px;color:var(--text-dim)">'+item.n+'</div><div style="font-size:16px;font-weight:600;'+color+'">'+fmtYi(v)+'</div></div>';
    });
    html+='</div>';
  }
  // History: market fund flow
  var hist=d.history||[];
  if(hist.length){
    var htype=d.historyType==='market_fund_flow'?'市场主力资金流向':'北向历史';
    html+='<div style="margin-top:14px;font-size:12px;color:var(--text-dim)">'+htype+' (近'+hist.length+'日)</div>';
    html+='<table><thead><tr><th>日期</th><th>上证</th><th>涨跌%</th><th>主力净流入</th><th>净占比</th><th>超大单净流入</th></tr></thead><tbody>';
    hist.forEach(function(r){
      if(!r.date)return;
      var mainV=r.mainNetIn||0;
      var mc=mainV>0?'color:var(--red)':mainV<0?'color:var(--green)':'';
      html+='<tr><td>'+r.date+'</td><td>'+(r.shIndex||'-')+'</td><td>'+(r.shChg!=null?r.shChg.toFixed(2)+'%':'-')+'</td>';
      html+='<td style="'+mc+'">'+fmtVal(mainV)+'</td><td>'+(r.mainPct!=null?r.mainPct.toFixed(2)+'%':'-')+'</td>';
      html+='<td>'+fmtVal(r.bigNetIn||0)+'</td></tr>';
    });
    html+='</tbody></table>';
  }
  el.innerHTML=html;
}
api("breadth").then(function(d){renderBreadth(document.getElementById("breadth-content"),d)}).catch(function(e){document.getElementById("breadth-content").innerHTML='<div class="error">'+e+'</div>'});
api("margin").then(function(d){renderMargin(document.getElementById("margin-content"),d)}).catch(function(e){document.getElementById("margin-content").innerHTML='<div class="error">'+e+'</div>'});
api("northbound").then(function(d){renderNorthbound(document.getElementById("northbound-content"),d)}).catch(function(e){document.getElementById("northbound-content").innerHTML='<div class="error">'+e+'</div>'});
</script>
</body>
</html>`;
}
