/**
 * Strategy Fitness Analysis Page — self-contained HTML SPA for
 * strategy-stock fitness analysis, global ranking display, and per-stock queries.
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

export function getStrategyFitnessPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 策略适配分析</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>🎯</text></svg>">
<style>
${THEME_CSS_VARS}
${BASE_CSS_RESET}
${HEADER_CSS}
${TOAST_CSS}
${KPI_CSS}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{margin-bottom:12px;font-size:15px;display:flex;align-items:center;gap:8px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:1fr 1fr}
.grid-3{grid-template-columns:repeat(auto-fill,minmax(280px,1fr))}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--text);transition:all .15s}
.btn:hover{background:var(--border)}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-sm{padding:4px 10px;font-size:12px}
label{display:block;font-size:13px;color:var(--text-dim);margin-bottom:4px}
select,input[type=text],input[type=number]{width:100%;padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px}
.metric-good{color:var(--green)}
.metric-bad{color:var(--red)}
.metric-ok{color:var(--yellow)}
.empty{text-align:center;padding:40px;color:var(--text-dim)}
.form-row{display:flex;gap:12px;align-items:flex-end;margin-bottom:12px;flex-wrap:wrap}
.form-row>div{flex:1;min-width:140px}
.progress-bar{background:var(--bg3);border-radius:6px;height:24px;overflow:hidden;position:relative;margin:12px 0}
.progress-bar .fill{background:var(--accent);height:100%;border-radius:6px;transition:width .5s}
.progress-bar .label{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);font-size:11px;font-weight:600;z-index:1}
.rank-badge{display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;font-size:12px;font-weight:700}
.rank-1{background:var(--green);color:#000}
.rank-2{background:var(--accent);color:#000}
.rank-3{background:var(--yellow);color:#000}
.rank-n{background:var(--bg3);color:var(--text)}
.status-idle{color:var(--text-dim)}
.status-running{color:var(--accent)}
.status-done{color:var(--green)}
.status-error{color:var(--red)}
.score-bar{display:inline-block;height:6px;border-radius:3px;vertical-align:middle;margin-left:6px}
nav{display:flex;gap:4px;background:var(--bg2);padding:0 24px;border-bottom:1px solid var(--border)}
nav button{background:none;border:none;color:var(--text-dim);padding:10px 16px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}
nav button:hover{color:var(--text)}
nav button.active{color:var(--accent);border-bottom-color:var(--accent)}
.tab-content{display:none}
.tab-content.active{display:block}
@media(max-width:768px){.grid-2{grid-template-columns:1fr}.form-row{flex-direction:column}}
</style>
</head>
<body>
<header>
  <h1>🎯 策略适配分析</h1>
  <div class="header-right">
    <span id="statusBadge" class="status-idle">● 空闲</span>
    <a href="/quant/admin/ui">← 返回管理面板</a>
  </div>
</header>
<nav>
  <button class="active" onclick="switchTab('run')">🚀 运行分析</button>
  <button onclick="switchTab('global')">🏆 全局排名</button>
  <button onclick="switchTab('stock')">📊 个股查询</button>
  <button onclick="switchTab('history')">📋 历史记录</button>
</nav>
<div class="container">

<!-- Tab 1: Run Analysis -->
<div id="tab-run" class="tab-content active">
  <div class="card">
    <h3>⚙️ 运行参数</h3>
    <div class="form-row">
      <div>
        <label>股票范围</label>
        <select id="universe">
          <option value="csi300">沪深300 (~300只)</option>
          <option value="csi500">中证500 (~500只)</option>
          <option value="csi1000" selected>中证1000 (~1000只)</option>
        </select>
      </div>
      <div>
        <label>回测年数</label>
        <select id="backtestYears">
          <option value="1">1年</option>
          <option value="2">2年</option>
          <option value="3">3年</option>
          <option value="5" selected>5年</option>
        </select>
      </div>
      <div>
        <label>每股 Top N</label>
        <input type="number" id="topNPerStock" value="5" min="3" max="10">
      </div>
      <div>
        <label>全局 Top N</label>
        <input type="number" id="topNGlobal" value="10" min="5" max="30">
      </div>
      <div>
        <label>并行数</label>
        <input type="number" id="workers" value="8" min="1" max="16">
      </div>
      <div>
        <label>初始资金</label>
        <input type="number" id="initialCapital" value="1000000" min="100000" step="100000">
      </div>
    </div>
    <div style="display:flex;gap:12px;align-items:center">
      <button class="btn btn-primary" id="btnRun" onclick="startAnalysis()">🚀 开始分析</button>
      <button class="btn" id="btnRefresh" onclick="checkStatus()">🔄 刷新状态</button>
      <span id="runStatus" style="font-size:13px;color:var(--text-dim)"></span>
    </div>
  </div>

  <div id="progressSection" style="display:none">
    <div class="card">
      <h3>📊 运行进度</h3>
      <div class="progress-bar">
        <div class="fill" id="progressFill" style="width:0%"></div>
        <div class="label" id="progressLabel">0%</div>
      </div>
      <div id="progressDetail" style="font-size:13px;color:var(--text-dim);margin-top:8px"></div>
    </div>
  </div>

  <div id="resultSection" style="display:none">
    <div class="card">
      <h3>✅ 运行结果</h3>
      <div class="grid grid-3" id="resultKpis"></div>
      <div id="resultPreview" style="margin-top:16px"></div>
    </div>
  </div>
</div>

<!-- Tab 2: Global Ranking -->
<div id="tab-global" class="tab-content">
  <div class="card">
    <h3>🏆 全局最优策略 Top 10</h3>
    <p style="font-size:13px;color:var(--text-dim);margin-bottom:12px">所有股票中综合得分最高的策略排名 — 得分为各股票中该策略composite_score的累加</p>
    <button class="btn btn-sm" onclick="loadGlobalRanking()" style="margin-bottom:12px">🔄 刷新</button>
    <div id="globalRankingTable"><div class="empty">点击刷新加载数据</div></div>
  </div>
</div>

<!-- Tab 3: Stock Query -->
<div id="tab-stock" class="tab-content">
  <div class="card">
    <h3>📊 个股最优策略查询</h3>
    <div class="form-row">
      <div style="flex:2">
        <label>股票代码</label>
        <input type="text" id="querySymbol" placeholder="例如: 600519" maxlength="6">
      </div>
      <div>
        <button class="btn btn-primary" onclick="queryStock()" style="margin-top:18px">🔍 查询</button>
      </div>
    </div>
    <div id="stockResult" style="margin-top:16px"><div class="empty">输入股票代码后点击查询</div></div>
  </div>
</div>

<!-- Tab 4: History -->
<div id="tab-history" class="tab-content">
  <div class="card">
    <h3>📋 分析历史</h3>
    <button class="btn btn-sm" onclick="loadHistory()" style="margin-bottom:12px">🔄 刷新</button>
    <div id="historyTable"><div class="empty">点击刷新加载</div></div>
  </div>
</div>

</div>
<div class="toast" id="toast"></div>
<script>
var B='${b}';
var TOKEN='${apiToken ?? ""}';
${SHARED_JS_HELPERS}
${SHARED_JS_API}

// ── Tab switching ─────────────────────────────────────
function switchTab(id){
  document.querySelectorAll('.tab-content').forEach(function(el){el.classList.remove('active')});
  document.querySelectorAll('nav button').forEach(function(el){el.classList.remove('active')});
  var tab=document.getElementById('tab-'+id);
  if(tab)tab.classList.add('active');
  // Highlight nav button
  var btns=document.querySelectorAll('nav button');
  var map={run:0,global:1,stock:2,history:3};
  if(map[id]!==undefined&&btns[map[id]])btns[map[id]].classList.add('active');
  // Auto-load data
  if(id==='global')loadGlobalRanking();
  if(id==='history')loadHistory();
}

// ── Start Analysis ────────────────────────────────────
var _pollTimer=null;
function startAnalysis(){
  var btn=document.getElementById('btnRun');
  btn.disabled=true;
  btn.textContent='⏳ 启动中...';
  document.getElementById('runStatus').textContent='正在提交...';
  document.getElementById('progressSection').style.display='block';
  document.getElementById('resultSection').style.display='none';

  var body={
    universe:document.getElementById('universe').value,
    backtest_years:parseInt(document.getElementById('backtestYears').value)||5,
    top_n_per_stock:parseInt(document.getElementById('topNPerStock').value)||5,
    top_n_global:parseInt(document.getElementById('topNGlobal').value)||10,
    workers:parseInt(document.getElementById('workers').value)||8,
    initial_capital:parseInt(document.getElementById('initialCapital').value)||1000000,
  };

  api('/quant/admin/api/strategy-fitness/run',{
    method:'POST',
    body:JSON.stringify(body),
    _timeout:4*3600000
  }).then(function(data){
    btn.disabled=false;
    btn.textContent='🚀 开始分析';
    if(data.ok||data.batch_id){
      toast('✅ 分析完成');
      showResult(data);
      updateProgress(100,'done');
      document.getElementById('runStatus').textContent='已完成 ('+data.batch_id+')';
    }else{
      toast('❌ '+(data.error||'分析失败'));
      document.getElementById('runStatus').textContent='失败: '+(data.error||'');
    }
    if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
  }).catch(function(e){
    btn.disabled=false;
    btn.textContent='🚀 开始分析';
    document.getElementById('runStatus').textContent='错误: '+e.message;
  });

  // Poll progress
  _pollTimer=setInterval(checkStatus,5000);
}

function checkStatus(){
  api('/quant/admin/api/strategy-fitness/status',{_timeout:10000}).then(function(data){
    if(!data)return;
    var badge=document.getElementById('statusBadge');
    if(data.status==='starting'||data.status==='loading_klines'||data.status==='loading_fundamentals'||data.status==='building_bars'){
      badge.className='status-running';
      badge.textContent='● 准备中';
      document.getElementById('progressSection').style.display='block';
      var detail2=data.phase||'正在准备...';
      if(data.elapsed_seconds)detail2+=' | 耗时: '+formatDuration(data.elapsed_seconds);
      updateProgress(0,'running');
      document.getElementById('progressDetail').textContent=detail2;
    }else if(data.status==='running'){
      badge.className='status-running';
      badge.textContent='● 运行中';
      document.getElementById('progressSection').style.display='block';
      var total=data.total_strategies||data.total_stocks||1;
      var done=data.completed_strategies||data.completed_stocks||0;
      var pct=Math.round(done/total*100);
      updateProgress(pct,'running');
      var detail=data.phase||(data.completed_strategies?'策略: '+data.completed_strategies+'/'+data.total_strategies:'已完成: '+data.completed_stocks+'/'+data.total_stocks+' 只股票');
      if(data.elapsed_seconds)detail+=' | 耗时: '+formatDuration(data.elapsed_seconds);
      if(data.estimated_remaining)detail+=' | 预计剩余: '+formatDuration(data.estimated_remaining);
      document.getElementById('progressDetail').textContent=detail;
    }else if(data.status==='done'){
      badge.className='status-done';
      badge.textContent='● 已完成';
      updateProgress(100,'done');
      if(data.results){
        document.getElementById('progressDetail').textContent=
          '完成: '+data.completed_stocks+' 只股票, '+
          (data.results.fitness_records||0)+' 条记录, '+
          '耗时 '+formatDuration(data.elapsed_seconds);
      }
      if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
    }else if(data.status==='error'){
      badge.className='status-error';
      badge.textContent='● 错误';
      if(_pollTimer){clearInterval(_pollTimer);_pollTimer=null}
    }else{
      badge.className='status-idle';
      badge.textContent='● 空闲';
    }
  }).catch(function(){});
}

function updateProgress(pct,status){
  var fill=document.getElementById('progressFill');
  var label=document.getElementById('progressLabel');
  fill.style.width=pct+'%';
  label.textContent=pct+'%';
  if(status==='done')fill.style.background='var(--green)';
  else fill.style.background='var(--accent)';
}

function formatDuration(sec){
  if(!sec||sec<0)return '-';
  var h=Math.floor(sec/3600);
  var m=Math.floor((sec%3600)/60);
  var s=sec%60;
  if(h>0)return h+'时'+m+'分';
  if(m>0)return m+'分'+s+'秒';
  return s+'秒';
}

function showResult(data){
  document.getElementById('resultSection').style.display='block';
  var kpis='';
  kpis+=kpiHtml(data.total_stocks||'-','股票数');
  kpis+=kpiHtml(data.total_strategies||'-','策略数');
  kpis+=kpiHtml(data.fitness_records||'-','适配记录');
  kpis+=kpiHtml(formatDuration(data.elapsed_seconds),'总耗时');
  kpis+=kpiHtml(data.batch_id||'-','批次ID');
  document.getElementById('resultKpis').innerHTML=kpis;

  // Show global top strategies preview
  var strats=data.global_top_strategies||[];
  if(strats.length>0){
    var html='<h4 style="margin-bottom:8px;font-size:14px">🏆 全局最优策略预览</h4>';
    html+='<table><tr><th>排名</th><th>策略</th><th>总得分</th><th>覆盖股票数</th><th>平均得分</th></tr>';
    strats.forEach(function(s){
      html+='<tr><td>'+rankBadge(s.rank)+'</td><td>'+esc(s.strategy_id)+'</td>';
      html+='<td class="metric-good">'+num(s.total_score)+'</td>';
      html+='<td>'+s.stock_count+'</td><td>'+num(s.avg_score)+'</td></tr>';
    });
    html+='</table>';
    document.getElementById('resultPreview').innerHTML=html;
  }
}

// ── Global Ranking ────────────────────────────────────
function loadGlobalRanking(){
  document.getElementById('globalRankingTable').innerHTML='<div class="loading">加载中...</div>';
  api('/quant/admin/api/strategy-fitness/global',{_timeout:15000}).then(function(data){
    var strats=data.strategies||[];
    if(!strats.length){
      document.getElementById('globalRankingTable').innerHTML='<div class="empty">暂无数据，请先运行分析</div>';
      return;
    }
    var maxScore=Math.max.apply(null,strats.map(function(s){return s.total_score||0}))||1;
    var html='<table><tr><th>排名</th><th>策略ID</th><th>策略名称</th><th>总得分</th><th>覆盖股票</th><th>平均得分</th><th>最高</th><th>最低</th><th>分布</th></tr>';
    strats.forEach(function(s){
      var barW=Math.round((s.total_score/maxScore)*100);
      html+='<tr><td>'+rankBadge(s.rank)+'</td>';
      html+='<td><strong>'+esc(s.strategy_id)+'</strong></td>';
      html+='<td>'+esc(s.strategy_name||'-')+'</td>';
      html+='<td class="metric-good" style="font-weight:700">'+num(s.total_score)+'</td>';
      html+='<td>'+s.stock_count+'</td>';
      html+='<td>'+num(s.avg_score)+'</td>';
      html+='<td>'+num(s.max_score)+'</td>';
      html+='<td>'+num(s.min_score)+'</td>';
      html+='<td><span class="score-bar" style="width:'+barW+'px;background:var(--accent)"></span></td>';
      html+='</tr>';
    });
    html+='</table>';
    if(data.batch_id)html+='<div style="font-size:12px;color:var(--text-dim);margin-top:8px">批次: '+esc(data.batch_id)+'</div>';
    document.getElementById('globalRankingTable').innerHTML=html;
  }).catch(function(){
    document.getElementById('globalRankingTable').innerHTML='<div class="empty">加载失败</div>';
  });
}

// ── Stock Query ──────────────────────────────────────
function queryStock(){
  var sym=document.getElementById('querySymbol').value.trim();
  if(!sym||sym.length<6){toast('请输入6位股票代码');return}
  document.getElementById('stockResult').innerHTML='<div class="loading">查询中...</div>';
  api('/quant/admin/api/strategy-fitness/stock?symbol='+encodeURIComponent(sym),{_timeout:15000}).then(function(data){
    var strats=data.strategies||[];
    if(!strats.length){
      document.getElementById('stockResult').innerHTML='<div class="empty">该股票无适配数据 ('+esc(sym)+')</div>';
      return;
    }
    var html='<h4 style="margin-bottom:8px">'+esc(sym)+' 最优策略 (Top '+strats.length+')</h4>';
    html+='<table><tr><th>排名</th><th>策略ID</th><th>综合得分</th><th>总收益</th><th>年化收益</th><th>夏普</th><th>最大回撤</th><th>胜率</th><th>盈亏比</th><th>交易数</th></tr>';
    strats.forEach(function(s){
      html+='<tr><td>'+rankBadge(s.rank)+'</td>';
      html+='<td><strong>'+esc(s.strategy_id)+'</strong></td>';
      html+='<td class="metric-good" style="font-weight:700">'+num(s.composite_score)+'</td>';
      html+='<td style="color:'+pctColor((s.total_return||0)*100)+'">'+pct((s.total_return||0)*100)+'</td>';
      html+='<td style="color:'+pctColor((s.annualized_return||0)*100)+'">'+pct((s.annualized_return||0)*100)+'</td>';
      html+='<td>'+(s.sharpe_ratio!=null?s.sharpe_ratio.toFixed(2):'-')+'</td>';
      html+='<td class="metric-bad">'+pct(-(s.max_drawdown||0)*100)+'</td>';
      html+='<td>'+((s.win_rate||0)*100).toFixed(1)+'%</td>';
      html+='<td>'+(s.profit_loss_ratio!=null?s.profit_loss_ratio.toFixed(2):'-')+'</td>';
      html+='<td>'+(s.total_trades||0)+'</td>';
      html+='</tr>';
    });
    html+='</table>';
    if(data.batch_id)html+='<div style="font-size:12px;color:var(--text-dim);margin-top:8px">批次: '+esc(data.batch_id)+'</div>';
    document.getElementById('stockResult').innerHTML=html;
  }).catch(function(){
    document.getElementById('stockResult').innerHTML='<div class="empty">查询失败</div>';
  });
}

// Enter key support
document.getElementById('querySymbol').addEventListener('keypress',function(e){
  if(e.key==='Enter')queryStock();
});

// ── History ──────────────────────────────────────────
function loadHistory(){
  document.getElementById('historyTable').innerHTML='<div class="loading">加载中...</div>';
  api('/quant/admin/api/strategy-fitness/history',{_timeout:15000}).then(function(data){
    var batches=data.batches||[];
    if(!batches.length){
      document.getElementById('historyTable').innerHTML='<div class="empty">暂无历史记录</div>';
      return;
    }
    var html='<table><tr><th>批次ID</th><th>时间</th><th>股票数</th><th>策略数</th></tr>';
    batches.forEach(function(b){
      html+='<tr><td>'+esc(b.batchId)+'</td>';
      html+='<td>'+esc(b.createdAt||'-')+'</td>';
      html+='<td>'+b.stockCount+'</td>';
      html+='<td>'+b.strategyCount+'</td></tr>';
    });
    html+='</table>';
    document.getElementById('historyTable').innerHTML=html;
  }).catch(function(){
    document.getElementById('historyTable').innerHTML='<div class="empty">加载失败</div>';
  });
}

// ── Helpers ────────────────────────────────────────────
function rankBadge(r){
  var cls=r===1?'rank-1':r===2?'rank-2':r===3?'rank-3':'rank-n';
  return '<span class="rank-badge '+cls+'">#'+r+'</span>';
}

// Initial status check
checkStatus();
</script>
</body>
</html>`;
}
