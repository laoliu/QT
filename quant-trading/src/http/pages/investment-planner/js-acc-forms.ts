/** Investment Planner — accumulation Top3 threshold form reading/writing/binding */
export function getAccFormsJs(): string {
  return `
function readAccumulationTop3ThresholdsFromForm(){
  return {
    pre_accum_days:Number(document.getElementById('acc-top3-pre-days').value)||10,
    pre_accum_count_min:Number(document.getElementById('acc-top3-pre-count').value)||2,
    pre_accum_volume_ratio:Number(document.getElementById('acc-top3-pre-vol').value)||1.5,
    rush_change_pct:Number(document.getElementById('acc-top3-rush-change').value)||5,
    rush_volume_ratio:Number(document.getElementById('acc-top3-rush-vol').value)||2,
    rush_turnover_pct:Number(document.getElementById('acc-top3-rush-turnover').value)||3,
    pre_surge_chip_min:Number(document.getElementById('acc-top3-anomaly-chip').value)||8,
    pre_surge_volume_price_min:Number(document.getElementById('acc-top3-anomaly-vp').value)||6,
    pre_surge_turnover_min:Number(document.getElementById('acc-top3-anomaly-turnover').value)||6
  };
}

function writeAccumulationTop3ThresholdsToForm(th){
  if(!th||typeof th!=='object')return;
  document.getElementById('acc-top3-pre-days').value=String(th.pre_accum_days||10);
  document.getElementById('acc-top3-pre-count').value=String(th.pre_accum_count_min||2);
  document.getElementById('acc-top3-pre-vol').value=String(th.pre_accum_volume_ratio||1.5);
  document.getElementById('acc-top3-rush-change').value=String(th.rush_change_pct||5);
  document.getElementById('acc-top3-rush-vol').value=String(th.rush_volume_ratio||2);
  document.getElementById('acc-top3-rush-turnover').value=String(th.rush_turnover_pct||3);
  document.getElementById('acc-top3-anomaly-chip').value=String(th.pre_surge_chip_min||8);
  document.getElementById('acc-top3-anomaly-vp').value=String(th.pre_surge_volume_price_min||6);
  document.getElementById('acc-top3-anomaly-turnover').value=String(th.pre_surge_turnover_min||6);
}

function persistAccumulationTop3Thresholds(th,presetKey){
  try{localStorage.setItem(ACC_TOP3_THRESHOLDS_KEY,JSON.stringify(th))}catch(e){}
  if(presetKey!=null){
    try{localStorage.setItem(ACC_TOP3_PRESET_KEY,String(presetKey))}catch(e){}
  }
}

function detectAccumulationTop3PresetByThresholds(th){
  var keys=Object.keys(ACC_TOP3_PRESETS);
  for(var i=0;i<keys.length;i++){
    var key=keys[i];
    var p=ACC_TOP3_PRESETS[key];
    if(
      Number(th.pre_accum_days)===Number(p.pre_accum_days) &&
      Number(th.pre_accum_count_min)===Number(p.pre_accum_count_min) &&
      Number(th.pre_accum_volume_ratio)===Number(p.pre_accum_volume_ratio) &&
      Number(th.rush_change_pct)===Number(p.rush_change_pct) &&
      Number(th.rush_volume_ratio)===Number(p.rush_volume_ratio) &&
      Number(th.rush_turnover_pct)===Number(p.rush_turnover_pct) &&
      Number(th.pre_surge_chip_min)===Number(p.pre_surge_chip_min) &&
      Number(th.pre_surge_volume_price_min)===Number(p.pre_surge_volume_price_min) &&
      Number(th.pre_surge_turnover_min)===Number(p.pre_surge_turnover_min)
    ) return key;
  }
  return 'custom';
}

function setAccumulationTop3PresetSelection(key){
  var el=document.getElementById('acc-top3-preset');
  if(!el)return;
  key=normalizeAccumulationTop3PresetKey(key);
  var selected=(ACC_TOP3_PRESETS[key]?key:'custom');
  el.value=selected;
  updateAccumulationTop3PresetHint(selected);
  updateAccumulationTop3PresetHelp(selected);
}

function updateAccumulationTop3PresetHint(key){
  var hintEl=document.getElementById('acc-top3-preset-hint');
  if(!hintEl)return;
  var text='当前: 自定义阈值';
  var color='var(--text-dim)';
  var bg='rgba(128,128,128,.12)';
  key=normalizeAccumulationTop3PresetKey(key);
  if(key==='stable'){
    text='当前: 稳健预设';
    color='var(--green)';
    bg='rgba(46,160,67,.12)';
  }else if(key==='swing'){
    text='当前: 波段预设';
    color='var(--accent)';
    bg='rgba(88,166,255,.12)';
  }else if(key==='shortline'){
    text='当前: 短线预设';
    color='#d29922';
    bg='rgba(210,153,34,.14)';
  }
  hintEl.innerHTML='<span style="display:inline-block;padding:2px 8px;border-radius:999px;color:'+color+';background:'+bg+';font-size:11px">'+esc(text)+'</span>';
}

function maybeRefreshAccumulationTop3AfterPresetChange(){
  var resultEl=document.getElementById('acc-top3-results');
  if(!resultEl||resultEl.style.display==='none')return;
  scanAccumulationTop3({silent:true});
}

function applyAccumulationTop3Preset(key,silent){
  key=normalizeAccumulationTop3PresetKey(key);
  var preset=ACC_TOP3_PRESETS[key];
  if(!preset){
    setAccumulationTop3PresetSelection('custom');
    return;
  }
  writeAccumulationTop3ThresholdsToForm(preset);
  persistAccumulationTop3Thresholds(preset,key);
  setAccumulationTop3PresetSelection(key);
  if(!silent)toast('✅ 已切换到'+accumulationTop3PresetLabel(key)+'预设');
}

function onAccumulationTop3PresetChange(){
  var el=document.getElementById('acc-top3-preset');
  var key=el?String(el.value||''):'custom';
  if(!ACC_TOP3_PRESETS[key]){
    var th=readAccumulationTop3ThresholdsFromForm();
    persistAccumulationTop3Thresholds(th,'custom');
    setAccumulationTop3PresetSelection('custom');
    maybeRefreshAccumulationTop3AfterPresetChange();
    return;
  }
  applyAccumulationTop3Preset(key,false);
  maybeRefreshAccumulationTop3AfterPresetChange();
}

function resetAccumulationTop3Preset(){
  applyAccumulationTop3Preset('swing',false);
  maybeRefreshAccumulationTop3AfterPresetChange();
}

function restoreAccumulationTop3Thresholds(){
  var savedPreset='';
  try{savedPreset=localStorage.getItem(ACC_TOP3_PRESET_KEY)||''}catch(e){}
  savedPreset=normalizeAccumulationTop3PresetKey(savedPreset);
  if(savedPreset&&ACC_TOP3_PRESETS[savedPreset]){
    applyAccumulationTop3Preset(savedPreset,true);
    return;
  }
  try{
    var raw=localStorage.getItem(ACC_TOP3_THRESHOLDS_KEY);
    if(!raw){
      applyAccumulationTop3Preset('swing',true);
      return;
    }
    var parsed=JSON.parse(raw);
    if(parsed&&typeof parsed==='object'){
      writeAccumulationTop3ThresholdsToForm(parsed);
      setAccumulationTop3PresetSelection(detectAccumulationTop3PresetByThresholds(parsed));
      return;
    }
  }catch(e){}
  applyAccumulationTop3Preset('swing',true);
}

function bindAccumulationTop3ThresholdInputs(){
  ['acc-top3-pre-days','acc-top3-pre-count','acc-top3-pre-vol','acc-top3-rush-change','acc-top3-rush-vol','acc-top3-rush-turnover','acc-top3-anomaly-chip','acc-top3-anomaly-vp','acc-top3-anomaly-turnover'].forEach(function(id){
    var el=document.getElementById(id);
    if(!el)return;
    el.addEventListener('change',function(){
      var th=readAccumulationTop3ThresholdsFromForm();
      persistAccumulationTop3Thresholds(th,'custom');
      setAccumulationTop3PresetSelection('custom');
      updateAccumulationTop3PresetHint('custom');
    });
  });
}

setInterval(function(){document.getElementById('clock').textContent=new Date().toLocaleString('zh-CN')},1000);

// Restore stock pool from localStorage
(function(){
  var saved=localStorage.getItem('quant-trading-rec-symbols');
  if(saved){document.getElementById('rec-symbols').value=saved}
  // Show stale-results banner when user manually changes the pool after an analysis
  var symInput=document.getElementById('rec-symbols');
  if(symInput){
    symInput.addEventListener('input',function(){
      var banner=document.getElementById('rec-pool-stale-banner');
      var results=document.getElementById('rec-results');
      if(!banner||!results||results.style.display==='none')return;
      var current=(symInput.value||'').trim();
      banner.style.display=(current!==_recSymbolsAtAnalysis)?'block':'none';
    });
  }
})();

(function(){
  restoreAccumulationTop3Thresholds();
  bindAccumulationTop3ThresholdInputs();
  bindAccumulationTop3PresetHelpPopover();
})();

(function(){
  ensureInstitutionDefaults();
})();

`;
}
