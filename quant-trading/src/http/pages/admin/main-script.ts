/**
 * Admin Management Page — main browser-side JavaScript.
 *
 * Returns the full <script> body as a string. The caller injects the three
 * dynamic variables (B, _T, _GT) before concatenating.
 */
export function getAdminMainScript(): string {
  return `
function esc(s){return String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}

/* ── Tabs (with hash-based routing for browser back/forward) ── */
function activateTab(name){
  document.querySelectorAll('.tab').forEach(function(x){x.classList.remove('active')});
  document.querySelectorAll('.tab-content').forEach(function(x){x.classList.remove('active')});
  var tab=document.querySelector('.tab[data-tab="'+name+'"]');
  var content=document.getElementById('tab-'+name);
  if(tab)tab.classList.add('active');
  if(content)content.classList.add('active');
  loadTab(name);
}
document.querySelectorAll('.tab').forEach(function(t){
  t.addEventListener('click',function(){
    var tab=t.dataset.tab;
    activateTab(tab);
    if(location.hash!=='#'+tab) history.pushState(null,null,'#'+tab);
  });
});
window.addEventListener('popstate',function(){
  var h=(location.hash||'').replace('#','');
  activateTab(h||'overview');
});
(function(){
  var h=(location.hash||'').replace('#','');
  if(h) activateTab(h);
})();
function toast(msg,ms){
  ms=ms||2500;var el=document.getElementById('toast');el.textContent=msg;el.classList.add('show');
  setTimeout(function(){el.classList.remove('show')},ms);
}
function api(path,opts){
  opts=opts||{};
  var h={'Content-Type':'application/json'};
  if(_T)h['Authorization']='Bearer '+_T;
  var ac=new AbortController();
  var tid=setTimeout(function(){ac.abort()},opts.timeout||15000);
  return fetch(B+path,Object.assign({headers:h,signal:ac.signal},opts))
    .then(function(r){clearTimeout(tid);if(!r.ok)throw new Error(r.status+' '+r.statusText);return r.json()})
    .catch(function(e){clearTimeout(tid);var msg=e.name==='AbortError'?'请求超时':e.message;toast('❌ '+msg);throw e});
}
setInterval(function(){document.getElementById('clock').textContent=new Date().toLocaleString('zh-CN')},1000);
// Set default end date for kline query to today
(function(){var el=document.getElementById('kline-end');if(el)el.value=new Date().toISOString().slice(0,10)})();
function loadTab(name){
  var m={overview:loadOverview,services:loadServices,data:loadData,alerts:loadAlerts,trading:loadTrading,strategies:loadStrategies,sectors:loadSectors,tasks:loadTasks,logs:loadLogs,chat:loadChat,'ai-chat':loadAiChat,config:loadConfig};
  if(m[name])m[name]();
}

/* ══════ Shared Helpers ══════ */
function card(title,inner){return '<div class="card"><h3>'+title+'</h3>'+inner+'</div>'}
function infoList(obj){
  var html='<ul class="info-list">';
  for(var k in obj)if(obj.hasOwnProperty(k))html+='<li><span class="label">'+esc(k)+'</span><span class="value">'+obj[k]+'</span></li>';
  return html+'</ul>';
}
function dotSt(running){return '<span class="dot '+(running?'dot-green':'dot-red')+'"></span>'+(running?'运行中':'已停止')}
function btnGrp(btns){return '<div class="btn-group">'+btns.map(function(b){return '<button class="btn '+b[0]+'"'+(b[3]?' disabled':'')+' onclick="'+b[1]+'">'+b[2]+'</button>'}).join('')+'</div>'}
function num(v){return v!=null?Number(v).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}):'-'}
function kpiHtml(val,label){return '<div class="kpi"><div class="kpi-val">'+val+'</div><div class="kpi-label">'+esc(label)+'</div></div>'}
function sevBadge(s){return '<span class="badge badge-'+(s==='critical'?'red':s==='warning'?'yellow':'blue')+'">'+esc(s)+'</span>'}
function fmtTime(ts){return ts?new Date(ts).toLocaleString('zh-CN'):'-'}

/* ══════ OVERVIEW ══════ */
function loadOverview(){
  api('/quant/admin/api/overview',{timeout:30000}).then(function(d){
    var kpi=document.getElementById('kpi-row');
    if(!kpi)return;
    kpi.innerHTML=[
      kpiHtml(esc(d.gateway.uptime),'运行时间'),
      kpiHtml(d.gateway.memoryMB+' MB','内存占用'),
      kpiHtml(dotSt(d.receiver.running),'RECEIVER'),
      kpiHtml('<span class="badge '+(d.engine.status==='running'?'badge-green':d.engine.status==='paused'?'badge-yellow':'badge-red')+'">'+esc(d.engine.status)+'</span>','实盘引擎'),
      kpiHtml(d.data.symbolCount,'数据品种'),
      kpiHtml(d.data.totalBars.toLocaleString(),'总K线数'),
      kpiHtml(esc(d.data.hitRate),'命中率'),
      kpiHtml(d.alerts.unacknowledged,'未确认告警'),
      kpiHtml(d.priceAlerts.active,'价格预警'),
      kpiHtml(d.scheduledTasks.pending,'待执行任务'),
      kpiHtml(d.strategies.builtin+' / '+d.strategies.user,'策略 内置/自定'),
    ].join('');
  }).catch(function(e){
    console.error('[overview] KPI load failed:', e);
    var kpi=document.getElementById('kpi-row');
    if(kpi)kpi.innerHTML='<div style="color:var(--text-dim);font-size:13px">⚠️ 概览数据加载失败，将自动重试</div>';
  });
  api('/quant/admin/api/events').then(function(ev){
    var box=document.getElementById('event-log');
    if(!box)return;
    var events=(ev&&ev.events)||[];
    box.innerHTML=events.slice(0,50).map(function(e){
      return '<div class="log-'+esc(e.category||'info')+'">['+new Date(e.timestamp).toLocaleTimeString('zh-CN')+'] <b>'+esc(e.category)+'</b> '+esc(typeof e.data==='string'?e.data:JSON.stringify(e.data))+'</div>';
    }).join('')||'<div style="color:var(--text-dim)">暂无事件</div>';
  }).catch(function(e){
    console.error('[overview] events load failed:', e);
    var el=document.getElementById('event-log');
    if(el)el.innerHTML='<div style="color:var(--text-dim)">⚠️ 事件加载失败，将自动重试</div>';
  });
  api('/quant/admin/api/alerts').then(function(al){
    var ra=document.getElementById('recent-alerts');
    if(!ra)return;
    var alerts=(al&&al.alerts)||[];
    ra.innerHTML=alerts.slice(0,10).map(function(a){
      return '<div style="padding:4px 0;border-bottom:1px solid var(--border);font-size:12px">'+
        sevBadge(a.severity)+' <span style="color:var(--text-dim)">'+fmtTime(a.timestamp)+'</span> '+esc(a.message)+'</div>';
    }).join('')||'<div style="color:var(--text-dim);font-size:12px">暂无告警</div>';
  }).catch(function(e){
    console.error('[overview] alerts load failed:', e);
    var el=document.getElementById('recent-alerts');
    if(el)el.innerHTML='<div style="color:var(--text-dim);font-size:12px">⚠️ 告警加载失败，将自动重试</div>';
  });
}

/* ══════ SERVICES ══════ */
function loadServices(){
  api('/quant/admin/api/services').then(function(d){
    var c=document.getElementById('svc-cards');c.innerHTML='';
    if(!d||!d.gateway){c.innerHTML='<div style="color:var(--text-dim)">⚠️ 服务数据异常</div>';return;}
    var gw=d.gateway;
    c.innerHTML+=card('🌐 网关服务',infoList({'状态':dotSt(gw.running),'PID':gw.pid,'端口':gw.port,'版本':gw.version,'运行时间':gw.uptime}));
    var rc=d.receiver;
    c.innerHTML+=card('📡 RECEIVER 桥接',infoList({'状态':dotSt(rc.running),'PID':rc.pid||'-','端口':rc.port,'主机':esc(rc.host),'重启次数':rc.restartCount,'启动':rc.startedAt?fmtTime(rc.startedAt):'-'})+
      btnGrp([['btn-primary','svcAction("receiver","start")','▶ 启动',rc.running],['btn-danger','svcAction("receiver","stop")','⏹ 停止',!rc.running],['','svcAction("receiver","restart")','🔄 重启',!rc.running]]));
    if(d.bridgeHealth){var bh=d.bridgeHealth;
      c.innerHTML+=card('🔗 桥接状态',infoList({'连接':dotSt(bh.bridge_connected),'待处理':bh.pending_requests,'上次轮询':bh.bridge_last_poll_age!=null?bh.bridge_last_poll_age.toFixed(1)+'s':'-'}));}
    var eng=d.engine;
    var eRun=eng.status==='running',ePause=eng.status==='paused',eStop=eng.status==='stopped';
    c.innerHTML+=card('🤖 实盘引擎',infoList({'状态':'<span class="badge '+(eRun?'badge-green':ePause?'badge-yellow':'badge-red')+'">'+esc(eng.status)+'</span>','活跃策略':eng.activeStrategies,'持仓数':eng.positions})+
      btnGrp([['btn-primary','svcAction("engine","start")','▶ 启动',eRun||ePause],['btn-danger','svcAction("engine","stop")','⏹ 停止',eStop],['','svcAction("engine","pause")','⏸ 暂停',!eRun],['','svcAction("engine","resume")','▶️ 恢复',!ePause]]));
    (d.services||[]).forEach(function(svc){c.innerHTML+=card('📌 '+esc(svc.id),infoList({'服务ID':svc.id,'状态':'<span class="dot dot-green"></span>已注册'}));});
  }).catch(function(){document.getElementById('svc-cards').innerHTML='<div style="color:var(--text-dim)">⚠️ 服务信息加载失败</div>'});
}
function svcAction(service,action){
  toast('⏳ 执行中…');
  api('/quant/admin/api/service/'+service+'/'+action,{method:'POST'}).then(function(r){
    toast('✅ '+(r.message||'成功'));setTimeout(loadServices,800);
  });
}

/* ══════ DATA ══════ */
function loadData(){
  api('/quant/admin/api/data').then(function(d){
    var c=document.getElementById('data-cards');c.innerHTML='';
    c.innerHTML+=card('🔌 数据源',infoList({'当前数据源':'<span class="badge badge-blue">'+esc(d.provider)+'</span>','Python':'<span style="font-size:11px;word-break:break-all">'+esc(d.pythonPath)+'</span>'})+
      '<table style="margin-top:8px"><thead><tr><th>数据源</th><th>说明</th><th>状态</th></tr></thead><tbody>'+
      d.availableProviders.map(function(p){return '<tr><td>'+esc(p.id)+'</td><td style="font-size:11px">'+esc(p.description)+'</td><td>'+(p.id===d.provider?'<span class="badge badge-green">使用中</span>':'<span class="badge badge-yellow">可用</span>')+'</td></tr>'}).join('')+'</tbody></table>');
    var pc=d.pythonCache||{};var kl=pc.klines||{};
    c.innerHTML+=card('💾 缓存概览 (SQLite)',infoList({
      '数据库大小':(pc.db_size_mb||0)+' MB',
      'K线总量':(kl.total_bars||0).toLocaleString(),
      '品种数':kl.unique_symbols||0,
      '股票列表':((pc.stock_list||{}).unique_stocks||0)+' 只',
      '基本面':((pc.fundamentals||{}).unique_stocks||0)+' 只',
      '代码名称映射':((pc.code_name_map||{}).entries||0)+' 条'
    })+btnGrp([['','refreshWarehouseStats()','🔄 刷新统计']]));
    c.innerHTML+=card('🌉 QMT 桥接配置',infoList({'RECEIVER端口':d.receiverPort,'主机':d.receiverHost,'自动启动':d.receiverEnabled?'✅ 是':'❌ 否'}));
    var symbolList=d.pythonSymbols||[];
    var tbody=document.querySelector('#cache-table tbody');
    tbody.innerHTML=symbolList.map(function(s){
      return '<tr><td><a href="#" onclick="openStockDetail(\\''+esc(s.symbol)+'\\');return false" style="color:var(--blue);cursor:pointer">'+esc(s.symbol)+'</a></td><td>'+esc(s.interval)+'</td><td>'+s.bars.toLocaleString()+'</td><td>'+(s.dateRange?esc(s.dateRange.min)+' ~ '+esc(s.dateRange.max):'-')+
        '</td><td><button class="btn btn-sm btn-danger" onclick="invalidateCache(\\''+esc(s.symbol)+'\\',\\''+esc(s.interval)+'\\')">清除</button></td></tr>';
    }).join('')||'<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">暂无缓存</td></tr>';
  }).catch(function(){document.getElementById('data-cards').innerHTML='<div style="color:var(--text-dim)">⚠️ 数据信息加载失败</div>'});
  // Auto-check bridge status
  api('/quant/admin/api/data-warehouse/bridge-status').then(function(r){
    if(!r||r.status==='error')return;
    var b=r.bridge||{};
    document.getElementById('sync-bridge-status').innerHTML='迅投桥接: '+(b.online?'<span class="badge badge-green">在线</span> '+esc(b.url||''):'<span class="badge badge-red">离线</span> (将使用 AKShare)');
  }).catch(function(){});
}
function refreshWarehouseStats(){toast('🔄 刷新…');loadData()}
function openStockDetail(sym){
  var s=sym||(document.getElementById('stock-detail-sym').value||'').trim();
  if(!s){toast('⚠️ 请输入股票代码');return}
  window.open(B+'/quant/admin/stock?symbol='+encodeURIComponent(s),'_blank');
}
function checkSync(){
  toast('🔍 检查数据更新…');
  api('/quant/admin/api/data-warehouse/sync').then(function(r){
    var el=document.getElementById('sync-result');
    if(r.status==='error'){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.message||'检查失败')+'</span>';return}
    var b=r.bridge||{};
    document.getElementById('sync-bridge-status').innerHTML='迅投桥接: '+(b.online?'<span class="badge badge-green">在线</span> '+esc(b.url||''):'<span class="badge badge-red">离线</span>');
    var html='<div>📊 总品种: <b>'+r.total+'</b>  ✅ 已最新: <b>'+r.fresh+'</b>  ⏳ 过期: <b>'+r.stale+'</b>  🆕 无缓存: <b>'+r.no_cache+'</b></div>';
    html+='<div>需同步: <b>'+r.need_fetch+'</b> ֻ</div>';
    el.innerHTML=html;
    if(r.need_fetch===0)toast('✅ 所有数据已是最新');
  });
}
function doSync(){
  var source=document.getElementById('sync-source').value;
  var maxStocks=parseInt(document.getElementById('sync-max').value)||0;
  toast('📥 正在同步数据… (可能需要几分钟)');
  var el=document.getElementById('sync-result');
  el.innerHTML='<div style="color:var(--text-dim)">⏳ 同步进行中，请耐心等待…</div>';
  api('/quant/admin/api/data-warehouse/sync',{method:'POST',body:JSON.stringify({source:source,max_stocks:maxStocks})}).then(function(r){
    if(r.status==='error'){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.message||'同步失败')+'</span>';toast('❌ 同步失败');return}
    var b=r.bridge||{};
    document.getElementById('sync-bridge-status').innerHTML='迅投桥接: '+(b.online?'<span class="badge badge-green">在线</span>':'<span class="badge badge-red">离线</span>');
    var html='<div>✅ 同步完成 (通道: <b>'+esc(r.source)+'</b>)</div>';
    html+='<div>成功: <b>'+r.fetched+'</b>  失败: <b>'+r.failed+'</b>  跳过: <b>'+r.skipped+'</b>  已最新: <b>'+r.fresh+'</b></div>';
    if(r.message)html+='<div>'+esc(r.message)+'</div>';
    el.innerHTML=html;
    toast('✅ 同步完成: '+r.fetched+' 只已更新');
    loadData();
  }).catch(function(err){
    el.innerHTML='<span style="color:var(--red)">❌ 请求超时或失败: '+esc(String(err.message||err))+'</span>';
    toast('❌ 同步请求失败');
  });
}
function invalidateCache(symbol,interval){
  if(!confirm('确认清除 '+symbol+' ('+interval+') 缓存？'))return;
  api('/quant/admin/api/data-warehouse/invalidate',{method:'POST',body:JSON.stringify({symbol:symbol,interval:interval})}).then(function(){toast('✅ 已清除');loadData()});
}
function doWarmup(){
  var symbols=document.getElementById('warmup-symbols').value.split(',').map(function(s){return s.trim()}).filter(Boolean);
  var interval=document.getElementById('warmup-interval').value;
  var start=document.getElementById('warmup-start').value;
  var end=document.getElementById('warmup-end').value;
  if(!symbols.length||!start||!end){toast('请填写完整');return}
  toast('⏳ 预热中…');
  api('/quant/admin/api/data-warehouse/warmup',{method:'POST',body:JSON.stringify({symbols:symbols,interval:interval,start:start,end:end})}).then(function(r){
    document.getElementById('warmup-result').innerHTML=(r.results||[]).map(function(x){
      return '<div>'+esc(x.symbol)+': 获取 '+x.fetched+' 条, 缓存 '+x.cached+' 条'+(x.error?' <span style="color:var(--red)">'+esc(x.error)+'</span>':'')+'</div>';
    }).join('');toast('✅ 预热完成');loadData();
  });
}
function checkLocalData(){
  toast('🔍 检查离线数据…');
  var el=document.getElementById('local-data-result');
  api('/quant/admin/api/local-data/check').then(function(r){
    if(r.status==='error'){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.message||'检查失败')+'</span>';return}
    var d=r.data||r;
    var html='<div>📂 缓存月份: <b>'+d.cached_months+'</b>  最新月: <b>'+(d.latest_month||'无')+'</b></div>';
    if(d.stale_months&&d.stale_months.length){
      html+='<div style="color:var(--orange)">⏳ 需更新: <b>'+d.stale_months.length+'</b> 个月</div>';
      d.stale_months.forEach(function(m){html+='<div style="font-size:11px;margin-left:12px">'+esc(m.month)+' — '+esc(m.reason)+(m.latest_date?' (最新: '+m.latest_date+')':'')+'</div>'});
    }else{html+='<div style="color:var(--green)">✅ '+esc(d.message||'全部最新')+'</div>'}
    el.innerHTML=html;
    document.getElementById('local-data-status').innerHTML='💾 '+d.cached_months+' 月, 最新: '+(d.latest_month||'-');
  });
}
function syncLocalData(){
  toast('📥 正在补全离线数据… (可能需要较长时间)');
  var el=document.getElementById('local-data-result');
  el.innerHTML='<div style="color:var(--text-dim)">⏳ 同步进行中，请耐心等待…</div>';
  api('/quant/admin/api/local-data/sync',{method:'POST',body:JSON.stringify({})}).then(function(r){
    if(r.status==='error'||r.ok===false){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.message||r.error||'同步失败')+'</span>';toast('❌ 同步失败');return}
    var html='<div>✅ 同步完成 (耗时 '+r.elapsed_seconds+'s)</div>';
    html+='<div>日线: <b>'+r.daily_months_synced+'</b> 月  复权因子: <b>'+r.adj_factor_months_synced+'</b> 月</div>';
    if(r.stock_basic)html+='<div>股票列表: '+esc(r.stock_basic.status)+(r.stock_basic.stocks?' ('+r.stock_basic.stocks+'只)':'')+'</div>';
    el.innerHTML=html;
    toast('✅ 离线数据同步完成');
  }).catch(function(err){
    el.innerHTML='<span style="color:var(--red)">❌ 请求超时或失败: '+esc(String(err.message||err))+'</span>';
    toast('❌ 同步请求失败');
  });
}
function queryKlines(){
  var symbol=document.getElementById('kline-symbol').value.trim();
  var interval=document.getElementById('kline-interval').value;
  var start=document.getElementById('kline-start').value;
  var end=document.getElementById('kline-end').value||new Date().toISOString().slice(0,10);
  if(!symbol){toast('请输入代码');return}
  if(!start){var d=new Date();d.setMonth(d.getMonth()-6);start=d.toISOString().slice(0,10)}
  toast('⏳ 查询中…');
  api('/quant/admin/api/query-klines?symbol='+encodeURIComponent(symbol)+'&interval='+interval+'&start='+encodeURIComponent(start)+'&end='+encodeURIComponent(end)).then(function(r){
    var bars=r.data||r.bars||r||[];
    var src=r.source==='cache'?'📦 缓存':'🌐 在线';
    if(!Array.isArray(bars)||!bars.length){document.getElementById('kline-result').innerHTML='<span style="color:var(--text-dim)">无数据</span>';return}
    var h='<div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">来源: '+src+' | 共 '+bars.length+' 条</div>';
    h+='<table><thead><tr><th>日期</th><th>开</th><th>高</th><th>低</th><th>收</th><th>量</th></tr></thead><tbody>';
    bars.slice(-50).forEach(function(b){
      var dt=b.date||b.timestamp;if(typeof dt==='number'){dt=new Date(dt).toISOString().slice(0,10)}
      h+='<tr><td>'+esc(dt)+'</td><td>'+num(b.open)+'</td><td>'+num(b.high)+'</td><td>'+num(b.low)+'</td><td>'+num(b.close)+'</td><td>'+(b.volume||0).toLocaleString()+'</td></tr>';
    });
    h+='</tbody></table>';
    if(bars.length>50)h+='<div style="font-size:11px;color:var(--text-dim);margin-top:4px">显示最近50条 / 共'+bars.length+'条</div>';
    document.getElementById('kline-result').innerHTML=h;toast('✅ '+src+' 查到 '+bars.length+' 条');
  });
}

/* ══════ ALERTS ══════ */
function loadAlerts(){
  api('/quant/admin/api/alerts').then(function(d){
    if(!d){return;}
    var s=d.summary||{};
    var c=document.getElementById('alert-summary-cards');
    c.innerHTML='<div class="kpi-row">'+[
      kpiHtml(s.total||0,'总告警'),kpiHtml(s.unacknowledged||0,'未确认'),
      kpiHtml((s.bySeverity&&s.bySeverity.critical)||0,'严重'),
      kpiHtml((s.bySeverity&&s.bySeverity.warning)||0,'警告'),
      kpiHtml((s.bySeverity&&s.bySeverity.info)||0,'信息'),
    ].join('')+'</div>';
    var tbody=document.querySelector('#alert-table tbody');
    tbody.innerHTML=d.alerts.map(function(a){
      return '<tr><td style="white-space:nowrap">'+fmtTime(a.timestamp)+'</td>'+
        '<td>'+sevBadge(a.severity)+'</td><td>'+esc(a.source)+'</td>'+
        '<td style="max-width:300px;word-break:break-all">'+esc(a.message)+'</td>'+
        '<td>'+(a.acknowledged?'<span class="badge badge-green">已确认</span>':'<span class="badge badge-yellow">待确认</span>')+'</td>'+
        '<td>'+(a.acknowledged?'':'<button class="btn btn-sm" onclick="ackAlert(\\''+esc(a.id)+'\\')">确认</button>')+'</td></tr>';
    }).join('')||'<tr><td colspan="6" style="text-align:center;color:var(--text-dim)">暂无告警</td></tr>';
  }).catch(function(){});
}
function ackAlert(id){api('/quant/api/alerts/acknowledge',{method:'POST',body:JSON.stringify({id:id})}).then(function(){toast('✅ 已确认');loadAlerts()})}
function ackAllAlerts(){api('/quant/api/alerts/acknowledge',{method:'POST',body:JSON.stringify({all:true})}).then(function(){toast('✅ 全部确认');loadAlerts()})}
function clearAlerts(){if(!confirm('确认清空所有告警？'))return;api('/quant/api/alerts',{method:'DELETE'}).then(function(){toast('✅ 已清空');loadAlerts()})}

/* ══════ TRADING ══════ */
var adminViewMode = '';
function switchAdminViewMode(mode){
  adminViewMode = mode;
  var sel=document.getElementById('adminModeSwitch');
  if(sel){sel.style.background=mode==='live'?'rgba(63,185,80,0.15)':'rgba(210,153,34,0.15)';sel.style.color=mode==='live'?'#3fb950':'#d29922'}
  loadTrading();
}
function adminModeParam(prefix){
  if(!adminViewMode) return '';
  return (prefix?'&':'?')+'mode='+adminViewMode;
}
function loadTrading(){
  // Inject mode switcher if not present
  var tc=document.getElementById('trading-summary-cards');
  if(!document.getElementById('adminModeSwitch')){
    tc.insertAdjacentHTML('beforebegin','<div style="margin-bottom:12px"><select id="adminModeSwitch" onchange="switchAdminViewMode(this.value)" style="padding:4px 12px;border-radius:6px;font-size:13px;font-weight:600;background:rgba(210,153,34,0.15);color:#d29922;border:1px solid rgba(210,153,34,0.3);cursor:pointer"><option value="">当前模式</option><option value="paper">🟡 模拟盘</option><option value="live">🟢 实盘</option></select></div>');
  }
  tc.innerHTML='';
  api('/quant/api/portfolio'+adminModeParam(false)).then(function(portfolio){
    if(portfolio&&portfolio.data){
      var p=portfolio.data;
      tc.innerHTML='<div class="kpi-row">'+[
        kpiHtml(num(p.totalValue),'总资产'),kpiHtml(num(p.profit),'总盈亏'),
        kpiHtml(p.profitPct!=null?(p.profitPct*100).toFixed(2)+'%':'-','盈亏率'),
        kpiHtml(num(p.cash),'可用资金'),
      ].join('')+'</div>';
      var pts=p.positions||[];
      document.querySelector('#positions-table tbody').innerHTML=pts.map(function(pos){
        return '<tr><td>'+esc(pos.symbol)+'</td><td>'+esc(pos.name||'')+'</td><td>'+esc(pos.direction||'long')+'</td><td>'+pos.quantity+'</td>'+
          '<td>'+num(pos.avgCost)+'</td><td>'+num(pos.currentPrice)+'</td>'+
          '<td style="color:'+(pos.unrealizedPnl>=0?'var(--green)':'var(--red)')+'">'+num(pos.unrealizedPnl)+'</td>'+
          '<td style="color:'+(pos.unrealizedPnlPct>=0?'var(--green)':'var(--red)')+'">'+(pos.unrealizedPnlPct!=null?(pos.unrealizedPnlPct*100).toFixed(2)+'%':'-')+'</td></tr>';
      }).join('')||'<tr><td colspan="8" style="text-align:center;color:var(--text-dim)">暂无持仓</td></tr>';
    }
  }).catch(function(){});
  api('/quant/api/orders'+adminModeParam(false)).then(function(o){
    var orders=o.data||o.orders||[];
    document.querySelector('#orders-table tbody').innerHTML=orders.slice(0,50).map(function(od){
      var sideMap={buy:'买入',sell:'卖出',short:'做空',cover:'平仓',buy_open:'买开',sell_close:'卖平',buy_close:'买平',sell_open:'卖开'};
      var sideLabel=sideMap[od.side]||od.side||'-';
      var sideClass=(od.side==='buy'||od.side==='buy_open'||od.side==='cover'||od.side==='buy_close')?'green':'red';
      var canCancel=(od.status==='pending'||od.status==='submitted');
      return '<tr><td style="white-space:nowrap">'+fmtTime(od.createdAt)+'</td><td>'+esc(od.symbol)+'</td>'+
        '<td><span class="badge badge-'+sideClass+'">'+sideLabel+'</span></td>'+
        '<td>'+num(od.price!=null?od.price:(od.avgFillPrice!=null?od.avgFillPrice:od.filledPrice))+'</td><td>'+(od.quantity||'-')+'</td>'+
        '<td><span class="badge badge-'+(od.status==='filled'?'green':od.status==='cancelled'?'red':'yellow')+'">'+esc(od.status)+'</span></td>'+
        '<td>'+(canCancel?'<button class="btn btn-sm btn-danger" onclick="cancelOrder(\\''+esc(od.orderId)+'\\')">撤单</button>':'')+'</td></tr>';
    }).join('')||'<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">暂无委托</td></tr>';
  }).catch(function(){});
  api('/quant/api/signals?limit=30'+adminModeParam(true)).then(function(s){
    var sigs=s.data||s.signals||[];
    document.querySelector('#signals-table tbody').innerHTML=sigs.map(function(sg){
      return '<tr><td style="white-space:nowrap">'+fmtTime(sg.timestamp)+'</td><td>'+esc(sg.symbol)+'</td>'+
        '<td><span class="badge badge-'+(sg.direction==='buy'||sg.direction==='cover'?'green':'red')+'">'+esc(sg.direction)+'</span></td>'+
        '<td>'+num(sg.price)+'</td><td>'+esc(sg.strategyId)+'</td>'+
        '<td>'+(sg.confidence!=null?(sg.confidence*100).toFixed(0)+'%':'-')+'</td>'+
        '<td style="max-width:200px;word-break:break-all;font-size:11px">'+esc(sg.reason||'')+'</td></tr>';
    }).join('')||'<tr><td colspan="7" style="text-align:center;color:var(--text-dim)">暂无信号</td></tr>';
  }).catch(function(){});
}
function cancelOrder(id){api('/quant/api/orders?id='+encodeURIComponent(id),{method:'DELETE'}).then(function(){toast('✅ 已撤单');loadTrading()})}

/* ══════ STRATEGIES ══════ */
function loadStrategies(){
  api('/quant/admin/api/strategies').then(function(d){
    if(!d){return;}
    var filter=(document.getElementById('strat-search').value||'').toLowerCase();
    document.getElementById('builtin-count').textContent=d.totalBuiltin;
    document.getElementById('user-count').textContent=d.totalUser;
    document.getElementById('template-count').textContent=d.totalTemplates;
    var bc=document.getElementById('builtin-cards');
    var filtered=d.builtin.filter(function(s){return !filter||s.id.toLowerCase().indexOf(filter)>=0||s.name.toLowerCase().indexOf(filter)>=0});
    bc.innerHTML=filtered.map(function(s){
      return '<div class="card"><h3>📈 '+esc(s.name)+' <span class="badge badge-blue">'+esc(s.engine||'builtin')+'</span></h3>'+
        '<p style="font-size:12px;color:var(--text-dim);margin-bottom:6px">'+esc(s.description)+'</p>'+
        '<div class="param-grid">'+(s.parameters||[]).slice(0,8).map(function(p){
          return '<div class="param-item"><span class="pname">'+esc(p.name)+'</span> <span class="pval">('+esc(p.type)+', 默认:'+esc(p.default==null?'-':p.default)+')</span></div>';
        }).join('')+'</div>'+
        ((s.parameters||[]).length>8?'<div style="font-size:11px;color:var(--text-dim);margin-top:4px">… 还有 '+((s.parameters||[]).length-8)+' 参数</div>':'')+
        '</div>';
    }).join('')||'<p style="color:var(--text-dim)">无匹配</p>';
    var uc=document.getElementById('user-cards');
    var uf=d.userStrategies.filter(function(s){return !filter||s.id.toLowerCase().indexOf(filter)>=0||s.name.toLowerCase().indexOf(filter)>=0});
    uc.innerHTML=uf.map(function(s){
      return '<div class="card"><h3>'+(s.starred?'⭐':'📄')+' '+esc(s.name)+' <span class="badge badge-yellow">'+esc(s.strategyId)+'</span></h3>'+
        '<p style="font-size:12px;color:var(--text-dim);margin-bottom:6px">'+esc(s.description||'无描述')+'</p>'+
        infoList({'标的':(s.symbols||[]).join(', ')||'-','标签':(s.tags||[]).map(function(t){return '<span class="badge badge-blue" style="margin-right:3px">'+esc(t)+'</span>'}).join('')||'-'})+
        '<div class="param-grid" style="margin-top:6px">'+Object.entries(s.parameters||{}).map(function(kv){
          return '<div class="param-item"><span class="pname">'+esc(kv[0])+'</span>=<span class="pval">'+esc(String(kv[1]))+'</span></div>';
        }).join('')+'</div></div>';
    }).join('')||'<p style="color:var(--text-dim)">暂无自定义策略</p>';
    var tmplC=document.getElementById('template-cards');
    var tf=(d.templates||[]).filter(function(t){return !filter||t.id.toLowerCase().indexOf(filter)>=0||t.name.toLowerCase().indexOf(filter)>=0});
    tmplC.innerHTML=tf.map(function(t){
      return '<div class="card"><h3>📝 '+esc(t.name)+' <span class="badge badge-green">'+esc(t.category)+'</span></h3>'+
        '<p style="font-size:12px;color:var(--text-dim);margin-bottom:6px">'+esc(t.description)+'</p>'+
        infoList(Object.assign({'基础策略':t.baseStrategyId},t.suggestedSymbols?{'推荐标的':t.suggestedSymbols.join(', ')}:{},t.notes?{'备注':t.notes}:{}))+
        '<div class="param-grid" style="margin-top:6px">'+Object.entries(t.suggestedParams||{}).map(function(kv){
          return '<div class="param-item"><span class="pname">'+esc(kv[0])+'</span>=<span class="pval">'+esc(String(kv[1]))+'</span></div>';
        }).join('')+'</div></div>';
    }).join('')||'<p style="color:var(--text-dim)">暂无模板</p>';
  }).catch(function(){});
}
var searchTimer;
var _stratSearch=document.getElementById('strat-search');
if(_stratSearch)_stratSearch.addEventListener('input',function(){clearTimeout(searchTimer);searchTimer=setTimeout(loadStrategies,300)});

/* ══════ TASKS ══════ */
function loadTasks(){
  api('/quant/admin/api/scheduled-tasks').then(function(d){
    if(!d){return;}
    var all=[].concat(d.pending||[],d.failed||[],(d.completed||[]).slice(0,20));
    document.querySelector('#tasks-table tbody').innerHTML=all.map(function(t){
      return '<tr><td style="font-size:11px">'+esc(t.id).slice(0,8)+'</td>'+
        '<td style="max-width:250px;word-break:break-all">'+esc(t.description)+'</td>'+
        '<td><span class="badge badge-'+(t.status==='pending'?'yellow':t.status==='completed'?'green':'red')+'">'+esc(t.status)+'</span></td>'+
        '<td style="white-space:nowrap">'+fmtTime(t.scheduledAt)+'</td>'+
        '<td style="white-space:nowrap">'+fmtTime(t.createdAt)+'</td>'+
        '<td>'+(t.status==='pending'?'<button class="btn btn-sm btn-danger" onclick="cancelTask(\\''+esc(t.id)+'\\')">取消</button>':'')+'</td></tr>';
    }).join('')||'<tr><td colspan="6" style="text-align:center;color:var(--text-dim)">暂无任务</td></tr>';
  }).catch(function(){});
  api('/quant/admin/api/price-alerts').then(function(pa){
    if(!pa){return;}
    document.getElementById('pa-count').textContent=pa.activeCount;
    document.querySelector('#pa-table tbody').innerHTML=pa.alerts.map(function(a){
      return '<tr><td>'+esc(a.symbol)+'</td><td>'+esc(a.type)+'</td><td>'+a.threshold+'</td>'+
        '<td><span class="badge badge-'+(a.triggered?'green':a.active!==false?'yellow':'red')+'">'+(a.triggered?'已触发':a.active!==false?'监控中':'已停止')+'</span></td>'+
        '<td><button class="btn btn-sm btn-danger" onclick="removePriceAlert(\\''+esc(a.id)+'\\')">删除</button></td></tr>';
    }).join('')||'<tr><td colspan="5" style="text-align:center;color:var(--text-dim)">暂无预警</td></tr>';
  }).catch(function(){});
}
function cancelTask(id){api('/quant/api/scheduled-tasks?id='+encodeURIComponent(id),{method:'DELETE'}).then(function(){toast('✅ 已取消');loadTasks()})}
function addPriceAlert(){
  var symbol=document.getElementById('pa-symbol').value.trim();
  var type=document.getElementById('pa-type').value;
  var threshold=Number(document.getElementById('pa-threshold').value);
  if(!symbol||!threshold){toast('请填写代码和阈值');return}
  api('/quant/api/price-alerts',{method:'POST',body:JSON.stringify({symbol:symbol,type:type,threshold:threshold})}).then(function(){toast('✅ 已添加');loadTasks()});
}
function removePriceAlert(id){api('/quant/api/price-alerts?id='+encodeURIComponent(id),{method:'DELETE'}).then(function(){toast('✅ 已删除');loadTasks()})}

/* ══════ CONFIG ══════ */
function loadConfig(){
  api('/quant/admin/api/config').then(function(d){document.getElementById('config-json').textContent=JSON.stringify(d,null,2)}).catch(function(){document.getElementById('config-json').textContent='⚠️ 配置加载失败'});
}

/* ══════ LOGS ══════ */
var logAutoTimer=null;
function loadLogs(){
  var level=document.getElementById('log-level').value;
  var source=document.getElementById('log-source').value.trim();
  var search=document.getElementById('log-search').value.trim();
  var limit=parseInt(document.getElementById('log-limit').value)||200;
  var qs='?limit='+limit;
  if(level)qs+='&level='+encodeURIComponent(level);
  if(source)qs+='&source='+encodeURIComponent(source);
  if(search)qs+='&search='+encodeURIComponent(search);
  api('/quant/admin/api/logs'+qs).then(function(d){
    var s=d.summary||{};
    document.getElementById('log-summary-row').innerHTML=[
      kpiHtml(d.total||0,'总日志'),
      kpiHtml(s.error||0,'错误'),
      kpiHtml(s.warn||0,'警告'),
      kpiHtml(s.info||0,'信息'),
      kpiHtml(s.debug||0,'调试'),
    ].join('');
    var box=document.getElementById('log-entries');
    var entries=d.entries||[];
    if(!entries.length){box.innerHTML='<div style="color:var(--text-dim)">暂无日志</div>';return}
    box.innerHTML=entries.map(function(e){
      var cls='log-info';
      if(e.level==='error'||e.level==='fatal')cls='log-critical';
      else if(e.level==='warn')cls='log-warning';
      else if(e.level==='debug')cls='log-signal';
      var ts=new Date(e.timestamp).toLocaleString('zh-CN',{hour12:false});
      var badge='<span class="badge badge-'+(e.level==='error'||e.level==='fatal'?'red':e.level==='warn'?'yellow':e.level==='debug'?'purple':'blue')+'">'+esc(e.level)+'</span>';
      return '<div class="'+cls+'" style="padding:2px 0;border-bottom:1px solid var(--border)">'+
        '<span style="color:var(--text-dim);font-size:10px">'+esc(ts)+'</span> '+badge+
        (e.source?' <span style="color:var(--accent);font-size:10px">['+esc(e.source)+']</span>':'')+
        ' '+esc(e.message)+'</div>';
    }).join('');
    box.scrollTop=box.scrollHeight;
    document.getElementById('log-status').textContent='显示 '+entries.length+' / '+d.total+' 条日志';
  }).catch(function(){});
}
function clearLogs(){
  if(!confirm('确认清空所有日志？'))return;
  api('/quant/admin/api/logs',{method:'DELETE'}).then(function(){toast('✅ 日志已清空');loadLogs()});
}
function toggleLogAuto(){
  var btn=document.getElementById('log-auto-btn');
  if(logAutoTimer){clearInterval(logAutoTimer);logAutoTimer=null;btn.textContent='▶ 自动刷新';return}
  logAutoTimer=setInterval(loadLogs,3000);
  btn.textContent='⏸ 停止刷新';
  toast('自动刷新已开启 (3秒)');
}

/* ══════ SECTORS ══════ */
var _sectorData=[];
function loadSectors(){
  api('/quant/admin/api/sector-index?action=stats').then(function(d){
    if(!d){return;}
    _sectorData=d.sectors||[];
    var st=document.getElementById('sector-stats');
    st.innerHTML='共 <b>'+d.total_sectors+'</b> 个板块，<b>'+d.total_stocks+'</b> 只股票'+(d.loaded_at?' <span style="color:var(--text-dim)">| 加载时间: '+fmtTime(d.loaded_at*1000)+'</span>':'');
    renderSectorTable(_sectorData);
  }).catch(function(){});
}
function renderSectorTable(sectors){
  var tb=document.querySelector('#sector-table tbody');
  tb.innerHTML=sectors.map(function(s){
    var mv=(s.total_free_float_mv/1e8).toFixed(1);
    return '<tr><td><a href="#" onclick="viewSectorMembers(\\''+esc(s.sector)+'\\');return false" style="color:var(--blue);cursor:pointer">'+esc(s.sector)+'</a></td><td>'+s.count+'</td><td>'+num(mv)+'</td><td><button class="btn btn-sm" onclick="viewSectorMembers(\\''+esc(s.sector)+'\\')">查看</button></td></tr>';
  }).join('')||'<tr><td colspan="4" style="text-align:center;color:var(--text-dim)">暂无板块数据，请上传 CSV</td></tr>';
}
function filterSectors(){
  var q=(document.getElementById('sector-search').value||'').trim().toLowerCase();
  if(!q){renderSectorTable(_sectorData);return;}
  renderSectorTable(_sectorData.filter(function(s){return s.sector.toLowerCase().indexOf(q)>=0}));
}
function viewSectorMembers(sector){
  document.getElementById('sector-members-title').textContent=sector+' 成员列表';
  api('/quant/admin/api/sector-index?action=sector_members&sector='+encodeURIComponent(sector)).then(function(d){
    var members=d.members||[];
    document.getElementById('sector-members-title').textContent=sector+' ('+members.length+' ֻ)';
    var tb=document.querySelector('#sector-members-table tbody');
    tb.innerHTML=members.map(function(m){
      var mv=(m.free_float_mv/1e8).toFixed(2);
      return '<tr><td><a href="#" onclick="openStockDetail(\\''+esc(m.code)+'\\');return false" style="color:var(--blue);cursor:pointer">'+esc(m.code)+'</a></td><td>'+esc(m.name)+'</td><td>'+esc(m.sector)+'</td><td>'+num(mv)+'</td></tr>';
    }).join('')||'<tr><td colspan="4" style="text-align:center;color:var(--text-dim)">无成员</td></tr>';
  });
}
function reloadSectorCSV(){
  toast('🔄 正在重新加载 CSV…');
  api('/quant/admin/api/sector-index/upload',{method:'POST',body:JSON.stringify({action:'reload'})}).then(function(d){
    toast('✅ 已重新加载: '+d.total_stocks+' 只股票, '+d.total_sectors+' 个板块');
    loadSectors();
  });
}
function uploadSectorCSV(){
  var fileInput=document.getElementById('sector-csv-file');
  if(!fileInput.files||!fileInput.files.length){toast('⚠️ 请选择 CSV 文件');return;}
  var file=fileInput.files[0];
  var reader=new FileReader();
  reader.onload=function(e){
    var content=e.target.result;
    toast('📤 正在上传…');
    api('/quant/admin/api/sector-index/upload',{method:'POST',body:JSON.stringify({action:'upload',content:content})}).then(function(d){
      var el=document.getElementById('sector-upload-result');
      el.innerHTML='<span style="color:var(--green)">✅ '+esc(d.message||'上传成功')+'</span>';
      toast('✅ 上传成功');
      loadSectors();
    }).catch(function(err){
      document.getElementById('sector-upload-result').innerHTML='<span style="color:var(--red)">❌ 上传失败: '+esc(err.message)+'</span>';
    });
  };
  reader.readAsText(file);
}

/* ══════ CHAT ══════ */
var _chatVersion='20260323c';
var _chatWs=null;
var _chatPending={};
var _chatReqId=0;
var _chatConnected=false;
var _chatCurrentRunId=null;
var _chatStreamBuf='';
var _chatLoaded=false;
var _chatReconnectTimer=null;
var _chatReconnectDelay=1000;
var _chatAutoReconnect=true;

function chatWsUrl(){
  var loc=window.location;
  var proto=loc.protocol==='https:'?'wss:':'ws:';
  return proto+'//'+loc.host+'/';
}

function chatSetStatus(msg,color){
  var el=document.getElementById('chat-status');
  el.textContent=msg;
  el.style.color=color||'var(--text-dim)';
}
function chatDebug(msg){
  var el=document.getElementById('chat-debug');
  if(el)el.textContent=msg;
}

function chatAppendMsg(role,text,opts){
  opts=opts||{};
  var box=document.getElementById('chat-messages');
  var div=document.createElement('div');
  div.style.marginBottom='12px';
  var label=role==='user'?'🧑 你':'🤖 OpenClaw';
  var labelColor=role==='user'?'var(--accent)':'var(--green)';
  div.innerHTML='<div style="font-size:11px;color:'+labelColor+';margin-bottom:2px;font-weight:600">'+label+'</div>'
    +'<div style="white-space:pre-wrap;word-break:break-word"'+(opts.id?' id="'+opts.id+'"':'')+'>'+(opts.html||esc(text))+'</div>';
  box.appendChild(div);
  box.scrollTop=box.scrollHeight;
  return div;
}

function chatConnect(){
  if(_chatWs){
    var oldWs=_chatWs;
    _chatWs=null;
    _chatConnected=false;
    _chatPending={};
    try{oldWs.close();}catch(e){}
  }
  if(_chatReconnectTimer){clearTimeout(_chatReconnectTimer);_chatReconnectTimer=null;}
  var token=_GT||localStorage.getItem('_ocGatewayToken')||'';
  if(!token){
    token=prompt('请输入 Gateway Token (gateway.auth.token):');
    if(!token)return;
    localStorage.setItem('_ocGatewayToken',token);
  }
  chatSetStatus('连接中…','var(--yellow)');
  var wsUrl=chatWsUrl();
  console.log('[chat] connecting to', wsUrl);
  var ws;
  try{ws=new WebSocket(wsUrl);}catch(ex){
    console.error('[chat] WebSocket creation failed:', ex);
    chatSetStatus('连接失败','var(--red)');
    chatScheduleReconnect();
    return;
  }
  ws.binaryType='arraybuffer';
  _chatWs=ws;
  var challengeHandled=false;

  function handleMessage(raw){
    var data;
    if(typeof raw==='string'){data=raw;}
    else if(raw instanceof ArrayBuffer){data=new TextDecoder().decode(raw);}
    else if(raw instanceof Blob){
      raw.text().then(function(t){handleMessage(t);});
      return;
    }else{console.warn('[chat] unknown message type:',typeof raw);return;}

    var frame;
    try{frame=JSON.parse(data);}catch(ex){console.warn('[chat] invalid JSON:',data.substring(0,200));return;}
    if(!frame||typeof frame!=='object')return;
    console.log('[chat] frame:', frame.type, frame.event||frame.method||frame.id||'');

    if(frame.type==='event'&&frame.event==='connect.challenge'){
      challengeHandled=true;
      console.log('[chat] got challenge, sending connect...');
      var req={
        type:'req',id:'connect-'+(++_chatReqId),method:'connect',
        params:{
          minProtocol:3,maxProtocol:3,
          client:{id:'openclaw-control-ui',displayName:'量化管理面板',version:'1.0',platform:'browser',mode:'webchat'},
          role:'operator',
          scopes:['operator.read','operator.write','operator.admin'],
          caps:[],
          auth:{token:token}
        }
      };
      try{ws.send(JSON.stringify(req));}catch(ex){console.error('[chat] send failed:',ex);}
      return;
    }
    if(frame.type==='res'){
      if(frame.id&&frame.id.indexOf('connect-')===0){
        if(frame.ok||(frame.payload&&frame.payload.type==='hello-ok')){
          _chatConnected=true;
          chatResetReconnect();
          console.log('[chat] connected OK');
          chatSetStatus('已连接','var(--green)');
          document.getElementById('chat-connect-btn').style.display='none';
          document.getElementById('chat-disconnect-btn').style.display='';
          document.getElementById('chat-send-btn').disabled=false;
          if(!_chatLoaded&&!_chatCurrentRunId)chatLoadHistory();
        }else{
          console.error('[chat] auth failed:', JSON.stringify(frame.error||frame));
          chatSetStatus('认证失败: '+(frame.error&&frame.error.message||'unknown'),'var(--red)');
          localStorage.removeItem('_ocGatewayToken');
          if(_chatWs===ws){_chatWs=null;}
          ws.close();
        }
        return;
      }
      var waiter=_chatPending[frame.id];
      if(waiter){
        delete _chatPending[frame.id];
        clearTimeout(waiter.t);
        console.log('[chat] RPC response:', frame.id, 'ok:', frame.ok, 'error:', JSON.stringify(frame.error||null).substring(0,200));
        waiter.cb(frame);
      }
      return;
    }
    if(frame.type==='event'&&frame.event==='chat'){
      console.log('[chat] chat event:', JSON.stringify(frame.payload).substring(0,300));
      chatHandleEvent(frame.payload);
      return;
    }
    if(frame.type==='hello-ok'){
      _chatConnected=true;
      chatResetReconnect();
      console.log('[chat] connected (hello-ok direct)');
      chatSetStatus('已连接','var(--green)');
      document.getElementById('chat-connect-btn').style.display='none';
      document.getElementById('chat-disconnect-btn').style.display='';
      document.getElementById('chat-send-btn').disabled=false;
      if(!_chatLoaded)chatLoadHistory();
    }
  }

  ws.addEventListener('open',function(){
    console.log('[chat] ws open, readyState='+ws.readyState+' ver='+_chatVersion);
    chatSetStatus('握手中…','var(--yellow)');
    chatDebug('v'+_chatVersion+' open, waiting challenge...');
    setTimeout(function(){
      if(!challengeHandled&&_chatWs===ws&&!_chatConnected){
        console.warn('[chat] no challenge after 8s, closing to retry...');
        chatSetStatus('重连中…','var(--yellow)');
        ws.close();
      }
    },8000);
  });
  ws.addEventListener('message',function(e){
    var dtype=typeof e.data==='string'?'str':(e.data instanceof ArrayBuffer?'ab':'blob');
    chatDebug('msg: '+dtype+' len='+(e.data&&e.data.length||e.data&&e.data.byteLength||'?'));
    console.log('[chat] raw message, type:', dtype, 'len:', (e.data&&e.data.length)||(e.data&&e.data.byteLength)||'?');
    handleMessage(e.data);
  });
  ws.addEventListener('close',function(ev){
    console.log('[chat] ws close, code='+ev.code+' reason='+ev.reason+' wasConnected='+_chatConnected);
    if(_chatWs!==ws)return;
    _chatConnected=false;
    if(_chatCurrentRunId){
      var el=document.getElementById('chat-stream-'+_chatCurrentRunId);
      if(el)el.textContent=(el.textContent==='⏳ 思考中…'?'':''+_chatStreamBuf)+'\\n⚠️ 连接中断，重连后请重新发送';
      _chatCurrentRunId=null;
      _chatStreamBuf='';
    }
    Object.keys(_chatPending).forEach(function(k){
      var w=_chatPending[k];
      delete _chatPending[k];
      clearTimeout(w.t);
      w.cb({ok:false,error:{message:'连接断开'}});
    });
    chatSetStatus('已断开','var(--red)');
    document.getElementById('chat-connect-btn').style.display='';
    document.getElementById('chat-disconnect-btn').style.display='none';
    document.getElementById('chat-send-btn').disabled=true;
    _chatWs=null;
    if(_chatAutoReconnect)chatScheduleReconnect();
  });
  ws.addEventListener('error',function(ev){
    console.error('[chat] ws error event', ev);
    if(_chatWs!==ws)return;
    chatSetStatus('连接失败','var(--red)');
  });
}

function chatDisconnect(intentional){
  _chatAutoReconnect=!intentional;
  if(_chatReconnectTimer){clearTimeout(_chatReconnectTimer);_chatReconnectTimer=null;}
  var ws=_chatWs;
  _chatWs=null;
  _chatConnected=false;
  _chatPending={};
  chatSetStatus(intentional?'已断开':'断开连接…','var(--red)');
  document.getElementById('chat-connect-btn').style.display='';
  document.getElementById('chat-disconnect-btn').style.display='none';
  document.getElementById('chat-send-btn').disabled=true;
  if(ws){try{ws.close();}catch(e){}}
}

function chatScheduleReconnect(){
  if(_chatReconnectTimer)return;
  var delay=_chatReconnectDelay;
  console.log('[chat] scheduling reconnect in '+delay+'ms');
  _chatReconnectTimer=setTimeout(function(){
    _chatReconnectTimer=null;
    if(!_chatConnected&&!_chatWs){
      _chatReconnectDelay=Math.min(_chatReconnectDelay*2,15000);
      chatConnect();
    }
  },delay);
}

function chatResetReconnect(){
  _chatReconnectDelay=1000;
  _chatAutoReconnect=true;
}

function chatRpc(method,params,timeoutMs){
  timeoutMs=timeoutMs||30000;
  return new Promise(function(resolve,reject){
    if(!_chatWs||!_chatConnected){reject(new Error('未连接'));return;}
    var id='rpc-'+(++_chatReqId);
    var frame={type:'req',id:id,method:method,params:params};
    var t=setTimeout(function(){delete _chatPending[id];reject(new Error('超时'))},timeoutMs);
    _chatPending[id]={cb:resolve,t:t};
    _chatWs.send(JSON.stringify(frame));
  });
}

function chatLoadHistory(){
  if(_chatCurrentRunId)return;
  chatRpc('chat.history',{sessionKey:'main',limit:50},60000).then(function(res){
    if(!res.ok)return;
    if(_chatCurrentRunId)return;
    _chatLoaded=true;
    var msgs=(res.payload&&res.payload.messages)||[];
    var box=document.getElementById('chat-messages');
    box.innerHTML='';
    var displayed=0;
    msgs.forEach(function(m){
      if(m.role==='system')return;
      var text=m.content||m.text||'';
      if(typeof text==='object'&&Array.isArray(text)){
        text=text.map(function(c){return c.text||''}).join('');
      }
      if(!text)return;
      if(text.indexOf('HEARTBEAT')===0||text.indexOf('Read HEARTBEAT')===0)return;
      if(text.indexOf('\u26a0\ufe0f Agent failed')===0||text.indexOf('Agent failed')===0)return;
      if(text.indexOf('Logs: openclaw logs')!==-1&&text.indexOf('No API key')!==-1)return;
      chatAppendMsg(m.role,text);
      displayed++;
    });
    if(displayed===0){
      box.innerHTML='<div style="color:var(--text-dim);text-align:center;padding:40px 0">暂无聊天记录，发送消息开始对话</div>';
    }
  }).catch(function(){});
}

function chatSend(){
  var input=document.getElementById('chat-input');
  var msg=input.value.trim();
  if(!msg)return;
  if(_chatConnected&&_chatWs&&_chatWs.readyState!==1){
    console.warn('[chat] stale connection detected: readyState='+_chatWs.readyState+', resetting');
    _chatConnected=false;
    _chatWs=null;
  }
  if(!_chatConnected||!_chatWs){
    console.warn('[chat] send: not connected, reconnecting first. ver='+_chatVersion);
    chatSetStatus('重连中…','var(--yellow)');
    var savedMsg=msg;
    chatResetReconnect();
    chatConnect();
    var attempts=0;
    var pollId=setInterval(function(){
      attempts++;
      if(_chatConnected&&_chatWs&&_chatWs.readyState===1){
        clearInterval(pollId);
        input.value=savedMsg;
        chatSend();
      }else if(attempts>20){
        clearInterval(pollId);
        toast('⚠️ 连接网关超时，请稍后重试');
        input.value=savedMsg;
      }
    },500);
    return;
  }
  input.value='';
  var box=document.getElementById('chat-messages');
  if(box.querySelector('div[style*="text-align:center"]')&&box.children.length===1)box.innerHTML='';
  chatAppendMsg('user',msg);
  var runId='admin-chat-'+(++_chatReqId)+'-'+Date.now();
  _chatCurrentRunId=runId;
  _chatStreamBuf='';
  chatAppendMsg('assistant','',{id:'chat-stream-'+runId,html:'<span style="color:var(--text-dim)">⏳ 思考中…</span>'});
  chatRpc('chat.send',{
    sessionKey:'main',
    message:msg,
    idempotencyKey:runId
  },120000).then(function(res){
    console.log('[chat] chat.send response:', JSON.stringify({ok:res.ok, error:res.error, payloadKeys:res.payload?Object.keys(res.payload):null}).substring(0,500));
    if(!res.ok){
      var el=document.getElementById('chat-stream-'+runId);
      if(el)el.textContent='❌ 发送失败: '+(res.error&&res.error.message||'未知错误');
      _chatCurrentRunId=null;
    }
  }).catch(function(err){
    console.error('[chat] chat.send error:', err.message);
    var el=document.getElementById('chat-stream-'+runId);
    if(el)el.textContent='❌ '+err.message;
    _chatCurrentRunId=null;
  });
}

function chatHandleEvent(payload){
  if(!payload)return;
  var runId=payload.runId||_chatCurrentRunId;
  var el=document.getElementById('chat-stream-'+runId);
  if(!el)return;
  if(payload.state==='delta'){
    if(payload.delta&&payload.delta.text){
      _chatStreamBuf+=payload.delta.text;
      el.textContent=_chatStreamBuf;
    }else if(payload.text){
      _chatStreamBuf+=payload.text;
      el.textContent=_chatStreamBuf;
    }
    var box=document.getElementById('chat-messages');
    box.scrollTop=box.scrollHeight;
  }else if(payload.state==='final'){
    var text='';
    if(payload.message){
      var content=payload.message.content;
      if(typeof content==='string')text=content;
      else if(Array.isArray(content))text=content.map(function(c){return c.text||''}).join('');
      else text=payload.message.text||_chatStreamBuf;
    }else{
      text=_chatStreamBuf;
    }
    el.textContent=text||_chatStreamBuf||'(空回复)';
    _chatStreamBuf='';
    _chatCurrentRunId=null;
    var box=document.getElementById('chat-messages');
    box.scrollTop=box.scrollHeight;
  }else if(payload.state==='error'){
    el.textContent='❌ '+(payload.errorMessage||'发生错误');
    _chatStreamBuf='';
    _chatCurrentRunId=null;
  }else if(payload.state==='aborted'){
    el.textContent=(el.textContent||'')+'\\n[已中止]';
    _chatStreamBuf='';
    _chatCurrentRunId=null;
  }
}

function chatClear(){
  document.getElementById('chat-messages').innerHTML='<div style="color:var(--text-dim);text-align:center;padding:40px 0">暂无聊天记录，发送消息开始对话</div>';
  _chatCurrentRunId=null;
  _chatStreamBuf='';
  _chatLoaded=false;
  if(_chatConnected&&_chatWs){
    chatRpc('sessions.reset',{key:'main'},30000).then(function(res){
      if(res.ok){console.log('[chat] session reset OK');_chatLoaded=true;}
      else{console.warn('[chat] session reset failed:',res.error);}
    }).catch(function(err){console.warn('[chat] session reset error:',err.message);});
  }
}

function loadChat(){
  console.log('[chat] loadChat: connected='+_chatConnected+' ws='+!!_chatWs+' timer='+!!_chatReconnectTimer+' ver='+_chatVersion);
  if(!_chatConnected&&!_chatWs&&!_chatReconnectTimer){
    chatResetReconnect();
    chatConnect();
  }
}

// Chat input keyboard shortcut: Enter to send, Shift+Enter for newline
var _chatInput=document.getElementById('chat-input');
if(_chatInput)_chatInput.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();chatSend();}
});

/* ── AI Chat iframe ── */
var _aiChatLoaded=false;
function loadAiChat(){
  if(_aiChatLoaded)return;
  _aiChatLoaded=true;
  var container=document.getElementById('ai-chat-frame-container');
  var status=document.getElementById('ai-chat-status');
  if(!container)return;
  // Proxy port is injected as _PROXY_PORT by the server
  var proxyPort=window._PROXY_PORT||0;
  if(!proxyPort){
    container.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--red);font-size:14px">⚠️ iframe 代理未启用 (未配置 openclawProxyPort)</div>';
    if(status)status.textContent='未启用';
    return;
  }
  var src=location.protocol+'//'+location.hostname+':'+proxyPort+'/';
  container.innerHTML='<iframe id="ai-chat-iframe" src="'+esc(src)+'" style="width:100%;height:100%;border:0" allow="clipboard-read; clipboard-write" title="OpenClaw Chat"></iframe>';
  if(status)status.textContent='已加载';
  var iframe=document.getElementById('ai-chat-iframe');
  if(iframe){
    iframe.addEventListener('load',function(){if(status)status.textContent='✅ 已连接'});
    iframe.addEventListener('error',function(){if(status)status.textContent='❌ 加载失败'});
  }
}
function reloadAiChat(){
  _aiChatLoaded=false;
  var container=document.getElementById('ai-chat-frame-container');
  if(container)container.innerHTML='<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-dim);font-size:14px">加载中…</div>';
  var status=document.getElementById('ai-chat-status');
  if(status)status.textContent='刷新中…';
  loadAiChat();
}

/* ── Auto-refresh overview every 15s ── */
setInterval(function(){
  var at=document.querySelector('.tab.active');
  if(at&&at.dataset.tab==='overview')loadOverview();
},15000);

/* ── Initial load ── */
loadOverview();
`;
}
