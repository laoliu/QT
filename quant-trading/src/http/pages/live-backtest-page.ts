/**
 * Live vs Backtest Comparison Page — overlay live trading equity against backtest,
 * highlight metric differences, and generate summary reports.
 *
 * Served at `/quant/admin/live-backtest`
 * Self-contained HTML SPA using inline Chart.js for equity curve overlay.
 * Communicates with `/quant/admin/api/live-backtest-compare`.
 */

export function getLiveBacktestPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>实盘 vs 回测对比</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📊</text></svg>">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"><\/script>
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff;--orange:#db6d28}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:16px 24px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:20px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.breadcrumb{font-size:12px;margin-bottom:12px;color:var(--text-dim)}
.breadcrumb a{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px}
.card h3{font-size:14px;font-weight:600;margin-bottom:12px;color:var(--accent)}
.form-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.form-row label{font-size:12px;color:var(--text-dim);min-width:80px}
select,input[type=text]{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:6px 10px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
.btn{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}
.btn-sm{padding:4px 10px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px;position:sticky;top:0;background:var(--bg2)}
tr:hover{background:var(--bg3)}
.up{color:var(--red)}.down{color:var(--green)}.neutral{color:var(--text-dim)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:500}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.kpi{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px 18px;min-width:120px;flex:1;text-align:center}
.kpi .val{font-size:20px;font-weight:700}
.kpi .lbl{font-size:11px;color:var(--text-dim);margin-top:4px}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.chart-container{position:relative;height:320px;margin-top:12px;background:var(--bg);border-radius:6px;padding:8px;border:1px solid var(--border)}
canvas{width:100%!important;height:100%!important}
.verdict-banner{padding:12px 16px;border-radius:8px;margin-bottom:14px;font-size:14px;font-weight:600;display:flex;align-items:center;gap:8px}
.verdict-consistent{background:rgba(63,185,80,.1);border:1px solid var(--green);color:var(--green)}
.verdict-divergent{background:rgba(248,81,73,.1);border:1px solid var(--red);color:var(--red)}
.verdict-inconclusive{background:rgba(210,153,34,.1);border:1px solid var(--yellow);color:var(--yellow)}
.metric-row{display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid var(--border)}
.metric-row:last-child{border-bottom:none}
.metric-label{width:80px;font-size:12px;color:var(--text-dim)}
.metric-bar{flex:1;height:24px;position:relative;display:flex;align-items:center}
.metric-bar-bg{position:absolute;height:6px;border-radius:3px;top:9px}
.metric-val{font-size:12px;min-width:65px;text-align:right}
.diff-tag{font-size:11px;padding:1px 6px;border-radius:8px;margin-left:4px}
.diff-better{background:rgba(63,185,80,.15);color:var(--green)}
.diff-worse{background:rgba(248,81,73,.15);color:var(--red)}
.diff-neutral{background:rgba(139,148,158,.15);color:var(--text-dim)}
.report-box{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:16px;font-size:13px;line-height:1.7;white-space:pre-wrap;max-height:400px;overflow-y:auto;font-family:inherit}
.tab-row{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:14px}
.tab{padding:8px 16px;font-size:13px;cursor:pointer;border-bottom:2px solid transparent;color:var(--text-dim);transition:all .15s}
.tab:hover{color:var(--text)}
.tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
.info-note{font-size:12px;color:var(--text-dim);margin-top:6px;padding:8px;background:var(--bg);border-radius:4px;border-left:3px solid var(--yellow)}
@media(max-width:700px){.kpi-row{flex-direction:column}.form-row{flex-direction:column;align-items:flex-start}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">首页</a> / <a href="${b}/quant/admin/ui">管理面板</a> / <span>实盘 vs 回测对比</span></div>
<h1>实盘 vs 回测对比</h1>

<!-- Input Section -->
<div class="card">
  <h3>对比设置</h3>
  <div class="form-row">
    <label>实盘数据</label>
    <select id="live-mode">
      <option value="live">实盘账户</option>
      <option value="paper">模拟盘</option>
    </select>
  </div>
  <div class="form-row">
    <label>回测记录</label>
    <select id="backtest-select">
      <option value="">加载中...</option>
    </select>
    <button class="btn btn-sm" onclick="loadBacktests()">刷新</button>
  </div>
  <div class="form-row">
    <label>日期范围</label>
    <input type="text" id="startDate" placeholder="YYYY-MM-DD (留空自动)" style="width:150px">
    <span style="font-size:12px;color:var(--text-dim)">~</span>
    <input type="text" id="endDate" placeholder="YYYY-MM-DD (留空自动)" style="width:150px">
  </div>
  <div class="form-row" style="margin-top:12px">
    <button class="btn btn-primary" id="compare-btn" onclick="runCompare()">开始对比</button>
    <span id="status-text" style="font-size:12px;color:var(--text-dim)"></span>
  </div>
  <div class="info-note">
    选择实盘/模拟盘账户的权益数据，与某次历史回测记录进行对比。系统将自动对齐日期、计算指标差异、生成可视化报告。
  </div>
</div>

<!-- Results Section -->
<div id="results-container" style="display:none">

  <!-- Verdict Banner -->
  <div id="verdict-banner" class="verdict-banner verdict-consistent"></div>

  <!-- KPI Row -->
  <div class="kpi-row" id="kpi-row"></div>

  <!-- Tabs -->
  <div class="tab-row">
    <div class="tab active" onclick="switchTab('chart')">权益曲线</div>
    <div class="tab" onclick="switchTab('metrics')">指标对比</div>
    <div class="tab" onclick="switchTab('divergence')">偏差分析</div>
    <div class="tab" onclick="switchTab('report')">文字报告</div>
  </div>

  <!-- Tab: Equity Curve Chart -->
  <div id="tab-chart" class="tab-content active">
    <div class="card">
      <h3>权益曲线叠加</h3>
      <div class="chart-container">
        <canvas id="equityChart"></canvas>
      </div>
    </div>
    <div class="card">
      <h3>偏差走势（实盘 - 回测）</h3>
      <div class="chart-container" style="height:200px">
        <canvas id="gapChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Tab: Metric Comparison -->
  <div id="tab-metrics" class="tab-content">
    <div class="card">
      <h3>核心指标对比</h3>
      <table>
        <thead>
          <tr>
            <th>指标</th>
            <th>实盘</th>
            <th>回测</th>
            <th>差异</th>
            <th>差异%</th>
            <th>评价</th>
            <th>显著性</th>
          </tr>
        </thead>
        <tbody id="metrics-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Tab: Divergence Analysis -->
  <div id="tab-divergence" class="tab-content">
    <div class="card">
      <h3>偏差统计</h3>
      <div class="kpi-row" id="divergence-kpis"></div>
    </div>
    <div class="card">
      <h3>日收益相关性散点图</h3>
      <div class="chart-container" style="height:280px">
        <canvas id="scatterChart"></canvas>
      </div>
    </div>
  </div>

  <!-- Tab: Text Report -->
  <div id="tab-report" class="tab-content">
    <div class="card">
      <h3>对比报告 <button class="btn btn-sm" onclick="copyReport()" style="margin-left:8px">复制</button> <button class="btn btn-sm" onclick="downloadReport()">下载</button></h3>
      <div id="report-box" class="report-box"></div>
    </div>
  </div>

</div>

<script>
var B='${b}';
var TOKEN='${apiToken ?? ""}';
var chartEquity=null,chartGap=null,chartScatter=null;
var lastResult=null;

function api(path,opts){
  var sep=path.indexOf('?')>=0?'&':'?';
  var url=B+path+sep+'token='+TOKEN;
  return fetch(url,opts).then(function(r){return r.json()});
}

function switchTab(id){
  document.querySelectorAll('.tab').forEach(function(t,i){
    var tabs=['chart','metrics','divergence','report'];
    var isActive=tabs[i]===id;
    t.classList.toggle('active',isActive);
  });
  document.querySelectorAll('.tab-content').forEach(function(c){c.classList.remove('active')});
  document.getElementById('tab-'+id).classList.add('active');
}

function loadBacktests(){
  api('/quant/api/backtests?limit=50&sortBy=createdAt&sortDir=desc').then(function(d){
    var sel=document.getElementById('backtest-select');
    sel.innerHTML='';
    var records=d.results||d.data||[];
    if(!records.length){
      sel.innerHTML='<option value="">暂无回测记录</option>';
      return;
    }
    records.forEach(function(r){
      var opt=document.createElement('option');
      opt.value=r.id;
      var date=r.createdAt?r.createdAt.slice(0,10):'?';
      var ret=r.totalReturn!=null?r.totalReturn.toFixed(1)+'%':'?';
      opt.textContent='['+date+'] '+r.strategyId+' ('+r.symbols.join(',')+') '+ret;
      sel.appendChild(opt);
    });
  }).catch(function(e){
    document.getElementById('backtest-select').innerHTML='<option value="">加载失败: '+e.message+'</option>';
  });
}

function runCompare(){
  var backtestId=document.getElementById('backtest-select').value;
  if(!backtestId){alert('请选择一个回测记录');return}
  var mode=document.getElementById('live-mode').value;
  var startDate=document.getElementById('startDate').value.trim();
  var endDate=document.getElementById('endDate').value.trim();

  var btn=document.getElementById('compare-btn');
  btn.disabled=true;btn.textContent='对比中...';
  document.getElementById('status-text').textContent='正在对齐数据并计算指标...';
  document.getElementById('results-container').style.display='none';

  var qs='backtestId='+encodeURIComponent(backtestId)+'&mode='+mode;
  if(startDate) qs+='&start='+startDate;
  if(endDate) qs+='&end='+endDate;

  api('/quant/admin/api/live-backtest-compare?'+qs).then(function(d){
    btn.disabled=false;btn.textContent='开始对比';
    document.getElementById('status-text').textContent='';
    if(d.error){alert('对比失败: '+d.error);return}
    lastResult=d;
    renderResults(d);
  }).catch(function(e){
    btn.disabled=false;btn.textContent='开始对比';
    document.getElementById('status-text').textContent='';
    alert('请求失败: '+e.message);
  });
}

function renderResults(data){
  document.getElementById('results-container').style.display='block';

  // Verdict banner
  var vb=document.getElementById('verdict-banner');
  var vmap={consistent:['verdict-consistent','实盘表现与回测基本一致 — 策略可信度高'],divergent:['verdict-divergent','实盘与回测存在显著偏差 — 需要排查原因'],inconclusive:['verdict-inconclusive','数据量不足，暂无法做出结论']};
  var v=vmap[data.overallVerdict]||vmap.inconclusive;
  vb.className='verdict-banner '+v[0];
  vb.textContent=v[1];

  // KPIs
  var kpiHtml='';
  kpiHtml+='<div class="kpi"><div class="val">'+(data.trackingError*100).toFixed(2)+'%</div><div class="lbl">跟踪误差（年化）</div></div>';
  kpiHtml+='<div class="kpi"><div class="val">'+data.returnCorrelation.toFixed(3)+'</div><div class="lbl">收益相关性</div></div>';
  if(data.maxDivergence){
    kpiHtml+='<div class="kpi"><div class="val '+(data.maxDivergence.gapPct>=0?'up':'down')+'">'+data.maxDivergence.gapPct.toFixed(2)+'%</div><div class="lbl">最大偏差 ('+data.maxDivergence.date+')</div></div>';
  }
  kpiHtml+='<div class="kpi"><div class="val">'+data.dateRange.from+' ~ '+data.dateRange.to+'</div><div class="lbl">对比区间</div></div>';
  document.getElementById('kpi-row').innerHTML=kpiHtml;

  renderEquityChart(data);
  renderGapChart(data);
  renderMetricsTable(data);
  renderDivergenceTab(data);
  renderReport(data);

  document.getElementById('results-container').scrollIntoView({behavior:'smooth'});
}

function renderEquityChart(data){
  var curve=data.alignedCurve||[];
  var labels=[],liveData=[],btData=[];
  curve.forEach(function(p){
    labels.push(p.date);
    liveData.push(p.liveEquity);
    btData.push(p.backtestEquity);
  });
  if(chartEquity){chartEquity.destroy()}
  var ctx=document.getElementById('equityChart').getContext('2d');
  chartEquity=new Chart(ctx,{
    type:'line',
    data:{
      labels:labels,
      datasets:[
        {label:'实盘',data:liveData,borderColor:'#58a6ff',backgroundColor:'rgba(88,166,255,0.1)',fill:false,borderWidth:2,pointRadius:0,tension:0.1,spanGaps:true},
        {label:'回测',data:btData,borderColor:'#bc8cff',backgroundColor:'rgba(188,140,255,0.1)',fill:false,borderWidth:2,pointRadius:0,tension:0.1,borderDash:[5,3],spanGaps:true}
      ]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{labels:{color:'#c9d1d9'}},tooltip:{callbacks:{label:function(ctx){return ctx.dataset.label+': ¥'+(ctx.parsed.y||0).toLocaleString('zh-CN',{minimumFractionDigits:2})}}}},
      scales:{
        x:{ticks:{color:'#8b949e',maxTicksLimit:12},grid:{color:'rgba(48,54,61,0.5)'}},
        y:{ticks:{color:'#8b949e',callback:function(v){return '¥'+v.toLocaleString()}},grid:{color:'rgba(48,54,61,0.5)'}}
      }
    }
  });
}

function renderGapChart(data){
  var curve=(data.alignedCurve||[]).filter(function(p){return p.gapPct!=null});
  var labels=[],gapData=[],colors=[];
  curve.forEach(function(p){
    labels.push(p.date);
    gapData.push(p.gapPct);
    colors.push(p.gapPct>=0?'rgba(88,166,255,0.6)':'rgba(248,81,73,0.6)');
  });
  if(chartGap){chartGap.destroy()}
  var ctx=document.getElementById('gapChart').getContext('2d');
  chartGap=new Chart(ctx,{
    type:'bar',
    data:{
      labels:labels,
      datasets:[{label:'偏差%',data:gapData,backgroundColor:colors,borderWidth:0}]
    },
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:'#8b949e',maxTicksLimit:12},grid:{color:'rgba(48,54,61,0.3)'}},
        y:{ticks:{color:'#8b949e',callback:function(v){return v.toFixed(1)+'%'}},grid:{color:'rgba(48,54,61,0.3)'}}
      }
    }
  });
}

