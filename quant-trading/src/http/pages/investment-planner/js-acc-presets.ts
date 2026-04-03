/** Investment Planner — accumulation Top3 preset help and popover */
export function getAccPresetsJs(): string {
  return `
function normalizeAccumulationTop3PresetKey(key){
  key=String(key||'').trim();
  if(key==='conservative')return 'stable';
  if(key==='balanced')return 'swing';
  if(key==='aggressive')return 'shortline';
  return key;
}

function accumulationTop3PresetLabel(key){
  if(key==='stable')return '稳健';
  if(key==='swing')return '波段';
  if(key==='shortline')return '短线';
  return '自定义';
}

function formatAccumulationTop3PresetCompareLine(key,label){
  var p=ACC_TOP3_PRESETS[key];
  if(!p)return label+': -';
  return label+': 前期吸筹 '+Number(p.pre_accum_days)+'天/≥'+Number(p.pre_accum_count_min)+'次/'+Number(p.pre_accum_volume_ratio)+'x，机构抢筹 '+Number(p.rush_change_pct)+'%/'+Number(p.rush_volume_ratio)+'x/'+Number(p.rush_turnover_pct)+'%，异动 '+Number(p.pre_surge_chip_min)+'/'+Number(p.pre_surge_volume_price_min)+'/'+Number(p.pre_surge_turnover_min);
}

function updateAccumulationTop3PresetHelp(key){
  var helpEl=document.getElementById('acc-top3-preset-help');
  var panelEl=document.getElementById('acc-top3-preset-help-panel');
  if(!helpEl||!panelEl)return;
  key=normalizeAccumulationTop3PresetKey(key);
  var current=ACC_TOP3_PRESETS[key]?key:'custom';
  var lines=['建议参数档位对比（前期吸筹 / 机构抢筹 / 启动前异动）'];
  lines.push('当前档位: '+accumulationTop3PresetLabel(current));
  lines.push(formatAccumulationTop3PresetCompareLine('stable','稳健'));
  lines.push(formatAccumulationTop3PresetCompareLine('swing','波段'));
  lines.push(formatAccumulationTop3PresetCompareLine('shortline','短线'));
  helpEl.title=lines.join('\\n');
  var keys=['stable','swing','shortline'];
  var html='<div style="font-weight:600;margin-bottom:6px">建议参数档位对比</div>'+
    '<div style="color:var(--text-dim);margin-bottom:8px">当前档位: '+esc(accumulationTop3PresetLabel(current))+'</div>';
  html+='<table class="acc-top3-help-table"><thead><tr><th>档位</th><th>前期吸筹</th><th>机构抢筹</th><th>启动前异动</th></tr></thead><tbody>';
  for(var i=0;i<keys.length;i++){
    var k=keys[i];
    var p=ACC_TOP3_PRESETS[k];
    if(!p)continue;
    var label=accumulationTop3PresetLabel(k);
    var active=(k===current);
    html+='<tr'+(active?' class="current"':'')+'>'+
      '<td>'+esc(label)+(active?'（当前）':'')+'</td>'+
      '<td>'+Number(p.pre_accum_days)+'天 / ≥'+Number(p.pre_accum_count_min)+'次 / '+Number(p.pre_accum_volume_ratio)+'x</td>'+
      '<td>'+Number(p.rush_change_pct)+'% / '+Number(p.rush_volume_ratio)+'x / '+Number(p.rush_turnover_pct)+'%</td>'+
      '<td>'+Number(p.pre_surge_chip_min)+' / '+Number(p.pre_surge_volume_price_min)+' / '+Number(p.pre_surge_turnover_min)+'</td>'+
    '</tr>';
  }
  html+='</tbody></table>';
  panelEl.innerHTML=html;
}

function toggleAccumulationTop3PresetHelp(ev){
  if(ev){ev.preventDefault();ev.stopPropagation();}
  var helpEl=document.getElementById('acc-top3-preset-help');
  var panelEl=document.getElementById('acc-top3-preset-help-panel');
  if(!helpEl||!panelEl)return;
  var open=(panelEl.style.display==='none'||!panelEl.style.display);
  setAccumulationTop3PresetHelpOpen(open,open);
}

function positionAccumulationTop3PresetHelpPanel(){
  var panelEl=document.getElementById('acc-top3-preset-help-panel');
  if(!panelEl)return;
  if(panelEl.style.display==='none'||!panelEl.style.display)return;
  panelEl.style.left='0';
  panelEl.style.right='auto';
  panelEl.style.maxWidth='min(92vw,620px)';
  var vw=Math.max(document.documentElement.clientWidth||0,window.innerWidth||0);
  panelEl.style.maxWidth=Math.max(260,vw-16)+'px';
  var rect=panelEl.getBoundingClientRect();
  if(rect.right>vw-8){
    panelEl.style.left='auto';
    panelEl.style.right='0';
    rect=panelEl.getBoundingClientRect();
  }
  if(rect.left<8){
    panelEl.style.left='0';
    panelEl.style.right='auto';
  }
}

function setAccumulationTop3PresetHelpOpen(open,focusPanel){
  var helpEl=document.getElementById('acc-top3-preset-help');
  var panelEl=document.getElementById('acc-top3-preset-help-panel');
  if(!helpEl||!panelEl)return;
  if(window._accTop3HelpCloseTimer){
    clearTimeout(window._accTop3HelpCloseTimer);
    window._accTop3HelpCloseTimer=0;
  }
  panelEl.setAttribute('aria-hidden',open?'false':'true');
  helpEl.setAttribute('aria-expanded',open?'true':'false');
  if(open){
    panelEl.style.display='block';
    positionAccumulationTop3PresetHelpPanel();
    panelEl.classList.remove('is-open');
    requestAnimationFrame(function(){panelEl.classList.add('is-open')});
    if(focusPanel)panelEl.focus();
  }else{
    panelEl.classList.remove('is-open');
    window._accTop3HelpCloseTimer=window.setTimeout(function(){
      if(!panelEl.classList.contains('is-open'))panelEl.style.display='none';
      window._accTop3HelpCloseTimer=0;
    },170);
  }
}

function closeAccumulationTop3PresetHelp(restoreFocus){
  var helpEl=document.getElementById('acc-top3-preset-help');
  var panelEl=document.getElementById('acc-top3-preset-help-panel');
  if(!helpEl||!panelEl)return;
  setAccumulationTop3PresetHelpOpen(false,false);
  if(restoreFocus)helpEl.focus();
}

function bindAccumulationTop3PresetHelpPopover(){
  var wrapEl=document.getElementById('acc-top3-preset-help-wrap');
  var panelEl=document.getElementById('acc-top3-preset-help-panel');
  if(!wrapEl||!panelEl)return;
  document.addEventListener('click',function(ev){
    if(!wrapEl.contains(ev.target))closeAccumulationTop3PresetHelp();
  });
  document.addEventListener('keydown',function(ev){
    if(ev.key!=='Escape')return;
    if(panelEl.style.display==='none'||!panelEl.style.display)return;
    ev.preventDefault();
    closeAccumulationTop3PresetHelp(true);
  });
  // Close the popover when the page or any scroll container moves.
  document.addEventListener('scroll',function(){
    if(panelEl.style.display==='none'||!panelEl.style.display)return;
    closeAccumulationTop3PresetHelp();
  },true);
  window.addEventListener('resize',positionAccumulationTop3PresetHelpPanel);
}
`;
}
