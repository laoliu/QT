/**
 * Market Sentiment Dashboard — 市场情绪面板
 *
 * Consolidated view of market sentiment indicators:
 * - 涨跌比 (Advance/Decline ratio)
 * - 涨停/跌停数 (Limit-up / Limit-down counts)
 * - 两融余额 (Margin trading balance)
 * - 北向资金 (Northbound capital flow)
 *
 * Served at `/quant/admin/sentiment-dashboard`
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
main{max-width:1200px;margin:0 auto;padding:20px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:130px;flex:1}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:22px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:8px 10px;border-bottom:2px solid var(--border);color:var(--text-dim);font-weight:600;font-size:12px;white-space:nowrap}
td{padding:7px 10px;border-bottom:1px solid var(--border);white-space:nowrap}
tr:hover{background:var(--bg3)}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.bar-chart{display:flex;align-items:flex-end;gap:2px;height:60px;margin:10px 0}
.bar-chart .bar{flex:1;min-width:4px;border-radius:2px 2px 0 0;transition:height .3s}
.ratio-bar{display:flex;height:24px;border-radius:4px;overflow:hidden;margin:8px 0}
.ratio-bar .seg{height:100%;display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:600;color:#fff}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.refresh-btn{background:var(--bg3);border:1px solid var(--border);color:var(--accent);padding:5px 14px;border-radius:6px;cursor:pointer;font-size:12px}
.refresh-btn:hover{border-color:var(--accent)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.mood-meter{text-align:center;padding:16px}
.mood-meter .emoji{font-size:48px}
.mood-meter .label{font-size:16px;font-weight:700;margin-top:8px}
.mood-meter .desc{font-size:12px;color:var(--text-dim);margin-top:4px}
</style>
</head>
<body>
<header>
  <h1>🌡️ 市场情绪面板</h1>
  <div class="header-right">
    <span id="updateTime" style="color:var(--text-dim)">--</span>
    <button class="refresh-btn" onclick="loadAll()">🔄 刷新</button>
    <a href="${b}/quant/">← 返回首页</a>
  </div>
</header>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / 市场情绪面板</div>

<main>
<!-- Mood Meter -->
<div class="card">
  <h3>📊 综合情绪指标</h3>
  <div id="moodArea" class="mood-meter">
    <div class="emoji" id="moodEmoji">⏳</div>
    <div class="label" id="moodLabel">加载中...</div>
    <div class="desc" id="moodDesc"></div>
  </div>
</div>

<!-- KPI Row -->
<div class="kpi-row" id="kpiRow">
  <div class="kpi"><div class="label">上涨</div><div class="value" id="kpiAdv" style="color:var(--green)">--</div></div>
  <div class="kpi"><div class="label">下跌</div><div class="value" id="kpiDec" style="color:var(--red)">--</div></div>
  <div class="kpi"><div class="label">涨跌比</div><div class="value" id="kpiADR">--</div></div>
  <div class="kpi"><div class="label">涨停</div><div class="value" id="kpiLU" style="color:var(--green)">--</div></div>
  <div class="kpi"><div class="label">跌停</div><div class="value" id="kpiLD" style="color:var(--red)">--</div></div>
  <div class="kpi"><div class="label">两融余额(亿)</div><div class="value" id="kpiMargin">--</div><div class="sub" id="kpiMarginChg"></div></div>
  <div class="kpi"><div class="label">北向净流入(亿)</div><div class="value" id="kpiNorth">--</div></div>
</div>

<!-- A/D Ratio Bar -->
<div class="card">
  <h3>📈 涨跌分布</h3>
  <div class="ratio-bar" id="adRatioBar">
    <div class="seg" style="background:var(--green);width:50%">上涨 --</div>
    <div class="seg" style="background:var(--text-dim);width:0%"></div>
    <div class="seg" style="background:var(--red);width:50%">下跌 --</div>
  </div>
</div>

<!-- Grid: 涨停板 + 跌停板 -->
<div class="grid-2">
  <div class="card">
    <h3>🔴 涨停股 (Top 10)</h3>
    <div id="limitUpTable" class="loading">加载中...</div>
  </div>
  <div class="card">
    <h3>🟢 跌停股 (Top 10)</h3>
    <div id="limitDownTable" class="loading">加载中...</div>
  </div>
</div>

<!-- Grid: 两融 + 北向 -->
<div class="grid-2">
  <div class="card">
    <h3>💰 两融余额趋势</h3>
    <div id="marginInfo" class="loading">加载中...</div>
  </div>
  <div class="card">
    <h3>🌏 北向资金</h3>
    <div id="northInfo" class="loading">加载中...</div>
  </div>
</div>
</main>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}
function api(path,opts){
  opts=opts||{};var to=opts._timeout||15000;
  return fetch(B+path,opts).then(function(r){
    if(!r.ok) return r.json().catch(function(){return{}}).then(function(b){throw new Error(b.error||r.status)});
    return r.json();
  }).catch(function(e){toast('❌ '+e.message);throw e});
}

function loadAll(){
  document.getElementById('updateTime').textContent='加载中...';
  Promise.allSettled([loadBreadth(), loadMargin(), loadNorthbound()]).then(function(){
    document.getElementById('updateTime').textContent='更新: '+new Date().toLocaleTimeString('zh-CN');
    computeMood();
  });
}

var breadthData=null, marginData=null, northData=null;

function loadBreadth(){
  return api('/quant/admin/api/sentiment-dashboard?action=breadth').then(function(d){
    breadthData=d;
    if(d.advancers!=null){
      document.getElementById('kpiAdv').textContent=d.advancers;
      document.getElementById('kpiDec').textContent=d.decliners;
      document.getElementById('kpiADR').textContent=d.advDecRatio||'-';
      document.getElementById('kpiLU').textContent=d.limitUp||0;
      document.getElementById('kpiLD').textContent=d.limitDown||0;

      var total=d.advancers+d.decliners+(d.flat||0);
      var ap=Math.round(d.advancers/total*100)||0;
      var fp=Math.round((d.flat||0)/total*100)||0;
      var dp=100-ap-fp;
      document.getElementById('adRatioBar').innerHTML=
        '<div class="seg" style="background:var(--green);width:'+ap+'%">上涨 '+d.advancers+'</div>'+
        (fp?'<div class="seg" style="background:var(--text-dim);width:'+fp+'%">平 '+(d.flat||0)+'</div>':'')+
        '<div class="seg" style="background:var(--red);width:'+dp+'%">下跌 '+d.decliners+'</div>';

      // Limit-up table
      var lu=d.limitUpStocks||[];
      if(lu.length){
        var h='<table><tr><th>代码</th><th>名称</th><th>涨幅</th></tr>';
        lu.forEach(function(s){h+='<tr><td>'+esc(s.code)+'</td><td>'+esc(s.name)+'</td><td style="color:var(--green)">'+num(s.changePct)+'%</td></tr>'});
        h+='</table>';
        document.getElementById('limitUpTable').innerHTML=h;
      } else {
        document.getElementById('limitUpTable').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">暂无涨停股</div>';
      }

      var ld=d.limitDownStocks||[];
      if(ld.length){
        var h2='<table><tr><th>代码</th><th>名称</th><th>跌幅</th></tr>';
        ld.forEach(function(s){h2+='<tr><td>'+esc(s.code)+'</td><td>'+esc(s.name)+'</td><td style="color:var(--red)">'+num(s.changePct)+'%</td></tr>'});
        h2+='</table>';
        document.getElementById('limitDownTable').innerHTML=h2;
      } else {
        document.getElementById('limitDownTable').innerHTML='<div style="color:var(--text-dim);padding:20px;text-align:center">暂无跌停股</div>';
      }
    } else if(d.error){
      document.getElementById('limitUpTable').innerHTML='<div style="color:var(--red);padding:12px">'+esc(d.error)+'</div>';
    }
  }).catch(function(){});
}

function loadMargin(){
  return api('/quant/admin/api/sentiment-dashboard?action=margin').then(function(d){
    marginData=d;
    if(d.rzrqye!=null){
      document.getElementById('kpiMargin').textContent=num(d.rzrqye);
      var chg=d.changePct||0;
      var chgEl=document.getElementById('kpiMarginChg');
      chgEl.textContent=(chg>=0?'+':'')+num(chg)+'%';
      chgEl.style.color=chg>=0?'var(--green)':'var(--red)';

      document.getElementById('marginInfo').innerHTML=
        '<div style="padding:8px 0"><div style="display:flex;justify-content:space-between;margin-bottom:8px">'+
        '<span>融资余额</span><span style="font-weight:700">'+num(d.rzye)+' 亿</span></div>'+
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px">'+
        '<span>融券余额</span><span style="font-weight:700">'+num(d.rqye)+' 亿</span></div>'+
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px">'+
        '<span>两融合计</span><span style="font-weight:700">'+num(d.rzrqye)+' 亿</span></div>'+
        '<div style="display:flex;justify-content:space-between">'+
        '<span>日变动</span><span style="font-weight:700;color:'+(chg>=0?'var(--green)':'var(--red)')+'">'+
        (chg>=0?'+':'')+num(d.change)+' 亿 ('+num(chg)+'%)</span></div>'+
        '<div style="margin-top:12px;font-size:11px;color:var(--text-dim)">日期: '+esc(d.date||'-')+'</div></div>';
    } else if(d.error){
      document.getElementById('marginInfo').innerHTML='<div style="color:var(--red);padding:12px">'+esc(d.error)+'</div>';
    }
  }).catch(function(){});
}

function loadNorthbound(){
  return api('/quant/admin/api/sentiment-dashboard?action=northbound').then(function(d){
    northData=d;
    if(d.netFlow!=null){
      var nf=d.netFlow;
      document.getElementById('kpiNorth').textContent=(nf>=0?'+':'')+num(nf);
      document.getElementById('kpiNorth').style.color=nf>=0?'var(--green)':'var(--red)';

      var html='<div style="padding:8px 0">'+
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px">'+
        '<span>沪股通净流入</span><span style="font-weight:700;color:'+(d.shConnect>=0?'var(--green)':'var(--red)')+'">'+
        (d.shConnect>=0?'+':'')+num(d.shConnect||0)+' 亿</span></div>'+
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px">'+
        '<span>深股通净流入</span><span style="font-weight:700;color:'+(d.szConnect>=0?'var(--green)':'var(--red)')+'">'+
        (d.szConnect>=0?'+':'')+num(d.szConnect||0)+' 亿</span></div>'+
        '<div style="display:flex;justify-content:space-between;margin-bottom:8px;border-top:1px solid var(--border);padding-top:8px">'+
        '<span>合计净流入</span><span style="font-weight:700;color:'+(nf>=0?'var(--green)':'var(--red)')+'">'+
        (nf>=0?'+':'')+num(nf)+' 亿</span></div>';

      if(d.history&&d.history.length){
        html+='<div style="margin-top:12px;font-size:12px;color:var(--text-dim)">近5日北向资金</div>';
        html+='<table><tr><th>日期</th><th>净流入(亿)</th></tr>';
        d.history.forEach(function(h){
          var v=h.netFlow||0;
          html+='<tr><td>'+esc(h.date)+'</td><td style="color:'+(v>=0?'var(--green)':'var(--red)')+'">'+
            (v>=0?'+':'')+num(v)+'</td></tr>';
        });
        html+='</table>';
      }
      html+='</div>';
      document.getElementById('northInfo').innerHTML=html;
    } else if(d.error){
      document.getElementById('northInfo').innerHTML='<div style="color:var(--red);padding:12px">'+esc(d.error)+'</div>';
    }
  }).catch(function(){});
}

function computeMood(){
  var score=50;
  var reasons=[];

  if(breadthData&&breadthData.advDecRatio!=null){
    var adr=breadthData.advDecRatio;
    if(adr>2){score+=15;reasons.push('涨跌比 '+adr+' (强势)')}
    else if(adr>1.2){score+=8;reasons.push('涨跌比 '+adr+' (偏多)')}
    else if(adr<0.5){score-=15;reasons.push('涨跌比 '+adr+' (弱势)')}
    else if(adr<0.8){score-=8;reasons.push('涨跌比 '+adr+' (偏空)')}

    var lu=breadthData.limitUp||0, ld=breadthData.limitDown||0;
    if(lu>50){score+=10;reasons.push('涨停'+lu+'家 (活跃)')}
    else if(lu>30){score+=5;reasons.push('涨停'+lu+'家')}
    if(ld>30){score-=10;reasons.push('跌停'+ld+'家 (恐慌)')}
  }

  if(marginData&&marginData.changePct!=null){
    var mc=marginData.changePct;
    if(mc>1){score+=5;reasons.push('两融增'+num(mc)+'%')}
    else if(mc<-1){score-=5;reasons.push('两融减'+num(mc)+'%')}
  }

  if(northData&&northData.netFlow!=null){
    var nf=northData.netFlow;
    if(nf>50){score+=10;reasons.push('北向净流入'+num(nf)+'亿')}
    else if(nf>20){score+=5;reasons.push('北向净流入'+num(nf)+'亿')}
    else if(nf<-50){score-=10;reasons.push('北向净流出'+num(Math.abs(nf))+'亿')}
    else if(nf<-20){score-=5;reasons.push('北向净流出'+num(Math.abs(nf))+'亿')}
  }

  score=Math.max(0,Math.min(100,score));

  var emoji,label,color;
  if(score>=75){emoji='🔥';label='极度乐观';color='var(--green)'}
  else if(score>=60){emoji='😊';label='偏多情绪';color='var(--green)'}
  else if(score>=40){emoji='😐';label='中性震荡';color='var(--yellow)'}
  else if(score>=25){emoji='😰';label='偏空情绪';color='var(--red)'}
  else{emoji='🥶';label='极度悲观';color='var(--red)'}

  document.getElementById('moodEmoji').textContent=emoji;
  document.getElementById('moodLabel').textContent=label+' ('+score+'/100)';
  document.getElementById('moodLabel').style.color=color;
  document.getElementById('moodDesc').textContent=reasons.join(' | ')||'数据不足';
}

loadAll();
setInterval(loadAll, 60000);
</script>
</body>
</html>`;
}
