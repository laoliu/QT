/**
 * Portfolio Combiner Page — Cross-asset multi-strategy portfolio construction & evaluation.
 *
 * Served at `/quant/admin/portfolio-combo`
 * Self-contained HTML SPA.
 *
 * Features:
 *  - Configure sub-strategies with weights
 *  - Choose combination method (fixed/risk_parity/vol_target)
 *  - Run combined backtest via Python portfolio_combiner engine
 *  - View combined equity curve, per-strategy metrics, correlation matrix
 *  - Run weight perturbation & slippage sensitivity tests
 *  - Run DSR/HLZ/PBO statistical validation
 */

export function getPortfolioComboPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>跨资产组合构建器</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🧩</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff;--orange:#db6d28}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:16px 24px}
a{color:var(--accent);text-decoration:none} a:hover{text-decoration:underline}
h1{font-size:20px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.breadcrumb{font-size:12px;margin-bottom:12px;color:var(--text-dim)} .breadcrumb a{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px}
.card h3{font-size:14px;font-weight:600;margin-bottom:12px;color:var(--accent)}
.form-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.form-row label{font-size:12px;color:var(--text-dim);min-width:70px}
input[type=text],input[type=number],select{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:6px 10px;color:var(--text);font-size:13px}
input:focus,select:focus{outline:none;border-color:var(--accent)}
textarea{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:8px 10px;color:var(--text);font-size:13px;width:100%;resize:vertical;font-family:monospace}
.btn{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)} .btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff} .btn-primary:hover{background:#2ea043}
.btn-sm{padding:4px 10px;font-size:12px}
.btn-danger{background:rgba(248,81,73,.15);border-color:var(--red);color:var(--red)}
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
.kpi .val{font-size:20px;font-weight:700} .kpi .lbl{font-size:11px;color:var(--text-dim);margin-top:4px}
.loading{text-align:center;padding:40px;color:var(--text-dim);font-size:14px}
.tab-bar{display:flex;gap:0;border-bottom:1px solid var(--border);margin-bottom:16px}
.tab{padding:8px 16px;font-size:13px;cursor:pointer;color:var(--text-dim);border-bottom:2px solid transparent;transition:all .15s}
.tab:hover{color:var(--text)} .tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none} .tab-content.active{display:block}
.strat-row{display:flex;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border)}
.strat-row:last-child{border:none}
.strat-name{min-width:160px;font-weight:500;font-size:13px}
.strat-input{width:80px;text-align:right}
.corr-matrix{display:grid;gap:2px;margin-top:12px;font-size:12px}
.corr-cell{text-align:center;padding:6px 4px;border-radius:3px}
.svg-chart{width:100%;height:280px;background:var(--bg);border:1px solid var(--border);border-radius:6px;overflow:hidden}
.info-note{font-size:12px;color:var(--text-dim);margin-top:6px;padding:8px;background:var(--bg);border-radius:4px;border-left:3px solid var(--yellow)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
@media(max-width:700px){.kpi-row{flex-direction:column}.form-row{flex-direction:column;align-items:stretch}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <a href="${b}/quant/admin/ui">🔧 管理面板</a> / <span>🧩 跨资产组合构建</span></div>
<h1>🧩 跨资产组合构建器</h1>

<!-- Tab Navigation -->
<div class="tab-bar">
  <div class="tab active" data-tab="config" onclick="switchTab('config')">⚙️ 组合配置</div>
  <div class="tab" data-tab="results" onclick="switchTab('results')">📊 绩效分析</div>
  <div class="tab" data-tab="validation" onclick="switchTab('validation')">🔬 统计验证</div>
  <div class="tab" data-tab="stress" onclick="switchTab('stress')">💪 压力测试</div>
</div>

<!-- Tab 1: Configuration -->
<div class="tab-content active" id="tab-config">
<div class="card">
  <h3>📋 子策略配置</h3>
  <div class="info-note">
    💡 添加子策略的回测净值数据（JSON格式），配置权重和资产类别。支持从回测结果导入。
  </div>
  <div id="strat-list">
    <div class="strat-row" data-idx="0">
      <input type="text" class="strat-name-input" placeholder="策略名称" value="ml_csi2000_fund" style="width:160px">
      <label style="font-size:11px;color:var(--text-dim)">权重%</label>
      <input type="number" class="strat-weight-input" value="40" min="0" max="100" style="width:60px">
      <select class="strat-asset-select" style="width:100px">
        <option value="equity" selected>A股</option>
        <option value="cb">可转债</option>
        <option value="etf">ETF</option>
        <option value="commodity">商品</option>
      </select>
      <label style="font-size:11px;color:var(--text-dim)">滑点bps</label>
      <input type="number" class="strat-slippage-input" value="10" min="0" style="width:60px">
      <button class="btn btn-sm btn-danger" onclick="removeStrat(this)">✕</button>
    </div>
    <div class="strat-row" data-idx="1">
      <input type="text" class="strat-name-input" placeholder="策略名称" value="cb_stock_quality" style="width:160px">
      <label style="font-size:11px;color:var(--text-dim)">权重%</label>
      <input type="number" class="strat-weight-input" value="45" min="0" max="100" style="width:60px">
      <select class="strat-asset-select" style="width:100px"><option value="equity">A股</option><option value="cb" selected>可转债</option><option value="etf">ETF</option><option value="commodity">商品</option></select>
      <label style="font-size:11px;color:var(--text-dim)">滑点bps</label>
      <input type="number" class="strat-slippage-input" value="5" min="0" style="width:60px">
      <button class="btn btn-sm btn-danger" onclick="removeStrat(this)">✕</button>
    </div>
    <div class="strat-row" data-idx="2">
      <input type="text" class="strat-name-input" placeholder="策略名称" value="v19_hgro" style="width:160px">
      <label style="font-size:11px;color:var(--text-dim)">权重%</label>
      <input type="number" class="strat-weight-input" value="5" min="0" max="100" style="width:60px">
      <select class="strat-asset-select" style="width:100px"><option value="equity" selected>A股</option><option value="cb">可转债</option><option value="etf">ETF</option><option value="commodity">商品</option></select>
      <label style="font-size:11px;color:var(--text-dim)">滑点bps</label>
      <input type="number" class="strat-slippage-input" value="20" min="0" style="width:60px">
      <button class="btn btn-sm btn-danger" onclick="removeStrat(this)">✕</button>
    </div>
    <div class="strat-row" data-idx="3">
      <input type="text" class="strat-name-input" placeholder="策略名称" value="etf_cross_v3" style="width:160px">
      <label style="font-size:11px;color:var(--text-dim)">权重%</label>
      <input type="number" class="strat-weight-input" value="10" min="0" max="100" style="width:60px">
      <select class="strat-asset-select" style="width:100px"><option value="equity">A股</option><option value="cb">可转债</option><option value="etf" selected>ETF</option><option value="commodity">商品</option></select>
      <label style="font-size:11px;color:var(--text-dim)">滑点bps</label>
      <input type="number" class="strat-slippage-input" value="5" min="0" style="width:60px">
      <button class="btn btn-sm btn-danger" onclick="removeStrat(this)">✕</button>
    </div>
  </div>
  <div style="margin-top:10px;display:flex;gap:8px">
    <button class="btn btn-sm" onclick="addStrat()">+ 添加子策略</button>
    <span id="weight-total" style="font-size:12px;color:var(--text-dim);line-height:28px">权重合计: 100%</span>
  </div>
</div>

<div class="card">
  <h3>⚙️ 组合参数</h3>
  <div class="form-row">
    <label>组合方法</label>
    <select id="method" style="width:180px">
      <option value="fixed" selected>固定权重</option>
      <option value="risk_parity">风险平价 (Risk Parity)</option>
      <option value="vol_target">波动率目标制 (Vol Target)</option>
    </select>
    <label>再平衡</label>
    <select id="rebalance" style="width:120px">
      <option value="monthly" selected>月度</option>
      <option value="weekly">周度</option>
      <option value="quarterly">季度</option>
      <option value="daily">每日</option>
    </select>
  </div>
  <div class="form-row">
    <label>初始资金</label>
    <input type="number" id="initialCash" value="10000000" style="width:140px">
    <label>波动率目标</label>
    <input type="number" id="volTarget" value="15" min="5" max="40" style="width:70px">
    <span style="font-size:11px;color:var(--text-dim)">%（仅vol_target模式）</span>
    <label>无风险利率</label>
    <input type="number" id="riskFreeRate" value="3" min="0" max="10" style="width:60px">
    <span style="font-size:11px;color:var(--text-dim)">%</span>
  </div>
</div>

<div class="card">
  <h3>📂 净值数据导入</h3>
  <div class="info-note">
    每个子策略需要一条净值曲线（JSON数组），格式: <code>[{"date":"2020-01-01","value":1000000}, ...]</code><br>
    粘贴完整JSON或从文件加载。每个策略的key应与上方策略名称匹配。
  </div>
  <textarea id="equity-data" rows="8" placeholder='{"ml_csi2000_fund":[{"date":"2020-07-01","value":1000000},...],"cb_stock_quality":[...]}'></textarea>
  <div style="margin-top:8px;display:flex;gap:8px">
    <button class="btn btn-sm" onclick="loadSampleData()">📥 加载示例数据</button>
    <button class="btn btn-sm" onclick="validateData()">✅ 验证数据</button>
    <input type="file" id="file-input" accept=".json" style="display:none" onchange="loadFile(event)">
    <button class="btn btn-sm" onclick="document.getElementById('file-input').click()">📁 从文件加载</button>
  </div>
</div>

<div style="margin:16px 0;display:flex;gap:10px">
  <button class="btn btn-primary" id="run-combo-btn" onclick="runCombine()">🚀 构建组合</button>
  <button class="btn" onclick="runAllTests()">🔬 一键全量验证</button>
  <span id="status" style="font-size:12px;color:var(--text-dim);line-height:32px"></span>
</div>
</div>

<!-- Tab 2: Results -->
<div class="tab-content" id="tab-results">
  <div id="results-empty" class="loading">请先在「组合配置」中配置策略并运行</div>
  <div id="results-content" style="display:none">
    <div class="kpi-row" id="combo-kpi"></div>
    <div class="card">
      <h3>📈 组合净值曲线</h3>
      <div id="equity-chart" class="svg-chart"></div>
    </div>
    <div class="card">
      <h3>📊 子策略对比</h3>
      <table><thead><tr>
        <th>策略</th><th>权重</th><th>年化收益</th><th>最大回撤</th><th>夏普比率</th><th>卡尔马比率</th><th>波动率</th><th>CVaR(95%)</th>
      </tr></thead><tbody id="sub-metrics-tbody"></tbody></table>
    </div>
    <div class="card">
      <h3>🔗 相关性矩阵</h3>
      <div id="corr-matrix"></div>
      <div id="corr-summary" style="margin-top:8px;font-size:12px;color:var(--text-dim)"></div>
    </div>
    <div class="card">
      <h3>⚖️ 权重变化历史</h3>
      <div style="max-height:300px;overflow-y:auto">
        <table><thead><tr><th>日期</th><th id="weight-cols">权重分配</th></tr></thead>
        <tbody id="weight-history-tbody"></tbody></table>
      </div>
    </div>
  </div>
</div>

<!-- Tab 3: Statistical Validation -->
<div class="tab-content" id="tab-validation">
  <div id="val-empty" class="loading">请先运行组合构建，然后执行统计验证</div>
  <div id="val-content" style="display:none">
    <div class="form-row" style="margin-bottom:12px">
      <label>独立试验数</label>
      <input type="number" id="n-trials" value="8" min="1" max="100" style="width:70px">
      <span style="font-size:11px;color:var(--text-dim)">N：测试过的独立策略配置数量（DSR/HLZ修正核心参数）</span>
      <button class="btn btn-primary btn-sm" onclick="runValidation()" style="margin-left:12px">🔬 运行验证</button>
    </div>
    <div class="kpi-row" id="val-kpi"></div>
    <div class="card">
      <h3>📉 DSR — Deflated Sharpe Ratio</h3>
      <div id="dsr-result"></div>
    </div>
    <div class="card">
      <h3>📐 HLZ — Harvey-Liu-Zhu 多重检验</h3>
      <div id="hlz-result"></div>
    </div>
    <div class="card">
      <h3>🎲 PBO — 回测过拟合概率</h3>
      <div id="pbo-result"></div>
    </div>
  </div>
</div>

<!-- Tab 4: Stress Tests -->
<div class="tab-content" id="tab-stress">
  <div id="stress-empty" class="loading">请先运行组合构建</div>
  <div id="stress-content" style="display:none">
    <div style="display:flex;gap:10px;margin-bottom:12px">
      <button class="btn btn-primary btn-sm" onclick="runPerturbation()">⚖️ 权重扰动 ±20%</button>
      <button class="btn btn-primary btn-sm" onclick="runSlippageTest()">📉 滑点灵敏度 2x/4x</button>
    </div>
    <div class="card">
      <h3>⚖️ 权重扰动测试</h3>
      <div id="perturbation-result"><span class="loading" style="padding:20px">点击上方按钮运行</span></div>
    </div>
    <div class="card">
      <h3>📉 滑点灵敏度测试</h3>
      <div id="slippage-result"><span class="loading" style="padding:20px">点击上方按钮运行</span></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
var TOKEN='${apiToken ?? ""}';
var comboResult=null;
var portfolioReturns=null;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function num(v){return v!=null?Number(v).toFixed(4):'-'}
function pct(v){return v!=null?(v>=0?'+':'')+(v*100).toFixed(2)+'%':'-'}
function pctRaw(v){return v!=null?(v>=0?'+':'')+v.toFixed(2)+'%':'-'}
function pctColor(v){return v>0?'var(--green)':v<0?'var(--red)':'var(--text)'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function api(path,opts){
  opts=opts||{};
  var url=B+path+(path.indexOf('?')>=0?'&':'?')+'token='+TOKEN;
  return fetch(url,opts).then(function(r){return r.json()});
}

/* ── Tab switching ── */
function switchTab(id){
  document.querySelectorAll('.tab').forEach(function(t){t.classList.toggle('active',t.dataset.tab===id)});
  document.querySelectorAll('.tab-content').forEach(function(c){c.classList.toggle('active',c.id==='tab-'+id)});
}

/* ── Strategy list management ── */
var stratIdx=4;
function addStrat(){
  var list=document.getElementById('strat-list');
  var row=document.createElement('div');
  row.className='strat-row';row.dataset.idx=stratIdx++;
  row.innerHTML='<input type="text" class="strat-name-input" placeholder="策略名称" style="width:160px">'
    +'<label style="font-size:11px;color:var(--text-dim)">权重%</label>'
    +'<input type="number" class="strat-weight-input" value="10" min="0" max="100" style="width:60px">'
    +'<select class="strat-asset-select" style="width:100px"><option value="equity">A股</option><option value="cb">可转债</option><option value="etf">ETF</option><option value="commodity">商品</option></select>'
    +'<label style="font-size:11px;color:var(--text-dim)">滑点bps</label>'
    +'<input type="number" class="strat-slippage-input" value="10" min="0" style="width:60px">'
    +'<button class="btn btn-sm btn-danger" onclick="removeStrat(this)">✕</button>';
  list.appendChild(row);
  updateWeightTotal();
}
function removeStrat(btn){
  var rows=document.querySelectorAll('.strat-row');
  if(rows.length<=1){toast('至少保留1个子策略');return}
  btn.closest('.strat-row').remove();
  updateWeightTotal();
}
function updateWeightTotal(){
  var total=0;
  document.querySelectorAll('.strat-weight-input').forEach(function(i){total+=parseFloat(i.value)||0});
  var el=document.getElementById('weight-total');
  el.textContent='权重合计: '+total+'%';
  el.style.color=Math.abs(total-100)<0.01?'var(--green)':'var(--yellow)';
}
document.addEventListener('input',function(e){if(e.target.classList.contains('strat-weight-input'))updateWeightTotal()});

function collectStrategies(){
  var strats=[];
  document.querySelectorAll('.strat-row').forEach(function(row){
    strats.push({
      name:row.querySelector('.strat-name-input').value.trim(),
      weight:parseFloat(row.querySelector('.strat-weight-input').value)||0,
      asset_class:row.querySelector('.strat-asset-select').value,
      slippage_bps:parseFloat(row.querySelector('.strat-slippage-input').value)||10
    });
  });
  return strats.filter(function(s){return s.name});
}

/* ── Data management ── */
function validateData(){
  try{
    var raw=document.getElementById('equity-data').value.trim();
    if(!raw){toast('请先输入净值数据','warning');return}
    var data=JSON.parse(raw);
    var strats=collectStrategies();
    var missing=strats.filter(function(s){return !data[s.name]}).map(function(s){return s.name});
    if(missing.length>0){toast('缺少策略数据: '+missing.join(', '));return}
    var info=[];
    Object.keys(data).forEach(function(k){
      var arr=data[k];
      if(Array.isArray(arr)&&arr.length>0){
        info.push(k+': '+arr.length+'天 ('+arr[0].date+' ~ '+arr[arr.length-1].date+')');
      }
    });
    toast('✅ 数据验证通过\\n'+info.join('\\n'),3000);
  }catch(e){toast('❌ JSON解析失败: '+e.message)}
}

function loadFile(e){
  var file=e.target.files[0];if(!file)return;
  var reader=new FileReader();
  reader.onload=function(ev){document.getElementById('equity-data').value=ev.target.result;toast('已加载: '+file.name)};
  reader.readAsText(file);
}

function loadSampleData(){
  var now=new Date();var dates=[];
  for(var i=0;i<1400;i++){var d=new Date(2020,6,1);d.setDate(d.getDate()+i);if(d.getDay()===0||d.getDay()===6)continue;dates.push(d.toISOString().slice(0,10))}
  dates=dates.slice(0,1200);
  var makeCurve=function(ann,vol){
    var v=1000000;var curve=[];var dailyR=Math.pow(1+ann,1/252)-1;var dailyV=vol/Math.sqrt(252);
    dates.forEach(function(dt){
      v*=(1+dailyR+(Math.random()-0.5)*2*dailyV);
      curve.push({date:dt,value:Math.round(v*100)/100});
    });return curve;
  };
  var sample={
    ml_csi2000_fund:makeCurve(0.35,0.22),
    cb_stock_quality:makeCurve(0.28,0.14),
    v19_hgro:makeCurve(0.20,0.35),
    etf_cross_v3:makeCurve(0.10,0.18)
  };
  document.getElementById('equity-data').value=JSON.stringify(sample);
  toast('已加载模拟示例数据（约1200个交易日）');
}

/* ── Run Combine ── */
function runCombine(){
  var strats=collectStrategies();
  if(strats.length<2){toast('至少需要2个子策略');return}
  var raw=document.getElementById('equity-data').value.trim();
  if(!raw){toast('请先导入净值数据');return}
  var equityData;
  try{equityData=JSON.parse(raw)}catch(e){toast('JSON解析失败: '+e.message);return}

  var btn=document.getElementById('run-combo-btn');
  btn.disabled=true;btn.textContent='⏳ 构建中...';
  document.getElementById('status').textContent='正在构建组合...';

  var payload={
    action:'combine',
    strategies:strats,
    equity_curves:equityData,
    method:document.getElementById('method').value,
    rebalance_freq:document.getElementById('rebalance').value,
    initial_cash:parseFloat(document.getElementById('initialCash').value)||10000000,
    portfolio_vol_target:(parseFloat(document.getElementById('volTarget').value)||15)/100,
    risk_free_rate:(parseFloat(document.getElementById('riskFreeRate').value)||3)/100
  };

  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)
  }).then(function(d){
    btn.disabled=false;btn.textContent='🚀 构建组合';
    document.getElementById('status').textContent='';
    if(d.error){toast('❌ '+d.error);return}
    comboResult=d;
    portfolioReturns=d.portfolio_returns||null;
    renderResults(d);
    switchTab('results');
    // Enable other tabs
    document.getElementById('results-empty').style.display='none';
    document.getElementById('results-content').style.display='block';
    document.getElementById('val-empty').style.display='none';
    document.getElementById('val-content').style.display='block';
    document.getElementById('stress-empty').style.display='none';
    document.getElementById('stress-content').style.display='block';
    toast('✅ 组合构建完成');
  }).catch(function(e){
    btn.disabled=false;btn.textContent='🚀 构建组合';
    toast('❌ 请求失败: '+e.message);
  });
}

