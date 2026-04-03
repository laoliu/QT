/** Investment Planner — chip accumulation detection, scanning, and Top3 rendering */
export function getAccScanJs(): string {
  return `
function detectAccumulation(){
  var symbol=document.getElementById('acc-symbol').value.trim();
  if(!symbol){toast('请输入股票代码');return;}
  var lookback=Number(document.getElementById('acc-lookback').value)||30;
  var asOfDate=document.getElementById('acc-date').value||'';
  var btn=document.getElementById('btn-acc-detect');
  btn.disabled=true;btn.textContent='⏳ 分析中…';
  document.getElementById('acc-status').textContent='正在分析 '+symbol+' 的吸筹信号…';
  document.getElementById('acc-result').style.display='none';
  document.getElementById('acc-scan-results').style.display='none';
  document.getElementById('acc-top3-results').style.display='none';
  document.getElementById('acc-detail').style.display='none';
  api('/quant/admin/api/chip-accumulation',{method:'POST',body:JSON.stringify({action:'detect',symbol:symbol,lookback:lookback,as_of_date:asOfDate})})
    .then(function(d){
      btn.disabled=false;btn.textContent='🔍 检测吸筹';
      if(d.error){document.getElementById('acc-status').textContent='❌ '+d.error;return;}
      renderAccResult(d);
      document.getElementById('acc-status').textContent='分析完成';
      toast('✅ 吸筹检测完成');
    }).catch(function(e){
      btn.disabled=false;btn.textContent='🔍 检测吸筹';
      document.getElementById('acc-status').textContent='❌ '+(e&&e.message||'检测失败，请检查网络连接');
    });
}

function detailAccumulation(){
  var symbol=document.getElementById('acc-symbol').value.trim();
  if(!symbol){toast('请输入股票代码');return;}
  var lookback=Number(document.getElementById('acc-lookback').value)||30;
  var asOfDate=document.getElementById('acc-date').value||'';
  document.getElementById('acc-status').textContent='正在获取 '+symbol+' 的详细分析…';
  document.getElementById('acc-result').style.display='none';
  document.getElementById('acc-top3-results').style.display='none';
  document.getElementById('acc-detail').style.display='none';
  api('/quant/admin/api/chip-accumulation',{method:'POST',body:JSON.stringify({action:'detail',symbol:symbol,lookback:lookback,as_of_date:asOfDate})})
    .then(function(d){
      if(d.error){document.getElementById('acc-status').textContent='❌ '+d.error;return;}
      renderAccResult(d);
      renderAccDetail(d);
      document.getElementById('acc-status').textContent='详细分析完成';
      toast('✅ 详细分析完成');
    }).catch(function(e){
      document.getElementById('acc-status').textContent='❌ '+(e&&e.message||'详细分析失败，请检查网络连接');
    });
}

function scanAccumulation(){
  var symbols=document.getElementById('acc-symbols').value.trim();
  var minScore=Number(document.getElementById('acc-min-score').value)||40;
  var lookback=Number(document.getElementById('acc-lookback').value)||30;
  var asOfDate=document.getElementById('acc-date').value||'';
  var btn=document.getElementById('btn-acc-scan');
  btn.disabled=true;btn.textContent='⏳ 扫描中…';
  document.getElementById('acc-status').textContent='正在批量扫描吸筹候选…';
  document.getElementById('acc-result').style.display='none';
  document.getElementById('acc-scan-results').style.display='none';
  document.getElementById('acc-top3-results').style.display='none';
  document.getElementById('acc-detail').style.display='none';
  api('/quant/admin/api/chip-accumulation',{method:'POST',body:JSON.stringify({action:'scan',symbols:symbols,lookback:lookback,min_confidence:minScore,as_of_date:asOfDate})})
    .then(function(d){
      btn.disabled=false;btn.textContent='🔎 批量扫描';
      if(d.error){document.getElementById('acc-status').textContent='❌ '+d.error;return;}
      renderAccScan(d);
      document.getElementById('acc-status').textContent='扫描完成，找到 '+(d.count||0)+' 只候选，正在同步复合前三…';
      scanAccumulationTop3({silent:true});
      toast('✅ 批量扫描完成');
    }).catch(function(e){
      btn.disabled=false;btn.textContent='🔎 批量扫描';
      document.getElementById('acc-status').textContent='❌ '+(e&&e.message||'批量扫描失败，请检查网络连接');
    });
}

function scanAccumulationTop3(opts){
  opts=opts||{};
  var silent=!!opts.silent;
  var symbols=document.getElementById('acc-symbols').value.trim();
  var minScore=Number(document.getElementById('acc-min-score').value)||40;
  var lookback=Number(document.getElementById('acc-lookback').value)||30;
  var asOfDate=document.getElementById('acc-date').value||'';
  var thresholds=readAccumulationTop3ThresholdsFromForm();
  var presetKey=detectAccumulationTop3PresetByThresholds(thresholds);
  persistAccumulationTop3Thresholds(thresholds,presetKey);
  setAccumulationTop3PresetSelection(presetKey);
  var btn=document.getElementById('btn-acc-top3');
  btn.disabled=true;btn.textContent='⏳ 计算中…';
  document.getElementById('acc-status').textContent='正在按前期吸筹 / 机构抢筹 / 技术壁垒或重组信息计算复合前三…';
  document.getElementById('acc-result').style.display='none';
  document.getElementById('acc-top3-results').style.display='none';
  document.getElementById('acc-detail').style.display='none';
  api('/quant/admin/api/chip-accumulation/top3',{method:'POST',body:JSON.stringify({symbols:symbols,lookback:lookback,min_confidence:minScore,evaluate_count:20,as_of_date:asOfDate,pre_accum_days:thresholds.pre_accum_days,pre_accum_count_min:thresholds.pre_accum_count_min,pre_accum_volume_ratio:thresholds.pre_accum_volume_ratio,rush_change_pct:thresholds.rush_change_pct,rush_volume_ratio:thresholds.rush_volume_ratio,rush_turnover_pct:thresholds.rush_turnover_pct,pre_surge_chip_min:thresholds.pre_surge_chip_min,pre_surge_volume_price_min:thresholds.pre_surge_volume_price_min,pre_surge_turnover_min:thresholds.pre_surge_turnover_min})})
    .then(function(d){
      btn.disabled=false;btn.textContent='🏆 复合前三';
      if(d.error){document.getElementById('acc-status').textContent='❌ '+d.error;return;}
      renderAccTop3(d);
      document.getElementById('acc-status').textContent=silent?'扫描完成，复合前三已同步':'复合前三计算完成';
      if(!silent)toast('✅ 复合前三已更新');
    }).catch(function(e){
      btn.disabled=false;btn.textContent='🏆 复合前三';
      document.getElementById('acc-status').textContent='❌ '+(e&&e.message||'复合前三计算失败，请检查网络连接');
    });
}

function renderAccResult(d){
  var el=document.getElementById('acc-result');
  el.style.display='block';
  var score=d.total_score||0;
  var scoreColor=score>=70?'var(--green)':score>=50?'#d29922':score>=30?'var(--text-dim)':'var(--red)';
  var levelLabels={strong:'🟢 明显吸筹',moderate:'🟡 疑似吸筹',weak:'⚪ 信号弱',none:'🔴 未发现'};
  document.getElementById('acc-kpis').innerHTML=[
    '<div class="kpi"><div class="kpi-val" style="color:'+scoreColor+'">'+score.toFixed(1)+'<span style="font-size:14px;color:var(--text-dim)">/100</span></div><div class="kpi-label">吸筹评分 <span style="font-size:11px">('+score.toFixed(1)+'%)</span></div></div>',
    kpiHtml(levelLabels[d.level]||d.level||'-','判断等级'),
    kpiHtml(esc(d.name||d.symbol||'-'),'股票'),
    kpiHtml(d.price?num(d.price):'-','现价'),
    kpiHtml(esc(d.advice||'-'),'建议')
  ].join('');

  var dims=d.dimension_scores||{};
  var dimLabels={fund_flow:'资金流向',chip:'筹码分布',volume_price:'量价结构',turnover:'换手率',large_order:'大单净量'};
  var dimHtml='';
  Object.keys(dimLabels).forEach(function(k){
    var v=dims[k]||0;
    var pct=Math.round(v/20*100);
    var sharePct=score>0?Math.round(v/score*100):0;
    var barColor=v>=14?'var(--green)':v>=8?'#d29922':'var(--red)';
    dimHtml+='<div style="margin-bottom:8px"><div style="display:flex;justify-content:space-between;font-size:12px;margin-bottom:2px"><span>'+dimLabels[k]+'</span><span style="color:'+barColor+'">'+v.toFixed(1)+'/20<span style="color:var(--text-dim);font-size:11px"> · 占'+sharePct+'%</span></span></div>';
    dimHtml+='<div style="background:var(--card-bg);border-radius:4px;height:8px;overflow:hidden"><div style="height:100%;width:'+pct+'%;background:'+barColor+';border-radius:4px;transition:width .3s"></div></div></div>';
  });
  document.getElementById('acc-dimensions').innerHTML=dimHtml;

  var signals=d.signals||[];
  var dimNames={fund_flow:'资金流向',chip:'筹码分布',volume_price:'量价结构',turnover:'换手率',large_order:'大单净量'};
  var sigHtml='';
  signals.forEach(function(s){
    var icon=s.score>=10?'✅':s.score>=5?'🔶':'⚪';
    var label=dimNames[s.dimension]||s.dimension||'';
    sigHtml+='<li>'+icon+' <strong>['+esc(label)+']</strong> '+esc(s.description||'')+'</li>';
  });
  document.getElementById('acc-signals').innerHTML=sigHtml||'<li style="color:var(--text-dim)">无信号</li>';
}

function renderAccScan(d){
  _accScanState=d;
  var el=document.getElementById('acc-scan-results');
  el.style.display='block';
  var stocks=filterAccumulationScanRows(d.stocks||[]);
  document.getElementById('acc-scan-count').textContent=String(stocks.length);
  var tbody=document.querySelector('#acc-scan-table tbody');
  var rows='';
  stocks.forEach(function(s){
    var score=s.total_score||0;
    var scoreColor=score>=70?'var(--green)':score>=50?'#d29922':'var(--text-dim)';
    var dims=s.dimension_scores||{};
    var levelLabels={strong:'明显吸筹',moderate:'疑似吸筹',weak:'信号弱',none:'未发现'};
    rows+='<tr>'+
      '<td><strong>'+esc(s.symbol||'')+'</strong></td>'+
      '<td>'+esc(s.name||'')+'</td>'+
      '<td>'+num(s.price)+'</td>'+
      '<td style="color:'+scoreColor+';font-weight:bold">'+score.toFixed(1)+'</td>'+
      '<td>'+(levelLabels[s.level]||s.level||'-')+'</td>'+
      '<td>'+num(dims.fund_flow)+'</td>'+
      '<td>'+num(dims.chip)+'</td>'+
      '<td>'+num(dims.volume_price)+'</td>'+
      '<td>'+num(dims.turnover)+'</td>'+
      '<td>'+num(dims.large_order)+'</td>'+
      '<td>'+esc(s.conclusion||'')+'</td>'+
      '<td><button class="btn btn-sm" style="padding:2px 8px;font-size:11px" onclick="accDetectOne(\\''+esc(s.symbol)+'\\')">详情</button></td>'+
      '</tr>';
  });
  tbody.innerHTML=rows||'<tr><td colspan="12" style="text-align:center;color:var(--text-dim)">当前筛选下无候选</td></tr>';
}

function renderAccDetail(d){
  var flows=d.fund_flow_series||[];
  if(!flows.length)return;
  document.getElementById('acc-detail').style.display='block';
  var tbody=document.querySelector('#acc-flow-table tbody');
  var rows='';
  flows.forEach(function(f){
    var mainNet=f.mainNetIn||0;
    var color=mainNet>=0?'var(--green)':'var(--red)';
    rows+='<tr>'+
      '<td>'+esc(f.date||'')+'</td>'+
      '<td style="color:'+color+'">'+(mainNet/10000).toFixed(1)+'万</td>'+
      '<td>'+((f.superLargeNetIn||0)/10000).toFixed(1)+'万</td>'+
      '<td>'+((f.largeNetIn||0)/10000).toFixed(1)+'万</td>'+
      '</tr>';
  });
  tbody.innerHTML=rows;
}

function renderAccTop3(d){
  _accTop3State=d;
  var el=document.getElementById('acc-top3-results');
  el.style.display='block';
  var allRows=Array.isArray(d.top3)?d.top3:[];
  var rows=filterAccumulationTop3Rows(allRows);
  document.getElementById('acc-top3-count').textContent=String(rows.length);
  var th=d.thresholds||{};
  var filterLabelMap={all:'全部主因',resonance:'吸筹与抢筹共振',pre:'前期吸筹主导',rush:'机构抢筹主导',chip:'纯吸筹评分达标'};
  var filterKey=getAccumulationTop3ReasonFilter();
  var backboneSuffix=isAccumulationTop3BackboneOnly()?'，且仅保留纯吸筹分≥20的骨架候选':'';
  document.getElementById('acc-top3-summary').textContent=
    '复合规则: 近'+String(th.pre_accum_days||10)+'天放量≥'+String(th.pre_accum_count_min||2)+'次且放量阈值≥'+num(th.pre_accum_volume_ratio||1.5)+'x MA5；今日涨幅≥'+num(th.rush_change_pct||5)+'%、量比≥'+num(th.rush_volume_ratio||2)+'、换手≥'+num(th.rush_turnover_pct||3)+'%；启动前异动需筹码≥'+num(th.pre_surge_chip_min||8)+'、量价≥'+num(th.pre_surge_volume_price_min||6)+'、换手≥'+num(th.pre_surge_turnover_min||6)+'；并叠加技术壁垒或重组信息。当前筛选: '+String(filterLabelMap[filterKey]||'全部主因')+backboneSuffix+'（原始 '+String(allRows.length)+' 只，当前 '+String(rows.length)+' 只）。';
  var tbody=document.querySelector('#acc-top3-table tbody');
  tbody.innerHTML=rows.map(function(item){
    var symbol=String(item.symbol||'');
    var symbolJs=escAttrJsSingleQuoted(symbol);
    var nameJs=escAttrJsSingleQuoted(item.name||'');
    var hits=[];
    if(item.conditionHits&&item.conditionHits.preAccumulation)hits.push('<span class="badge badge-green">前期吸筹</span>');
    if(item.conditionHits&&item.conditionHits.institutionalRush)hits.push('<span class="badge badge-blue">机构抢筹</span>');
    if(item.conditionHits&&item.conditionHits.techMoatOrRestructuring)hits.push('<span class="badge badge-purple">壁垒/重组</span>');
    if(item.conditionHits&&item.conditionHits.preSurgeAnomaly)hits.push('<span class="badge badge-yellow">启动前异动</span>');
    var moatCell='-';
    if(Number(item.moatScore||0)>0){
      moatCell='<span class="badge badge-purple">'+num(item.moatScore)+'</span> '+esc(item.moatGrade||'');
    }
    if(item.restructureKeywords&&item.restructureKeywords.length){
      var restructureNote='<span style="font-size:11px;color:var(--text-dim)">重组词: '+esc(item.restructureKeywords.join('、'))+'</span>';
      moatCell = moatCell==='-' ? restructureNote : moatCell+'<br>'+restructureNote;
    }
    var notes=[
      item.conditionDetails&&item.conditionDetails.preAccumulation,
      item.conditionDetails&&item.conditionDetails.institutionalRush,
      item.conditionDetails&&item.conditionDetails.techMoatOrRestructuring,
      item.conditionDetails&&item.conditionDetails.preSurgeAnomaly
    ].filter(Boolean).slice(0,3).map(function(text){
      return '<div style="margin-bottom:4px">• '+esc(text)+'</div>';
    }).join('');
    var ambushReasons=Array.isArray(item.ambushReasons)?item.ambushReasons:[];
    var riskFlags=Array.isArray(item.riskFlags)?item.riskFlags:[];
    var scoreComponents=item.scoreComponents&&typeof item.scoreComponents==='object'?item.scoreComponents:null;
    if(ambushReasons.length){
      notes+='<div style="margin-top:6px;color:var(--text-dim)">潜伏理由: '+esc(ambushReasons.slice(0,2).join('；'))+'</div>';
    }
    if(scoreComponents){
      notes+='<div style="margin-top:4px;color:var(--text-dim)">分项: 放量'+num(scoreComponents.pre_accum_hits)+' / 主力'+num((Number(scoreComponents.main_force_zljc)||0)+(Number(scoreComponents.main_force_var6)||0))+' / 均线'+num(scoreComponents.ma_deviation)+' / 涨跌'+num(scoreComponents.day_change)+'</div>';
    }
    if(riskFlags.length){
      notes+='<div style="margin-top:4px;color:var(--yellow)">风险: '+esc(riskFlags.slice(0,2).join('；'))+'</div>';
    }
    var reasonTrail=Array.isArray(item.reasonTrail)?item.reasonTrail:[];
    var reasonCell='<strong>'+esc(item.selectionReason||'复合信号入围')+'</strong>';
    if(reasonTrail.length){
      reasonCell+='<br><span style="font-size:11px;color:var(--text-dim)">'+esc(reasonTrail.join(' · '))+'</span>';
    }
    return '<tr>'+
      '<td><span class="badge badge-purple">#'+numInt(item.rank||0)+'</span></td>'+
      '<td><strong>'+esc(symbol)+'</strong></td>'+
      '<td>'+esc(item.name||'')+'</td>'+
      '<td>'+(hits.join(' ')||'<span style="color:var(--text-dim)">暂无</span>')+'</td>'+
      '<td style="font-size:11px;line-height:1.5">'+reasonCell+'</td>'+
      '<td><strong>'+esc(item.judgment||'-')+'</strong><br><span style="font-size:11px;color:var(--text-dim)">命中 '+numInt(item.conditionHitCount||0)+'/3 · 综合 '+num(item.totalScore||0)+'</span></td>'+
      '<td><span class="badge badge-green">'+num(item.chipScore||0)+'</span></td>'+
      '<td><span class="badge badge-blue">建仓 '+num(item.buildScore||0)+'</span><br><span style="font-size:11px;color:var(--text-dim)">置信 '+num(item.confidence||0)+' · 净判断 '+num(item.netConviction||0)+'</span></td>'+
      '<td>'+moatCell+'</td>'+
      '<td style="font-size:11px;line-height:1.5">'+(notes||'<span style="color:var(--text-dim)">暂无说明</span>')+'</td>'+
      '<td><button class="btn btn-sm" style="padding:2px 8px;font-size:11px" onclick="accDetectOne(\\\''+symbolJs+'\\\')">详情</button> <a class="btn btn-sm" style="padding:2px 8px;font-size:11px;text-decoration:none" href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(symbol)+'" target="_blank">个股页</a> <button class="btn btn-sm" style="padding:2px 8px;font-size:11px" onclick="addAccumulationTop3ToFavorites(\\\''+symbolJs+'\\\',\\\''+nameJs+'\\\')">收藏</button></td>'+
      '</tr>';
  }).join('')||'<tr><td colspan="11" style="text-align:center;color:var(--text-dim)">当前筛选下暂无候选</td></tr>';
}

function addAccumulationTop3ToFavorites(symbol,name){
  if(!symbol){toast('⚠️ 缺少股票代码');return}
  api('/quant/admin/api/favorites',{
    method:'POST',
    body:JSON.stringify({
      code:symbol,
      name:name||symbol,
      category:'accumulation-top3',
      tags:['accumulation','top3'],
      priority:5,
      notes:'来自主力吸筹复合前三面板'
    })
  }).then(function(d){
    if(d&&d.ok){toast('✅ 已加入收藏: '+symbol);return}
    toast('❌ 加入收藏失败');
  }).catch(function(){toast('❌ 加入收藏失败')});
}

function accDetectOne(symbol){
  document.getElementById('acc-symbol').value=symbol;
  detailAccumulation();
}

function getAccumulationTop3ReasonFilter(){
  var el=document.getElementById('acc-top3-reason-filter');
  return el?String(el.value||'all'):'all';
}

function getAccumulationScanFilters(){
  var totalEl=document.getElementById('acc-scan-filter-total');
  var chipEl=document.getElementById('acc-scan-filter-chip');
  return {
    totalMin:Math.max(0,Math.min(100,Number(totalEl&&totalEl.value)||0)),
    chipMin:Math.max(0,Math.min(20,Number(chipEl&&chipEl.value)||0))
  };
}

function filterAccumulationScanRows(rows){
  rows=Array.isArray(rows)?rows:[];
  var filters=getAccumulationScanFilters();
  return rows.filter(function(item){
    var score=Number(item&&item.total_score||0);
    var chip=Number(item&&item.dimension_scores&&item.dimension_scores.chip||0);
    return score>=filters.totalMin && chip>=filters.chipMin;
  });
}

function onAccumulationScanFilterChange(){
  if(_accScanState)renderAccScan(_accScanState);
}

function isAccumulationTop3BackboneOnly(){
  var el=document.getElementById('acc-top3-backbone-only');
  return !!(el&&el.checked);
}

function filterAccumulationTop3Rows(rows){
  rows=Array.isArray(rows)?rows:[];
  var filterKey=getAccumulationTop3ReasonFilter();
  return rows.filter(function(item){
    if(isAccumulationTop3BackboneOnly() && Number(item&&item.chipScore||0)<20)return false;
    var reason=String(item&&item.selectionReason||'');
    if(filterKey==='all')return true;
    if(filterKey==='resonance')return reason==='吸筹与抢筹共振';
    if(filterKey==='pre')return reason==='前期吸筹主导';
    if(filterKey==='rush')return reason==='机构抢筹主导';
    if(filterKey==='chip')return reason==='纯吸筹评分达标';
    return true;
  });
}

function onAccumulationTop3ReasonFilterChange(){
  if(_accTop3State)renderAccTop3(_accTop3State);
}
`;
}
