/** Investment Planner — Top-N 策略组合回测 (combo backtest) */
export function getComboBacktestJs(): string {
  return `
/* ══════ 策略组合回测 ══════ */
var _comboResult=null;
var _comboCompareResults=null;
var COMBO_COLORS=['#58a6ff','#3fb950','#f85149','#d29922','#bc8cff','#db6d28','#f778ba','#79c0ff','#7ee787','#ffa657'];
var ALLOC_MODE_LABELS={
  equal:'等权分配',
  risk_parity:'风险平价',
  inverse_vol:'波动率倒数',
  max_sharpe:'最大夏普',
  min_variance:'最小方差',
  target_vol:'目标波动率',
  max_diversification:'最大分散化',
  custom:'自定义权重'
};

function comboParams(){
  var p = {
    top_n: parseInt(document.getElementById('combo-top-n').value)||10,
    allocation_mode: document.getElementById('combo-alloc-mode').value||'equal',
    rebalance_freq: document.getElementById('combo-rebalance').value||'monthly',
    period_days: parseInt(document.getElementById('combo-period').value)||252,
    initial_capital: parseFloat(document.getElementById('combo-capital').value)||1000000
  };
  if(p.allocation_mode==='custom'){
    var ws=collectComboCustomWeights();
    if(ws)p.custom_weights=ws;
  }
  return p;
}

function setComboLoading(loading){
  var btn1=document.getElementById('btn-combo-run');
  var btn2=document.getElementById('btn-combo-compare');
  if(btn1)btn1.disabled=loading;
  if(btn2)btn2.disabled=loading;
  var st=document.getElementById('combo-status');
  if(loading){
    st.innerHTML='<span class="spinner-sm"></span> 正在运行组合回测，请稍候（可能需要1-3分钟）...';
  }else{
    st.textContent='';
  }
}

function runComboBacktest(){
  var p=comboParams();
  setComboLoading(true);
  _comboProgressTimer=setInterval(pollComboProgress,3000);
  document.getElementById('combo-results').style.display='none';
  document.getElementById('combo-compare-results').style.display='none';
  api('/quant/admin/api/strategy-fitness/portfolio-backtest',{
    method:'POST',
    body:JSON.stringify(p),
    _timeout:600000
  }).then(function(d){
    stopComboProgress();
    setComboLoading(false);
    if(!d||d.error){toast('\\u274c '+(d&&d.error||'组合回测失败'));return;}
    _comboResult=d;
    renderComboResults(d);
  }).catch(function(e){
    stopComboProgress();
    setComboLoading(false);
    toast('\\u274c '+e.message);
  });
}

function runComboCompare(){
  var p=comboParams();
  setComboLoading(true);
  document.getElementById('combo-results').style.display='none';
  document.getElementById('combo-compare-results').style.display='none';
  var st=document.getElementById('combo-status');
  st.innerHTML='<span class="spinner-sm"></span> 正在对比多种配权模式，请耐心等待（约3-10分钟）...';
  api('/quant/admin/api/strategy-fitness/portfolio-backtest/compare',{
    method:'POST',
    body:JSON.stringify({
      top_n:p.top_n,
      rebalance_freq:p.rebalance_freq,
      period_days:p.period_days,
      initial_capital:p.initial_capital,
      modes:['equal','risk_parity','max_sharpe','min_variance','inverse_vol']
    }),
    _timeout:1800000
  }).then(function(d){
    setComboLoading(false);
    if(!d||d.error){toast('\\u274c '+(d&&d.error||'对比失败'));return;}
    _comboCompareResults=d.comparisons||d;
    renderComboCompare(_comboCompareResults,p.initial_capital);
  }).catch(function(e){
    setComboLoading(false);
    toast('\\u274c '+e.message);
  });
}

function renderComboResults(d){
  document.getElementById('combo-results').style.display='block';
  var m=d.combined_metrics||{};

  // KPI cards
  var retColor=m.total_return>=0?'var(--green)':'var(--red)';
  var kpis='';
  kpis+=kpiHtml((m.total_return>=0?'+':'')+m.total_return+'%','总收益');
  kpis+=kpiHtml((m.annualized_return>=0?'+':'')+m.annualized_return+'%','年化收益');
  kpis+=kpiHtml(m.sharpe_ratio,'夏普比率');
  kpis+=kpiHtml('-'+m.max_drawdown+'%','最大回撤');
  kpis+=kpiHtml(m.win_rate+'%','胜率');
  kpis+=kpiHtml(m.calmar_ratio,'Calmar比率');
  kpis+=kpiHtml(d.top_n+'个','策略数');
  kpis+=kpiHtml(ALLOC_MODE_LABELS[d.allocation_mode]||d.allocation_mode,'配权模式');
  document.getElementById('combo-kpis').innerHTML=kpis;

  // Equity curve — combined + sub-strategies
  var curves=[];
  var lineInfos=[];

  // Combined curve first
  var comboCurve=(d.equity_curve||[]).map(function(p){return {date:p.date,equity:p.value}});
  if(comboCurve.length>0){
    curves.push(comboCurve);
    lineInfos.push({label:'组合净值',color:'#fff'});
  }
  // Sub-strategy curves
  var subCurves=d.sub_equity_curves||{};
  var subNames=d.strategies_used||[];
  subNames.forEach(function(name,i){
    var sc=subCurves[name];
    if(sc&&sc.length>0){
      curves.push(sc.map(function(p){return {date:p.date,equity:p.value}}));
      lineInfos.push({label:name,color:COMBO_COLORS[i%COMBO_COLORS.length]});
    }
  });
  if(curves.length>0){
    drawMultiEquityCurve('combo-equity-canvas',curves,lineInfos,1.0);
  }

  // Legend
  var legHtml=lineInfos.map(function(l){
    return '<div class="bt-legend-item"><div class="bt-legend-dot" style="background:'+l.color+'"></div>'+esc(l.label)+'</div>';
  }).join('');
  document.getElementById('combo-legend').innerHTML=legHtml;

  // Sub-strategy table
  var subs=d.sub_results||[];
  var tbody='';
  subs.forEach(function(s,i){
    var arColor=s.annual_return>=0?'var(--green)':'var(--red)';
    tbody+='<tr>';
    tbody+='<td><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:'+COMBO_COLORS[i%COMBO_COLORS.length]+';margin-right:4px;vertical-align:middle"></span>'+esc(s.name)+'</td>';
    tbody+='<td>'+pct(s.weight*100)+'%</td>';
    tbody+='<td style="color:'+arColor+'">'+(s.annual_return>=0?'+':'')+s.annual_return+'%</td>';
    tbody+='<td style="color:var(--red)">-'+s.max_drawdown+'%</td>';
    tbody+='<td>'+s.sharpe+'</td>';
    tbody+='<td>'+s.total_trades+'</td>';
    tbody+='<td>\\u00a5'+numInt(s.total_fees)+'</td>';
    tbody+='</tr>';
  });
  document.getElementById('combo-sub-tbody').innerHTML=tbody;

  // Contributions pie chart (simple bar chart fallback)
  var contribs=d.contributions||[];
  var contribHtml='';
  contribs.sort(function(a,b){return Math.abs(b.contribution_pct)-Math.abs(a.contribution_pct)});
  contribs.forEach(function(c,i){
    var w=Math.min(Math.abs(c.contribution_pct),100);
    var color=c.contribution_pct>=0?'var(--green)':'var(--red)';
    contribHtml+='<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px">';
    contribHtml+='<span style="min-width:120px">'+esc(c.strategy_id)+'</span>';
    contribHtml+='<div style="flex:1;background:var(--bg3);border-radius:3px;height:16px;position:relative">';
    contribHtml+='<div style="width:'+w+'%;height:100%;background:'+color+';border-radius:3px;opacity:0.6"></div>';
    contribHtml+='</div>';
    contribHtml+='<span style="min-width:60px;text-align:right;color:'+color+'">'+c.contribution_pct+'%</span>';
    contribHtml+='</div>';
  });
  document.getElementById('combo-contributions').innerHTML=contribHtml;

  // Monthly returns heatmap
  var monthly=d.monthly_returns||[];
  if(monthly.length>0){
    var mHtml='<div style="display:flex;flex-wrap:wrap;gap:3px;align-items:flex-end">';
    monthly.forEach(function(mr){
      var h=Math.min(Math.abs(mr['return'])*3,60);
      var color=mr['return']>=0?'var(--green)':'var(--red)';
      mHtml+='<div style="text-align:center;min-width:40px">';
      mHtml+='<div style="height:'+Math.max(h,4)+'px;background:'+color+';opacity:0.6;border-radius:2px 2px 0 0;margin-bottom:2px"></div>';
      mHtml+='<div style="font-size:9px;color:var(--text-dim)">'+mr.month.substring(5)+'</div>';
      mHtml+='<div style="font-size:10px;color:'+color+'">'+(mr['return']>=0?'+':'')+mr['return']+'%</div>';
      mHtml+='</div>';
    });
    mHtml+='</div>';
    document.getElementById('combo-monthly').innerHTML=mHtml;
  }

  // Correlation matrix
  var corr=d.correlation_matrix;
  if(corr){
    var names=Object.keys(corr);
    var corrHtml='<table style="font-size:11px"><thead><tr><th></th>';
    names.forEach(function(n){corrHtml+='<th style="max-width:80px;overflow:hidden;text-overflow:ellipsis">'+esc(n.substring(0,12))+'</th>'});
    corrHtml+='</tr></thead><tbody>';
    names.forEach(function(r){
      corrHtml+='<tr><td style="font-weight:600;max-width:80px;overflow:hidden;text-overflow:ellipsis">'+esc(r.substring(0,12))+'</td>';
      names.forEach(function(c){
        var v=corr[r]&&corr[r][c]!==undefined?corr[r][c]:0;
        var bg=v>0.7?'rgba(248,81,73,.2)':v>0.3?'rgba(210,153,34,.15)':'rgba(63,185,80,.1)';
        corrHtml+='<td style="text-align:center;background:'+bg+'">'+v.toFixed(2)+'</td>';
      });
      corrHtml+='</tr>';
    });
    corrHtml+='</tbody></table>';
    document.getElementById('combo-correlation').innerHTML=corrHtml;
  }else{
    document.getElementById('combo-correlation').innerHTML='<span style="color:var(--text-dim)">策略数不足，无法计算相关性矩阵</span>';
  }

  // Drawdown curve
  var ddCurve=d.drawdown_curve||[];
  if(ddCurve.length>1){
    drawComboDrawdown('combo-dd-canvas',ddCurve);
  }

  // Errors
  var errs=d.errors||[];
  if(errs.length>0){
    document.getElementById('combo-errors').innerHTML='<div style="color:var(--yellow);font-size:12px;margin-top:8px">\\u26a0\\ufe0f 部分策略回测失败: '+errs.map(function(e){return esc(e)}).join('; ')+'</div>';
  }else{
    document.getElementById('combo-errors').innerHTML='';
  }
}

function drawComboDrawdown(canvasId,ddData){
  var canvas=document.getElementById(canvasId);
  if(!canvas||ddData.length<2)return;
  var dpr=window.devicePixelRatio||1;
  var rect=canvas.parentElement.getBoundingClientRect();
  canvas.width=rect.width*dpr;
  canvas.height=rect.height*dpr;
  canvas.style.width=rect.width+'px';
  canvas.style.height=rect.height+'px';
  var ctx=canvas.getContext('2d');
  ctx.scale(dpr,dpr);
  var W=rect.width,H=rect.height;
  var padL=50,padR=16,padT=8,padB=24;

  var minDD=0;
  ddData.forEach(function(p){if(p.drawdown<minDD)minDD=p.drawdown});
  minDD=minDD*1.1;
  if(minDD>-0.5)minDD=-0.5;

  ctx.fillStyle='#0d1117';
  ctx.fillRect(0,0,W,H);

  // Grid
  ctx.strokeStyle='#21262d';ctx.lineWidth=0.5;
  for(var i=0;i<=4;i++){
    var y=padT+(H-padT-padB)*(i/4);
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(W-padR,y);ctx.stroke();
    ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='right';
    var val=minDD*(i/4);
    ctx.fillText(val.toFixed(1)+'%',padL-4,y+3);
  }

  // Drawdown area
  ctx.beginPath();
  ctx.strokeStyle='#f85149';ctx.lineWidth=1;
  for(var j=0;j<ddData.length;j++){
    var x=padL+(W-padL-padR)*(j/(ddData.length-1));
    var y=padT+(H-padT-padB)*(ddData[j].drawdown/minDD);
    if(j===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();
  // Fill
  ctx.lineTo(padL+(W-padL-padR),padT);
  ctx.lineTo(padL,padT);
  ctx.closePath();
  ctx.fillStyle='rgba(248,81,73,0.1)';
  ctx.fill();

  // X labels
  ctx.fillStyle='#8b949e';ctx.font='10px sans-serif';ctx.textAlign='center';
  var step=Math.max(1,Math.floor(ddData.length/6));
  for(var j=0;j<ddData.length;j+=step){
    var x=padL+(W-padL-padR)*(j/(ddData.length-1));
    ctx.fillText((ddData[j].date||'').substring(5),x,H-padB+14);
  }
}

function renderComboCompare(comparisons,capital){
  document.getElementById('combo-compare-results').style.display='block';

  var modes=Object.keys(comparisons);
  // Summary table
  var thHtml='<tr><th>配权模式</th><th>总收益</th><th>年化收益</th><th>夏普比率</th><th>最大回撤</th><th>胜率</th><th>Calmar</th><th>策略数</th></tr>';
  var tdHtml='';
  var bestMode='';var bestSharpe=-999;
  modes.forEach(function(mode){
    var r=comparisons[mode];
    if(!r||r.error)return;
    var m=r.combined_metrics||{};
    if(m.sharpe_ratio>bestSharpe){bestSharpe=m.sharpe_ratio;bestMode=mode;}
  });
  modes.forEach(function(mode){
    var r=comparisons[mode];
    if(!r||r.error){
      tdHtml+='<tr><td>'+(ALLOC_MODE_LABELS[mode]||mode)+'</td><td colspan="7" style="color:var(--red)">'+(r?r.error:'失败')+'</td></tr>';
      return;
    }
    var m=r.combined_metrics||{};
    var isBest=mode===bestMode;
    var retColor=m.total_return>=0?'var(--green)':'var(--red)';
    tdHtml+='<tr'+(isBest?' style="background:rgba(63,185,80,.08)"':'')+'>';
    tdHtml+='<td>'+(ALLOC_MODE_LABELS[mode]||mode)+(isBest?' <span class="badge badge-green">\\u6700\\u4f18</span>':'')+'</td>';
    tdHtml+='<td style="color:'+retColor+';font-weight:600">'+(m.total_return>=0?'+':'')+m.total_return+'%</td>';
    tdHtml+='<td style="color:'+retColor+'">'+(m.annualized_return>=0?'+':'')+m.annualized_return+'%</td>';
    tdHtml+='<td>'+m.sharpe_ratio+'</td>';
    tdHtml+='<td style="color:var(--red)">-'+m.max_drawdown+'%</td>';
    tdHtml+='<td>'+m.win_rate+'%</td>';
    tdHtml+='<td>'+m.calmar_ratio+'</td>';
    tdHtml+='<td>'+r.top_n+'</td>';
    tdHtml+='</tr>';
  });
  document.getElementById('combo-compare-table').innerHTML='<thead>'+thHtml+'</thead><tbody>'+tdHtml+'</tbody>';

  // Multi-curve comparison chart
  var curves=[];var labels=[];
  modes.forEach(function(mode,i){
    var r=comparisons[mode];
    if(!r||r.error||!r.equity_curve)return;
    curves.push(r.equity_curve.map(function(p){return {date:p.date,equity:p.value}}));
    labels.push({label:ALLOC_MODE_LABELS[mode]||mode,color:COMBO_COLORS[i%COMBO_COLORS.length]});
  });
  if(curves.length>0){
    drawMultiEquityCurve('combo-compare-canvas',curves,labels,capital);
    var legHtml=labels.map(function(l){
      return '<div class="bt-legend-item"><div class="bt-legend-dot" style="background:'+l.color+'"></div>'+esc(l.label)+'</div>';
    }).join('');
    document.getElementById('combo-compare-legend').innerHTML=legHtml;
  }

  // Best mode recommendation
  if(bestMode){
    var br=comparisons[bestMode];
    var bm=br.combined_metrics||{};
    document.getElementById('combo-compare-recommendation').innerHTML=
      '<div style="background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.3);border-radius:8px;padding:12px;margin-top:12px">'+
      '<strong>\\ud83c\\udfc6 推荐配权模式: '+(ALLOC_MODE_LABELS[bestMode]||bestMode)+'</strong><br>'+
      '<span style="font-size:13px">年化收益 '+(bm.annualized_return>=0?'+':'')+bm.annualized_return+'% | 夏普 '+bm.sharpe_ratio+' | 最大回撤 -'+bm.max_drawdown+'% | 胜率 '+bm.win_rate+'%</span>'+
      '</div>';
  }
}

/* ── Load fitness top-N for the combo tab ── */
var _comboTopStrategies=[];
var _comboProgressTimer=null;

function pollComboProgress(){
  api('/quant/admin/api/strategy-fitness/portfolio-backtest/status').then(function(d){
    if(!d)return;
    var st=document.getElementById('combo-status');
    if(!st)return;
    var txt='';
    if(d.phase)txt+=d.phase;
    if(d.completed!==undefined&&d.total)txt+=' ('+d.completed+'/'+d.total+')';
    if(d.elapsed_seconds)txt+=' '+d.elapsed_seconds+'s';
    if(txt)st.innerHTML='<span class="spinner-sm"></span> '+esc(txt);
    if(d.status==='done'||d.status==='error')stopComboProgress();
  }).catch(function(){});
}
function stopComboProgress(){
  if(_comboProgressTimer){clearInterval(_comboProgressTimer);_comboProgressTimer=null;}
}

/* ── Custom weight editor ── */
function onComboAllocModeChange(){
  var mode=document.getElementById('combo-alloc-mode').value;
  var panel=document.getElementById('combo-custom-weights-panel');
  if(mode==='custom'){
    panel.style.display='block';
    buildComboWeightEditor();
  }else{
    panel.style.display='none';
  }
}
(function(){
  var sel=document.getElementById('combo-alloc-mode');
  if(sel)sel.addEventListener('change',onComboAllocModeChange);
})();

function buildComboWeightEditor(){
  var container=document.getElementById('combo-weight-sliders');
  if(!container)return;
  var strats=_comboTopStrategies;
  if(!strats.length){container.innerHTML='<span style="color:var(--text-dim)">请先加载策略排名</span>';return;}
  var topN=parseInt(document.getElementById('combo-top-n').value)||10;
  var sliced=strats.slice(0,topN);
  var equalW=Math.round(10000/sliced.length)/100;
  var html='';
  sliced.forEach(function(s,i){
    var name=s.strategy_name||s.strategy_id||('策略'+(i+1));
    html+='<div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">';
    html+='<span style="min-width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="'+esc(name)+'">'+esc(name.substring(0,14))+'</span>';
    html+='<input type="range" min="0" max="100" step="0.5" value="'+equalW+'" class="combo-wt-slider" data-idx="'+i+'" oninput="updateComboWeightTotal()" style="flex:1">';
    html+='<input type="number" min="0" max="100" step="0.5" value="'+equalW+'" class="combo-wt-num" data-idx="'+i+'" oninput="syncComboSlider(this)" style="width:55px;font-size:11px">';
    html+='<span style="font-size:11px">%</span>';
    html+='</div>';
  });
  container.innerHTML=html;
  updateComboWeightTotal();
}
function syncComboSlider(numInput){
  var idx=numInput.dataset.idx;
  var slider=document.querySelector('.combo-wt-slider[data-idx="'+idx+'"]');
  if(slider)slider.value=numInput.value;
  updateComboWeightTotal();
}
function updateComboWeightTotal(){
  var nums=document.querySelectorAll('.combo-wt-num');
  var total=0;
  nums.forEach(function(n){total+=parseFloat(n.value)||0});
  var el=document.getElementById('combo-weight-total');
  if(el){
    el.textContent=total.toFixed(1)+'%';
    el.style.color=Math.abs(total-100)<0.5?'var(--green)':'var(--red)';
  }
}
function resetComboCustomWeights(){
  var nums=document.querySelectorAll('.combo-wt-num');
  var sliders=document.querySelectorAll('.combo-wt-slider');
  var eq=nums.length>0?Math.round(10000/nums.length)/100:0;
  nums.forEach(function(n){n.value=eq});
  sliders.forEach(function(s){s.value=eq});
  updateComboWeightTotal();
}
function collectComboCustomWeights(){
  var nums=document.querySelectorAll('.combo-wt-num');
  if(!nums.length)return null;
  var ws=[];
  nums.forEach(function(n){ws.push(parseFloat(n.value)||0)});
  // Normalise to sum=1
  var sum=0;ws.forEach(function(w){sum+=w});
  if(sum<=0)return null;
  return ws.map(function(w){return Math.round(w/sum*10000)/10000});
}

/* ── Paper Trading Integration ── */
function applyComboToPaper(){
  if(!_comboResult||!_comboResult.sub_results){
    toast('\\u274c 请先运行组合回测');return;
  }
  var btn=document.getElementById('btn-combo-paper');
  var statusEl=document.getElementById('combo-paper-status');
  var resultEl=document.getElementById('combo-paper-result');
  var dryRun=document.getElementById('combo-paper-dry').checked;
  var executePct=parseInt(document.getElementById('combo-paper-pct').value)||100;
  var createCond=document.getElementById('combo-paper-cond').checked;

  // Build allocations from sub_results (strategy name + weight)
  var allocations=_comboResult.sub_results.map(function(s){
    return {
      strategy_name: s.name,
      weight: s.weight,
      latest_signals: s.latest_signals||[]
    };
  });

  btn.disabled=true;
  statusEl.innerHTML='<span class="spinner-sm"></span> '+(dryRun?'试运行中...':'正在生成模拟盘订单...');
  resultEl.style.display='none';

  api('/quant/admin/api/strategy-fitness/portfolio-backtest/apply-paper',{
    method:'POST',
    body:JSON.stringify({
      allocations:allocations,
      dry_run:dryRun,
      execute_pct:executePct,
      create_conditional_orders:createCond,
      trailing_stop_pct:5
    }),
    _timeout:60000
  }).then(function(d){
    btn.disabled=false;
    statusEl.textContent='';
    resultEl.style.display='block';
    if(!d||d.error){
      resultEl.innerHTML='<div style="color:var(--red)">\\u274c '+(d&&d.error||'执行失败')+'</div>';
      return;
    }
    var html='<div style="background:rgba(63,185,80,.08);border:1px solid rgba(63,185,80,.3);border-radius:8px;padding:12px">';
    if(dryRun){
      html+='<strong>\\ud83d\\udccb 试运行结果</strong><br>';
      html+='<span style="font-size:12px">'+esc(JSON.stringify(d,null,2).substring(0,500))+'</span>';
      html+='<br><button class="btn btn-primary" style="margin-top:8px" onclick="document.getElementById(\\'combo-paper-dry\\').checked=false;applyComboToPaper()">\\u2705 确认执行</button>';
    }else{
      html+='<strong>\\u2705 模拟盘订单已生成</strong><br>';
      html+='<span style="font-size:12px">'+esc(JSON.stringify(d,null,2).substring(0,500))+'</span>';
    }
    html+='</div>';
    resultEl.innerHTML=html;
  }).catch(function(e){
    btn.disabled=false;
    statusEl.textContent='';
    resultEl.style.display='block';
    resultEl.innerHTML='<div style="color:var(--red)">\\u274c '+esc(e.message)+'</div>';
  });
}

function loadComboTopStrategies(){
  api('/quant/admin/api/strategy-fitness/global').then(function(d){
    var strats=d.strategies||[];
    _comboTopStrategies=strats;
    if(!strats.length){
      document.getElementById('combo-top-strategies').innerHTML='<span style="color:var(--text-dim)">暂无全局策略排名数据。请先在管理面板运行 Fitness 分析。</span>';
      return;
    }
    var html='<div style="display:flex;flex-wrap:wrap;gap:6px">';
    strats.forEach(function(s,i){
      html+='<span class="badge" style="font-size:11px;padding:3px 8px;background:rgba(88,166,255,.1);border:1px solid rgba(88,166,255,.3)">'+
        '#'+(s.rank||i+1)+' '+esc(s.strategy_name||s.strategy_id)+
        ' <span style="color:var(--text-dim)">('+numInt(s.total_score)+'分)</span></span>';
    });
    html+='</div>';
    document.getElementById('combo-top-strategies').innerHTML=html;
    // Update top_n max
    var topNInput=document.getElementById('combo-top-n');
    if(topNInput)topNInput.max=strats.length;
  }).catch(function(){
    document.getElementById('combo-top-strategies').innerHTML='<span style="color:var(--text-dim)">加载策略排名失败</span>';
  });
}
`;
}