function renderResults(d){
  var m=d.portfolio_metrics||{};
  // KPIs
  document.getElementById('combo-kpi').innerHTML=
    kpi(pct(m.annualized_return),'年化收益',pctColor(m.annualized_return))+
    kpi((m.max_drawdown!=null?'-'+(m.max_drawdown*100).toFixed(2)+'%':'-'),'最大回撤','var(--red)')+
    kpi(num(m.sharpe_ratio),'夏普比率',m.sharpe_ratio>=1.0?'var(--green)':'var(--yellow)')+
    kpi(num(m.calmar_ratio),'卡尔马比率',m.calmar_ratio>=1.0?'var(--green)':'var(--text)')+
    kpi((m.volatility!=null?(m.volatility*100).toFixed(2)+'%':'-'),'波动率','var(--text)')+
    kpi((m.cvar_95!=null?(m.cvar_95*100).toFixed(2)+'%':'-'),'CVaR(95%)','var(--orange)');

  // Sub-strategy metrics
  var sm=d.sub_strategy_metrics||{};
  var fw=d.final_weights||{};
  var tbody='';
  Object.keys(sm).forEach(function(name){
    var s=sm[name];
    tbody+='<tr><td><strong>'+esc(name)+'</strong></td>'
      +'<td>'+(fw[name]!=null?(fw[name]*100).toFixed(1)+'%':'-')+'</td>'
      +'<td style="color:'+pctColor(s.annualized_return)+'">'+pct(s.annualized_return)+'</td>'
      +'<td style="color:var(--red)">'+(s.max_drawdown!=null?'-'+(s.max_drawdown*100).toFixed(2)+'%':'-')+'</td>'
      +'<td>'+num(s.sharpe_ratio)+'</td>'
      +'<td>'+num(s.calmar_ratio)+'</td>'
      +'<td>'+(s.volatility!=null?(s.volatility*100).toFixed(2)+'%':'-')+'</td>'
      +'<td>'+(s.cvar_95!=null?(s.cvar_95*100).toFixed(2)+'%':'-')+'</td></tr>';
  });
  document.getElementById('sub-metrics-tbody').innerHTML=tbody;

  // Correlation matrix
  var corr=d.correlation||{};
  renderCorrelationMatrix(corr);

  // Equity curve chart
  renderEquityChart(d.equity_curve||[]);

  // Weight history
  renderWeightHistory(d.weight_history||[],Object.keys(sm));
}

