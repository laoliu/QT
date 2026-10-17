/**
 * Strategy Lab Page — self-contained HTML SPA for AI-driven strategy
 * combination, exploration, and backtesting.
 *
 * Tabs:
 *  1. Compose  — create strategy combinations with visual builder
 *  2. Explore  — auto-generate candidate combinations and batch backtest
 *  3. History  — browse saved compositions and compare results
 */

/** Return the full HTML for the Strategy Lab page. */
export function getStrategyLabPageHtml(
  baseUrl: string,
  apiToken?: string,
  initialStrategies?: Array<{ id: string; name: string }>,
): string {
  const b = baseUrl.replace(/\/$/, "");
  // Safely embed initial strategies as a JSON data block (not inline JS).
  // <script type="application/json"> is never executed — JS reads .textContent.
  const safeStrategies = (initialStrategies ?? []).map((s) => ({
    id: s.id,
    name: s.name.replace(/[\r\n]+/g, " ").trim(),
  }));
  // Escape </ to prevent premature </script> closing
  const strategiesDataBlock = JSON.stringify(safeStrategies).replace(/<\//g, "<\\/");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 策略实验室</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:10px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:18px;font-weight:600}
nav{display:flex;gap:4px;background:var(--bg2);padding:0 24px;border-bottom:1px solid var(--border)}
nav button{background:none;border:none;color:var(--text-dim);padding:10px 16px;font-size:14px;cursor:pointer;border-bottom:2px solid transparent;transition:all .2s}
nav button:hover{color:var(--text)}
nav button.active{color:var(--accent);border-bottom-color:var(--accent)}
.container{max-width:1400px;margin:0 auto;padding:20px 24px}
.tab-content{display:none}
.tab-content.active{display:block}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px}
.card h3{margin-bottom:12px;font-size:15px}
.grid{display:grid;gap:16px}
.grid-2{grid-template-columns:repeat(auto-fill,minmax(400px,1fr))}
.grid-3{grid-template-columns:repeat(auto-fill,minmax(300px,1fr))}
.btn{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;border-radius:6px;font-size:13px;cursor:pointer;border:1px solid var(--border);background:var(--bg3);color:var(--text);transition:all .15s}
.btn:hover{background:var(--border)}
.btn-primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.btn-primary:hover{opacity:.85}
.btn-danger{background:var(--red);color:#fff;border-color:var(--red)}
.btn-sm{padding:4px 10px;font-size:12px}
label{display:block;font-size:13px;color:var(--text-dim);margin-bottom:4px}
select,input[type=text],input[type=number]{width:100%;padding:6px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px}
select:focus,input:focus{outline:none;border-color:var(--accent)}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.tag-voting{background:#1f3a5f;color:#58a6ff}
.tag-weighted{background:#2a1f3f;color:#bc8cff}
.tag-gated{background:#1a3f2a;color:#3fb950}
.tag-cascade{background:#3f2a1a;color:#db6d28}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{color:var(--text-dim);font-weight:500;font-size:12px}
.metric-good{color:var(--green)}
.metric-bad{color:var(--red)}
.metric-ok{color:var(--yellow)}
.empty{text-align:center;padding:40px;color:var(--text-dim)}
.loading{text-align:center;padding:20px;color:var(--text-dim)}
.sub-strategy-list{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0}
.sub-strategy-item{background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:8px 12px;font-size:12px;display:flex;align-items:center;gap:8px}
.sub-strategy-item .remove{cursor:pointer;color:var(--red);font-size:14px}
.result-rank{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;border-radius:50%;font-size:12px;font-weight:700}
.rank-1{background:#3fb950;color:#000}
.rank-2{background:#58a6ff;color:#000}
.rank-3{background:#d29922;color:#000}
.form-row{display:flex;gap:12px;align-items:flex-end;margin-bottom:12px;flex-wrap:wrap}
.form-row > div{flex:1;min-width:150px}
#explore-progress{margin-top:12px}
.back-link{color:var(--accent);text-decoration:none;font-size:13px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
</style>
</head>
<body>
<header>
  <h1>\u{1f9ea} Quant Trading \u7b56\u7565\u5b9e\u9a8c\u5ba4</h1>
  <div style="display:flex;gap:12px;align-items:center">
    <a href="${b}/quant/admin/ui" class="back-link">\u2190 \u8fd4\u56de\u7ba1\u7406\u9762\u677f</a>
    <span id="clock" style="font-size:12px;color:var(--text-dim)"></span>
  </div>
</header>
<nav>
  <button class="active" data-tab="compose">\u{1f3d7}\ufe0f \u7ec4\u5408</button>
  <button data-tab="explore">\u{1f52d} \u63a2\u7d22</button>
  <button data-tab="history">\u{1f4cb} \u7b56\u7565\u7ba1\u7406</button>
  <button data-tab="batch">\u26a1 \u6279\u91cf\u56de\u6d4b</button>
</nav>
<div class="container">

<!-- ═══ Compose Tab ═══ -->
<div id="tab-compose" class="tab-content active">
  <div class="card">
    <h3>\u{1f3d7}\ufe0f \u521b\u5efa\u7b56\u7565\u7ec4\u5408</h3>
    <div class="form-row">
      <div><label>\u7ec4\u5408\u540d\u79f0</label><input type="text" id="comp-name" placeholder="\u6211\u7684\u7ec4\u5408\u7b56\u7565"></div>
      <div><label>\u7ec4\u5408\u65b9\u6cd5</label>
        <select id="comp-method">
          <option value="voting">\u6295\u7968 (Voting) \u2014 \u591a\u6570\u4e00\u81f4\u65f6\u4ea4\u6613</option>
          <option value="weighted-score">\u52a0\u6743 (Weighted) \u2014 \u52a0\u6743\u8bc4\u5206\u8d85\u9608\u503c</option>
          <option value="gated">\u95e8\u63a7 (Gated) \u2014 \u4e3b\u7b56\u7565+\u8fc7\u6ee4\u5668\u786e\u8ba4</option>
          <option value="cascade">\u7ea7\u8054 (Cascade) \u2014 \u9010\u7ea7\u8fc7\u6ee4</option>
        </select>
      </div>
      <div id="comp-min-votes-wrap"><label>\u6700\u5c11\u6295\u7968\u6570</label><input type="number" id="comp-min-votes" value="2" min="1" max="10"></div>
    </div>
    <div class="form-row">
      <div style="flex:3"><label>\u6dfb\u52a0\u5b50\u7b56\u7565</label>
        <div style="display:flex;gap:8px">
          <select id="comp-strategy-select" style="flex:1"></select>
          <input type="number" id="comp-weight" value="1" min="1" max="10" style="width:80px" placeholder="\u6743\u91cd">
          <select id="comp-role" style="width:100px">
            <option value="primary">\u4e3b\u7b56\u7565</option>
            <option value="filter">\u8fc7\u6ee4\u5668</option>
          </select>
          <button class="btn" onclick="addSubStrategy()">\u2795 \u6dfb\u52a0</button>
        </div>
      </div>
    </div>
    <div style="margin-top:4px;display:flex;gap:8px;flex-wrap:wrap">
      <button class="btn btn-sm" onclick="openBatchAdd()" title="\u4ece\u5217\u8868\u4e2d\u591a\u9009\u7b56\u7565\u4e00\u6b21\u6027\u6dfb\u52a0">\u{1f4e6} \u6279\u91cf\u6dfb\u52a0</button>
      <button class="btn btn-sm" onclick="smartRecommend()" title="\u6839\u636e\u7b56\u7565\u7c7b\u578b\u81ea\u52a8\u63a8\u8350\u4e92\u8865\u7ec4\u5408">\u{1f9e0} \u667a\u80fd\u63a8\u8350</button>
      <button class="btn btn-sm" onclick="clearSubStrategies()" title="\u6e05\u7a7a\u5df2\u6dfb\u52a0\u7684\u5b50\u7b56\u7565">\u{1f5d1}\ufe0f \u6e05\u7a7a</button>
    </div>
    <!-- Batch-add modal overlay -->
    <div id="batch-add-overlay" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.6);z-index:1000;display:none;align-items:center;justify-content:center">
      <div style="background:var(--bg2);border:1px solid var(--border);border-radius:10px;width:640px;max-height:80vh;display:flex;flex-direction:column;padding:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
          <h3 style="font-size:15px">\u{1f4e6} \u6279\u91cf\u6dfb\u52a0\u5b50\u7b56\u7565</h3>
          <div style="display:flex;gap:8px;align-items:center">
            <input type="text" id="batch-add-search" placeholder="\u641c\u7d22\u7b56\u7565..." style="width:200px" oninput="filterBatchList()">
            <button class="btn btn-sm" onclick="toggleBatchAll()">\u5168\u9009/\u53d6\u6d88</button>
            <button class="btn btn-sm" onclick="closeBatchAdd()">\u2716</button>
          </div>
        </div>
        <div id="batch-add-list" style="flex:1;overflow-y:auto;max-height:50vh;border:1px solid var(--border);border-radius:6px;padding:8px"></div>
        <div style="margin-top:12px;display:flex;justify-content:space-between;align-items:center">
          <span id="batch-add-count" style="font-size:12px;color:var(--text-dim)">\u5df2\u9009 0 \u4e2a</span>
          <div style="display:flex;gap:8px;align-items:center">
            <label style="font-size:12px;margin:0">\u6743\u91cd:</label>
            <input type="number" id="batch-add-weight" value="1" min="1" max="10" style="width:60px">
            <select id="batch-add-role" style="width:90px">
              <option value="primary">\u4e3b\u7b56\u7565</option>
              <option value="filter">\u8fc7\u6ee4\u5668</option>
            </select>
            <button class="btn btn-primary btn-sm" onclick="confirmBatchAdd()">\u2705 \u786e\u8ba4\u6dfb\u52a0</button>
          </div>
        </div>
      </div>
    </div>
    <div id="comp-sub-list" class="sub-strategy-list"></div>
    <div style="margin-top:12px">
      <button class="btn btn-primary" onclick="createComposition()">\u{1f680} \u521b\u5efa\u7ec4\u5408\u7b56\u7565</button>
    </div>
    <div id="comp-result" style="margin-top:12px"></div>
  </div>
</div>

<!-- ═══ Explore Tab ═══ -->
<div id="tab-explore" class="tab-content">
  <div class="card">
    <h3>\u{1f52d} AI \u7b56\u7565\u63a2\u7d22</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">\u81ea\u52a8\u751f\u6210\u591a\u4e2a\u5019\u9009\u7b56\u7565\u7ec4\u5408\uff0c\u6279\u91cf\u56de\u6d4b\uff0c\u6309\u7efc\u5408\u8bc4\u5206\u6392\u5e8f\u627e\u51fa\u6700\u4f18\u7ec4\u5408\u3002</p>
    <div class="form-row">
      <div><label>\u6807\u7684\u4ee3\u7801</label><input type="text" id="explore-symbols" value="000300" placeholder="\u9017\u53f7\u5206\u9694"></div>
      <div><label>\u5f00\u59cb\u65e5\u671f</label><input type="text" id="explore-start" placeholder="YYYY-MM-DD"></div>
      <div><label>\u7ed3\u675f\u65e5\u671f</label><input type="text" id="explore-end" placeholder="YYYY-MM-DD"></div>
    </div>
    <div class="form-row">
      <div><label>\u5019\u9009\u6570\u91cf</label><input type="number" id="explore-count" value="10" min="3" max="30"></div>
      <div><label>\u6bcf\u7ec4\u6700\u5c11\u7b56\u7565</label><input type="number" id="explore-min" value="2" min="2" max="5"></div>
      <div><label>\u6bcf\u7ec4\u6700\u591a\u7b56\u7565</label><input type="number" id="explore-max" value="4" min="2" max="6"></div>
      <div><label>\u8fd4\u56de Top K</label><input type="number" id="explore-topk" value="5" min="1" max="10"></div>
    </div>
    <button class="btn btn-primary" id="btn-explore" onclick="runExploration()">\u{1f680} \u5f00\u59cb\u63a2\u7d22</button>
    <div id="explore-progress"></div>
  </div>
  <div id="explore-results"></div>
  <div class="card" style="margin-top:16px">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h3>\u{1f4c1} \u63a2\u7d22\u5386\u53f2</h3>
      <button class="btn btn-sm" onclick="loadExploreHistory()">\u{1f504} \u5237\u65b0</button>
    </div>
    <div id="explore-history-list"><div class="empty">\u52a0\u8f7d\u4e2d...</div></div>
  </div>
</div>

<!-- ═══ Strategy Management Tab ═══ -->
<div id="tab-history" class="tab-content">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h3>\u{1f4d0} \u5185\u7f6e\u57fa\u7840\u7b56\u7565</h3>
      <span id="builtin-count" style="font-size:12px;color:var(--text-dim)"></span>
    </div>
    <div id="builtin-strategy-list"><div class="loading">\u52a0\u8f7d\u4e2d...</div></div>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
      <h3>\u2b50 \u7528\u6237\u6dfb\u52a0\u7684\u7b56\u7565\u7ec4\u5408</h3>
      <div style="display:flex;gap:8px;align-items:center">
        <span id="user-comp-count" style="font-size:12px;color:var(--text-dim)"></span>
        <button class="btn btn-sm" onclick="loadStrategyManagement()">\u{1f504} \u5237\u65b0</button>
      </div>
    </div>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">\u901a\u8fc7\u63a2\u7d22\u6536\u85cf\u6216\u624b\u52a8\u7ec4\u5408\u6dfb\u52a0\u7684\u7b56\u7565\uff0c\u5df2\u6ce8\u518c\u5230\u7b56\u7565\u5e93\uff0c\u53ef\u76f4\u63a5\u7528\u4e8e\u56de\u6d4b\u548c\u5b9e\u9645\u4ea4\u6613\u3002\u5220\u9664\u540e\u5c06\u4ece\u7b56\u7565\u5e93\u4e2d\u79fb\u9664\u3002</p>
    <div id="history-list"></div>
  </div>
</div>

<!-- ═══ Batch Backtest Tab ═══ -->
<div id="tab-batch" class="tab-content">
  <div class="card">
    <h3>\u26a1 \u6279\u91cf\u56de\u6d4b\u5bf9\u6bd4</h3>
    <p style="color:var(--text-dim);font-size:13px;margin-bottom:12px">\u9009\u62e9\u591a\u4e2a\u7b56\u7565\uff08\u5305\u62ec\u7ec4\u5408\u7b56\u7565\uff09\u540c\u65f6\u56de\u6d4b\u5e76\u6392\u540d\u3002</p>
    <div class="form-row">
      <div><label>\u6807\u7684\u4ee3\u7801</label><input type="text" id="batch-symbols" value="000300" placeholder="\u9017\u53f7\u5206\u9694"></div>
      <div><label>\u5f00\u59cb\u65e5\u671f</label><input type="text" id="batch-start" placeholder="YYYY-MM-DD"></div>
      <div><label>\u7ed3\u675f\u65e5\u671f</label><input type="text" id="batch-end" placeholder="YYYY-MM-DD"></div>
    </div>
    <div class="form-row">
      <div style="flex:3"><label>\u7b56\u7565 (\u591a\u9009)</label>
        <input type="text" id="batch-search" placeholder="\u{1f50d} \u641c\u7d22\u7b56\u7565\u540d\u79f0\u6216ID..." oninput="filterBatchStrategies()" style="margin-bottom:6px;width:100%">
        <div style="display:flex;gap:6px;margin-bottom:6px;font-size:12px">
          <button class="btn" onclick="batchSelectAll()" style="padding:2px 8px">\u5168\u9009</button>
          <button class="btn" onclick="batchSelectNone()" style="padding:2px 8px">\u53d6\u6d88\u5168\u9009</button>
          <span id="batch-count" style="color:var(--text-dim);line-height:24px"></span>
        </div>
        <select id="batch-strategies" multiple style="height:320px"></select>
      </div>
    </div>
    <button class="btn btn-primary" id="btn-batch" onclick="runBatchBacktest()">\u{1f680} \u8fd0\u884c\u6279\u91cf\u56de\u6d4b</button>
    <div id="batch-results" style="margin-top:16px"></div>
    <div style="margin-top:24px">
      <h3>\u{1f4c2} \u5386\u53f2\u8bb0\u5f55 <button class="btn" onclick="loadBatchHistory()" style="font-size:12px;padding:2px 8px">\u5237\u65b0</button></h3>
      <div id="batch-history" style="margin-top:8px"><div class="empty">\u70b9\u51fb\u5237\u65b0\u52a0\u8f7d\u5386\u53f2\u8bb0\u5f55</div></div>
    </div>
  </div>
</div>

</div><!-- .container -->

<script type="application/json" id="initial-strategies-data">${strategiesDataBlock}</script>

<script>
var _apiToken = ${JSON.stringify(apiToken ?? "")};
var _baseUrl = ${JSON.stringify(b)};
var _subStrategies = [];
var _lastBatchRanked = [];
var _allStrategies = (function(){try{var el=document.getElementById('initial-strategies-data');return el?JSON.parse(el.textContent||'[]'):[]}catch(e){return[]}})();

function api(path, opts){
  var headers = {'Content-Type':'application/json'};
  if(_apiToken) headers['Authorization'] = 'Bearer '+_apiToken;
  return fetch(_baseUrl+path,Object.assign({headers},opts||{})).then(function(r){return r.json()});
}

function renderStrategyOptions(strategies){
  var sel = document.getElementById('comp-strategy-select');
  sel.innerHTML = '';
  if(!Array.isArray(strategies) || strategies.length===0){
    var emptyOpt = document.createElement('option');
    emptyOpt.value = '';
    emptyOpt.textContent = '\u6682\u65e0\u53ef\u7528\u7b56\u7565';
    sel.appendChild(emptyOpt);
    return;
  }
  strategies.forEach(function(s){
    var opt = document.createElement('option');
    opt.value = s.id;
    opt.textContent = s.name + ' (' + s.id + ')';
    sel.appendChild(opt);
  });
}

function switchTab(name, triggerEl){
  document.querySelectorAll('.tab-content').forEach(function(el){el.classList.remove('active')});
  document.querySelectorAll('nav button').forEach(function(el){el.classList.remove('active')});
  var tab = document.getElementById('tab-'+name);
  if(tab) tab.classList.add('active');
  var activeBtn = triggerEl || document.querySelector('nav button[data-tab="'+name+'"]');
  if(activeBtn) activeBtn.classList.add('active');
  if(name==='explore') loadExploreHistory();
  if(name==='history') loadStrategyManagement();
  if(name==='batch') loadBatchStrategies();
  if(name==='batch') loadBatchHistory();
}

document.querySelectorAll('nav button[data-tab]').forEach(function(btn){
  btn.addEventListener('click', function(){
    switchTab(btn.getAttribute('data-tab') || 'compose', btn);
  });
});

function updateClock(){
  document.getElementById('clock').textContent = new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'});
}
setInterval(updateClock,1000);
updateClock();

// ── Load available strategies ──
function loadStrategies(){
  return api('/quant/admin/api/strategies').then(function(data){
    _allStrategies = data.builtin || data.strategies || data || [];
    // Also fetch user compositions and merge them
    return api('/quant/admin/api/compositions').then(function(compData){
      var compositions = compData.compositions || [];
      compositions.forEach(function(c){
        if(!_allStrategies.find(function(s){return s.id===c.id})){
          _allStrategies.push({id: c.id, name: '\u2b50 '+c.name});
        }
      });
      renderStrategyOptions(_allStrategies);
      return _allStrategies;
    }).catch(function(){
      renderStrategyOptions(_allStrategies);
      return _allStrategies;
    });
  }).catch(function(){
    _allStrategies = [];
    renderStrategyOptions([]);
    return _allStrategies;
  });
}
loadStrategies();
renderStrategyOptions(_allStrategies);

// ── Compose Tab ──
function addSubStrategy(){
  var id = document.getElementById('comp-strategy-select').value;
  var weight = parseInt(document.getElementById('comp-weight').value) || 1;
  var role = document.getElementById('comp-role').value;
  if(!id) return;
  if(_subStrategies.find(function(s){return s.strategyId===id})){
    alert('\u8be5\u7b56\u7565\u5df2\u6dfb\u52a0');
    return;
  }
  _subStrategies.push({strategyId:id, weight:weight, role:role});
  renderSubStrategies();
}

function removeSubStrategy(idx){
  _subStrategies.splice(idx,1);
  renderSubStrategies();
}

function renderSubStrategies(){
  var el = document.getElementById('comp-sub-list');
  el.innerHTML = _subStrategies.map(function(s,i){
    var methodTag = s.role==='filter'?'<span class="tag tag-gated">\u8fc7\u6ee4</span>':'<span class="tag tag-voting">\u4e3b</span>';
    return '<div class="sub-strategy-item">'+methodTag+' <strong>'+s.strategyId+'</strong> \u6743\u91cd:'+s.weight+' <span class="remove" onclick="removeSubStrategy('+i+')">\u2716</span></div>';
  }).join('');
}

function updateComposeMethodFields(){
  var methodEl = document.getElementById('comp-method');
  var minVotesWrap = document.getElementById('comp-min-votes-wrap');
  var minVotesInput = document.getElementById('comp-min-votes');
  if(!methodEl || !minVotesWrap || !minVotesInput) return;
  var isVoting = methodEl.value === 'voting';
  minVotesWrap.style.display = isVoting ? '' : 'none';
  minVotesInput.disabled = !isVoting;
}

// ── Batch Add Sub-strategies ──
function openBatchAdd(){
  var overlay = document.getElementById('batch-add-overlay');
  overlay.style.display = 'flex';
  var list = document.getElementById('batch-add-list');
  var existingIds = {};
  _subStrategies.forEach(function(s){ existingIds[s.strategyId] = true; });
  list.innerHTML = _allStrategies.map(function(s){
    var checked = existingIds[s.id] ? ' checked disabled' : '';
    return '<label class="batch-add-item" style="display:flex;align-items:center;gap:8px;padding:4px 6px;cursor:pointer;border-radius:4px" data-id="'+s.id+'" data-name="'+(s.name||'').toLowerCase()+'" data-sid="'+s.id.toLowerCase()+'">'
      + '<input type="checkbox" class="batch-cb" value="'+s.id+'"'+checked+' onchange="updateBatchCount()">'
      + '<span style="font-size:13px">'+s.name+' <span style="color:var(--text-dim);font-size:11px">('+s.id+')</span></span>'
      + '</label>';
  }).join('');
  updateBatchCount();
  document.getElementById('batch-add-search').value = '';
}

function closeBatchAdd(){
  document.getElementById('batch-add-overlay').style.display = 'none';
}

function filterBatchList(){
  var q = (document.getElementById('batch-add-search').value || '').toLowerCase();
  document.querySelectorAll('.batch-add-item').forEach(function(el){
    var name = el.getAttribute('data-name') || '';
    var sid = el.getAttribute('data-sid') || '';
    el.style.display = (name.indexOf(q) >= 0 || sid.indexOf(q) >= 0) ? '' : 'none';
  });
}

function toggleBatchAll(){
  var cbs = document.querySelectorAll('.batch-cb:not(:disabled)');
  var allChecked = true;
  cbs.forEach(function(cb){ if(!cb.checked) allChecked = false; });
  cbs.forEach(function(cb){
    if(cb.closest('.batch-add-item').style.display !== 'none'){
      cb.checked = !allChecked;
    }
  });
  updateBatchCount();
}

function updateBatchCount(){
  var count = 0;
  document.querySelectorAll('.batch-cb:not(:disabled)').forEach(function(cb){ if(cb.checked) count++; });
  document.getElementById('batch-add-count').textContent = '\u5df2\u9009 '+count+' \u4e2a';
}

function confirmBatchAdd(){
  var weight = parseInt(document.getElementById('batch-add-weight').value) || 1;
  var role = document.getElementById('batch-add-role').value;
  var added = 0;
  document.querySelectorAll('.batch-cb:not(:disabled)').forEach(function(cb){
    if(cb.checked){
      var id = cb.value;
      if(!_subStrategies.find(function(s){return s.strategyId===id})){
        _subStrategies.push({strategyId:id, weight:weight, role:role});
        added++;
      }
    }
  });
  renderSubStrategies();
  closeBatchAdd();
  if(added > 0) alert('\u5df2\u6dfb\u52a0 '+added+' \u4e2a\u5b50\u7b56\u7565');
}

function clearSubStrategies(){
  if(_subStrategies.length === 0) return;
  if(!confirm('\u786e\u8ba4\u6e05\u7a7a\u5168\u90e8 '+_subStrategies.length+' \u4e2a\u5b50\u7b56\u7565\uff1f')) return;
  _subStrategies = [];
  renderSubStrategies();
}

// ── Smart Recommend ──
var _recommendPresets = [
  {
    label: '\u8d8b\u52bf\u8ddf\u8e2a\u7ec4\u5408',
    desc: '\u591a\u79cd\u5747\u7ebf\u4ea4\u53c9+\u52a8\u91cf+MACD\u4fe1\u53f7\u878d\u5408',
    keywords: ['ma_crossover','dual-ma-crossover','macd','momentum','trend','ema','moving_average','triple_ma']
  },
  {
    label: '\u6446\u52a8\u8fc7\u6ee4\u7ec4\u5408',
    desc: 'RSI+KDJ+\u5e03\u6797\u5e26\u7b49\u6446\u52a8\u6307\u6807\u4ea4\u53c9\u9a8c\u8bc1',
    keywords: ['rsi','kdj','bollinger','stochastic','oscillat','oversold','overbought','williams','cci']
  },
  {
    label: '\u91cf\u4ef7\u5171\u632f\u7ec4\u5408',
    desc: '\u6210\u4ea4\u91cf\u7a81\u7834+\u4ef7\u683c\u52a8\u91cf+VWAP\u591a\u7ef4\u786e\u8ba4',
    keywords: ['volume','vwap','obv','money_flow','turnover','vol_breakout','accumulation']
  },
  {
    label: '\u591a\u56e0\u5b50\u878d\u5408',
    desc: '\u6df7\u5408\u8d8b\u52bf+\u6446\u52a8+\u91cf\u4ef7\u591a\u7c7b\u578b\u4fe1\u53f7',
    keywords: ['ma_crossover','rsi','volume','macd','bollinger','momentum','kdj','trend']
  }
];

function smartRecommend(){
  var html = '<div style="margin-bottom:12px;font-size:13px;color:var(--text-dim)">\u9009\u62e9\u4e00\u79cd\u63a8\u8350\u6a21\u5f0f\uff0c\u81ea\u52a8\u5339\u914d\u5e76\u6dfb\u52a0\u4e92\u8865\u7b56\u7565\uff1a</div>';
  _recommendPresets.forEach(function(p,i){
    html += '<div style="padding:10px;margin-bottom:8px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;cursor:pointer" onclick="applyRecommend('+i+')" onmouseover="this.style.borderColor=\\'var(--accent)\\'" onmouseout="this.style.borderColor=\\'var(--border)\\'">'
      + '<strong>'+p.label+'</strong><div style="font-size:12px;color:var(--text-dim);margin-top:2px">'+p.desc+'</div></div>';
  });
  html += '<div style="padding:10px;margin-bottom:8px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;cursor:pointer" onclick="applyRecommendRandom()" onmouseover="this.style.borderColor=\\'var(--accent)\\'" onmouseout="this.style.borderColor=\\'var(--border)\\'">'
    + '<strong>\u{1f3b2} \u968f\u673a\u7ec4\u5408</strong><div style="font-size:12px;color:var(--text-dim);margin-top:2px">\u968f\u673a\u9009\u53d6 3-5 \u4e2a\u4e0d\u540c\u7b56\u7565\uff0c\u8bd5\u8bd5\u8fd0\u6c14</div></div>';
  var overlay = document.getElementById('batch-add-overlay');
  document.getElementById('batch-add-list').innerHTML = html;
  document.getElementById('batch-add-count').textContent = '\u9009\u62e9\u63a8\u8350\u6a21\u5f0f';
  overlay.querySelector('h3').textContent = '\u{1f9e0} \u667a\u80fd\u63a8\u8350';
  overlay.style.display = 'flex';
}

function applyRecommend(presetIdx){
  var preset = _recommendPresets[presetIdx];
  if(!preset) return;
  var matched = [];
  _allStrategies.forEach(function(s){
    var sid = s.id.toLowerCase();
    var sname = (s.name||'').toLowerCase();
    for(var k=0;k<preset.keywords.length;k++){
      if(sid.indexOf(preset.keywords[k])>=0 || sname.indexOf(preset.keywords[k])>=0){
        matched.push(s);
        break;
      }
    }
  });
  if(matched.length < 2){
    alert('\u672a\u627e\u5230\u8db3\u591f\u7684\u5339\u914d\u7b56\u7565\uff08\u81f3\u5c11\u9700\u8981 2 \u4e2a\uff09');
    return;
  }
  // Limit to 6 max
  if(matched.length > 6) matched = matched.slice(0, 6);
  var added = 0;
  matched.forEach(function(s){
    if(!_subStrategies.find(function(x){return x.strategyId===s.id})){
      _subStrategies.push({strategyId:s.id, weight:1, role:'primary'});
      added++;
    }
  });
  renderSubStrategies();
  closeBatchAdd();
  alert('\u300c'+preset.label+'\u300d\u5df2\u6dfb\u52a0 '+added+' \u4e2a\u7b56\u7565\uff08\u5171\u5339\u914d '+matched.length+' \u4e2a\uff09');
}

function applyRecommendRandom(){
  if(_allStrategies.length < 3){
    alert('\u53ef\u7528\u7b56\u7565\u4e0d\u8db3');
    return;
  }
  var pool = _allStrategies.slice();
  var n = 3 + Math.floor(Math.random() * 3); // 3-5
  if(n > pool.length) n = pool.length;
  // Fisher-Yates shuffle
  for(var i = pool.length - 1; i > 0; i--){
    var j = Math.floor(Math.random() * (i + 1));
    var tmp = pool[i]; pool[i] = pool[j]; pool[j] = tmp;
  }
  var picked = pool.slice(0, n);
  var added = 0;
  picked.forEach(function(s){
    if(!_subStrategies.find(function(x){return x.strategyId===s.id})){
      _subStrategies.push({strategyId:s.id, weight:1, role:'primary'});
      added++;
    }
  });
  renderSubStrategies();
  closeBatchAdd();
  alert('\u968f\u673a\u6dfb\u52a0\u4e86 '+added+' \u4e2a\u7b56\u7565');
}

function createComposition(){
  if(_subStrategies.length < 2){
    alert('\u81f3\u5c11\u9700\u8981 2 \u4e2a\u5b50\u7b56\u7565');
    return;
  }
  var method = document.getElementById('comp-method').value;
  var body = {
    name: document.getElementById('comp-name').value || '\u7ec4\u5408\u7b56\u7565',
    method: method,
    strategies: _subStrategies
  };
  if(method === 'voting'){
    body.minVotes = parseInt(document.getElementById('comp-min-votes').value) || 2;
  }
  api('/quant/admin/api/compositions',{method:'POST',body:JSON.stringify(body)}).then(function(data){
    var el = document.getElementById('comp-result');
    if(data.ok){
      el.innerHTML = '<div style="color:var(--green)">\u2705 \u7ec4\u5408\u7b56\u7565\u5df2\u521b\u5efa: <strong>'+data.composition.id+'</strong>\u3002\u53ef\u5728\u201c\u6279\u91cf\u56de\u6d4b\u201d\u4e2d\u4f7f\u7528\u3002</div>';
      _subStrategies = [];
      renderSubStrategies();
    } else {
      el.innerHTML = '<div style="color:var(--red)">\u274c '+( data.error||'\u521b\u5efa\u5931\u8d25')+'</div>';
    }
  });
}

// ── Explore Tab ──
function runExploration(){
  var btn = document.getElementById('btn-explore');
  btn.disabled = true;
  btn.textContent = '\u63a2\u7d22\u4e2d...';
  document.getElementById('explore-progress').innerHTML = '<div class="loading">\u{1f916} \u6b63\u5728\u8bfb\u53d6\u7b56\u7565\u5e93\u5e76\u7531 AI \u751f\u6210\u7ec4\u5408\uff0c\u968f\u540e\u8fdb\u884c\u6279\u91cf\u56de\u6d4b...</div>';
  document.getElementById('explore-results').innerHTML = '';

  var symbols = document.getElementById('explore-symbols').value || '000300';
  var body = {
    symbols: symbols,
    startDate: document.getElementById('explore-start').value || undefined,
    endDate: document.getElementById('explore-end').value || undefined,
    candidateCount: parseInt(document.getElementById('explore-count').value) || 10,
    minStrategies: parseInt(document.getElementById('explore-min').value) || 2,
    maxStrategies: parseInt(document.getElementById('explore-max').value) || 4,
    topK: parseInt(document.getElementById('explore-topk').value) || 5,
    initialCash: 100000
  };

  api('/quant/admin/api/strategy-lab/ai-explore',{method:'POST',body:JSON.stringify(body)}).then(function(data){
    btn.disabled = false;
    btn.textContent = '\u{1f680} \u5f00\u59cb\u63a2\u7d22';
    var sourceText = data && data.source === 'ai' ? '\u7531 AI \u751f\u6210\u5019\u9009\u7b56\u7565' : '\u4f7f\u7528\u89c4\u5219\u56de\u9000\u751f\u6210\u5019\u9009\u7b56\u7565';
    document.getElementById('explore-progress').innerHTML = '<div style="font-size:12px;color:var(--text-dim)">\u2705 '+sourceText+'\uff0c\u5df2\u5b8c\u6210\u56de\u6d4b\u6392\u5e8f</div>';
    renderExploreResults(data);
    loadExploreHistory();
  }).catch(function(err){
    btn.disabled = false;
    btn.textContent = '\u{1f680} \u5f00\u59cb\u63a2\u7d22';
    document.getElementById('explore-progress').innerHTML = '<div style="color:var(--red)">\u63a2\u7d22\u5931\u8d25: '+err.message+'</div>';
  });
}

function renderExploreResults(data){
  var el = document.getElementById('explore-results');
  if(!data.ok && !data.ranked){
    el.innerHTML = '<div class="card"><div style="color:var(--red)">'+(data.error||'\u63a2\u7d22\u5931\u8d25')+'</div></div>';
    return;
  }
  var ranked = data.ranked || [];
  if(ranked.length===0){
    var failHtml = '<div class="card"><div class="empty">\u672a\u627e\u5230\u6709\u6548\u7ed3\u679c</div>';
    var failedRuns = (data.results || []).filter(function(r){ return !r.ok; });
    if(failedRuns.length > 0){
      failHtml += '<div style="margin-top:12px;font-size:12px;color:var(--text-dim)">\u5931\u8d25\u8be6\u60c5 ('+failedRuns.length+' \u6761):<ul style="text-align:left;margin:4px 0">';
      failedRuns.slice(0,5).forEach(function(r){ failHtml += '<li>'+r.label+': '+(r.error||'\u672a\u77e5\u9519\u8bef')+'</li>'; });
      if(failedRuns.length > 5) failHtml += '<li>\u2026\u53e6\u6709 '+(failedRuns.length-5)+' \u6761</li>';
      failHtml += '</ul></div>';
    }
    failHtml += '</div>';
    el.innerHTML = failHtml;
    return;
  }
  var candidates = data.candidates || [];
  var candidateMap = {};
  candidates.forEach(function(c){ candidateMap[c.name] = c; });
  // Fetch saved compositions to mark already-favorited candidates
  api('/quant/admin/api/compositions').then(function(compData){
    var savedComps = compData.compositions || [];
    var savedSet = {};
    savedComps.forEach(function(c){
      var sig = c.method + '|' + c.strategies.map(function(s){return s.strategyId}).sort().join('+');
      savedSet[sig] = true;
    });
    _renderExploreTable(el, data, ranked, candidates, candidateMap, savedSet);
  }).catch(function(){
    _renderExploreTable(el, data, ranked, candidates, candidateMap, {});
  });
  // Store candidates globally for favorite action
  window._lastExploreCandidates = candidates;
  window._lastExploreRanked = ranked;
}

function _renderExploreTable(el, data, ranked, candidates, candidateMap, savedSet){
  var html = '<div class="card"><h3>\u{1f3c6} \u63a2\u7d22\u7ed3\u679c (\u6210\u529f '+data.successCount+'/'+data.totalRuns+')</h3>';
  html += '<table><thead><tr><th>\u6392\u540d</th><th>\u7b56\u7565</th><th>\u7ec4\u6210</th><th>\u603b\u6536\u76ca</th><th>\u5e74\u5316</th><th>\u6700\u5927\u56de\u64a4</th><th>\u590f\u666e</th><th>\u80dc\u7387</th><th>\u4ea4\u6613\u6570</th><th>\u64cd\u4f5c</th></tr></thead><tbody>';
  ranked.forEach(function(r,i){
    var rankClass = i<3?'rank-'+(i+1):'';
    var cand = candidateMap[r.label];
    var subHtml = '';
    if(cand && cand.strategies){
      subHtml = '<span class="tag tag-'+cand.method+'">'+cand.method+'</span> ';
      subHtml += cand.strategies.map(function(s){ return s.strategyId; }).join(' + ');
    }
    html += '<tr>';
    html += '<td><span class="result-rank '+rankClass+'">'+(i+1)+'</span></td>';
    html += '<td><strong>'+r.label+'</strong></td>';
    html += '<td style="font-size:12px">'+subHtml+'</td>';
    html += '<td class="'+(r.totalReturn>0?'metric-good':'metric-bad')+'">'+(r.totalReturn||0).toFixed(2)+'%</td>';
    html += '<td>'+(r.annualizedReturn||0).toFixed(2)+'%</td>';
    html += '<td class="metric-bad">-'+(r.maxDrawdown||0).toFixed(2)+'%</td>';
    html += '<td class="'+((r.sharpeRatio||0)>1?'metric-good':(r.sharpeRatio||0)>0?'metric-ok':'metric-bad')+'">'+(r.sharpeRatio||0).toFixed(2)+'</td>';
    html += '<td>'+(r.winRate||0).toFixed(1)+'%</td>';
    html += '<td>'+(r.totalTrades||0)+'</td>';
    if(cand){
      var candSig = cand.method + '|' + cand.strategies.map(function(s){return s.strategyId}).sort().join('+');
      if(savedSet[candSig]){
        html += '<td><span class="tag" style="background:#3f2a1a;color:#d29922">\u2b50 \u5df2\u6536\u85cf</span></td>';
      } else {
        html += '<td><button class="btn btn-sm btn-primary" onclick="favoriteCandidate('+i+')">\u2b50 \u6536\u85cf</button></td>';
      }
    } else {
      html += '<td></td>';
    }
    html += '</tr>';
  });
  html += '</tbody></table></div>';
  el.innerHTML = html;
}

function favoriteCandidate(rankIdx){
  var ranked = window._lastExploreRanked || [];
  var candidates = window._lastExploreCandidates || [];
  if(!ranked[rankIdx]) return;
  var label = ranked[rankIdx].label;
  var cand = null;
  candidates.forEach(function(c){ if(c.name===label) cand=c; });
  if(!cand){
    alert('\u672a\u627e\u5230\u5019\u9009\u7b56\u7565\u8be6\u60c5');
    return;
  }
  var body = {
    name: cand.name,
    method: cand.method,
    strategies: cand.strategies,
    minVotes: cand.minVotes,
    rationale: cand.rationale
  };
  api('/quant/admin/api/strategy-lab/favorite',{method:'POST',body:JSON.stringify(body)}).then(function(data){
    if(data.ok){
      alert('\u2705 \u5df2\u6536\u85cf\u300c'+cand.name+'\u300d\uff0c\u53ef\u5728\u201c\u5386\u53f2\u201d\u6807\u7b7e\u9875\u67e5\u770b\uff0c\u4e5f\u53ef\u7528\u4e8e\u5b9e\u9645\u4ea4\u6613\u3002');
    } else {
      alert('\u6536\u85cf\u5931\u8d25: '+(data.error||'\u672a\u77e5\u9519\u8bef'));
    }
  });
}

// ── Strategy Management Tab ──
function loadStrategyManagement(){
  loadBuiltinStrategies();
  loadCompositions();
}

function loadBuiltinStrategies(){
  api('/quant/admin/api/strategies').then(function(data){
    var all = data.builtin || [];
    // Filter to only pure builtins (exclude user-added compositions)
    var builtins = all.filter(function(s){ return s.id.indexOf('fav-')!==0 && s.id.indexOf('composed-')!==0; });
    var el = document.getElementById('builtin-strategy-list');
    var countEl = document.getElementById('builtin-count');
    if(countEl) countEl.textContent = '\u5171 '+builtins.length+' \u4e2a';
    if(!el) return;
    if(builtins.length===0){
      el.innerHTML = '<div class="empty">\u65e0\u5185\u7f6e\u7b56\u7565</div>';
      return;
    }
    var html = '<table><thead><tr><th>ID</th><th>\u540d\u79f0</th><th>\u8bf4\u660e</th></tr></thead><tbody>';
    builtins.forEach(function(s){
      html += '<tr>';
      html += '<td style="font-family:monospace;font-size:12px">'+s.id+'</td>';
      html += '<td>'+s.name+'</td>';
      html += '<td style="font-size:12px;color:var(--text-dim)">'+(s.description||'-')+'</td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  });
}

function loadCompositions(){
  return api('/quant/admin/api/compositions').then(function(data){
    var compositions = data.compositions || [];
    var el = document.getElementById('history-list');
    if(!el) return compositions;
    document.getElementById('user-comp-count').textContent = compositions.length > 0 ? '\u5171 '+compositions.length+' \u4e2a' : '';
    if(compositions.length===0){
      el.innerHTML = '<div class="empty">\u8fd8\u6ca1\u6709\u6536\u85cf\u7684\u7b56\u7565\u3002\u5728\u201c\u63a2\u7d22\u201d\u7ed3\u679c\u4e2d\u70b9\u51fb\u2b50\u6536\u85cf\uff0c\u6216\u5728\u201c\u7ec4\u5408\u201d\u6807\u7b7e\u9875\u4e2d\u624b\u52a8\u521b\u5efa\u3002</div>';
      return compositions;
    }
    var html = '<div class="grid grid-2">';
    compositions.forEach(function(c){
      var methodTag = '<span class="tag tag-'+c.method+'">'+c.method+'</span>';
      var isFav = c.id && c.id.indexOf('fav-')===0;
      var originTag = isFav ? '<span class="tag" style="background:#3f2a1a;color:#d29922">\u2b50 \u6536\u85cf</span>' : '<span class="tag" style="background:#1f3a5f;color:#58a6ff">\u624b\u52a8</span>';
      html += '<div class="card">';
      html += '<div style="display:flex;justify-content:space-between;align-items:center">';
      html += '<h3>'+originTag+' '+methodTag+' '+c.name+'</h3>';
      html += '<button class="btn btn-sm btn-danger" onclick="deleteComposition(this.dataset.cid)" data-cid="'+c.id+'">\u5220\u9664</button>';
      html += '</div>';
      if(c.description) html += '<div style="font-size:12px;color:var(--text-dim);margin:4px 0">'+c.description+'</div>';
      html += '<div style="font-size:12px;color:var(--text-dim);margin:4px 0">ID: '+c.id+'</div>';
      html += '<div style="margin:8px 0">\u7b56\u7565\u6570: '+c.strategies.length+'</div>';
      html += '<div class="sub-strategy-list">';
      c.strategies.forEach(function(s){
        html += '<div class="sub-strategy-item"><strong>'+s.strategyId+'</strong> w='+( s.weight||1)+'</div>';
      });
      html += '</div>';
      if(c.createdAt) html += '<div style="font-size:11px;color:var(--text-dim);margin-top:8px">\u521b\u5efa: '+c.createdAt+'</div>';
      html += '</div>';
    });
    html += '</div>';
    el.innerHTML = html;
    return compositions;
  });
}

function deleteComposition(id){
  if(!confirm('\u786e\u5b9a\u5220\u9664\u7ec4\u5408\u7b56\u7565 '+id+' ?')) return;
  api('/quant/admin/api/compositions',{method:'DELETE',body:JSON.stringify({id:id})}).then(function(){
    loadCompositions();
  });
}

// ── Batch Backtest Tab ──
var _batchAllOptions = []; // {value, text, selected}

function filterBatchStrategies(){
  var keyword = (document.getElementById('batch-search').value || '').trim().toLowerCase();
  var sel = document.getElementById('batch-strategies');
  // Save current selections
  var selectedSet = {};
  for(var i=0; i<sel.options.length; i++){
    if(sel.options[i].selected) selectedSet[sel.options[i].value] = true;
  }
  // Also merge from stored state
  _batchAllOptions.forEach(function(o){ if(o.selected) selectedSet[o.value] = true; });
  // Update stored selections
  _batchAllOptions.forEach(function(o){ o.selected = !!selectedSet[o.value]; });
  // Filter: split keyword by space, all parts must match
  var parts = keyword ? keyword.split(/\s+/) : [];
  var filtered = _batchAllOptions.filter(function(o){
    if(parts.length===0) return true;
    var haystack = (o.text + ' ' + o.value).toLowerCase();
    return parts.every(function(p){ return haystack.indexOf(p)!==-1; });
  });
  // Rebuild select
  sel.innerHTML = '';
  filtered.forEach(function(o){
    var opt = document.createElement('option');
    opt.value = o.value;
    opt.textContent = o.text;
    opt.selected = !!selectedSet[o.value];
    sel.appendChild(opt);
  });
  var countEl = document.getElementById('batch-count');
  if(countEl) countEl.textContent = keyword ? '\u5339\u914d: '+filtered.length+'/'+_batchAllOptions.length : '\u5171 '+_batchAllOptions.length+' \u4e2a\u7b56\u7565';
}

function batchSelectAll(){
  var sel = document.getElementById('batch-strategies');
  for(var i=0; i<sel.options.length; i++) sel.options[i].selected = true;
  // Sync to stored
  var selectedSet = {};
  for(var i=0; i<sel.options.length; i++) selectedSet[sel.options[i].value] = true;
  _batchAllOptions.forEach(function(o){ if(selectedSet[o.value]) o.selected = true; });
}

function batchSelectNone(){
  var sel = document.getElementById('batch-strategies');
  for(var i=0; i<sel.options.length; i++) sel.options[i].selected = false;
  _batchAllOptions.forEach(function(o){ o.selected = false; });
}

function loadBatchStrategies(){
  loadStrategies().then(function(){
    var sel = document.getElementById('batch-strategies');
    sel.innerHTML = '';
    _batchAllOptions = [];
    _allStrategies.forEach(function(s){
      var isUser = s.id.indexOf('fav-')===0 || s.id.indexOf('composed-')===0;
      var text = (isUser ? '\u2b50 ' : '\u{1f4d0} ')+s.name+' ('+s.id+')';
      _batchAllOptions.push({value: s.id, text: text, selected: false});
    });
    // Also load user compositions (composed-* and fav-*)
    api('/quant/admin/api/compositions').then(function(data){
      var compositions = data.compositions || [];
      compositions.forEach(function(c){
        // Skip if already in _allStrategies
        if(_allStrategies.find(function(s){return s.id===c.id})) return;
        if(_batchAllOptions.find(function(o){return o.value===c.id})) return;
        var text = '\u2b50 '+c.name+' ('+c.id+')';
        _batchAllOptions.push({value: c.id, text: text, selected: false});
      });
      filterBatchStrategies();
    });
  });
}

function runBatchBacktest(){
  var btn = document.getElementById('btn-batch');
  var sel = document.getElementById('batch-strategies');
  // Sync current visible selections into stored state
  for(var i=0; i<sel.options.length; i++){
    var o = _batchAllOptions.find(function(x){return x.value===sel.options[i].value});
    if(o) o.selected = sel.options[i].selected;
  }
  var ids = _batchAllOptions.filter(function(o){return o.selected}).map(function(o){return o.value});
  if(ids.length===0){
    alert('\u8bf7\u81f3\u5c11\u9009\u62e9\u4e00\u4e2a\u7b56\u7565');
    return;
  }
  btn.disabled = true;
  btn.textContent = '\u56de\u6d4b\u4e2d...';
  var body = {
    strategyIds: ids,
    symbols: document.getElementById('batch-symbols').value || '000300',
    startDate: document.getElementById('batch-start').value || undefined,
    endDate: document.getElementById('batch-end').value || undefined,
    initialCash: 100000
  };
  api('/quant/admin/api/batch-backtest',{method:'POST',body:JSON.stringify(body)}).then(function(data){
    btn.disabled = false;
    btn.textContent = '\u{1f680} \u8fd0\u884c\u6279\u91cf\u56de\u6d4b';
    renderBatchResults(data);
  }).catch(function(){
    btn.disabled = false;
    btn.textContent = '\u{1f680} \u8fd0\u884c\u6279\u91cf\u56de\u6d4b';
  });
}

function renderBatchResults(data){
  var el = document.getElementById('batch-results');
  if(!data.ok){
    el.innerHTML = '<div class="card"><div style="color:var(--red)">'+(data.error||'\u6279\u91cf\u56de\u6d4b\u5931\u8d25')+'</div></div>';
    return;
  }
  var ranked = data.ranked || [];
  var failedRuns = (data.results || []).filter(function(r){ return !r.ok; });
  if(ranked.length===0){
    var failHtml = '<div class="card"><div class="empty">\u672a\u627e\u5230\u6709\u6548\u7ed3\u679c</div>';
    if(failedRuns.length > 0){
      failHtml += '<div style="margin-top:12px;font-size:12px;color:var(--text-dim)">\u5931\u8d25\u8be6\u60c5 ('+failedRuns.length+' \u6761):<ul style="text-align:left;margin:4px 0">';
      failedRuns.slice(0,5).forEach(function(r){ failHtml += '<li>'+r.label+': '+(r.error||'\u672a\u77e5\u9519\u8bef')+'</li>'; });
      if(failedRuns.length > 5) failHtml += '<li>\u2026\u53e6\u6709 '+(failedRuns.length-5)+' \u6761</li>';
      failHtml += '</ul></div>';
    }
    failHtml += '</div>';
    el.innerHTML = failHtml;
    return;
  }
  _lastBatchRanked = ranked;
  var html = '<div class="card"><h3>\u{1f3c6} \u6279\u91cf\u56de\u6d4b\u7ed3\u679c (\u6210\u529f '+data.successCount+'/'+data.totalRuns+')</h3>';
  html += '<div style="margin:8px 0"><button class="btn btn-primary" onclick="importTopNToCompose(3)" style="font-size:12px;margin-right:6px">\u{1f4e5} Top-3 \u5bfc\u5165\u7ec4\u5408</button>';
  html += '<button class="btn btn-primary" onclick="importTopNToCompose(5)" style="font-size:12px;margin-right:6px">\u{1f4e5} Top-5 \u5bfc\u5165\u7ec4\u5408</button>';
  html += '<button class="btn" onclick="importTopNToCompose(10)" style="font-size:12px">Top-10</button></div>';
  html += '<table><thead><tr><th>\u6392\u540d</th><th>\u7b56\u7565</th><th>\u603b\u6536\u76ca</th><th>\u5e74\u5316</th><th>\u6700\u5927\u56de\u64a4</th><th>\u590f\u666e</th><th>\u80dc\u7387</th><th>\u4ea4\u6613\u6570</th></tr></thead><tbody>';
  ranked.forEach(function(r,i){
    var rankClass = i<3?'rank-'+(i+1):'';
    html += '<tr>';
    html += '<td><span class="result-rank '+rankClass+'">'+(i+1)+'</span></td>';
    html += '<td><strong>'+r.label+'</strong></td>';
    html += '<td class="'+((r.totalReturn||0)>0?'metric-good':'metric-bad')+'">'+(r.totalReturn||0).toFixed(2)+'%</td>';
    html += '<td>'+(r.annualizedReturn||0).toFixed(2)+'%</td>';
    html += '<td class="metric-bad">-'+(r.maxDrawdown||0).toFixed(2)+'%</td>';
    html += '<td class="'+((r.sharpeRatio||0)>1?'metric-good':(r.sharpeRatio||0)>0?'metric-ok':'metric-bad')+'">'+(r.sharpeRatio||0).toFixed(2)+'</td>';
    html += '<td>'+(r.winRate||0).toFixed(1)+'%</td>';
    html += '<td>'+(r.totalTrades||0)+'</td>';
    html += '</tr>';
  });
  html += '</tbody></table>';
  if(failedRuns.length > 0){
    html += '<div style="margin-top:12px;font-size:12px;color:var(--text-dim)">\u5931\u8d25 ('+failedRuns.length+' \u6761): ';
    failedRuns.forEach(function(r){ html += r.label+': '+(r.error||'\u672a\u77e5\u9519\u8bef')+'; '; });
    html += '</div>';
  }
  html += '</div>';
  el.innerHTML = html;
}

// ── Import Top-N to Compose ──
function importTopNToCompose(n){
  var topN = _lastBatchRanked.slice(0, n);
  if(topN.length===0){ alert('\u6ca1\u6709\u53ef\u5bfc\u5165\u7684\u7ed3\u679c'); return; }
  var added = 0;
  topN.forEach(function(r, i){
    var sid = r.strategyId || r.label;
    if(_subStrategies.find(function(s){return s.strategyId===sid})) return;
    _subStrategies.push({strategyId: sid, weight: n - i, role: 'signal'});
    added++;
  });
  renderSubStrategies();
  alert('\u5df2\u5bfc\u5165 ' + added + ' \u4e2a\u7b56\u7565\u5230\u7ec4\u5408 Tab\uff08\u6743\u91cd\u6309\u6392\u540d\u9012\u51cf\uff09');
  switchTab('compose');
}

// ── Batch Backtest History ──
function loadBatchHistory(){
  var el = document.getElementById('batch-history');
  if(!el) return;
  el.innerHTML = '<div class="empty">\u52a0\u8f7d\u4e2d...</div>';
  api('/quant/admin/api/batch-backtest/history').then(function(data){
    var records = data.records || [];
    if(records.length===0){
      el.innerHTML = '<div class="empty">\u8fd8\u6ca1\u6709\u6279\u91cf\u56de\u6d4b\u5386\u53f2\u8bb0\u5f55</div>';
      return;
    }
    var html = '<table><thead><tr><th>\u65f6\u95f4</th><th>\u6807\u7684</th><th>\u533a\u95f4</th><th>\u6210\u529f/\u603b\u6570</th><th>\u6700\u4f18\u7b56\u7565</th><th>\u5206\u6570</th><th>\u64cd\u4f5c</th></tr></thead><tbody>';
    records.forEach(function(r){
      var dt = new Date(r.timestamp);
      var dateStr = dt.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})+' '+dt.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
      var syms = Array.isArray(r.symbols) ? r.symbols.join(',') : r.symbols;
      html += '<tr>';
      html += '<td>'+dateStr+'</td>';
      html += '<td>'+syms+'</td>';
      html += '<td>'+r.startDate+' ~ '+r.endDate+'</td>';
      html += '<td>'+r.successCount+'/'+r.totalRuns+'</td>';
      html += '<td><strong>'+(r.bestStrategy||'-')+'</strong></td>';
      html += '<td>'+(r.bestCompositeScore||0).toFixed(1)+'</td>';
      html += '<td><button class="btn" onclick="viewBatchRecord(\\''+r.id+'\\')" style="font-size:11px;padding:1px 6px">\u67e5\u770b</button> ';
      html += '<button class="btn" onclick="deleteBatchRecord(\\''+r.id+'\\')" style="font-size:11px;padding:1px 6px;color:var(--red)">\u5220\u9664</button></td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  }).catch(function(e){ el.innerHTML = '<div style="color:var(--red)">\u52a0\u8f7d\u5931\u8d25: '+e.message+'</div>'; });
}

function viewBatchRecord(id){
  api('/quant/admin/api/batch-backtest/history?id='+id).then(function(data){
    if(!data.ok){ alert('\u672a\u627e\u5230\u8bb0\u5f55'); return; }
    var record = data.record;
    renderBatchResults({ok:true, ranked:record.ranked, successCount:record.successCount, totalRuns:record.totalRuns, results:record.ranked});
  });
}

function deleteBatchRecord(id){
  if(!confirm('\u786e\u8ba4\u5220\u9664\u8be5\u6279\u91cf\u56de\u6d4b\u8bb0\u5f55?')) return;
  api('/quant/admin/api/batch-backtest/history?id='+id,{method:'DELETE'}).then(function(){
    loadBatchHistory();
  });
}

// ── Explore History ──
function loadExploreHistory(){
  var el = document.getElementById('explore-history-list');
  if(!el) return;
  // Fetch both history and saved compositions to mark favorited items
  Promise.all([
    api('/quant/admin/api/strategy-lab/explore-history'),
    api('/quant/admin/api/compositions')
  ]).then(function(results){
    var data = results[0];
    var savedComps = results[1].compositions || [];
    // Build a set of saved strategy signatures: "method|id1+id2+..."
    var savedSet = {};
    savedComps.forEach(function(c){
      var sig = c.method + '|' + c.strategies.map(function(s){return s.strategyId}).sort().join('+');
      savedSet[sig] = c.name;
    });
    var records = data.records || [];
    if(records.length===0){
      el.innerHTML = '<div class="empty">\u8fd8\u6ca1\u6709\u63a2\u7d22\u8bb0\u5f55\u3002\u70b9\u51fb\u201c\u5f00\u59cb\u63a2\u7d22\u201d\u751f\u6210\u7b2c\u4e00\u6b21\u7ed3\u679c\u3002</div>';
      return;
    }
    var html = '<table><thead><tr><th>\u65f6\u95f4</th><th>\u6807\u7684</th><th>\u6765\u6e90</th><th>\u5019\u9009</th><th>\u6210\u529f</th><th>\u6700\u4f18\u7b56\u7565</th><th>\u7ec4\u6210</th><th>\u64cd\u4f5c</th></tr></thead><tbody>';
    records.forEach(function(r){
      var dt = new Date(r.createdAt);
      var dateStr = dt.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit'})+' '+dt.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});
      var best = r.ranked && r.ranked.length>0 ? r.ranked[0].label : '-';
      var bestReturn = r.ranked && r.ranked.length>0 ? (r.ranked[0].totalReturn||0).toFixed(2)+'%' : '';
      var bestCand = null;
      if(r.candidates && r.ranked && r.ranked.length>0){
        r.candidates.forEach(function(c){ if(c.name===r.ranked[0].label) bestCand=c; });
      }
      var bestSubs = bestCand && bestCand.strategies ? bestCand.strategies.map(function(s){return s.strategyId}).sort().join(' + ') : '-';
      var isFaved = false;
      if(bestCand && bestCand.strategies){
        var sig = bestCand.method + '|' + bestCand.strategies.map(function(s){return s.strategyId}).sort().join('+');
        isFaved = !!savedSet[sig];
      }
      var favBadge = isFaved ? ' <span class="tag" style="background:#3f2a1a;color:#d29922">\u2b50 \u5df2\u6536\u85cf</span>' : '';
      html += '<tr>';
      html += '<td style="font-size:12px">'+dateStr+'</td>';
      html += '<td>'+r.params.symbols.join(',')+'</td>';
      html += '<td><span class="tag '+(r.source==='ai'?'tag-voting':'tag-gated')+'">'+r.source+'</span></td>';
      html += '<td>'+r.totalRuns+'</td>';
      html += '<td>'+r.successCount+'/'+r.totalRuns+'</td>';
      html += '<td>'+best+(bestReturn?' <span style="color:var(--green)">'+bestReturn+'</span>':'')+''+favBadge+'</td>';
      html += '<td style="font-size:12px">'+bestSubs+'</td>';
      html += '<td><button class="btn btn-sm" onclick="viewExploreRecord(\\''+r.id+'\\')">\u67e5\u770b</button> ';
      html += '<button class="btn btn-sm btn-danger" onclick="deleteExploreRecord(\\''+r.id+'\\')">\u5220\u9664</button></td>';
      html += '</tr>';
    });
    html += '</tbody></table>';
    el.innerHTML = html;
  });
}

function viewExploreRecord(id){
  api('/quant/admin/api/strategy-lab/explore-history?id='+encodeURIComponent(id)).then(function(data){
    if(!data.ok || !data.record){
      alert('\u672a\u627e\u5230\u8be5\u8bb0\u5f55');
      return;
    }
    var r = data.record;
    document.getElementById('explore-progress').innerHTML = '<div style="font-size:12px;color:var(--text-dim)">\u{1f4c1} \u5386\u53f2\u8bb0\u5f55: '+new Date(r.createdAt).toLocaleString('zh-CN')+' | \u6807\u7684: '+r.params.symbols.join(',')+' | '+r.params.startDate+' ~ '+r.params.endDate+'</div>';
    renderExploreResults({ok:true, source:r.source, candidates:r.candidates, ranked:r.ranked, results:r.results, successCount:r.successCount, totalRuns:r.totalRuns});
  });
}

function deleteExploreRecord(id){
  if(!confirm('\u786e\u5b9a\u5220\u9664\u8be5\u63a2\u7d22\u8bb0\u5f55\uff1f')) return;
  api('/quant/admin/api/strategy-lab/explore-history',{method:'DELETE',body:JSON.stringify({id:id})}).then(function(){
    loadExploreHistory();
  });
}

// ── Init defaults ──
(function(){
  var now = new Date();
  var y1 = new Date(now); y1.setFullYear(y1.getFullYear()-1);
  var methodEl = document.getElementById('comp-method');
  if(methodEl){
    methodEl.addEventListener('change', updateComposeMethodFields);
  }
  updateComposeMethodFields();
  document.getElementById('explore-start').value = y1.toISOString().slice(0,10);
  document.getElementById('explore-end').value = now.toISOString().slice(0,10);
  document.getElementById('batch-start').value = y1.toISOString().slice(0,10);
  document.getElementById('batch-end').value = now.toISOString().slice(0,10);
})();
</script>
</body>
</html>`;
}
