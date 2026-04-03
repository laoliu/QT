/**
 * Shared theme, CSS reset, and browser-side JS utilities for all quant-trading HTML pages.
 *
 * Each self-contained SPA page embeds these strings into its template literal so
 * theme colors, typography, and helper functions stay consistent across pages.
 */

// ── CSS ──────────────────────────────────────────────────────────

/** CSS custom properties (dark theme palette) used by all pages. */
export const THEME_CSS_VARS = `:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}`;

/** Minimal CSS reset + body defaults shared by every page. */
export const BASE_CSS_RESET = `*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}`;

/** Common header bar CSS. */
export const HEADER_CSS = `header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
.header-right{display:flex;align-items:center;gap:12px;font-size:13px}
.header-right a{color:var(--accent);text-decoration:none}`;

/** Toast notification CSS (requires <div class="toast" id="toast"></div>). */
export const TOAST_CSS = `.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0);pointer-events:auto}`;

/** KPI card CSS (used by planner, admin, etc.). */
export const KPI_CSS = `.kpi{background:var(--bg3);border:1px solid var(--border);border-radius:8px;padding:12px 16px;text-align:center;min-width:90px}
.kpi-val{font-size:18px;font-weight:700}
.kpi-label{font-size:11px;color:var(--text-dim);margin-top:4px}`;

// ── JS helpers (browser-side, returned as strings) ───────────────

/** HTML entity escaping + number/percentage formatting helpers.
 *  These are embedded inside a `<script>` tag in each page. */
export const SHARED_JS_HELPERS = `
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function escAttrJsSingleQuoted(s){return esc(s).replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'")}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function numInt(v){return v!=null?Number(v).toLocaleString('zh-CN'):'-'}
function pct(v){return v!=null?(v>=0?'+':'')+v.toFixed(2)+'%':'-'}
function pctColor(v){return v>0?'var(--green)':v<0?'var(--red)':'var(--text)'}
function kpiClickable(val,label,onclick){return '<div class="kpi" style="cursor:pointer" onclick="'+onclick+'"><div class="kpi-val">'+val+'</div><div class="kpi-label">'+label+'</div></div>'}
function kpiHtml(val,label){return '<div class="kpi"><div class="kpi-val">'+val+'</div><div class="kpi-label">'+label+'</div></div>'}
function toast(msg,ms){ms=ms||2500;var el=document.getElementById('toast');if(!el)return;el.textContent=msg;el.classList.add('show');setTimeout(function(){el.classList.remove('show')},ms)}
`.trim();

/**
 * API fetch wrapper with timeout + retry (browser-side JS string).
 * Expects a global `B` (base URL) to be defined before this runs.
 */
export const SHARED_JS_API = `
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
`.trim();
