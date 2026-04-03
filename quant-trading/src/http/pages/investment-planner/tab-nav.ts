/**
 * Investment Planner — tab navigation script (independent of main logic).
 * Separated so a runtime error in the main script cannot break tab switching.
 */
export const TAB_NAV_SCRIPT = `
(function(){
  /* ── event-delegation: one listener on .tabs, survives innerHTML changes ── */
  var tabsContainer=document.querySelector('.tabs');
  if(!tabsContainer)return;

  function activateTab(name){
    document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
    document.querySelectorAll('.tab-content').forEach(function(x){x.classList.remove('active')});
    var tabEl=document.querySelector('.tab[data-tab="'+name+'"]');
    var contentEl=document.getElementById('tab-'+name);
    if(!tabEl||!contentEl)return;
    tabEl.classList.add('active');
    contentEl.classList.add('active');
    /* notify main script so data-loading hooks still fire */
    try{if(typeof window._onTabActivated==='function')window._onTabActivated(name)}catch(e){console.error('[tab]',e)}
  }

  tabsContainer.addEventListener('click',function(e){
    var el=e.target;
    while(el&&el!==tabsContainer){
      if(el.classList&&el.classList.contains('tab')&&el.dataset.tab){
        var tab=el.dataset.tab;
        activateTab(tab);
        if(location.hash!=='#'+tab)history.pushState(null,null,'#'+tab);
        return;
      }
      el=el.parentElement;
    }
  });

  window.addEventListener('popstate',function(){
    var h=(location.hash||'').replace('#','');
    activateTab(h||'plan');
  });

  /* expose for main script */
  window._activateTab=activateTab;

  /* restore from hash */
  var h=(location.hash||'').replace('#','');
  if(h)activateTab(h);
})();
`;
