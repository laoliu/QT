/**
 * Portfolio Backtest Page — backtest strategies against user's existing stock holdings.
 *
 * Served at `/quant/admin/portfolio-backtest`
 * Self-contained HTML SPA, communicates with `/quant/admin/api/portfolio-backtest`
 * and `/quant/admin/api/strategies`.
 */

export function getPortfolioBacktestPageHtml(baseUrl: string, apiToken?: string): string {
  const b = baseUrl.replace(/\/$/, "");
  const tokenParam = apiToken ? `&token=${apiToken}` : "";
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>持仓策略回测</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:16px 24px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:20px;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.breadcrumb{font-size:12px;margin-bottom:12px;color:var(--text-dim)}
.breadcrumb a{color:var(--accent)}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:14px}
.card h3{font-size:14px;font-weight:600;margin-bottom:12px;color:var(--accent)}
.form-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.form-row label{font-size:12px;color:var(--text-dim);min-width:60px}
input[type=text],input[type=number],select{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:6px 10px;color:var(--text);font-size:13px}
input:focus,select:focus{outline:none;border-color:var(--accent)}
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
.strategy-chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
.chip{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;border-radius:16px;font-size:12px;background:var(--bg3);border:1px solid var(--border);cursor:pointer;user-select:none;transition:all .15s}
.chip.selected{background:rgba(88,166,255,.2);border-color:var(--accent);color:var(--accent)}
.chip:hover{border-color:var(--accent)}
.chip .remove{font-size:14px;cursor:pointer;margin-left:2px;color:var(--text-dim)}
.chip .remove:hover{color:var(--red)}
.info-note{font-size:12px;color:var(--text-dim);margin-top:6px;padding:8px;background:var(--bg);border-radius:4px;border-left:3px solid var(--yellow)}
.chart-container{position:relative;height:250px;margin-top:12px;background:var(--bg);border-radius:6px;padding:8px;border:1px solid var(--border)}
canvas{width:100%!important;height:100%!important}
@media(max-width:700px){.kpi-row{flex-direction:column}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <a href="${b}/quant/admin/ui">🔧 管理面板</a> / <span>📊 持仓策略回测</span></div>
<h1>📊 持仓策略回测</h1>

<!-- Input Section -->
<div class="card">
  <h3>📝 回测设置</h3>
  <div class="form-row">
    <label>股票代码</label>
    <input type="text" id="symbols" placeholder="600519,000858,300750" style="width:300px" title="逗号分隔多只股票代码">
    <button class="btn btn-sm" onclick="loadHoldingsStandalone()" style="font-size:11px">📂 从持仓导入</button>
    <span style="font-size:11px;color:var(--text-dim)">逗号分隔，如: 600519,000858,300750</span>
  </div>
  <div id="sa-holding-chips" style="display:none;margin:-8px 0 8px;padding-left:90px;display:flex;flex-wrap:wrap;gap:4px"></div>
  <div class="form-row">
    <label>起始日期</label>
    <input type="text" id="startDate" placeholder="YYYY-MM-DD" style="width:130px">
    <label>结束日期</label>
    <input type="text" id="endDate" placeholder="YYYY-MM-DD" style="width:130px">
    <label>初始资金</label>
    <input type="number" id="initialCash" value="100000" style="width:120px">
  </div>

  <h3 style="margin-top:16px">📋 选择策略 <span style="font-size:11px;color:var(--text-dim);font-weight:normal">(点击选中/取消，至少选1个)</span></h3>
  <div class="strategy-chips" id="strategy-chips">
    <span class="loading">加载策略列表中...</span>
  </div>

  <div class="form-row" style="margin-top:16px">
    <button class="btn btn-primary" id="run-btn" onclick="runBacktest()">🚀 开始回测</button>
    <button class="btn btn-sm" onclick="selectAllStrategies()">全选</button>
    <button class="btn btn-sm" onclick="clearAllStrategies()">清除</button>
    <button class="btn btn-sm" onclick="selectRecommended()">推荐组合</button>
    <span id="status-text" style="font-size:12px;color:var(--text-dim)"></span>
  </div>
  <div class="info-note">
    💡 回测基于<strong>日K线数据</strong>（非tick级），包含真实A股费率：佣金万3、印花税万5、过户费万0.2、滑点千1。多只股票时资金等权分配。
  </div>
</div>

<!-- Results Section -->
<div id="results-container" style="display:none">

  <!-- KPI Row -->
  <div class="kpi-row" id="kpi-row"></div>

  <!-- Comparison Table -->
  <div class="card">
    <h3>📊 策略对比排名</h3>
    <div style="overflow-x:auto">
      <table id="comparison-table">
        <thead>
          <tr>
            <th>排名</th>
            <th>策略</th>
            <th>年化收益</th>
            <th>最终市值</th>
            <th>盈亏</th>
            <th>最大回撤</th>
            <th>夏普比率</th>
            <th>胜率</th>
            <th>交易次数</th>
            <th>综合分</th>
          </tr>
        </thead>
        <tbody id="comparison-tbody"></tbody>
      </table>
    </div>
  </div>

  <!-- Best Strategy Highlight -->
  <div class="card" id="best-strategy-card" style="display:none">
    <h3>🏆 最优策略推荐</h3>
    <div id="best-strategy-content"></div>
  </div>

  <!-- Failed Strategies -->
  <div class="card" id="failed-card" style="display:none">
    <h3>⚠️ 失败的策略</h3>
    <div id="failed-content"></div>
  </div>
</div>

<script>
var B='${b}';
var TOKEN='${apiToken ?? ""}';
var selectedStrategies=new Set();
var allStrategies=[];

// Recommended strategy set for portfolio backtesting
var RECOMMENDED=['dual-ma-crossover','momentum-breakout','mean-reversion','rsi-filter','rsi-enhanced','macd-trend','bollinger-breakout','volume-confirmation','ema-crossover','turtle-breakout'];

function api(path,opts){
  var url=B+path+(path.indexOf('?')>=0?'&':'?')+'token='+TOKEN;
  return fetch(url,opts).then(function(r){return r.json()});
}

function setDefaults(){
  var now=new Date();
  document.getElementById('endDate').value=now.toISOString().slice(0,10);
  var oneYearAgo=new Date(now);
  oneYearAgo.setFullYear(oneYearAgo.getFullYear()-1);
  document.getElementById('startDate').value=oneYearAgo.toISOString().slice(0,10);
}

function loadStrategies(){
  api('/quant/admin/api/strategies').then(function(d){
    allStrategies=d.builtin||[];
    var container=document.getElementById('strategy-chips');
    container.innerHTML='';
    allStrategies.forEach(function(s){
      var chip=document.createElement('div');
      chip.className='chip'+(RECOMMENDED.indexOf(s.id)>=0?' selected':'');
      chip.setAttribute('data-id',s.id);
      chip.innerHTML=s.name;
      chip.title=s.description||s.id;
      chip.onclick=function(){
        chip.classList.toggle('selected');
        if(chip.classList.contains('selected')){
          selectedStrategies.add(s.id);
        }else{
          selectedStrategies.delete(s.id);
        }
      };
      if(RECOMMENDED.indexOf(s.id)>=0) selectedStrategies.add(s.id);
      container.appendChild(chip);
    });
  }).catch(function(e){
    document.getElementById('strategy-chips').innerHTML='<span style="color:var(--red)">加载策略失败: '+e.message+'</span>';
  });
}

function selectAllStrategies(){
  selectedStrategies.clear();
  document.querySelectorAll('.chip').forEach(function(c){
    c.classList.add('selected');
    selectedStrategies.add(c.getAttribute('data-id'));
  });
}

function clearAllStrategies(){
  selectedStrategies.clear();
  document.querySelectorAll('.chip').forEach(function(c){c.classList.remove('selected')});
}

function selectRecommended(){
  selectedStrategies.clear();
  document.querySelectorAll('.chip').forEach(function(c){
    var id=c.getAttribute('data-id');
    if(RECOMMENDED.indexOf(id)>=0){
      c.classList.add('selected');
      selectedStrategies.add(id);
    }else{
      c.classList.remove('selected');
    }
  });
}

/* ── Load holdings ── */
var _saHoldings=[];
function loadHoldingsStandalone(){
  var chipsEl=document.getElementById('sa-holding-chips');
  chipsEl.style.display='flex';
  chipsEl.innerHTML='<span style="font-size:11px;color:var(--text-dim)">加载中…</span>';
  var liveP=fetch('${b}/quant/api/portfolio?mode=live').then(function(r){return r.json()}).catch(function(){return null});
  var paperP=fetch('${b}/quant/api/portfolio?mode=paper').then(function(r){return r.json()}).catch(function(){return null});
  Promise.all([liveP,paperP]).then(function(results){
    var positions=[];
    var liveData=results[0];
    if(liveData&&liveData.data&&liveData.data.positions&&liveData.data.positions.length){
      positions=liveData.data.positions;
    } else {
      var paperData=results[1];
      if(paperData&&paperData.data&&paperData.data.positions&&paperData.data.positions.length){
        positions=paperData.data.positions;
      }
    }
    if(!positions.length){
      chipsEl.innerHTML='<span style="font-size:11px;color:var(--text-dim)">暂无持仓数据</span>';
      return;
    }
    _saHoldings=positions.map(function(p){
      return {symbol:p.symbol,name:p.name||p.symbol,qty:p.quantity||p.shares||0};
    });
    saRenderHoldingChips();
    saSelectAllHoldings();
  }).catch(function(e){
    chipsEl.innerHTML='<span style="font-size:11px;color:var(--red)">加载失败</span>';
  });
}

function saRenderHoldingChips(){
  var chipsEl=document.getElementById('sa-holding-chips');
  chipsEl.style.display='flex';
  var html='<button class="btn btn-sm" onclick="saSelectAllHoldings()" style="font-size:10px;padding:1px 6px">全选</button>';
  html+='<button class="btn btn-sm" onclick="saClearHoldings()" style="font-size:10px;padding:1px 6px">清除</button>';
  _saHoldings.forEach(function(h){
    html+='<div class="chip selected" data-sa-holding="'+h.symbol+'" onclick="saToggleHolding(this)" style="cursor:pointer;font-size:11px;padding:2px 8px" title="'+(h.name)+' ('+h.qty+'股)">';
    html+=(h.name||h.symbol)+' <span style="opacity:.6;font-size:10px">'+h.symbol+'</span></div>';
  });
  chipsEl.innerHTML=html;
}

function saToggleHolding(el){el.classList.toggle('selected');saSyncHoldingsToInput()}
function saSelectAllHoldings(){document.querySelectorAll('[data-sa-holding]').forEach(function(c){c.classList.add('selected')});saSyncHoldingsToInput()}
function saClearHoldings(){document.querySelectorAll('[data-sa-holding]').forEach(function(c){c.classList.remove('selected')});saSyncHoldingsToInput()}
function saSyncHoldingsToInput(){
  var sel=[];
  document.querySelectorAll('[data-sa-holding].selected').forEach(function(c){sel.push(c.getAttribute('data-sa-holding'))});
  document.getElementById('symbols').value=sel.join(',');
}

function runBacktest(){
  var symbols=document.getElementById('symbols').value.trim();
  if(!symbols){alert('请输入股票代码');return}
  var ids=Array.from(selectedStrategies);
  if(ids.length===0){alert('请至少选择一个策略');return}

  var btn=document.getElementById('run-btn');
  btn.disabled=true;
  btn.textContent='⏳ 回测中...';
  document.getElementById('status-text').textContent='正在回测 '+ids.length+' 个策略 × '+symbols.split(',').length+' 只股票，请耐心等待...';
  document.getElementById('results-container').style.display='none';

  var body={
    symbols:symbols,
    strategyIds:ids.join(','),
    startDate:document.getElementById('startDate').value,
    endDate:document.getElementById('endDate').value,
    initialCash:parseFloat(document.getElementById('initialCash').value)||100000
  };

  api('/quant/admin/api/portfolio-backtest',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)
  }).then(function(d){
    btn.disabled=false;
    btn.textContent='🚀 开始回测';
    document.getElementById('status-text').textContent='';
    if(d.error){
      alert('回测失败: '+d.error);
      return;
    }
    renderResults(d);
  }).catch(function(e){
    btn.disabled=false;
    btn.textContent='🚀 开始回测';
    document.getElementById('status-text').textContent='';
    alert('请求失败: '+e.message);
  });
}

