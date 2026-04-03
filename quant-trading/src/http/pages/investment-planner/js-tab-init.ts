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
    if(tabName==='portfolio')refreshPortfolio();
    if(tabName==='signals')loadSignals();
    if(tabName==='milestones')loadMilestones();
    if(tabName==='screen'){loadScreenerHistory();restoreScreenResults();}
    if(tabName==='institution'){ensureInstitutionDefaults();loadInstitutionHistory();}
    if(tabName==='plan'){loadPlanHistory();restoreCachedPlan();}
    if(tabName==='recommend'){loadDailyPickHistory();loadOptHistory();}
  }catch(e){console.error('[tab-data-load]',e)}
}
/* wire the callback so the isolated tab script triggers data loads */
window._onTabActivated=function(tabName){_onTabDataLoad(tabName)};

// Load user-created composed strategies into strategy-preference dropdowns
function loadComposedStrategies(){
  api('/quant/admin/api/compositions').then(function(d){
    var comps=(d&&d.compositions)||[];
    if(!comps.length)return;
    var methodLabels={voting:'投票',weighted:'加权',gated:'门控',scored:'评分',cascade:'级联'};
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

// Initial data load (hash already restored by the isolated tab script)
(function(){
  loadComposedStrategies();
  var h=(location.hash||'').replace('#','');
  if(!h){loadPlanHistory();restoreCachedPlan();}
  else{_onTabDataLoad(h);}
})();
`;
}
