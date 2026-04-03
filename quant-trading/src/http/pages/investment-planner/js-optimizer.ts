/** Investment Planner — portfolio optimization, strategy/stock detail modals */
export function getOptimizerJs(): string {
  return `
var _optBest=null;
function toggleOptimizer(){
  var panel=document.getElementById('opt-panel');
  var toggle=document.getElementById('opt-toggle');
  if(panel.style.display==='none'){
    panel.style.display='block';toggle.textContent='▼ 收起';
  }else{
    panel.style.display='none';toggle.textContent='▶ 展开';
  }
}

function runOptimizer(){
  var btn=document.getElementById('btn-opt-run');
  btn.disabled=true;btn.textContent='⏳ 正在寻优…';
  var autoScreen=document.getElementById('opt-auto-screen').checked;
  var statusEl=document.getElementById('opt-status');
  statusEl.textContent=autoScreen?
    '正在从全市场~2000+只股票中筛选并构建智能股票池，再运行多轮回测（约3-8分钟）…':
    '正在运行多轮回测，请耐心等待（约2-5分钟）…';
  document.getElementById('opt-results').style.display='none';
  toast('正在运行股票池寻优…');

  var endVal=document.getElementById('opt-end').value.trim();
  var body={
    capital:Number(document.getElementById('opt-capital').value)||500000,
    start_date:document.getElementById('opt-start').value||'2025-06-01',
    max_rounds:Number(document.getElementById('opt-rounds').value)||16,
    auto_screen:document.getElementById('opt-auto-screen').checked
  };
  if(endVal)body.end_date=endVal;

  api('/quant/admin/api/optimize-pool',{method:'POST',body:JSON.stringify(body),_timeout:600000})
    .then(function(d){
      btn.disabled=false;btn.textContent='🔬 开始寻优（约3-8分钟）';
      if(d.error){statusEl.textContent='寻优失败: '+d.error;toast('❌ '+d.error);return;}
      statusEl.textContent='寻优完成！共测试 '+d.totalRounds+' 个组合'+(d.screeningSummary?(' · 全市场扫描'+d.screeningSummary.afterFilter+'只股票'):'');
      renderOptResults(d);
      loadOptHistory();
    })
    .catch(function(e){
      btn.disabled=false;btn.textContent='🔬 开始寻优（约3-8分钟）';
      statusEl.textContent='寻优失败: '+e.message;
    });
}

function renderOptResults(d){
  document.getElementById('opt-results').style.display='block';
  _optBest=d.best;

  // Screening summary (auto mode)
  var screenCard=document.getElementById('opt-screening-card');
  if(d.screeningSummary && d.autoScreen){
    var ss=d.screeningSummary;
    screenCard.style.display='block';
    document.getElementById('opt-screen-kpis').innerHTML=
      kpiHtml(numInt(ss.totalFetched),'\u5168\u5e02\u573a\u83b7\u53d6')+
      kpiHtml(numInt(ss.afterFilter),'\u7b5b\u9009\u540e')+
      kpiHtml((ss.autoPoolsCreated||0)+'\u4e2a','\u751f\u6210\u80a1\u7968\u6c60')+
      kpiHtml(esc(ss.source||'Sina'),'\u6570\u636e\u6e90');
    var poolsHtml='';
    (ss.autoPoolNames||[]).forEach(function(p){
      poolsHtml+='<span class=\"badge badge-blue\" style=\"margin:2px;padding:3px 8px\">'+esc(p.label)+' ('+p.count+'\u53ea)</span> ';
    });
    document.getElementById('opt-screen-pools').innerHTML=poolsHtml;
  }else{
    screenCard.style.display='none';
  }

  // Best recommendation text
  var best=d.best||{};
  document.getElementById('opt-best-text').innerHTML=esc(d.recommendText||'').replace(/\\n/g,'<br>');

  // Best KPIs
  document.getElementById('opt-best-kpis').innerHTML=
    kpiHtml(best.poolLabel||'-','股票池')+
    kpiHtml(best.strategyLabel||'-','策略')+
    kpiHtml('<span style="color:'+pctColor(best.totalReturn)+'">'+pct(best.totalReturn)+'</span>','收益率')+
    kpiHtml(best.sharpeRatio!=null?best.sharpeRatio.toFixed(2):'-','夏普比率')+
    kpiHtml('<span style="color:var(--red)">'+pct(-Math.abs(best.maxDrawdown||0))+'</span>','最大回撤')+
    kpiHtml(best.symbolCount+'只','股票数');

  // Table
  document.getElementById('opt-round-count').textContent=d.totalRounds;
  var tbody=document.querySelector('#opt-table tbody');
  var rows='';
  var rounds=d.rounds||[];
  rounds.forEach(function(r){
    var isTop=r.rank===1;
    var style=isTop?'background:rgba(63,185,80,0.06)':'';
    rows+='<tr style="'+style+'">';
    rows+='<td>'+(isTop?'🏆 ':'')+r.rank+'</td>';
    var poolBadge=(r.poolKey&&r.poolKey.indexOf('auto_')===0)?'<span class="badge badge-blue" style="font-size:9px;margin-left:3px">智能</span>':'';
    rows+='<td>'+esc(r.poolLabel)+poolBadge+'</td>';
    rows+='<td>'+r.symbolCount+'</td>';
    rows+='<td>'+esc(r.strategyLabel)+'</td>';
    rows+='<td>'+esc(r.riskLabel)+'</td>';
    rows+='<td>'+r.rebalanceDays+'天</td>';
    if(r.error){
      rows+='<td colspan="6" style="color:var(--red)">'+esc(r.error)+'</td>';
    }else{
      rows+='<td style="color:'+pctColor(r.totalReturn)+'"><strong>'+pct(r.totalReturn)+'</strong></td>';
      rows+='<td><strong>'+r.sharpeRatio.toFixed(2)+'</strong></td>';
      rows+='<td style="color:var(--red)">'+pct(-Math.abs(r.maxDrawdown||0))+'</td>';
      rows+='<td>'+(r.winRate||0).toFixed(1)+'</td>';
      rows+='<td>'+(r.profitFactor||0).toFixed(2)+'</td>';
      rows+='<td><span class="badge '+(r.compositeScore>=80?'badge-green':r.compositeScore>=40?'badge-yellow':'badge-orange')+'">'+r.compositeScore+'</span></td>';
    }
    rows+='</tr>';
  });
  tbody.innerHTML=rows;
  toast('✅ 寻优完成！共测试 '+d.totalRounds+' 个组合');
}

function applyOptimalPool(){
  if(!_optBest)return;
  // Fill recommend parameters with best combination
  document.getElementById('rec-symbols').value=(_optBest.symbols||[]).join(',');
  var stratSel=document.getElementById('rec-strategy');
  for(var i=0;i<stratSel.options.length;i++){
    if(stratSel.options[i].value===_optBest.strategy){stratSel.selectedIndex=i;break;}
  }
  var riskSel=document.getElementById('rec-risk');
  for(var i=0;i<riskSel.options.length;i++){
    if(riskSel.options[i].value===_optBest.riskLevel){riskSel.selectedIndex=i;break;}
  }
  document.getElementById('rec-rebalance').value=_optBest.rebalanceDays||5;
  toast('✅ 已采用最优方案，点击「生成建仓推荐」查看详情');
}

/* ══════ OPTIMIZATION HISTORY ══════ */
function loadOptHistory(){
  var statusEl=document.getElementById('opt-history-status');
  statusEl.textContent='加载中…';
  api('/quant/admin/api/optimize-history?limit=50')
    .then(function(d){
      if(d.error){statusEl.textContent='加载失败: '+d.error;return;}
      var entries=d.entries||[];
      if(entries.length===0){
        statusEl.textContent='暂无寻优历史记录';
        document.getElementById('opt-history-panel').style.display='none';
        return;
      }
      statusEl.textContent='共 '+d.total+' 条记录';
      document.getElementById('opt-history-panel').style.display='block';
      var tbody=document.querySelector('#opt-history-table tbody');
      var rows='';
      entries.forEach(function(e,i){
        var time=e.createdAt?(new Date(e.createdAt)).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}):'';
        rows+='<tr>';
        rows+='<td>'+(e.starred?'⭐ ':'')+(i+1)+'</td>';
        rows+='<td>'+esc(time)+'</td>';
        rows+='<td>'+numInt(e.capital)+'</td>';
        rows+='<td>'+esc(e.startDate||'')+'</td>';
        rows+='<td>'+e.totalRounds+'</td>';
        rows+='<td>'+(e.autoScreen?'🌐 全市场':'预设池')+'</td>';
        rows+='<td>'+esc(e.bestPoolLabel||'-')+'</td>';
        rows+='<td>'+esc(e.bestStrategyLabel||'-')+'</td>';
        rows+='<td style="color:'+pctColor(e.bestReturn)+'"><strong>'+pct(e.bestReturn)+'</strong></td>';
        rows+='<td>'+(e.bestSharpe!=null?e.bestSharpe.toFixed(2):'-')+'</td>';
        rows+='<td style="color:var(--red)">'+pct(-Math.abs(e.bestMaxDrawdown||0))+'</td>';
        rows+='<td>'+e.bestSymbolCount+'</td>';
        rows+='<td>';
        rows+='<button class="btn btn-sm" onclick="viewOptHistoryDetail(\\''+e.id+'\\')">📋 详情</button> ';
        rows+='<button class="btn btn-sm" onclick="deleteOptHistory(\\''+e.id+'\\')">🗑</button>';
        rows+='</td>';
        rows+='</tr>';
      });
      tbody.innerHTML=rows;
    })
    .catch(function(err){
      statusEl.textContent='加载失败: '+err.message;
    });
}

function viewOptHistoryDetail(id){
  api('/quant/admin/api/optimize-history?id='+encodeURIComponent(id))
    .then(function(d){
      if(d.error){toast('❌ '+d.error);return;}
      var modal=document.getElementById('opt-history-detail-modal');
      modal.style.display='block';
      var time=d.createdAt?(new Date(d.createdAt)).toLocaleString('zh-CN'):'';
      document.getElementById('opt-detail-title').textContent='寻优详情 — '+time;
      var html='';
      // Params
      html+='<div class="kpi-row">';
      html+=kpiHtml(numInt(d.params.capital),'资金');
      html+=kpiHtml(esc(d.params.startDate),'起始');
      html+=kpiHtml(esc(d.params.endDate||'今天'),'结束');
      html+=kpiHtml(d.totalRounds,'测试轮次');
      html+=kpiHtml(d.params.autoScreen?'🌐 全市场':'预设池','筛选模式');
      html+='</div>';
      // Best
      if(d.best){
        var b=d.best;
        html+='<div class="card" style="background:rgba(63,185,80,0.08);border:1px solid var(--green);margin:12px 0">';
        html+='<h3 style="color:var(--green)">🏆 最优方案</h3>';
        if(d.recommendText)html+='<p>'+esc(d.recommendText).replace(/\\n/g,'<br>')+'</p>';
        html+='<div class="kpi-row">';
        html+=kpiHtml(esc(b.poolLabel||'-'),'股票池');
        html+=kpiHtml(esc(b.strategyLabel||'-'),'策略');
        html+=kpiHtml('<span style="color:'+pctColor(b.totalReturn)+'">'+pct(b.totalReturn)+'</span>','收益率');
        html+=kpiHtml(b.sharpeRatio!=null?b.sharpeRatio.toFixed(2):'-','夏普比率');
        html+=kpiHtml('<span style="color:var(--red)">'+pct(-Math.abs(b.maxDrawdown||0))+'</span>','最大回撤');
        html+=kpiHtml(b.symbolCount+'只','股票数');
        html+='</div></div>';
      }
      // Screening summary
      if(d.screeningSummary){
        var ss=d.screeningSummary;
        html+='<div class="card" style="border:1px solid var(--accent);margin:12px 0">';
        html+='<h3 style="color:var(--accent)">🌐 全市场筛选</h3>';
        html+='<div class="kpi-row">';
        html+=kpiHtml(numInt(ss.totalFetched),'全市场获取');
        html+=kpiHtml(numInt(ss.afterFilter),'筛选后');
        html+=kpiHtml((ss.autoPoolsCreated||0)+'个','生成股票池');
        html+='</div>';
        if(ss.autoPoolNames&&ss.autoPoolNames.length){
          html+='<div style="margin-top:6px">';
          ss.autoPoolNames.forEach(function(p){html+='<span class="badge badge-blue" style="margin:2px;padding:3px 8px">'+esc(p.label)+' ('+p.count+'只)</span> ';});
          html+='</div>';
        }
        html+='</div>';
      }
      // All rounds table
      if(d.rounds&&d.rounds.length){
        html+='<div class="card" style="margin:12px 0"><h3>📊 全部测试轮次 ('+d.rounds.length+' 轮)</h3>';
        html+='<div style="overflow-x:auto"><table style="font-size:12px">';
        html+='<thead><tr><th>排名</th><th>股票池</th><th>只数</th><th>策略</th><th>风险</th><th>调仓</th><th>收益%</th><th>夏普</th><th>回撤%</th><th>胜率%</th><th>利润因子</th><th>综合分</th></tr></thead><tbody>';
        d.rounds.forEach(function(r){
          var isTop=r.rank===1;
          var style=isTop?'background:rgba(63,185,80,0.06)':'';
          html+='<tr style="'+style+'">';
          html+='<td>'+(isTop?'🏆 ':'')+r.rank+'</td>';
          html+='<td>'+esc(r.poolLabel)+'</td>';
          html+='<td>'+r.symbolCount+'</td>';
          html+='<td>'+esc(r.strategyLabel)+'</td>';
          html+='<td>'+esc(r.riskLabel||'')+'</td>';
          html+='<td>'+(r.rebalanceDays||'-')+'天</td>';
          if(r.error){
            html+='<td colspan="6" style="color:var(--red)">'+esc(r.error)+'</td>';
          }else{
            html+='<td style="color:'+pctColor(r.totalReturn)+'"><strong>'+pct(r.totalReturn)+'</strong></td>';
            html+='<td><strong>'+(r.sharpeRatio||0).toFixed(2)+'</strong></td>';
            html+='<td style="color:var(--red)">'+pct(-Math.abs(r.maxDrawdown||0))+'</td>';
            html+='<td>'+(r.winRate||0).toFixed(1)+'</td>';
            html+='<td>'+(r.profitFactor||0).toFixed(2)+'</td>';
            html+='<td><span class="badge '+(r.compositeScore>=80?'badge-green':r.compositeScore>=40?'badge-yellow':'badge-orange')+'">'+r.compositeScore+'</span></td>';
          }
          html+='</tr>';
        });
        html+='</tbody></table></div></div>';
      }
      document.getElementById('opt-detail-content').innerHTML=html;
    })
    .catch(function(err){toast('❌ 加载详情失败: '+err.message);});
}

function closeOptHistoryDetail(){
  document.getElementById('opt-history-detail-modal').style.display='none';
}

/* ── Strategy Backtest Detail Modal ── */
function showStrategyDetail(idx){
  if(!_dailyPickState||!_dailyPickState.strategyRanking)return;
  var r=_dailyPickState.strategyRanking[idx];
  if(!r)return;
  var modal=document.getElementById('strategy-detail-modal');
  modal.style.display='block';

  var label=r.strategyLabel||r.strategy||'-';
  var statusBadge=r.error?'<span class="badge badge-red">错误</span>'
    :(r.rank!=null&&r.rank<=3?'<span class="badge badge-green">入围 #'+r.rank+'</span>'
    :'<span class="badge badge-yellow">观察 #'+(r.rank||'-')+'</span>');

  document.getElementById('strategy-detail-header').innerHTML=
    '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'+
    '<h2 style="font-size:18px;margin:0">📊 '+esc(label)+' — 回测详情</h2>'+statusBadge+
    '</div>'+
    '<p style="color:var(--text-dim);font-size:12px;margin:6px 0 0">策略: '+esc(r.strategy)+' | 筛选引擎: '+esc(r.screenStrategy||r.strategy)+
    ' | 候选: '+r.screenCount+'只'+(_dailyPickState.input?' | 回测周期: '+esc(_dailyPickState.input.startDate||'')+' ~ '+esc(_dailyPickState.input.endDate||''):'')+
    '</p>';

  function isNum(v){return typeof v==='number'&&isFinite(v)}
  function f2(v){return isNum(v)?Number(v).toFixed(2):'-'}
  function fPct(v){return isNum(v)?(v>=0?'+':'')+Number(v).toFixed(2)+'%':'-'}
  function fMoney(v){return isNum(v)?'¥'+Number(v).toLocaleString(undefined,{minimumFractionDigits:0,maximumFractionDigits:0}):'-'}

  // KPIs
  var fs=r.backtestDetail&&r.backtestDetail.fullSummary?r.backtestDetail.fullSummary:{};
  document.getElementById('strategy-detail-kpis').innerHTML=
    kpiHtml(fPct(r.totalReturn),'总收益率')+
    kpiHtml(fPct(r.annualizedReturn),'年化收益率')+
    kpiHtml(isNum(r.maxDrawdown)?'-'+Math.abs(r.maxDrawdown).toFixed(2)+'%':'-','最大回撤')+
    kpiHtml(f2(r.sharpeRatio),'夏普比率')+
    kpiHtml(isNum(r.winRate)?Number(r.winRate).toFixed(1)+'%':'-','胜率')+
    kpiHtml(f2(r.profitFactor),'利润因子')+
    kpiHtml(isNum(r.compositeScore)?Number(r.compositeScore).toFixed(1):'-','综合分')+
    kpiHtml(r.totalTrades!=null?r.totalTrades:'-','总交易次数');

  // Score breakdown
  var sb=r.scoreBreakdown;
  if(sb){
    var breakdownRows='';
    var items=[
      {label:'收益率贡献', field:'totalReturn', formula:'totalReturn × 1.0', desc:'总收益越高贡献越大'},
      {label:'年化收益贡献', field:'annualizedReturn', formula:'annualizedReturn × 0.35', desc:'年化收益的权重贡献'},
      {label:'夏普比率贡献', field:'sharpeRatio', formula:'sharpeRatio × 12', desc:'风险调整收益，权重较高'},
      {label:'胜率贡献', field:'winRate', formula:'winRate × 0.28', desc:'胜率越高越稳'},
      {label:'利润因子贡献', field:'profitFactor', formula:'min(profitFactor, 4) × 4', desc:'利润因子上限4，防极端影响'},
      {label:'回撤惩罚', field:'maxDrawdown', formula:'-maxDrawdown × 0.85', desc:'回撤越大扣分越多'},
      {label:'交易频率加分', field:'tradeFrequency', formula:'min(totalTrades, 120) × 0.03', desc:'交易次数加分，上限120笔'},
    ];
    items.forEach(function(item){
      var entry=sb[item.field];
      if(!entry)return;
      var isNeg=entry.contribution<0;
      var barColor=isNeg?'var(--red)':'var(--green)';
      var barWidth=Math.min(Math.abs(entry.contribution)/Math.max(Math.abs(sb.total)||1,1)*100,100);
      breakdownRows+='<tr>';
      breakdownRows+='<td style="white-space:nowrap">'+item.label+'</td>';
      breakdownRows+='<td style="font-family:monospace;font-size:11px;color:var(--text-dim)">'+item.formula+'</td>';
      breakdownRows+='<td style="font-family:monospace">'+f2(entry.value)+'</td>';
      breakdownRows+='<td style="font-family:monospace">'+f2(entry.weight)+'</td>';
      breakdownRows+='<td style="font-family:monospace;font-weight:bold;color:'+(isNeg?'var(--red)':'var(--green)')+'">'+
        (entry.contribution>=0?'+':'')+Number(entry.contribution).toFixed(2)+'</td>';
      breakdownRows+='<td style="width:120px"><div style="height:14px;border-radius:3px;background:var(--bg2);position:relative;overflow:hidden">'+
        '<div style="position:absolute;'+(isNeg?'right':'left')+':0;top:0;height:100%;width:'+barWidth+'%;background:'+barColor+';border-radius:3px;opacity:0.7"></div>'+
        '</div></td>';
      breakdownRows+='<td style="font-size:11px;color:var(--text-dim)">'+item.desc+'</td>';
      breakdownRows+='</tr>';
    });
    document.getElementById('strategy-detail-score-breakdown').innerHTML=
      '<table style="font-size:12px;width:100%"><thead><tr><th>指标</th><th>计算公式</th><th>实际值</th><th>权重</th><th>贡献分</th><th>贡献可视化</th><th>说明</th></tr></thead><tbody>'+
      breakdownRows+
      '<tr style="border-top:2px solid var(--border);font-weight:bold"><td colspan="4">综合分合计</td><td style="font-size:14px;color:var(--accent)">'+Number(sb.total).toFixed(1)+'</td><td colspan="2"></td></tr>'+
      '</tbody></table>'+
      '<p style="font-size:11px;color:var(--text-dim);margin-top:8px">💡 <b>综合分公式</b>: totalReturn×1.0 + annualizedReturn×0.35 + sharpeRatio×12 + winRate×0.28 + min(profitFactor,4)×4 - maxDrawdown×0.85 + min(totalTrades,120)×0.03</p>';
  }else{
    document.getElementById('strategy-detail-score-breakdown').innerHTML='<div style="color:var(--text-dim)">无评分数据</div>';
  }

  // Equity curve
  var detail=r.backtestDetail;
  if(detail&&detail.equityCurve&&detail.equityCurve.length>1){
    setTimeout(function(){
      drawEquityCurve('sd-equity-canvas',detail.equityCurve,[{label:label,color:'#58a6ff'}],_dailyPickState.input?_dailyPickState.input.initialCapital:500000);
    },50);
  }else{
    var ec=document.getElementById('sd-equity-canvas');
    if(ec&&ec.parentElement)ec.parentElement.innerHTML='<div style="padding:40px;text-align:center;color:var(--text-dim)">无净值曲线数据</div>';
  }

  // Monthly returns chart
  var monthlyEl=document.getElementById('sd-monthly-chart');
  if(detail&&detail.monthlyReturns&&detail.monthlyReturns.length>0){
    var maxAbsRet=1;
    detail.monthlyReturns.forEach(function(m){var ar=Math.abs(m['return']);if(ar>maxAbsRet)maxAbsRet=ar;});
    var monthBars='';
    detail.monthlyReturns.forEach(function(m){
      var ret=m['return'];
      var hPct=Math.round(Math.abs(ret)/maxAbsRet*100);
      var color=ret>=0?'var(--green)':'var(--red)';
      monthBars+='<div style="display:flex;flex-direction:column;align-items:center;min-width:28px;font-size:9px;flex:1">';
      monthBars+='<span style="color:'+color+';margin-bottom:2px">'+(ret>=0?'+':'')+ret.toFixed(1)+'%</span>';
      monthBars+='<div style="width:18px;height:'+Math.max(hPct,3)+'px;background:'+color+';border-radius:2px;opacity:0.75"></div>';
      monthBars+='<span style="color:var(--text-dim);margin-top:2px">'+m.month.substring(5)+'</span>';
      monthBars+='</div>';
    });
    monthlyEl.innerHTML=monthBars;
  }else{
    monthlyEl.innerHTML='<div style="width:100%;text-align:center;color:var(--text-dim);padding:20px">无月度数据</div>';
  }

  // Candidates table
  var cdBody=document.querySelector('#sd-candidates-table tbody');
  var cdRows='';
  var candidates=r.screenCandidates||[];
  candidates.forEach(function(c){
    var reasons=(c.reasons||[]).map(function(reason){return '<span class="badge badge-blue" style="margin:1px;font-size:10px">'+esc(reason)+'</span>';}).join(' ');
    cdRows+='<tr>';
    cdRows+='<td>'+c.rank+'</td>';
    cdRows+='<td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(c.symbol)+'" target="_blank" style="color:var(--accent)">'+esc(c.symbol)+'</a></td>';
    cdRows+='<td>'+esc(c.name||c.symbol)+'</td>';
    cdRows+='<td>'+f2(c.price)+'</td>';
    cdRows+='<td style="color:'+(c.changePct>=0?'var(--green)':'var(--red)')+'">'+(c.changePct>=0?'+':'')+Number(c.changePct).toFixed(2)+'%</td>';
    cdRows+='<td style="font-weight:bold">'+f2(c.score)+'</td>';
    cdRows+='<td>'+reasons+'</td>';
    cdRows+='</tr>';
  });
  cdBody.innerHTML=cdRows||'<tr><td colspan="7" style="color:var(--text-dim)">无候选数据</td></tr>';

  // Trades table
  var trBody=document.querySelector('#sd-trades-table tbody');
  var trRows='';
  var trades=detail?detail.trades:[];
  if(trades&&trades.length){
    trades.forEach(function(t){
      var isBuy=t.side==='buy';
      var sideLabel=isBuy?'<span style="color:var(--red)">买入</span>':'<span style="color:var(--green)">卖出</span>';
      var pnlStr=t.pnl!=null?((t.pnl>=0?'+':'')+Number(t.pnl).toFixed(2)):'-';
      var pnlPctStr=t.pnlPct!=null?((t.pnlPct>=0?'+':'')+Number(t.pnlPct).toFixed(2)+'%'):'-';
      var pnlColor=t.pnl!=null?(t.pnl>=0?'var(--green)':'var(--red)'):'var(--text)';
      var amount=t.price&&t.shares?Number(t.price*t.shares).toFixed(0):'';
      trRows+='<tr>';
      trRows+='<td>'+esc(t.date||'-')+'</td>';
      trRows+='<td>'+esc(t.symbol||'-')+'</td>';
      trRows+='<td>'+esc(t.name||'-')+'</td>';
      trRows+='<td>'+sideLabel+'</td>';
      trRows+='<td>'+f2(t.price)+'</td>';
      trRows+='<td>'+(t.shares||'-')+'</td>';
      trRows+='<td>'+(amount?'¥'+Number(amount).toLocaleString():'-')+'</td>';
      trRows+='<td style="color:'+pnlColor+'">'+pnlStr+'</td>';
      trRows+='<td style="color:'+pnlColor+'">'+pnlPctStr+'</td>';
      trRows+='<td style="font-size:10px;color:var(--text-dim)">'+(t.commission?'佣金¥'+Number(t.commission).toFixed(2):'-')+'</td>';
      trRows+='</tr>';
    });
  }
  trBody.innerHTML=trRows||'<tr><td colspan="10" style="color:var(--text-dim)">无交易记录</td></tr>';

  // Final positions table
  var posBody=document.querySelector('#sd-positions-table tbody');
  var posRows='';
  var positions=detail?detail.finalPositions:[];
  if(positions&&positions.length){
    positions.forEach(function(p){
      var pnlColor=p.pnl>=0?'var(--green)':'var(--red)';
      posRows+='<tr>';
      posRows+='<td>'+esc(p.symbol||'-')+'</td>';
      posRows+='<td>'+esc(p.name||'-')+'</td>';
      posRows+='<td>'+p.shares+'</td>';
      posRows+='<td>'+f2(p.avgCost)+'</td>';
      posRows+='<td>'+f2(p.currentPrice)+'</td>';
      posRows+='<td style="color:'+pnlColor+'">'+(p.pnl>=0?'+':'')+Number(p.pnl).toFixed(2)+'</td>';
      posRows+='<td style="color:'+pnlColor+'">'+(p.pnlPct>=0?'+':'')+Number(p.pnlPct).toFixed(2)+'%</td>';
      posRows+='</tr>';
    });
  }
  posBody.innerHTML=posRows||'<tr><td colspan="7" style="color:var(--text-dim)">无期末持仓（已全部清仓）</td></tr>';

  // Full summary
  var fullSumEl=document.getElementById('sd-full-summary');
  if(fs&&Object.keys(fs).length>0){
    var sumItems=[
      ['策略名称', fs.strategyLabel||fs.strategy||'-'],
      ['风险偏好', fs.riskProfile||fs.riskTolerance||'-'],
      ['初始资金', fMoney(fs.initialCapital)],
      ['期末资金', fMoney(fs.finalEquity)],
      ['总收益率', fPct(fs.totalReturn)],
      ['年化收益率', fPct(fs.annualizedReturn)],
      ['最大回撤', isNum(fs.maxDrawdown)?'-'+Math.abs(fs.maxDrawdown).toFixed(2)+'%':'-'],
      ['回撤起始', fs.maxDrawdownStart||'-'],
      ['回撤结束', fs.maxDrawdownEnd||'-'],
      ['夏普比率', f2(fs.sharpeRatio)],
      ['胜率', isNum(fs.winRate)?Number(fs.winRate).toFixed(1)+'%':'-'],
      ['利润因子', f2(fs.profitFactor)],
      ['总交易次数', fs.totalTrades!=null?fs.totalTrades:'-'],
      ['盈利次数', fs.winningTrades!=null?fs.winningTrades:'-'],
      ['亏损次数', fs.losingTrades!=null?fs.losingTrades:'-'],
      ['平均盈利', fMoney(fs.avgWin)],
      ['平均亏损', fMoney(fs.avgLoss)],
      ['总佣金', fMoney(fs.totalCommission)],
      ['交易天数', fs.tradingDays!=null?fs.tradingDays+'天':'-'],
      ['回测起始', fs.startDate||'-'],
      ['回测结束', fs.endDate||'-'],
    ];
    var sumHtml='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:6px 16px">';
    sumItems.forEach(function(pair){
      sumHtml+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text-dim)">'+pair[0]+'</span><span style="font-family:monospace">'+pair[1]+'</span></div>';
    });
    sumHtml+='</div>';
    fullSumEl.innerHTML=sumHtml;
  }else{
    fullSumEl.innerHTML='<div style="color:var(--text-dim)">无详细指标</div>';
  }
}

function closeStrategyDetail(){
  document.getElementById('strategy-detail-modal').style.display='none';
}

/* ── Stock Candidate Detail Modal ── */
function showStockDetail(idx){
  if(!_dailyPickState)return;
  var combined=Array.isArray(_dailyPickState.combinedList)?_dailyPickState.combinedList:[];
  var item=combined[idx];
  if(!item)return;
  var modal=document.getElementById('stock-detail-modal');
  modal.style.display='block';

  function isNum(v){return typeof v==='number'&&isFinite(v)}
  function f2(v){return isNum(v)?Number(v).toFixed(2):'-'}
  function fPct(v){return isNum(v)?(v>=0?'+':'')+Number(v).toFixed(2)+'%':'-'}

  var statusBadge=item.selected?'<span class="badge badge-green">入围</span>':'<span class="badge badge-yellow">候补</span>';

  document.getElementById('stock-detail-header').innerHTML=
    '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">'+
    '<h2 style="font-size:18px;margin:0">📊 '+esc(item.symbol)+' '+esc(item.name||'')+' — 综合评分详情</h2>'+statusBadge+
    ' <a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(item.symbol)+'" target="_blank" class="btn btn-sm" style="text-decoration:none">🔗 个股详情页</a>'+
    '</div>'+
    '<p style="color:var(--text-dim);font-size:12px;margin:6px 0 0">排名: #'+item.rank+' | 现价: '+f2(item.price)+' | 涨跌: '+fPct(item.changePct)+' | 入选次数: '+item.appearanceCount+'次</p>';

  // KPIs
  var btReturnKpi=isNum(item.backtestReturn)?fPct(item.backtestReturn):'-';
  var btSharpeKpi=isNum(item.backtestSharpe)?f2(item.backtestSharpe):'-';
  document.getElementById('stock-detail-kpis').innerHTML=
    kpiHtml('<strong>'+f2(item.combinedScore)+'</strong>','综合分(回测)')+
    kpiHtml(btReturnKpi,'回测收益率')+
    kpiHtml(btSharpeKpi,'夏普比率')+
    kpiHtml(item.appearanceCount+'次','入选次数')+
    kpiHtml((item.selectedBy||[]).length+'个','来源策略数')+
    kpiHtml(item.selected?'✅ 是':'❌ 否','是否入围')+
    kpiHtml(f2(item.price),'现价')+
    kpiHtml(fPct(item.changePct),'涨跌幅');

  // Score breakdown
  var sb=item.scoreBreakdown;
  if(sb){
    // Top-level breakdown
    var topRows='';
    var breakdownItems=[
      {label:'平均筛选评分',value:sb.avgScreenScore,desc:'各策略筛选评分均值',formula:'各策略筛选评分均值'},
      {label:'回测综合分',value:sb.total,desc:'基于回测收益、夏普、胜率等指标的综合评分',formula:'收益率×1.1 + 年化×0.25 + 夏普×10 + 胜率×0.3 + 盈亏比×3 − 回撤×0.8'},
    ];
    breakdownItems.forEach(function(bi){
      var barWidth=sb.total>0?Math.min(Math.abs(bi.value)/sb.total*100,100):0;
      topRows+='<tr>';
      topRows+='<td style="white-space:nowrap;font-weight:600">'+bi.label+'</td>';
      topRows+='<td style="font-family:monospace;font-size:11px;color:var(--text-dim)">'+bi.formula+'</td>';
      topRows+='<td style="font-family:monospace;font-weight:bold;color:var(--green)">+'+Number(bi.value).toFixed(2)+'</td>';
      topRows+='<td style="width:120px"><div style="height:14px;border-radius:3px;background:var(--bg2);position:relative;overflow:hidden">'+
        '<div style="position:absolute;left:0;top:0;height:100%;width:'+barWidth+'%;background:var(--green);border-radius:3px;opacity:0.7"></div>'+
        '</div></td>';
      topRows+='<td style="font-size:11px;color:var(--text-dim)">'+bi.desc+'</td>';
      topRows+='</tr>';
    });
    var scoreHtml='<table style="font-size:12px;width:100%;margin-bottom:12px"><thead><tr><th>组成部分</th><th>计算公式</th><th>贡献分</th><th>占比</th><th>说明</th></tr></thead><tbody>'+
      topRows+
      '<tr style="border-top:2px solid var(--border);font-weight:bold"><td colspan="2">综合分合计</td><td style="font-size:14px;color:var(--accent)">'+Number(sb.total).toFixed(1)+'</td><td colspan="2"></td></tr>'+
      '</tbody></table>';
    scoreHtml+='<p style="font-size:11px;color:var(--text-dim);margin-top:4px">💡 <b>综合分</b>: 基于个股回测的综合指标评分（收益率、年化收益、夏普比率、胜率、盈亏比、最大回撤加权计算）</p>';
    document.getElementById('stock-detail-score-breakdown').innerHTML=scoreHtml;
  }else{
    document.getElementById('stock-detail-score-breakdown').innerHTML='<div style="color:var(--text-dim)">无评分数据</div>';
  }

  // Per-strategy contribution detail
  var perStrategy=sb?sb.perStrategy:[];
  if(perStrategy&&perStrategy.length){
    var psRows='';
    perStrategy.forEach(function(ps,psIdx){
      var reasons=(ps.reasons||[]).map(function(r){return '<span class="badge badge-blue" style="margin:1px;font-size:10px">'+esc(r)+'</span>';}).join(' ');
      psRows+='<tr'+(psIdx===0?' style="background:rgba(63,185,80,.06)"':'')+'>';
      psRows+='<td style="font-weight:600">'+esc(ps.strategyLabel||ps.strategy)+'</td>';
      psRows+='<td>策略排名 #'+ps.strategyRank+'</td>';
      psRows+='<td>候选排名 #'+ps.candidateRank+'</td>';
      psRows+='<td style="font-family:monospace;font-weight:bold">'+f2(ps.screenScore)+'</td>';
      psRows+='</tr>';
      if(reasons){
        psRows+='<tr style="background:rgba(0,0,0,.02)"><td colspan="4" style="padding:4px 12px;font-size:11px">理由: '+reasons+'</td></tr>';
      }
    });
    document.getElementById('stock-detail-strategy-contributions').innerHTML=
      '<table style="font-size:12px;width:100%"><thead><tr><th>策略</th><th>策略排名</th><th>候选排名</th><th>筛选评分</th></tr></thead><tbody>'+
      psRows+'</tbody></table>'+
      '<p style="font-size:11px;color:var(--text-dim);margin-top:8px">💡 各策略独立筛选评分，最终综合分来自个股回测指标</p>';
  }else{
    document.getElementById('stock-detail-strategy-contributions').innerHTML='<div style="color:var(--text-dim)">无策略贡献数据</div>';
  }

  // Reasons section
  var sourceDetails=item.sourceDetails||[];
  if(sourceDetails.length){
    var reasonsHtml='';
    sourceDetails.forEach(function(src){
      var reasons=src.reasons||[];
      reasonsHtml+='<div style="margin-bottom:10px;padding:8px 12px;background:var(--bg2);border-radius:6px;border-left:3px solid var(--accent)">';
      reasonsHtml+='<div style="font-weight:600;margin-bottom:4px">'+esc(src.strategyLabel||src.strategy)+' <span style="font-size:11px;color:var(--text-dim)">(策略排名#'+src.strategyRank+', 候选排名#'+src.candidateRank+', 评分'+f2(src.screenScore)+')</span></div>';
      if(reasons.length){
        reasonsHtml+='<div style="display:flex;flex-wrap:wrap;gap:4px">';
        reasons.forEach(function(r){reasonsHtml+='<span class="badge badge-blue" style="font-size:11px">'+esc(r)+'</span>';});
        reasonsHtml+='</div>';
      }else{
        reasonsHtml+='<div style="color:var(--text-dim);font-size:12px">无详细理由</div>';
      }
      reasonsHtml+='</div>';
    });
    document.getElementById('stock-detail-reasons').innerHTML=reasonsHtml;
  }else{
    document.getElementById('stock-detail-reasons').innerHTML='<div style="color:var(--text-dim)">无选股理由</div>';
  }

  // Single-stock backtest results (if available)
  var stockBt=Array.isArray(_dailyPickState.stockBacktestRanking)?_dailyPickState.stockBacktestRanking:[];
  var myBt=null;
  for(var i=0;i<stockBt.length;i++){
    if(stockBt[i].symbol===item.symbol){myBt=stockBt[i];break;}
  }
  // Reset sub-sections
  document.getElementById('stock-bt-equity-section').style.display='none';
  document.getElementById('stock-bt-trades-section').style.display='none';
  document.getElementById('stock-bt-monthly-section').style.display='none';

  if(myBt&&myBt.totalReturn!=null&&!myBt.error){
    var btHtml='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:6px 16px;margin-bottom:8px">';
    var btItems=[
      ['回测策略', esc(myBt.strategyLabel||myBt.strategy||'-')],
      ['总收益率', fPct(myBt.totalReturn)],
      ['年化收益率', fPct(myBt.annualizedReturn)],
      ['最大回撤', isNum(myBt.maxDrawdown)?'-'+Math.abs(myBt.maxDrawdown).toFixed(2)+'%':'-'],
      ['夏普比率', f2(myBt.sharpeRatio)],
      ['胜率', isNum(myBt.winRate)?Number(myBt.winRate).toFixed(1)+'%':'-'],
      ['利润因子', f2(myBt.profitFactor)],
      ['交易次数', myBt.totalTrades!=null?myBt.totalTrades:'-'],
      ['综合分', isNum(myBt.compositeScore)?Number(myBt.compositeScore).toFixed(1):'-'],
      ['回测排名', myBt.rank!=null?'#'+myBt.rank:'-'],
    ];
    btItems.forEach(function(pair){
      btHtml+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text-dim)">'+pair[0]+'</span><span style="font-family:monospace">'+pair[1]+'</span></div>';
    });
    btHtml+='</div>';
    document.getElementById('stock-detail-backtest').innerHTML=btHtml;

    // Equity curve
    var btEquity=Array.isArray(myBt.equityCurve)?myBt.equityCurve:[];
    if(btEquity.length>1){
      document.getElementById('stock-bt-equity-section').style.display='block';
      var initialCapBt=_dailyPickState.input?_dailyPickState.input.initialCapital:500000;
      setTimeout(function(){
        drawStockBtEquityCurve('stock-bt-equity-canvas',btEquity,myBt.trades||[],initialCapBt,myBt.strategyLabel||myBt.strategy||'');
      },50);
    }

    // Trades table
    var btTrades=Array.isArray(myBt.trades)?myBt.trades:[];
    if(btTrades.length>0){
      document.getElementById('stock-bt-trades-section').style.display='block';
      document.getElementById('stock-bt-trades-count').textContent='(最近'+btTrades.length+'笔)';
      var trBody=document.querySelector('#stock-bt-trades-table tbody');
      var trRows='';
      btTrades.forEach(function(t){
        var isBuy=t.side==='buy'||t.type==='buy'||t.action==='buy';
        var typeColor=isBuy?'var(--red)':'var(--green)';
        var typeLabel=isBuy?'买入':'卖出';
        var returnPct=t.pnlPct!=null?t.pnlPct:(t.returnPct!=null?t.returnPct:null);
        var amount=(t.price!=null&&t.shares!=null)?Math.round(t.price*t.shares):(t.amount!=null?Number(t.amount).toFixed(0):'-');
        trRows+='<tr>';
        trRows+='<td style="white-space:nowrap">'+(t.date||t.entryDate||'-')+'</td>';
        trRows+='<td style="color:'+typeColor+';font-weight:600">'+typeLabel+'</td>';
        trRows+='<td>'+(t.symbol||item.symbol)+'</td>';
        trRows+='<td style="font-family:monospace">'+(t.price!=null?Number(t.price).toFixed(2):'-')+'</td>';
        trRows+='<td style="font-family:monospace">'+(t.shares||t.quantity||'-')+'</td>';
        trRows+='<td style="font-family:monospace">'+amount+'</td>';
        trRows+='<td style="font-family:monospace;color:'+(returnPct!=null&&returnPct>=0?'var(--green)':'var(--red)')+'">'+(returnPct!=null?(returnPct>=0?'+':'')+Number(returnPct).toFixed(2)+'%':'-')+'</td>';
        trRows+='</tr>';
      });
      trBody.innerHTML=trRows;
    }

    // Monthly returns
    var btMonthly=Array.isArray(myBt.monthlyReturns)?myBt.monthlyReturns:[];
    if(btMonthly.length>0){
      document.getElementById('stock-bt-monthly-section').style.display='block';
      var monthlyEl=document.getElementById('stock-bt-monthly-chart');
      var maxAbsRet=1;
      btMonthly.forEach(function(m){var ar=Math.abs(m['return']);if(ar>maxAbsRet)maxAbsRet=ar;});
      var monthBars='';
      btMonthly.forEach(function(m){
        var ret=m['return'];
        var hPct=Math.round(Math.abs(ret)/maxAbsRet*100);
        var color=ret>=0?'var(--green)':'var(--red)';
        monthBars+='<div style="display:flex;flex-direction:column;align-items:center;min-width:28px;font-size:9px;flex:1">';
        monthBars+='<span style="color:'+color+';margin-bottom:2px">'+(ret>=0?'+':'')+ret.toFixed(1)+'%</span>';
        monthBars+='<div style="width:18px;height:'+Math.max(hPct,3)+'px;background:'+color+';border-radius:2px;opacity:0.75"></div>';
        monthBars+='<span style="color:var(--text-dim);margin-top:2px">'+m.month.substring(5)+'</span>';
        monthBars+='</div>';
      });
      monthlyEl.innerHTML=monthBars;
    }
  }else if(myBt&&myBt.error){
    document.getElementById('stock-detail-backtest').innerHTML='<div style="color:var(--red)">回测失败: '+esc(myBt.error)+'</div>';
  }else{
    document.getElementById('stock-detail-backtest').innerHTML='<div style="color:var(--text-dim)">该股票不在入围股票池中，无单独回测数据</div>';
  }

  // Routing
  var routingArr2=Array.isArray(_dailyPickState.routingEnrichment)?_dailyPickState.routingEnrichment:[];
  var myRt=null;
  for(var ri=0;ri<routingArr2.length;ri++){
    if(routingArr2[ri].symbol===item.symbol&&routingArr2[ri].routing){myRt=routingArr2[ri].routing;break;}
  }
  if(myRt){
    var rtHtml='<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:6px 16px">';
    var rtItems=[
      ['组合策略', myRt.combo||'-'],
      ['组合方式', myRt.comboMethod||'-'],
      ['波动率特征', myRt.volatility||'-'],
      ['趋势特征', myRt.trend||'-'],
      ['市值特征', myRt.marketCap||'-'],
      ['特征键', myRt.profileKey||'-'],
      ['数据来源', myRt.source||'-'],
      ['路由原因', myRt.reason||'-'],
    ];
    rtItems.forEach(function(pair){
      rtHtml+='<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid var(--border)"><span style="color:var(--text-dim)">'+pair[0]+'</span><span style="font-family:monospace;font-size:12px">'+esc(pair[1])+'</span></div>';
    });
    rtHtml+='</div>';
    if(myRt.strategies&&myRt.strategies.length){
      rtHtml+='<div style="margin-top:8px"><span style="color:var(--text-dim);font-size:12px">推荐策略: </span>';
      myRt.strategies.forEach(function(s){rtHtml+='<span class="badge badge-blue" style="margin:1px">'+esc(s)+'</span>';});
      rtHtml+='</div>';
    }
    document.getElementById('stock-detail-routing').innerHTML=rtHtml;
  }else{
    document.getElementById('stock-detail-routing').innerHTML='<div style="color:var(--text-dim)">无自适应路由数据</div>';
  }

  // Timeline chart
  renderStockTimeline('stock-detail-timeline',item.symbol);
}

function closeStockDetail(){
  document.getElementById('stock-detail-modal').style.display='none';
}

function deleteOptHistory(id){
  if(!confirm('确定删除此寻优记录？'))return;
  api('/quant/admin/api/optimize-history?id='+encodeURIComponent(id),{method:'DELETE'})
    .then(function(d){
      if(d.error){toast('❌ '+d.error);return;}
      toast('✅ 已删除');
      loadOptHistory();
    })
    .catch(function(err){toast('❌ 删除失败: '+err.message);});
}
`;
}
