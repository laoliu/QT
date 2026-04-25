/**
 * Risk Control Simulator Page — MDD Gradient + Vol Scaling interactive simulator.
 *
 * Served at `/quant/admin/risk-simulator`
 * Self-contained HTML SPA.
 *
 * Features:
 *  - Configure MDD gradient thresholds (L1/L2/recovery)
 *  - Configure Vol Scaling parameters (target vol, lookback)
 *  - Upload/paste equity curve data
 *  - Simulate risk controls applied to historical NAV and visualize impact
 *  - Show state transitions, drawdown chart, position scaling over time
 */

export function getRiskSimulatorPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>风控模拟器 — MDD梯度 + 波动率缩放</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🛡️</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff;--orange:#db6d28}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:16px 24px}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
h1{font-size:20px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.breadcrumb{font-size:12px;margin-bottom:12px;color:var(--text-dim)}.breadcrumb a{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px}
.card h3{font-size:14px;font-weight:600;margin-bottom:12px;color:var(--accent)}
.form-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.form-row label{font-size:12px;color:var(--text-dim);min-width:90px}
input[type=text],input[type=number],select{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:6px 10px;color:var(--text);font-size:13px}
input:focus,select:focus{outline:none;border-color:var(--accent)}
textarea{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:8px 10px;color:var(--text);font-size:13px;width:100%;resize:vertical;font-family:monospace}
.btn{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}.btn-primary:hover{background:#2ea043}
.btn-sm{padding:4px 10px;font-size:12px}
table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px;position:sticky;top:0;background:var(--bg2)}
tr:hover{background:var(--bg3)}
.badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:500}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.badge-blue{background:rgba(88,166,255,.15);color:var(--accent)}
.badge-purple{background:rgba(188,140,255,.15);color:var(--purple)}
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
.kpi{background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:12px 18px;min-width:120px;flex:1;text-align:center}
.kpi .val{font-size:20px;font-weight:700}.kpi .lbl{font-size:11px;color:var(--text-dim);margin-top:4px}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.svg-chart{width:100%;height:240px;background:var(--bg);border:1px solid var(--border);border-radius:6px;overflow:hidden}
.info-note{font-size:12px;color:var(--text-dim);margin-top:6px;padding:8px;background:var(--bg);border-radius:4px;border-left:3px solid var(--yellow)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.legend{display:flex;gap:16px;margin-top:8px;font-size:11px;color:var(--text-dim)}
.legend-item{display:flex;align-items:center;gap:4px}
.legend-dot{width:10px;height:10px;border-radius:50%}
.slider-row{display:flex;align-items:center;gap:8px}
.slider-row input[type=range]{flex:1;accent-color:var(--accent)}
.slider-val{min-width:40px;text-align:right;font-size:12px;color:var(--accent)}
@media(max-width:700px){.two-col{grid-template-columns:1fr}.kpi-row{flex-direction:column}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <a href="${b}/quant/admin/ui">🔧 管理面板</a> / <span>🛡️ 风控模拟器</span></div>
<h1>🛡️ 风控模拟器 — MDD梯度 + 波动率缩放</h1>

<!-- Live Risk Overlay Status Bar -->
<div class="card" id="live-overlay-bar" style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 16px">
  <span style="font-size:13px;font-weight:600">实盘风控覆盖:</span>
  <span id="overlay-status-badge" style="font-size:12px">加载中…</span>
  <span id="overlay-state-badge" style="font-size:12px"></span>
  <span id="overlay-scale-badge" style="font-size:12px"></span>
  <div style="margin-left:auto;display:flex;gap:8px">
    <button class="btn btn-sm" id="overlay-toggle-btn" onclick="toggleOverlay()" style="display:none">▶️ 启用</button>
    <button class="btn btn-sm btn-primary" id="apply-live-btn" onclick="applyToLive()">📡 应用当前参数到实盘</button>
    <button class="btn btn-sm" onclick="loadLiveConfig()">📥 从实盘加载参数</button>
  </div>
</div>

<div class="two-col">
<!-- Left: MDD Gradient Config -->
<div class="card">
  <h3>📉 MDD梯度风控配置</h3>
  <div class="info-note" style="margin-bottom:12px">
    模拟R113 v19_hgro MDD梯度方案：回撤超过L1阈值时减仓50%，超过L2时清仓，恢复后逐步回补。
  </div>
  <div class="slider-row">
    <label style="min-width:80px;font-size:12px;color:var(--text-dim)">L1 阈值</label>
    <input type="range" id="l1" min="5" max="30" value="12" oninput="updateSlider('l1')">
    <span class="slider-val" id="l1-val">12%</span>
  </div>
  <div class="slider-row">
    <label style="min-width:80px;font-size:12px;color:var(--text-dim)">L2 阈值</label>
    <input type="range" id="l2" min="10" max="50" value="18" oninput="updateSlider('l2')">
    <span class="slider-val" id="l2-val">18%</span>
  </div>
  <div class="slider-row">
    <label style="min-width:80px;font-size:12px;color:var(--text-dim)">恢复阈值</label>
    <input type="range" id="recovery" min="1" max="15" value="6" oninput="updateSlider('recovery')">
    <span class="slider-val" id="recovery-val">6%</span>
  </div>
  <div class="form-row">
    <label>L1 仓位比例</label>
    <input type="number" id="l1-scale" value="50" min="0" max="100" style="width:60px">%
    <label>恢复天数</label>
    <input type="number" id="ramp-days" value="5" min="1" max="20" style="width:50px">
  </div>
</div>

<!-- Right: Vol Scaling Config -->
<div class="card">
  <h3>📊 波动率缩放配置</h3>
  <div class="info-note" style="margin-bottom:12px">
    动态调整仓位使组合波动率趋近目标值。高波时自动减仓，低波时可适度加仓。
  </div>
  <div class="slider-row">
    <label style="min-width:100px;font-size:12px;color:var(--text-dim)">目标波动率</label>
    <input type="range" id="target-vol" min="5" max="40" value="18" oninput="updateSlider('target-vol')">
    <span class="slider-val" id="target-vol-val">18%</span>
  </div>
  <div class="form-row">
    <label>回看窗口</label>
    <input type="number" id="vol-lookback" value="60" min="10" max="252" style="width:70px">天
    <label>最大杠杆</label>
    <input type="number" id="max-leverage" value="1.5" min="1" max="3" step="0.1" style="width:60px">x
    <label>最小仓位</label>
    <input type="number" id="min-scale" value="10" min="0" max="50" style="width:60px">%
  </div>
  <div class="form-row">
    <label>启用模式</label>
    <select id="risk-mode" style="width:180px">
      <option value="both" selected>MDD梯度 + Vol缩放</option>
      <option value="mdd_only">仅MDD梯度</option>
      <option value="vol_only">仅Vol缩放</option>
      <option value="none">无风控（基线对比）</option>
    </select>
  </div>
</div>
</div>

<!-- Data Input -->
<div class="card">
  <h3>📂 净值数据</h3>
  <textarea id="nav-data" rows="5" placeholder='[{"date":"2020-07-01","value":1000000},{"date":"2020-07-02","value":1005000},...]'></textarea>
  <div style="margin-top:8px;display:flex;gap:8px">
    <button class="btn btn-sm" onclick="loadSampleNav()">📥 生成模拟数据</button>
    <input type="file" id="nav-file" accept=".json" style="display:none" onchange="loadNavFile(event)">
    <button class="btn btn-sm" onclick="document.getElementById('nav-file').click()">📁 从文件加载</button>
    <button class="btn btn-primary" id="sim-btn" onclick="runSimulation()">🚀 运行模拟</button>
    <span id="sim-status" style="font-size:12px;color:var(--text-dim);line-height:32px"></span>
  </div>
</div>

<!-- Results -->
<div id="results" style="display:none">
  <div class="kpi-row" id="sim-kpi"></div>

  <div class="two-col">
    <div class="card">
      <h3>📈 净值曲线对比（原始 vs 风控后）</h3>
      <div id="nav-chart" class="svg-chart"></div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:var(--text-dim)"></div>原始净值</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div>风控后净值</div>
      </div>
    </div>
    <div class="card">
      <h3>📉 回撤曲线</h3>
      <div id="dd-chart" class="svg-chart"></div>
      <div class="legend">
        <div class="legend-item"><div class="legend-dot" style="background:var(--text-dim)"></div>原始回撤</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--red)"></div>风控后回撤</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--yellow);width:10px;height:2px;border-radius:0"></div>L1线</div>
        <div class="legend-item"><div class="legend-dot" style="background:var(--red);width:10px;height:2px;border-radius:0"></div>L2线</div>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>📊 仓位缩放因子</h3>
    <div id="scale-chart" class="svg-chart"></div>
    <div class="legend">
      <div class="legend-item"><div class="legend-dot" style="background:var(--accent)"></div>MDD缩放</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--purple)"></div>Vol缩放</div>
      <div class="legend-item"><div class="legend-dot" style="background:var(--green)"></div>综合缩放</div>
    </div>
  </div>

  <div class="card">
    <h3>🔄 风控状态转换记录</h3>
    <div style="max-height:300px;overflow-y:auto">
      <table><thead><tr><th>日期</th><th>从状态</th><th>到状态</th><th>回撤</th><th>NAV</th><th>缩放因子</th></tr></thead>
      <tbody id="transitions-tbody"></tbody></table>
    </div>
  </div>

  <div class="card">
    <h3>📊 原始 vs 风控后对比</h3>
    <table><thead><tr><th>指标</th><th>原始</th><th>风控后</th><th>变化</th></tr></thead>
    <tbody id="compare-tbody"></tbody></table>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
