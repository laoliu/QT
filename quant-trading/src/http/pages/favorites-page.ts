/** Return the full HTML for the favorites management page. */
export function getFavoritesPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Quant Trading 股票收藏</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}.actions{display:flex;gap:8px;flex-wrap:wrap}
.h1-badge{display:inline-flex;align-items:center;margin-left:8px;padding:2px 8px;border-radius:999px;font-size:12px;vertical-align:middle;border:1px solid var(--border);color:var(--text-dim);background:var(--bg3);cursor:pointer}
.h1-badge:hover{border-color:var(--accent);color:var(--accent)}
.h1-badge.warn{color:var(--yellow);border-color:rgba(210,153,34,.45);background:rgba(210,153,34,.12)}
.h1-badge.active{color:var(--accent);border-color:var(--accent);background:rgba(88,166,255,.12)}
.h1-badge:disabled{cursor:not-allowed;color:var(--text-dim);border-color:var(--border);background:var(--bg3)}
.h1-badge:disabled:hover{color:var(--text-dim);border-color:var(--border)}
main{padding:20px 24px;display:grid;grid-template-columns:360px 1fr;gap:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px}.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
label{font-size:12px;color:var(--text-dim);display:block;margin-bottom:4px}
input,select,textarea{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px}
textarea{min-height:84px;resize:vertical}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}.btn-primary{background:#238636;border-color:#2ea043;color:#fff}.btn-danger{background:#da3633;border-color:#f85149;color:#fff}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin-bottom:14px}.stat{background:var(--bg3);border:1px solid var(--border);border-radius:10px;padding:12px}
.stat .label{font-size:11px;color:var(--text-dim)}.stat .value{font-size:22px;font-weight:700;margin-top:4px}
.filters{display:grid;grid-template-columns:1.4fr 1fr 1fr 1fr;gap:10px;margin-bottom:14px}.muted{color:var(--text-dim);font-size:12px}
table{width:100%;border-collapse:collapse;font-size:12px}th,td{padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:top;text-align:left}th{color:var(--text-dim);font-size:11px}
tr:hover{background:rgba(255,255,255,.02)}.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(88,166,255,.12);color:var(--accent);font-size:11px;margin:2px 4px 0 0}
.priority{color:var(--yellow);letter-spacing:1px}.empty{padding:24px 0;text-align:center;color:var(--text-dim)}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.chg-up{color:var(--green)!important}.chg-down{color:var(--red)!important}
.th-sort{background:none;border:none;color:inherit;padding:0;margin:0;cursor:pointer;font:inherit}
.th-sort:hover{color:var(--accent)}
.quote-missing{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(139,148,158,.18);color:var(--text-dim);font-size:11px}
.status-btn{background:none;border:none;color:var(--accent);cursor:pointer;padding:0;font-size:12px}
.status-btn:hover{text-decoration:underline}
.status-btn:disabled{color:var(--text-dim);cursor:not-allowed;text-decoration:none}
.filter-banner{display:none;margin-bottom:10px;padding:8px 10px;border:1px solid var(--border);border-radius:8px;background:rgba(88,166,255,.1);font-size:12px;color:var(--text)}
.banner-close{margin-left:10px;background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:12px}
.banner-close:hover{color:var(--text)}
@media(max-width:980px){main{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.stats{grid-template-columns:1fr 1fr 1fr}}
@media(max-width:640px){.filters,.grid,.stats{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <a href="${b}/quant/admin/ui">🔧 管理面板</a> / <span>⭐ 股票收藏</span></div>
<header>
  <h1>⭐ 股票收藏 <button type="button" id="missing-count-badge" class="h1-badge" onclick="toggleMissingQuoteFilter()">无行情 0</button></h1>
  <div class="actions">
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
    <button class="btn" onclick="resetForm()">新建收藏</button>
  </div>
</header>
<main>
  <section class="card">
    <h2 id="form-title">新增收藏</h2>
    <div class="grid">
      <div>
        <label for="code">股票代码</label>
        <input id="code" placeholder="例如 600519">
      </div>
      <div>
        <label for="name">股票名称</label>
        <input id="name" placeholder="例如 贵州茅台">
      </div>
    </div>
    <div class="grid">
      <div>
        <label for="category">分类</label>
        <input id="category" placeholder="watchlist / ideas / portfolio">
      </div>
      <div>
        <label for="priority">优先级</label>
        <select id="priority">
          <option value="">未设置</option>
          <option value="1">1</option>
          <option value="2">2</option>
          <option value="3">3</option>
          <option value="4">4</option>
          <option value="5">5</option>
        </select>
      </div>
    </div>
    <div class="grid">
      <div>
        <label for="priceTarget">目标价</label>
        <input id="priceTarget" type="number" step="0.01" placeholder="2500">
      </div>
      <div>
        <label for="convictionScore">信心分</label>
        <input id="convictionScore" type="number" min="1" max="10" step="1" placeholder="1-10">
      </div>
    </div>
    <div>
      <label for="tags">标签</label>
      <input id="tags" placeholder="逗号分隔，例如 蓝筹,白酒,长期">
    </div>
    <div style="margin-top:10px">
      <label for="notes">备注</label>
      <textarea id="notes" placeholder="记录你的关注理由、交易计划或风险点"></textarea>
    </div>
    <div class="row" style="margin-top:12px">
      <button class="btn btn-primary" onclick="submitFavorite()" id="submit-btn">保存收藏</button>
      <button class="btn" onclick="resetForm()">重置</button>
      <span class="muted" id="form-status"></span>
    </div>
  </section>
  <section class="card">
    <div class="stats">
      <div class="stat"><div class="label">收藏总数</div><div class="value" id="stat-count">0</div></div>
      <div class="stat"><div class="label">分类数量</div><div class="value" id="stat-categories">0</div></div>
      <div class="stat"><div class="label">标签数量</div><div class="value" id="stat-tags">0</div></div>
    </div>
    <div class="filters">
      <input id="filter-query" placeholder="搜索代码 / 名称 / 备注" oninput="renderFavorites()">
      <select id="filter-category" onchange="renderFavorites()"><option value="">全部分类</option></select>
      <select id="filter-tag" onchange="renderFavorites()"><option value="">全部标签</option></select>
      <select id="filter-priority" onchange="renderFavorites()">
        <option value="">全部优先级</option>
        <option value="5">5</option>
        <option value="4">4+</option>
        <option value="3">3+</option>
        <option value="2">2+</option>
        <option value="1">1+</option>
      </select>
    </div>
    <div class="toolbar">
      <div class="muted" id="list-status">加载中…</div>
      <button class="btn" onclick="loadFavorites()">刷新</button>
    </div>
    <div class="filter-banner" id="missing-filter-banner"></div>
    <div style="overflow:auto">
      <table>
        <thead>
          <tr>
            <th>代码 / 名称</th>
            <th>分类 / 标签</th>
            <th>优先级</th>
            <th><button class="th-sort" id="sort-change-pct" onclick="toggleChangePctSort()">现价 / 涨跌</button></th>
            <th>目标 / 信心</th>
            <th>备注</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody id="favorites-body"></tbody>
      </table>
    </div>
  </section>
</main>
<script>
const B='${b}';
let favorites=[];
let editCode='';
let changePctSort='desc';
let onlyMissingQuotes=false;
const MISSING_BANNER_KEY='quant-favorites-missing-banner-dismissed';
let missingBannerDismissed=false;
try{missingBannerDismissed=sessionStorage.getItem(MISSING_BANNER_KEY)==='1';}catch{}
function esc(v){return String(v??'').replace(/[&<>"']/g,function(ch){return({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch];});}
function escAttrJsSingleQuoted(v){return String(v??'').replace(/\\\\/g,'\\\\\\\\').replace(/'/g,"\\\\'");}
function tagsFromInput(value){return String(value||'').split(',').map(function(item){return item.trim();}).filter(Boolean);}
function setMissingBannerDismissed(value){
  missingBannerDismissed=!!value;
  try{sessionStorage.setItem(MISSING_BANNER_KEY, missingBannerDismissed?'1':'0');}catch{}
}
function formatQuoteTime(ts){
  const n=Number(ts);
  if(!Number.isFinite(n) || n<=0) return '';
  const d=new Date(n);
  if(Number.isNaN(d.getTime())) return '';
  const m=String(d.getMonth()+1).padStart(2,'0');
  const day=String(d.getDate()).padStart(2,'0');
  const h=String(d.getHours()).padStart(2,'0');
  const min=String(d.getMinutes()).padStart(2,'0');
  const s=String(d.getSeconds()).padStart(2,'0');
  return m+'-'+day+' '+h+':'+min+':'+s;
}
function updateMissingCountBadge(totalMissingCount){
  const el=document.getElementById('missing-count-badge');
  if(!el) return;
  el.textContent=onlyMissingQuotes?('无行情 '+totalMissingCount+' · 筛选中'):('无行情 '+totalMissingCount);
  const canToggle=onlyMissingQuotes||totalMissingCount>0;
  el.disabled=!canToggle;
  el.title=onlyMissingQuotes?'点击查看全部股票':(totalMissingCount>0?'点击只看无行情股票':'当前无无行情股票');
  el.setAttribute('aria-pressed', onlyMissingQuotes?'true':'false');
  el.setAttribute('aria-label', onlyMissingQuotes?('无行情筛选已开启，当前 '+totalMissingCount+' 条，点击查看全部股票'):('当前无行情 '+totalMissingCount+' 条，点击切换筛选'));
  if(totalMissingCount>0){
    el.classList.add('warn');
  }else{
    el.classList.remove('warn');
  }
  if(onlyMissingQuotes){
    el.classList.add('active');
  }else{
    el.classList.remove('active');
  }
}
function updateSortHeader(){
  const el=document.getElementById('sort-change-pct');
  if(!el) return;
  const suffix=changePctSort==='desc'?' ↓':changePctSort==='asc'?' ↑':'';
  const filterSuffix=onlyMissingQuotes?' · 仅无行情':'';
  el.textContent='现价 / 涨跌'+suffix+filterSuffix;
}
function toggleChangePctSort(){
  changePctSort=changePctSort==='none'?'desc':changePctSort==='desc'?'asc':'none';
  updateSortHeader();
  renderFavorites();
}
function toggleMissingQuoteFilter(){
  const totalMissing=favorites.filter(function(item){return !Number.isFinite(Number(item.changePct));}).length;
  if(!onlyMissingQuotes && totalMissing===0) return;
  onlyMissingQuotes=!onlyMissingQuotes;
  renderFavorites();
}
function dismissMissingBanner(){
  setMissingBannerDismissed(true);
  renderFavorites();
}
function restoreMissingBanner(){
  setMissingBannerDismissed(false);
  renderFavorites();
}
async function api(path, options){
  const res=await fetch(path,Object.assign({headers:{'Content-Type':'application/json'}},options||{}));
  const text=await res.text();
  const data=text?JSON.parse(text):{};
  if(!res.ok) throw new Error(data.error||('请求失败: '+res.status));
  return data;
}
function setStatus(id,text,isError){const el=document.getElementById(id); if(el){el.textContent=text||''; el.style.color=isError?'var(--red)':'var(--text-dim)';}}
function resetForm(){
  editCode='';
  document.getElementById('form-title').textContent='新增收藏';
  document.getElementById('submit-btn').textContent='保存收藏';
  ['code','name','category','priority','priceTarget','convictionScore','tags','notes'].forEach(function(id){document.getElementById(id).value='';});
  document.getElementById('code').disabled=false;
  setStatus('form-status','',false);
}
function populateFilters(meta){
  const categorySel=document.getElementById('filter-category');
  const tagSel=document.getElementById('filter-tag');
  const currentCategory=categorySel.value;
  const currentTag=tagSel.value;
  categorySel.innerHTML='<option value="">全部分类</option>'+meta.categories.map(function(item){return '<option value="'+esc(item)+'">'+esc(item)+'</option>';}).join('');
  tagSel.innerHTML='<option value="">全部标签</option>'+meta.tags.map(function(item){return '<option value="'+esc(item)+'">'+esc(item)+'</option>';}).join('');
  categorySel.value=currentCategory;
  tagSel.value=currentTag;
  document.getElementById('stat-categories').textContent=String(meta.categories.length);
  document.getElementById('stat-tags').textContent=String(meta.tags.length);
}
function renderFavorites(){
  updateSortHeader();
  const q=document.getElementById('filter-query').value.trim().toLowerCase();
  const category=document.getElementById('filter-category').value;
  const tag=document.getElementById('filter-tag').value;
  const priorityValue=document.getElementById('filter-priority').value;
  const minPriority=priorityValue?Number(priorityValue):0;
  const body=document.getElementById('favorites-body');
  let rows=favorites.filter(function(item){
    if(category && item.category!==category) return false;
    if(tag && !(item.tags||[]).includes(tag)) return false;
    if(minPriority && (Number(item.priority||0) < minPriority)) return false;
    if(!q) return true;
    return [item.code,item.name,item.notes,(item.tags||[]).join(','),item.category].some(function(field){return String(field||'').toLowerCase().includes(q);});
  });
  const normalizedRows=rows.map(function(item,index){
    const changePct=Number(item.changePct);
    return {
      item:item,
      index:index,
      hasQuote:Number.isFinite(changePct),
      changePct:changePct,
    };
  });
  if(onlyMissingQuotes){
    normalizedRows.splice(0, normalizedRows.length, ...normalizedRows.filter(function(entry){return !entry.hasQuote;}));
  }
  normalizedRows.sort(function(a,b){
    if(a.hasQuote!==b.hasQuote) return a.hasQuote?-1:1;
    if(!a.hasQuote && !b.hasQuote) return a.index-b.index;
    if(changePctSort==='asc') return a.changePct-b.changePct;
    if(changePctSort==='desc') return b.changePct-a.changePct;
    return a.index-b.index;
  });
  rows=normalizedRows.map(function(entry){return entry.item;});
  const totalMissingCount=favorites.filter(function(item){return !Number.isFinite(Number(item.changePct));}).length;
  const shownMissingCount=normalizedRows.filter(function(entry){return !entry.hasQuote;}).length;
  updateMissingCountBadge(totalMissingCount);
  document.getElementById('stat-count').textContent=String(rows.length);
  const bannerEl=document.getElementById('missing-filter-banner');
  if(bannerEl){
    if(onlyMissingQuotes && !missingBannerDismissed){
      bannerEl.style.display='block';
      bannerEl.innerHTML='当前仅显示无行情股票（'+shownMissingCount+' 条） <button class="status-btn" onclick="toggleMissingQuoteFilter()">退出过滤</button><button class="banner-close" onclick="dismissMissingBanner()">关闭提示</button>';
    }else{
      bannerEl.style.display='none';
      bannerEl.textContent='';
    }
  }
  const statusEl=document.getElementById('list-status');
  if(statusEl){
    if(rows.length){
      const toggleLabel=onlyMissingQuotes?'查看全部':(totalMissingCount===0?'暂无无行情项':'只看无行情('+totalMissingCount+')');
      const toggleDisabledAttr=!onlyMissingQuotes&&totalMissingCount===0?' disabled':'';
      const restoreBtn=onlyMissingQuotes&&missingBannerDismissed&&totalMissingCount>0?' <button class="status-btn" onclick="restoreMissingBanner()">恢复提示</button>':'';
      statusEl.innerHTML='显示 '+rows.length+' 条收藏（无行情 '+shownMissingCount+'/'+totalMissingCount+'） <button class="status-btn" onclick="toggleMissingQuoteFilter()"'+toggleDisabledAttr+'>'+toggleLabel+'</button>'+restoreBtn;
      statusEl.style.color='var(--text-dim)';
    }else{
      if(onlyMissingQuotes){
        statusEl.innerHTML='当前筛选下暂无无行情股票 <button class="status-btn" onclick="toggleMissingQuoteFilter()">查看全部</button>';
      }else{
        statusEl.textContent='暂无匹配的收藏';
      }
      statusEl.style.color='var(--text-dim)';
    }
  }
  if(!rows.length){
    const emptyText=onlyMissingQuotes?'当前筛选下暂无无行情股票':'暂无收藏股票';
    body.innerHTML='<tr><td colspan="7" class="empty">'+emptyText+'</td></tr>';
    return;
  }
  body.innerHTML=rows.map(function(item){
    const priority=Number(item.priority||0);
    const stars=priority?('★'.repeat(priority)+'☆'.repeat(Math.max(0,5-priority))):'-';
    const tags=(item.tags||[]).map(function(tagItem){return '<span class="pill">'+esc(tagItem)+'</span>';}).join('');
    const codeJs=escAttrJsSingleQuoted(item.code);
    const price=Number(item.currentPrice);
    const changePct=Number(item.changePct);
    const hasPrice=Number.isFinite(price);
    const hasChange=Number.isFinite(changePct);
    const changeClass=hasChange?(changePct>0?'chg-up':changePct<0?'chg-down':''):'muted';
    const priceText=hasPrice?price.toFixed(2):'-';
    const changeText=hasChange?((changePct>0?'+':'')+changePct.toFixed(2)+'%'):'-';
    const quoteBadge=hasChange?'':'<span class="quote-missing" title="数据源暂不可用或代码未识别">无行情</span>';
    const quoteTime=formatQuoteTime(item.quoteTimestamp);
    const quoteTimeText=quoteTime?('<div class="muted" style="margin-top:4px">更新 '+esc(quoteTime)+'</div>'):'';
    return '<tr>'+
      '<td><a href="'+B+'/quant/admin/stock?symbol='+encodeURIComponent(item.code)+'" target="_blank" style="color:var(--accent);text-decoration:none;font-weight:600">'+esc(item.code)+'</a><div class="muted">'+esc(item.name||'')+'</div></td>'+
      '<td><div>'+esc(item.category||'-')+'</div><div>'+tags+'</div></td>'+
      '<td><span class="priority">'+esc(stars)+'</span></td>'+
      '<td><div>'+esc(priceText)+'</div><div class="muted '+changeClass+'">'+esc(changeText)+'</div><div style="margin-top:4px">'+quoteBadge+'</div>'+quoteTimeText+'</td>'+
      '<td><div>目标价: '+esc(item.priceTarget ?? '-')+'</div><div class="muted">信心: '+esc(item.convictionScore ?? '-')+'</div></td>'+
      '<td>'+esc(item.notes||'-')+'</td>'+
      '<td><div class="row"><button class="btn" onclick="editFavorite(\\''+codeJs+'\\')">编辑</button><button class="btn btn-danger" onclick="removeFavorite(\\''+codeJs+'\\')">删除</button></div></td>'+
    '</tr>';
  }).join('');
}
function editFavorite(code){
  const item=favorites.find(function(entry){return entry.code===code;});
  if(!item) return;
  editCode=item.code;
  document.getElementById('form-title').textContent='编辑收藏 '+item.code;
  document.getElementById('submit-btn').textContent='更新收藏';
  document.getElementById('code').value=item.code;
  document.getElementById('code').disabled=true;
  document.getElementById('name').value=item.name||'';
  document.getElementById('category').value=item.category||'';
  document.getElementById('priority').value=item.priority||'';
  document.getElementById('priceTarget').value=item.priceTarget||'';
  document.getElementById('convictionScore').value=item.convictionScore||'';
  document.getElementById('tags').value=(item.tags||[]).join(', ');
  document.getElementById('notes').value=item.notes||'';
  setStatus('form-status','已载入 '+item.code,false);
  window.scrollTo({top:0,behavior:'smooth'});
}
async function removeFavorite(code){
  if(!confirm('确认删除 '+code+' ?')) return;
  try{
    await api(B+'/quant/admin/api/favorites',{method:'DELETE',body:JSON.stringify({code:code})});
    if(editCode===code) resetForm();
    await loadFavorites();
  }catch(err){setStatus('list-status',err.message,true);}
}
async function submitFavorite(){
  const code=document.getElementById('code').value.trim();
  if(!code){setStatus('form-status','请填写股票代码',true);return;}
  const payload={
    code:code,
    name:document.getElementById('name').value.trim(),
    category:document.getElementById('category').value.trim(),
    priority:document.getElementById('priority').value?Number(document.getElementById('priority').value):undefined,
    priceTarget:document.getElementById('priceTarget').value?Number(document.getElementById('priceTarget').value):undefined,
    convictionScore:document.getElementById('convictionScore').value?Number(document.getElementById('convictionScore').value):undefined,
    tags:tagsFromInput(document.getElementById('tags').value),
    notes:document.getElementById('notes').value.trim()
  };
  try{
    setStatus('form-status',editCode?'更新中…':'保存中…',false);
    await api(B+'/quant/admin/api/favorites',{
      method:editCode?'PATCH':'POST',
      body:JSON.stringify(payload)
    });
    resetForm();
    await loadFavorites();
    setStatus('form-status','保存成功',false);
  }catch(err){setStatus('form-status',err.message,true);}
}
async function loadFavorites(){
  try{
    setStatus('list-status','加载中…',false);
    const data=await api(B+'/quant/admin/api/favorites');
    favorites=data.favorites||[];
    if(onlyMissingQuotes){
      const totalMissing=favorites.filter(function(item){return !Number.isFinite(Number(item.changePct));}).length;
      if(totalMissing===0) onlyMissingQuotes=false;
    }
    populateFilters({categories:data.categories||[],tags:data.tags||[]});
    document.getElementById('stat-count').textContent=String(data.count||0);
    renderFavorites();
  }catch(err){setStatus('list-status',err.message,true); document.getElementById('favorites-body').innerHTML='<tr><td colspan="7" class="empty">加载失败</td></tr>';}
}
loadFavorites();
</script>
</body>
</html>`;
}