function renderResults(data){
  document.getElementById('results-container').style.display='block';
  var comparison=data.comparison||[];
  var initialCash=data.initialCash||100000;

  // KPI row
  var best=comparison[0];
  var kpiHtml='';
  if(best){
    kpiHtml+='<div class="kpi"><div class="val '+(best.totalReturn>=0?'up':'down')+'">'+fmt(best.totalReturn)+'%</div><div class="lbl">最佳总收益</div></div>';
    kpiHtml+='<div class="kpi"><div class="val">'+fmt(best.annualizedReturn)+'%</div><div class="lbl">最佳年化</div></div>';
    kpiHtml+='<div class="kpi"><div class="val">&yen;'+fmtMoney(best.finalValue)+'</div><div class="lbl">最终市值</div></div>';
    kpiHtml+='<div class="kpi"><div class="val '+(best.profit>=0?'up':'down')+'">&yen;'+fmtMoney(best.profit)+'</div><div class="lbl">盈亏</div></div>';
    kpiHtml+='<div class="kpi"><div class="val">'+data.successCount+'/'+data.totalRuns+'</div><div class="lbl">成功/总数</div></div>';
  }
  document.getElementById('kpi-row').innerHTML=kpiHtml;

  // Comparison table
  var tbody=document.getElementById('comparison-tbody');
  tbody.innerHTML='';
  comparison.forEach(function(r){
    var profit=r.profit!=null?r.profit:0;
    var cl=profit>=0?'up':'down';
    var row='<tr>';
    row+='<td><span class="badge '+(r.rank===1?'badge-green':r.rank<=3?'badge-blue':'badge-purple')+'">#'+r.rank+'</span></td>';
    row+='<td><strong>'+r.strategy+'</strong><br><span style="font-size:11px;color:var(--text-dim)">'+r.strategyId+'</span></td>';
    row+='<td class="'+cl+'">'+fmt(r.annualizedReturn)+'%</td>';
    row+='<td>&yen;'+fmtMoney(r.finalValue)+'</td>';
    row+='<td class="'+cl+'">&yen;'+fmtMoney(profit)+'</td>';
    row+='<td class="down">-'+fmt(Math.abs(r.maxDrawdown||0))+'%</td>';
    row+='<td>'+fmt(r.sharpeRatio)+'</td>';
    row+='<td>'+fmt(r.winRate)+'%</td>';
    row+='<td>'+(r.totalTrades||0)+'</td>';
    row+='<td style="font-weight:600;color:var(--yellow,#e3b341)">'+fmt(r.compositeScore)+'</td>';
    row+='</tr>';
    tbody.innerHTML+=row;
  });

  // Best strategy card
  if(data.bestStrategy){
    document.getElementById('best-strategy-card').style.display='block';
    document.getElementById('best-strategy-content').innerHTML=
      '<div style="font-size:15px;font-weight:600;margin-bottom:8px">🏆 '+data.bestStrategy+'</div>'+
      '<div style="font-size:13px;color:var(--text-dim)">'+
      '在 '+data.symbols.join(', ')+' 上的回测期间 ('+data.period+') 表现最优。'+
      (best?' 初始资金 &yen;'+fmtMoney(initialCash)+'，最终市值 &yen;'+fmtMoney(best.finalValue)+'，'+(best.profit>=0?'盈利':'亏损')+' &yen;'+fmtMoney(Math.abs(best.profit||0))+'。':'')+
      '</div>';
  }else{
    document.getElementById('best-strategy-card').style.display='none';
  }

  // Failed strategies
  var failed=data.failed||[];
  if(failed.length>0){
    document.getElementById('failed-card').style.display='block';
    var fhtml='<ul style="font-size:12px;list-style:none">';
    failed.forEach(function(f){fhtml+='<li style="margin-bottom:4px"><span class="badge badge-red">失败</span> '+f.strategy+': '+f.error+'</li>'});
    fhtml+='</ul>';
    document.getElementById('failed-content').innerHTML=fhtml;
  }else{
    document.getElementById('failed-card').style.display='none';
  }

  // Scroll to results
  document.getElementById('results-container').scrollIntoView({behavior:'smooth'});
}

function fmt(v){return v!=null?Number(v).toFixed(2):'N/A'}
function fmtMoney(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'N/A'}

// Init
setDefaults();
loadStrategies();
</script>
</body>
</html>`;
}