var TOKEN='${apiToken ?? ""}';

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toFixed(4):'-'}
function pct(v){return v!=null?(v>=0?'+':'')+(v*100).toFixed(2)+'%':'-'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}
function api(path,opts){var url=B+path+(path.indexOf('?')>=0?'&':'?')+'token='+TOKEN;return fetch(url,opts).then(function(r){return r.json()})}

function updateSlider(id){document.getElementById(id+'-val').textContent=document.getElementById(id).value+'%'}

function loadSampleNav(){
  var dates=[];for(var i=0;i<1400;i++){var d=new Date(2020,6,1);d.setDate(d.getDate()+i);if(d.getDay()===0||d.getDay()===6)continue;dates.push(d.toISOString().slice(0,10))}
  dates=dates.slice(0,1000);
  var v=1000000;var curve=[];
  dates.forEach(function(dt){v*=(1+0.001+(Math.random()-0.5)*0.04);curve.push({date:dt,value:Math.round(v*100)/100})});
  document.getElementById('nav-data').value=JSON.stringify(curve);
  toast('已生成约1000天模拟净值数据');
}
function loadNavFile(e){
  var file=e.target.files[0];if(!file)return;
  var reader=new FileReader();reader.onload=function(ev){document.getElementById('nav-data').value=ev.target.result;toast('已加载: '+file.name)};
  reader.readAsText(file);
}

