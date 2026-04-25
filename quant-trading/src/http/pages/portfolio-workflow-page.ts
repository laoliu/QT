/**
 * Portfolio Workflow Page — unified step-by-step wizard that orchestrates:
 *   Step 1: Strategy Fitness Analysis (run / poll / view global ranking)
 *   Step 2: Select top-N strategies + combo backtest
 *   Step 3: Cross-asset portfolio construction (combine, validate, stress)
 *   Step 4: Apply to paper trading
 *
 * Self-contained HTML SPA, no external dependencies.
 */
import {
  THEME_CSS_VARS,
  BASE_CSS_RESET,
  HEADER_CSS,
  TOAST_CSS,
  KPI_CSS,
  SHARED_JS_HELPERS,
  SHARED_JS_API,
} from "./_shared.js";

export function getPortfolioWorkflowPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>投资组合工作流</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🚀</text></svg>">
<style>
${THEME_CSS_VARS}
${BASE_CSS_RESET}
${HEADER_CSS}
${TOAST_CSS}
${KPI_CSS}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{margin-bottom:12px;font-size:15px;display:flex;align-items:center;gap:8px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--text);transition:all .15s}
.btn:hover{background:var(--border)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-success{background:var(--green);color:#fff;border-color:var(--green)}
.btn-sm{padding:4px 10px;font-size:12px}
label{display:block;font-size:13px;color:var(--text-dim);margin-bottom:4px}
select,input[type=text],input[type=number]{padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px}
.badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:11px;font-weight:600}
.badge-green{background:rgba(63,185,80,.15);color:var(--green)}
.badge-red{background:rgba(248,81,73,.15);color:var(--red)}
.badge-yellow{background:rgba(210,153,34,.15);color:var(--yellow)}
.info-note{background:var(--bg3);border-left:3px solid var(--accent);padding:10px 14px;font-size:12px;color:var(--text-dim);border-radius:0 6px 6px 0;margin-bottom:12px}

/* Stepper */
.stepper{display:flex;gap:0;margin-bottom:24px;position:relative}
.step{flex:1;text-align:center;padding:12px 8px;cursor:pointer;position:relative;border-bottom:3px solid var(--border);transition:all .2s}
.step:hover{background:var(--bg3)}
.step.active{border-bottom-color:var(--accent);color:var(--accent)}
.step.done{border-bottom-color:var(--green);color:var(--green)}
.step-num{display:inline-flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:50%;border:2px solid var(--border);font-size:14px;font-weight:700;margin-bottom:4px}
.step.active .step-num{border-color:var(--accent);background:var(--accent);color:#fff}
.step.done .step-num{border-color:var(--green);background:var(--green);color:#fff}
.step-label{display:block;font-size:12px;margin-top:4px}
.step-panel{display:none}
.step-panel.active{display:block}

/* Progress bar */
.progress-bar{width:100%;height:8px;background:var(--bg3);border-radius:4px;overflow:hidden;margin:8px 0}
.progress-fill{height:100%;background:var(--accent);transition:width .3s;border-radius:4px}

/* Strategy checkbox list */
.strat-list{max-height:400px;overflow-y:auto;border:1px solid var(--border);border-radius:6px;padding:8px}
.strat-item{display:flex;align-items:center;gap:10px;padding:6px 8px;border-radius:4px;cursor:pointer}
.strat-item:hover{background:var(--bg3)}
.strat-item input[type=checkbox]{width:16px;height:16px;accent-color:var(--accent)}
.strat-item .strat-name{flex:1;font-size:13px}
.strat-item .strat-score{font-size:12px;color:var(--yellow)}
.strat-item .strat-sr{font-size:12px;color:var(--green)}

/* KPI row */
.kpi-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}

/* Param row */
.param-row{display:flex;gap:16px;align-items:end;flex-wrap:wrap;margin-bottom:12px}
.param-row .param-group{display:flex;flex-direction:column;gap:4px}

/* Equity chart */
.chart-container{background:var(--bg3);border-radius:8px;padding:16px;margin-bottom:16px}
</style>
</head>
<body>
<header>
  <div style="display:flex;align-items:center;gap:12px">
    <h1>🚀 投资组合工作流</h1>
  </div>
  <div class="header-right">
    <a href="/quant/admin/ui">🏠 首页</a>
    <a href="/quant/admin/portfolio-combo">⚙️ 组合构建器</a>
    <a href="/quant/admin/strategy-fitness">🎯 策略适配</a>
  </div>
</header>
<div class="container">
  <!-- Stepper -->
  <div class="stepper">
    <div class="step active" onclick="goStep(1)" id="step-1">
      <div class="step-num">1</div>
      <span class="step-label">策略筛选</span>
    </div>
    <div class="step" onclick="goStep(2)" id="step-2">
      <div class="step-num">2</div>
      <span class="step-label">选择策略 & 配置</span>
    </div>
    <div class="step" onclick="goStep(3)" id="step-3">
      <div class="step-num">3</div>
      <span class="step-label">组合构建 & 验证</span>
    </div>
    <div class="step" onclick="goStep(4)" id="step-4">
      <div class="step-num">4</div>
      <span class="step-label">应用到模拟盘</span>
    </div>
  </div>

  <!-- ====== STEP 1: Strategy Fitness ====== -->
  <div class="step-panel active" id="panel-1">
    <div class="card">
      <h3>🎯 第一步：策略适应度分析</h3>
      <div class="info-note">
        系统将对股票池中所有股票 × 所有策略进行回测，计算综合评分并排名。
        如果已有历史批次数据，可直接加载使用。
      </div>
      <div class="param-row">
        <div class="param-group">
          <label>回测天数</label>
          <input type="number" id="s1-days" value="250" style="width:100px" min="60" max="2000">
        </div>
        <div class="param-group">
          <label>初始资金</label>
          <input type="number" id="s1-cash" value="100000" style="width:120px">
        </div>
        <div class="param-group" style="padding-bottom:0">
          <button class="btn btn-primary" id="s1-run-btn" onclick="step1Run()">🚀 运行分析</button>
        </div>
        <div class="param-group" style="padding-bottom:0">
          <button class="btn" onclick="step1Load()">📂 加载历史批次</button>
        </div>
        <div class="param-group" style="padding-bottom:0">
          <button class="btn btn-danger btn-sm" onclick="step1Stop()" id="s1-stop-btn" disabled>⏹ 停止</button>
        </div>
      </div>
      <div id="s1-progress" style="display:none">
        <div class="progress-bar"><div class="progress-fill" id="s1-progress-fill" style="width:0%"></div></div>
        <div id="s1-progress-text" style="font-size:12px;color:var(--text-dim)"></div>
      </div>
    </div>
    <div class="card" id="s1-results" style="display:none">
      <h3>📊 全局策略排名</h3>
      <div class="kpi-row" id="s1-kpis"></div>
      <table>
        <thead><tr><th>#</th><th>策略</th><th>综合评分</th><th>覆盖股票数</th><th>平均评分</th><th>最高评分</th><th>最低评分</th></tr></thead>
        <tbody id="s1-table"></tbody>
      </table>
      <div style="margin-top:12px">
        <button class="btn btn-primary" onclick="step1Done()">✅ 继续下一步 →</button>
      </div>
    </div>
  </div>

  <!-- ====== STEP 2: Select Strategies & Configure ====== -->
  <div class="step-panel" id="panel-2">
    <div class="card">
      <h3>🎛️ 第二步：选择子策略 & 配置权重</h3>
      <div class="info-note">
        从排名中勾选要纳入组合的策略，设置权重分配方法和参数。建议选择 3~8 个低相关策略。
      </div>
      <div style="display:flex;gap:20px;flex-wrap:wrap">
        <div style="flex:1;min-width:350px">
          <h4 style="font-size:14px;margin-bottom:8px">可选策略（按评分排序）</h4>
          <div class="strat-list" id="s2-strat-list">
            <div style="color:var(--text-dim);padding:20px;text-align:center">请先完成第一步</div>
          </div>
          <div style="margin-top:8px;font-size:12px;color:var(--text-dim);display:flex;align-items:center;gap:10px">
            已选: <span id="s2-count">0</span> 个策略
            <button class="btn" id="s2-auto-btn" onclick="step2AutoSelect()" style="font-size:11px;padding:3px 10px;background:var(--accent);color:#fff;border-radius:4px">🤖 自动优选低相关组合</button>
            <label style="font-size:11px;display:flex;align-items:center;gap:3px">目标数:
              <input type="number" id="s2-auto-count" value="5" min="2" max="10" style="width:40px;font-size:11px">
            </label>
          </div>
        </div>
        <div style="flex:1;min-width:300px">
          <h4 style="font-size:14px;margin-bottom:8px">组合参数</h4>
          <div class="param-row">
            <div class="param-group">
              <label>组合方法</label>
              <select id="s2-method" style="width:150px" onchange="updateWeightTable()">
                <option value="fixed">固定权重</option>
                <option value="risk_parity">风险平价</option>
                <option value="vol_target">波动率目标制</option>
              </select>
            </div>
            <div class="param-group">
              <label>再平衡频率</label>
              <select id="s2-rebalance" style="width:120px">
                <option value="monthly">月度</option>
                <option value="weekly">周度</option>
                <option value="quarterly">季度</option>
              </select>
            </div>
          </div>
          <div class="param-row">
            <div class="param-group">
              <label>初始资金</label>
              <input type="number" id="s2-cash" value="10000000" style="width:140px">
            </div>
            <div class="param-group">
              <label>波动率目标%</label>
              <input type="number" id="s2-vol" value="15" style="width:80px">
            </div>
            <div class="param-group">
              <label>无风险利率%</label>
              <input type="number" id="s2-rf" value="3" style="width:80px">
            </div>
          </div>
          <div class="param-row">
            <div class="param-group">
              <label>回测天数</label>
              <input type="number" id="s2-days" value="1000" style="width:100px" min="60">
            </div>
            <div class="param-group">
              <label>默认滑点(bps)</label>
              <input type="number" id="s2-slippage" value="10" style="width:80px">
            </div>
          </div>

          <h4 style="font-size:14px;margin:16px 0 8px">权重分配</h4>
          <div id="s2-weight-table"></div>
        </div>
      </div>

      <!-- Correlation Preview -->
      <div id="s2-corr-section" style="margin-top:16px">
        <button class="btn" onclick="step2CorrPreview()" id="s2-corr-btn" style="background:var(--bg3)">📊 预检相关性</button>
        <span style="font-size:12px;color:var(--text-dim);margin-left:8px">选完策略后点击，快速预估策略间相关性（无需跑回测）</span>
        <div id="s2-corr-result" style="margin-top:12px"></div>
      </div>

      <div style="margin-top:16px;display:flex;gap:8px">
        <button class="btn" onclick="goStep(1)">← 上一步</button>
        <button class="btn btn-primary" id="s2-run-btn" onclick="step2Run()">🚀 运行组合回测</button>
      </div>
      <div id="s2-progress" style="display:none;margin-top:12px">
        <div class="progress-bar"><div class="progress-fill" id="s2-progress-fill" style="width:0%"></div></div>
        <div id="s2-progress-text" style="font-size:12px;color:var(--text-dim)"></div>
      </div>
    </div>
  </div>

  <!-- ====== STEP 3: Combine + Validate + Stress ====== -->
  <div class="step-panel" id="panel-3">
    <div class="card">
      <h3>📊 第三步：组合构建 & 验证</h3>
      <div class="info-note">
        使用第二步的回测净值数据，构建投资组合、统计验证和压力测试。
      </div>
    </div>

    <!-- 3a: Performance -->
    <div class="card" id="s3-perf" style="display:none">
      <h3>📈 绩效分析</h3>
      <div class="kpi-row" id="s3-kpis"></div>
      <div class="chart-container" id="s3-chart"></div>
      <h4 style="font-size:14px;margin-bottom:8px">子策略对比</h4>
      <table>
        <thead><tr><th>策略</th><th>权重</th><th>年化收益</th><th>最大回撤</th><th>Sharpe</th><th>波动率</th></tr></thead>
        <tbody id="s3-sub-table"></tbody>
      </table>
      <h4 style="font-size:14px;margin:16px 0 8px">🔗 相关性矩阵</h4>
      <div id="s3-corr"></div>
    </div>

    <!-- 3b: Statistical Validation -->
    <div class="card" id="s3-val" style="display:none">
      <h3>🔬 统计验证</h3>
      <div class="param-row">
        <div class="param-group">
          <label>独立试验数</label>
          <input type="number" id="s3-ntrials" value="8" style="width:80px" min="1">
        </div>
        <div class="param-group" style="padding-bottom:0">
          <button class="btn btn-primary btn-sm" onclick="step3Validate()">🔬 运行验证</button>
        </div>
      </div>
      <div class="kpi-row" id="s3-val-kpis"></div>
      <div id="s3-val-detail"></div>
    </div>

    <!-- 3c: Stress Tests -->
    <div class="card" id="s3-stress" style="display:none">
      <h3>💪 压力测试</h3>
      <div style="display:flex;gap:8px;margin-bottom:12px">
        <button class="btn btn-primary btn-sm" onclick="step3Perturbation()">🔄 权重扰动 ±20%</button>
        <button class="btn btn-primary btn-sm" onclick="step3Slippage()">📉 滑点灵敏度 2x/4x</button>
      </div>
      <div id="s3-perturb-result"></div>
      <div id="s3-slippage-result" style="margin-top:12px"></div>
    </div>

    <div style="display:flex;gap:8px;margin-top:8px">
      <button class="btn" onclick="goStep(2)">← 上一步</button>
      <button class="btn btn-primary" onclick="step3Done()" id="s3-next-btn" disabled>✅ 继续下一步 →</button>
    </div>
  </div>

  <!-- ====== STEP 4: Apply ====== -->
  <div class="step-panel" id="panel-4">
    <div class="card">
      <h3>🎯 第四步：应用到模拟盘</h3>
      <div class="info-note">
        确认组合配置无误后，可将此策略组合应用到模拟盘。系统将按照确定的权重分配资金并自动执行。
      </div>
    </div>

    <div class="card" id="s4-summary">
      <h3>📋 组合摘要</h3>
      <div class="kpi-row" id="s4-kpis"></div>
      <table>
        <thead><tr><th>策略</th><th>权重</th><th>资产类别</th><th>滑点bps</th></tr></thead>
        <tbody id="s4-table"></tbody>
      </table>
    </div>

    <div class="card">
      <h3>🔧 执行选项</h3>
      <div class="param-row">
        <div class="param-group">
          <label>执行模式</label>
          <select id="s4-mode" style="width:150px">
            <option value="paper">模拟盘</option>
            <option value="dry_run">仅生成信号（不下单）</option>
          </select>
        </div>
        <div class="param-group">
          <label>初始资金</label>
          <input type="number" id="s4-cash" value="10000000" style="width:140px">
        </div>
      </div>
      <div style="margin-top:16px;display:flex;gap:8px">
        <button class="btn" onclick="goStep(3)">← 上一步</button>
        <button class="btn btn-success" onclick="step4Apply()">🚀 应用到模拟盘</button>
      </div>
      <div id="s4-result" style="margin-top:12px"></div>
    </div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var B='${b}';
var TOKEN=${apiToken ? `'${apiToken}'` : "null"};
${SHARED_JS_HELPERS}

// Override api() to auto-append token query param
function api(path,opts){
  opts=opts||{};
  var timeoutMs=opts._timeout||15000;
  var url=B+path+(path.indexOf('?')>=0?'&':'?')+'token='+(TOKEN||'');
  var headers=opts.body?{'Content-Type':'application/json'}:{};
  if(opts.headers)Object.keys(opts.headers).forEach(function(k){headers[k]=opts.headers[k]});
  var fetchOpts={method:opts.method||'GET',headers:headers};
  if(opts.body)fetchOpts.body=opts.body;
  function doFetch(){
    var controller=typeof AbortController!=='undefined'?new AbortController():null;
    var timer=controller?setTimeout(function(){controller.abort()},timeoutMs):null;
    var fo=controller?Object.assign({},fetchOpts,{signal:controller.signal}):fetchOpts;
    return fetch(url,fo).then(function(r){
      if(timer)clearTimeout(timer);
      if(!r.ok)return r.json().catch(function(){return{}}).then(function(body){throw new Error(body.error||r.status+' '+r.statusText)});
      return r.json();
    }).catch(function(e){
      if(timer)clearTimeout(timer);
      if(e&&e.name==='AbortError')throw new Error('请求超时 ('+Math.round(timeoutMs/1000)+'s)');
      throw e;
    });
  }
  return doFetch().catch(function(e){
    if(e instanceof TypeError&&/fetch/i.test(e.message))return doFetch();
    throw e;
  }).catch(function(e){toast('❌ '+e.message);throw e});
}
function kpi(v,l,c){return '<div class="kpi"><div class="kpi-val" style="color:'+(c||'var(--text)')+'">'+v+'</div><div class="kpi-label">'+l+'</div></div>'}
function pctRaw(v){return v!=null?v.toFixed(2)+'%':'-'}

// ── State ──
var currentStep=1;
var globalRanking=[];     // from step 1
var globalBatchId='';     // batch_id from step 1
var selectedStrats=[];    // from step 2
var comboResult=null;     // from step 2 combine
var portfolioReturns=null;
var s1Timer=null;
var s2Timer=null;

// ── Stepper ──
function goStep(n){
  document.querySelectorAll('.step').forEach(function(el,i){
    el.classList.remove('active');
    if(i+1<n) el.classList.add('done');
  });
  document.getElementById('step-'+n).classList.add('active');
  document.querySelectorAll('.step-panel').forEach(function(el){el.classList.remove('active')});
  document.getElementById('panel-'+n).classList.add('active');
  currentStep=n;
}

// ═══════════════════════════════════════════════════════════
// STEP 1: Strategy Fitness
// ═══════════════════════════════════════════════════════════
function step1Run(){
  var btn=document.getElementById('s1-run-btn');
  btn.disabled=true;btn.textContent='⏳ 运行中...';
  document.getElementById('s1-stop-btn').disabled=false;
  document.getElementById('s1-progress').style.display='block';
  document.getElementById('s1-results').style.display='none';

  // Start polling immediately (the /run API blocks until done)
  s1Timer=setInterval(step1CheckStatus,3000);

  api('/quant/admin/api/strategy-fitness/run',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      days:parseInt(document.getElementById('s1-days').value)||250,
      initialCash:parseFloat(document.getElementById('s1-cash').value)||100000
    }),
    _timeout:4*3600000
  }).then(function(d){
    if(s1Timer){clearInterval(s1Timer);s1Timer=null}
    btn.disabled=false;btn.textContent='🚀 运行分析';
    document.getElementById('s1-stop-btn').disabled=true;
    toast('✅ 分析完成');
    step1LoadResults();
  }).catch(function(e){
    if(s1Timer){clearInterval(s1Timer);s1Timer=null}
    btn.disabled=false;btn.textContent='🚀 运行分析';
    document.getElementById('s1-stop-btn').disabled=true;
    toast('❌ '+e.message);
  });
}

