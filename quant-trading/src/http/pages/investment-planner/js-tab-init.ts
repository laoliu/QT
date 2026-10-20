/** Investment Planner — tab activation, data loading hooks, and strategy dropdown */
export function getTabInitJs(): string {
  return `
function activateTab(tabName){
  if(window._activateTab)window._activateTab(tabName);
  else{
    /* fallback if isolated script somehow didn't load */
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
    document.querySelectorAll('.tab-content').forEach(function(x){x.classList.remove('active')});
    var tabEl=document.querySelector('.tab[data-tab="'+tabName+'"]');
    var contentEl=document.getElementById('tab-'+tabName);
    if(!tabEl||!contentEl)return;
    tabEl.classList.add('active');
    contentEl.classList.add('active');
  }
  _onTabDataLoad(tabName);
}
function _onTabDataLoad(tabName){
  try{
    if(tabName==='portfolio'){refreshPortfolio();loadEquityCurve();}
    if(tabName==='signals')loadSignals();
    if(tabName==='milestones')loadMilestones();
    if(tabName==='screen'){loadScreenerHistory();restoreScreenResults();}
    if(tabName==='institution'){ensureInstitutionDefaults();loadInstitutionHistory();}
    if(tabName==='plan'){loadTodayPlan();loadPlanHistory();restoreCachedPlan();}
    if(tabName==='recommend'){loadDailyPickHistory();loadOptHistory();}
    if(tabName==='rebalance'){refreshGlobalMode();loadRebalancePortfolio();}
    if(tabName==='add-position')loadAddPositionPortfolio();
    if(tabName==='combo-backtest')loadComboTopStrategies();
    if(tabName==='recommend')refreshGlobalMode();
    if(tabName==='swing-overlay')refreshSwingDashboard();
  }catch(e){console.error('[tab-data-load]',e)}
}
/* wire the callback so the isolated tab script triggers data loads */
window._onTabActivated=function(tabName){_onTabDataLoad(tabName)};

// Load user-created composed strategies into strategy-preference dropdowns
function loadComposedStrategies(){
  api('/quant/admin/api/compositions').then(function(d){
    var comps=(d&&d.compositions)||[];
    if(!comps.length)return;
    var methodLabels={voting:'ͶƱ',weighted:'加权',gated:'门控',scored:'评分',cascade:'级联'};
    // Append to strategy-pref (investment plan) dropdown
    var prefSel=document.getElementById('strategy-pref');
    if(prefSel){
      var grp=document.createElement('optgroup');
      grp.label='── 自定义组合策略 ──';
      comps.forEach(function(c){
        var opt=document.createElement('option');
        opt.value='composed:'+c.id;
        opt.textContent=c.name+(c.method?' ('+( methodLabels[c.method]||c.method)+')':'');
        grp.appendChild(opt);
      });
      prefSel.appendChild(grp);
    }
    // Append to screen-strategy (screening) dropdown
    var screenSel=document.getElementById('screen-strategy');
    if(screenSel){
      var grp2=document.createElement('optgroup');
      grp2.label='── 自定义组合策略 ──';
      comps.forEach(function(c){
        var opt=document.createElement('option');
        opt.value='composed:'+c.id;
        opt.textContent=c.name+(c.method?' ('+(methodLabels[c.method]||c.method)+')':'');
        grp2.appendChild(opt);
      });
      screenSel.appendChild(grp2);
    }
    // Append to bt-strategy (backtest) dropdown
    var btSel=document.getElementById('bt-strategy');
    if(btSel){
      var grp3=document.createElement('optgroup');
      grp3.label='── 自定义组合策略 ──';
      comps.forEach(function(c){
        var opt=document.createElement('option');
        opt.value='composed:'+c.id;
        opt.textContent=c.name+(c.method?' ('+(methodLabels[c.method]||c.method)+')':'');
        grp3.appendChild(opt);
      });
      btSel.appendChild(grp3);
    }
    // Append to daily-pick strategy checkboxes
    var dpContainer=document.getElementById('dp-composed-strategies');
    if(dpContainer){
      var sep=document.createElement('span');
      sep.style.cssText='display:inline-block;width:1px;height:16px;background:var(--border);margin:0 8px;vertical-align:middle';
      dpContainer.appendChild(sep);
      comps.forEach(function(c){
        var lbl=document.createElement('label');
        lbl.style.cssText='min-width:auto;display:inline-flex;align-items:center;gap:2px;white-space:nowrap';
        var cb=document.createElement('input');
        cb.type='checkbox';
        cb.className='dp-strategy';
        cb.value='composed:'+c.id;
        cb.checked=true;
        cb.style.cssText='vertical-align:middle;margin-right:4px';
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(c.name));
        dpContainer.appendChild(lbl);
      });
    }
  }).catch(function(){/* compositions not available */});
}

/* ══════ VIEW MODE SELECTOR (combo box) ══════ */
var _cachedBrokerMode=null;
var _globalModeRequest=0;
function refreshGlobalMode(){
  var request=++_globalModeRequest;
  updateRebalanceModeHint(_cachedBrokerMode&&_cachedBrokerMode!=='paper',_cachedBrokerMode);
  return api('/quant/admin/api/engine-status').then(function(d){
    if(request!==_globalModeRequest)return;
    var brokerType=d&&!d.error&&d.brokerType||null;
    _cachedBrokerMode=brokerType;
    updateRebalanceModeHint(brokerType&&brokerType!=='paper',brokerType);
  }).catch(function(){
    if(request!==_globalModeRequest)return;
    _cachedBrokerMode=null;
    updateRebalanceModeHint(false,null);
  });
}

/* ── mode combo handler ── */
(function(){
  var sel=document.getElementById('global-mode-select');
  if(sel){
    sel.addEventListener('change',function(){
      var mode=sel.value;
      localStorage.setItem('qt-view-mode',mode);
      if(window._applyViewMode)window._applyViewMode(mode);
      /* reload plan data for this mode */
      currentPlan=null;currentPlanVersion=null;
      restoreCachedPlan();
      loadPlanHistory();
      updatePlanModeIndicator();
      /* load data for the default tab of this mode */
      var activeTab=document.querySelector('.tab.active');
      if(activeTab)_onTabDataLoad(activeTab.dataset.tab);
      refreshGlobalMode();
    });
  }
})();

function updateRebalanceModeHint(isLive,brokerType){
  var viewMode=window._currentViewMode||'paper';
  var viewLabel=viewMode==='live'?'实盘视图':'模拟盘视图';
  var mismatch=brokerType&&((viewMode==='live')!==!!isLive);
  var engineLabel=brokerType?(isLive?'实盘 ('+brokerType+')':'模拟盘 (paper)'):'状态未确认';
  var color=(!brokerType||mismatch)?'var(--yellow)':isLive?'var(--red)':'var(--green)';
  var badge=document.getElementById('global-engine-mode');
  if(badge){
    badge.textContent='交易引擎：'+engineLabel;
    badge.style.color=color;
    badge.title='账户视图不改变交易引擎；实际委托由交易引擎处理';
  }
  var el=document.getElementById('rebalance-mode-hint');
  if(!el)return;
  el.style.display='block';
  el.style.color=color;
  if(!brokerType){
    el.style.background='rgba(210,153,34,.12)';
    el.innerHTML='⚠️ 当前为 <strong>'+viewLabel+'</strong>；交易引擎状态未确认，请刷新检查。账户视图切换不会改变交易引擎。';
  }else if(mismatch){
    el.style.background='rgba(210,153,34,.12)';
    el.innerHTML='⚠️ 当前为 <strong>'+viewLabel+'</strong>，交易引擎为 <strong>'+esc(engineLabel)+'</strong>，两者不一致。视图切换不会改变交易引擎，实际委托由交易引擎处理。';
  }else if(isLive){
    el.style.background='rgba(248,81,73,.12)';
    el.innerHTML='⚠️ 当前为 <strong>实盘视图</strong>，交易引擎为 <strong>'+esc(engineLabel)+'</strong>，执行调仓将提交真实委托';
  }else{
    el.style.background='rgba(13,148,136,.12)';
    el.innerHTML='🟢 当前为 <strong>模拟盘视图</strong>，交易引擎为 <strong>模拟盘 (paper)</strong>，调仓仅在模拟账户中记录';
  }
}

function updatePlanModeIndicator(){
  var el=document.getElementById('plan-mode-indicator');
  if(!el)return;
  var mode=window._currentViewMode||'paper';
  if(mode==='live'){
    el.innerHTML='<span class="badge badge-red">🔴 实盘计划</span>';
  }else{
    el.innerHTML='<span class="badge badge-green">🟢 模拟盘计划</span>';
  }
}

// Initial data load (hash already restored by the isolated tab script)
(function(){
  loadComposedStrategies();
  refreshGlobalMode();
  updatePlanModeIndicator();
  loadPlanHistory();restoreCachedPlan();
  initRegimeListeners();
  loadMacroRegime();
  var h=(location.hash||'').replace('#','');
  if(h){_onTabDataLoad(h);}

  // Auto-restore daily-pick history when accessed via ?history=<id>
  var urlParams=new URLSearchParams(location.search);
  var historyId=urlParams.get('history');
  if(historyId){
    activateTab('recommend');
    fetchDailyPickHistory().then(function(){
      restoreDailyPickHistory(historyId);
    });
  }
})();
`;
}