function runSimulation(){
  var raw=document.getElementById('nav-data').value.trim();
  if(!raw){toast('请先输入净值数据');return}
  var navData;try{navData=JSON.parse(raw)}catch(e){toast('JSON解析失败: '+e.message);return}
  if(!Array.isArray(navData)||navData.length<10){toast('数据点太少（至少10个）');return}

  var btn=document.getElementById('sim-btn');btn.disabled=true;btn.textContent='⏳ 模拟中...';
  document.getElementById('sim-status').textContent='正在对'+navData.length+'个数据点运行风控模拟...';

  var payload={
    action:'risk_simulate',
    nav_data:navData,
    mode:document.getElementById('risk-mode').value,
    mdd_config:{
      l1_threshold:parseInt(document.getElementById('l1').value)/100,
      l2_threshold:parseInt(document.getElementById('l2').value)/100,
      recovery_threshold:parseInt(document.getElementById('recovery').value)/100,
      l1_position_scale:parseInt(document.getElementById('l1-scale').value)/100,
      recovery_ramp_days:parseInt(document.getElementById('ramp-days').value)||5
    },
    vol_config:{
      target_vol:parseInt(document.getElementById('target-vol').value)/100,
      lookback:parseInt(document.getElementById('vol-lookback').value)||60,
      max_leverage:parseFloat(document.getElementById('max-leverage').value)||1.5,
      min_scale:parseInt(document.getElementById('min-scale').value)/100
    }
  };

  api('/quant/admin/api/risk-simulate',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
  }).then(function(d){
    btn.disabled=false;btn.textContent='🚀 运行模拟';
    document.getElementById('sim-status').textContent='';
    if(d.error){toast('❌ '+d.error);return}
    document.getElementById('results').style.display='block';
    renderSimResults(d);
    toast('✅ 模拟完成');
  }).catch(function(e){
    btn.disabled=false;btn.textContent='🚀 运行模拟';
    toast('❌ '+e.message);
  });
}