function renderMetricsTable(data){
  var tbody=document.getElementById('metrics-tbody');
  tbody.innerHTML='';
  (data.metrics||[]).forEach(function(m){
    var isRatio=m.key==='sharpeRatio';
    var isTrades=m.key==='totalTrades';
    function fmtV(v){
      if(isRatio) return v.toFixed(2);
      if(isTrades) return Math.round(v);
      return v.toFixed(2)+'%';
    }
    var verdictBadge=m.verdict==='better'?'<span class="badge badge-green">优于</span>':m.verdict==='worse'?'<span class="badge badge-red">劣于</span>':'<span class="badge badge-yellow">持平</span>';
    var sigBadge=m.significance==='high'?'<span class="badge badge-red">高</span>':m.significance==='medium'?'<span class="badge badge-yellow">中</span>':'<span class="badge badge-blue">低</span>';
    var diffCl=m.diff>=0?'up':'down';
    tbody.innerHTML+='<tr><td><strong>'+m.label+'</strong></td><td>'+fmtV(m.live)+'</td><td>'+fmtV(m.backtest)+'</td><td class="'+diffCl+'">'+(m.diff>=0?'+':'')+fmtV(m.diff)+'</td><td class="'+diffCl+'">'+(m.diffPct>=0?'+':'')+m.diffPct.toFixed(1)+'%</td><td>'+verdictBadge+'</td><td>'+sigBadge+'</td></tr>';
  });
}