function kpi(val,label,color){
  return '<div class="kpi"><div class="val" style="color:'+(color||'var(--text)')+'">'+val+'</div><div class="lbl">'+label+'</div></div>';
}

function renderCorrelationMatrix(corr){
  var matrix=corr.matrix||{};
  var names=Object.keys(matrix);
  if(!names.length){document.getElementById('corr-matrix').innerHTML='<span class="loading">无数据</span>';return}
  var n=names.length;
  var html='<div class="corr-matrix" style="grid-template-columns:100px repeat('+n+',1fr)">';
  html+='<div></div>';
  names.forEach(function(name){html+='<div style="font-size:11px;font-weight:600;text-align:center;padding:4px">'+esc(name.slice(0,8))+'</div>'});
  names.forEach(function(r){
    html+='<div style="font-size:11px;font-weight:600;padding:4px">'+esc(r.slice(0,10))+'</div>';
    names.forEach(function(c){
      var v=matrix[r]&&matrix[r][c]!=null?matrix[r][c]:0;
      var bg=v>0.5?'rgba(248,81,73,0.3)':v>0.2?'rgba(210,153,34,0.2)':v<-0.2?'rgba(63,185,80,0.2)':'rgba(88,166,255,0.1)';
      if(r===c)bg='rgba(88,166,255,0.2)';
      html+='<div class="corr-cell" style="background:'+bg+'">'+v.toFixed(3)+'</div>';
    });
  });
  html+='</div>';
  document.getElementById('corr-matrix').innerHTML=html;
  document.getElementById('corr-summary').innerHTML=
    '平均相关: <strong>'+((corr.average||0)).toFixed(4)+'</strong> | 最大配对相关: <strong>'+(corr.max_pairwise||0).toFixed(4)+'</strong>'
    +(corr.average<0.3?' <span class="badge badge-green">分散化优异</span>':corr.average<0.6?' <span class="badge badge-yellow">一般</span>':' <span class="badge badge-red">高相关</span>');
}