function step1Stop(){
  api('/quant/admin/api/strategy-fitness/stop',{method:'POST',_timeout:10000}).then(function(){
    toast('已停止分析');
    if(s1Timer)clearInterval(s1Timer);
    var btn=document.getElementById('s1-run-btn');
    btn.disabled=false;btn.textContent='🚀 运行分析';
    document.getElementById('s1-stop-btn').disabled=true;
  });
}

function step1CheckStatus(){
  api('/quant/admin/api/strategy-fitness/status',{_timeout:10000}).then(function(d){
    if(!d)return;
    var pct=d.progress||0;
    document.getElementById('s1-progress-fill').style.width=pct+'%';
    document.getElementById('s1-progress-text').textContent=
      (d.detail||'运行中...')+' ('+Math.round(pct)+'%)';
    if(d.status==='done'||d.status==='completed'){
      clearInterval(s1Timer);
      document.getElementById('s1-run-btn').disabled=false;
      document.getElementById('s1-run-btn').textContent='🚀 运行分析';
      document.getElementById('s1-stop-btn').disabled=true;
      step1LoadResults();
    }
  }).catch(function(){});
}

function step1Load(){
  step1LoadResults();
}

function step1LoadResults(){
  api('/quant/admin/api/strategy-fitness/global').then(function(d){
    if(!d||!d.strategies){toast('暂无排名数据');return}
    globalBatchId=d.batch_id||'';
    globalRanking=d.strategies.sort(function(a,b){return(b.total_score||0)-(a.total_score||0)});
    renderStep1Results();
    toast('✅ 已加载全局排名 ('+globalRanking.length+' 个策略)');
  });
}

