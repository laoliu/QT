/**
 * Notification Config Page (UI-9) — 000407
 *
 * 管理: 090108Bark、PushPlus、Webhook 等推送渠道
 */
export function getNotificationConfigPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 0004</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>90</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922}
*{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text)}
a{color:var(--accent);text-decoration:none}a:hover{text-decoration:underline}
.breadcrumb{background:var(--bg);padding:8px 24px;font-size:12px;border-bottom:1px solid var(--border)}
header{display:flex;justify-content:space-between;align-items:center;padding:16px 24px;background:var(--bg2);border-bottom:1px solid var(--border)}
header h1{margin:0;font-size:20px}
.actions{display:flex;gap:8px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg3);color:var(--text);cursor:pointer;font-size:13px}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-danger{background:#da3633;border-color:#f85149;color:#fff}
main{padding:20px 24px;display:grid;grid-template-columns:380px 1fr;gap:16px}
.card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{margin:0 0 12px;font-size:15px}
label{font-size:12px;color:var(--text-dim);display:block;margin-bottom:4px;margin-top:10px}
input,select{width:100%;background:var(--bg3);border:1px solid var(--border);border-radius:8px;color:var(--text);padding:8px 10px;font-size:13px}
.form-actions{display:flex;gap:8px;margin-top:16px}
.channel-list{display:flex;flex-direction:column;gap:10px}
.channel-card{background:var(--bg3);border:1px solid var(--border);border-radius:10px;padding:12px;display:flex;justify-content:space-between;align-items:center}
.channel-info{flex:1}
.channel-name{font-weight:600;font-size:14px}.channel-type{color:var(--text-dim);font-size:12px}
.channel-url{color:var(--text-dim);font-size:11px;word-break:break-all;margin-top:4px}
.channel-actions{display:flex;gap:6px;align-items:center}
.toggle{background:none;border:none;cursor:pointer;font-size:16px;padding:4px}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px}
.pill-on{background:rgba(63,185,80,.15);color:var(--green)}.pill-off{background:rgba(139,148,158,.15);color:var(--text-dim)}
.empty{padding:24px;text-align:center;color:var(--text-dim)}
.toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(60px);background:var(--bg3);color:var(--text);padding:10px 20px;border-radius:8px;border:1px solid var(--border);font-size:13px;opacity:0;pointer-events:none;transition:transform .3s,opacity .3s;z-index:9999}
.toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.test-result{margin-top:8px;padding:8px;border-radius:6px;font-size:12px;display:none}
@media(max-width:960px){main{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="breadcrumb"><a href="${b}/quant/">92 07</a> / <a href="${b}/quant/admin/ui">99 管理面板</a> / <span>90 0004</span></div>
<header>
  <h1>90 0004</h1>
  <div class="actions">
    <a class="btn" href="${b}/quant/admin/ui">返回管理面板</a>
  </div>
</header>
<main>
  <section class="card">
    <h2 id="form-title">0004</h2>
    <label for="ch-type">渠道类型</label>
    <select id="ch-type" onchange="onTypeChange()">
      <option value="wechat_work">0901</option>
      <option value="dingtalk">钉钉</option>
      <option value="bark">Bark (iOS)</option>
      <option value="pushplus">PushPlus</option>
      <option value="webhook">00 Webhook</option>
    </select>

    <label for="ch-name">名称</label>
    <input id="ch-name" placeholder="例如: 090901">

    <label for="ch-url">Webhook URL</label>
    <input id="ch-url" placeholder="https://...">

    <div id="secret-group">
      <label for="ch-secret">Secret/Token (05)</label>
      <input id="ch-secret" placeholder="0507 Token">
    </div>

    <label for="ch-min-severity">最低推送级别</label>
    <select id="ch-min-severity">
      <option value="info">00 (info)</option>
      <option value="warning">警告及以上 (warning)</option>
      <option value="critical">仅严重 (critical)</option>
    </select>

    <label>推送类别 (05)</label>
    <div style="display:flex;gap:12px;margin-top:4px">
      <label style="font-size:13px;color:var(--text)"><input type="checkbox" class="cat-cb" value="trade" checked> 交易</label>
      <label style="font-size:13px;color:var(--text)"><input type="checkbox" class="cat-cb" value="alert" checked> 澯</label>
      <label style="font-size:13px;color:var(--text)"><input type="checkbox" class="cat-cb" value="report" checked> 报告</label>
      <label style="font-size:13px;color:var(--text)"><input type="checkbox" class="cat-cb" value="system"> 0301</label>
    </div>

    <div class="form-actions">
      <button class="btn btn-primary" onclick="saveChannel()">保存</button>
      <button class="btn" onclick="testChannel()">发送测试</button>
      <button class="btn" onclick="resetForm()">重置</button>
    </div>
    <div class="test-result" id="test-result"></div>
  </section>

  <section class="card">
    <h2>已配置渠道 <span id="channel-count" style="color:var(--text-dim)">(0)</span></h2>
    <div id="channel-list" class="channel-list"><div class="empty">暂无配置</div></div>
  </section>
</main>
<div class="toast" id="toast"></div>

<script>
const B='${b}';
let channels=[];
let editIndex=-1;

function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function toast(msg,ms){ms=ms||2500;const el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');setTimeout(()=>el.classList.remove('show'),ms)}

function onTypeChange(){
  const t=document.getElementById('ch-type').value;
  const sg=document.getElementById('secret-group');
  sg.style.display=(t==='dingtalk'||t==='webhook'||t==='pushplus'||t==='bark')?'block':'none';
}

function resetForm(){
  editIndex=-1;
  document.getElementById('form-title').textContent='0004';
  document.getElementById('ch-type').value='wechat_work';
  document.getElementById('ch-name').value='';
  document.getElementById('ch-url').value='';
  document.getElementById('ch-secret').value='';
  document.getElementById('ch-min-severity').value='info';
  document.querySelectorAll('.cat-cb').forEach(cb=>cb.checked=cb.value!=='system');
  document.getElementById('test-result').style.display='none';
  onTypeChange();
}

function renderChannels(){
  document.getElementById('channel-count').textContent='('+channels.length+')';
  if(channels.length===0){
    document.getElementById('channel-list').innerHTML='<div class="empty">010004</div>';
    return;
  }
  let h='';
  channels.forEach((ch,i)=>{
    const statusPill=ch.enabled?'<span class="pill pill-on">启用</span>':'<span class="pill pill-off">禁用</span>';
    const typeLabels={wechat_work:'0901',dingtalk:'钉钉',bark:'Bark',pushplus:'PushPlus',webhook:'Webhook'};
    h+='<div class="channel-card">';
    h+='<div class="channel-info">';
    h+='<div class="channel-name">'+esc(ch.name||typeLabels[ch.type]||ch.type)+' '+statusPill+'</div>';
    h+='<div class="channel-type">'+esc(typeLabels[ch.type]||ch.type)+' | 级别: '+esc(ch.minSeverity||'info')+'</div>';
    h+='<div class="channel-url">'+esc(ch.url?ch.url.slice(0,60)+'...':'')+'</div>';
    h+='</div>';
    h+='<div class="channel-actions">';
    h+='<button class="btn" style="padding:4px 8px;font-size:11px" onclick="editChannel('+i+')">29</button>';
    h+='<button class="toggle" onclick="toggleChannel('+i+')" title="'+(ch.enabled?'禁用':'启用')+'">'+(ch.enabled?'08':'72')+'</button>';
    h+='<button class="btn btn-danger" style="padding:4px 8px;font-size:11px" onclick="removeChannel('+i+')">01</button>';
    h+='</div></div>';
  });
  document.getElementById('channel-list').innerHTML=h;
}

async function loadChannels(){
  try{
    const res=await fetch(B+'/quant/api/notification/channels');
    if(res.ok){channels=await res.json();renderChannels()}
  }catch(e){
    // If API not available, use local storage as fallback
    const saved=localStorage.getItem('qt_notification_channels');
    if(saved){channels=JSON.parse(saved);renderChannels()}
  }
}

function getFormData(){
  const cats=[...document.querySelectorAll('.cat-cb:checked')].map(cb=>cb.value);
  return{
    type:document.getElementById('ch-type').value,
    name:document.getElementById('ch-name').value.trim(),
    url:document.getElementById('ch-url').value.trim(),
    secret:document.getElementById('ch-secret').value.trim()||undefined,
    token:document.getElementById('ch-secret').value.trim()||undefined,
    enabled:true,
    minSeverity:document.getElementById('ch-min-severity').value,
    categories:cats.length>0?cats:undefined,
  };
}

async function saveChannel(){
  const data=getFormData();
  if(!data.url){toast('请输入 Webhook URL');return}

  try{
    const res=await fetch(B+'/quant/api/notification/channels',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)
    });
    if(res.ok){toast('保存成功');loadChannels();resetForm();return}
  }catch(e){}

  // Fallback: save locally
  if(editIndex>=0){channels[editIndex]=data}else{channels.push(data)}
  localStorage.setItem('qt_notification_channels',JSON.stringify(channels));
  renderChannels();resetForm();toast('01(本地)');
}

async function testChannel(){
  const data=getFormData();
  if(!data.url){toast('请输入 Webhook URL');return}
  const resultEl=document.getElementById('test-result');
  resultEl.style.display='block';
  resultEl.style.background='rgba(88,166,255,.1)';
  resultEl.textContent='发送中...';

  try{
    const res=await fetch(B+'/quant/api/notification/test',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)
    });
    if(res.ok){
      const r=await res.json();
      if(r.success){
        resultEl.style.background='rgba(63,185,80,.1)';
        resultEl.textContent='73 090106';
      }else{
        resultEl.style.background='rgba(248,81,73,.1)';
        resultEl.textContent='74 02: '+(r.error||'ā04');
      }
    }else{
      resultEl.style.background='rgba(248,81,73,.1)';
      resultEl.textContent='74 API 请求失败';
    }
  }catch(e){
    resultEl.style.background='rgba(248,81,73,.1)';
    resultEl.textContent='74 '+e.message;
  }
}

