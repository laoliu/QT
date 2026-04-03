/** Investment Planner — backtesting operations, equity curves, and chart drawing */
export function getBacktestJs(): string {
  return `
var BT_COLORS=['#58a6ff','#3fb950','#f85149','#d29922','#bc8cff','#db6d28'];

function btParams(){
  var symsRaw=document.getElementById('bt-symbols').value.trim();
  var symbols=symsRaw?symsRaw.split(',').map(function(s){return s.trim()}).filter(Boolean):[];
  return {
    initial_capital:parseFloat(document.getElementById('bt-capital').value)||500000,
    start_date:document.getElementById('bt-start').value||fmtDate((function(){var d=new Date();d.setFullYear(d.getFullYear()-1);return d})()),
    end_date:document.getElementById('bt-end').value||fmtDate(new Date()),
    risk_tolerance:document.getElementById('bt-risk').value||'medium',
    rebalance_days:parseInt(document.getElementById('bt-rebalance').value)||14,
    commission_rate:parseFloat(document.getElementById('bt-commission').value)||0.0003,
    stamp_tax:parseFloat(document.getElementById('bt-tax').value)||0.001,
    symbols:symbols.length>0?symbols:undefined
  };
}

function setBtLoading(loading){
  var b1=document.getElementById('btn-bt-single');
  var b2=document.getElementById('btn-bt-compare');
  var b3=document.getElementById('btn-bt-multi-period');
  b1.disabled=loading;
  b2.disabled=loading;
  b3.disabled=loading;
  var st=document.getElementById('bt-status');
  if(loading){
    st.innerHTML='<span class="spinner-sm"></span> 正在回测，请稍候（可能需要30-60秒获取历史数据）...';
  }else{
    st.textContent='';
  }
}

function runSingleBacktest(){
  var p=btParams();
  p.action='run';
  var btVal=document.getElementById('bt-strategy').value||'momentum';
  if(btVal.indexOf('composed:')===0){
    p.composed_strategy_id=btVal.slice(9);
    p.strategy='composed';
  }else{
    p.strategy=btVal;
  }
  setBtLoading(true);
  document.getElementById('bt-single-results').style.display='none';
  document.getElementById('bt-compare-results').style.display='none';
  document.getElementById('bt-multi-period-results').style.display='none';
  api('/quant/admin/api/backtest',{method:'POST',body:JSON.stringify(p),_timeout:120000}).then(function(d){
    setBtLoading(false);
    if(d.error){toast('❌ '+d.error);return;}
    renderSingleBacktest(d);
  }).catch(function(){setBtLoading(false)});
}

function runCompareBacktest(){
  var p=btParams();
  p.action='compare';
  setBtLoading(true);
  document.getElementById('bt-single-results').style.display='none';
  document.getElementById('bt-compare-results').style.display='none';
  document.getElementById('bt-multi-period-results').style.display='none';
  api('/quant/admin/api/backtest',{method:'POST',body:JSON.stringify(p),_timeout:120000}).then(function(d){
    setBtLoading(false);
    if(d.error){toast('❌ '+d.error);return;}
    renderCompareBacktest(d);
  }).catch(function(){setBtLoading(false)});
}

function renderSingleBacktest(data){
  var s=data.summary||{};
  document.getElementById('bt-single-results').style.display='block';
  // KPIs
  var kpiHtmlStr='';
  kpiHtmlStr+=kpiHtml((s.totalReturn>=0?'+':'')+s.totalReturn+'%','总收益');
  kpiHtmlStr+=kpiHtml((s.annualizedReturn>=0?'+':'')+s.annualizedReturn+'%','年化收益');
  kpiHtmlStr+=kpiHtml('-'+s.maxDrawdown+'%','最大回撤');
  kpiHtmlStr+=kpiHtml(s.sharpeRatio,'夏普比率');
  kpiHtmlStr+=kpiHtml(s.winRate+'%','胜率');
  kpiHtmlStr+=kpiHtml(s.profitFactor,'盈亏比');
  kpiHtmlStr+=kpiHtml(s.totalTrades,'总交易数');
  kpiHtmlStr+=kpiHtml('¥'+numInt(s.totalCommission),'总手续费');
  document.getElementById('bt-kpis').innerHTML=kpiHtmlStr;

  // Equity curve
  drawEquityCurve('bt-equity-canvas',data.equityCurve,[{label:s.strategyLabel,color:BT_COLORS[0]}],s.initialCapital);

  // Monthly returns
  renderMonthlyChart(data.monthlyReturns);

  // Trades table
  var trades=data.trades||[];
  document.getElementById('bt-trade-count').textContent=s.totalTrades;
  var tbody=document.querySelector('#bt-trades-table tbody');
  tbody.innerHTML=trades.map(function(t){
    var pColor=t.pnl>0?'var(--green)':t.pnl<0?'var(--red)':'var(--text)';
    var sideLabel=t.side==='buy'?'<span class="badge badge-green">买入</span>':'<span class="badge badge-red">卖出</span>';
    return '<tr><td>'+esc(t.date)+'</td><td>'+esc(t.symbol)+' '+esc(t.name)+'</td><td>'+sideLabel+'</td>'+
      '<td>'+t.shares+'</td><td>'+t.price+'</td>'+
      '<td style="color:'+pColor+'">'+(t.pnl>0?'+':'')+t.pnl+'</td>'+
      '<td style="color:'+pColor+'">'+(t.pnlPct>0?'+':'')+t.pnlPct+'%</td>'+
      '<td>'+t.commission+'</td></tr>';
  }).join('');

  // Final positions
  var fps=data.finalPositions||[];
  var tbody2=document.querySelector('#bt-final-pos-table tbody');
  tbody2.innerHTML=fps.map(function(p){
    var pColor=p.pnl>0?'var(--green)':p.pnl<0?'var(--red)':'var(--text)';
    return '<tr><td>'+esc(p.symbol)+'</td><td>'+esc(p.name)+'</td><td>'+p.shares+'</td>'+
      '<td>'+p.avgCost+'</td><td>'+p.currentPrice+'</td>'+
      '<td style="color:'+pColor+'">'+(p.pnl>0?'+':'')+p.pnl+'</td>'+
      '<td style="color:'+pColor+'">'+(p.pnlPct>0?'+':'')+p.pnlPct+'%</td></tr>';
  }).join('');
}

function renderCompareBacktest(data){
  document.getElementById('bt-compare-results').style.display='block';
  var results=data.comparison||[];
  var best=data.bestStrategy;

  // Summary table
  var sumHtml='<div style="overflow-x:auto"><table><thead><tr>'+
    '<th>策略</th><th>总收益</th><th>年化收益</th><th>最大回撤</th><th>夏普比率</th><th>胜率</th><th>盈亏比</th><th>交易数</th><th>结果</th>'+
    '</tr></thead><tbody>';
  results.forEach(function(r){
    var s=r.summary||{};
    if(s.error){
      sumHtml+='<tr><td>'+esc(s.strategyLabel||s.strategy)+'</td><td colspan="7" style="color:var(--red)">'+esc(s.error)+'</td><td></td></tr>';
      return;
    }
    var isBest=s.strategy===best;
    var retColor=s.totalReturn>=0?'var(--green)':'var(--red)';
    sumHtml+='<tr'+(isBest?' style="background:rgba(63,185,80,.08)"':'')+'>';
    sumHtml+='<td>'+esc(s.strategyLabel)+(isBest?' <span class="badge badge-green">最优</span>':'')+'</td>';
    sumHtml+='<td style="color:'+retColor+';font-weight:600">'+(s.totalReturn>=0?'+':'')+s.totalReturn+'%</td>';
    sumHtml+='<td style="color:'+retColor+'">'+(s.annualizedReturn>=0?'+':'')+s.annualizedReturn+'%</td>';
    sumHtml+='<td style="color:var(--red)">-'+s.maxDrawdown+'%</td>';
    sumHtml+='<td>'+s.sharpeRatio+'</td>';
    sumHtml+='<td>'+s.winRate+'%</td>';
    sumHtml+='<td>'+s.profitFactor+'</td>';
    sumHtml+='<td>'+s.totalTrades+'</td>';
    sumHtml+='<td>¥'+numInt(s.finalEquity)+'</td></tr>';
  });
  sumHtml+='</tbody></table></div>';
  document.getElementById('bt-compare-summary').innerHTML=sumHtml;

  // Multi-curve chart
  var curves=[];
  var labels=[];
  results.forEach(function(r,i){
    if(r.equityCurve&&r.equityCurve.length>0){
      curves.push(r.equityCurve);
      labels.push({label:r.summary.strategyLabel||r.summary.strategy,color:BT_COLORS[i%BT_COLORS.length]});
    }
  });
  if(curves.length>0){
    drawMultiEquityCurve('bt-compare-canvas',curves,labels,data.initialCapital);
    var legendHtml=labels.map(function(l){
      return '<div class="bt-legend-item"><div class="bt-legend-dot" style="background:'+l.color+'"></div>'+esc(l.label)+'</div>';
    }).join('');
    document.getElementById('bt-compare-legend').innerHTML=legendHtml;
  }

  // Per-strategy detail cards
  var cardsHtml='';
  results.forEach(function(r,i){
    var s=r.summary||{};
    if(s.error)return;
    var color=BT_COLORS[i%BT_COLORS.length];
    var isBest=s.strategy===best;
    cardsHtml+='<div class="card" style="border-left:3px solid '+color+'">';
    cardsHtml+='<h3>'+esc(s.strategyLabel)+(isBest?' 🏆':'')+'</h3>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">总收益</span><span style="color:'+(s.totalReturn>=0?'var(--green)':'var(--red)')+';font-weight:600">'+(s.totalReturn>=0?'+':'')+s.totalReturn+'%</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">年化收益</span><span>'+(s.annualizedReturn>=0?'+':'')+s.annualizedReturn+'%</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">最大回撤</span><span style="color:var(--red)">-'+s.maxDrawdown+'%</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">夏普比率</span><span>'+s.sharpeRatio+'</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">胜率</span><span>'+s.winRate+'%</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">盈亏比</span><span>'+s.profitFactor+'</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">交易数</span><span>'+s.totalTrades+'</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">盈利/亏损</span><span>'+s.winningTrades+'/'+s.losingTrades+'</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">手续费</span><span>¥'+numInt(s.totalCommission)+'</span></div>';
    cardsHtml+='<div class="bt-metric"><span class="bt-metric-label">终值</span><span style="font-weight:600">¥'+numInt(s.finalEquity)+'</span></div>';
    cardsHtml+='</div>';
  });
  document.getElementById('bt-compare-cards').innerHTML=cardsHtml;
}

/* ── Period Presets & Multi-Period Batch ── */
function fmtDate(d){return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}

function setBtPeriod(months){
  var end=new Date();
  var start=new Date();
  start.setMonth(start.getMonth()-months);
  document.getElementById('bt-start').value=fmtDate(start);
  document.getElementById('bt-end').value=fmtDate(end);
}

// Init default dates to recent 1 year
(function(){
  var now=new Date();
  var y1=new Date();y1.setFullYear(y1.getFullYear()-1);
  var bs=document.getElementById('bt-start');
  var be=document.getElementById('bt-end');
  if(bs&&!bs.value)bs.value=fmtDate(y1);
  if(be&&!be.value)be.value=fmtDate(now);
})();

function runMultiPeriodBacktest(){
  var p=btParams();
  var now=new Date();
  var periods=[
    {label:'近3个月',months:3},
    {label:'近半年',months:6},
    {label:'近1年',months:12}
  ];
  setBtLoading(true);
  document.getElementById('bt-single-results').style.display='none';
  document.getElementById('bt-compare-results').style.display='none';
  document.getElementById('bt-multi-period-results').style.display='none';
  var st=document.getElementById('bt-status');
  var completed=0;
  var allResults=[];
  var total=periods.length;

  function runNext(idx){
    if(idx>=periods.length){
      setBtLoading(false);
      renderMultiPeriodResults(allResults,periods,p.initial_capital);
      return;
    }
    var period=periods[idx];
    st.innerHTML='<span class="spinner-sm"></span> 正在回测 '+period.label+' ('+(idx+1)+'/'+total+')...';
    var startD=new Date(now);
    startD.setMonth(startD.getMonth()-period.months);
    var body={
      action:'compare',
      initial_capital:p.initial_capital,
      start_date:fmtDate(startD),
      end_date:fmtDate(now),
      risk_tolerance:p.risk_tolerance,
      rebalance_days:p.rebalance_days,
      commission_rate:p.commission_rate,
      stamp_tax:p.stamp_tax,
      symbols:p.symbols
    };
    api('/quant/admin/api/backtest',{method:'POST',body:JSON.stringify(body),_timeout:120000}).then(function(d){
      allResults.push({period:period,data:d});
      runNext(idx+1);
    }).catch(function(){
      allResults.push({period:period,data:{error:'请求失败'}});
      runNext(idx+1);
    });
  }
  runNext(0);
}

function renderMultiPeriodResults(allResults,periods,initialCapital){
  document.getElementById('bt-multi-period-results').style.display='block';

  // Build cross-period summary table: rows = strategies, cols = periods
  // Dynamically discover all strategies from comparison results
  var builtinNames=['momentum','value','growth','swing','mixed','manipulation','spread-arbitrage','rsi-reversal','vwap-reversion','mean-reversion-zscore','williams-r','momentum-breakout','range-breakout','dual-ma-crossover','trend-combo','double-bottom','macd-divergence','volume-confirmation','gated'];
  var builtinLabels={momentum:'趋势动量',value:'价值投资',growth:'成长投资',swing:'波段交易',mixed:'混合策略',manipulation:'跟庄策略','spread-arbitrage':'价差套利','rsi-reversal':'RSI反转','vwap-reversion':'VWAP回归','mean-reversion-zscore':'均值回归Z-Score','williams-r':'威廉指标反转','momentum-breakout':'动量突破','range-breakout':'区间突破','dual-ma-crossover':'双均线交叉','trend-combo':'趋势合并','double-bottom':'双底双顶','macd-divergence':'MACD背离','volume-confirmation':'成交量确认','gated':'条件门控'};
  var strategyNames=builtinNames.slice();
  var strategyLabels=Object.assign({},builtinLabels);
  // Collect composed/extra strategies from results
  allResults.forEach(function(ar){
    var comp=(ar.data&&ar.data.comparison)||[];
    comp.forEach(function(r){
      if(r.summary&&strategyNames.indexOf(r.summary.strategy)===-1){
        strategyNames.push(r.summary.strategy);
        strategyLabels[r.summary.strategy]=r.summary.strategyLabel||r.summary.strategy;
      }
    });
  });

  var html='<div style="overflow-x:auto"><table><thead><tr><th>策略</th>';
  periods.forEach(function(p){html+='<th colspan="3" style="text-align:center;border-bottom:2px solid var(--border)">'+esc(p.label)+'</th>'});
  html+='<th>综合评分</th></tr><tr><th></th>';
  periods.forEach(function(){html+='<th>总收益</th><th>最大回撤</th><th>夏普</th>'});
  html+='<th></th></tr></thead><tbody>';

  var scores={};
  strategyNames.forEach(function(strat){
    scores[strat]=0;
    html+='<tr><td style="font-weight:600">'+esc(strategyLabels[strat])+'</td>';
    allResults.forEach(function(ar){
      var comp=(ar.data&&ar.data.comparison)||[];
      var found=null;
      comp.forEach(function(r){if(r.summary&&r.summary.strategy===strat)found=r.summary});
      if(found&&!found.error){
        var noTrades=found.totalTrades===0;
        if(noTrades){
          html+='<td colspan="3" style="color:var(--text-dim)">未产生交易</td>';
        }else{
          var retColor=found.totalReturn>=0?'var(--green)':'var(--red)';
          html+='<td style="color:'+retColor+';font-weight:600">'+(found.totalReturn>=0?'+':'')+found.totalReturn+'%</td>';
          var ddText=found.maxDrawdown>0?'-'+found.maxDrawdown+'%':'0%';
          html+='<td style="color:var(--red)">'+ddText+'</td>';
          html+='<td>'+found.sharpeRatio+'</td>';
        }
        // Score: balanced weights with clamped sharpe to prevent extreme values dominating
        var clampedSharpe=Math.max(-3,Math.min(3,parseFloat(found.sharpeRatio)||0));
        scores[strat]+=(found.totalReturn||0)*1.0 - (found.maxDrawdown||0)*0.8 + clampedSharpe*5;
      }else{
        html+='<td colspan="3" style="color:var(--text-dim)">—</td>';
      }
    });
    html+='<td style="font-weight:600">'+scores[strat].toFixed(1)+'</td></tr>';
  });
  html+='</tbody></table></div>';

  // Find best overall
  var bestStrat=null,bestScore=-Infinity;
  Object.keys(scores).forEach(function(k){if(scores[k]>bestScore){bestScore=scores[k];bestStrat=k}});
  if(bestStrat){
    html+='<div style="margin-top:12px;padding:10px;background:rgba(63,185,80,.08);border-radius:8px;font-size:13px">';
    html+='🏆 <strong>综合最优策略: '+esc(strategyLabels[bestStrat])+'</strong> (综合评分: '+bestScore.toFixed(1)+')';
    html+='<br><span style="color:var(--text-dim)">评分规则: 总收益×1.0 − 最大回撤×0.8 + 夏普比率(限±3)×5，跨3个周期累加</span>';
    html+='</div>';
  }

  document.getElementById('bt-multi-period-summary').innerHTML=html;

  // Per-period equity curve charts
  var chartsHtml='';
  allResults.forEach(function(ar,idx){
    if(!ar.data||ar.data.error)return;
    var results=ar.data.comparison||[];
    var canvasId='bt-mp-canvas-'+idx;
    chartsHtml+='<div class="card" style="margin-bottom:16px">';
    chartsHtml+='<h3>📈 '+esc(ar.period.label)+' 净值曲线</h3>';
    chartsHtml+='<div class="bt-chart" style="height:260px"><canvas id="'+canvasId+'"></canvas></div>';
    chartsHtml+='<div class="bt-legend" id="bt-mp-legend-'+idx+'"></div>';
    chartsHtml+='</div>';
  });
  document.getElementById('bt-multi-period-charts').innerHTML=chartsHtml;

  // Draw curves after DOM is ready
  setTimeout(function(){
    allResults.forEach(function(ar,idx){
      if(!ar.data||ar.data.error)return;
      var results=ar.data.comparison||[];
      var curves=[],labels=[];
      results.forEach(function(r,i){
        if(r.equityCurve&&r.equityCurve.length>0){
          curves.push(r.equityCurve);
          labels.push({label:r.summary.strategyLabel||r.summary.strategy,color:BT_COLORS[i%BT_COLORS.length]});
        }
      });
      if(curves.length>0){
        drawMultiEquityCurve('bt-mp-canvas-'+idx,curves,labels,initialCapital);
        var legendHtml=labels.map(function(l){
          return '<div class="bt-legend-item"><div class="bt-legend-dot" style="background:'+l.color+'"></div>'+esc(l.label)+'</div>';
        }).join('');
        document.getElementById('bt-mp-legend-'+idx).innerHTML=legendHtml;
      }
    });
  },100);
}

/* ── Canvas Drawing for Equity Curves ── */
function drawEquityCurve(canvasId,curve,lineInfos,baseline){
  drawMultiEquityCurve(canvasId,[curve],lineInfos,baseline);
}

function drawMultiEquityCurve(canvasId,curves,lineInfos,baseline){
  var canvas=document.getElementById(canvasId);
  if(!canvas)return;
  var dpr=window.devicePixelRatio||1;
  var rect=canvas.parentElement.getBoundingClientRect();
  canvas.width=rect.width*dpr;
  canvas.height=rect.height*dpr;
  canvas.style.width=rect.width+'px';
  canvas.style.height=rect.height+'px';
  var ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  var W=rect.width,H=rect.height;
  var padL=60,padR=16,padT=16,padB=28;

  // Find global min/max
  var allVals=[baseline||0];
  curves.forEach(function(c){c.forEach(function(p){allVals.push(p.equity)})});
  var minVal=Math.min.apply(null,allVals)*0.98;
  var maxVal=Math.max.apply(null,allVals)*1.02;
  if(maxVal===minVal)maxVal=minVal+1;

  // Background
  ctx.fillStyle='#0d1117';
  ctx.fillRect(0,0,W,H);

  // Grid
  ctx.strokeStyle='#21262d';
  ctx.lineWidth=0.5;
  for(var i=0;i<=4;i++){
    var y=padT+(H-padT-padB)*(1-i/4);
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
    ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='right';
    var val=minVal+(maxVal-minVal)*(i/4);
    ctx.fillText('¥'+Math.round(val).toLocaleString(),padL-4,y+3);
  }

  // Baseline
  if(baseline){
    var blY=padT+(H-padT-padB)*(1-(baseline-minVal)/(maxVal-minVal));
    ctx.strokeStyle='#30363d';ctx.lineWidth=1;ctx.setLineDash([4,4]);
    ctx.beginPath();ctx.moveTo(padL,blY);ctx.lineTo(W-padR,blY);ctx.stroke();
    ctx.setLineDash([]);
  }

  // Find max length
  var maxLen=0;
  curves.forEach(function(c){if(c.length>maxLen)maxLen=c.length});
  if(maxLen<2)return;

  // Draw each curve
  curves.forEach(function(curve,ci){
    var color=lineInfos[ci]?lineInfos[ci].color:BT_COLORS[ci%BT_COLORS.length];
    ctx.strokeStyle=color;
    ctx.lineWidth=1.5;
    ctx.beginPath();
    for(var j=0;j<curve.length;j++){
      var x=padL+(W-padL-padR)*(j/(maxLen-1));
      var y=padT+(H-padT-padB)*(1-(curve[j].equity-minVal)/(maxVal-minVal));
      if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
    }
    ctx.stroke();
    // Fill area under curve (very transparent)
    if(curves.length===1){
      ctx.lineTo(padL+(W-padL-padR)*((curve.length-1)/(maxLen-1)),H-padB);
      ctx.lineTo(padL,H-padB);
      ctx.closePath();
      ctx.fillStyle=color.replace(')',',0.08)').replace('rgb','rgba').replace('#','');
      // Use hex to rgba
      var r2=parseInt(color.slice(1,3),16),g2=parseInt(color.slice(3,5),16),b2=parseInt(color.slice(5,7),16);
      ctx.fillStyle='rgba('+r2+','+g2+','+b2+',0.08)';
      ctx.fill();
    }
  });

  // X-axis labels
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='center';
  var longestCurve=curves[0];
  curves.forEach(function(c){if(c.length>longestCurve.length)longestCurve=c});
  var step2=Math.max(1,Math.floor(longestCurve.length/6));
  for(var j=0;j<longestCurve.length;j+=step2){
    var x=padL+(W-padL-padR)*(j/(maxLen-1));
    var dateLabel=(longestCurve[j].date||'').substring(5);
    ctx.fillText(dateLabel,x,H-padB+14);
  }
}

/* ── Stock Backtest Equity Curve with Trade Markers ── */
function drawStockBtEquityCurve(canvasId,curve,trades,baseline,label){
  var canvas=document.getElementById(canvasId);
  if(!canvas||!curve||curve.length<2)return;
  var dpr=window.devicePixelRatio||1;
  var rect=canvas.parentElement.getBoundingClientRect();
  canvas.width=rect.width*dpr;
  canvas.height=rect.height*dpr;
  canvas.style.width=rect.width+'px';
  canvas.style.height=rect.height+'px';
  var ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  var W=rect.width,H=rect.height;
  var padL=60,padR=16,padT=20,padB=32;

  // Range
  var allVals=[baseline||0];
  curve.forEach(function(p){allVals.push(p.equity)});
  var minVal=Math.min.apply(null,allVals)*0.98;
  var maxVal=Math.max.apply(null,allVals)*1.02;
  if(maxVal===minVal)maxVal=minVal+1;

  // Background
  ctx.fillStyle='#0d1117';
  ctx.fillRect(0,0,W,H);

  // Build date index for trade markers
  var dateIndex={};
  curve.forEach(function(p,idx){dateIndex[p.date]=idx;});

  // Grid
  ctx.strokeStyle='#21262d';ctx.lineWidth=0.5;
  for(var i=0;i<=4;i++){
    var y=padT+(H-padT-padB)*(1-i/4);
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
    ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='right';
    var val=minVal+(maxVal-minVal)*(i/4);
    ctx.fillText('¥'+Math.round(val).toLocaleString(),padL-4,y+3);
  }

  // Baseline
  if(baseline){
    var blY=padT+(H-padT-padB)*(1-(baseline-minVal)/(maxVal-minVal));
    ctx.strokeStyle='#30363d';ctx.lineWidth=1;ctx.setLineDash([4,4]);
    ctx.beginPath();ctx.moveTo(padL,blY);ctx.lineTo(W-padR,blY);ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle='#8b949e';ctx.font='9px sans-serif';ctx.textAlign='left';
    ctx.fillText('初始 ¥'+Math.round(baseline).toLocaleString(),padL+4,blY-4);
  }

  var n=curve.length;
  function xPos(idx){return padL+(W-padL-padR)*(idx/(n-1));}
  function yPos(v){return padT+(H-padT-padB)*(1-(v-minVal)/(maxVal-minVal));}

  // Draw equity line
  ctx.strokeStyle='#58a6ff';ctx.lineWidth=2;
  ctx.beginPath();
  for(var j=0;j<n;j++){
    var x=xPos(j),y=yPos(curve[j].equity);
    if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();

  // Fill area
  ctx.lineTo(xPos(n-1),H-padB);
  ctx.lineTo(xPos(0),H-padB);
  ctx.closePath();
  ctx.fillStyle='rgba(88,166,255,0.06)';
  ctx.fill();

  // Trade markers
  if(trades&&trades.length){
    trades.forEach(function(t){
      var tDate=t.date||t.entryDate||'';
      var idx=dateIndex[tDate];
      if(idx==null){
        // Find closest date in curve
        for(var di=0;di<curve.length;di++){
          if(curve[di].date>=tDate){idx=di;break;}
        }
        if(idx==null)idx=curve.length-1;
      }
      var x=xPos(idx);
      var y=yPos(curve[idx].equity);
      var isBuy=t.side==='buy'||t.type==='buy'||t.action==='buy';
      if(isBuy){
        // Buy: upward triangle (red)
        ctx.fillStyle='#f85149';
        ctx.beginPath();
        ctx.moveTo(x,y-8);
        ctx.lineTo(x-5,y);
        ctx.lineTo(x+5,y);
        ctx.closePath();
        ctx.fill();
      }else{
        // Sell: downward triangle (green)
        ctx.fillStyle='#3fb950';
        ctx.beginPath();
        ctx.moveTo(x,y+8);
        ctx.lineTo(x-5,y);
        ctx.lineTo(x+5,y);
        ctx.closePath();
        ctx.fill();
      }
    });
  }

  // End value annotation
  var lastEquity=curve[n-1].equity;
  var returnPct=baseline?((lastEquity-baseline)/baseline*100):0;
  var retColor=returnPct>=0?'#3fb950':'#f85149';
  ctx.fillStyle=retColor;ctx.font='bold 11px sans-serif';ctx.textAlign='right';
  ctx.fillText('¥'+Math.round(lastEquity).toLocaleString()+' ('+(returnPct>=0?'+':'')+returnPct.toFixed(2)+'%)',W-padR,padT-4);

  // Label
  if(label){
    ctx.fillStyle='#58a6ff';ctx.font='11px sans-serif';ctx.textAlign='left';
    ctx.fillText(label,padL,padT-4);
  }

  // X-axis date labels
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='center';
  var step=Math.max(1,Math.floor(n/6));
  for(var j=0;j<n;j+=step){
    var x=xPos(j);
    var dateLabel=(curve[j].date||'').substring(5);
    ctx.fillText(dateLabel,x,H-padB+14);
  }
  if((n-1)%step!==0){
    var x=xPos(n-1);
    ctx.fillText((curve[n-1].date||'').substring(5),x,H-padB+14);
  }

  // Legend for trade markers
  ctx.font='10px sans-serif';
  var legX=padL+4,legY=H-4;
  ctx.fillStyle='#f85149';ctx.fillText('▲ 买入',legX,legY);
  ctx.fillStyle='#3fb950';ctx.fillText('▼ 卖出',legX+50,legY);

  // Tooltip
  canvas.onmousemove=function(e){
    var r=canvas.getBoundingClientRect();
    var mx=(e.clientX-r.left);
    var ci=Math.round((mx-padL)/(W-padL-padR)*(n-1));
    if(ci<0)ci=0;if(ci>=n)ci=n-1;
    if(mx>=padL&&mx<=W-padR){
      canvas.style.cursor='crosshair';
      canvas.title=curve[ci].date+
        '\\n净值: ¥'+Math.round(curve[ci].equity).toLocaleString()+
        '\\n现金: ¥'+Math.round(curve[ci].cash||0).toLocaleString()+
        '\\n持仓: ¥'+Math.round(curve[ci].positions||0).toLocaleString();
    }else{
      canvas.style.cursor='default';canvas.title='';
    }
  };
  canvas.onmouseleave=function(){canvas.style.cursor='default';canvas.title='';};
}

/* ── Stock Selection Timeline Chart ── */
function gatherStockTimeline(symbol){
  var history=syncDailyPickHistory();
  var points=[];
  for(var i=history.length-1;i>=0;i--){
    var entry=history[i];
    if(!entry||!entry.result)continue;
    var combined=Array.isArray(entry.result.combinedList)?entry.result.combinedList:[];
    for(var j=0;j<combined.length;j++){
      if(combined[j].symbol===symbol){
        var item=combined[j];
        points.push({
          date:entry.createdAt,
          dateLabel:formatDailyPickTime(entry.createdAt),
          price:item.price,
          changePct:item.changePct,
          score:item.combinedScore||0,
          rank:item.rank,
          selected:!!item.selected,
          appearanceCount:item.appearanceCount||1,
          strategies:(item.selectedBy||[]).slice()
        });
        break;
      }
    }
  }
  return points;
}

function renderStockTimeline(containerId,symbol){
  var container=document.getElementById(containerId);
  if(!container)return;
  var points=gatherStockTimeline(symbol);
  if(points.length<1){
    container.innerHTML='<div style="color:var(--text-dim);font-size:12px;padding:16px;text-align:center">该股票在历史选股记录中无数据。多次运行综合选股后此处将展示价格与评分的时间轴变化。</div>';
    return;
  }

  // Build chart HTML
  var chartHtml='<div style="position:relative;height:320px;margin-bottom:8px"><canvas id="stock-timeline-canvas"></canvas></div>';
  chartHtml+='<div id="stock-timeline-legend" style="display:flex;gap:16px;flex-wrap:wrap;font-size:11px;justify-content:center;margin-bottom:8px"></div>';
  chartHtml+='<div id="stock-timeline-table" style="overflow-x:auto"></div>';
  container.innerHTML=chartHtml;

  // Draw chart after DOM update
  setTimeout(function(){drawStockTimelineChart('stock-timeline-canvas',points);},50);

  // Legend
  var legendEl=document.getElementById('stock-timeline-legend');
  if(legendEl){
    legendEl.innerHTML=
      '<span style="color:#58a6ff">━━ 价格 (¥)</span>'+
      '<span style="color:#3fb950">━━ 综合分</span>'+
      '<span>🟢 入围</span>'+
      '<span>🟡 候补</span>';
  }

  // Data table
  var tableEl=document.getElementById('stock-timeline-table');
  if(tableEl){
    var rows='';
    points.forEach(function(pt,idx){
      var priceChange='';
      if(idx>0){
        var diff=pt.price-points[idx-1].price;
        var diffPct=points[idx-1].price?((diff/points[idx-1].price)*100):0;
        priceChange='<span style="color:'+(diff>=0?'var(--green)':'var(--red)')+'">'+
          (diff>=0?'+':'')+diff.toFixed(2)+' ('+(diffPct>=0?'+':'')+diffPct.toFixed(2)+'%)</span>';
      }
      var scoreDiff='';
      if(idx>0){
        var sd=pt.score-points[idx-1].score;
        scoreDiff='<span style="color:'+(sd>=0?'var(--green)':'var(--red)')+'">'+
          (sd>=0?'+':'')+sd.toFixed(1)+'</span>';
      }
      rows+='<tr style="'+(pt.selected?'background:rgba(63,185,80,0.06)':'')+'">';
      rows+='<td style="white-space:nowrap;font-size:11px">'+esc(pt.dateLabel)+'</td>';
      rows+='<td style="font-family:monospace">¥'+Number(pt.price).toFixed(2)+'</td>';
      rows+='<td>'+priceChange+'</td>';
      rows+='<td style="font-family:monospace">'+Number(pt.score).toFixed(1)+'</td>';
      rows+='<td>'+scoreDiff+'</td>';
      rows+='<td>#'+pt.rank+'</td>';
      rows+='<td>'+(pt.selected?'<span class="badge badge-green">入围</span>':'<span class="badge badge-yellow">候补</span>')+'</td>';
      rows+='<td style="font-size:10px">'+pt.strategies.map(function(s){return '<span class="badge badge-blue" style="margin:1px">'+esc(s)+'</span>';}).join(' ')+'</td>';
      rows+='</tr>';
    });
    tableEl.innerHTML='<table style="font-size:12px;width:100%"><thead><tr><th>时间</th><th>价格</th><th>价格变化</th><th>综合分</th><th>分数变化</th><th>排名</th><th>状态</th><th>来源策略</th></tr></thead><tbody>'+rows+'</tbody></table>';
  }
}

function drawStockTimelineChart(canvasId,points){
  var canvas=document.getElementById(canvasId);
  if(!canvas||!points||points.length<1)return;
  var dpr=window.devicePixelRatio||1;
  var rect=canvas.parentElement.getBoundingClientRect();
  canvas.width=rect.width*dpr;
  canvas.height=rect.height*dpr;
  canvas.style.width=rect.width+'px';
  canvas.style.height=rect.height+'px';
  var ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  var W=rect.width,H=rect.height;
  var padL=60,padR=60,padT=24,padB=48;

  // Price range
  var prices=points.map(function(p){return p.price});
  var minPrice=Math.min.apply(null,prices);
  var maxPrice=Math.max.apply(null,prices);
  var priceMargin=(maxPrice-minPrice)*0.15||maxPrice*0.05||1;
  minPrice-=priceMargin;maxPrice+=priceMargin;

  // Score range
  var scores=points.map(function(p){return p.score});
  var minScore=Math.min.apply(null,scores);
  var maxScore=Math.max.apply(null,scores);
  var scoreMargin=(maxScore-minScore)*0.15||maxScore*0.05||5;
  minScore=Math.max(0,minScore-scoreMargin);maxScore+=scoreMargin;

  // Background
  ctx.fillStyle='#0d1117';
  ctx.fillRect(0,0,W,H);

  // Grid lines
  ctx.strokeStyle='#21262d';
  ctx.lineWidth=0.5;
  for(var i=0;i<=4;i++){
    var y=padT+(H-padT-padB)*(1-i/4);
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
    // Left axis: price
    ctx.fillStyle='#58a6ff';ctx.font='10px sans-serif';ctx.textAlign='right';
    var pv=minPrice+(maxPrice-minPrice)*(i/4);
    ctx.fillText('¥'+pv.toFixed(2),padL-4,y+3);
    // Right axis: score
    ctx.fillStyle='#3fb950';ctx.textAlign='left';
    var sv=minScore+(maxScore-minScore)*(i/4);
    ctx.fillText(sv.toFixed(1),W-padR+4,y+3);
  }

  var n=points.length;
  function xPos(idx){return n===1?(padL+(W-padL-padR)/2):(padL+(W-padL-padR)*(idx/(n-1)));}
  function yPrice(v){return padT+(H-padT-padB)*(1-(v-minPrice)/(maxPrice-minPrice));}
  function yScore(v){return padT+(H-padT-padB)*(1-(v-minScore)/(maxScore-minScore));}

  // Price line
  ctx.strokeStyle='#58a6ff';ctx.lineWidth=2;
  ctx.beginPath();
  for(var i=0;i<n;i++){
    var x=xPos(i),y=yPrice(points[i].price);
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();
  // Fill under price line
  if(n>=2){
    ctx.lineTo(xPos(n-1),H-padB);
    ctx.lineTo(xPos(0),H-padB);
    ctx.closePath();
    ctx.fillStyle='rgba(88,166,255,0.06)';
    ctx.fill();
  }

  // Score line (dashed)
  ctx.strokeStyle='#3fb950';ctx.lineWidth=2;ctx.setLineDash([6,3]);
  ctx.beginPath();
  for(var i=0;i<n;i++){
    var x=xPos(i),y=yScore(points[i].score);
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();
  ctx.setLineDash([]);

  // Data points markers
  for(var i=0;i<n;i++){
    var x=xPos(i);
    var yp=yPrice(points[i].price);
    var ys=yScore(points[i].score);

    // Vertical guide line at each point
    ctx.strokeStyle='rgba(139,148,158,0.15)';ctx.lineWidth=0.5;
    ctx.beginPath();ctx.moveTo(x,padT);ctx.lineTo(x,H-padB);ctx.stroke();

    // Price point dot
    var dotR=n<=3?6:4;
    ctx.beginPath();ctx.arc(x,yp,dotR,0,Math.PI*2);
    ctx.fillStyle='#58a6ff';ctx.fill();
    ctx.strokeStyle='#0d1117';ctx.lineWidth=1.5;ctx.stroke();

    // Score point dot
    ctx.beginPath();ctx.arc(x,ys,dotR,0,Math.PI*2);
    ctx.fillStyle='#3fb950';ctx.fill();
    ctx.strokeStyle='#0d1117';ctx.lineWidth=1.5;ctx.stroke();

    // Selection marker (triangle above price point for 入围)
    if(points[i].selected){
      ctx.fillStyle='#3fb950';
      ctx.beginPath();
      ctx.moveTo(x,yp-12);
      ctx.lineTo(x-5,yp-20);
      ctx.lineTo(x+5,yp-20);
      ctx.closePath();
      ctx.fill();
      // Small "买" text
      ctx.fillStyle='#3fb950';ctx.font='bold 9px sans-serif';ctx.textAlign='center';
      ctx.fillText('买',x,yp-22);
    }

    // Rank label near price point
    ctx.fillStyle='#8b949e';ctx.font='9px sans-serif';ctx.textAlign='center';
    ctx.fillText('#'+points[i].rank,x,yp+14);

    // Show value labels for single point or sparse charts
    if(n<=3){
      ctx.fillStyle='#58a6ff';ctx.font='bold 11px sans-serif';ctx.textAlign='center';
      ctx.fillText('¥'+Number(points[i].price).toFixed(2),x,yp-((points[i].selected)?34:10));
      ctx.fillStyle='#3fb950';ctx.font='bold 11px sans-serif';
      ctx.fillText(Number(points[i].score).toFixed(1),x,ys-(n===1?10:10));
    }
  }

  // X-axis date labels
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='center';
  var labelStep=Math.max(1,Math.floor(n/8));
  for(var i=0;i<n;i+=labelStep){
    var x=xPos(i);
    var dl=points[i].dateLabel||'';
    var short=dl.replace(/^\\d{4}\\//,'').replace(/:\\d{2}$/,'');
    if(n===1){
      ctx.fillText(dl,x,H-padB+14);
    }else{
      ctx.save();
      ctx.translate(x,H-padB+8);
      ctx.rotate(-Math.PI/6);
      ctx.textAlign='right';
      ctx.fillText(short,0,0);
      ctx.restore();
    }
  }
  // Always show last label (multi-point)
  if(n>1&&(n-1)%labelStep!==0){
    var x=xPos(n-1);
    var dl=points[n-1].dateLabel||'';
    var short=dl.replace(/^\\d{4}\\//,'').replace(/:\\d{2}$/,'');
    ctx.save();
    ctx.translate(x,H-padB+8);
    ctx.rotate(-Math.PI/6);
    ctx.textAlign='right';
    ctx.fillText(short,0,0);
    ctx.restore();
  }

  // Axis labels
  ctx.fillStyle='#58a6ff';ctx.font='11px sans-serif';ctx.textAlign='center';
  ctx.fillText('价格 (¥)',padL/2,12);
  ctx.fillStyle='#3fb950';
  ctx.fillText('综合分',W-padR/2,12);

  // Price change annotation: first → last
  if(n>=2){
    var firstP=points[0].price,lastP=points[n-1].price;
    var totalDiff=lastP-firstP;
    var totalPct=firstP?(totalDiff/firstP*100):0;
    var changeColor=totalDiff>=0?'#3fb950':'#f85149';
    ctx.fillStyle=changeColor;ctx.font='bold 11px sans-serif';ctx.textAlign='right';
    ctx.fillText((totalDiff>=0?'+':'')+'¥'+totalDiff.toFixed(2)+' ('+(totalPct>=0?'+':'')+totalPct.toFixed(2)+'%)',W-padR,H-4);
  }else if(n===1){
    // Single point: show current day change
    var cp=points[0].changePct;
    var cpColor=cp>=0?'#3fb950':'#f85149';
    ctx.fillStyle=cpColor;ctx.font='bold 11px sans-serif';ctx.textAlign='center';
    ctx.fillText('当日涨跌 '+(cp>=0?'+':'')+Number(cp).toFixed(2)+'%',W/2,H-4);
  }

  // Tooltip on hover
  canvas.onmousemove=function(e){
    var r=canvas.getBoundingClientRect();
    var mx=(e.clientX-r.left);
    var closestIdx=-1,closestDist=Infinity;
    for(var i=0;i<n;i++){
      var d=Math.abs(mx-xPos(i));
      if(d<closestDist){closestDist=d;closestIdx=i;}
    }
    if(closestIdx>=0&&closestDist<30){
      canvas.style.cursor='crosshair';
      canvas.title=points[closestIdx].dateLabel+
        '\\n价格: ¥'+Number(points[closestIdx].price).toFixed(2)+
        '\\n涨跌: '+(points[closestIdx].changePct>=0?'+':'')+Number(points[closestIdx].changePct).toFixed(2)+'%'+
        '\\n综合分: '+Number(points[closestIdx].score).toFixed(1)+
        '\\n排名: #'+points[closestIdx].rank+
        (points[closestIdx].selected?'\\n✅ 入围':'\\n候补')+
        '\\n策略: '+points[closestIdx].strategies.join(', ');
    }else{
      canvas.style.cursor='default';
      canvas.title='';
    }
  };
  canvas.onmouseleave=function(){canvas.style.cursor='default';canvas.title='';};
}

/* ══════ POOL OPTIMIZER ══════ */
`;
}