function renderStep1Results(){
  document.getElementById('s1-results').style.display='block';
  document.getElementById('s1-progress').style.display='none';
  var top=globalRanking.slice(0,3);
  document.getElementById('s1-kpis').innerHTML=
    kpi(globalRanking.length,'策略总数','var(--accent)')+
    kpi(top[0]?esc(top[0].strategy_id):'—','最佳策略','var(--green)')+
    kpi(top[0]?top[0].total_score.toFixed(1):'—','最高评分','var(--yellow)');

  var html='';
  globalRanking.forEach(function(s,i){
    html+='<tr><td>'+(s.rank||i+1)+'</td><td><strong>'+esc(s.strategy_id)+'</strong></td>'
      +'<td style="color:var(--yellow)">'+num(s.total_score)+'</td>'
      +'<td>'+numInt(s.stock_count)+'</td>'
      +'<td>'+num(s.avg_score)+'</td>'
      +'<td style="color:var(--green)">'+num(s.max_score)+'</td>'
      +'<td style="color:var(--red)">'+num(s.min_score)+'</td></tr>';
  });
  document.getElementById('s1-table').innerHTML=html;

  // Populate step 2 strategy list
  renderStep2StratList();
}

function step1Done(){
  if(!globalRanking.length){toast('请先完成策略分析');return}
  goStep(2);
}