function renderEquityChart(curve){
  if(!curve||!curve.length){return}
  var container=document.getElementById('equity-chart');
  var w=container.clientWidth||800,h=280;
  var values=curve.map(function(p){return p.value});
  var minV=Math.min.apply(null,values),maxV=Math.max.apply(null,values);
  var range=maxV-minV||1;
  var pad={t:20,r:60,b:30,l:10};
  var cw=w-pad.l-pad.r,ch=h-pad.t-pad.b;
  var points=curve.map(function(p,i){
    return (pad.l+i/(curve.length-1)*cw)+','+(pad.t+ch-(p.value-minV)/range*ch);
  }).join(' ');
  var svg='<svg width="'+w+'" height="'+h+'" viewBox="0 0 '+w+' '+h+'">'
    +'<polyline fill="none" stroke="var(--accent)" stroke-width="1.5" points="'+points+'"/>';
  // Y-axis labels
  for(var i=0;i<=4;i++){
    var yVal=minV+range*(i/4);
    var yPos=pad.t+ch-ch*(i/4);
    svg+='<text x="'+(w-5)+'" y="'+(yPos+4)+'" fill="var(--text-dim)" font-size="10" text-anchor="end">'+(yVal/10000).toFixed(0)+'万</text>';
    svg+='<line x1="'+pad.l+'" y1="'+yPos+'" x2="'+(w-pad.r)+'" y2="'+yPos+'" stroke="var(--border)" stroke-width="0.5" stroke-dasharray="3"/>';
  }
  // X-axis: first and last date
  svg+='<text x="'+pad.l+'" y="'+(h-5)+'" fill="var(--text-dim)" font-size="10">'+esc(curve[0].date)+'</text>';
  svg+='<text x="'+(w-pad.r)+'" y="'+(h-5)+'" fill="var(--text-dim)" font-size="10" text-anchor="end">'+esc(curve[curve.length-1].date)+'</text>';
  svg+='</svg>';
  container.innerHTML=svg;
}

