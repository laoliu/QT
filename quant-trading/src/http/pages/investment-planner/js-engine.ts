/** Investment Planner — recommendation rendering, engine status, conditional orders, breakout */
export function getEngineJs(): string {
  return `
function renderRecommend(d){
  _lastRecommendData=d;
  document.getElementById('rec-results').style.display='block';

  // Backtest KPIs
  var bs=d.backtestSummary||{};
  document.getElementById('rec-bt-kpis').innerHTML=
    kpiHtml('<span style="color:'+pctColor(bs.totalReturn)+'">'+pct(bs.totalReturn)+'</span>','回测收益')+
    kpiHtml(bs.sharpeRatio!=null?bs.sharpeRatio.toFixed(2):'-','夏普比率')+
    kpiHtml('<span style="color:var(--red)">'+pct(-Math.abs(bs.maxDrawdown||0))+'</span>','最大回撤')+
    kpiHtml((bs.winRate||0).toFixed(1)+'%','胜率')+
    kpiHtml(esc(bs.strategyLabel||bs.strategy),'策略');

  // Allocation KPIs
  var sm=d.summary||{};
  document.getElementById('rec-alloc-kpis').innerHTML=
    kpiHtml(numInt(sm.totalCapital),'总资金')+
    kpiHtml(numInt(sm.investedAmount),'建议投入')+
    kpiHtml(numInt(sm.cashReserve),'现金储备')+
    kpiHtml(sm.cashReservePct+'%','现金比例')+
    kpiHtml(sm.positionCount+'ֻ','持仓数');

  // Allocation table with decision fields
  var tbody=document.querySelector('#rec-table tbody');
  var rows='';
  var allocs=d.allocations||[];
  var execHint=document.getElementById('rec-exec-hint');
  if(execHint){
    execHint.textContent='执行会按分批入场第1笔价格提交限价买单；当前推荐 '+allocs.length+' 只，建议投入 ¥'+numInt(sm.investedAmount||0)+'。';
  }
  // Show broker mode warning
  var modeHintEl=document.getElementById('rec-exec-mode-hint');
  if(modeHintEl){
    api('/quant/admin/api/engine-status').then(function(es){
      var isLive=es.brokerType&&es.brokerType!=='paper';
      if(isLive){
        modeHintEl.style.display='block';
        modeHintEl.style.background='rgba(248,81,73,.12)';
        modeHintEl.style.color='var(--red)';
        modeHintEl.innerHTML='⚠️ 当前引擎为 <strong>实盘模式 ('+esc(es.brokerType||'live')+')</strong>，执行建仓将提交真实委托';
      }else{
        modeHintEl.style.display='block';
        modeHintEl.style.background='rgba(13,148,136,.12)';
        modeHintEl.style.color='var(--green)';
        modeHintEl.innerHTML='🟢 当前引擎为 <strong>模拟盘模式</strong>，执行建仓仅在模拟账户中记录';
      }
    }).catch(function(){
      modeHintEl.style.display='none';
    });
  }
  document.getElementById('rec-exec-status').textContent='';
  document.getElementById('rec-exec-result').innerHTML='';
  allocs.forEach(function(a,i){
    var catBadge=a.category==='core'?'<span class="badge badge-green">核心</span>':
      a.category==='growth'?'<span class="badge badge-blue">成长</span>':
      '<span class="badge badge-purple">卫星</span>';
    var reasonsHtml=(a.reasons||[]).slice(0,5).map(function(r){return '<span class="badge badge-yellow" style="margin:1px">'+esc(r)+'</span>'}).join(' ');
    var riskHtml='';
    if(a.riskLevel==='high'){riskHtml='<span class="badge badge-red" style="margin-left:4px" title="'+esc(a.riskWarning||'')+'">⚠️</span>';}
    else if(a.riskLevel==='medium'){riskHtml='<span class="badge badge-orange" style="margin-left:4px" title="'+esc(a.riskWarning||'')+'">⚡</span>';}

    // Entry prices
    var ep=a.entryPrices;
    var entryHtml='-';
    if(ep){
      entryHtml='<div style="font-size:11px;line-height:1.5">';
      entryHtml+='<div>①'+num(ep.batch1.price)+' <span style="color:var(--text-dim)">('+ep.batch1.pct+'%)</span></div>';
      entryHtml+='<div>②'+num(ep.batch2.price)+' <span style="color:var(--text-dim)">('+ep.batch2.pct+'%)</span></div>';
      entryHtml+='<div>③'+num(ep.batch3.price)+' <span style="color:var(--text-dim)">('+ep.batch3.pct+'%)</span></div>';
      entryHtml+='</div>';
    }

    // Exit targets
    var ex=a.exitTargets;
    var exitHtml='-';
    if(ex){
      exitHtml='<div style="font-size:11px;line-height:1.5">';
      exitHtml+='<div style="color:var(--red)">🛑'+num(ex.stopLoss.price)+' ('+ex.stopLoss.pct+'%)</div>';
      exitHtml+='<div style="color:var(--green)">🎯'+num(ex.takeProfit1.price)+' (+10%)</div>';
      exitHtml+='<div style="color:var(--green)">🎯'+num(ex.takeProfit2.price)+' (+20%)</div>';
      if(ex.chipResistance){exitHtml+='<div style="color:var(--orange)">📊'+num(ex.chipResistance.price)+' (筹码压力)</div>';}
      exitHtml+='</div>';
    }

    // Expected return
    var er=a.expectedReturn;
    var erHtml='-';
    if(er){
      erHtml='<div style="font-size:11px;line-height:1.5">';
      erHtml+='<div style="color:var(--green)">乐观 +'+er.optimistic+'%</div>';
      erHtml+='<div><strong>期望 '+(er.expected>=0?'+':'')+er.expected+'%</strong></div>';
      erHtml+='<div style="color:var(--text-dim)">≈¥'+numInt(er.expectedAmount)+'</div>';
      erHtml+='</div>';
    }

    // Goal contribution
    var gc=a.goalContribution;
    var gcHtml='-';
    if(gc){
      gcHtml='<div style="font-size:11px;line-height:1.5">';
      gcHtml+='<div>份额 '+gc.weight+'%</div>';
      gcHtml+='<div>贡献 ¥'+numInt(gc.expectedContrib)+'</div>';
      gcHtml+='<div style="color:var(--accent)">占目标 '+gc.expectedContribPct+'%</div>';
      gcHtml+='</div>';
    }

    // Hold days
    var holdHtml=a.expectedHoldDays?(a.expectedHoldDays+'天'):'-';

    // Max loss
    var mlHtml=a.maxLossAmount?('<span style="color:var(--red);font-size:11px">最大亏¥'+numInt(a.maxLossAmount)+'</span>'):'';

    rows+='<tr>'+
      '<td>'+(i+1)+'</td>'+
      '<td style="cursor:pointer" onclick="viewStock(\\''+esc(a.code).replace(/'/g,"\\\\'")+'\\')">'+' <strong><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(a.code)+'" target="_blank" style="color:var(--accent);text-decoration:underline;cursor:pointer">'+esc(a.code)+'</a></strong></td>'+
      '<td>'+esc(a.name)+'</td>'+
      '<td>'+num(a.price)+'</td>'+
      '<td style="color:'+pctColor(a.changePct)+'">'+pct(a.changePct)+'</td>'+
      '<td><strong>'+numInt(a.shares)+'</strong></td>'+
      '<td><strong>'+numInt(a.amount)+'</strong><br>'+mlHtml+'</td>'+
      '<td>'+a.weight+'%</td>'+
      '<td>'+entryHtml+'</td>'+
      '<td>'+exitHtml+'</td>'+
      '<td>'+holdHtml+'</td>'+
      '<td>'+erHtml+'</td>'+
      '<td>'+gcHtml+'</td>'+
      '<td><span class="badge '+(a.score>=60?'badge-green':a.score>=40?'badge-yellow':'badge-orange')+'">'+a.score+'</span>'+riskHtml+'</td>'+
      '<td>'+catBadge+'</td>'+
      '<td><div style="display:flex;flex-wrap:wrap;gap:2px">'+reasonsHtml+'</div></td>'+
      '</tr>';
  });
  tbody.innerHTML=rows;

  // Categories cards
  var cats=d.categories||{};
  var catHtml='';
  if(cats.core&&cats.core.length){
    catHtml+='<div class="card"><h3 style="color:var(--green)">🟢 核心仓位（约60%资金 — 长期持有）</h3><ul class="rules-list">';
    cats.core.forEach(function(c){catHtml+='<li><span class="rules-icon">💎</span><div><strong>'+esc(c.code)+' '+esc(c.name)+'</strong> — '+esc((c.reasons||[]).slice(0,2).join(' | '))+'<br><span style="color:var(--text-dim);font-size:11px">'+c.shares+'股 × '+num(c.price)+' = ¥'+numInt(c.amount)+'</span></div></li>'});
    catHtml+='</ul></div>';
  }
  if(cats.growth&&cats.growth.length){
    catHtml+='<div class="card"><h3 style="color:var(--accent)">🔵 成长仓位（约30%资金 — 趋势跟踪）</h3><ul class="rules-list">';
    cats.growth.forEach(function(c){catHtml+='<li><span class="rules-icon">📈</span><div><strong>'+esc(c.code)+' '+esc(c.name)+'</strong> — '+esc((c.reasons||[]).slice(0,2).join(' | '))+'<br><span style="color:var(--text-dim);font-size:11px">'+c.shares+'股 × '+num(c.price)+' = ¥'+numInt(c.amount)+'</span></div></li>'});
    catHtml+='</ul></div>';
  }
  if(cats.satellite&&cats.satellite.length){
    catHtml+='<div class="card"><h3 style="color:var(--purple)">🟣 卫星仓位（约10%资金 — 弹性配置）</h3><ul class="rules-list">';
    cats.satellite.forEach(function(c){catHtml+='<li><span class="rules-icon">🛰️</span><div><strong>'+esc(c.code)+' '+esc(c.name)+'</strong> — '+esc((c.reasons||[]).slice(0,2).join(' | '))+'<br><span style="color:var(--text-dim);font-size:11px">'+c.shares+'股 × '+num(c.price)+' = ¥'+numInt(c.amount)+'</span></div></li>'});
    catHtml+='</ul></div>';
  }
  document.getElementById('rec-categories').innerHTML=catHtml;

  // Risk warning banner
  var riskStocks=allocs.filter(function(a){return a.riskLevel==='high'});
  var medRiskStocks=allocs.filter(function(a){return a.riskLevel==='medium'});
  var riskBanner='';
  if(riskStocks.length>0){
    riskBanner+='<div class="card" style="border:1px solid var(--red);background:rgba(248,81,73,.08);margin-bottom:12px">'+
      '<h3 style="color:var(--red);margin-top:0">⚠️ 风险提示：'+riskStocks.length+'只股票综合评分较低</h3>'+
      '<p style="color:var(--text-dim);font-size:13px;margin:4px 0">综合评分基于估值、技术面、资金面、动量、波动率等多维度分析，低于25分意味着多项指标表现不佳，投资风险较高。建议：</p>'+
      '<ul style="color:var(--text-dim);font-size:12px;margin:4px 0;padding-left:20px">'+
      '<li>降低仓位比例或替换为评分更高的标的</li>'+
      '<li>设置更严格的止损线（建议-5%以内）</li>'+
      '<li>密切关注盘面变化，出现异常及时离场</li></ul>'+
      '<p style="font-size:12px;color:var(--text-dim)">涉及股票：'+riskStocks.map(function(s){return esc(s.code)+' '+esc(s.name)+' ('+s.score+'分)'}).join('、')+'</p></div>';
  }
  if(medRiskStocks.length>0){
    riskBanner+='<div class="card" style="border:1px solid var(--orange);background:rgba(255,166,0,.06);margin-bottom:12px">'+
      '<p style="color:var(--orange);margin:0"><strong>⚡ '+medRiskStocks.length+'只股票评分偏低（<40分）</strong>，建议控制仓位</p></div>';
  }
  document.getElementById('rec-categories').insertAdjacentHTML('beforebegin',riskBanner);

  // Tips
  var tipsHtml='';
  (d.tips||[]).forEach(function(t,i){
    tipsHtml+='<li><span class="rules-icon">'+(i===0?'📋':i===1?'🛑':i===2?'💰':i===3?'🔄':i===4?'💵':'🚨')+'</span>'+esc(t)+'</li>';
  });
  document.getElementById('rec-tips').innerHTML=tipsHtml;

  // Pool table
  var allStocks=d.allStocks||[];
  document.getElementById('rec-pool-count').textContent=allStocks.length;
  var poolRows='';
  allStocks.forEach(function(s){
    var st=s.technicals;
    var sTechHtml='-';
    if(st){
      var sTrend=st.trend_score>=3?'📈多头':st.trend_score>=2?'↗偏多':st.trend_score<=0?'📉偏空':'→震荡';
      var sRsi=st.rsi14!=null?(st.rsi14>70?'🔴'+st.rsi14.toFixed(0):st.rsi14<30?'🟢'+st.rsi14.toFixed(0):st.rsi14.toFixed(0)):'-';
      sTechHtml='<span style="font-size:11px">'+sTrend+' RSI:'+sRsi+'</span>';
    }
    poolRows+='<tr>'+
      '<td style="cursor:pointer" onclick="viewStock(\\''+esc(s.code).replace(/'/g,"\\\\'")+'\\')"><strong><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(s.code)+'" target="_blank" style="color:var(--accent);text-decoration:underline;cursor:pointer" onclick="event.stopPropagation()">'+esc(s.code)+'</a></strong></td>'+
      '<td>'+esc(s.name)+'</td>'+
      '<td>'+num(s.price)+'</td>'+
      '<td style="color:'+pctColor(s.changePct)+'">'+pct(s.changePct)+'</td>'+
      '<td>'+(s.pe?s.pe.toFixed(1):'-')+'</td>'+
      '<td>'+(s.pb?s.pb.toFixed(2):'-')+'</td>'+
      '<td>'+(s.marketCap?s.marketCap.toFixed(0):'-')+'</td>'+
      '<td>'+(s.turnover?s.turnover.toFixed(2):'-')+'</td>'+
      '<td><span class="badge '+(s.score>=60?'badge-green':s.score>=40?'badge-yellow':'badge-orange')+'">'+s.score+'</span></td>'+
      '<td>'+sTechHtml+'</td>'+
      '<td>'+(s.inFinalPosition?'<span class="badge badge-green">回测持仓</span>':'<span class="badge badge-orange" title="回测期间未在最终持仓中，但仍在您的股池中">回测未持</span>')+'</td>'+
      '</tr>';
  });
  document.querySelector('#rec-pool-table tbody').innerHTML=poolRows;

  toast('✅ 建仓推荐已生成');
}

function renderMonthlyChart(monthly){
  var container=document.getElementById('bt-monthly-chart');
  if(!container||!monthly||monthly.length===0){container.innerHTML='<span style="color:var(--text-dim);font-size:12px">无月度数据</span>';return;}
  var maxAbs=1;
  monthly.forEach(function(m){var a=Math.abs(m.return);if(a>maxAbs)maxAbs=a});
  var barWidth=Math.max(16,Math.min(40,Math.floor((container.clientWidth-monthly.length*4)/monthly.length)));
  var midH=60;
  var html='';
  monthly.forEach(function(m){
    var h=Math.abs(m.return)/maxAbs*midH;
    var color=m.return>=0?'var(--green)':'var(--red)';
    var top=m.return>=0?(midH-h):midH;
    html+='<div style="display:flex;flex-direction:column;align-items:center;width:'+barWidth+'px">';
    html+='<div style="height:'+midH+'px;position:relative;width:100%">';
    html+='<div style="position:absolute;bottom:'+(m.return>=0?midH-h-midH+'px':'0')+';top:'+(m.return>=0?midH-h:midH)+'px;left:2px;right:2px;background:'+color+';border-radius:2px;height:'+Math.max(2,h)+'px"></div>';
    html+='</div>';
    html+='<div style="font-size:9px;color:var(--text-dim);margin-top:2px">'+m.month.substring(5)+'</div>';
    html+='<div style="font-size:9px;color:'+color+'">'+(m.return>=0?'+':'')+m.return+'%</div>';
    html+='</div>';
  });
  container.innerHTML=html;
}

/* ══════ CONDITIONAL ORDERS & ENGINE STATUS ══════ */

function toggleCondPanel(){
  var panel=document.getElementById('cond-panel');
  var toggle=document.getElementById('cond-toggle');
  if(panel.style.display==='none'){
    panel.style.display='block';
    toggle.textContent='▼ 收起';
    loadEngineStatus();
    loadConditionalOrders();
  }else{
    panel.style.display='none';
    toggle.textContent='▶ 展开';
  }
}

var _enginePositions=[];
var _enginePendingOrders=[];
var _engineStartPollTimer=null;
function loadEngineStatus(){
  api('/quant/admin/api/engine-status')
    .then(function(d){
      _enginePositions=d.positions||[];
      _enginePendingOrders=(d.pendingOrders||[]).filter(function(o){
        // Exclude orders cancelled locally in the last 10s to prevent flicker
        var t=_recentlyCancelledIds[o.orderId];
        if(t&&Date.now()-t<10000)return false;
        if(t)delete _recentlyCancelledIds[o.orderId];
        return true;
      });
      var el=document.getElementById('engine-status-kpis');
      var mode=d.mode||'unknown';
      var modeColor=d.isRunning?'var(--green)':mode==='starting'?'var(--yellow,orange)':mode==='error'?'var(--red)':'var(--red)';
      var modeLabels={running:'运行中',starting:'启动中…',paused:'已暂停',error:'错误',stopped:'未启动'};
      var modeLabel=modeLabels[mode]||'未启动';
      if(mode==='error'&&d.lastError){
        modeLabel+='<div style="font-size:10px;color:var(--text-dim);margin-top:4px;word-break:break-all;max-width:200px">'+esc(d.lastError)+'</div>';
      }
      var toggleBtn='';
      if(mode==='starting'){
        toggleBtn='<span style="margin-left:6px;font-size:10px;color:orange">⏳</span>';
      }else if(d.isRunning){
        toggleBtn='<button onclick="toggleEngine(false)" style="margin-left:6px;font-size:10px;padding:1px 6px;border-radius:4px;border:1px solid var(--red);color:var(--red);background:transparent;cursor:pointer">ֹͣ</button>';
      }else{
        toggleBtn='<button onclick="toggleEngine(true)" style="margin-left:6px;font-size:10px;padding:1px 6px;border-radius:4px;border:1px solid var(--green);color:var(--green);background:transparent;cursor:pointer">启动</button>';
      }
      var autoExec=d.brokerType&&d.brokerType!=='paper';
      var autoLabel=autoExec?'<span style="color:var(--red);font-weight:600">实盘 ('+esc(d.brokerType)+')</span>':'<span style="color:var(--green)">仅模拟</span>';
      el.innerHTML=
        kpiHtml('<span style="color:'+modeColor+'">'+modeLabel+'</span>'+toggleBtn,'引擎状态')+
        kpiHtml(autoLabel,'信号执行')+
        kpiHtml(esc(d.broker||'-'),'券商')+
        kpiClickable(d.positionCount!=null?d.positionCount:'-','持仓数','togglePositionsDetail()')+
        kpiClickable(d.pendingOrderCount!=null?d.pendingOrderCount:'-','挂单数','togglePendingDetail()')+
        kpiHtml(d.conditionalOrderCount!=null?d.conditionalOrderCount:'-','条件单');
      var prevWarn=document.getElementById('auto-exec-warn');
      if(autoExec){
        if(!prevWarn){
          el.insertAdjacentHTML('afterend','<div style="font-size:11px;color:var(--red);margin-top:4px;display:flex;align-items:center;gap:4px" id="auto-exec-warn">⚠️ 当前为实盘模式，信号将自动下单。如需切换回模拟，修改配置 tradingAccount.mode=&quot;paper&quot; 后重启网关。</div>');
        }
      }else if(prevWarn){
        prevWarn.remove();
      }
      // Re-render tables if visible so cancel/status changes are reflected
      if(document.getElementById('engine-pending-detail').style.display!=='none')renderPendingTable();
      if(document.getElementById('engine-positions-detail').style.display!=='none')renderPositionsTable();
      // Keep polling while engine is starting
      if(mode==='starting'&&!_engineStartPollTimer){
        _engineStartPollTimer=setInterval(function(){
          api('/quant/admin/api/engine-status').then(function(d2){
            var m2=d2.mode||'stopped';
            if(m2!=='starting'){
              clearInterval(_engineStartPollTimer);
              _engineStartPollTimer=null;
              if(m2==='running')toast('✅ 引擎已启动');
              else if(m2==='error')toast('❌ 引擎启动失败');
              loadEngineStatus();
            }
          }).catch(function(){});
        },1500);
      }
    })
    .catch(function(){
      document.getElementById('engine-status-kpis').innerHTML=
        kpiHtml('<span style="color:var(--red)">未连接</span>','引擎状态');
    });
}

function toggleEngine(start){
  var endpoint=start?'/quant/api/engine/start':'/quant/api/engine/stop';
  var label=start?'启动':'ֹͣ';
  api(endpoint,{method:'POST'}).then(function(r){
    toast('⏳ '+(r&&r.message?r.message:label+'中…'));
    setTimeout(loadEngineStatus,600);
  }).catch(function(){});
}

function togglePositionsDetail(){
  var el=document.getElementById('engine-positions-detail');
  document.getElementById('engine-pending-detail').style.display='none';
  if(el.style.display==='none'){
    el.style.display='block';
    renderPositionsTable();
  }else{
    el.style.display='none';
  }
}
function togglePendingDetail(){
  var el=document.getElementById('engine-pending-detail');
  document.getElementById('engine-positions-detail').style.display='none';
  if(el.style.display==='none'){
    el.style.display='block';
    renderPendingTable();
  }else{
    el.style.display='none';
  }
}
function renderPositionsTable(){
  var tbody=document.querySelector('#engine-positions-table tbody');
  if(!_enginePositions.length){
    tbody.innerHTML='<tr><td colspan="10" style="color:var(--text-dim)">暂无持仓</td></tr>';
    return;
  }
  var rows='';
  _enginePositions.forEach(function(p){
    var pnl=p.unrealizedPnl!=null?p.unrealizedPnl:null;
    var pnlPct=p.unrealizedPnlPct!=null?p.unrealizedPnlPct:null;
    var pnlColor=pnl!=null?(pnl>0?'var(--green)':pnl<0?'var(--red)':'var(--text)'):'var(--text-dim)';
    rows+='<tr>'+
      '<td>'+esc(p.symbol||'')+'</td>'+
      '<td>'+esc(p.name||'-')+'</td>'+
      '<td>'+numInt(p.quantity)+'</td>'+
      '<td>'+numInt(p.availableQty)+'</td>'+
      '<td>'+numInt(p.frozenQty)+'</td>'+
      '<td>'+num(p.avgCost)+'</td>'+
      '<td>'+(p.currentPrice!=null?num(p.currentPrice):'-')+'</td>'+
      '<td>'+(p.marketValue!=null?'¥'+numInt(p.marketValue):'-')+'</td>'+
      '<td style="color:'+pnlColor+'">'+(pnl!=null?(pnl>=0?'+':'')+num(pnl):'-')+'</td>'+
      '<td style="color:'+pnlColor+'">'+(pnlPct!=null?(pnlPct>=0?'+':'')+pnlPct.toFixed(2)+'%':'-')+'</td>'+
      '</tr>';
  });
  tbody.innerHTML=rows;
}
function renderPendingTable(){
  var tbody=document.querySelector('#engine-pending-table tbody');
  if(!_enginePendingOrders.length){
    tbody.innerHTML='<tr><td colspan="12" style="color:var(--text-dim)">暂无挂单</td></tr>';
    return;
  }
  var sideLabel={buy:'买入',sell:'卖出',short:'做空',cover:'平空'};
  var statusLabel={pending:'待提交',submitted:'已提交',partial:'部分成交',filled:'已成交',cancelled:'已撤',rejected:'已拒绝',expired:'已过期'};
  var rows='';
  _enginePendingOrders.forEach(function(o){
    var sideColor=o.side==='buy'||o.side==='cover'?'var(--red)':'var(--green)';
    rows+='<tr>'+
      '<td style="font-size:11px">'+esc((o.orderId||'').slice(0,8))+'</td>'+
      '<td>'+esc(o.symbol||'')+'</td>'+
      '<td style="color:'+sideColor+'">'+(sideLabel[o.side]||o.side)+'</td>'+
      '<td>'+esc(o.orderType||'-')+'</td>'+
      '<td>'+(o.price!=null?num(o.price):'市价')+'</td>'+
      '<td>'+numInt(o.quantity)+'</td>'+
      '<td>'+numInt(o.filledQty||0)+'</td>'+
      '<td><span class="badge badge-yellow">'+(statusLabel[o.status]||o.status)+'</span></td>'+
      '<td style="font-size:11px">'+(o.brokerType==='paper'?'<span style="color:var(--text-dim)">模拟</span>':esc(o.brokerType||'-'))+'</td>'+
      '<td style="font-size:11px">'+esc(o.strategyId||'-')+'</td>'+
      '<td style="font-size:11px">'+(o.createdAt?new Date(o.createdAt).toLocaleString('zh-CN'):'-')+'</td>'+
      '<td><button class="btn btn-sm" style="padding:2px 8px;font-size:11px;color:var(--red)" onclick="cancelPendingOrder(\\''+esc(o.orderId)+'\\')">撤单</button></td>'+
      '</tr>';
  });
  tbody.innerHTML=rows;
}

var _recentlyCancelledIds={};
function cancelPendingOrder(orderId){
  if(!confirm('确认撤销挂单 '+orderId.slice(0,8)+' ?'))return;
  api('/quant/api/orders?id='+encodeURIComponent(orderId),{method:'DELETE'})
    .then(function(d){
      if(d.status==='ok'){
        toast('✅ 已撤单 '+orderId.slice(0,8));
        // Track recently cancelled so loadEngineStatus won't re-add it
        _recentlyCancelledIds[orderId]=Date.now();
        _enginePendingOrders=_enginePendingOrders.filter(function(o){return o.orderId!==orderId});
        renderPendingTable();
        // Delay refresh to let backend persist the cancellation
        setTimeout(loadEngineStatus,1500);
      }
      else{toast('❌ 撤单失败: '+(d.message||'未知错误'));}
    })
    .catch(function(e){toast('❌ 撤单请求失败: '+e.message);});
}

function loadConditionalOrders(){
  document.getElementById('cond-status').textContent='加载中…';
  api('/quant/admin/api/conditional-orders?active=false')
    .then(function(d){
      var orders=d.orders||[];
      document.getElementById('cond-status').textContent='共 '+orders.length+' 条记录，活跃 '+(d.activeCount||0)+' 条';
      var tbody=document.querySelector('#cond-order-table tbody');
      if(!orders.length){
        tbody.innerHTML='<tr><td colspan="11" style="color:var(--text-dim)">暂无条件单</td></tr>';
        return;
      }
      var rows='';
      orders.forEach(function(o){
        var typeLabel=o.type==='stop_loss'?'<span class="badge badge-red">止损</span>':
          o.type==='take_profit'?'<span class="badge badge-green">ֹӯ</span>':
          '<span class="badge badge-blue">跟踪止损</span>';
        var triggerDisplay;
        if(o.type==='trailing_stop'){
          triggerDisplay=(o.triggerValue*100).toFixed(1)+'%';
          if(o.trailingStopPrice)triggerDisplay+='<br><span style="font-size:11px;color:var(--text-dim)">当前止损价 '+num(o.trailingStopPrice)+'</span>';
          if(o.peakPrice)triggerDisplay+='<br><span style="font-size:11px;color:var(--text-dim)">峰值 '+num(o.peakPrice)+'</span>';
        }else{
          triggerDisplay=num(o.triggerValue);
        }
        var statusHtml;
        if(o.triggered){
          statusHtml='<span class="badge badge-yellow">已触发</span>';
          if(o.triggerResult)statusHtml+='<br><span style="font-size:10px;color:var(--text-dim)">'+esc(o.triggerResult)+'</span>';
        }else{
          statusHtml='<span class="badge badge-green">监控中</span>';
        }
        var actionHtml=o.triggered?'-':'<button class="btn btn-sm" style="padding:2px 8px;font-size:11px;color:var(--red)" onclick="removeConditionalOrder(\\''+esc(o.id)+'\\')">删除</button>';
        var time=o.createdAt?(new Date(o.createdAt)).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
        rows+='<tr>'+
          '<td style="font-size:11px">'+esc(o.id)+'</td>'+
          '<td><strong>'+esc(o.symbol)+'</strong></td>'+
          '<td>'+typeLabel+'</td>'+
          '<td>'+num(o.entryPrice)+'</td>'+
          '<td>'+triggerDisplay+'</td>'+
          '<td>'+numInt(o.quantity)+'</td>'+
          '<td>'+esc(o.strategyId||'-')+'</td>'+
          '<td style="font-size:11px">'+esc(o.description||'-')+'</td>'+
          '<td style="font-size:11px">'+time+'</td>'+
          '<td>'+statusHtml+'</td>'+
          '<td>'+actionHtml+'</td>'+
          '</tr>';
      });
      tbody.innerHTML=rows;
    })
    .catch(function(e){
      document.getElementById('cond-status').textContent='加载失败: '+e.message;
    });
}

function removeConditionalOrder(id){
  if(!confirm('确认删除条件单 '+id+' ?'))return;
  api('/quant/admin/api/conditional-orders',{method:'DELETE',body:JSON.stringify({id:id})})
    .then(function(d){
      if(d.ok){toast('✅ 已删除 '+id);loadConditionalOrders();loadEngineStatus();}
      else{toast('❌ 删除失败');}
    });
}

function checkConditionalNow(){
  document.getElementById('cond-status').textContent='正在检查触发条件…';
  api('/quant/admin/api/conditional-orders',{method:'POST',body:JSON.stringify({action:'check_now'})})
    .then(function(d){
      document.getElementById('cond-status').textContent='检查完成，触发 '+(d.count||0)+' 条';
      if(d.count>0){toast('⚡ 触发了 '+d.count+' 条条件单');loadConditionalOrders();loadEngineStatus();}
      else{toast('检查完成，无触发');}
    })
    .catch(function(e){
      document.getElementById('cond-status').textContent='检查失败: '+e.message;
    });
}

function updateCondAddFields(){
  var type=document.getElementById('cond-add-type').value;
  var label=document.getElementById('cond-add-trigger-label');
  var input=document.getElementById('cond-add-trigger');
  if(type==='stop_loss'){label.textContent='止损价';input.placeholder='95.00';}
  else if(type==='take_profit'){label.textContent='止盈价';input.placeholder='120.00';}
  else{label.textContent='回撤比例';input.placeholder='0.08';}
}

function addManualConditional(){
  var symbol=document.getElementById('cond-add-symbol').value.trim();
  var type=document.getElementById('cond-add-type').value;
  var entryPrice=Number(document.getElementById('cond-add-entry').value);
  var triggerVal=Number(document.getElementById('cond-add-trigger').value);
  var qty=Number(document.getElementById('cond-add-qty').value)||100;
  if(!symbol){toast('请输入股票代码');return;}
  if(!entryPrice||entryPrice<=0){toast('请输入有效的入场价');return;}
  if(!triggerVal||triggerVal<=0){toast('请输入有效的触发值');return;}

  var body={action:'add',type:type,symbol:symbol,entryPrice:entryPrice,quantity:qty};
  if(type==='stop_loss')body.stopPrice=triggerVal;
  else if(type==='take_profit')body.targetPrice=triggerVal;
  else body.trailPct=triggerVal;

  api('/quant/admin/api/conditional-orders',{method:'POST',body:JSON.stringify(body)})
    .then(function(d){
      if(d.ok){toast('✅ 已创建条件单 '+d.id);loadConditionalOrders();loadEngineStatus();}
      else{toast('❌ '+(d.error||'创建失败'));}
    });
}

/* ── Breakout Monitor (突破监控) ── */

var _boExpanded=false;
function toggleBreakoutPanel(){
  _boExpanded=!_boExpanded;
  document.getElementById('bo-panel').style.display=_boExpanded?'block':'none';
  document.getElementById('bo-toggle').textContent=_boExpanded?'▼ 收起':'▶ 展开';
  if(_boExpanded)loadBreakoutOrders();
}

function loadBreakoutOrders(){
  document.getElementById('bo-status').textContent='加载中…';
  api('/quant/admin/api/breakout-orders')
    .then(function(d){
      var setups=d.setups||[];
      var eodRules=d.eodRules||[];
      document.getElementById('bo-status').textContent='共 '+setups.length+' 条记录，活跃 '+(d.activeCount||0)+' 条';
      var tbody=document.querySelector('#bo-table tbody');
      if(!setups.length){
        tbody.innerHTML='<tr><td colspan="9" style="color:var(--text-dim)">暂无突破监控</td></tr>';
      }else{
        var rows='';
        setups.forEach(function(s){
          var statusBadge;
          if(s.status==='watching')statusBadge='<span class="badge badge-blue">监控中</span>';
          else if(s.status==='confirming')statusBadge='<span class="badge badge-yellow">确认中</span>';
          else if(s.status==='triggered')statusBadge='<span class="badge badge-green">已触发</span>';
          else if(s.status==='pullback_wait')statusBadge='<span class="badge badge-yellow">等待回踩</span>';
          else if(s.status==='expired')statusBadge='<span class="badge" style="background:var(--bg-dim)">已过期</span>';
          else if(s.status==='cancelled')statusBadge='<span class="badge" style="background:var(--bg-dim)">已取消</span>';
          else statusBadge='<span class="badge">'+esc(s.status)+'</span>';
          var canCancel=(s.status==='watching'||s.status==='confirming'||s.status==='pullback_wait');
          var actionHtml=canCancel?'<button class="btn btn-sm" style="padding:2px 8px;font-size:11px;color:var(--red)" onclick="cancelBreakoutSetup(\\''+esc(s.id)+'\\')">取消</button>':'-';
          var time=s.createdAt?(new Date(s.createdAt)).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'-';
          rows+='<tr>'+
            '<td style="font-size:11px">'+esc(s.id)+'</td>'+
            '<td style="cursor:pointer" onclick="viewStock(\\''+esc(s.symbol)+'\\')">'+' <strong><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(s.symbol)+'" target="_blank" style="color:var(--accent);text-decoration:underline;cursor:pointer">'+esc(s.symbol)+'</a></strong></td>'+
            '<td>'+num(s.triggerPrice)+'</td>'+
            '<td>'+((s.bufferPct||0)*100).toFixed(1)+'%</td>'+
            '<td>'+numInt(s.quantity||0)+'</td>'+
            '<td>'+statusBadge+'</td>'+
            '<td style="font-size:11px">'+esc(s.description||'-')+'</td>'+
            '<td style="font-size:11px">'+time+'</td>'+
            '<td>'+actionHtml+'</td>'+
            '</tr>';
        });
        tbody.innerHTML=rows;
      }
      // EOD rules
      var eodSection=document.getElementById('eod-section');
      if(eodRules.length){
        eodSection.style.display='block';
        var etbody=document.querySelector('#eod-table tbody');
        var erows='';
        eodRules.forEach(function(r){
          erows+='<tr>'+
            '<td style="font-size:11px">'+esc(r.id)+'</td>'+
            '<td style="cursor:pointer" onclick="viewStock(\\''+esc(r.symbol)+'\\')">'+' <strong><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(r.symbol)+'" target="_blank" style="color:var(--accent);text-decoration:underline;cursor:pointer">'+esc(r.symbol)+'</a></strong></td>'+
            '<td>MA'+r.maPeriod+'</td>'+
            '<td>'+esc(r.maType||'sma')+'</td>'+
            '<td>'+esc(r.exitAction==='reduce_half'?'减半':'清仓')+'</td>'+
            '<td style="font-size:11px">'+esc(r.description||'-')+'</td>'+
            '<td>'+(r.active?'<span class="badge badge-green">活跃</span>':'<span class="badge">停用</span>')+'</td>'+
            '</tr>';
        });
        etbody.innerHTML=erows;
      }else{
        eodSection.style.display='none';
      }
    })
    .catch(function(e){
      document.getElementById('bo-status').textContent='加载失败: '+e.message;
    });
}

function cancelBreakoutSetup(id){
  if(!confirm('确认取消突破监控 '+id+' ?'))return;
  api('/quant/admin/api/breakout-orders',{method:'DELETE',body:JSON.stringify({id:id})})
    .then(function(d){
      if(d.ok){toast('✅ 已取消 '+id);loadBreakoutOrders();}
      else{toast('❌ 取消失败');}
    });
}

/* ── Chip Accumulation (主力吸筹) ── */
`;
}
