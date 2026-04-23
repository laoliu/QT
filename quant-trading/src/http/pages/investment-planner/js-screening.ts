/** Investment Planner — stock screening, rebalancing, signals, milestones, and monitoring */
export function getScreeningJs(): string {
  return `
/* Sync plan portfolio with actual broker positions in live mode */
function _getSyncedPlan(plan){
  var mode=window._currentViewMode||'paper';
  if(mode!=='live')return Promise.resolve(plan);
  return api('/quant/api/portfolio?mode=live',{_timeout:15000}).then(function(d){
    if(!d||!d.data||!d.data.positions||!d.data.positions.length)return Object.assign({},plan,{portfolio:[]});
    var liveSymbols={};
    d.data.positions.forEach(function(p){liveSymbols[p.symbol]=p});
    var filtered=plan.portfolio.filter(function(p){return !!liveSymbols[p.symbol]});
    return Object.assign({},plan,{portfolio:filtered});
  }).catch(function(){return Object.assign({},plan,{portfolio:[]})});
}

function refreshPortfolio(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    // Auto-load latest plan from history before giving up
    api('/quant/admin/api/plan-history').then(function(d){
      if(d.entries&&d.entries.length>0){
        var latest=d.entries[0];
        api('/quant/admin/api/plan-history?id='+encodeURIComponent(latest.id)).then(function(r){
          if(r.status==='ok'&&r.entry&&r.entry.fullPlan){
            currentPlan=r.entry.fullPlan;
            currentPlanVersion=r.entry.version||null;
            try{sessionStorage.setItem('openclaw-plan-cache',JSON.stringify(currentPlan))}catch(e){}
            refreshPortfolio();
          }else{
            document.getElementById('portfolio-kpis').innerHTML='<div style="width:100%;text-align:center;padding:40px 20px"><div style="font-size:48px;margin-bottom:12px">⚠️</div><div style="font-size:16px;margin-bottom:16px;color:var(--text-dim)">请先生成投资计划</div><button class="btn" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">📋 前往制定计划</button></div>';
          }
        }).catch(function(){
          document.getElementById('portfolio-kpis').innerHTML='<div style="width:100%;text-align:center;padding:40px 20px"><div style="font-size:48px;margin-bottom:12px">⚠️</div><div style="font-size:16px;margin-bottom:16px;color:var(--text-dim)">请先生成投资计划</div><button class="btn" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">📋 前往制定计划</button></div>';
        });
      }else{
        document.getElementById('portfolio-kpis').innerHTML='<div style="width:100%;text-align:center;padding:40px 20px"><div style="font-size:48px;margin-bottom:12px">⚠️</div><div style="font-size:16px;margin-bottom:16px;color:var(--text-dim)">请先生成投资计划</div><button class="btn" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">📋 前往制定计划</button></div>';
      }
    }).catch(function(){
      document.getElementById('portfolio-kpis').innerHTML='<div style="width:100%;text-align:center;padding:40px 20px"><div style="font-size:48px;margin-bottom:12px">⚠️</div><div style="font-size:16px;margin-bottom:16px;color:var(--text-dim)">请先生成投资计划</div><button class="btn" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">📋 前往制定计划</button></div>';
    });
    return;
  }
  toast('🔄 刷新持仓行情…');
  var _mode=window._currentViewMode||'paper';
  _getSyncedPlan(currentPlan).then(function(syncedPlan){
  api('/quant/admin/api/investment-monitor',{method:'POST',body:JSON.stringify({action:'monitor',plan:syncedPlan,mode:_mode}),_timeout:60000}).then(function(d){
    var s=d.summary;
    _lastPortfolioSignals[_mode]=d.signals||[];
    document.getElementById('portfolio-kpis').innerHTML=[
      kpiHtml('¥'+numInt(s.totalCost),'总成本'),
      kpiHtml('¥'+numInt(s.totalValue),'总市值'),
      kpiHtml('<span style="color:'+pctColor(s.totalPnl)+'">¥'+numInt(s.totalPnl)+'</span>','总盈亏'),
      kpiHtml('<span style="color:'+pctColor(s.totalPnlPct)+'">'+pct(s.totalPnlPct)+'</span>','收益率'),
      kpiHtml(s.positionCount,'持仓数'),
      kpiClickable(s.signalCount,'活跃信号','showSignalPopup()'),
      kpiClickable('<span style="color:var(--red)">'+s.urgentSignals+'</span>','紧急信号','showSignalPopup("紧急")'),
    ].join('');

    var tbody=document.querySelector('#portfolio-table tbody');
    tbody.innerHTML=(d.positions||[]).map(function(p){
      var statusBadge='<span class="badge badge-green">正常</span>';
      if(p.pnlPct<=-8)statusBadge='<span class="badge badge-red">⚠ 接近止损</span>';
      else if(p.pnlPct<0)statusBadge='<span class="badge badge-yellow">浮亏</span>';
      else if(p.pnlPct>=20)statusBadge='<span class="badge badge-blue">🎯 接近止盈</span>';
      return '<tr>'+
        '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(p.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(p.symbol)+'</a></td>'+
        '<td>'+esc(p.name)+'</td>'+
        '<td>'+numInt(p.shares)+'</td>'+
        '<td>'+num(p.buyPrice)+'</td>'+
        '<td>'+num(p.currentPrice)+'</td>'+
        '<td>¥'+numInt(p.currentValue)+'</td>'+
        '<td style="color:'+pctColor(p.pnl)+'">¥'+numInt(p.pnl)+'</td>'+
        '<td style="color:'+pctColor(p.pnlPct)+'">'+pct(p.pnlPct)+'</td>'+
        '<td style="color:'+pctColor(p.changePct)+'">'+pct(p.changePct)+'</td>'+
        '<td style="color:var(--red)">'+num(p.stopLoss)+'</td>'+
        '<td style="color:var(--green)">'+num(p.takeProfit)+'</td>'+
        '<td>'+statusBadge+'</td>'+
        '</tr>';
    }).join('')||'<tr><td colspan="12" style="text-align:center;color:var(--text-dim)">暂无持仓数据</td></tr>';

    // Store signals (per mode) with dedup – skip if same symbol+type within 5min
    if(d.signals&&d.signals.length){
      var _m=window._currentViewMode||'paper';
      var DEDUP_MS=5*60*1000;
      d.signals.forEach(function(s){
        s.timestamp=d.timestamp;s._mode=_m;
        var dup=signalHistory[_m].some(function(h){
          return h.symbol===s.symbol&&h.type===s.type&&(new Date(s.timestamp).getTime()-new Date(h.timestamp).getTime()<DEDUP_MS);
        });
        if(!dup){signalHistory[_m].unshift(s)}
      });
      if(signalHistory[_m].length>100)signalHistory[_m]=signalHistory[_m].slice(0,100);
    }
    toast('✅ 持仓已刷新');
  });
  });
}

function showSignalPopup(filterUrgency){
  var _mode=window._currentViewMode||'paper';
  var signals=_lastPortfolioSignals[_mode]||[];
  if(filterUrgency)signals=signals.filter(function(s){return s.urgency===filterUrgency});
  var existing=document.getElementById('signal-popup-overlay');
  if(existing)existing.remove();
  var overlay=document.createElement('div');
  overlay.id='signal-popup-overlay';
  overlay.style.cssText='position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.6);z-index:9999;display:flex;align-items:center;justify-content:center';
  overlay.onclick=function(e){if(e.target===overlay)overlay.remove()};
  var modeLabel=_mode==='live'?'实盘':'模拟盘';
  var title=filterUrgency?modeLabel+' 紧急信号 ('+signals.length+')':modeLabel+' 活跃信号 ('+signals.length+')';
  var content;
  if(!signals.length){
    content='<div style="text-align:center;padding:40px;color:var(--text-dim)">✅ 当前无'+(filterUrgency||'')+'信号</div>';
  }else{
    content=signals.map(function(s){
      var icon=s.type==='STOP_LOSS'?'🛑':s.type==='TAKE_PROFIT'?'💰':s.type==='VOLUME_SPIKE'||s.type==='VOLUME_EXTREME'?'📊':s.type==='CHURNING_ALERT'||s.type==='CHURNING_PATTERN'?'🔄':s.type==='DISTRIBUTION_ALERT'||s.type==='DISTRIBUTION_PATTERN'?'📤':'⚠️';
      var urgBadge='<span class="badge badge-'+(s.urgency==='紧急'?'red':s.urgency==='重要'?'orange':'yellow')+'">'+esc(s.urgency)+'</span>';
      var actionBadge=s.action==='卖出'?'<span class="badge badge-red">'+esc(s.action)+'</span>':
                      s.action==='减仓'?'<span class="badge badge-orange">'+esc(s.action)+'</span>':
                      '<span class="badge badge-yellow">'+esc(s.action||'关注')+'</span>';
      return '<div style="padding:12px;border-bottom:1px solid var(--border);">' +
        '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'+icon+' <a href="'+B+'/quant/admin/stock?symbol='+esc(s.symbol)+'" target="_blank" style="color:var(--accent);font-weight:600">'+esc(s.symbol)+'</a> <span style="color:var(--text)">'+esc(s.name)+'</span> '+actionBadge+' '+urgBadge+'</div>'+
        '<div style="font-size:13px;color:var(--text-dim);margin-bottom:4px">'+esc(s.message)+'</div>'+
        '<div style="font-size:12px;color:var(--text-dim)">现价: '+num(s.currentPrice)+(s.triggerPrice?' | 触发价: '+num(s.triggerPrice):'')+(s.pnlPct!=null?' | 盈亏: <span style="color:'+pctColor(s.pnlPct)+'">'+pct(s.pnlPct)+'</span>':'')+'</div>'+
        '</div>';
    }).join('');
  }
  overlay.innerHTML='<div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;width:600px;max-width:90vw;max-height:80vh;overflow:hidden;display:flex;flex-direction:column">'+
    '<div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">'+
      '<h3 style="margin:0;font-size:16px">'+(filterUrgency?'🚨':'📡')+' '+title+'</h3>'+
      '<span style="cursor:pointer;font-size:20px;color:var(--text-dim)" onclick="document.getElementById(\\'signal-popup-overlay\\').remove()">✕</span>'+
    '</div>'+
    '<div style="overflow-y:auto;flex:1">'+content+'</div>'+
    '<div style="padding:12px 20px;border-top:1px solid var(--border);text-align:right">'+
      '<button class="btn" onclick="_activateTab(\\'signals\\');history.pushState(null,null,\\'#signals\\');document.getElementById(\\'signal-popup-overlay\\').remove()">查看全部信号详情</button>'+
    '</div></div>';
  document.body.appendChild(overlay);
}

/* ══════ PORTFOLIO CONDITIONAL ORDER TRACKING ══════ */
var _pfCondExpanded=false;
function togglePortfolioConds(){
  _pfCondExpanded=!_pfCondExpanded;
  document.getElementById('pf-cond-panel').style.display=_pfCondExpanded?'block':'none';
  document.getElementById('pf-cond-toggle').textContent=_pfCondExpanded?'▼ 收起':'▶ 展开';
  if(_pfCondExpanded)loadPortfolioConditionals();
}

function loadPortfolioConditionals(){
  var statusEl=document.getElementById('pf-cond-status');
  statusEl.textContent='加载中…';
  api('/quant/admin/api/conditional-orders?active=false')
    .then(function(d){
      var orders=d.orders||[];
      var active=orders.filter(function(o){return !o.triggered});
      var triggered=orders.filter(function(o){return o.triggered});
      statusEl.textContent='共 '+orders.length+' 条 | 监控中 '+active.length+' 条 | 已触发 '+triggered.length+' 条';
      var tbody=document.querySelector('#pf-cond-table tbody');
      if(!orders.length){
        tbody.innerHTML='<tr><td colspan="11" style="color:var(--text-dim)">暂无条件单</td></tr>';
        return;
      }
      var rows='';
      orders.forEach(function(o){
        var typeLabel=o.type==='stop_loss'?'<span class="badge badge-red">止损</span>':
          o.type==='take_profit'?'<span class="badge badge-green">止盈</span>':
          o.type==='trailing_stop'?'<span class="badge badge-blue">跟踪止损</span>':
          o.type==='trailing_atr'?'<span class="badge badge-blue">ATR止损</span>':
          o.type==='trailing_tp'?'<span class="badge badge-green">跟踪止盈</span>':
          o.type==='staged_tp'?'<span class="badge badge-green">分段止盈</span>':
          '<span class="badge">'+esc(o.type)+'</span>';
        var triggerDisplay=o.type==='trailing_stop'||o.type==='trailing_atr'?((o.triggerValue*100).toFixed(1)+'%'):num(o.triggerValue);
        var trailingPrice=o.trailingStopPrice?num(o.trailingStopPrice):'-';
        var peakPrice=o.peakPrice?num(o.peakPrice):'-';
        var statusHtml;
        if(o.triggered){
          statusHtml='<span class="badge badge-yellow">已触发</span>';
          if(o.triggeredAt)statusHtml+='<br><span style="font-size:10px">'+(new Date(o.triggeredAt)).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})+'</span>';
        }else{
          statusHtml='<span class="badge badge-green">监控中</span>';
        }
        var resultHtml=o.triggerResult?'<span style="font-size:11px">'+esc(o.triggerResult)+'</span>':'-';
        var time=o.createdAt?(new Date(o.createdAt)).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
        var rowStyle=o.triggered?'background:rgba(251,191,36,.06)':'';
        rows+='<tr style="'+rowStyle+'">'+
          '<td><strong>'+esc(o.symbol)+'</strong></td>'+
          '<td style="font-size:11px">'+esc(o.name||'')+'</td>'+
          '<td>'+typeLabel+'</td>'+
          '<td>'+num(o.entryPrice)+'</td>'+
          '<td>'+triggerDisplay+'</td>'+
          '<td>'+trailingPrice+'</td>'+
          '<td>'+peakPrice+'</td>'+
          '<td>'+numInt(o.quantity)+'</td>'+
          '<td style="font-size:11px">'+time+'</td>'+
          '<td>'+statusHtml+'</td>'+
          '<td>'+resultHtml+'</td>'+
          '</tr>';
      });
      tbody.innerHTML=rows;
    })
    .catch(function(e){
      statusEl.textContent='加载失败: '+e.message;
    });
}

/* ══════ EQUITY CURVE ══════ */
function loadEquityCurve(){
  var statusEl=document.getElementById('equity-curve-status');
  statusEl.textContent='加载中…';
  // Load snapshots and summary in parallel
  Promise.all([
    api('/quant/api/equity-history?mode=paper'),
    api('/quant/api/equity-history/summary?mode=paper')
  ]).then(function(results){
    var snapshots=results[0].data||[];
    var summary=results[1].data||{};
    if(!snapshots.length){
      statusEl.textContent='暂无权益历史数据（需要引擎运行后自动记录）';
      document.getElementById('equity-curve-kpis').innerHTML='';
      return;
    }
      // Render KPIs
      var kpiRow=document.getElementById('equity-curve-kpis');
      kpiRow.innerHTML=[
        kpiHtml(summary.totalDays+'天','记录天数'),
        kpiHtml('¥'+numInt(summary.startEquity||0),'起始权益'),
        kpiHtml('¥'+numInt(summary.endEquity||0),'当前权益'),
        kpiHtml('<span style="color:'+pctColor(summary.totalReturnPct)+'">'+pct(summary.totalReturnPct/100)+'</span>','总收益率'),
        kpiHtml('<span style="color:var(--red)">-'+pct(summary.maxDrawdownPct/100)+'</span>','最大回撤'),
        kpiHtml(summary.winRate?summary.winRate.toFixed(1)+'%':'-','日胜率'),
      ].join('');
      // Draw chart
      drawEquityCurve(snapshots);
      statusEl.textContent='最近更新: '+(snapshots[snapshots.length-1].date||'');
    })
    .catch(function(e){
      statusEl.textContent='加载失败: '+e.message;
    });
}

function drawEquityCurve(snapshots){
  var canvas=document.getElementById('equity-canvas');
  if(!canvas||!canvas.getContext)return;
  var ctx=canvas.getContext('2d');
  var rect=canvas.parentElement.getBoundingClientRect();
  var w=rect.width||800;
  var h=rect.height||300;
  canvas.width=w*2;canvas.height=h*2;
  canvas.style.width=w+'px';canvas.style.height=h+'px';
  ctx.scale(2,2);
  ctx.clearRect(0,0,w,h);

  var equities=snapshots.map(function(s){return s.equity});
  var minE=Math.min.apply(null,equities);
  var maxE=Math.max.apply(null,equities);
  var range=maxE-minE||1;
  var padL=60,padR=20,padT=20,padB=30;
  var chartW=w-padL-padR;
  var chartH=h-padT-padB;

  // Grid
  ctx.strokeStyle='rgba(255,255,255,0.06)';
  ctx.lineWidth=0.5;
  for(var gi=0;gi<=4;gi++){
    var gy=padT+chartH*(1-gi/4);
    ctx.beginPath();ctx.moveTo(padL,gy);ctx.lineTo(w-padR,gy);ctx.stroke();
    ctx.fillStyle='rgba(255,255,255,0.4)';
    ctx.font='10px sans-serif';
    ctx.textAlign='right';
    ctx.fillText('¥'+numInt(minE+range*gi/4),padL-4,gy+4);
  }

  // Equity line
  ctx.strokeStyle='#0d9488';
  ctx.lineWidth=1.5;
  ctx.beginPath();
  for(var i=0;i<snapshots.length;i++){
    var x=padL+chartW*(i/(snapshots.length-1||1));
    var y=padT+chartH*(1-(equities[i]-minE)/range);
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  }
  ctx.stroke();

  // Fill gradient
  var grad=ctx.createLinearGradient(0,padT,0,h-padB);
  grad.addColorStop(0,'rgba(13,148,136,0.3)');
  grad.addColorStop(1,'rgba(13,148,136,0.02)');
  ctx.lineTo(padL+chartW,h-padB);
  ctx.lineTo(padL,h-padB);
  ctx.closePath();
  ctx.fillStyle=grad;
  ctx.fill();

  // Date labels
  ctx.fillStyle='rgba(255,255,255,0.4)';
  ctx.font='10px sans-serif';
  ctx.textAlign='center';
  var labelCount=Math.min(6,snapshots.length);
  for(var li=0;li<labelCount;li++){
    var idx=Math.floor(li*(snapshots.length-1)/(labelCount-1||1));
    var lx=padL+chartW*(idx/(snapshots.length-1||1));
    ctx.fillText(snapshots[idx].date.slice(5),lx,h-padB+14);
  }
}

/* ══════ SCREENING ══════ */
function runScreening(){
  var el=document.getElementById('screen-status');
  el.innerHTML='<span style="color:var(--accent)">⏳ 正在全市场扫描，请稍候…</span>';
  toast('正在筛选股票…');

  var screenSel=document.getElementById('screen-strategy');
  var screenVal=screenSel.value;
  var strategy=screenVal;
  var composedId=null;
  if(screenVal.indexOf('composed:')===0){
    composedId=screenVal.slice(9);
    var selOpt=screenSel.options[screenSel.selectedIndex];
    strategy=selOpt?selOpt.textContent:'auto';
  }

  var body={
    action:'screen',
    strategy:strategy,
    composed_strategy_id:composedId,
    risk_tolerance:document.getElementById('risk-tolerance')?document.getElementById('risk-tolerance').value:'medium',
    top_n:Number(document.getElementById('screen-top-n').value)||20
  };

  api('/quant/admin/api/investment-screen',{method:'POST',body:JSON.stringify(body),_timeout:120000}).then(function(d){
    el.innerHTML='<span style="color:var(--green)">✅ 筛选完成 ('+esc(d.strategy)+' / '+esc(d.riskProfile)+')</span>';
    renderScreenResults(d);
    // Cache results so browser back restores them
    try{sessionStorage.setItem('openclaw-screen-results',JSON.stringify(d))}catch(e){}
    // Auto-refresh history comparison after screening
    loadScreenerHistory();
  }).catch(function(){el.innerHTML='<span style="color:var(--red)">❌ 筛选失败</span>'});
}

function renderScreenResults(d){
    document.getElementById('screen-results').style.display='block';
    document.getElementById('screen-count').textContent=(d.candidates||[]).length;

    // Enrichment KPIs (macro regime, position guidance, lianban pool, hot stocks)
    var kpisEl=document.getElementById('screen-enrichment-kpis');
    if(kpisEl){
      var regimeLabel={'recovery':'🟢 复苏','overheating':'🟡 过热','stagflation':'🟠 滞胀','recession':'🔴 衰退'};
      var regime=regimeLabel[d.macroRegime]||('⚪ '+(d.macroRegime||'δ֪'));
      var kpis=[kpiHtml(regime,'宏观周期')];
      if(d.lianbanPoolCount!=null)kpis.push(kpiHtml(String(d.lianbanPoolCount),'涨停池'));
      if(d.hotStocksCount!=null)kpis.push(kpiHtml(String(d.hotStocksCount),'热股池'));
      // Position guidance from macro regime
      var pg=d.positionGuidance;
      if(pg){
        var scaleColor=pg.positionScale>=0.9?'var(--green)':pg.positionScale>=0.7?'var(--yellow)':'var(--red)';
        kpis.push(kpiHtml('<span style="color:'+scaleColor+'">'+(pg.positionScale*100).toFixed(0)+'%</span>','建议仓位'));
        kpis.push(kpiHtml(String(pg.maxPositions),'最大持仓'));
      }
      kpisEl.innerHTML=kpis.join('');
      var kpiTitle=d.macroDescription||'';
      if(pg&&pg.regimeAdjustment)kpiTitle+='\\n'+pg.regimeAdjustment;
      if(kpiTitle)kpisEl.title=kpiTitle;
    }

    var tbody=document.querySelector('#screen-table tbody');
    tbody.innerHTML=(d.candidates||[]).map(function(c,i){
      // Build inline enrichment badges
      var tags=[];
      if(c.lianban){
        var lb=c.lianban;
        var lbBadge=lb.board_count+'板';
        if(lb.quality_score>=70)tags.push('<span class="badge badge-purple" style="margin:1px" title="质量'+num(lb.quality_score)+'">🔥'+lbBadge+'</span>');
        else if(lb.quality_score>=40)tags.push('<span class="badge badge-yellow" style="margin:1px">'+lbBadge+'</span>');
        else tags.push('<span class="badge badge-red" style="margin:1px">'+lbBadge+'⚠</span>');
      }
      if(c.smartMoney){
        var sm=c.smartMoney;
        if(sm.score>=70)tags.push('<span class="badge badge-green" style="margin:1px" title="'+esc(sm.conclusion||'')+'">机构'+sm.score+'</span>');
        else if(sm.score<=30)tags.push('<span class="badge badge-red" style="margin:1px" title="'+esc(sm.conclusion||'')+'">机构'+sm.score+'</span>');
        else tags.push('<span class="badge badge-blue" style="margin:1px">机构'+sm.score+'</span>');
      }
      if(c.sentiment){
        var st=c.sentiment;
        if(st.score>=70)tags.push('<span class="badge badge-orange" style="margin:1px" title="'+esc(st.interpretation||'')+'">🔥舆情'+Math.round(st.score)+'</span>');
        else if(st.score>=40)tags.push('<span class="badge badge-sim" style="margin:1px" title="'+esc(st.interpretation||'')+'">舆情'+Math.round(st.score)+'</span>');
        else if(st.score>0)tags.push('<span class="badge" style="margin:1px;background:var(--bg-dim);color:var(--text-dim)" title="'+esc(st.interpretation||'')+'">舆情'+Math.round(st.score)+'</span>');
      }
      if(c.analystConsensus&&c.analystConsensus.total>=3){
        var ac=c.analystConsensus;
        var buyRatio=ac.buy/ac.total;
        if(buyRatio>=0.7)tags.push('<span class="badge badge-green" style="margin:1px">👍 '+ac.buy+'/'+ac.total+'看好</span>');
        else if(ac.sell>ac.buy)tags.push('<span class="badge badge-red" style="margin:1px">👎 '+ac.sell+'/'+ac.total+'看空</span>');
        else tags.push('<span class="badge badge-blue" style="margin:1px">📊 '+ac.buy+'/'+ac.total+'看好</span>');
      }
      if(c.earningsWarning)tags.push('<span class="badge badge-orange" style="margin:1px">📅 财报'+esc(c.earningsWarning)+'</span>');
      var tagHtml=tags.length?tags.join(''):'<span style="color:var(--text-dim);font-size:11px">-</span>';

      return '<tr>'+
        '<td>'+(i+1)+'</td>'+
        '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(c.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(c.symbol)+'</a></td>'+
        '<td>'+esc(c.name)+'</td>'+
        '<td>'+num(c.price)+'</td>'+
        '<td style="color:'+pctColor(c.changePct)+'">'+pct(c.changePct)+'</td>'+
        '<td>'+num(c.pe)+'</td>'+
        '<td>'+num(c.pb||0)+'</td>'+
        '<td>'+num((c.totalMarketCap||0)/1e8)+'</td>'+
        '<td>'+num(c.turnoverRate)+'</td>'+
        '<td>'+num(c.volumeRatio||0)+'</td>'+
        '<td><span class="badge badge-purple">'+c.score+'</span></td>'+
        '<td style="font-size:11px">'+tagHtml+'</td>'+
        '<td style="font-size:11px">'+(c.reasons||[]).slice(0,6).map(function(r){return '<span class="badge badge-blue" style="margin:1px">'+esc(r)+'</span>'}).join(' ')+'</td>'+
        '<td><button class="btn btn-sm" onclick="viewStock(\\''+esc(c.symbol)+'\\')">详情</button></td>'+
        '</tr>';
    }).join('')||'<tr><td colspan="14" style="text-align:center;color:var(--text-dim)">无筛选结果</td></tr>';
}

function restoreScreenResults(){
  try{
    var raw=sessionStorage.getItem('openclaw-screen-results');
    if(raw){
      var d=JSON.parse(raw);
      var el=document.getElementById('screen-status');
      if(el)el.innerHTML='<span style="color:var(--green)">✅ 筛选完成 ('+esc(d.strategy)+' / '+esc(d.riskProfile)+') — 缓存</span>';
      renderScreenResults(d);
    }
  }catch(e){}
}

/* ══════ SCREENER HISTORY ══════ */
function loadScreenerHistory(){
  var statusEl=document.getElementById('screener-history-status');
  statusEl.textContent='加载历史…';
  api('/quant/api/stocklist/history').then(function(d){
    if(!d.comparison||!d.dates||d.dates.length===0){
      statusEl.textContent='暂无历史数据，请先运行选股';
      return;
    }
    var c=d.comparison;
    statusEl.textContent='最新: '+c.date+(c.prevDate?' vs '+c.prevDate:'');
    document.getElementById('screener-history-panel').style.display='block';
    document.getElementById('screener-history-kpis').innerHTML=[
      kpiHtml(c.total,'本次入选'),
      kpiHtml('<span style="color:var(--green)">'+c.new.length+'</span>','新入选'),
      kpiHtml(c.kept.length,'连续入选'),
      kpiHtml('<span style="color:var(--red)">'+c.dropped.length+'</span>','已退出'),
      kpiHtml(d.dates.length+'天','历史记录'),
    ].join('');

    // New stocks
    document.getElementById('sh-new').innerHTML=c.new.length?c.new.map(function(s){
      return '<a href="'+B+'/quant/admin/stock?symbol='+esc(s)+'" target="_blank" class="badge badge-green" style="text-decoration:none;padding:4px 8px;cursor:pointer">🆕 '+esc(s)+'</a>';
    }).join(''):'<span style="color:var(--text-dim);font-size:12px">无</span>';

    // Kept stocks with consecutive days
    document.getElementById('sh-kept').innerHTML=c.kept.length?c.kept.map(function(s){
      var days=c.consecutive[s]||1;
      var badge=days>=5?'badge-purple':days>=3?'badge-blue':'badge-yellow';
      return '<a href="'+B+'/quant/admin/stock?symbol='+esc(s)+'" target="_blank" class="badge '+badge+'" style="text-decoration:none;padding:4px 8px;cursor:pointer">'+esc(s)+' <sup>'+days+'天</sup></a>';
    }).join(''):'<span style="color:var(--text-dim);font-size:12px">无</span>';

    // Dropped stocks
    document.getElementById('sh-dropped').innerHTML=c.dropped.length?c.dropped.map(function(s){
      return '<span class="badge badge-red" style="padding:4px 8px;opacity:0.7">'+esc(s)+'</span>';
    }).join(''):'<span style="color:var(--text-dim);font-size:12px">无</span>';

    // History table
    var dates=d.dates.slice(0,10);
    var tbody=document.querySelector('#sh-history-table tbody');
    tbody.innerHTML=dates.map(function(date){
      var entry=d.history[date]||{};
      var codes=Array.isArray(entry)?entry:(entry.codes||[]);
      var strategy=Array.isArray(entry)?'':(entry.strategy||'');
      return '<tr><td>'+esc(date)+'</td><td>'+esc(strategy||'-')+'</td><td>'+codes.length+'</td><td style="font-size:11px">'+codes.map(function(c){return esc(c)}).join(', ')+'</td></tr>';
    }).join('');
  }).catch(function(){statusEl.textContent='加载失败'});
}

/* ══════ REBALANCE ══════ */

/* ── State for interactive rebalance ── */
var _rebalancePositions=[];   // loaded from portfolio API
var _rebalanceNewRows=0;      // counter for new purchase rows
var _lastAiRebalance=null;    // last AI suggestion

/* ── Load portfolio holdings into rebalance table ── */
function loadRebalancePortfolio(){
  var statusEl=document.getElementById('rebalance-holdings-status');
  statusEl.textContent='正在加载持仓…';
  var _mode=window._currentViewMode||'paper';

  // Try live portfolio first, fall back to plan-based monitor
  api('/quant/api/portfolio',{_timeout:30000}).then(function(d){
    if(!d||d.status!=='ok'||!d.data||!d.data.positions||!d.data.positions.length){
      // Fall back to plan-based monitor
      return _loadRebalanceFromPlan();
    }
    var pf=d.data;
    _rebalancePositions=pf.positions.map(function(p){
      return {
        symbol:p.symbol,name:p.name||p.symbol,
        shares:p.quantity||p.shares||0,
        cost:p.avgCost||p.costPrice||0,
        currentPrice:p.currentPrice||p.lastPrice||0,
        pnlPct:p.pnlPct!=null?p.pnlPct:((p.currentPrice||0)/(p.avgCost||1)-1)*100,
        weight:p.weight||0,
        marketValue:p.marketValue||0,
        changePct:p.changePct||p.todayPnlPct||0,
        stopLoss:p.stopLoss||0,takeProfit:p.takeProfit||0,
      };
    });
    // Calculate weights if not provided
    var totalValue=_rebalancePositions.reduce(function(s,p){return s+(p.marketValue||p.currentPrice*p.shares)},0);
    if(totalValue>0)_rebalancePositions.forEach(function(p){
      if(!p.weight)p.weight=((p.currentPrice*p.shares)/totalValue*100);
      if(!p.marketValue)p.marketValue=p.currentPrice*p.shares;
    });
    _renderRebalanceKpis(pf,totalValue);
    _renderRebalanceHoldings();
    statusEl.textContent='持仓已加载 ('+_rebalancePositions.length+'只) · '+(pf.source||_mode);
  }).catch(function(e){
    console.warn('[rebalance] portfolio API failed, trying plan-based',e.message);
    _loadRebalanceFromPlan();
  });
}

function _loadRebalanceFromPlan(){
  var statusEl=document.getElementById('rebalance-holdings-status');
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    statusEl.textContent='无可用持仓数据。请先在"制定计划"中生成计划，或确保实盘已连接。';
    document.getElementById('rebalance-holdings-body').innerHTML=
      '<tr><td colspan="11" style="text-align:center;color:var(--text-dim);padding:30px">'+
      '暂无持仓 · <button class="btn btn-sm" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">前往制定计划</button></td></tr>';
    return;
  }
  statusEl.textContent='正在通过计划数据加载…';
  var _mode=window._currentViewMode||'paper';
  api('/quant/admin/api/investment-monitor',{method:'POST',body:JSON.stringify({action:'monitor',plan:currentPlan,mode:_mode}),_timeout:60000}).then(function(d){
    _rebalancePositions=(d.positions||[]).map(function(p){
      return {
        symbol:p.symbol,name:p.name||p.symbol,
        shares:p.shares||0,cost:p.buyPrice||p.cost||0,
        currentPrice:p.currentPrice||0,
        pnlPct:p.pnlPct||0,weight:p.weight||0,
        marketValue:p.currentValue||0,changePct:p.changePct||0,
        stopLoss:p.stopLoss||0,takeProfit:p.takeProfit||0,
      };
    });
    var totalValue=_rebalancePositions.reduce(function(s,p){return s+p.marketValue},0);
    if(totalValue>0)_rebalancePositions.forEach(function(p){
      if(!p.weight)p.weight=(p.marketValue/totalValue*100);
    });
    var s=d.summary||{};
    _renderRebalanceKpis({totalValue:s.totalValue,cash:0,profit:s.totalPnl,profitPct:s.totalPnlPct},totalValue);
    _renderRebalanceHoldings();
    statusEl.textContent='持仓已加载 ('+_rebalancePositions.length+'只) · 计划模式';
  }).catch(function(e){
    statusEl.textContent='加载失败: '+e.message;
  });
}

function _renderRebalanceKpis(pf,totalValue){
  var kpis=document.getElementById('rebalance-portfolio-kpis');
  if(!kpis)return;
  kpis.innerHTML=[
    kpiHtml('¥'+numInt(totalValue||pf.totalValue||pf.marketValue||0),'总市值'),
    kpiHtml('¥'+numInt(pf.cash||0),'可用资金'),
    kpiHtml('<span style="color:'+pctColor(pf.profit||0)+'">¥'+numInt(pf.profit||0)+'</span>','总盈亏'),
    kpiHtml('<span style="color:'+pctColor(pf.profitPct||0)+'">'+pct(pf.profitPct||0)+'</span>','收益率'),
    kpiHtml(_rebalancePositions.length,'持仓数'),
  ].join('');
}

function _renderRebalanceHoldings(){
  var tbody=document.getElementById('rebalance-holdings-body');
  if(!_rebalancePositions.length){
    tbody.innerHTML='<tr><td colspan="11" style="text-align:center;color:var(--text-dim);padding:30px">暂无持仓</td></tr>';
    return;
  }
  tbody.innerHTML=_rebalancePositions.map(function(p,i){
    return '<tr data-rb-idx="'+i+'" data-symbol="'+esc(p.symbol)+'">' +
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(p.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(p.symbol)+'</a></td>'+
      '<td>'+esc(p.name)+'</td>'+
      '<td>'+numInt(p.shares)+'</td>'+
      '<td>'+num(p.cost)+'</td>'+
      '<td>'+num(p.currentPrice)+'<br><span style="font-size:11px;color:'+pctColor(p.changePct)+'">'+pct(p.changePct)+'</span></td>'+
      '<td style="color:'+pctColor(p.pnlPct)+'">'+pct(p.pnlPct)+'</td>'+
      '<td>'+pct(p.weight)+'</td>'+
      '<td>'+_actionSelect(i,'existing')+'</td>'+
      '<td id="rb-param-'+i+'">'+_actionParams(i,'hold',p)+'</td>'+
      '<td id="rb-strategy-'+i+'">—</td>'+
      '<td><input type="text" id="rb-note-'+i+'" placeholder="备注" style="width:80px;font-size:11px"></td>'+
      '</tr>';
  }).join('');
  // Clear new rows
  document.getElementById('rebalance-new-rows').innerHTML='';
  _rebalanceNewRows=0;
}

function _actionSelect(idx,type){
  var prefix=type==='new'?'rbn':'rb';
  return '<select id="'+prefix+'-action-'+idx+'" onchange="_onRebalanceActionChange('+idx+',\\''+type+'\\',this.value)" style="font-size:12px;padding:2px 4px">' +
    (type==='existing'?
      '<option value="hold">✊ 持有</option>'+
      '<option value="add">📈 加仓</option>'+
      '<option value="reduce">📉 减仓</option>'+
      '<option value="sell">🛑 卖出</option>'
    :
      '<option value="buy">🆕 买入</option>'
    ) +
    '</select>';
}

function _actionParams(idx,action,pos){
  var p=pos||_rebalancePositions[idx]||{};
  if(action==='hold') return '<span style="color:var(--text-dim);font-size:12px">维持不变</span>';
  if(action==='add'){
    return '<div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">'+
      '<button class="btn btn-sm" onclick="_setAddPct('+idx+',25)" style="padding:1px 6px;font-size:11px">+25%</button>'+
      '<button class="btn btn-sm" onclick="_setAddPct('+idx+',50)" style="padding:1px 6px;font-size:11px">+50%</button>'+
      '<button class="btn btn-sm" onclick="_setAddPct('+idx+',100)" style="padding:1px 6px;font-size:11px">+100%</button>'+
      '<input type="number" id="rb-qty-'+idx+'" value="'+Math.max(100,Math.round(p.shares*0.25/100)*100)+'" min="100" step="100" style="width:70px;font-size:11px" placeholder="股数">'+
      '</div>';
  }
  if(action==='reduce'){
    return '<div style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">'+
      '<button class="btn btn-sm" onclick="_setReducePct('+idx+',25)" style="padding:1px 6px;font-size:11px">-25%</button>'+
      '<button class="btn btn-sm" onclick="_setReducePct('+idx+',50)" style="padding:1px 6px;font-size:11px">-50%</button>'+
      '<button class="btn btn-sm" onclick="_setReducePct('+idx+',75)" style="padding:1px 6px;font-size:11px">-75%</button>'+
      '<input type="number" id="rb-qty-'+idx+'" value="'+Math.max(100,Math.round(p.shares*0.5/100)*100)+'" min="100" step="100" style="width:70px;font-size:11px" placeholder="股数">'+
      '</div>';
  }
  if(action==='sell'){
    return '<div style="font-size:12px">'+
      '<label><input type="radio" name="rb-sell-'+idx+'" value="all" checked> 全部 '+numInt(p.shares)+' 股</label><br>'+
      '<label><input type="radio" name="rb-sell-'+idx+'" value="partial"> 部分 </label>'+
      '<input type="number" id="rb-sell-qty-'+idx+'" value="'+p.shares+'" min="100" step="100" style="width:70px;font-size:11px">'+
      '</div>';
  }
  return '';
}

function _strategySelect(idx,action){
  if(action==='hold') return '—';
  if(action==='add'){
    return '<select id="rb-strat-'+idx+'" style="font-size:11px;padding:2px">'+
      '<option value="market">市价买入</option>'+
      '<option value="limit">限价买入</option>'+
      '<option value="batch">分批加仓</option>'+
      '<option value="pullback">回调加仓</option>'+
      '</select>';
  }
  if(action==='reduce'){
    return '<select id="rb-strat-'+idx+'" style="font-size:11px;padding:2px">'+
      '<option value="market">市价卖出</option>'+
      '<option value="limit">限价卖出</option>'+
      '<option value="trailing">移动止盈</option>'+
      '<option value="batch">分批减仓</option>'+
      '</select>';
  }
  if(action==='sell'){
    return '<select id="rb-strat-'+idx+'" style="font-size:11px;padding:2px">'+
      '<option value="market">市价清仓</option>'+
      '<option value="limit">限价卖出</option>'+
      '<option value="stop">止损卖出</option>'+
      '<option value="trailing">移动止损</option>'+
      '<option value="twap">TWAP 分批</option>'+
      '</select>';
  }
  if(action==='buy'){
    return '<select id="rb-strat-'+idx+'" style="font-size:11px;padding:2px">'+
      '<option value="market">市价买入</option>'+
      '<option value="limit">限价买入</option>'+
      '<option value="batch">分批建仓</option>'+
      '</select>';
  }
  return '—';
}

function _onRebalanceActionChange(idx,type,action){
  var p=(type==='existing')?_rebalancePositions[idx]:null;
  var prefix=(type==='new')?'rbn':'rb';
  var paramEl=document.getElementById(prefix+'-param-'+idx);
  var stratEl=document.getElementById(prefix+'-strategy-'+idx);
  if(paramEl) paramEl.innerHTML=_actionParams(idx,action,p);
  if(stratEl) stratEl.innerHTML=_strategySelect(idx,action);
}

function _setAddPct(idx,pctVal){
  var p=_rebalancePositions[idx];if(!p)return;
  var qty=Math.max(100,Math.round(p.shares*pctVal/100/100)*100);
  var el=document.getElementById('rb-qty-'+idx);
  if(el)el.value=qty;
}
function _setReducePct(idx,pctVal){
  var p=_rebalancePositions[idx];if(!p)return;
  var qty=Math.max(100,Math.round(p.shares*pctVal/100/100)*100);
  var el=document.getElementById('rb-qty-'+idx);
  if(el)el.value=qty;
}

/* ── Add new stock row ── */
function showAddNewPositionRow(){
  var tbody=document.getElementById('rebalance-new-rows');
  var ni=_rebalanceNewRows++;
  var nIdx='n'+ni;
  var row=document.createElement('tr');
  row.setAttribute('data-rb-new',ni);
  row.innerHTML=
    '<td><input type="text" id="rbn-symbol-'+ni+'" placeholder="代码" style="width:70px;font-size:12px"></td>'+
    '<td><input type="text" id="rbn-name-'+ni+'" placeholder="名称" style="width:60px;font-size:12px"></td>'+
    '<td colspan="2">—</td>'+
    '<td><input type="number" id="rbn-price-'+ni+'" placeholder="价格" style="width:70px;font-size:12px" step="0.01"></td>'+
    '<td>—</td><td>—</td>'+
    '<td><span class="badge badge-green">🆕 新购</span></td>'+
    '<td id="rbn-param-'+ni+'"><input type="number" id="rbn-qty-'+ni+'" value="100" min="100" step="100" style="width:70px;font-size:11px" placeholder="股数"></td>'+
    '<td id="rbn-strategy-'+ni+'">'+_strategySelect(ni,'buy')+'</td>'+
    '<td style="display:flex;gap:4px"><input type="text" id="rbn-note-'+ni+'" placeholder="备注" style="width:60px;font-size:11px">'+
    '<button class="btn btn-sm" onclick="removeNewPositionRow('+ni+')" style="padding:1px 6px;font-size:11px;color:var(--red)">✕</button></td>';
  tbody.appendChild(row);
  // Auto-lookup stock name
  var symInput=document.getElementById('rbn-symbol-'+ni);
  symInput.addEventListener('blur',function(){
    var sym=symInput.value.trim();if(!sym)return;
    api('/quant/admin/api/stock-detail?symbol='+encodeURIComponent(sym)+'&kline_days=1',{_timeout:10000}).then(function(d){
      var info=d&&(d.info||d.data||d);
      if(info&&(info.name||info.stockName)){
        var nameEl=document.getElementById('rbn-name-'+ni);
        var priceEl=document.getElementById('rbn-price-'+ni);
        if(nameEl&&!nameEl.value)nameEl.value=info.name||info.stockName||'';
        if(priceEl&&!priceEl.value&&(info.price||info.currentPrice))priceEl.value=info.price||info.currentPrice;
      }
    }).catch(function(){});
  });
}
function removeNewPositionRow(ni){
  var row=document.querySelector('tr[data-rb-new="'+ni+'"]');
  if(row)row.remove();
}

/* ── Reset all actions to hold ── */
function resetRebalanceActions(){
  _renderRebalanceHoldings();
  toast('↩️ 已重置所有操作');
}

/* ── Collect and submit manual rebalance ── */
var _pendingRebalanceActions=null;
var _rbExecRunning=false;

function submitManualRebalance(){
  var actions=[];

  // Existing positions
  _rebalancePositions.forEach(function(p,i){
    var actionSel=document.getElementById('rb-action-'+i);
    if(!actionSel)return;
    var action=actionSel.value;
    if(action==='hold')return; // skip hold entries

    var item={action:action,symbol:p.symbol,name:p.name,currentPrice:p.currentPrice,shares:p.shares};

    if(action==='add'||action==='reduce'){
      var qtyEl=document.getElementById('rb-qty-'+i);
      item.quantity=parseInt(qtyEl?qtyEl.value:0)||0;
    }
    if(action==='sell'){
      var radios=document.querySelectorAll('input[name="rb-sell-'+i+'"]');
      var sellAll=true;
      radios.forEach(function(r){if(r.checked&&r.value==='partial')sellAll=false});
      if(sellAll){
        item.quantity=p.shares;
      }else{
        var sq=document.getElementById('rb-sell-qty-'+i);
        item.quantity=parseInt(sq?sq.value:0)||p.shares;
      }
    }
    var stratEl=document.getElementById('rb-strat-'+i);
    if(stratEl)item.strategy=stratEl.value;
    var noteEl=document.getElementById('rb-note-'+i);
    if(noteEl&&noteEl.value)item.note=noteEl.value;

    actions.push(item);
  });

  // New positions
  for(var ni=0;ni<_rebalanceNewRows;ni++){
    var symEl=document.getElementById('rbn-symbol-'+ni);
    if(!symEl||!symEl.value.trim())continue;
    var sym=symEl.value.trim();
    var nameEl=document.getElementById('rbn-name-'+ni);
    var priceEl=document.getElementById('rbn-price-'+ni);
    var qtyEl=document.getElementById('rbn-qty-'+ni);
    var stratEl=document.getElementById('rbn-strat-'+ni);
    var noteEl=document.getElementById('rbn-note-'+ni);
    actions.push({
      action:'buy',symbol:sym,
      name:(nameEl?nameEl.value:'')||sym,
      currentPrice:parseFloat(priceEl?priceEl.value:0)||0,
      quantity:parseInt(qtyEl?qtyEl.value:0)||100,
      strategy:(stratEl?stratEl.value:'market'),
      note:(noteEl?noteEl.value:''),
    });
  }

  if(!actions.length){
    toast('⚠️ 请至少选择一个操作（非持有）');return;
  }

  var btn=document.getElementById('btn-submit-rebalance');
  btn.disabled=true;btn.textContent='⏳ 验证中…';
  var statusEl=document.getElementById('rebalance-status');
  statusEl.textContent='正在验证调仓计划…';

  // Step 1: Validate with backend (enrich with real-time quotes)
  api('/quant/admin/api/rebalance-manual',{
    method:'POST',
    body:JSON.stringify({actions:actions,mode:window._currentViewMode||'paper'}),
    _timeout:60000
  }).then(function(d){
    btn.disabled=false;btn.textContent='📋 提交调仓计划';
    if(d.error){statusEl.textContent='验证失败: '+d.error;toast('❌ '+d.error);return;}
    statusEl.textContent='✅ 调仓计划已验证，请确认后执行';
    // Step 2: Show execution panel
    _pendingRebalanceActions=d.actions||actions;
    _showRebalanceExecPanel(d);
  }).catch(function(e){
    btn.disabled=false;btn.textContent='📋 提交调仓计划';
    statusEl.textContent='验证失败: '+e.message;
  });
}

function _showRebalanceExecPanel(d){
  var panel=document.getElementById('rebalance-exec-panel');
  panel.style.display='block';
  panel.scrollIntoView({behavior:'smooth',block:'nearest'});

  var enriched=d.actions||[];
  var summary=d.summary||{};

  // Summary text
  var parts=[];
  if(summary.buyCount)parts.push('新购 '+summary.buyCount+'只');
  if(summary.addCount)parts.push('加仓 '+summary.addCount+'只');
  if(summary.reduceCount)parts.push('减仓 '+summary.reduceCount+'只');
  if(summary.sellCount)parts.push('卖出 '+summary.sellCount+'只');
  var modeLabel=(window._currentViewMode==='live')?'🔴 实盘':'🟢 模拟盘';
  document.getElementById('rebalance-exec-summary').innerHTML=
    '<div style="font-size:13px"><strong>'+modeLabel+' · 调仓执行计划</strong>: '+parts.join('，')+'</div>';

  // KPIs
  document.getElementById('rebalance-exec-kpis').innerHTML=[
    kpiHtml(summary.totalActions||enriched.length,'操作总数'),
    kpiHtml('¥'+numInt(summary.totalBuyAmount||0),'<span style="color:var(--green)">买入金额</span>'),
    kpiHtml('¥'+numInt(summary.totalSellAmount||0),'<span style="color:var(--red)">卖出金额</span>'),
    kpiHtml('¥'+numInt(summary.netFlow||0),'净流入/流出'),
  ].join('');

  // Action table
  var actionLabels={sell:'🛑 卖出',reduce:'📉 减仓',add:'📈 加仓',buy:'🆕 买入'};
  var actionColors={sell:'var(--red)',reduce:'var(--orange)',add:'var(--green)',buy:'var(--blue)'};
  var tbody=document.querySelector('#rebalance-exec-table tbody');
  tbody.innerHTML=enriched.map(function(a){
    return '<tr>'+
      '<td style="color:'+(actionColors[a.action]||'var(--text)')+'">'+( actionLabels[a.action]||a.action)+'</td>'+
      '<td>'+esc(a.symbol)+'</td>'+
      '<td>'+esc(a.name||'')+'</td>'+
      '<td>'+num(a.currentPrice||0)+'</td>'+
      '<td>'+numInt(a.quantity||0)+'</td>'+
      '<td>¥'+numInt(a.amount||0)+'</td>'+
      '<td>'+esc(a.strategy||'market')+'</td>'+
      '<td><span class="badge badge-yellow">待执行</span></td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="8" style="text-align:center;color:var(--text-dim)">无操作</td></tr>';

  // Reset execution state
  document.getElementById('rebalance-exec-result').innerHTML='';
  document.getElementById('rebalance-exec-status').textContent='请选择试运行或实盘执行';
  document.getElementById('btn-rb-exec').disabled=false;
  document.getElementById('btn-rb-dry-run').disabled=false;
}

function executeRebalancePlan(dryRun){
  if(_rbExecRunning)return;
  if(!_pendingRebalanceActions||!_pendingRebalanceActions.length){
    toast('⚠️ 无待执行操作');return;
  }

  if(!dryRun){
    var modeT=(window._currentViewMode==='live')?'【实盘模式】将提交真实委托':'【模拟盘】将记录模拟委托';
    if(!confirm(modeT+'，是否确认执行调仓？'))return;
  }

  var actions=_pendingRebalanceActions;
  var trailingVal=Number(document.getElementById('rb-exec-trailing').value)||8;
  var createCond=document.getElementById('rb-exec-conds').checked;

  // Convert to recommend-execute format:
  // Buy/Add → buy orders, Sell/Reduce → sell orders
  var buyAllocations=[];
  var sellOrders=[];
  actions.forEach(function(a){
    if(a.action==='buy'||a.action==='add'){
      buyAllocations.push({
        code:a.symbol,name:a.name||a.symbol,
        shares:a.quantity||0,
        price:a.currentPrice||0,
        entryPrices:{batch1:{price:a.currentPrice,pct:100}},
      });
    }
    if(a.action==='sell'||a.action==='reduce'){
      sellOrders.push({
        symbol:a.symbol,name:a.name||a.symbol,
        quantity:a.quantity||0,
        price:a.currentPrice||0,
        side:'sell',
      });
    }
  });

  _rbExecRunning=true;
  var dryBtn=document.getElementById('btn-rb-dry-run');
  var liveBtn=document.getElementById('btn-rb-exec');
  dryBtn.disabled=true;liveBtn.disabled=true;
  var statusEl=document.getElementById('rebalance-exec-status');
  var resultEl=document.getElementById('rebalance-exec-result');
  statusEl.textContent=dryRun?'正在试运行…':'正在提交委托…';

  // Execute sells first, then buys
  var sellPromise;
  if(sellOrders.length&&!dryRun){
    sellPromise=_executeSellOrders(sellOrders);
  }else{
    sellPromise=Promise.resolve({submitted:0,orders:[]});
  }

  sellPromise.then(function(sellResult){
    if(buyAllocations.length){
      return api('/quant/admin/api/recommend-execute',{
        method:'POST',
        body:JSON.stringify({
          dry_run:dryRun,
          execute_pct:100,
          create_conditional_orders:createCond,
          trailing_stop_pct:trailingVal,
          allocations:buyAllocations,
        }),
        _timeout:60000
      }).then(function(buyResp){
        return {sellResult:sellResult,buyResp:buyResp};
      });
    }
    return {sellResult:sellResult,buyResp:null};
  }).then(function(combined){
    _rbExecRunning=false;
    dryBtn.disabled=false;

    var sellRes=combined.sellResult||{};
    var buyResp=combined.buyResp||{};

    if(dryRun){
      var plan=buyResp.plan||{};
      var items=plan.items||[];
      statusEl.style.color='var(--green)';
      statusEl.textContent='试运行完成：买入 '+items.length+' 笔，卖出 '+sellOrders.length+' 笔';
      var html='<div class="card" style="padding:10px;font-size:12px">';
      if(items.length){
        html+='<strong>买入计划：</strong><div style="overflow-x:auto;margin-top:4px"><table style="font-size:12px"><thead><tr><th>代码</th><th>数量</th><th>限价</th><th>金额</th></tr></thead><tbody>';
        items.forEach(function(it){
          html+='<tr><td>'+esc(it.code)+'</td><td>'+numInt(it.quantity)+'</td><td>'+num(it.limitPrice)+'</td><td>¥'+numInt(it.notional)+'</td></tr>';
        });
        html+='</tbody></table></div>';
      }
      if(sellOrders.length){
        html+='<div style="margin-top:8px"><strong>卖出计划：</strong></div>';
        sellOrders.forEach(function(s){
          html+='<div>'+esc(s.symbol)+' '+esc(s.name)+' × '+numInt(s.quantity)+' 股 @ ¥'+num(s.price)+'</div>';
        });
      }
      html+='</div>';
      resultEl.innerHTML=html;
      toast('✅ 试运行完成');
      return;
    }

    // Real execution results
    var buyExec=buyResp.execution||{};
    var totalSubmitted=(Number(buyExec.submitted)||0)+(Number(sellRes.submitted)||0);
    var totalFailed=(Number(buyExec.rejected)||0)+(Number(sellRes.rejected)||0);
    var conds=Number(buyExec.conditionalOrders)||0;

    statusEl.style.color=totalFailed>0?'var(--orange)':'var(--green)';
    statusEl.textContent='执行完成：已提交 '+totalSubmitted+' 笔，失败 '+totalFailed+' 笔，条件单 '+conds+' 个';

    var html='<div class="card" style="padding:10px;font-size:12px">';
    var allOrders=[];
    if(sellRes.orders)sellRes.orders.forEach(function(o){o._side='卖出';allOrders.push(o)});
    if(buyExec.orders)(buyExec.orders).forEach(function(o){o._side='买入';allOrders.push(o)});
    if(allOrders.length){
      html+='<div style="overflow-x:auto"><table style="font-size:12px"><thead><tr><th>方向</th><th>代码</th><th>数量</th><th>价格</th><th>订单ID</th><th>状态</th></tr></thead><tbody>';
      allOrders.forEach(function(o){
        html+='<tr><td>'+(o._side||'')+'</td><td>'+esc(o.code||o.symbol)+'</td><td>'+numInt(o.quantity)+'</td><td>'+num(o.price)+'</td><td style="font-size:10px">'+esc((o.orderId||'').slice(0,8))+'</td><td>'+esc(o.status||'submitted')+'</td></tr>';
      });
      html+='</tbody></table></div>';
    }else{
      html+='<div style="color:var(--text-dim)">无成功委托</div>';
    }
    html+='</div>';
    resultEl.innerHTML=html;
    liveBtn.disabled=true; // Prevent re-execution

    toast(totalFailed>0?'⚠️ 调仓已执行，部分失败':'✅ 调仓委托已全部提交');

    // Start fill polling
    if(totalSubmitted>0){
      _startRebalanceFillPolling(allOrders);
    }
    // Refresh holdings after delay
    setTimeout(function(){loadRebalancePortfolio()},5000);

  }).catch(function(e){
    _rbExecRunning=false;
    dryBtn.disabled=false;liveBtn.disabled=false;
    statusEl.style.color='var(--red)';
    statusEl.textContent='执行失败: '+e.message;
  });
}

function _executeSellOrders(sellOrders){
  // Use the order API to submit sell orders
  var results={submitted:0,rejected:0,orders:[]};
  var promises=sellOrders.map(function(s){
    return api('/quant/api/orders',{
      method:'POST',
      body:JSON.stringify({
        symbol:s.symbol,
        side:'sell',
        orderType:s.price>0?'limit':'market',
        quantity:s.quantity,
        price:s.price||undefined,
      }),
      _timeout:30000
    }).then(function(r){
      if(r.status==='error'||r.error){
        results.rejected++;
        return;
      }
      results.submitted++;
      var order=r.data||r;
      results.orders.push({code:s.symbol,symbol:s.symbol,name:s.name,quantity:s.quantity,price:s.price,orderId:order.id||order.orderId||'',status:order.status||'submitted'});
    }).catch(function(){results.rejected++});
  });
  return Promise.all(promises).then(function(){return results});
}

var _rbFillPollTimer=null;
function _startRebalanceFillPolling(orders){
  if(_rbFillPollTimer)clearInterval(_rbFillPollTimer);
  var orderIds=orders.map(function(o){return o.orderId}).filter(Boolean);
  if(!orderIds.length)return;
  var pollCount=0;
  _rbFillPollTimer=setInterval(function(){
    pollCount++;
    if(pollCount>60){clearInterval(_rbFillPollTimer);_rbFillPollTimer=null;return;}
    api('/quant/admin/api/order-fill-status',{
      method:'POST',
      body:JSON.stringify({orderIds:orderIds}),
      _timeout:10000
    }).then(function(d){
      if(!d||!d.orders)return;
      var allDone=true;
      d.orders.forEach(function(o){
        if(o.status!=='filled'&&o.status!=='cancelled'&&o.status!=='rejected')allDone=false;
      });
      // Update status in exec table
      var statusEl=document.getElementById('rebalance-exec-status');
      var filled=d.orders.filter(function(o){return o.status==='filled'}).length;
      statusEl.textContent='成交确认: '+filled+'/'+d.orders.length+' 已成交'+(allDone?'' : '，轮询中…');
      if(allDone){
        clearInterval(_rbFillPollTimer);_rbFillPollTimer=null;
        statusEl.style.color='var(--green)';
        statusEl.textContent='✅ 全部委托已处理完成 ('+filled+' 成交)';
        toast('✅ 调仓委托全部处理完成');
        loadRebalancePortfolio();
      }
    }).catch(function(){});
  },5000);
}

/* ── Apply AI suggestions to the interactive table ── */
function applyAiRebalanceToTable(){
  if(!_lastAiRebalance||!_lastAiRebalance.actions)return;
  _lastAiRebalance.actions.forEach(function(a){
    // Find matching position
    var matchIdx=-1;
    _rebalancePositions.forEach(function(p,i){if(p.symbol===a.symbol)matchIdx=i});
    if(matchIdx>=0&&a.action!=='buy'){
      var sel=document.getElementById('rb-action-'+matchIdx);
      if(sel){sel.value=a.action;_onRebalanceActionChange(matchIdx,'existing',a.action);}
      // Set quantity
      setTimeout(function(){
        if(a.action==='add'||a.action==='reduce'){
          var qEl=document.getElementById('rb-qty-'+matchIdx);
          if(qEl&&a.suggestedShares)qEl.value=a.suggestedShares;
        }
        var noteEl=document.getElementById('rb-note-'+matchIdx);
        if(noteEl)noteEl.value=a.reason||'';
      },50);
    }else if(a.action==='buy'){
      showAddNewPositionRow();
      var ni=_rebalanceNewRows-1;
      setTimeout(function(){
        var sEl=document.getElementById('rbn-symbol-'+ni);
        var nEl=document.getElementById('rbn-name-'+ni);
        var pEl=document.getElementById('rbn-price-'+ni);
        var qEl=document.getElementById('rbn-qty-'+ni);
        if(sEl)sEl.value=a.symbol;
        if(nEl)nEl.value=a.name||'';
        if(pEl)pEl.value=a.currentPrice||'';
        if(qEl&&a.suggestedShares)qEl.value=a.suggestedShares;
      },50);
    }
  });
  toast('📥 AI 建议已应用到持仓表');
}

/* ══════ SMART STRATEGY ADVISOR ══════ */
var _lastSmartStrategy=null;

function runSmartStrategy(){
  // Prefer actual loaded positions from broker/monitor over plan
  var positions=_rebalancePositions;
  if(!positions||!positions.length){
    if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
      toast('⚠️ 请先加载持仓或生成投资计划');return;
    }
    // fallback to plan portfolio
    positions=currentPlan.portfolio;
  }
  var btn=document.getElementById('btn-smart-strategy');
  btn.disabled=true;btn.textContent='⏳ 分析中…';
  var statusEl=document.getElementById('rebalance-status');
  statusEl.textContent='🧠 正在获取行情、技术指标、宏观数据…';
  toast('🧠 正在进行智能策略诊断（'+positions.length+'只持仓）…');

  // Build plan payload from actual positions
  var planPayload={
    portfolio:positions.map(function(p){
      return {
        symbol:p.symbol,
        name:p.name||p.symbol,
        shares:p.shares||p.quantity||0,
        price:p.cost||p.avgCost||p.buyPrice||0,
        stopLoss:p.stopLoss||0,
        takeProfit:p.takeProfit||0,
      };
    }),
    initialCapital:(currentPlan&&currentPlan.initialCapital)||0,
  };

  api('/quant/admin/api/smart-strategy',{
    method:'POST',
    body:JSON.stringify({plan:planPayload}),
    _timeout:300000
  }).then(function(d){
    btn.disabled=false;btn.textContent='🧠 智能策略诊断';
    if(d.error){statusEl.textContent='诊断失败: '+d.error;toast('❌ '+d.error);return;}
    statusEl.textContent='✅ 智能策略诊断完成';
    _lastSmartStrategy=d;
    renderSmartStrategy(d);
  }).catch(function(e){
    btn.disabled=false;btn.textContent='🧠 智能策略诊断';
    statusEl.textContent='诊断失败: '+e.message;
  });
}

function renderSmartStrategy(d){
  document.getElementById('smart-strategy-results').style.display='block';

  // Macro regime banner
  var macro=d.macroRegime||{};
  var regimeColors={recovery:'var(--green)',overheating:'var(--orange)',stagflation:'var(--red)',recession:'var(--red)',unknown:'var(--text-dim)'};
  var regimeIcons={recovery:'🟢',overheating:'🟡',stagflation:'🔴',recession:'⚫',unknown:'❓'};
  var regimeNames={recovery:'复苏期',overheating:'过热期',stagflation:'滞胀期',recession:'衰退期',unknown:'未知'};
  var regime=macro.regime||'unknown';
  document.getElementById('smart-strategy-macro').innerHTML=
    '<div style="padding:8px 12px;border-radius:6px;background:rgba(139,92,246,.08);border:1px solid rgba(139,92,246,.2)">'+
    '<strong>'+( regimeIcons[regime]||'❓')+' 宏观周期: '+(regimeNames[regime]||regime)+'</strong>'+
    '<span style="font-size:12px;color:var(--text-dim);margin-left:8px">'+ esc(macro.description||'')+'</span></div>';

  // KPIs
  var s=d.summary||{};
  document.getElementById('smart-strategy-kpis').innerHTML=[
    kpiHtml(s.totalPositions||0,'诊断持仓'),
    kpiHtml(s.sellCount||0,'<span style="color:var(--red)">建议卖出</span>'),
    kpiHtml(s.reduceCount||0,'<span style="color:var(--orange)">建议减仓</span>'),
    kpiHtml(s.holdCount||0,'建议持有'),
    kpiHtml(s.addCount||0,'<span style="color:var(--green)">建议加仓</span>'),
    kpiHtml(pct(s.avgConfidence||0),'平均置信度'),
  ].join('');

  // Position cards
  var positions=d.positions||[];
  var cardsHtml='';
  positions.forEach(function(p){
    var rec=p.recommendation||{};
    var tech=p.technicals||{};
    var manip=p.manipulation||{};
    var combo=p.routerCombo;
    var actionColor={sell:'var(--red)',reduce:'var(--orange)',hold:'var(--text-dim)',add:'var(--green)'};
    var actionLabel={sell:'🛑 卖出',reduce:'📉 减仓',hold:'✊ 持有',add:'📈 加仓'};
    var confColor=rec.confidence>=70?'var(--green)':rec.confidence>=50?'var(--orange)':'var(--text-dim)';

    cardsHtml+='<div class="card" style="margin-top:8px;border-left:3px solid '+(actionColor[rec.action]||'var(--border)')+';padding:12px">';

    // Header row
    cardsHtml+='<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">';
    cardsHtml+='<a href="'+B+'/quant/admin/stock?symbol='+esc(p.symbol)+'" target="_blank" style="color:var(--accent);font-weight:700;font-size:15px">'+esc(p.symbol)+'</a>';
    cardsHtml+='<span style="font-weight:600">'+esc(p.name)+'</span>';
    cardsHtml+='<span class="badge badge-'+(rec.action==='sell'?'red':rec.action==='reduce'?'orange':rec.action==='add'?'green':'yellow')+'" style="font-size:13px">'+(actionLabel[rec.action]||rec.action)+'</span>';
    cardsHtml+='<span style="font-size:12px;color:'+confColor+'">置信度 '+rec.confidence+'%</span>';
    cardsHtml+='<span style="font-size:12px;color:var(--text-dim)">现价 '+num(p.currentPrice)+'</span>';
    cardsHtml+='<span style="font-size:12px;color:'+pctColor(p.pnlPct)+'">盈亏 '+pct(p.pnlPct)+'</span>';
    cardsHtml+='<span style="font-size:12px;color:var(--text-dim)">仓位 '+pct(p.weight)+'</span>';
    cardsHtml+='</div>';

    // Strategy recommendation
    cardsHtml+='<div style="background:rgba(139,92,246,.06);border-radius:6px;padding:8px 10px;margin-bottom:8px">';
    cardsHtml+='<div style="font-weight:600;font-size:13px">📌 策略: '+esc(rec.strategyName||'')+'</div>';
    cardsHtml+='<div style="font-size:12px;color:var(--text-dim);margin-top:2px">'+esc(rec.strategyDetail||'')+'</div>';
    cardsHtml+='</div>';

    // Two-column detail
    cardsHtml+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:12px">';

    // Left: Technical indicators
    cardsHtml+='<div style="background:var(--bg);border-radius:4px;padding:6px 8px">';
    cardsHtml+='<div style="font-weight:600;margin-bottom:4px">📊 技术指标</div>';
    var rsiVal=tech.rsi14;
    var rsiColor=!rsiVal?'var(--text-dim)':rsiVal>70?'var(--red)':rsiVal<30?'var(--green)':'var(--text)';
    cardsHtml+='<div>RSI(14): <span style="color:'+rsiColor+';font-weight:600">'+(rsiVal?rsiVal.toFixed(0):'N/A')+'</span>';
    cardsHtml+=(rsiVal>70?' 超买':rsiVal<30?' 超卖':'')+'</div>';
    cardsHtml+='<div>趋势得分: '+(tech.trendScore||0)+'/4 <span style="color:var(--text-dim)">('+(['极弱','弱','中性','强','极强'][tech.trendScore||0])+')</span></div>';
    cardsHtml+='<div>5日动量: <span style="color:'+pctColor(tech.momentum5d||0)+'">'+pct(tech.momentum5d||0)+'</span></div>';
    cardsHtml+='<div>20日动量: <span style="color:'+pctColor(tech.momentum20d||0)+'">'+pct(tech.momentum20d||0)+'</span></div>';
    cardsHtml+='<div>量能: '+esc(tech.volumeTier||'N/A')+' ('+num(tech.volumeRatio||0)+')</div>';
    cardsHtml+='<div>价格位置: '+(tech.pricePosition!=null?(tech.pricePosition*100).toFixed(0)+'%':'N/A')+' <span style="color:var(--text-dim)">(20日高低)</span></div>';
    cardsHtml+='</div>';

    // Right: Pattern & signals
    cardsHtml+='<div style="background:var(--bg);border-radius:4px;padding:6px 8px">';
    cardsHtml+='<div style="font-weight:600;margin-bottom:4px">🔍 市场信号</div>';
    var patternLabels={none:'无异常',accumulation:'🧲 吸筹迹象',distribution:'📤 出货迹象',churning:'🔄 对倒交易'};
    var mp=manip.pattern||'none';
    cardsHtml+='<div>主力行为: <span style="font-weight:600">'+(patternLabels[mp]||mp)+'</span>';
    if(manip.confidence)cardsHtml+=' (置信 '+(manip.confidence<=1?(manip.confidence*100).toFixed(0):manip.confidence.toFixed(0))+'%)';
    cardsHtml+='</div>';
    if(manip.signals&&manip.signals.length){
      cardsHtml+='<div style="margin-top:3px;color:var(--text-dim)">';
      manip.signals.slice(0,3).forEach(function(s){cardsHtml+='<div>• '+esc(s)+'</div>'});
      cardsHtml+='</div>';
    }
    // Decision reasons
    if(rec.reasons&&rec.reasons.length){
      cardsHtml+='<div style="margin-top:6px;font-weight:600">💡 判断依据</div>';
      rec.reasons.forEach(function(r){cardsHtml+='<div style="color:var(--text-dim)">• '+esc(r)+'</div>'});
    }
    cardsHtml+='</div>';

    cardsHtml+='</div>'; // end grid

    // Router combo (if available)
    if(combo&&combo.name){
      cardsHtml+='<div style="margin-top:6px;font-size:11px;padding:4px 8px;background:rgba(99,102,241,.06);border-radius:4px">';
      cardsHtml+='<strong>🔀 策略路由: </strong>'+esc(combo.name)+' ('+esc(combo.method||'')+')';
      if(combo.strategies&&combo.strategies.length)cardsHtml+=' — '+combo.strategies.map(esc).join('+');
      if(combo.reason)cardsHtml+=' <span style="color:var(--text-dim)">| '+esc(combo.reason)+'</span>';
      if(combo.stockProfile){
        var sp=combo.stockProfile;
        cardsHtml+=' <span style="color:var(--text-dim)">| 画像: '+esc(sp.volatility)+'/'+esc(sp.trend)+'/'+esc(sp.marketCap)+'</span>';
      }
      cardsHtml+='</div>';
    }

    cardsHtml+='</div>'; // end card
  });
  document.getElementById('smart-strategy-cards').innerHTML=cardsHtml||'<p style="text-align:center;color:var(--text-dim)">无持仓数据</p>';
  toast('🧠 诊断完成');
}

function applySmartStrategyToTable(){
  if(!_lastSmartStrategy||!_lastSmartStrategy.positions)return;
  _lastSmartStrategy.positions.forEach(function(p){
    var rec=p.recommendation||{};
    if(rec.action==='hold')return;
    var matchIdx=-1;
    _rebalancePositions.forEach(function(pos,i){if(pos.symbol===p.symbol)matchIdx=i});
    if(matchIdx<0)return;
    var sel=document.getElementById('rb-action-'+matchIdx);
    if(sel){sel.value=rec.action;_onRebalanceActionChange(matchIdx,'existing',rec.action);}
    setTimeout(function(){
      var noteEl=document.getElementById('rb-note-'+matchIdx);
      if(noteEl)noteEl.value=rec.strategyName+': '+rec.strategyDetail;
      // Select matching strategy in dropdown
      var stratEl=document.getElementById('rb-strat-'+matchIdx);
      if(stratEl){
        var strat=rec.strategyName||'';
        if(strat.indexOf('分批')>=0||strat.indexOf('阶梯')>=0)stratEl.value='batch';
        else if(strat.indexOf('移动止')>=0)stratEl.value='trailing';
        else if(strat.indexOf('止损')>=0)stratEl.value='stop';
        else if(strat.indexOf('限价')>=0)stratEl.value='limit';
      }
    },50);
  });
  toast('📥 智能策略建议已应用到持仓表');
}

/* ── AI auto-suggest (original runRebalance) ── */
function runRebalance(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    toast('⚠️ 请先生成投资计划');_activateTab('plan');history.pushState(null,null,'#plan');return;
  }
  var btn=document.getElementById('btn-ai-rebalance');
  btn.disabled=true;btn.textContent='⏳ 正在分析…';
  var statusEl=document.getElementById('rebalance-status');
  statusEl.textContent='正在获取行情并分析调仓…';
  toast('正在生成调仓建议…');

  // Get latest screener results
  var symVal=(document.getElementById('rec-symbols').value||'').trim();
  var body={
    action:'rebalance',
    plan:currentPlan,
    newCandidates:symVal?symVal.split(',').map(function(s){return s.trim()}).filter(Boolean):[]
  };

  api('/quant/admin/api/rebalance',{method:'POST',body:JSON.stringify(body),_timeout:60000})
    .then(function(d){
      btn.disabled=false;btn.textContent='🤖 AI 自动建议';
      if(d.error){statusEl.textContent='分析失败: '+d.error;toast('❌ '+d.error);return;}
      statusEl.textContent='AI 分析完成';
      _lastAiRebalance=d;
      renderRebalance(d);
    })
    .catch(function(e){
      btn.disabled=false;btn.textContent='🤖 AI 自动建议';
      statusEl.textContent='分析失败: '+e.message;
    });
}

function renderRebalance(d){
  document.getElementById('rebalance-results').style.display='block';
  var s=d.summary||{};
  document.getElementById('rebalance-kpis').innerHTML=[
    kpiHtml(s.sellCount||0,'<span style="color:var(--red)">卖出</span>'),
    kpiHtml(s.reduceCount||0,'<span style="color:var(--orange)">减仓</span>'),
    kpiHtml(s.holdCount||0,'持有'),
    kpiHtml(s.addCount||0,'<span style="color:var(--blue)">加仓</span>'),
    kpiHtml(s.buyCount||0,'<span style="color:var(--green)">新买</span>'),
  ].join('');

  // Action cards
  var actions=d.actions||[];
  var actionsEl=document.getElementById('rebalance-actions');
  var grouped={sell:[],reduce:[],hold:[],add:[],buy:[]};
  actions.forEach(function(a){
    if(grouped[a.action])grouped[a.action].push(a);
  });
  var groupLabels={sell:'🛑 卖出',reduce:'📉 减仓',hold:'✊ 持有',add:'📈 加仓',buy:'🆕 新买'};
  var groupColors={sell:'var(--red)',reduce:'var(--orange)',hold:'var(--text-dim)',add:'var(--blue)',buy:'var(--green)'};
  var html='';
  Object.keys(groupLabels).forEach(function(key){
    var items=grouped[key]||[];
    if(!items.length)return;
    html+='<div class="card" style="margin-top:10px;border-left:3px solid '+groupColors[key]+'">';
    html+='<h3>'+groupLabels[key]+' ('+items.length+'ֻ)</h3>';
    html+='<div style="display:flex;flex-wrap:wrap;gap:6px">';
    items.forEach(function(a){
      html+='<div style="background:var(--card-bg);border:1px solid var(--border);border-radius:6px;padding:8px 12px;min-width:180px">';
      html+='<div style="font-weight:600"><a href="'+B+'/quant/admin/stock?symbol='+esc(a.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(a.symbol)+'</a> '+esc(a.name||'')+'</div>';
      html+='<div style="font-size:12px;color:var(--text-dim)">'+esc(a.reason||'')+'</div>';
      if(a.currentPrice)html+='<div style="font-size:11px;margin-top:2px">现价: '+num(a.currentPrice)+(a.changePct!=null?' <span style="color:'+pctColor(a.changePct)+'">'+pct(a.changePct)+'</span>':'')+'</div>';
      if(a.suggestedShares)html+='<div style="font-size:11px">建议: '+(a.action==='sell'||a.action==='reduce'?'卖出':'买入')+' '+numInt(a.suggestedShares)+' 股</div>';
      html+='</div>';
    });
    html+='</div></div>';
  });
  actionsEl.innerHTML=html||'<div class="card"><h3>✅ 无需调仓</h3><p style="font-size:12px;color:var(--text-dim)">当前持仓状态良好</p></div>';

  // Detail table
  var tbody=document.querySelector('#rebalance-table tbody');
  tbody.innerHTML=actions.map(function(a){
    var actionBadge={sell:'<span class="badge badge-red">卖出</span>',reduce:'<span class="badge badge-orange">减仓</span>',hold:'<span class="badge badge-yellow">持有</span>',add:'<span class="badge badge-blue">加仓</span>',buy:'<span class="badge badge-green">新买</span>'};
    return '<tr>'+
      '<td>'+(actionBadge[a.action]||esc(a.action))+'</td>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+esc(a.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(a.symbol)+'</a></td>'+
      '<td>'+esc(a.name||'')+'</td>'+
      '<td>'+num(a.currentPrice)+'</td>'+
      '<td style="color:'+pctColor(a.changePct)+'">'+pct(a.changePct)+'</td>'+
      '<td>'+pct(a.targetWeight||0)+'</td>'+
      '<td>'+pct(a.currentWeight||0)+'</td>'+
      '<td>'+numInt(a.suggestedShares||0)+'</td>'+
      '<td style="font-size:11px">'+esc(a.reason||'')+'</td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="9" style="text-align:center;color:var(--text-dim)">无需调仓</td></tr>';

  toast('✅ 调仓建议已生成');
}

function _showSignalEmpty(){
  document.getElementById('signal-kpis').innerHTML='<div style="width:100%;text-align:center;padding:40px 20px"><div style="font-size:48px;margin-bottom:12px">⚠️</div><div style="font-size:16px;margin-bottom:16px;color:var(--text-dim)">请先生成投资计划并刷新持仓</div><button class="btn" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">📋 前往制定计划</button></div>';
}

/* ══════ SIGNALS ══════ */
function loadSignals(){
  if(!currentPlan||!currentPlan.portfolio||!currentPlan.portfolio.length){
    // Auto-load latest plan from history before giving up
    api('/quant/admin/api/plan-history').then(function(d){
      if(d.entries&&d.entries.length>0){
        var latest=d.entries[0];
        api('/quant/admin/api/plan-history?id='+encodeURIComponent(latest.id)).then(function(r){
          if(r.status==='ok'&&r.entry&&r.entry.fullPlan){
            currentPlan=r.entry.fullPlan;
            currentPlanVersion=r.entry.version||null;
            try{sessionStorage.setItem('openclaw-plan-cache',JSON.stringify(currentPlan))}catch(e){}
            loadSignals();
          }else{
            _showSignalEmpty();
          }
        }).catch(function(){_showSignalEmpty()});
      }else{
        _showSignalEmpty();
      }
    }).catch(function(){_showSignalEmpty()});
    return;
  }
  // Refresh portfolio to get latest signals
  var _mode=window._currentViewMode||'paper';
  _getSyncedPlan(currentPlan).then(function(syncedPlan){
  api('/quant/admin/api/investment-monitor',{method:'POST',body:JSON.stringify({action:'monitor',plan:syncedPlan,mode:_mode}),_timeout:60000}).then(function(d){
    var signals=d.signals||[];
    _lastPortfolioSignals[_mode]=signals;
    var modeTag=_mode==='live'?'🔴 实盘':'🟢 模拟盘';
    document.getElementById('signal-kpis').innerHTML=[
      kpiHtml(modeTag,'当前模式'),
      kpiHtml(signals.length,'当前信号'),
      kpiHtml(signals.filter(function(s){return s.urgency==='紧急'}).length,'<span style="color:var(--red)">紧急</span>'),
      kpiHtml(signals.filter(function(s){return s.urgency==='重要'}).length,'<span style="color:var(--orange)">重要</span>'),
      kpiHtml(signals.filter(function(s){return s.urgency==='注意'}).length,'注意'),
    ].join('');

    var sl=document.getElementById('signal-list');
    if(!signals.length){
      sl.innerHTML='<div class="card"><h3>✅ 当前无交易信号</h3><p style="font-size:12px;color:var(--text-dim)">所有持仓状态正常，无需操作</p></div>';
    }else{
      sl.innerHTML=signals.map(function(s){
        var cls=s.urgency==='紧急'?'urgent':s.urgency==='重要'?'important':'';
        var icon=s.type==='STOP_LOSS'?'🛑':s.type==='TAKE_PROFIT'?'💰':s.type==='VOLUME_SPIKE'?'📊':'⚠️';
        var actionBadge=s.action==='卖出'?'<span class="badge badge-red">'+esc(s.action)+'</span>':
                        s.action==='减仓'?'<span class="badge badge-orange">'+esc(s.action)+'</span>':
                        '<span class="badge badge-yellow">'+esc(s.action)+'</span>';
        return '<div class="card signal-card '+cls+'">'+
          '<h3>'+icon+' '+esc(s.symbol)+' '+esc(s.name)+' '+actionBadge+
          ' <span class="badge badge-'+(s.urgency==='紧急'?'red':s.urgency==='重要'?'orange':'yellow')+'">'+esc(s.urgency)+'</span></h3>'+
          '<p style="font-size:12px">'+esc(s.message)+'</p>'+
          '<div style="font-size:12px;color:var(--text-dim);margin-top:4px">'+
            '现价: '+num(s.currentPrice)+
            (s.triggerPrice?' | 触发价: '+num(s.triggerPrice):'')+
            (s.pnlPct!=null?' | 盈亏: <span style="color:'+pctColor(s.pnlPct)+'">'+pct(s.pnlPct)+'</span>':'')+
          '</div></div>';
      }).join('');
    }

    // Signal history table (per mode) with dedup – skip if same symbol+type within 5min
    if(d.signals&&d.signals.length){
      var DEDUP_MS=5*60*1000;
      d.signals.forEach(function(s){
        s.timestamp=d.timestamp;s._mode=_mode;
        var dup=signalHistory[_mode].some(function(h){
          return h.symbol===s.symbol&&h.type===s.type&&(new Date(s.timestamp).getTime()-new Date(h.timestamp).getTime()<DEDUP_MS);
        });
        if(!dup){signalHistory[_mode].unshift(s)}
      });
      if(signalHistory[_mode].length>100)signalHistory[_mode]=signalHistory[_mode].slice(0,100);
    }
    var _histBadge=document.getElementById('signal-history-mode-badge');
    if(_histBadge){_histBadge.innerHTML=_mode==='live'?'<span class="badge badge-red">实盘</span>':'<span class="badge badge-green">模拟盘</span>';}
    var ht=document.querySelector('#signal-history-table tbody');
    ht.innerHTML=(signalHistory[_mode]||[]).slice(0,50).map(function(s){
      return '<tr>'+
        '<td style="white-space:nowrap;font-size:11px">'+esc(s.timestamp||'-')+'</td>'+
        '<td><span class="badge badge-'+(s.type==='STOP_LOSS'?'red':s.type==='TAKE_PROFIT'?'green':'yellow')+'">'+esc(s.type)+'</span></td>'+
        '<td>'+esc(s.symbol)+' '+esc(s.name||'')+'</td>'+
        '<td>'+esc(s.action)+'</td>'+
        '<td>'+num(s.currentPrice)+'</td>'+
        '<td style="color:'+(s.pnlPct>=0?'var(--green)':'var(--red)')+'">'+pct(s.pnlPct)+'</td>'+
        '<td style="font-size:11px;max-width:200px;word-break:break-all">'+esc(s.message)+'</td>'+
        '</tr>';
    }).join('')||'<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">暂无历史信号</td></tr>';
  });
  });
}

/* ══════ MILESTONES ══════ */
function loadMilestones(){
  if(!currentPlan||!currentPlan.milestones){
    document.getElementById('milestone-kpis').innerHTML='<div style="width:100%;text-align:center;padding:40px 20px"><div style="font-size:48px;margin-bottom:12px">⚠️</div><div style="font-size:16px;margin-bottom:16px;color:var(--text-dim)">请先生成投资计划</div><button class="btn" onclick="_activateTab(\\'plan\\');history.pushState(null,null,\\'#plan\\')">📋 前往制定计划</button></div>';
    return;
  }
  var plan=currentPlan.plan;
  var milestones=currentPlan.milestones;
  var now=new Date();
  var planDate=new Date(currentPlan.generatedAt||now);
  var monthsElapsed=Math.max(0,(now.getFullYear()-planDate.getFullYear())*12+(now.getMonth()-planDate.getMonth()));

  document.getElementById('milestone-kpis').innerHTML=[
    kpiHtml('¥'+numInt(plan.initialCapital),'初始资金'),
    kpiHtml('¥'+numInt(plan.targetCapital),'目标资金'),
    kpiHtml(plan.requiredReturn+'%','目标收益'),
    kpiHtml(monthsElapsed+'/'+plan.timeframeMonths,'已过/总月数'),
  ].join('');

  // Milestone chart
  var chart=document.getElementById('milestone-chart');
  chart.innerHTML=milestones.map(function(m){
    var progress=Math.min(monthsElapsed>=m.month?100:monthsElapsed>=m.month-1?(monthsElapsed-m.month+1)*100:0,100);
    var isCurrent=m.month===monthsElapsed+1;
    return '<div style="display:flex;align-items:center;gap:8px;padding:4px 0;'+(isCurrent?'background:var(--bg3);border-radius:4px;padding:6px 8px':'')+'">' +
      '<span style="min-width:40px;font-size:12px;color:'+(isCurrent?'var(--accent)':'var(--text-dim)')+'">第'+m.month+'月</span>' +
      '<div class="milestone-bar" style="flex:1"><div class="milestone-fill" style="width:'+progress+'%"></div></div>' +
      '<span style="min-width:100px;font-size:12px;text-align:right">¥'+numInt(m.targetCapital)+'</span>' +
      '<span style="min-width:60px;font-size:11px;color:var(--text-dim);text-align:right">+'+m.targetReturn+'%</span>' +
      '</div>';
  }).join('');

  // Milestone details table
  var tbody=document.querySelector('#milestone-table tbody');
  tbody.innerHTML=milestones.map(function(m){
    var status=monthsElapsed>=m.month?'<span class="badge badge-green">已过</span>':
              m.month===monthsElapsed+1?'<span class="badge badge-blue">当前</span>':
              '<span class="badge badge-yellow">未到</span>';
    return '<tr>'+
      '<td>第 '+m.month+' 月</td>'+
      '<td>¥'+numInt(m.targetCapital)+'</td>'+
      '<td>+'+m.targetReturn+'%</td>'+
      '<td>-</td><td>-</td>'+
      '<td>'+status+'</td></tr>';
  }).join('');
}

function viewStock(sym){
  window.open(B+'/quant/admin/stock?symbol='+encodeURIComponent(sym),'_blank');
}

function startMonitoring(){
  // Hide fill confirmation panel if visible
  var fillPanel=document.getElementById('rec-fill-confirm');
  if(fillPanel)fillPanel.style.display='none';
  stopFillPolling();
  toast('🚀 监控已启用。切换到"交易信号"查看实时信号');
  // Switch to signals tab
  activateTab('signals');
  history.pushState(null,null,'#signals');
}

/* ── Auto-refresh signals every 30s ── */
setInterval(function(){
  var at=document.querySelector('.tab.active');
  if(!at)return;
  if(at.dataset.tab==='portfolio')refreshPortfolio();
  if(at.dataset.tab==='signals')loadSignals();
},30000);

/* Latest plan auto-load is now handled inside loadPlanHistory() */
`;
}