function renderWeightHistory(history,names){
  if(!history||!history.length){document.getElementById('weight-history-tbody').innerHTML='<tr><td colspan="2" class="loading">固定权重模式无变化记录</td></tr>';return}
  var cols=names||[];
  document.getElementById('weight-cols').textContent=cols.map(function(n){return n}).join(' / ');
  var tbody='';
  history.slice(-50).forEach(function(h){
    var wts=h.weights||{};
    tbody+='<tr><td>'+esc(h.date)+'</td><td>';
    cols.forEach(function(n,i){
      if(i>0)tbody+=' / ';
      tbody+='<span style="color:var(--accent)">'+(wts[n]!=null?(wts[n]*100).toFixed(1)+'%':'—')+'</span>';
    });
    tbody+='</td></tr>';
  });
  document.getElementById('weight-history-tbody').innerHTML=tbody;
}

/* ── Statistical Validation ── */
function runValidation(){
  if(!comboResult){toast('请先运行组合构建');return}
  var nTrials=parseInt(document.getElementById('n-trials').value)||8;

  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'validate',portfolio_returns:comboResult.portfolio_returns||[],n_trials:nTrials,
      observed_sharpe:comboResult.portfolio_metrics?comboResult.portfolio_metrics.sharpe_ratio:null})
  }).then(function(d){
    if(d.error){toast('❌ '+d.error);return}
    renderValidation(d);
    toast('✅ 统计验证完成');
  }).catch(function(e){toast('❌ '+e.message)});
}

