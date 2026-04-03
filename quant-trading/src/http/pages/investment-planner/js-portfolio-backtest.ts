/** Investment Planner — portfolio (holdings) strategy backtesting */
export function getPortfolioBacktestJs(): string {
  return `
var pbtSelectedStrategies=new Set();
var pbtAllStrategies=[];
var PBT_RECOMMENDED=['dual-ma-crossover','momentum-breakout','mean-reversion','rsi-filter','rsi-enhanced','macd-trend','bollinger-breakout','volume-confirmation','ema-crossover','turtle-breakout'];

/* ── Strategy category mapping ── */
var PBT_CATEGORIES=[
  {key:'trend',icon:'📈',label:'趋势跟踪',ids:['dual-ma-crossover','ema-crossover','multi-ma-convergence','momentum-breakout','trend-combo','cta-trend','macd-trend','dmi-adx','parabolic-sar','ichimoku','turtle-breakout','turtle']},
  {key:'indicator',icon:'📊',label:'技术指标',ids:['rsi-filter','rsi-enhanced','rsi','rsi-macd-combined','stochastic-rsi','williams-r','macd-confirmation','macd-divergence','macd','macd-histogram-reversal','kdj','obv-trend','cci-divergence','roc-momentum']},
  {key:'reversion',icon:'🔄',label:'均值回归',ids:['mean-reversion','rsi-reversal','mean-reversion-cn','mean-reversion-zscore','vwap-reversion','donchian-mid-reversion','double-bottom']},
  {key:'breakout',icon:'⚡',label:'突破策略',ids:['bollinger-breakout','bollinger-bandwidth','keltner-channel','r-breaker','range-breakout','ttm-squeeze','dual-thrust','aberration']},
  {key:'composite',icon:'🎯',label:'综合策略',ids:['composite','gated','scored','enhanced-composite','volume-confirmation','multi-factor']},
  {key:'intraday',icon:'💹',label:'日内与套利',ids:['t0-day-trading','t0-enhanced','grid','grid-trading','pair-trading','auction','dragon-return','spread-arbitrage']},
  {key:'quant',icon:'🛡️',label:'CTA量化',ids:['volatility-target','momentum-rotation','pivot-point','chandelier-exit','atr-trailing']}
];

function pbtGetCategory(id){
  for(var i=0;i<PBT_CATEGORIES.length;i++){
    if(PBT_CATEGORIES[i].ids.indexOf(id)>=0)return PBT_CATEGORIES[i].key;
  }
  return 'other';
}

function setPbtPeriod(months){
  var now=new Date();
  document.getElementById('pbt-end').value=fmtDate(now);
  var ago=new Date(now);ago.setMonth(ago.getMonth()-months);
  document.getElementById('pbt-start').value=fmtDate(ago);
  /* highlight active period button */
  document.querySelectorAll('.pbt-period-btns .btn').forEach(function(b){b.classList.remove('active')});
  event&&event.target&&event.target.classList.add('active');
}

function loadPbtStrategies(){
  api('/quant/admin/api/strategies').then(function(d){
    pbtAllStrategies=d.builtin||[];
    renderPbtStrategyChips();
  }).catch(function(e){
    var c=document.getElementById('pbt-strategy-chips');
    if(c)c.innerHTML='<span style="color:var(--red)">加载策略失败: '+esc(e.message)+'</span>';
  });
}

function renderPbtStrategyChips(filter){
  var container=document.getElementById('pbt-strategy-chips');
  if(!container)return;
  container.innerHTML='';
  var filterLower=(filter||'').toLowerCase();

  /* group strategies by category */
  var grouped={};
  PBT_CATEGORIES.forEach(function(cat){grouped[cat.key]=[]});
  grouped['other']=[];

  pbtAllStrategies.forEach(function(s){
    if(filterLower&&s.name.toLowerCase().indexOf(filterLower)<0&&s.id.toLowerCase().indexOf(filterLower)<0&&(s.description||'').toLowerCase().indexOf(filterLower)<0)return;
    var cat=pbtGetCategory(s.id);
    if(!grouped[cat])grouped[cat]=[];
    grouped[cat].push(s);
  });

  var allCats=PBT_CATEGORIES.concat(grouped['other']&&grouped['other'].length?[{key:'other',icon:'📦',label:'其他策略',ids:[]}]:[]);

  allCats.forEach(function(cat){
    var items=grouped[cat.key];
    if(!items||items.length===0)return;

    var group=document.createElement('div');
    group.className='strat-group';

    var title=document.createElement('div');
    title.className='strat-group-title';
    title.innerHTML='<span class="strat-group-icon">'+cat.icon+'</span> '+esc(cat.label)+' <span class="strat-group-count">'+items.length+'</span>';
    group.appendChild(title);

    var chips=document.createElement('div');
    chips.className='strat-group-chips';

    items.forEach(function(s){
      var chip=document.createElement('div');
      var isRec=PBT_RECOMMENDED.indexOf(s.id)>=0;
      chip.className='chip'+(isRec?' chip-rec':'')+(pbtSelectedStrategies.has(s.id)?' selected':'');
      chip.setAttribute('data-pbt-id',s.id);
      chip.innerHTML=esc(s.name);
      chip.title=s.description||s.id;
      chip.onclick=function(){
        chip.classList.toggle('selected');
        if(chip.classList.contains('selected')){
          pbtSelectedStrategies.add(s.id);
        }else{
          pbtSelectedStrategies.delete(s.id);
        }
        pbtUpdateSelectedCount();
      };
      chips.appendChild(chip);
    });

    group.appendChild(chips);
    container.appendChild(group);
  });

  pbtUpdateSelectedCount();
}

function pbtUpdateSelectedCount(){
  var el=document.getElementById('pbt-selected-count');
  if(el)el.textContent='已选 '+pbtSelectedStrategies.size+' / '+pbtAllStrategies.length+' 个策略';
}

function pbtFilterStrategies(q){
  renderPbtStrategyChips(q);
}

function pbtSelectAll(){
  pbtSelectedStrategies.clear();
  pbtAllStrategies.forEach(function(s){pbtSelectedStrategies.add(s.id)});
  document.querySelectorAll('[data-pbt-id]').forEach(function(c){c.classList.add('selected')});
  pbtUpdateSelectedCount();
}

function pbtClearAll(){
  pbtSelectedStrategies.clear();
  document.querySelectorAll('[data-pbt-id]').forEach(function(c){c.classList.remove('selected')});
  pbtUpdateSelectedCount();
}

function pbtSelectRecommended(){
  pbtSelectedStrategies.clear();
  document.querySelectorAll('[data-pbt-id]').forEach(function(c){
    var id=c.getAttribute('data-pbt-id');
    if(PBT_RECOMMENDED.indexOf(id)>=0){
      c.classList.add('selected');
      pbtSelectedStrategies.add(id);
    }else{
      c.classList.remove('selected');
    }
  });
  pbtUpdateSelectedCount();
}

function runPortfolioBacktest(){
  var symbols=document.getElementById('pbt-symbols').value.trim();
  if(!symbols){toast('请输入股票代码');return}
  var ids=Array.from(pbtSelectedStrategies);
  if(ids.length===0){toast('请至少选择一个策略');return}

  var btn=document.getElementById('btn-pbt-run');
  btn.disabled=true;btn.textContent='⏳ 回测中...';
  document.getElementById('pbt-status').textContent='正在回测 '+ids.length+' 个策略 × '+symbols.split(',').length+' 只股票，请耐心等待...';
  document.getElementById('pbt-results').style.display='none';

  var body={
    symbols:symbols,
    strategyIds:ids.join(','),
    startDate:document.getElementById('pbt-start').value,
    endDate:document.getElementById('pbt-end').value,
    initialCash:parseFloat(document.getElementById('pbt-capital').value)||100000
  };

  api('/quant/admin/api/portfolio-backtest',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body),
    _timeout: Math.max(120000, ids.length * 8000)
  }).then(function(d){
    btn.disabled=false;btn.textContent='🚀 开始回测';
    document.getElementById('pbt-status').textContent='';
    if(d.error){toast('❌ 回测失败: '+d.error);return}
    renderPbtResults(d);
  }).catch(function(e){
    btn.disabled=false;btn.textContent='🚀 开始回测';
    document.getElementById('pbt-status').textContent='';
    toast('❌ 请求失败: '+e.message);
  });
}

function renderPbtResults(data){
  document.getElementById('pbt-results').style.display='block';
  var comparison=data.comparison||[];
  var initialCash=data.initialCash||100000;

  // KPI row
  var best=comparison[0];
  var kpiHtml='';
  if(best){
    kpiHtml+='<div class="kpi"><div class="kpi-val '+(best.totalReturn>=0?'up':'down')+'">'+pbtFmt(best.totalReturn)+'%</div><div class="kpi-label">最佳总收益</div></div>';
    kpiHtml+='<div class="kpi"><div class="kpi-val">'+pbtFmt(best.annualizedReturn)+'%</div><div class="kpi-label">最佳年化</div></div>';
    kpiHtml+='<div class="kpi"><div class="kpi-val">&yen;'+pbtFmtMoney(best.finalValue)+'</div><div class="kpi-label">最终市值</div></div>';
    kpiHtml+='<div class="kpi"><div class="kpi-val '+(best.profit>=0?'up':'down')+'">&yen;'+pbtFmtMoney(best.profit)+'</div><div class="kpi-label">盈亏</div></div>';
    kpiHtml+='<div class="kpi"><div class="kpi-val">'+data.successCount+'/'+data.totalRuns+'</div><div class="kpi-label">成功/总数</div></div>';
  }
  document.getElementById('pbt-kpis').innerHTML=kpiHtml;

  // Comparison table
  var tbody=document.getElementById('pbt-comparison-table').querySelector('tbody');
  tbody.innerHTML='';
  comparison.forEach(function(r){
    var profit=r.profit!=null?r.profit:0;
    var cl=profit>=0?'up':'down';
    var row='<tr>';
    row+='<td><span class="badge '+(r.rank===1?'badge-green':r.rank<=3?'badge-blue':'badge-purple')+'">#'+r.rank+'</span></td>';
    row+='<td><strong>'+esc(r.strategy)+'</strong><br><span style="font-size:11px;color:var(--text-dim)">'+esc(r.strategyId)+'</span></td>';
    row+='<td><span style="font-weight:600;color:'+(r.compositeScore>=50?'var(--green)':r.compositeScore>=20?'var(--yellow)':'var(--red)')+'">'+pbtFmt(r.compositeScore)+'</span></td>';
    row+='<td class="'+cl+'">'+pbtFmt(r.totalReturn)+'%</td>';
    row+='<td class="'+cl+'">'+pbtFmt(r.annualizedReturn)+'%</td>';
    row+='<td>&yen;'+pbtFmtMoney(r.finalValue)+'</td>';
    row+='<td class="'+cl+'">&yen;'+pbtFmtMoney(profit)+'</td>';
    row+='<td class="down">-'+pbtFmt(Math.abs(r.maxDrawdown||0))+'%</td>';
    row+='<td>'+pbtFmt(r.sharpeRatio)+'</td>';
    row+='<td>'+pbtFmt(r.winRate)+'%</td>';
    row+='<td>'+(r.totalTrades||0)+'</td>';
    row+='</tr>';
    tbody.innerHTML+=row;
  });

  // Best strategy card
  var bestCard=document.getElementById('pbt-best-card');
  if(data.bestStrategy&&best){
    bestCard.style.display='block';
    document.getElementById('pbt-best-content').innerHTML=
      '<div style="font-size:15px;font-weight:600;margin-bottom:8px">🏆 '+esc(data.bestStrategy)+'</div>'+
      '<div style="font-size:13px;color:var(--text-dim)">'+
      '在 '+(data.symbols||[]).join(', ')+' 上的回测期间 ('+esc(data.period||'')+') 表现最优。'+
      '初始资金 &yen;'+pbtFmtMoney(initialCash)+'，最终市值 &yen;'+pbtFmtMoney(best.finalValue)+'，'+
      (best.profit>=0?'盈利':'亏损')+' &yen;'+pbtFmtMoney(Math.abs(best.profit||0))+'。'+
      '</div>';
  }else{
    bestCard.style.display='none';
  }

  // Failed strategies
  var failed=data.failed||[];
  var failedCard=document.getElementById('pbt-failed-card');
  if(failed.length>0){
    failedCard.style.display='block';
    var fhtml='<ul style="font-size:12px;list-style:none">';
    failed.forEach(function(f){fhtml+='<li style="margin-bottom:4px"><span class="badge badge-red">失败</span> '+esc(f.strategy)+': '+esc(f.error)+'</li>'});
    fhtml+='</ul>';
    document.getElementById('pbt-failed-content').innerHTML=fhtml;
  }else{
    failedCard.style.display='none';
  }

  document.getElementById('pbt-results').scrollIntoView({behavior:'smooth'});
}

function pbtFmt(v){return v!=null?Number(v).toFixed(2):'N/A'}
function pbtFmtMoney(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'N/A'}

/* ── init: set defaults + lazy-load strategies on tab activation ── */
(function(){
  var pbtInited=false;
  function pbtInit(){
    if(pbtInited)return;
    pbtInited=true;
    PBT_RECOMMENDED.forEach(function(id){pbtSelectedStrategies.add(id)});
    setPbtPeriod(12);
    loadPbtStrategies();
  }
  var origOnTab=window._onTabActivated;
  window._onTabActivated=function(name){
    if(origOnTab)origOnTab(name);
    if(name==='pf-backtest') pbtInit();
  };
  /* If tab is already active (hash navigation or direct link), init immediately */
  var activeTab=document.querySelector('.tab.active');
  if(activeTab&&activeTab.getAttribute('data-tab')==='pf-backtest') pbtInit();
})();
`;
}
