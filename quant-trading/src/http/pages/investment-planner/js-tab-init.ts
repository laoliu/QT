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
function refreshGlobalMode(){
  api('/quant/admin/api/engine-status').then(function(d){
    var isLive=d.brokerType&&d.brokerType!=='paper';
    _cachedBrokerMode=isLive?d.brokerType:'paper';
    updateRebalanceModeHint(isLive,d.brokerType);
  }).catch(function(){});
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
  var el=document.getElementById('rebalance-mode-hint');
  if(!el)return;
  el.style.display='block';
  if(isLive){
    el.style.background='rgba(248,81,73,.12)';
    el.style.color='var(--red)';
    el.innerHTML='⚠️ 当前引擎为 <strong>实盘模式 ('+esc(brokerType||'live')+')</strong>，调仓操作将作用于实盘账户';
  }else{
    el.style.background='rgba(13,148,136,.12)';
    el.style.color='var(--green)';
    el.innerHTML='🟢 当前引擎为 <strong>模拟盘模式</strong>，调仓仅在模拟账户中记录';
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
