/**
 * Investment Planner — tab navigation script (independent of main logic).
 * Separated so a runtime error in the main script cannot break tab switching.
 * Supports paper / live view-mode switching via combo box.
 */
export const TAB_NAV_SCRIPT = `
(function(){
  var tabsContainer=document.querySelector('.tabs');
  if(!tabsContainer)return;

  /* ── paper tabs / live tabs sets ── */
  var PAPER_TABS=['plan','screen','institution','accumulation','backtest','combo-backtest','pf-backtest','portfolio','signals'];
  var LIVE_TABS=['plan','recommend','add-position','rebalance','signals','milestones'];
  function _isTabVisible(name){
    var mode=window._currentViewMode||'paper';
    var allowed=mode==='live'?LIVE_TABS:PAPER_TABS;
    return allowed.indexOf(name)>=0;
  }

  function activateTab(name){
    if(!_isTabVisible(name))return;
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
    document.querySelectorAll('.tab-content').forEach(function(x){x.classList.remove('active')});
    var tabEl=document.querySelector('.tab[data-tab="'+name+'"]');
    var contentEl=document.getElementById('tab-'+name);
    if(!tabEl||!contentEl)return;
    tabEl.classList.add('active');
    contentEl.classList.add('active');
    try{if(typeof window._onTabActivated==='function')window._onTabActivated(name)}catch(e){console.error('[tab]',e)}
  }

  /* ── mode switching: show/hide tab groups ── */
  function applyViewMode(mode){
    window._currentViewMode=mode;
    document.querySelectorAll('.tab-group').forEach(function(g){
      var gm=g.dataset.mode;
      if(!gm||gm==='shared')return;
      g.style.display=(gm===mode)?'flex':'none';
    });
    /* also hide tab-content panels that don't belong to this mode */
    var allowed=mode==='live'?LIVE_TABS:PAPER_TABS;
    document.querySelectorAll('.tab-content').forEach(function(c){
      var tabName=(c.id||'').replace('tab-','');
      if(tabName&&allowed.indexOf(tabName)<0){
        c.classList.remove('active');
        c.style.display='none';
      }else{
        c.style.display='';
      }
    });
    /* if current active tab is not in this mode, switch to default */
    var activeTab=document.querySelector('.tab.active');
    var activeTabName=activeTab?activeTab.dataset.tab:'';
    if(!activeTabName||allowed.indexOf(activeTabName)<0){
      var def=mode==='live'?'recommend':'plan';
      activateTab(def);
      history.replaceState(null,null,'#'+def);
    }
    /* update body class for styling */
    document.body.classList.remove('mode-paper','mode-live');
    document.body.classList.add('mode-'+mode);
    /* update nav-guide visibility */
    var gp=document.querySelector('.nav-guide-paper');
    var gl=document.querySelector('.nav-guide-live');
    if(gp)gp.style.display=mode==='paper'?'':'none';
    if(gl)gl.style.display=mode==='live'?'':'none';
  }
  window._applyViewMode=applyViewMode;

  tabsContainer.addEventListener('click',function(e){
    var el=e.target;
    while(el&&el!==tabsContainer){
      if(el.classList&&el.classList.contains('tab')&&el.dataset.tab){
        var tab=el.dataset.tab;
        if(!_isTabVisible(tab))return;
        activateTab(tab);
        if(location.hash!=='#'+tab)history.pushState(null,null,'#'+tab);
        return;
      }
      el=el.parentElement;
    }
  });

  window.addEventListener('popstate',function(){
    var h=(location.hash||'').replace('#','');
    if(_isTabVisible(h))activateTab(h);
    else activateTab('plan');
  });

  window._activateTab=activateTab;

  /* initial mode from localStorage */
  var savedMode=localStorage.getItem('qt-view-mode')||'paper';
  applyViewMode(savedMode);
  var sel=document.getElementById('global-mode-select');
  if(sel)sel.value=savedMode;

  /* restore hash */
  var h=(location.hash||'').replace('#','');
  if(h&&_isTabVisible(h))activateTab(h);
})();
`;