function renderValidation(d){
  var dsr=d.dsr||{},hlz=d.hlz||{},pbo=d.pbo||{},sum=d.summary||{};
  // KPIs
  document.getElementById('val-kpi').innerHTML=
    kpi(sum.verdict||'-','综合判定',sum.verdict==='STRONG'?'var(--green)':sum.verdict==='ACCEPTABLE'?'var(--yellow)':'var(--red)')+
    kpi(sum.score||'-','得分','var(--accent)')+
    kpi('p='+num(dsr.p_value),'DSR p值',dsr.significant?'var(--green)':'var(--red)')+
    kpi(num(hlz.t_adjusted),'HLZ t_adj',hlz.significant?'var(--green)':'var(--yellow)')+
    kpi((pbo.pbo!=null?pbo.pbo.toFixed(4):'-'),'PBO',pbo.is_overfitted?'var(--red)':'var(--green)');

  // DSR detail
  document.getElementById('dsr-result').innerHTML=
    '<table><tr><th>指标</th><th>值</th><th>达标</th></tr>'
    +'<tr><td>观察SR</td><td>'+num(dsr.observed_sr)+'</td><td>—</td></tr>'
    +'<tr><td>Haircut SR (E[max])</td><td>'+num(dsr.haircut_sr)+'</td><td>—</td></tr>'
    +'<tr><td>p-value</td><td><strong>'+num(dsr.p_value)+'</strong></td><td>'+(dsr.significant?'<span class="badge badge-green">✅ &lt;0.05</span>':'<span class="badge badge-red">❌ ≥0.05</span>')+'</td></tr>'
    +'<tr><td>显著性</td><td colspan="2">'+(dsr.significant?'<strong style="color:var(--green)">通过</strong>':'<strong style="color:var(--red)">未通过</strong>')+'</td></tr>'
    +'</table>'
    +'<div class="info-note" style="margin-top:8px">Bailey & Lopez de Prado (2014): 修正多重检验后的夏普比率。N='+((d.summary||{}).n_trials||'?')+'个独立试验。</div>';

  // HLZ detail
  var hlzNote=hlz.note||'';
  document.getElementById('hlz-result').innerHTML=
    '<table><tr><th>指标</th><th>值</th><th>达标</th></tr>'
    +'<tr><td>t_observed (SR×√n_years)</td><td>'+num(hlz.t_observed)+'</td><td>—</td></tr>'
    +'<tr><td>惩罚项 √(2·ln(N))</td><td>'+num(hlz.penalty)+'</td><td>—</td></tr>'
    +'<tr><td>t_adjusted</td><td><strong>'+num(hlz.t_adjusted)+'</strong></td><td>'+(hlz.significant?'<span class="badge badge-green">✅ ≥2.0</span>':'<span class="badge badge-yellow">⚠️ &lt;2.0</span>')+'</td></tr>'
    +'<tr><td>Haircut%</td><td>'+pctRaw(hlz.haircut_pct)+'</td><td>—</td></tr>'
    +'</table>'
    +(hlzNote?'<div class="info-note" style="margin-top:8px">'+esc(hlzNote)+'</div>':'');

  // PBO detail
  document.getElementById('pbo-result').innerHTML=
    '<table><tr><th>指标</th><th>值</th><th>达标</th></tr>'
    +'<tr><td>PBO</td><td><strong>'+num(pbo.pbo)+'</strong></td><td>'+(pbo.is_overfitted?'<span class="badge badge-red">❌ ≥0.50 (过拟合)</span>':'<span class="badge badge-green">✅ &lt;0.50</span>')+'</td></tr>'
    +'<tr><td>Logit均值</td><td>'+num(pbo.logit_mean)+'</td><td>'+(pbo.logit_mean>0?'<span class="badge badge-green">正向</span>':'<span class="badge badge-yellow">负向</span>')+'</td></tr>'
    +'</table>';
}