// ═══════════════════════════════════════════════════════════
// STEP 2: Select + Configure + Combo Backtest
// ═══════════════════════════════════════════════════════════
function renderStep2StratList(){
  var html='';
  globalRanking.forEach(function(s,i){
    var checked=i<5?'checked':''; // top 5 pre-selected
    html+='<label class="strat-item">'
      +'<input type="checkbox" value="'+esc(s.strategy_id)+'" '+checked+' onchange="updateWeightTable()">'
      +'<span class="strat-name">'+esc(s.strategy_id)+'</span>'
      +'<span class="strat-score">总分: '+num(s.total_score)+'</span>'
      +'<span class="strat-sr">覆盖: '+numInt(s.stock_count)+'只</span>'
      +'</label>';
  });
  document.getElementById('s2-strat-list').innerHTML=html;
  updateWeightTable();
}

function getSelectedStrategies(){
  var checks=document.querySelectorAll('#s2-strat-list input[type=checkbox]:checked');
  var ids=[];
  checks.forEach(function(cb){ids.push(cb.value)});
  return ids;
}

function updateWeightTable(){
  var ids=getSelectedStrategies();
  document.getElementById('s2-count').textContent=ids.length;
  var method=document.getElementById('s2-method').value;
  var isAuto=(method==='risk_parity'||method==='vol_target');
  var equal=ids.length>0?Math.round(100/ids.length*10)/10:0;
  var html='<table><thead><tr><th>策略</th><th>权重%</th><th>资产类别</th><th>滑点bps</th></tr></thead><tbody>';
  ids.forEach(function(id){
    var slippage=document.getElementById('s2-slippage').value||'10';
    html+='<tr><td>'+esc(id)+'</td>'
      +'<td>'+(isAuto
        ?'<span style="color:var(--accent);font-size:12px">🔄 自动</span><input type="hidden" class="wt-input" data-id="'+esc(id)+'" value="'+equal+'">'
        :'<input type="number" class="wt-input" data-id="'+esc(id)+'" value="'+equal+'" style="width:70px" step="0.1">')
      +'</td>'
      +'<td><select class="ac-input" data-id="'+esc(id)+'" style="width:100px">'
      +'<option value="equity">A股</option><option value="cb">可转债</option><option value="etf">ETF</option><option value="commodity">商品</option>'
      +'</select></td>'
      +'<td><input type="number" class="sl-input" data-id="'+esc(id)+'" value="'+slippage+'" style="width:60px"></td></tr>';
  });
  html+='</tbody></table>';
  if(isAuto)html+='<div style="margin-top:6px;font-size:12px;color:var(--accent)">⚡ '+
    (method==='risk_parity'?'风险平价':'波动率目标制')+' 模式下权重由系统根据各策略波动率自动计算</div>';
  document.getElementById('s2-weight-table').innerHTML=html;
}

function step2CorrPreview(){
  var ids=getSelectedStrategies();
  if(ids.length<2){toast('至少选择2个策略');return}
  var btn=document.getElementById('s2-corr-btn');
  btn.disabled=true;btn.textContent='⏳ 计算中...';

  api('/quant/admin/api/strategy-fitness/correlation-preview',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({strategy_ids:ids,batch_id:globalBatchId}),
    _timeout:15000
  }).then(function(d){
    btn.disabled=false;btn.textContent='📊 预检相关性';
    if(d.error){toast('❌ '+d.error);return}

    var verdict=d.verdict;
    var verdictColor=verdict==='LOW'?'var(--green)':verdict==='MODERATE'?'var(--yellow)':'var(--red)';
    var verdictText=verdict==='LOW'?'✅ 低相关 — 分散效果好':verdict==='MODERATE'?'⚠️ 中等相关 — 可接受':'❌ 高相关 — 分散效果差';

    var html='<div class="card" style="padding:12px">';
    // Verdict badge
    html+='<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">';
    html+='<span style="font-size:16px;font-weight:700;color:'+verdictColor+'">'+verdictText+'</span>';
    html+='<span style="font-size:12px;color:var(--text-dim)">平均|相关|: '+d.avg_correlation.toFixed(3)+' | 共'+d.n_common_symbols+'只股票</span>';
    html+='</div>';

    // Correlation matrix
    var names=ids;
    html+='<table style="font-size:12px"><thead><tr><th></th>';
    names.forEach(function(n){html+='<th style="max-width:80px;overflow:hidden;text-overflow:ellipsis">'+esc(n.substring(0,12))+'</th>'});
    html+='</tr></thead><tbody>';
    names.forEach(function(r){
      html+='<tr><td><strong>'+esc(r.substring(0,12))+'</strong></td>';
      names.forEach(function(c){
        var v=d.matrix[r]&&d.matrix[r][c]!=null?d.matrix[r][c]:0;
        var abs=Math.abs(v);
        var bg=r===c?'rgba(88,166,255,.2)':abs>0.5?'rgba(248,81,73,.25)':abs>0.3?'rgba(210,153,34,.2)':'rgba(63,185,80,.15)';
        html+='<td style="background:'+bg+';text-align:center;font-weight:'+(abs>0.5?'700':'400')+'">'+v.toFixed(3)+'</td>';
      });
      html+='</tr>';
    });
    html+='</tbody></table>';

    // High correlation warnings
    if(d.high_correlation_pairs&&d.high_correlation_pairs.length>0){
      html+='<div style="margin-top:10px;padding:8px;background:rgba(248,81,73,.1);border-radius:6px;font-size:12px">';
      html+='<strong style="color:var(--red)">⚠️ 高相关配对:</strong><br>';
      d.high_correlation_pairs.forEach(function(p){
        html+='<span style="color:var(--red)">'+esc(p.a)+' ↔ '+esc(p.b)+' = '+p.corr.toFixed(3)+'</span><br>';
      });
      html+='<span style="color:var(--text-dim)">建议替换其中一个，降低组合整体相关性</span>';
      html+='</div>';
    }

    // Recommended alternatives
    if(d.alternatives&&d.alternatives.length>0){
      html+='<div style="margin-top:10px;padding:8px;background:rgba(63,185,80,.1);border-radius:6px;font-size:12px">';
      html+='<strong style="color:var(--green)">💡 推荐替换策略（低相关）:</strong><br>';
      d.alternatives.forEach(function(alt){
        html+='<span>• '+esc(alt)+'</span><br>';
      });
      html+='</div>';
    }

    html+='<div style="margin-top:8px;padding:8px;background:rgba(88,166,255,.08);border-radius:6px;font-size:11px;color:var(--text-dim)">'
      +'⚠️ 此相关性基于选股<strong>评分向量</strong>计算。实际回测中日收益相关性通常更高（受市场 β 影响）。</div>';

    html+='</div>';
    document.getElementById('s2-corr-result').innerHTML=html;
    toast('相关性预检完成');
  }).catch(function(e){
    btn.disabled=false;btn.textContent='📊 预检相关性';
  });
}