function renderDivergenceTab(data){
  var kpis=document.getElementById('divergence-kpis');
  var te=(data.trackingError*100).toFixed(2);
  var corr=data.returnCorrelation.toFixed(3);
  var teClass=data.trackingError>0.3?'up':data.trackingError>0.15?'':'down';
  var corrClass=data.returnCorrelation>0.7?'down':data.returnCorrelation>0.4?'':'up';
  kpis.innerHTML='<div class="kpi"><div class="val '+teClass+'">'+te+'%</div><div class="lbl">跟踪误差（年化）</div><div style="font-size:11px;color:var(--text-dim);margin-top:4px">'+(data.trackingError>0.3?'偏差大':'偏差可控')+'</div></div>'
    +'<div class="kpi"><div class="val '+corrClass+'">'+corr+'</div><div class="lbl">日收益相关性</div><div style="font-size:11px;color:var(--text-dim);margin-top:4px">'+(data.returnCorrelation>0.7?'高度相关':data.returnCorrelation>0.4?'中等相关':'相关性弱')+'</div></div>'
    +'<div class="kpi"><div class="val">'+(data.metrics||[]).filter(function(m){return m.significance==="high"&&m.verdict==="worse"}).length+'</div><div class="lbl">高显著偏差指标数</div></div>';

  // Scatter chart (daily returns)
  if(chartScatter){chartScatter.destroy()}
  var curve=(data.alignedCurve||[]).filter(function(p){return p.liveEquity!=null&&p.backtestEquity!=null});
  var points=[];
  for(var i=1;i<curve.length;i++){
    var prevL=curve[i-1].liveEquity,curL=curve[i].liveEquity;
    var prevB=curve[i-1].backtestEquity,curB=curve[i].backtestEquity;
    if(prevL>0&&prevB>0){
      points.push({x:((curB-prevB)/prevB*100),y:((curL-prevL)/prevL*100)});
    }
  }
  var ctx=document.getElementById('scatterChart').getContext('2d');
  chartScatter=new Chart(ctx,{
    type:'scatter',
    data:{datasets:[{label:'日收益 (回测 vs 实盘)',data:points,backgroundColor:'rgba(88,166,255,0.5)',pointRadius:3}]},
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{labels:{color:'#c9d1d9'}}},
      scales:{
        x:{title:{display:true,text:'回测日收益%',color:'#8b949e'},ticks:{color:'#8b949e'},grid:{color:'rgba(48,54,61,0.3)'}},
        y:{title:{display:true,text:'实盘日收益%',color:'#8b949e'},ticks:{color:'#8b949e'},grid:{color:'rgba(48,54,61,0.3)'}}
      }
    }
  });
}

function renderReport(data){
  var box=document.getElementById('report-box');
  box.textContent=data.summary||'暂无报告内容';
}

function copyReport(){
  if(!lastResult||!lastResult.summary)return;
  navigator.clipboard.writeText(lastResult.summary).then(function(){
    alert('报告已复制到剪贴板');
  });
}

function downloadReport(){
  if(!lastResult||!lastResult.summary)return;
  var blob=new Blob([lastResult.summary],{type:'text/markdown;charset=utf-8'});
  var url=URL.createObjectURL(blob);
  var a=document.createElement('a');
  a.href=url;
  a.download='live-vs-backtest-report-'+new Date().toISOString().slice(0,10)+'.md';
  a.click();
  URL.revokeObjectURL(url);
}

// Init
loadBacktests();
<\/script>
</body>
</html>`;
}