/* ── Stress Tests ── */
function runPerturbation(){
  if(!comboResult){toast('请先运行组合构建');return}
  document.getElementById('perturbation-result').innerHTML='<span class="loading">运行中...</span>';
  var raw=document.getElementById('equity-data').value.trim();
  var equityData;try{equityData=JSON.parse(raw)}catch(e){toast('数据解析失败');return}
  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'perturbation',strategies:collectStrategies(),equity_curves:equityData,
      method:document.getElementById('method').value,initial_cash:parseFloat(document.getElementById('initialCash').value)||10000000})
  }).then(function(d){
    if(d.error){document.getElementById('perturbation-result').innerHTML='<span style="color:var(--red)">'+esc(d.error)+'</span>';return}
    var html='<div class="kpi-row">'
      +kpi(d.all_stable?'✅ 全部稳定':'⚠️ 存在不稳定',d.all_stable?'权重稳定':'需关注',d.all_stable?'var(--green)':'var(--yellow)')
      +kpi(pctRaw(d.max_sr_change_pct),'最大SR变化','var(--accent)')
      +kpi(num(d.baseline_sharpe),'基线SR','var(--text)')+'</div>';
    html+='<table><thead><tr><th>策略</th><th>扰动方向</th><th>扰动后权重</th><th>扰动SR</th><th>SR变化%</th><th>稳定?</th></tr></thead><tbody>';
    (d.results||[]).forEach(function(r){
      html+='<tr><td>'+esc(r.strategy)+'</td><td>'+esc(r.direction)+'</td>'
        +'<td>'+r.perturbed_weight.toFixed(1)+'%</td>'
        +'<td>'+num(r.sharpe_ratio)+'</td>'
        +'<td style="color:'+(r.sr_change_pct>15?'var(--yellow)':'var(--green)')+'">'+r.sr_change_pct.toFixed(2)+'%</td>'
        +'<td>'+(r.stable?'<span class="badge badge-green">✅</span>':'<span class="badge badge-red">❌</span>')+'</td></tr>';
    });
    html+='</tbody></table>';
    document.getElementById('perturbation-result').innerHTML=html;
    toast(d.all_stable?'✅ 权重扰动全部通过':'⚠️ 存在不稳定扰动');
  }).catch(function(e){document.getElementById('perturbation-result').innerHTML='<span style="color:var(--red)">'+e.message+'</span>'});
}