function step2AutoSelect(){
  if(!globalRanking.length){toast('请先完成第一步');return}
  var count=parseInt(document.getElementById('s2-auto-count').value)||5;
  var btn=document.getElementById('s2-auto-btn');
  btn.disabled=true;btn.textContent='⏳ 优选中...';

  api('/quant/admin/api/strategy-fitness/auto-select',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({batch_id:globalBatchId,count:count,max_avg_corr:0.4}),
    _timeout:15000
  }).then(function(d){
    btn.disabled=false;btn.textContent='🤖 自动优选低相关组合';
    if(d.error){toast('❌ '+d.error);return}

    // Update checkboxes
    var selected=d.selected||[];
    document.querySelectorAll('#s2-strat-list input[type=checkbox]').forEach(function(cb){
      cb.checked=selected.indexOf(cb.value)>=0;
    });
    updateWeightTable();

    // Show result in corr preview area
    var verdict=d.verdict;
    var verdictColor=verdict==='LOW'?'var(--green)':verdict==='MODERATE'?'var(--yellow)':'var(--red)';
    var verdictLabel=verdict==='LOW'?'✅ 低相关':'⚠️ 中等相关';

    var html='<div class="card" style="padding:12px">';
    html+='<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">';
    html+='<span style="font-size:15px;font-weight:700;color:var(--green)">🤖 已自动选择 '+selected.length+' 个策略</span>';
    html+='<span style="font-size:13px;font-weight:600;color:'+verdictColor+'">'+verdictLabel+' (平均|相关|: '+d.avg_correlation.toFixed(3)+')</span>';
    html+='</div>';

    // Strategy info
    html+='<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:12px">';
    (d.strategy_info||[]).forEach(function(s){
      html+='<span style="background:var(--bg3);padding:4px 10px;border-radius:12px;font-size:12px">'
        +'<strong>'+esc(s.strategy_id)+'</strong> #'+s.rank+' ('+s.total_score.toFixed(0)+'分)</span>';
    });
    html+='</div>';

    // Correlation matrix
    var names=selected;
    html+='<table style="font-size:12px"><thead><tr><th></th>';
    names.forEach(function(n){html+='<th style="max-width:80px;overflow:hidden;text-overflow:ellipsis">'+esc(n.substring(0,12))+'</th>'});
    html+='</tr></thead><tbody>';
    names.forEach(function(r){
      html+='<tr><td><strong>'+esc(r.substring(0,12))+'</strong></td>';
      names.forEach(function(c){
        var v=d.matrix[r]&&d.matrix[r][c]!=null?d.matrix[r][c]:0;
        var abs=Math.abs(v);
        var bg=r===c?'rgba(88,166,255,.2)':abs>0.5?'rgba(248,81,73,.25)':abs>0.3?'rgba(210,153,34,.2)':'rgba(63,185,80,.15)';
        html+='<td style="background:'+bg+';text-align:center">'+v.toFixed(3)+'</td>';
      });
      html+='</tr>';
    });
    html+='</tbody></table>';
    html+='<div style="margin-top:8px;padding:8px;background:rgba(88,166,255,.08);border-radius:6px;font-size:11px;color:var(--text-dim)">'
      +'⚠️ 说明：此相关性基于策略<strong>选股评分</strong>向量计算，实际回测中的日收益相关性可能更高（因市场整体涨跌影响）。'
      +'<br>如需真正低相关，建议混合不同资产类别（ETF、可转债、商品）。</div>';
    html+='</div>';
    document.getElementById('s2-corr-result').innerHTML=html;

    // Auto-switch to risk_parity to avoid bad strategies dragging equally
    document.getElementById('s2-method').value='risk_parity';
    updateWeightTable();

    toast('✅ 已自动选择 '+selected.length+' 个低相关策略，已切换为风险平价模式');
  }).catch(function(e){
    btn.disabled=false;btn.textContent='🤖 自动优选低相关组合';
  });
}

function collectStep2Config(){
  var ids=getSelectedStrategies();
  if(ids.length<2){toast('至少选择2个策略');return null}
  var strategies=[];
  ids.forEach(function(id){
    var wt=parseFloat(document.querySelector('.wt-input[data-id="'+id+'"]').value)||0;
    var ac=document.querySelector('.ac-input[data-id="'+id+'"]').value;
    var sl=parseFloat(document.querySelector('.sl-input[data-id="'+id+'"]').value)||10;
    strategies.push({name:id,weight:wt,asset_class:ac,slippage_bps:sl});
  });
  return{
    strategies:strategies,
    method:document.getElementById('s2-method').value,
    rebalance_freq:document.getElementById('s2-rebalance').value,
    initial_cash:parseFloat(document.getElementById('s2-cash').value)||10000000,
    portfolio_vol_target:(parseFloat(document.getElementById('s2-vol').value)||15)/100,
    risk_free_rate:(parseFloat(document.getElementById('s2-rf').value)||3)/100,
    days:parseInt(document.getElementById('s2-days').value)||1000,
    slippage:parseInt(document.getElementById('s2-slippage').value)||10
  };
}

function step2Run(){
  var cfg=collectStep2Config();
  if(!cfg)return;
  selectedStrats=cfg.strategies;

  var btn=document.getElementById('s2-run-btn');
  btn.disabled=true;btn.textContent='⏳ 运行组合回测中...';
  document.getElementById('s2-progress').style.display='block';
  document.getElementById('s2-progress-text').textContent='正在启动回测...';

  // Map method names to allocation_mode for backend
  var modeMap={fixed:'equal',risk_parity:'risk_parity',vol_target:'target_vol'};
  var allocMode=modeMap[cfg.method]||'equal';

  // Custom weights for fixed mode
  var customWeights=null;
  if(cfg.method==='fixed'){
    customWeights=cfg.strategies.map(function(s){return s.weight});
  }

  // Start polling for progress display immediately
  s2Timer=setInterval(step2CheckProgress,2000);

  // Run portfolio backtest via fitness API (blocking — returns full result)
  api('/quant/admin/api/strategy-fitness/portfolio-backtest',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({
      batch_id:globalBatchId,
      top_n:cfg.strategies.length,
      strategy_ids:cfg.strategies.map(function(s){return s.name}),
      allocation_mode:allocMode,
      rebalance_freq:cfg.rebalance_freq,
      period_days:cfg.days,
      initial_capital:cfg.initial_cash,
      custom_weights:customWeights
    }),
    _timeout:10*60*1000
  }).then(function(d){
    if(s2Timer){clearInterval(s2Timer);s2Timer=null}
    btn.disabled=false;btn.textContent='🚀 运行组合回测';
    document.getElementById('s2-progress').style.display='none';
    if(d.error){toast('❌ '+d.error);return}
    if(d.ok){
      step2HandleRealResult(d,cfg);
    }else{
      toast('❌ 回测失败: '+(d.error||'未知错误'));
    }
  }).catch(function(e){
    if(s2Timer){clearInterval(s2Timer);s2Timer=null}
    btn.disabled=false;btn.textContent='🚀 运行组合回测';
    document.getElementById('s2-progress').style.display='none';
    toast('❌ '+e.message);
  });
}