function renderSimResults(d){
  var orig=d.original_metrics||{};
  var ctrl=d.controlled_metrics||{};

  // KPIs
  document.getElementById('sim-kpi').innerHTML=
    kpi(pct(ctrl.annualized_return),'风控后年化',ctrl.annualized_return>=0?'var(--green)':'var(--red)')
    +kpi('-'+(ctrl.max_drawdown!=null?(ctrl.max_drawdown*100).toFixed(2):'?')+'%','风控后MDD','var(--red)')
    +kpi(num(ctrl.sharpe_ratio),'风控后SR',ctrl.sharpe_ratio>=1?'var(--green)':'var(--yellow)')
    +kpi((d.mdd_stats?d.mdd_stats.l1_triggers:'0')+'次','L1触发','var(--yellow)')
    +kpi((d.mdd_stats?d.mdd_stats.l2_triggers:'0')+'次','L2触发','var(--red)')
    +kpi(num(d.vol_diagnostics?d.vol_diagnostics.realized_vol:null),'实际波动率','var(--text)');

  // NAV chart
  renderDualChart('nav-chart',d.original_nav||[],d.controlled_nav||[],
    'var(--text-dim)','var(--accent)',function(v){return (v/10000).toFixed(0)+'万'});

  // Drawdown chart
  renderDualChart('dd-chart',d.original_drawdown||[],d.controlled_drawdown||[],
    'var(--text-dim)','var(--red)',function(v){return (v*100).toFixed(1)+'%'},true);

  // Scale chart
  renderScaleChart(d.mdd_scales||[],d.vol_scales||[],d.combined_scales||[]);

  // Transitions
  var transitions=d.transitions||[];
  var tbody='';
  transitions.forEach(function(t){
    var stateColor={'normal':'var(--green)','l1_reduced':'var(--yellow)','l2_liquidated':'var(--red)','recovering':'var(--accent)'};
    tbody+='<tr><td>'+esc(t.date||'')+'</td>'
      +'<td><span class="badge" style="color:'+(stateColor[t.from_state]||'var(--text)')+'">'+esc(t.from_state)+'</span></td>'
      +'<td><span class="badge" style="color:'+(stateColor[t.to_state]||'var(--text)')+'">'+esc(t.to_state)+'</span></td>'
      +'<td>'+(t.drawdown!=null?(t.drawdown*100).toFixed(2)+'%':'-')+'</td>'
      +'<td>'+(t.nav!=null?(t.nav/10000).toFixed(2)+'万':'-')+'</td>'
      +'<td>'+num(t.scale)+'</td></tr>';
  });
  if(!transitions.length)tbody='<tr><td colspan="6" class="loading">无状态转换（回撤未触及L1阈值）</td></tr>';
  document.getElementById('transitions-tbody').innerHTML=tbody;

  // Comparison table
  var metrics=[
    ['年化收益',pct(orig.annualized_return),pct(ctrl.annualized_return)],
    ['最大回撤','-'+(orig.max_drawdown?orig.max_drawdown*100:0).toFixed(2)+'%','-'+(ctrl.max_drawdown?ctrl.max_drawdown*100:0).toFixed(2)+'%'],
    ['夏普比率',num(orig.sharpe_ratio),num(ctrl.sharpe_ratio)],
    ['波动率',(orig.volatility?(orig.volatility*100).toFixed(2)+'%':'-'),(ctrl.volatility?(ctrl.volatility*100).toFixed(2)+'%':'-')],
    ['卡尔马比率',num(orig.calmar_ratio),num(ctrl.calmar_ratio)]
  ];
  var cBody='';
  metrics.forEach(function(m){
    cBody+='<tr><td><strong>'+m[0]+'</strong></td><td>'+m[1]+'</td><td>'+m[2]+'</td><td>—</td></tr>';
  });
  document.getElementById('compare-tbody').innerHTML=cBody;
}