function editChannel(idx){
  const ch=channels[idx];
  editIndex=idx;
  document.getElementById('form-title').textContent='编辑渠道';
  document.getElementById('ch-type').value=ch.type;
  document.getElementById('ch-name').value=ch.name||'';
  document.getElementById('ch-url').value=ch.url||'';
  document.getElementById('ch-secret').value=ch.secret||ch.token||'';
  document.getElementById('ch-min-severity').value=ch.minSeverity||'info';
  document.querySelectorAll('.cat-cb').forEach(cb=>{
    cb.checked=ch.categories?ch.categories.includes(cb.value):cb.value!=='system';
  });
  onTypeChange();
}

function toggleChannel(idx){
  channels[idx].enabled=!channels[idx].enabled;
  localStorage.setItem('qt_notification_channels',JSON.stringify(channels));
  renderChannels();

  // Try server update
  fetch(B+'/quant/api/notification/channels',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(channels[idx])
  }).catch(()=>{});
}

function removeChannel(idx){
  if(!confirm('确认删除此渠道?'))return;
  channels.splice(idx,1);
  localStorage.setItem('qt_notification_channels',JSON.stringify(channels));
  renderChannels();
  toast('01');
}

onTypeChange();
loadChannels();
</script>
</body>
</html>`;
}