function step2CheckProgress(){
  api('/quant/admin/api/strategy-fitness/portfolio-backtest/status',{_timeout:10000}).then(function(d){
    if(!d||d.status==='idle')return;
    var total=d.total||1;
    var completed=d.completed||0;
    var pct=Math.round(completed/total*100);
    document.getElementById('s2-progress-fill').style.width=pct+'%';
    var text=d.phase||'回测中...';
    if(d.elapsed_seconds)text+=' ('+d.elapsed_seconds+'s)';
    document.getElementById('s2-progress-text').textContent=text+' '+pct+'%';
  }).catch(function(){});
}

function step2HandleRealResult(d,cfg){
  // Transform multi_strategy_backtest.py response to renderStep3 format
  var cm=d.combined_metrics||{};

  // Build portfolio_metrics (values as ratios, not percentages)
  var pm={
    annualized_return:(cm.annualized_return||0)/100,
    max_drawdown:(cm.max_drawdown||0)/100,
    sharpe_ratio:cm.sharpe_ratio||0,
    calmar_ratio:cm.calmar_ratio||0,
    volatility:0,
    cvar_95:0
  };

  // Compute volatility and CVaR from equity curve
  var curve=d.equity_curve||[];
  if(curve.length>2){
    var dailyRets=[];
    for(var i=1;i<curve.length;i++){
      if(curve[i-1].value>0) dailyRets.push(curve[i].value/curve[i-1].value-1);
    }
    if(dailyRets.length>5){
      var mean=dailyRets.reduce(function(a,b){return a+b},0)/dailyRets.length;
      var variance=dailyRets.reduce(function(a,b){return a+(b-mean)*(b-mean)},0)/dailyRets.length;
      pm.volatility=Math.sqrt(variance*252);
      // CVaR 95%: average of worst 5%
      var sorted=dailyRets.slice().sort(function(a,b){return a-b});
      var n5=Math.max(1,Math.floor(sorted.length*0.05));
      var worst=sorted.slice(0,n5);
      pm.cvar_95=worst.reduce(function(a,b){return a+b},0)/worst.length;
    }
  }

  // Build sub_strategy_metrics and final_weights from sub_results
  var subMetrics={},finalWeights={};
  var subEquityCurves=d.sub_equity_curves||{};
  (d.sub_results||[]).forEach(function(s){
    subMetrics[s.name]={
      annualized_return:(s.annual_return||0)/100,
      max_drawdown:(s.max_drawdown||0)/100,
      sharpe_ratio:s.sharpe||0,
      volatility:0
    };
    finalWeights[s.name]=s.weight||0;
    // Compute per-strategy volatility from sub equity curve
    var sc=subEquityCurves[s.name]||[];
    if(sc.length>5){
      var sr=[];
      for(var j=1;j<sc.length;j++){
        if(sc[j-1].value>0) sr.push(sc[j].value/sc[j-1].value-1);
      }
      if(sr.length>5){
        var sm=sr.reduce(function(a,b){return a+b},0)/sr.length;
        var sv=sr.reduce(function(a,b){return a+(b-sm)*(b-sm)},0)/sr.length;
        subMetrics[s.name].volatility=Math.sqrt(sv*252);
      }
    }
  });

  // Build correlation in expected format
  var corrData={};
  if(d.correlation_matrix){
    var corrMatrix=d.correlation_matrix;
    var corrNames=Object.keys(corrMatrix);
    var corrVals=[];
    corrNames.forEach(function(r){
      corrNames.forEach(function(c){
        if(r!==c) corrVals.push(Math.abs(corrMatrix[r][c]||0));
      });
    });
    var avgCorr=corrVals.length>0?(corrVals.reduce(function(a,b){return a+b},0)/corrVals.length):0;
    var maxCorr=corrVals.length>0?Math.max.apply(null,corrVals):0;
    corrData={matrix:corrMatrix,average:avgCorr.toFixed(3),max_pairwise:maxCorr.toFixed(3)};
  }

  // Compute portfolio_returns (daily returns) for statistical validation
  var portReturns=[];
  if(curve.length>2){
    for(var k=1;k<curve.length;k++){
      if(curve[k-1].value>0) portReturns.push(curve[k].value/curve[k-1].value-1);
    }
  }

  // Assemble the result object compatible with renderStep3
  var result={
    portfolio_metrics:pm,
    equity_curve:curve,
    sub_strategy_metrics:subMetrics,
    final_weights:finalWeights,
    correlation:corrData,
    portfolio_returns:portReturns
  };

  comboResult=result;
  comboResult._equity_curves=subEquityCurves;
  comboResult._cfg=cfg;
  portfolioReturns=portReturns;
  renderStep3(result);
  goStep(3);
  toast('✅ 组合回测完成 (真实数据)');
}

function step2Combine(equityCurves){
  var cfg=collectStep2Config();
  if(!cfg)return;

  var payload={
    action:'combine',
    strategies:cfg.strategies,
    equity_curves:equityCurves,
    method:cfg.method,
    rebalance_freq:cfg.rebalance_freq,
    initial_cash:cfg.initial_cash,
    portfolio_vol_target:cfg.portfolio_vol_target,
    risk_free_rate:cfg.risk_free_rate
  };

  document.getElementById('s2-run-btn').disabled=true;
  document.getElementById('s2-run-btn').textContent='⏳ 构建中...';

  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload),
    _timeout:60000
  }).then(function(d){
    document.getElementById('s2-run-btn').disabled=false;
    document.getElementById('s2-run-btn').textContent='🚀 运行组合回测';
    document.getElementById('s2-progress').style.display='none';
    if(d.error){toast('❌ '+d.error);return}
    comboResult=d;
    comboResult._equity_curves=equityCurves;
    comboResult._cfg=cfg;
    portfolioReturns=d.portfolio_returns||null;
    renderStep3(d);
    goStep(3);
    toast('✅ 组合构建完成');
  }).catch(function(e){
    document.getElementById('s2-run-btn').disabled=false;
    document.getElementById('s2-run-btn').textContent='🚀 运行组合回测';
    toast('❌ '+e.message);
  });
}



