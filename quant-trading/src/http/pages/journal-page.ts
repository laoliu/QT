/** Return the full HTML for the trade journal page. */
export function getJournalPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 交易笔记</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📝</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}.actions{display:flex;gap:8px;flex-wrap:wrap}
main{padding:20px 24px;display:grid;grid-template-columns:380px 1fr;gap:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px}
label{font-size:12px;color:var(--text-dim);display:block;margin-bottom:4px}
input,select,textarea{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px}
textarea{min-height:100px;resize:vertical}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.grid3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}.btn-primary:hover{background:#2ea043}
.btn-danger{background:#da3633;border-color:#f85149;color:#fff}.btn-danger:hover{background:#b62324}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:14px}
.stat{background:var(--bg3);border:1px solid var(--border);border-radius:10px;padding:12px}
.stat .label{font-size:11px;color:var(--text-dim)}.stat .value{font-size:22px;font-weight:700;margin-top:4px}
.filters{display:grid;grid-template-columns:1.2fr 1fr 1fr 1fr;gap:10px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:top;text-align:left}
th{color:var(--text-dim);font-size:11px}
tr{cursor:pointer}tr:hover{background:rgba(255,255,255,.02)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:rgba(88,166,255,.12);color:var(--accent);font-size:11px;margin:2px 4px 0 0}
.empty{padding:24px 0;text-align:center;color:var(--text-dim)}
.toolbar{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.stars{color:var(--yellow);letter-spacing:1px;font-size:14px}
.pnl-pos{color:var(--red)}.pnl-neg{color:var(--green)}
.top-tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:8px}
.tag-btn{background:var(--bg3);border:1px solid var(--border);border-radius:999px;padding:3px 10px;font-size:11px;color:var(--text-dim);cursor:pointer}
.tag-btn:hover,.tag-btn.active{border-color:var(--accent);color:var(--accent);background:rgba(88,166,255,.1)}
@media(max-width:980px){main{grid-template-columns:1fr}.filters{grid-template-columns:1fr 1fr}.stats{grid-template-columns:1fr 1fr}}
@media(max-width:640px){.filters,.grid,.grid3,.stats{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <a href="${b}/quant/admin/ui">🔧 管理面板</a> / <span>📝 交易笔记</span></div>
<header>
  <h1>📝 交易笔记</h1>
  <div class="actions">
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
    <button class="btn" onclick="resetForm()">新建笔记</button>
    <button class="btn" onclick="exportCsv()">导出 CSV</button>
  </div>
</header>
<main>
  <section class="card" id="formCard">
    <h2 id="form-title">新增笔记</h2>
    <div class="grid">
      <div><label for="f-symbol">股票代码</label><input id="f-symbol" placeholder="例如 600519"></div>
      <div><label for="f-date">日期</label><input id="f-date" type="date"></div>
    </div>
    <div style="margin-top:10px"><label for="f-content">笔记内容</label><textarea id="f-content" placeholder="记录交易思路、执行情况…"></textarea></div>
    <div style="margin-top:10px"><label for="f-lesson">经验教训</label><textarea id="f-lesson" rows="3" placeholder="从这笔交易中学到的…"></textarea></div>
    <div class="grid" style="margin-top:10px">
      <div><label for="f-tags">标签（逗号分隔）</label><input id="f-tags" placeholder="止损, 追涨, 波段"></div>
      <div><label for="f-rating">评分 (1-5)</label><select id="f-rating"><option value="">不评分</option><option value="1">1 ★</option><option value="2">2 ★★</option><option value="3">3 ★★★</option><option value="4">4 ★★★★</option><option value="5">5 ★★★★★</option></select></div>
    </div>
    <div class="grid3" style="margin-top:10px">
      <div><label for="f-direction">方向</label><select id="f-direction"><option value="">--</option><option value="buy">买入</option><option value="sell">卖出</option><option value="short">做空</option><option value="cover">平仓</option></select></div>
      <div><label for="f-entry">买入价</label><input id="f-entry" type="number" step="0.01" placeholder="0.00"></div>
      <div><label for="f-exit">卖出价</label><input id="f-exit" type="number" step="0.01" placeholder="0.00"></div>
    </div>
    <div class="grid" style="margin-top:10px">
      <div><label for="f-pnl">盈亏 (¥)</label><input id="f-pnl" type="number" step="0.01" placeholder="0.00"></div>
      <div><label for="f-strategy">策略ID</label><input id="f-strategy" placeholder="可选"></div>
    </div>
    <div class="row" style="margin-top:14px">
      <button class="btn btn-primary" onclick="saveNote()">保存</button>
      <button class="btn btn-danger" id="btn-delete" style="display:none" onclick="deleteNote()">删除</button>
      <button class="btn" onclick="resetForm()">取消</button>
    </div>
    <input type="hidden" id="f-id" value="">
  </section>

  <section class="card">
    <div class="stats" id="statsRow">
      <div class="stat"><div class="label">总笔记</div><div class="value" id="st-total">--</div></div>
      <div class="stat"><div class="label">平均评分</div><div class="value" id="st-rating">--</div></div>
      <div class="stat"><div class="label">本月笔记</div><div class="value" id="st-month">--</div></div>
      <div class="stat"><div class="label">标签数</div><div class="value" id="st-tags">--</div></div>
    </div>
    <div class="top-tags" id="topTags"></div>
    <div class="filters">
      <div><label>搜索</label><input id="q-search" placeholder="关键词、股票代码、标签…" oninput="loadNotes()"></div>
      <div><label>日期</label><input id="q-date" type="date" onchange="loadNotes()"></div>
      <div><label>标签</label><select id="q-tag" onchange="loadNotes()"><option value="">全部标签</option></select></div>
      <div><label>评分</label><select id="q-rating" onchange="loadNotes()"><option value="">全部评分</option><option value="5">5 ★★★★★</option><option value="4">4+ ★★★★</option><option value="3">3+ ★★★</option><option value="2">2+ ★★</option><option value="1">1+ ★</option></select></div>
    </div>
    <div class="toolbar">
      <span class="muted" id="resultCount"></span>
    </div>
    <table>
      <thead><tr><th>日期</th><th>代码</th><th>方向</th><th>评分</th><th>盈亏</th><th>标签</th><th>内容摘要</th></tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    <div class="empty" id="emptyMsg" style="display:none">暂无笔记，点击「新建笔记」开始记录</div>
  </section>
</main>

<script>
const B = '${b}';
const API = B + '/quant/admin/api/journal';
let allNotes = [];
let editingId = null;

function $(id){ return document.getElementById(id); }

async function fetchJSON(url, opts){
  const r = await fetch(url, opts);
  return r.json();
}

async function loadNotes(){
  const params = new URLSearchParams();
  const q = $('q-search').value.trim();
  const date = $('q-date').value;
  const tag = $('q-tag').value;
  const rating = $('q-rating').value;
  if(q) params.set('query', q);
  if(date) params.set('date', date);
  if(tag) params.set('tag', tag);
  if(rating) params.set('minRating', rating);
  const data = await fetchJSON(API + '?' + params.toString());
  allNotes = data.notes || [];
  renderTable();
  renderStats(data.summary);
  renderTags(data.tags || []);
}

function renderTable(){
  const tbody = $('tbody');
  if(!allNotes.length){
    tbody.innerHTML = '';
    $('emptyMsg').style.display = '';
    $('resultCount').textContent = '';
    return;
  }
  $('emptyMsg').style.display = 'none';
  $('resultCount').textContent = allNotes.length + ' 条笔记';
  const dirMap = {buy:'买入',sell:'卖出',short:'做空',cover:'平仓'};
  tbody.innerHTML = allNotes.map(n => {
    const stars = n.rating ? '★'.repeat(n.rating) + '☆'.repeat(5 - n.rating) : '--';
    const pnlStr = n.pnl != null ? (n.pnl >= 0 ? '+' : '') + n.pnl.toFixed(2) : '--';
    const pnlCls = n.pnl > 0 ? 'pnl-pos' : n.pnl < 0 ? 'pnl-neg' : '';
    const tags = (n.tags||[]).map(t => '<span class="pill">'+esc(t)+'</span>').join('');
    const summary = esc((n.content||'').slice(0, 60)) + (n.content && n.content.length > 60 ? '…' : '');
    return '<tr onclick="editNote(\\''+n.id+'\\')"><td>'+esc(n.date)+'</td><td>'+esc(n.symbol)+'</td><td>'+(dirMap[n.direction]||'--')+'</td><td class="stars">'+stars+'</td><td class="'+pnlCls+'">'+pnlStr+'</td><td>'+tags+'</td><td>'+summary+'</td></tr>';
  }).join('');
}

function renderStats(s){
  if(!s) return;
  $('st-total').textContent = s.totalNotes;
  $('st-rating').textContent = s.avgRating ? s.avgRating.toFixed(1) + ' ★' : '--';
  const thisMonth = new Date().toISOString().slice(0,7);
  $('st-month').textContent = (s.notesByMonth && s.notesByMonth[thisMonth]) || 0;
  $('st-tags').textContent = s.topTags ? s.topTags.length : 0;
}

function renderTags(tags){
  const sel = $('q-tag');
  const cur = sel.value;
  sel.innerHTML = '<option value="">全部标签</option>' + tags.map(t => '<option value="'+esc(t)+'">'+esc(t)+'</option>').join('');
  sel.value = cur;
  const container = $('topTags');
  container.innerHTML = tags.slice(0, 15).map(t => '<button class="tag-btn" onclick="filterByTag(\\''+esc(t)+'\\')">'+esc(t)+'</button>').join('');
}

function filterByTag(t){
  $('q-tag').value = t;
  loadNotes();
}

function esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function resetForm(){
  editingId = null;
  $('form-title').textContent = '新增笔记';
  $('f-id').value = '';
  $('f-symbol').value = '';
  $('f-date').value = new Date().toISOString().slice(0,10);
  $('f-content').value = '';
  $('f-lesson').value = '';
  $('f-tags').value = '';
  $('f-rating').value = '';
  $('f-direction').value = '';
  $('f-entry').value = '';
  $('f-exit').value = '';
  $('f-pnl').value = '';
  $('f-strategy').value = '';
  $('btn-delete').style.display = 'none';
}

function editNote(id){
  const n = allNotes.find(x => x.id === id);
  if(!n) return;
  editingId = id;
  $('form-title').textContent = '编辑笔记 — ' + n.id;
  $('f-id').value = n.id;
  $('f-symbol').value = n.symbol || '';
  $('f-date').value = n.date || '';
  $('f-content').value = n.content || '';
  $('f-lesson').value = n.lesson || '';
  $('f-tags').value = (n.tags||[]).join(', ');
  $('f-rating').value = n.rating != null ? String(n.rating) : '';
  $('f-direction').value = n.direction || '';
  $('f-entry').value = n.entryPrice != null ? n.entryPrice : '';
  $('f-exit').value = n.exitPrice != null ? n.exitPrice : '';
  $('f-pnl').value = n.pnl != null ? n.pnl : '';
  $('f-strategy').value = n.strategyId || '';
  $('btn-delete').style.display = '';
  $('formCard').scrollIntoView({behavior:'smooth',block:'start'});
}

async function saveNote(){
  const symbol = $('f-symbol').value.trim();
  if(!symbol){ alert('请输入股票代码'); return; }
  const content = $('f-content').value.trim();
  if(!content){ alert('请输入笔记内容'); return; }

  const tags = $('f-tags').value.split(/[,，]/).map(s=>s.trim()).filter(Boolean);
  const ratingVal = $('f-rating').value;
  const rating = ratingVal ? Number(ratingVal) : undefined;
  const direction = $('f-direction').value || undefined;
  const entryPrice = $('f-entry').value ? Number($('f-entry').value) : undefined;
  const exitPrice = $('f-exit').value ? Number($('f-exit').value) : undefined;
  const pnl = $('f-pnl').value ? Number($('f-pnl').value) : undefined;
  const strategyId = $('f-strategy').value.trim() || undefined;
  const date = $('f-date').value || new Date().toISOString().slice(0,10);
  const lesson = $('f-lesson').value.trim() || undefined;

  if(editingId){
    await fetchJSON(API, {
      method: 'PATCH',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ id: editingId, content, tags, rating, lesson })
    });
  } else {
    await fetchJSON(API, {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ symbol, date, content, tags, rating, direction, entryPrice, exitPrice, pnl, strategyId, lesson })
    });
  }
  resetForm();
  loadNotes();
}

