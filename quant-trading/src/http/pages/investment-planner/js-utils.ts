/** Investment Planner — utility functions (esc, num, pct, api, toast) */
export function getUtilsJs(): string {
  return `
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function escAttrJsSingleQuoted(s){return esc(s).replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'")}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function numInt(v){return v!=null?Number(v).toLocaleString('zh-CN'):'-'}
function pct(v){return v!=null?(v>=0?'+':'')+v.toFixed(2)+'%':'-'}
function pctColor(v){return v>0?'var(--green)':v<0?'var(--red)':'var(--text)'}
function kpiClickable(val,label,onclick){return '<div class="kpi" style="cursor:pointer" onclick="'+onclick+'"><div class="kpi-val">'+val+'</div><div class="kpi-label">'+label+'</div></div>'}
function kpiHtml(val,label){return '<div class="kpi"><div class="kpi-val">'+val+'</div><div class="kpi-label">'+label+'</div></div>'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}

function api(path,opts){
  opts=opts||{};
  var timeoutMs=opts._timeout||15000;
  var headers=opts.body?{'Content-Type':'application/json'}:{};
  if(opts.headers)Object.keys(opts.headers).forEach(function(k){headers[k]=opts.headers[k]});
  var fetchOpts=Object.assign({},opts,{headers:headers});
  delete fetchOpts._timeout;
  function doFetch(){
    var controller=typeof AbortController!=='undefined'?new AbortController():null;
    var timer=controller?setTimeout(function(){controller.abort()},timeoutMs):null;
    var fo=controller?Object.assign({},fetchOpts,{signal:controller.signal}):fetchOpts;
    return fetch(B+path,fo).then(function(r){
      if(timer)clearTimeout(timer);
      if(!r.ok) return r.json().catch(function(){return {}}).then(function(body){
        throw new Error(body.error||r.status+' '+r.statusText);
      });
      return r.json();
    }).catch(function(e){
      if(timer)clearTimeout(timer);
      if(e&&e.name==='AbortError')throw new Error('请求超时 ('+Math.round(timeoutMs/1000)+'s)');
      throw e;
    });
  }
  return doFetch().catch(function(e){
    if(e instanceof TypeError&&/fetch/i.test(e.message)){return doFetch()}
    throw e;
  }).catch(function(e){toast('❌ '+e.message);throw e});
}
`;
}