// ═══════════════════════════════════════════════════════════
// STEP 3: Performance + Validation + Stress
// ═══════════════════════════════════════════════════════════
function renderStep3(d){
  document.getElementById('s3-perf').style.display='block';
  document.getElementById('s3-val').style.display='block';
  document.getElementById('s3-stress').style.display='block';
  document.getElementById('s3-next-btn').disabled=false;

  var m=d.portfolio_metrics||{};
  // KPIs
  document.getElementById('s3-kpis').innerHTML=
    kpi(pctRaw(m.annualized_return*100),'年化收益',pctColor(m.annualized_return*100))
    +kpi('-'+pctRaw(m.max_drawdown*100),'最大回撤','var(--red)')
    +kpi(num(m.sharpe_ratio),'夏普比率',m.sharpe_ratio>=1?'var(--green)':'var(--yellow)')
    +kpi(num(m.calmar_ratio),'卡尔马比率','var(--text)')
    +kpi(pctRaw(m.volatility*100),'波动率','var(--text)')
    +kpi(pctRaw(m.cvar_95*100),'CVaR(95%)','var(--orange)');

  // Equity chart SVG
  var curve=d.equity_curve||[];
  if(curve.length>0){
    var vals=curve.map(function(p){return p.value});
    var minV=Math.min.apply(null,vals),maxV=Math.max.apply(null,vals);
    var W=800,H=200,pad=40;
    var svg='<svg viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto">';
    svg+='<rect width="'+W+'" height="'+H+'" fill="var(--bg3)" rx="8"/>';
    var pts=[];
    for(var i=0;i<curve.length;i++){
      var x=pad+i/(curve.length-1)*(W-2*pad);
      var y=H-pad-(vals[i]-minV)/(maxV-minV||1)*(H-2*pad);
      pts.push(x.toFixed(1)+','+y.toFixed(1));
    }
    svg+='<polyline points="'+pts.join(' ')+'" fill="none" stroke="var(--accent)" stroke-width="1.5"/>';
    svg+='<text x="'+pad+'" y="'+(H-10)+'" fill="var(--text-dim)" font-size="10">'+curve[0].date+'</text>';
    svg+='<text x="'+(W-pad)+'" y="'+(H-10)+'" fill="var(--text-dim)" font-size="10" text-anchor="end">'+curve[curve.length-1].date+'</text>';
    svg+='</svg>';
    document.getElementById('s3-chart').innerHTML=svg;
  }

  // Sub-strategy table
  var sub=d.sub_strategy_metrics||{};
  var fw=d.final_weights||{};
  var html='';
  Object.keys(sub).forEach(function(k){
    var s=sub[k];
    html+='<tr><td><strong>'+esc(k)+'</strong></td>'
      +'<td>'+(fw[k]!=null?(fw[k]*100).toFixed(1)+'%':'—')+'</td>'
      +'<td style="color:'+pctColor(s.annualized_return*100)+'">'+pctRaw(s.annualized_return*100)+'</td>'
      +'<td style="color:var(--red)">-'+pctRaw(s.max_drawdown*100)+'</td>'
      +'<td>'+num(s.sharpe_ratio)+'</td>'
      +'<td>'+pctRaw(s.volatility*100)+'</td></tr>';
  });
  document.getElementById('s3-sub-table').innerHTML=html;

  // Correlation matrix
  var corr=d.correlation||{};
  if(corr.matrix){
    var names=Object.keys(corr.matrix);
    var chtml='<table><thead><tr><th></th>';
    names.forEach(function(n){chtml+='<th>'+esc(n.substring(0,10))+'</th>'});
    chtml+='</tr></thead><tbody>';
    names.forEach(function(r){
      chtml+='<tr><td><strong>'+esc(r.substring(0,10))+'</strong></td>';
      names.forEach(function(c){
        var v=corr.matrix[r][c];
        var bg=v>0.5?'rgba(248,81,73,.2)':v>0.3?'rgba(210,153,34,.2)':v<0?'rgba(63,185,80,.15)':'rgba(63,185,80,.1)';
        chtml+='<td style="background:'+bg+';text-align:center">'+v.toFixed(3)+'</td>';
      });
      chtml+='</tr>';
    });
    chtml+='</tbody></table>';
    chtml+='<div style="font-size:11px;color:var(--text-dim);margin-top:4px">平均相关: '+corr.average+' | 最大配对相关: '+corr.max_pairwise+'</div>';
    document.getElementById('s3-corr').innerHTML=chtml;
  }
}

function step3Validate(){
  if(!comboResult||!portfolioReturns){toast('请先完成组合构建');return}
  var nTrials=parseInt(document.getElementById('s3-ntrials').value)||8;
  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'validate',portfolio_returns:portfolioReturns,n_trials:nTrials,
      observed_sharpe:comboResult.portfolio_metrics?comboResult.portfolio_metrics.sharpe_ratio:null}),
    _timeout:30000
  }).then(function(d){
    if(d.error){toast('❌ '+d.error);return}
    document.getElementById('s3-val-kpis').innerHTML=
      kpi(d.summary.verdict||'-','综合判定',d.summary.verdict==='STRONG'?'var(--green)':d.summary.verdict==='ACCEPTABLE'?'var(--yellow)':'var(--red)')
      +kpi(d.summary.score||'-','得分','var(--accent)')
      +kpi('p='+num(d.dsr.p_value),'DSR p值',d.dsr.significant?'var(--green)':'var(--red)')
      +kpi(num(d.hlz.t_adjusted),'HLZ t_adj',d.hlz.significant?'var(--green)':'var(--yellow)')
      +kpi(d.pbo.pbo!=null?d.pbo.pbo.toFixed(4):'-','PBO',d.pbo.is_overfitted?'var(--red)':'var(--green)');

    var html='<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:12px">';
    html+='<div class="card"><h4 style="font-size:13px;color:var(--accent)">DSR</h4><p style="font-size:12px">观察SR: '+num(d.dsr.observed_sr)+'<br>Haircut SR: '+num(d.dsr.haircut_sr)+'<br>'+(d.dsr.significant?'<span class="badge badge-green">✅ 通过</span>':'<span class="badge badge-red">❌ 未通过</span>')+'</p></div>';
    html+='<div class="card"><h4 style="font-size:13px;color:var(--accent)">HLZ</h4><p style="font-size:12px">t_obs: '+num(d.hlz.t_observed)+'<br>惩罚: '+num(d.hlz.penalty)+'<br>'+(d.hlz.significant?'<span class="badge badge-green">✅ t_adj≥2.0</span>':'<span class="badge badge-yellow">⚠️ t_adj<2.0</span>')+'</p></div>';
    html+='<div class="card"><h4 style="font-size:13px;color:var(--accent)">PBO</h4><p style="font-size:12px">PBO: '+num(d.pbo.pbo)+'<br>Logit均值: '+num(d.pbo.logit_mean)+'<br>'+(d.pbo.is_overfitted?'<span class="badge badge-red">❌ 过拟合</span>':'<span class="badge badge-green">✅ 未过拟合</span>')+'</p></div>';
    html+='</div>';
    document.getElementById('s3-val-detail').innerHTML=html;
    toast('✅ 统计验证完成');
  });
}

