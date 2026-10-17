/** Investment Planner — institution threshold presets and custom preset management */
export function getInstPresetsJs(): string {
  return `
function readInstitutionThresholdsFromForm(){
  return {
    pre_accum_days:Number(document.getElementById('inst-pre-accum-days').value)||10,
    pre_accum_count_min:Number(document.getElementById('inst-pre-accum-count').value)||2,
    pre_accum_volume_ratio:Number(document.getElementById('inst-pre-accum-vol-ratio').value)||1.5,
    rush_change_pct:Number(document.getElementById('inst-rush-change-pct').value)||5,
    rush_volume_ratio:Number(document.getElementById('inst-rush-vol-ratio').value)||2,
    rush_turnover_pct:Number(document.getElementById('inst-rush-turnover').value)||3
  };
}

function writeInstitutionThresholdsToForm(th){
  document.getElementById('inst-pre-accum-days').value=String(th.pre_accum_days||10);
  document.getElementById('inst-pre-accum-count').value=String(th.pre_accum_count_min||2);
  document.getElementById('inst-pre-accum-vol-ratio').value=String(th.pre_accum_volume_ratio||1.5);
  document.getElementById('inst-rush-change-pct').value=String(th.rush_change_pct||5);
  document.getElementById('inst-rush-vol-ratio').value=String(th.rush_volume_ratio||2);
  document.getElementById('inst-rush-turnover').value=String(th.rush_turnover_pct||3);
}

function setInstitutionPresetLabel(key){
  var el=document.getElementById('inst-preset-label');
  if(!el)return;
  var map={conservative:'保守',balanced:'平衡',aggressive:'激进',custom:'自定义'};
  if(key&&String(key).indexOf('custom:')===0){
    var customName=String(key).slice(7);
    el.textContent='当前: 自定义('+customName+')';
    var selectEl=document.getElementById('inst-custom-preset');
    if(selectEl)selectEl.value=customName;
    return;
  }
  el.textContent='当前: '+(map[key]||'自定义');
  var selectEl=document.getElementById('inst-custom-preset');
  if(selectEl)selectEl.value='';
}

function persistInstitutionThresholds(th,presetKey){
  try{localStorage.setItem(INSTITUTION_THRESHOLDS_KEY,JSON.stringify(th))}catch(e){}
  try{localStorage.setItem(INSTITUTION_PRESET_KEY,presetKey||'custom')}catch(e){}
}

function loadInstitutionCustomPresets(){
  try{
    var raw=localStorage.getItem(INSTITUTION_CUSTOM_PRESETS_KEY);
    if(!raw){institutionCustomThresholdPresets={};return}
    var parsed=JSON.parse(raw);
    if(parsed&&typeof parsed==='object'&&!Array.isArray(parsed))institutionCustomThresholdPresets=parsed;
    else institutionCustomThresholdPresets={};
  }catch(e){institutionCustomThresholdPresets={}}
}

function persistInstitutionCustomPresets(){
  try{localStorage.setItem(INSTITUTION_CUSTOM_PRESETS_KEY,JSON.stringify(institutionCustomThresholdPresets||{}))}catch(e){}
}

function renderInstitutionCustomPresetOptions(selectedName){
  var selectEl=document.getElementById('inst-custom-preset');
  if(!selectEl)return;
  var names=Object.keys(institutionCustomThresholdPresets||{}).sort(function(a,b){return String(a).localeCompare(String(b),'zh-CN')});
  var options=['<option value="">选择自定义预设</option>'];
  for(var i=0;i<names.length;i++){
    var name=names[i];
    var selected=(selectedName&&selectedName===name)?' selected':'';
    options.push('<option value="'+esc(name)+'"'+selected+'>'+esc(name)+'</option>');
  }
  selectEl.innerHTML=options.join('');
}

function saveInstitutionCustomPreset(){
  var th=readInstitutionThresholdsFromForm();
  var name=(prompt('请输入预设名称（例如：短线跟庄）','')||'').trim();
  if(!name){toast('⚠️ 预设名称不能为空');return}
  if(name.length>24){toast('⚠️ 预设名称最多 24 个字符');return}
  if(INSTITUTION_THRESHOLD_PRESETS[name]){toast('⚠️ 名称与系统预设冲突，请换一个');return}
  if(institutionCustomThresholdPresets[name]&&!confirm('预设“'+name+'”已存在，是否覆盖？'))return;
  institutionCustomThresholdPresets[name]=th;
  persistInstitutionCustomPresets();
  renderInstitutionCustomPresetOptions(name);
  persistInstitutionThresholds(th,'custom:'+name);
  setInstitutionPresetLabel('custom:'+name);
  toast('✅ 已保存自定义预设：'+name);
}

function applyInstitutionCustomPreset(name,silent){
  var preset=institutionCustomThresholdPresets[name];
  if(!preset){
    if(!silent)toast('⚠️ 该自定义预设不存在');
    return;
  }
  writeInstitutionThresholdsToForm(preset);
  persistInstitutionThresholds(preset,'custom:'+name);
  setInstitutionPresetLabel('custom:'+name);
  if(!silent)toast('✅ 已应用自定义预设：'+name);
}

function applySelectedInstitutionCustomPreset(){
  var selectEl=document.getElementById('inst-custom-preset');
  var name=selectEl?String(selectEl.value||'').trim():'';
  if(!name){toast('⚠️ 请先选择一个自定义预设');return}
  applyInstitutionCustomPreset(name,false);
}

function deleteSelectedInstitutionCustomPreset(){
  var selectEl=document.getElementById('inst-custom-preset');
  var name=selectEl?String(selectEl.value||'').trim():'';
  if(!name){toast('⚠️ 请先选择一个自定义预设');return}
  if(!confirm('确定删除自定义预设“'+name+'”？'))return;
  delete institutionCustomThresholdPresets[name];
  persistInstitutionCustomPresets();
  renderInstitutionCustomPresetOptions('');
  var savedPreset='';
  try{savedPreset=localStorage.getItem(INSTITUTION_PRESET_KEY)||''}catch(e){}
  if(savedPreset==='custom:'+name){
    persistInstitutionThresholds(readInstitutionThresholdsFromForm(),'custom');
    setInstitutionPresetLabel('custom');
  }
  toast('✅ 已删除自定义预设：'+name);
}

function applyInstitutionThresholdPreset(presetKey,silent){
  var preset=INSTITUTION_THRESHOLD_PRESETS[presetKey];
  if(!preset){
    setInstitutionPresetLabel('custom');
    return;
  }
  writeInstitutionThresholdsToForm(preset);
  persistInstitutionThresholds(preset,presetKey);
  setInstitutionPresetLabel(presetKey);
  if(!silent)toast('✅ 已切换到'+(presetKey==='conservative'?'保守':presetKey==='aggressive'?'激进':'平衡')+'档位');
}

function restoreInstitutionThresholdPreset(){
  var savedPreset='';
  var savedThresholds=null;
  loadInstitutionCustomPresets();
  renderInstitutionCustomPresetOptions('');
  try{savedPreset=localStorage.getItem(INSTITUTION_PRESET_KEY)||''}catch(e){}
  try{
    var raw=localStorage.getItem(INSTITUTION_THRESHOLDS_KEY);
    if(raw)savedThresholds=JSON.parse(raw);
  }catch(e){}
  if(savedPreset&&INSTITUTION_THRESHOLD_PRESETS[savedPreset]){
    applyInstitutionThresholdPreset(savedPreset,true);
    return;
  }
  if(savedPreset&&String(savedPreset).indexOf('custom:')===0){
    var customName=String(savedPreset).slice(7);
    if(institutionCustomThresholdPresets[customName]){
      applyInstitutionCustomPreset(customName,true);
      return;
    }
  }
  if(savedThresholds&&typeof savedThresholds==='object'){
    writeInstitutionThresholdsToForm(savedThresholds);
    setInstitutionPresetLabel(detectInstitutionPresetByThresholds(savedThresholds));
    return;
  }
  applyInstitutionThresholdPreset('balanced',true);
}

function detectInstitutionPresetByThresholds(th){
  var keys=Object.keys(INSTITUTION_THRESHOLD_PRESETS);
  for(var i=0;i<keys.length;i++){
    var key=keys[i];
    var p=INSTITUTION_THRESHOLD_PRESETS[key];
    if(
      Number(th.pre_accum_days)===Number(p.pre_accum_days) &&
      Number(th.pre_accum_count_min)===Number(p.pre_accum_count_min) &&
      Number(th.pre_accum_volume_ratio)===Number(p.pre_accum_volume_ratio) &&
      Number(th.rush_change_pct)===Number(p.rush_change_pct) &&
      Number(th.rush_volume_ratio)===Number(p.rush_volume_ratio) &&
      Number(th.rush_turnover_pct)===Number(p.rush_turnover_pct)
    ) return key;
  }
  var customKeys=Object.keys(institutionCustomThresholdPresets||{});
  for(var j=0;j<customKeys.length;j++){
    var customName=customKeys[j];
    var cp=institutionCustomThresholdPresets[customName]||{};
    if(
      Number(th.pre_accum_days)===Number(cp.pre_accum_days) &&
      Number(th.pre_accum_count_min)===Number(cp.pre_accum_count_min) &&
      Number(th.pre_accum_volume_ratio)===Number(cp.pre_accum_volume_ratio) &&
      Number(th.rush_change_pct)===Number(cp.rush_change_pct) &&
      Number(th.rush_volume_ratio)===Number(cp.rush_volume_ratio) &&
      Number(th.rush_turnover_pct)===Number(cp.rush_turnover_pct)
    ) return 'custom:'+customName;
  }
  return 'custom';
}

function ensureInstitutionDefaults(){
  var dateEl=document.getElementById('inst-date');
  if(!dateEl)return;
  if(!dateEl.value){
    var now=new Date();
    if(now.getDay()===6)now.setDate(now.getDate()-1);
    else if(now.getDay()===0)now.setDate(now.getDate()-2);
    dateEl.value=now.toISOString().slice(0,10);
  }
  var saved=localStorage.getItem('quant-trading-inst-symbols');
  if(saved&&!document.getElementById('inst-symbols').value){
    document.getElementById('inst-symbols').value=saved;
  }
  if(!_instPresetInitialized){
    restoreInstitutionThresholdPreset();
    _instPresetInitialized=true;
  }
}

`;
}