function runSlippageTest(){
  if(!comboResult){toast('请先运行组合构建');return}
  document.getElementById('slippage-result').innerHTML='<span class="loading">运行中...</span>';
  var raw=document.getElementById('equity-data').value.trim();
  var equityData;try{equityData=JSON.parse(raw)}catch(e){toast('数据解析失败');return}
  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'slippage',strategies:collectStrategies(),equity_curves:equityData,
      method:document.getElementById('method').value,initial_cash:parseFloat(document.getElementById('initialCash').value)||10000000})
  }).then(function(d){
    if(d.error){document.getElementById('slippage-result').innerHTML='<span style="color:var(--red)">'+esc(d.error)+'</span>';return}
    var html='<div class="kpi-row">'
      +kpi(d.all_pass?'✅ 全部通过':'❌ 未通过','滑点压力',d.all_pass?'var(--green)':'var(--red)')+'</div>';
    html+='<table><thead><tr><th>滑点倍率</th><th>夏普比率</th><th>年化收益</th><th>最大回撤</th><th>SR>0.8?</th></tr></thead><tbody>';
    (d.results||[]).forEach(function(r){
      html+='<tr><td><strong>'+esc(r.multiplier)+'</strong></td>'
        +'<td>'+num(r.sharpe_ratio)+'</td>'
        +'<td>'+pct(r.annualized_return)+'</td>'
        +'<td style="color:var(--red)">'+(r.max_drawdown!=null?'-'+(r.max_drawdown*100).toFixed(2)+'%':'-')+'</td>'
        +'<td>'+(r.sr_above_threshold?'<span class="badge badge-green">✅</span>':'<span class="badge badge-red">❌</span>')+'</td></tr>';
    });
    html+='</tbody></table>';
    document.getElementById('slippage-result').innerHTML=html;
    toast(d.all_pass?'✅ 滑点灵敏度全部通过':'⚠️ 高倍率滑点下SR不足');
  }).catch(function(e){document.getElementById('slippage-result').innerHTML='<span style="color:var(--red)">'+e.message+'</span>'});
}

/* ── Run All Tests ── */
function runAllTests(){
  if(!document.getElementById('equity-data').value.trim()){toast('请先导入净值数据');return}
  runCombine();
  // Validation and stress tests will be available after combo completes
}

updateWeightTotal();
</script>
</body>
</html>`;
}