function step3Perturbation(){
  if(!comboResult)return;
  var cfg=comboResult._cfg;
  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'perturbation',strategies:cfg.strategies,
      equity_curves:comboResult._equity_curves,method:cfg.method,rebalance_freq:cfg.rebalance_freq}),
    _timeout:60000
  }).then(function(d){
    if(d.error){toast('❌ '+d.error);return}
    var html='<h4 style="font-size:13px;margin-bottom:8px">🔄 权重扰动结果</h4>';
    html+='<div class="kpi-row">'+kpi(d.all_stable?'✅ 全部稳定':'⚠️ 不稳定',d.all_stable?'权重稳定':'需关注',d.all_stable?'var(--green)':'var(--yellow)')
      +kpi(pctRaw(d.max_sr_change_pct),'最大SR变化','var(--accent)')
      +kpi(num(d.baseline_sharpe),'基线SR','var(--text)')+'</div>';
    html+='<table><thead><tr><th>策略</th><th>方向</th><th>扰动后权重</th><th>SR</th><th>SR变化%</th><th>稳定</th></tr></thead><tbody>';
    (d.results||[]).forEach(function(r){
      html+='<tr><td>'+esc(r.strategy)+'</td><td>'+esc(r.direction)+'</td>'
        +'<td>'+r.perturbed_weight.toFixed(1)+'%</td><td>'+num(r.sharpe_ratio)+'</td>'
        +'<td style="color:'+(r.sr_change_pct>15?'var(--yellow)':'var(--green)')+'">'+r.sr_change_pct.toFixed(2)+'%</td>'
        +'<td>'+(r.stable?'<span class="badge badge-green">✅</span>':'<span class="badge badge-red">❌</span>')+'</td></tr>';
    });
    html+='</tbody></table>';
    document.getElementById('s3-perturb-result').innerHTML=html;
    toast('✅ 权重扰动测试完成');
  });
}

function step3Slippage(){
  if(!comboResult)return;
  var cfg=comboResult._cfg;
  api('/quant/admin/api/portfolio-combo',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({action:'slippage',strategies:cfg.strategies,
      equity_curves:comboResult._equity_curves,method:cfg.method,rebalance_freq:cfg.rebalance_freq}),
    _timeout:60000
  }).then(function(d){
    if(d.error){toast('❌ '+d.error);return}
    var html='<h4 style="font-size:13px;margin-bottom:8px">📉 滑点灵敏度</h4>';
    html+='<div class="kpi-row">'+kpi(d.all_pass?'✅ 全部通过':'⚠️ 存在风险','滑点压力',d.all_pass?'var(--green)':'var(--yellow)')+'</div>';
    html+='<table><thead><tr><th>滑点倍率</th><th>Sharpe</th><th>年化收益</th><th>最大回撤</th><th>SR≥0.8?</th></tr></thead><tbody>';
    (d.results||[]).forEach(function(r){
      html+='<tr><td><strong>'+r.multiplier+'x</strong></td><td>'+num(r.sharpe_ratio)+'</td>'
        +'<td style="color:'+pctColor(r.annualized_return*100)+'">'+pctRaw(r.annualized_return*100)+'</td>'
        +'<td style="color:var(--red)">-'+pctRaw(r.max_drawdown*100)+'</td>'
        +'<td>'+(r.sr_ok?'<span class="badge badge-green">✅</span>':'<span class="badge badge-red">❌</span>')+'</td></tr>';
    });
    html+='</tbody></table>';
    document.getElementById('s3-slippage-result').innerHTML=html;
    toast('✅ 滑点灵敏度测试完成');
  });
}

function step3Done(){
  if(!comboResult){toast('请先完成组合构建');return}
  renderStep4Summary();
  goStep(4);
}

// ═══════════════════════════════════════════════════════════
// STEP 4: Apply
// ═══════════════════════════════════════════════════════════
function renderStep4Summary(){
  if(!comboResult)return;
  var m=comboResult.portfolio_metrics||{};
  document.getElementById('s4-kpis').innerHTML=
    kpi(pctRaw(m.annualized_return*100),'年化收益',pctColor(m.annualized_return*100))
    +kpi(num(m.sharpe_ratio),'夏普比率',m.sharpe_ratio>=1?'var(--green)':'var(--yellow)')
    +kpi('-'+pctRaw(m.max_drawdown*100),'最大回撤','var(--red)')
    +kpi(comboResult._cfg.method,'组合方法','var(--accent)');

  var html='';
  var fw=comboResult.final_weights||{};
  selectedStrats.forEach(function(s){
    html+='<tr><td><strong>'+esc(s.name)+'</strong></td>'
      +'<td>'+(fw[s.name]!=null?(fw[s.name]*100).toFixed(1)+'%':s.weight.toFixed(1)+'%')+'</td>'
      +'<td>'+esc(s.asset_class)+'</td>'
      +'<td>'+s.slippage_bps+'</td></tr>';
  });
  document.getElementById('s4-table').innerHTML=html;
  document.getElementById('s4-cash').value=comboResult._cfg.initial_cash;
}

function step4Apply(){
  var mode=document.getElementById('s4-mode').value;
  if(mode==='dry_run'){
    toast('🔧 仅信号模式：组合配置已记录，不执行下单');
    document.getElementById('s4-result').innerHTML=
      '<div class="info-note" style="border-left-color:var(--green)">✅ 组合配置已保存。在策略路由页面可查看和管理。<br>'
      +'<a href="/quant/admin/strategy-routing">→ 前往策略路由</a></div>';
    return;
  }

  var fw=comboResult.final_weights||{};
  var allocations=Object.keys(fw).map(function(k){ return {strategy_name:k, weight:fw[k]}; });

  var payload={
    allocations:allocations,
    method:comboResult._cfg.method,
    rebalance_freq:comboResult._cfg.rebalance_freq,
    initial_cash:parseFloat(document.getElementById('s4-cash').value)||10000000,
    initial_capital:parseFloat(document.getElementById('s4-cash').value)||10000000
  };

  api('/quant/admin/api/strategy-fitness/portfolio-backtest/apply-paper',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(payload),
    _timeout:30000
  }).then(function(d){
    if(d.error){
      document.getElementById('s4-result').innerHTML='<div class="info-note" style="border-left-color:var(--red)">❌ '+esc(d.error)+'</div>';
      return;
    }
    document.getElementById('s4-result').innerHTML=
      '<div class="info-note" style="border-left-color:var(--green)">✅ 已成功应用到模拟盘！<br>'
      +'资金分配完成，系统将按照策略信号自动执行交易。<br>'
      +(d.message?'<div style="margin-top:6px;font-size:12px;color:var(--text-dim)">'+esc(d.message)+'</div>':'')
      +(d.autoTradeConfigured?'<div style="margin-top:6px;font-size:12px;color:var(--green)">✅ 已自动配置到自动交易调度器（策略: '+allocations.map(function(a){return a.strategy_name}).join(', ')+'）</div>':'')
      +'<div style="margin-top:10px;display:flex;gap:12px;flex-wrap:wrap">'
      +'<a href="/quant/admin/auto-trade" class="btn" style="display:inline-block;padding:6px 14px;background:var(--accent);color:#fff;border-radius:6px;text-decoration:none;font-size:13px">🚀 前往自动交易调度中心</a>'
      +'<a href="/quant/admin/ui" style="font-size:13px;color:var(--text-dim);text-decoration:underline">返回管理面板</a>'
      +'</div></div>';
    toast('✅ 组合已应用到模拟盘');
  }).catch(function(e){
    document.getElementById('s4-result').innerHTML='<div class="info-note" style="border-left-color:var(--red)">❌ '+esc(e.message)+'</div>';
  });
}
</script>
</body>
</html>`;
}
