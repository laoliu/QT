/**
 * Risk Dashboard Page — 风险管理仪表板
 *
 * Consolidated risk view:
 * - Portfolio drawdown curve
 * - VaR / CVaR metrics
 * - Sector concentration / exposure
 * - Active risk gate status
 * - Position-level risk
 *
 * Served at `/quant/admin/risk-dashboard`
 */

export function getRiskDashboardHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>风险管理 — OpenClaw 量化</title>
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
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:130px;flex:1}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}
.risk-level{display:inline-block;padding:3px 10px;border-radius:4px;font-size:12px;font-weight:600}
.risk-low{background:rgba(63,185,80,.15);color:var(--green)}
.risk-medium{background:rgba(210,153,34,.15);color:var(--yellow)}
.risk-high{background:rgba(248,81,73,.15);color:var(--red)}
.bar-h{height:10px;border-radius:5px;background:var(--bg3);overflow:hidden;margin:4px 0}
.bar-h .fill{height:100%;border-radius:5px;transition:width .3s}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.refresh-btn{background:var(--bg3);border:1px solid var(--border);color:var(--accent);padding:5px 14px;border-radius:6px;cursor:pointer;font-size:12px}
.refresh-btn:hover{border-color:var(--accent)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.dd-chart{position:relative;height:120px;background:var(--bg3);border-radius:6px;overflow:hidden;margin-top:8px}
.dd-chart svg{width:100%;height:100%}
</style>
</head>
<body>
<header>
  <h1>🛡️ 风险管理仪表板</h1>
  <div class="header-right">
    <span id="updateTime" style="color:var(--text-dim)">--</span>
    <button class="refresh-btn" onclick="loadAll()">🔄 刷新</button>
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 风险管理</div>

<main>
<!-- Risk Overview KPIs -->
<div class="kpi-row">
  <div class="kpi"><div class="label">总资产</div><div class="value" id="kpiTotal">--</div></div>
  <div class="kpi"><div class="label">今日盈亏</div><div class="value" id="kpiPnl">--</div></div>
  <div class="kpi"><div class="label">最大回撤</div><div class="value" id="kpiDD" style="color:var(--red)">--</div></div>
  <div class="kpi"><div class="label">夏普比率</div><div class="value" id="kpiSharpe">--</div></div>
  <div class="kpi"><div class="label">VaR (95%)</div><div class="value" id="kpiVaR" style="color:var(--yellow)">--</div></div>
  <div class="kpi"><div class="label">持仓数</div><div class="value" id="kpiPosCount">--</div></div>
</div>

<!-- Overall Risk Level -->
<div class="card">
  <h3>🚦 风险等级</h3>
  <div id="riskLevel" style="text-align:center;padding:16px">
    <div id="riskBadge" class="risk-level risk-low" style="font-size:24px;padding:12px 32px">加载中...</div>
    <div id="riskReasons" style="margin-top:12px;font-size:13px;color:var(--text-dim)"></div>
  </div>
</div>

<!-- Drawdown + Equity -->
<div class="card">
  <h3>📉 回撤曲线</h3>
  <div id="drawdownChart" class="dd-chart"><svg id="ddSvg"></svg></div>
  <div style="font-size:11px;color:var(--text-dim);margin-top:6px;text-align:right" id="ddRange"></div>
</div>

<!-- Concentration & Position Risk -->
<div class="grid-2">
  <div class="card">
    <h3>📊 持仓集中度</h3>
    <div id="concentrationArea" class="loading">加载中...</div>
  </div>
  <div class="card">
    <h3>⚠️ 持仓风险明细</h3>
    <div id="positionRisk" class="loading">加载中...</div>
  </div>
</div>

<!-- Risk Gate Status -->
<div class="card">
  <h3>🔒 风控规则状态</h3>
  <div id="riskGateArea" class="loading">加载中...</div>
</div>

<!-- Recent Alerts -->
<div class="card">
  <h3>🔔 近期风控警报</h3>
  <div id="alertsArea" class="loading">加载中...</div>
</div>
</main>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function pct(v){return v!=null?(v>=0?'+':'')+Number(v).toFixed(2)+'%':'-'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}
function api(path){
  return fetch(B+path).then(function(r){
    if(!r.ok) return r.json().catch(function(){return{}}).then(function(b){throw new Error(b.error||r.status)});
    return r.json();
  }).catch(function(e){toast('❌ '+e.message);throw e});
}

function loadAll(){
  document.getElementById('updateTime').textContent='加载中...';
  Promise.allSettled([loadPortfolio(), loadEquity(), loadAlerts()]).then(function(){
    document.getElementById('updateTime').textContent='更新: '+new Date().toLocaleTimeString('zh-CN');
  });
}

function loadPortfolio(){
  return api('/quant/api/portfolio').then(function(d){
    var totalVal=d.totalValue||0;
    document.getElementById('kpiTotal').textContent=num(totalVal);

    var pnl=d.dailyPnl||0;
    var pnlEl=document.getElementById('kpiPnl');
    pnlEl.textContent=(pnl>=0?'+':'')+num(pnl);
    pnlEl.style.color=pnl>=0?'var(--green)':'var(--red)';

    var positions=d.positions||[];
    document.getElementById('kpiPosCount').textContent=positions.length;

    // Position risk table
    if(positions.length){
      var h='<table><tr><th>代码</th><th>名称</th><th>市值</th><th>占比</th><th>盈亏%</th><th>风险</th></tr>';
      positions.forEach(function(p){
        var mv=p.marketValue||p.quantity*(p.currentPrice||0);
        var weight=totalVal>0?(mv/totalVal*100):0;
        var pnlPct=p.unrealizedPnlPct||0;
        var risk='low';
        if(weight>30||pnlPct<-10)risk='high';
        else if(weight>20||pnlPct<-5)risk='medium';
        h+='<tr><td>'+esc(p.symbol||p.code)+'</td><td>'+esc(p.name||'-')+'</td>'+
          '<td>'+num(mv)+'</td><td>'+num(weight)+'%</td>'+
          '<td style="color:'+(pnlPct>=0?'var(--green)':'var(--red)')+'">'+pct(pnlPct)+'</td>'+
          '<td><span class="risk-level risk-'+risk+'">'+(risk==='high'?'高':risk==='medium'?'中':'低')+'</span></td></tr>';
      });
      h+='</table>';
      document.getElementById('positionRisk').innerHTML=h;

      // Concentration bars
      var sortedPos=positions.slice().sort(function(a,b){
        return(b.marketValue||b.quantity*(b.currentPrice||0))-(a.marketValue||a.quantity*(a.currentPrice||0))});
      var ch='';
      sortedPos.slice(0,10).forEach(function(p){
        var mv=p.marketValue||p.quantity*(p.currentPrice||0);
        var w=totalVal>0?(mv/totalVal*100):0;
        var color=w>30?'var(--red)':w>20?'var(--yellow)':'var(--green)';
        ch+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'+
          '<div style="width:80px;font-size:12px;text-align:right">'+esc(p.symbol||p.code)+'</div>'+
          '<div class="bar-h" style="flex:1"><div class="fill" style="width:'+Math.min(w,100)+'%;background:'+color+'"></div></div>'+
          '<div style="width:50px;font-size:12px">'+num(w)+'%</div></div>';
      });
      document.getElementById('concentrationArea').innerHTML=ch||'<div style="color:var(--text-dim)">无持仓</div>';
    } else {
      document.getElementById('positionRisk').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">无持仓</div>';
      document.getElementById('concentrationArea').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">无持仓</div>';
    }
  }).catch(function(){});
}

function loadEquity(){
  return api('/quant/api/equity-history/summary').then(function(d){
    var dd=d.maxDrawdownPct||d.maxDrawdown||0;
    document.getElementById('kpiDD').textContent=pct(-Math.abs(dd));

    var sharpe=d.sharpeRatio||d.sharpe||null;
    document.getElementById('kpiSharpe').textContent=sharpe!=null?Number(sharpe).toFixed(2):'-';

    // Estimate VaR from equity data
    var dailyPnls=d.dailyReturns||[];
    if(dailyPnls.length>5){
      var sorted=dailyPnls.slice().sort(function(a,b){return a-b});
      var idx=Math.floor(sorted.length*0.05);
      var var95=sorted[idx]||0;
      document.getElementById('kpiVaR').textContent=pct(var95);
    }

    // Risk level computation
    var riskScore=0;
    var reasons=[];
    if(Math.abs(dd)>20){riskScore+=3;reasons.push('最大回撤 '+pct(-Math.abs(dd)))}
    else if(Math.abs(dd)>10){riskScore+=2;reasons.push('最大回撤 '+pct(-Math.abs(dd)))}
    else if(Math.abs(dd)>5){riskScore+=1;reasons.push('最大回撤 '+pct(-Math.abs(dd)))}

    if(sharpe!=null){
      if(sharpe<0){riskScore+=2;reasons.push('夏普比率为负')}
      else if(sharpe<0.5){riskScore+=1;reasons.push('夏普比率偏低')}
    }

    var badge=document.getElementById('riskBadge');
    if(riskScore>=4){badge.className='risk-level risk-high';badge.textContent='高风险'}
    else if(riskScore>=2){badge.className='risk-level risk-medium';badge.textContent='中风险'}
    else{badge.className='risk-level risk-low';badge.textContent='低风险'}
    document.getElementById('riskReasons').textContent=reasons.join(' | ')||'各项指标正常';

    // Drawdown chart (SVG)
    drawDrawdownChart(d.equityCurve||d.snapshots||[]);
    document.getElementById('ddRange').textContent=
      '数据范围: '+(d.startDate||'-')+' ~ '+(d.endDate||'-')+' ('+((d.snapshots||d.equityCurve||[]).length)+' 天)';
  }).catch(function(){});
}

function drawDrawdownChart(snapshots){
  if(!snapshots.length)return;
  var svg=document.getElementById('ddSvg');
  var w=svg.getBoundingClientRect().width||800;
  var h=120;
  svg.setAttribute('viewBox','0 0 '+w+' '+h);

  var peak=0;
  var dds=snapshots.map(function(s){
    var eq=s.equity||s.totalEquity||s;
    if(eq>peak)peak=eq;
    return peak>0?((eq-peak)/peak*100):0;
  });
  var minDD=Math.min.apply(null,dds);
  if(minDD>=0)minDD=-1;

  var pts=dds.map(function(dd,i){
    var x=dds.length>1?(i/(dds.length-1)*w):w/2;
    var y=h-(-dd/(-minDD)*h);
    return x.toFixed(1)+','+y.toFixed(1);
  });

  svg.innerHTML='<defs><linearGradient id="ddGrad" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0%" stop-color="'+('var(--red)')+'" stop-opacity="0.3"/>'+
    '<stop offset="100%" stop-color="'+('var(--red)')+'" stop-opacity="0.05"/>'+
    '</linearGradient></defs>'+
    '<polygon points="0,0 '+pts.join(' ')+' '+w+',0" fill="url(#ddGrad)"/>'+
    '<polyline points="'+pts.join(' ')+'" fill="none" stroke="var(--red)" stroke-width="1.5"/>';
}

function loadAlerts(){
  return api('/quant/api/alerts?limit=20').then(function(d){
    var alerts=(d.alerts||d||[]).slice(0,15);
    if(!alerts.length){
      document.getElementById('alertsArea').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">无风控警报</div>';
      return;
    }
    var h='<table><tr><th>时间</th><th>级别</th><th>来源</th><th>内容</th></tr>';
    alerts.forEach(function(a){
      var sev=a.severity||'info';
      var cls=sev==='critical'?'risk-high':sev==='warning'?'risk-medium':'risk-low';
      h+='<tr><td>'+esc(a.time||a.timestamp||'-').substring(0,19)+'</td>'+
        '<td><span class="risk-level '+cls+'">'+esc(sev)+'</span></td>'+
        '<td>'+esc(a.source||'-')+'</td>'+
        '<td style="white-space:normal;max-width:400px">'+esc(a.message||a.text||'-')+'</td></tr>';
    });
    h+='</table>';
    document.getElementById('alertsArea').innerHTML=h;
  }).catch(function(){});
}

// Risk gate status
api('/quant/admin/api/engine-status').then(function(d){
  var rg=d.riskGate||{};
  var rules=[
    {name:'单笔限额',status:rg.maxOrderValue?'启用':'未配置'},
    {name:'集中度限制',status:rg.maxPositionPct?'启用':'未配置'},
    {name:'日亏损限制',status:rg.dailyLossStop?'启用':'未配置'},
    {name:'总亏损限制',status:rg.totalLossStop?'启用':'未配置'},
    {name:'下单频率限制',status:rg.orderFrequency?'启用':'未配置'},
  ];
  var h='<table><tr><th>规则</th><th>状态</th></tr>';
  rules.forEach(function(r){
    var cls=r.status==='启用'?'risk-low':'risk-medium';
    h+='<tr><td>'+esc(r.name)+'</td><td><span class="risk-level '+cls+'">'+esc(r.status)+'</span></td></tr>';
  });
  h+='</table>';
  document.getElementById('riskGateArea').innerHTML=h;
}).catch(function(){
  document.getElementById('riskGateArea').innerHTML='<div style="color:var(--text-dim);padding:12px">引擎未启动</div>';
});

loadAll();
setInterval(loadAll, 30000);
</script>
</body>
</html>`;
}
