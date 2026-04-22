/** Investment Planner — add-position recommendation (加仓推荐) */
export function getAddPositionJs(): string {
  return `
/* ══════ ADD POSITION RECOMMEND (加仓推荐) ══════ */

function loadAddPositionPortfolio(){
  toast('🔄 加载持仓数据…');
  var source=document.getElementById('ap-source').value||'auto';
  var kpiEl=document.getElementById('ap-portfolio-kpis');
  kpiEl.innerHTML='<span style="color:var(--text-dim);font-size:12px">加载中…</span>';

  // Fetch live positions first, fallback to plan
  var liveP=api('/quant/api/portfolio?mode=live',{_timeout:15000}).catch(function(){return null});
  var planP=api('/quant/admin/api/plan-history',{_timeout:15000}).catch(function(){return null});

  Promise.all([liveP,planP]).then(function(results){
    var liveData=results[0];
    var planData=results[1];
    var positions=[];

    // Live positions
    if(source!=='plan'&&liveData&&liveData.data&&liveData.data.positions&&liveData.data.positions.length){
      positions=liveData.data.positions.map(function(p){
        var qty=p.quantity||p.shares||0;
        var price=p.currentPrice||p.price||0;
        return {symbol:p.symbol,name:p.name||p.symbol,shares:qty,cost:p.avgCost||p.cost||p.buyPrice||0,currentPrice:price,pnlPct:p.unrealizedPnlPct!=null?p.unrealizedPnlPct*100:(p.pnlPct||0),value:p.marketValue||(qty*price)};
      });
    }
    // Fallback to plan
    if(!positions.length&&source!=='live'){
      if(currentPlan&&currentPlan.portfolio&&currentPlan.portfolio.length){
        positions=currentPlan.portfolio.map(function(p){
          return {symbol:p.symbol,name:p.name||p.symbol,shares:p.shares||0,cost:p.price||0,currentPrice:p.price||0,pnlPct:0,value:(p.shares||0)*(p.price||0)};
        });
      }else if(planData&&planData.entries&&planData.entries.length){
        toast('ℹ️ 无实盘持仓，将从最新投资计划加载');
        // Load latest plan
        return api('/quant/admin/api/plan-history?id='+encodeURIComponent(planData.entries[0].id),{_timeout:15000}).then(function(r){
          if(r.status==='ok'&&r.entry&&r.entry.fullPlan&&r.entry.fullPlan.portfolio){
            currentPlan=r.entry.fullPlan;
            var planPos=currentPlan.portfolio.map(function(p){
              return {symbol:p.symbol,name:p.name||p.symbol,shares:p.shares||0,cost:p.price||0,currentPrice:p.price||0,pnlPct:0,value:(p.shares||0)*(p.price||0)};
            });
            _renderApPortfolioKpis(planPos);
          }else{
            kpiEl.innerHTML='<div style="text-align:center;padding:20px;color:var(--text-dim)">暂无持仓数据，请先建仓或生成投资计划</div>';
          }
        });
      }
    }
    _renderApPortfolioKpis(positions);
  });
}

function _renderApPortfolioKpis(positions){
  var kpiEl=document.getElementById('ap-portfolio-kpis');
  if(!positions||!positions.length){
    kpiEl.innerHTML='<div style="width:100%;text-align:center;padding:30px 20px;color:var(--text-dim)">暂无持仓数据 — 请先在「建仓推荐」中建仓</div>';
    return;
  }
  var totalValue=0;var totalCost=0;var count=positions.length;
  positions.forEach(function(p){totalValue+=p.value||0;totalCost+=(p.cost||0)*(p.shares||0)});
  var totalPnl=totalValue-totalCost;
  var totalPnlPct=totalCost>0?(totalPnl/totalCost*100):0;
  kpiEl.innerHTML=[
    kpiHtml(count,'持仓数'),
    kpiHtml('¥'+numInt(totalValue),'总市值'),
    kpiHtml('¥'+numInt(totalCost),'总成本'),
    kpiHtml('<span style="color:'+pctColor(totalPnlPct)+'">'+pct(totalPnlPct)+'</span>','总收益率'),
  ].join('');
}

function runAddPositionRecommend(){
  var btn=document.getElementById('btn-ap-run');
  btn.disabled=true;btn.textContent='⏳ 正在分析持仓…';
  _addPosRecData=null;
  _addPosExecuted=false;
  document.getElementById('ap-status').textContent='正在扫描持仓 → 技术分析 → 主力行为 → 风控检查 → 生成加仓建议…';
  document.getElementById('ap-results').style.display='none';
  toast('📈 正在生成加仓推荐…');

  var body={
    budget:Number(document.getElementById('ap-budget').value)||100000,
    max_add_pct:Number(document.getElementById('ap-max-add-pct').value)||50,
    min_confidence:Number(document.getElementById('ap-min-confidence').value)||60,
    source:document.getElementById('ap-source').value||'auto',
    check_accumulation:document.getElementById('ap-check-accumulation').checked,
    check_risk:document.getElementById('ap-check-risk').checked
  };

  // Include current plan if available
  if(currentPlan&&currentPlan.portfolio&&currentPlan.portfolio.length){
    body.plan=currentPlan;
  }

  api('/quant/admin/api/add-position-recommend',{method:'POST',body:JSON.stringify(body),_timeout:300000})
    .then(function(d){
      btn.disabled=false;btn.textContent='📈 生成加仓推荐';
      if(d.error){
        document.getElementById('ap-status').textContent='❌ '+d.error;
        toast('❌ '+d.error);
        return;
      }
      document.getElementById('ap-status').textContent='分析完成';
      renderAddPositionRecommend(d);
    })
    .catch(function(e){
      btn.disabled=false;btn.textContent='📈 生成加仓推荐';
      document.getElementById('ap-status').textContent='分析失败: '+e.message;
    });
}

function renderAddPositionRecommend(d){
  _addPosRecData=d;
  document.getElementById('ap-results').style.display='block';

  var recs=d.recommendations||[];
  var skipped=d.skipped||[];
  var summary=d.summary||{};

  // KPIs
  document.getElementById('ap-rec-kpis').innerHTML=[
    kpiHtml(summary.totalPositions||0,'扫描持仓'),
    kpiHtml('<span style="color:var(--green)">'+(summary.recommendCount||recs.length)+'</span>','建议加仓'),
    kpiHtml(summary.skippedCount||skipped.length,'不建议加仓'),
    kpiHtml('¥'+numInt(summary.totalAddAmount||0),'建议加仓金额'),
    kpiHtml(pct(summary.avgConfidence||0),'平均置信度'),
  ].join('');

  // Show broker mode warning
  var modeHintEl=document.getElementById('ap-exec-mode-hint');
  if(modeHintEl){
    api('/quant/admin/api/engine-status').then(function(es){
      var isLive=es.brokerType&&es.brokerType!=='paper';
      if(isLive){
        modeHintEl.style.display='block';
        modeHintEl.style.background='rgba(248,81,73,.12)';
        modeHintEl.style.color='var(--red)';
        modeHintEl.innerHTML='⚠️ 当前引擎为 <strong>实盘模式 ('+esc(es.brokerType||'live')+')</strong>，执行加仓将提交真实委托';
      }else{
        modeHintEl.style.display='block';
        modeHintEl.style.background='rgba(13,148,136,.12)';
        modeHintEl.style.color='var(--green)';
        modeHintEl.innerHTML='🟢 当前引擎为 <strong>模拟盘模式</strong>，执行加仓仅在模拟账户中记录';
      }
    }).catch(function(){modeHintEl.style.display='none'});
  }
  document.getElementById('ap-exec-status').textContent='';
  document.getElementById('ap-exec-result').innerHTML='';

  // Recommendation cards
  var cardsHtml='';
  recs.forEach(function(r){
    var confColor=r.confidence>=80?'var(--green)':r.confidence>=60?'var(--accent)':'var(--orange)';
    var scoreColor=r.score>=70?'badge-green':r.score>=50?'badge-yellow':'badge-orange';

    cardsHtml+='<div class="card" style="margin-top:10px;border-left:3px solid var(--green);padding:14px">';

    // Header
    cardsHtml+='<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px">';
    cardsHtml+='<a href="'+B+'/quant/admin/stock?symbol='+esc(r.symbol)+'" target="_blank" style="color:var(--accent);font-weight:700;font-size:15px">'+esc(r.symbol)+'</a>';
    cardsHtml+='<span style="font-weight:600">'+esc(r.name)+'</span>';
    cardsHtml+='<span class="badge badge-green" style="font-size:13px">📈 建议加仓</span>';
    cardsHtml+='<span style="font-size:12px;color:'+confColor+'">置信度 '+r.confidence+'%</span>';
    cardsHtml+='<span class="badge '+scoreColor+'">评分 '+r.score+'</span>';
    cardsHtml+='</div>';

    // Two-column detail
    cardsHtml+='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;font-size:12px">';

    // Left: Position info
    cardsHtml+='<div style="background:var(--bg);border-radius:4px;padding:8px 10px">';
    cardsHtml+='<div style="font-weight:600;margin-bottom:4px">💼 当前持仓</div>';
    cardsHtml+='<div>持仓: '+numInt(r.currentShares||0)+' 股</div>';
    cardsHtml+='<div>成本: ¥'+num(r.costPrice||0)+'</div>';
    cardsHtml+='<div>现价: ¥'+num(r.currentPrice||0)+' <span style="color:'+pctColor(r.changePct||0)+'">'+pct(r.changePct||0)+'</span></div>';
    cardsHtml+='<div>盈亏: <span style="color:'+pctColor(r.pnlPct||0)+'">'+pct(r.pnlPct||0)+'</span></div>';
    cardsHtml+='</div>';

    // Right: Add position info
    cardsHtml+='<div style="background:var(--bg);border-radius:4px;padding:8px 10px">';
    cardsHtml+='<div style="font-weight:600;margin-bottom:4px">📈 加仓建议</div>';
    cardsHtml+='<div style="color:var(--green);font-weight:600">建议加仓 '+numInt(r.addShares||0)+' 股</div>';
    cardsHtml+='<div>加仓金额: ¥'+numInt(r.addAmount||0)+'</div>';
    if(r.entryPrice)cardsHtml+='<div>建议入场: ¥'+num(r.entryPrice)+'</div>';
    if(r.stopLoss)cardsHtml+='<div style="color:var(--red)">止损: ¥'+num(r.stopLoss)+'</div>';
    if(r.takeProfit)cardsHtml+='<div style="color:var(--green)">止盈: ¥'+num(r.takeProfit)+'</div>';
    cardsHtml+='</div>';

    cardsHtml+='</div>'; // end grid

    // Signals
    if(r.signals&&r.signals.length){
      cardsHtml+='<div style="margin-top:8px;font-size:12px">';
      cardsHtml+='<div style="font-weight:600;margin-bottom:4px">📊 加仓信号</div>';
      cardsHtml+='<div style="display:flex;flex-wrap:wrap;gap:4px">';
      r.signals.forEach(function(s){
        cardsHtml+='<span class="badge badge-blue" style="margin:1px">'+esc(s)+'</span>';
      });
      cardsHtml+='</div></div>';
    }

    // Reasons
    if(r.reasons&&r.reasons.length){
      cardsHtml+='<div style="margin-top:6px;font-size:12px">';
      cardsHtml+='<div style="font-weight:600;margin-bottom:3px">💡 判断依据</div>';
      r.reasons.forEach(function(reason){
        cardsHtml+='<div style="color:var(--text-dim)">• '+esc(reason)+'</div>';
      });
      cardsHtml+='</div>';
    }

    // Risk flags
    if(r.riskFlags&&r.riskFlags.length){
      cardsHtml+='<div style="margin-top:6px;font-size:11px;color:var(--orange)">';
      r.riskFlags.forEach(function(f){cardsHtml+='<div>⚠️ '+esc(f)+'</div>'});
      cardsHtml+='</div>';
    }

    cardsHtml+='</div>';
  });
  document.getElementById('ap-rec-cards').innerHTML=cardsHtml||'<div class="card" style="text-align:center;padding:30px;color:var(--text-dim)">当前持仓无加仓机会</div>';

  // Detail table
  var tbody=document.querySelector('#ap-table tbody');
  var rows='';
  recs.forEach(function(r,i){
    var confColor=r.confidence>=80?'var(--green)':r.confidence>=60?'var(--accent)':'var(--orange)';
    var scoreColor=r.score>=70?'badge-green':r.score>=50?'badge-yellow':'badge-orange';
    var reasonsHtml=(r.reasons||[]).slice(0,4).map(function(x){return '<span class="badge badge-yellow" style="margin:1px">'+esc(x)+'</span>'}).join(' ');

    var entryHtml='-';
    if(r.entryPrice){
      entryHtml='<div style="font-size:11px">¥'+num(r.entryPrice)+'</div>';
    }
    var exitHtml='-';
    if(r.stopLoss||r.takeProfit){
      exitHtml='<div style="font-size:11px;line-height:1.5">';
      if(r.stopLoss)exitHtml+='<div style="color:var(--red)">🛑¥'+num(r.stopLoss)+'</div>';
      if(r.takeProfit)exitHtml+='<div style="color:var(--green)">🎯¥'+num(r.takeProfit)+'</div>';
      exitHtml+='</div>';
    }

    rows+='<tr>'+
      '<td><input type="checkbox" class="ap-row-check" data-idx="'+i+'" checked onchange="updateApSelectionHint()"></td>'+
      '<td>'+(i+1)+'</td>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(r.symbol)+'" target="_blank" style="color:var(--accent);font-weight:700">'+esc(r.symbol)+'</a></td>'+
      '<td>'+esc(r.name)+'</td>'+
      '<td>'+num(r.currentPrice)+'</td>'+
      '<td style="color:'+pctColor(r.changePct||0)+'">'+pct(r.changePct||0)+'</td>'+
      '<td>'+numInt(r.currentShares||0)+'</td>'+
      '<td style="color:'+pctColor(r.pnlPct||0)+'">'+pct(r.pnlPct||0)+'</td>'+
      '<td style="color:var(--green);font-weight:600">'+numInt(r.addShares||0)+'</td>'+
      '<td>¥'+numInt(r.addAmount||0)+'</td>'+
      '<td>'+entryHtml+'</td>'+
      '<td>'+exitHtml+'</td>'+
      '<td style="color:'+confColor+'">'+r.confidence+'%</td>'+
      '<td><span class="badge '+scoreColor+'">'+r.score+'</span></td>'+
      '<td><div style="display:flex;flex-wrap:wrap;gap:2px">'+reasonsHtml+'</div></td>'+
      '</tr>';
  });
  tbody.innerHTML=rows||'<tr><td colspan="15" style="text-align:center;color:var(--text-dim)">无加仓推荐</td></tr>';

  // Skipped list
  if(skipped.length){
    document.getElementById('ap-skip-panel').style.display='block';
    var skipHtml='<div style="display:flex;flex-wrap:wrap;gap:6px">';
    skipped.forEach(function(s){
      skipHtml+='<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:12px">';
      skipHtml+='<strong>'+esc(s.symbol)+'</strong> '+esc(s.name||'');
      skipHtml+='<div style="color:var(--text-dim);font-size:11px">'+esc(s.reason||'不满足加仓条件')+'</div>';
      skipHtml+='</div>';
    });
    skipHtml+='</div>';
    document.getElementById('ap-skip-list').innerHTML=skipHtml;
  }else{
    document.getElementById('ap-skip-panel').style.display='none';
  }

  updateApSelectionHint();
  toast('✅ 加仓推荐生成完成');
}

function getSelectedApAllocations(){
  var recs=(_addPosRecData&&_addPosRecData.recommendations)||[];
  var selected=[];
  document.querySelectorAll('.ap-row-check:checked').forEach(function(cb){
    var idx=parseInt(cb.getAttribute('data-idx'));
    if(recs[idx])selected.push(recs[idx]);
  });
  return selected;
}

function updateApSelectionHint(){
  var sel=getSelectedApAllocations();
  var total=0;
  sel.forEach(function(a){total+=a.addAmount||0});
  var hint=document.getElementById('ap-sel-hint');
  if(hint)hint.textContent='已选 '+sel.length+' 只，合计 ¥'+numInt(total);
}

function toggleApSelectAll(checked){
  document.querySelectorAll('.ap-row-check').forEach(function(cb){cb.checked=checked});
  updateApSelectionHint();
}

function executeAddPosition(dryRun,forcePremarket){
  if(_addPosExecRunning)return;
  if(_addPosExecuted&&!dryRun){
    toast('已执行过加仓，请重新生成推荐后再下单');
    return;
  }
  var data=_addPosRecData;
  if(!data||!Array.isArray(data.recommendations)||!data.recommendations.length){
    toast('请先生成加仓推荐');
    return;
  }

  var selected=getSelectedApAllocations();
  if(!selected.length){
    toast('请至少勾选一只股票');
    return;
  }

  if(!dryRun&&!forcePremarket){
    var modeHintEl=document.getElementById('ap-exec-mode-hint');
    var modeText=modeHintEl&&modeHintEl.style.color==='var(--red)'?'【实盘模式】将提交真实加仓委托':'【模拟盘】将记录模拟加仓委托';
    if(!confirm(modeText+'（已选 '+selected.length+' 只）并创建条件单，是否继续？'))return;
  }
  if(forcePremarket){
    if(!confirm('将跳过交易时段检查，直接提交限价预埋委托到QMT。\\n委托将在开盘后按限价撮合。是否继续？'))return;
  }

  var pctVal=Number(document.getElementById('ap-exec-pct').value);
  if(!isFinite(pctVal))pctVal=100;
  pctVal=Math.max(10,Math.min(100,Math.round(pctVal)));

  var trailingVal=Number(document.getElementById('ap-exec-trailing').value);
  if(!isFinite(trailingVal))trailingVal=8;
  trailingVal=Math.max(2,Math.min(20,trailingVal));

  var createCond=document.getElementById('ap-exec-conds').checked;
  var statusEl=document.getElementById('ap-exec-status');
  var resultEl=document.getElementById('ap-exec-result');
  var dryBtn=document.getElementById('btn-ap-dry-run');
  var liveBtn=document.getElementById('btn-ap-exec');

  _addPosExecRunning=true;
  dryBtn.disabled=true;
  liveBtn.disabled=true;
  statusEl.style.color='var(--text-dim)';
  statusEl.textContent=forcePremarket?'正在提交预埋委托…':(dryRun?'正在试运行…':'正在提交加仓委托…');
  resultEl.innerHTML='';

  // Convert to recommend-execute format
  var allocations=selected.map(function(r){
    return {
      code:r.symbol,
      name:r.name,
      shares:r.addShares||0,
      price:r.currentPrice||0,
      entryPrices:{batch1:{price:r.entryPrice||r.currentPrice||0,pct:pctVal}},
      exitTargets:{
        stopLoss:r.stopLoss?{price:r.stopLoss,pct:-8}:undefined,
        takeProfit1:r.takeProfit?{price:r.takeProfit,pct:10}:undefined
      }
    };
  });

  api('/quant/admin/api/recommend-execute',{
    method:'POST',
    body:JSON.stringify({
      dry_run:dryRun,
      force_premarket:!!forcePremarket,
      execute_pct:pctVal,
      create_conditional_orders:createCond,
      trailing_stop_pct:trailingVal,
      allocations:allocations
    })
  })
    .then(function(resp){
      if(resp.error){
        statusEl.style.color='var(--red)';
        statusEl.textContent='执行失败: '+resp.error;
        if(resp.hint){
          resultEl.innerHTML='<div class="card" style="border:1px solid var(--red);background:rgba(248,81,73,.08)">'+esc(resp.hint)+'</div>';
        }
        toast('❌ '+resp.error);
        return;
      }

      var plan=resp.plan||{};
      var items=Array.isArray(plan.items)?plan.items:[];
      var skipped=Array.isArray(plan.skipped)?plan.skipped:[];

      if(resp.dryRun){
        statusEl.style.color='var(--green)';
        statusEl.textContent='试运行完成：计划加仓 '+(plan.totalOrders||0)+' 笔，预计金额 ¥'+numInt(plan.estimatedAmount||0)+'。';
        var html='<div class="card" style="padding:10px">';
        html+='<div style="font-size:12px;color:var(--text-dim);margin-bottom:6px">按比例 '+(plan.executePct||pctVal)+'% 生成加仓执行计划（未下单）</div>';
        if(items.length){
          html+='<div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>数量</th><th>限价</th><th>金额</th></tr></thead><tbody>';
          items.forEach(function(it){
            html+='<tr><td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(it.code)+'" target="_blank" style="color:var(--accent)">'+esc(it.code)+'</a></td><td>'+numInt(it.quantity)+'</td><td>'+num(it.limitPrice)+'</td><td>¥'+numInt(it.notional)+'</td></tr>';
          });
          html+='</tbody></table></div>';
        }
        if(skipped.length){
          html+='<div style="margin-top:8px;font-size:12px;color:var(--orange)">跳过 '+skipped.length+' 只：'+skipped.map(function(s){return esc(s.code+':'+s.reason)}).join('；')+'</div>';
        }
        html+='</div>';
        resultEl.innerHTML=html;
        toast('✅ 试运行完成');
        return;
      }

      var exec=resp.execution||{};
      var submitted=Number(exec.submitted)||0;
      var rejected=Number(exec.rejected)||0;
      var conds=Number(exec.conditionalOrders)||0;
      statusEl.style.color=rejected>0?'var(--orange)':'var(--green)';
      statusEl.textContent='加仓完成：已提交 '+submitted+' 笔，失败 '+rejected+' 笔，创建条件单 '+conds+' 个。';

      var rows='';
      (exec.orders||[]).forEach(function(o){
        rows+='<tr><td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(o.code)+'" target="_blank" style="color:var(--accent)">'+esc(o.code)+'</a></td><td>'+numInt(o.quantity)+'</td><td>'+num(o.price)+'</td><td>'+esc(o.orderId)+'</td><td>'+esc(o.status)+'</td><td>'+numInt((o.conditionals||[]).length)+'</td></tr>';
      });
      var failHtml='';
      if(Array.isArray(exec.failures)&&exec.failures.length){
        failHtml='<div style="margin-top:8px;font-size:12px;color:var(--orange)">失败明细：'+exec.failures.map(function(f){return esc(f.code+':'+f.reason)}).join('；')+'</div>';
      }
      resultEl.innerHTML='<div class="card" style="padding:10px">'+
        (rows?('<div style="overflow-x:auto"><table><thead><tr><th>代码</th><th>数量</th><th>价格</th><th>订单ID</th><th>状态</th><th>条件单</th></tr></thead><tbody>'+rows+'</tbody></table></div>'):'<div style="font-size:12px;color:var(--text-dim)">本次没有成功提交委托。</div>')+
        failHtml+
        '</div>';
      toast(rejected>0?'⚠️ 加仓已执行，存在部分失败':'✅ 加仓委托已提交');

      if(submitted>0){
        startFillConfirmPolling(exec.orders||[]);
      }
      _addPosExecuted=true;
    })
    .catch(function(e){
      statusEl.style.color='var(--red)';
      statusEl.textContent='执行失败: '+e.message;
    })
    .finally(function(){
      _addPosExecRunning=false;
      dryBtn.disabled=false;
      if(_addPosExecuted){
        liveBtn.disabled=true;
        liveBtn.title='已执行，请重新生成推荐后再下单';
      }else{
        liveBtn.disabled=false;
        liveBtn.title='';
      }
    });
}
`;
}