function kpi(val,label,color){return '<div class="kpi"><div class="val" style="color:'+(color||'var(--text)')+'">'+val+'</div><div class="lbl">'+label+'</div></div>'}

function renderDualChart(containerId,series1,series2,color1,color2,yFmt,invert){
  var container=document.getElementById(containerId);
  var w=container.clientWidth||600,h=240;
  var allVals=series1.concat(series2);
  if(!allVals.length){container.innerHTML='<div class="loading">无数据</div>';return}
  var minV=Math.min.apply(null,allVals),maxV=Math.max.apply(null,allVals);
  if(invert){var tmp=minV;minV=-maxV;maxV=-tmp;series1=series1.map(function(v){return -v});series2=series2.map(function(v){return -v})}
  var range=maxV-minV||1;
  var pad={t:15,r:50,b:10,l:10},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  var toPoints=function(s){return s.map(function(v,i){return (pad.l+i/(s.length-1)*cw)+','+(pad.t+ch-(v-minV)/range*ch)}).join(' ')};
  var svg='<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'">';
  svg+='<polyline fill="none" stroke="'+color1+'" stroke-width="1" stroke-opacity="0.5" points="'+toPoints(series1)+'"/>';
  svg+='<polyline fill="none" stroke="'+color2+'" stroke-width="1.5" points="'+toPoints(series2)+'"/>';
  for(var i=0;i<=3;i++){
    var yVal=minV+range*(i/3);var yPos=pad.t+ch-ch*(i/3);
    svg+='<text x="'+(w-5)+'" y="'+(yPos+4)+'" fill="var(--text-dim)" font-size="9" text-anchor="end">'+yFmt(invert?-yVal:yVal)+'</text>';
    svg+='<line x1="'+pad.l+'" y1="'+yPos+'" x2="'+(w-pad.r)+'" y2="'+yPos+'" stroke="var(--border)" stroke-width="0.5" stroke-dasharray="3"/>';
  }
  svg+='</svg>';container.innerHTML=svg;
}

function renderScaleChart(mdd,vol,combined){
  var container=document.getElementById('scale-chart');
  var w=container.clientWidth||800,h=240;
  var pad={t:15,r:50,b:10,l:10},cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  var toPoints=function(s,color){
    if(!s.length)return '';
    return '<polyline fill="none" stroke="'+color+'" stroke-width="1.2" points="'+s.map(function(v,i){
      var x=pad.l+i/(s.length-1)*cw;var y=pad.t+ch-v*ch;return x+','+y}).join(' ')+'"/>';
  };
  var svg='<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'">';
  svg+=toPoints(mdd,'var(--accent)');svg+=toPoints(vol,'var(--purple)');svg+=toPoints(combined,'var(--green)');
  // Y labels 0%, 50%, 100%, 150%
  [0,0.5,1,1.5].forEach(function(v){
    var y=pad.t+ch-v/1.5*ch;
    svg+='<text x="'+(w-5)+'" y="'+(y+4)+'" fill="var(--text-dim)" font-size="9" text-anchor="end">'+(v*100).toFixed(0)+'%</text>';
    svg+='<line x1="'+pad.l+'" y1="'+y+'" x2="'+(w-pad.r)+'" y2="'+y+'" stroke="var(--border)" stroke-width="0.5" stroke-dasharray="3"/>';
  });
  // 100% reference line
  var refY=pad.t+ch-1/1.5*ch;
  svg+='<line x1="'+pad.l+'" y1="'+refY+'" x2="'+(w-pad.r)+'" y2="'+refY+'" stroke="var(--yellow)" stroke-width="1" stroke-dasharray="5,3"/>';
  svg+='</svg>';container.innerHTML=svg;
}
// ── Live Risk Overlay Integration ────────────────────────────────