async function deleteNote(){
  if(!editingId) return;
  if(!confirm('确定删除此笔记？')) return;
  await fetchJSON(API, {
    method: 'DELETE',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ id: editingId })
  });
  resetForm();
  loadNotes();
}

function exportCsv(){
  if(!allNotes.length){ alert('没有数据可导出'); return; }
  const headers = ['ID','日期','代码','方向','评分','盈亏','标签','内容','经验教训','策略','买入价','卖出价','创建时间'];
  const rows = allNotes.map(n => [
    n.id, n.date, n.symbol, n.direction||'', n.rating||'', n.pnl!=null?n.pnl:'',
    (n.tags||[]).join(';'), '"'+(n.content||'').replace(/"/g,'""')+'"',
    '"'+(n.lesson||'').replace(/"/g,'""')+'"',
    n.strategyId||'', n.entryPrice||'', n.exitPrice||'', n.createdAt||''
  ]);
  const csv = [headers.join(','), ...rows.map(r=>r.join(','))].join('\\n');
  const blob = new Blob([csv], {type:'text/csv;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url; a.download = 'trade-journal-'+new Date().toISOString().slice(0,10)+'.csv';
  a.click(); URL.revokeObjectURL(url);
}

// Init
resetForm();
loadNotes();
</script>
</body>
</html>`;
}