function refreshOverlayStatus(){
  api('/quant/admin/api/risk-overlay/status').then(function(res){
    if(!res||!res.ok||!res.data)return;
    var d=res.data;
    var stateLabels={normal:'正常',l1_reduced:'L1减仓',l2_liquidated:'L2清仓',recovering:'恢复中'};
    var stateColors={normal:'var(--green)',l1_reduced:'var(--yellow)',l2_liquidated:'var(--red)',recovering:'var(--accent)'};

    document.getElementById('overlay-status-badge').innerHTML=
      '<span class="badge '+(d.enabled?'badge-green':'badge-red')+'">'+(d.enabled?'已启用':'未启用')+'</span>';

    if(d.enabled){
      document.getElementById('overlay-state-badge').innerHTML=
        '状态: <span style="color:'+(stateColors[d.mddState]||'var(--text)')+';font-weight:600">'+(stateLabels[d.mddState]||d.mddState)+'</span>';
      document.getElementById('overlay-scale-badge').innerHTML=
        '缩放: <span style="color:'+(d.combinedScale>=0.9?'var(--green)':d.combinedScale>=0.5?'var(--yellow)':'var(--red)')+';font-weight:600">'+(d.combinedScale*100).toFixed(1)+'%</span>'+
        ' | 回撤: <span style="color:var(--red)">'+(d.currentDrawdown*100).toFixed(2)+'%</span>';
    }

    var btn=document.getElementById('overlay-toggle-btn');
    btn.style.display='inline-flex';
    btn.textContent=d.enabled?'⏸️ 停用':'▶️ 启用';
    btn.style.borderColor=d.enabled?'var(--red)':'var(--green)';
    btn.style.color=d.enabled?'var(--red)':'var(--green)';
  }).catch(function(){});
}
refreshOverlayStatus();

function toggleOverlay(){
  api('/quant/admin/api/risk-overlay/status').then(function(res){
    var current=res&&res.data&&res.data.enabled;
    return api('/quant/admin/api/risk-overlay/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!current})});
  }).then(function(){toast(arguments[0]&&arguments[0].ok?'✅ 已切换风控覆盖状态':'❌ 切换失败');refreshOverlayStatus()});
}

function applyToLive(){
  var payload={
    enabled:true,
    mode:document.getElementById('risk-mode').value,
    mdd:{
      l1Threshold:parseInt(document.getElementById('l1').value)/100,
      l2Threshold:parseInt(document.getElementById('l2').value)/100,
      recoveryThreshold:parseInt(document.getElementById('recovery').value)/100,
      l1PositionScale:parseInt(document.getElementById('l1-scale').value)/100,
      recoveryRampDays:parseInt(document.getElementById('ramp-days').value)||5
    },
    vol:{
      targetVol:parseInt(document.getElementById('target-vol').value)/100,
      lookback:parseInt(document.getElementById('vol-lookback').value)||60,
      maxLeverage:parseFloat(document.getElementById('max-leverage').value)||1.5,
      minScale:parseInt(document.getElementById('min-scale').value)/100
    }
  };
  api('/quant/admin/api/risk-overlay/config',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
  }).then(function(r){
    toast(r&&r.ok?'✅ 已应用到实盘风控覆盖 (已启用)':'❌ 应用失败: '+(r&&r.error||'未知错误'));
    refreshOverlayStatus();
  }).catch(function(e){toast('❌ '+e.message)});
}

function loadLiveConfig(){
  api('/quant/admin/api/risk-overlay/config').then(function(r){
    if(!r||!r.ok||!r.config)return;
    var c=r.config;
    if(c.mdd){
      document.getElementById('l1').value=Math.round(c.mdd.l1Threshold*100);updateSlider('l1');
      document.getElementById('l2').value=Math.round(c.mdd.l2Threshold*100);updateSlider('l2');
      document.getElementById('recovery').value=Math.round(c.mdd.recoveryThreshold*100);updateSlider('recovery');
      document.getElementById('l1-scale').value=Math.round(c.mdd.l1PositionScale*100);
      document.getElementById('ramp-days').value=c.mdd.recoveryRampDays||5;
    }
    if(c.vol){
      document.getElementById('target-vol').value=Math.round(c.vol.targetVol*100);updateSlider('target-vol');
      document.getElementById('vol-lookback').value=c.vol.lookback||60;
      document.getElementById('max-leverage').value=c.vol.maxLeverage||1.5;
      document.getElementById('min-scale').value=Math.round(c.vol.minScale*100);
    }
    if(c.mode){document.getElementById('risk-mode').value=c.mode}
    toast('✅ 已从实盘加载风控参数');
  }).catch(function(e){toast('❌ '+e.message)});
}</script>
</body>
</html>`;
}
