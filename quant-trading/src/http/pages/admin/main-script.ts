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
function _localDate(d){d=d||new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')}
function _tsToDate(ts){return _localDate(new Date(ts))}
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
setInterval(function(){document.getElementById('clock').textContent=new Date().toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})},1000);
// Set default end date for kline query to today
(function(){var el=document.getElementById('kline-end');if(el)el.value=_localDate()})();
function loadTab(name){
  var m={overview:loadOverview,services:loadServices,data:loadData,alerts:loadAlerts,trading:loadTrading,strategies:loadStrategies,sectors:loadSectors,tasks:loadTasks,logs:loadLogs,'ai-chat':chatLoad,config:loadConfig};
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
function fmtTime(ts){return ts?new Date(ts).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'-'}

/* ══════ Stock Name Cache ══════ */
var _nameCache={};
function resolveNames(symbols){var miss=symbols.filter(function(s){return s&&!_nameCache[s]});if(!miss.length)return Promise.resolve();return api('/quant/api/stock-names?symbols='+miss.join(',')).then(function(r){var d=r&&r.data||{};for(var k in d)if(d.hasOwnProperty(k))_nameCache[k]=d[k]}).catch(function(){})}
var _condLabels={price_above:'价格≥',price_below:'价格≤',change_pct_above:'涨幅≥%',change_pct_below:'跌幅≤%',volume_above:'成交量≥',volume_spike:'量异动',sentiment_spike:'舆情异动',hot_rank_above:'热度排名≤'};

/* ══════ OVERVIEW ══════ */
function loadOverview(){
  api('/quant/admin/api/overview',{timeout:30000}).then(function(d){
    var kpi=document.getElementById('kpi-row');
    if(!kpi)return;
    kpi.innerHTML=[
      kpiHtml(esc(d.application.uptime),'运行时间'),
      kpiHtml(d.application.memoryMB+' MB','内存占用'),
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
      return '<div class="log-'+esc(e.category||'info')+'">['+new Date(e.timestamp).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})+'] <b>'+esc(e.category)+'</b> '+esc(typeof e.data==='string'?e.data:JSON.stringify(e.data))+'</div>';
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
    if(!d||!d.application){c.innerHTML='<div style="color:var(--text-dim)">⚠️ 服务数据异常</div>';return;}
    var gw=d.application;
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
  api('/quant/admin/api/data-warehouse/sync',{timeout:5*60*1000}).then(function(r){
    var el=document.getElementById('sync-result');
    if(r.status==='error'){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.message||'检查失败')+'</span>';return}
    var b=r.bridge||{};
    document.getElementById('sync-bridge-status').innerHTML='迅投桥接: '+(b.online?'<span class="badge badge-green">在线</span> '+esc(b.url||''):'<span class="badge badge-red">离线</span>');
    var html='<div>📊 总品种: <b>'+r.total+'</b>  ✅ 已最新: <b>'+r.fresh+'</b>  ⏳ 过期: <b>'+r.stale+'</b>  🆕 无缓存: <b>'+r.no_cache+'</b></div>';
    html+='<div>需同步: <b>'+r.need_fetch+'</b> 只</div>';
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
  api('/quant/admin/api/data-warehouse/sync',{method:'POST',body:JSON.stringify({source:source,max_stocks:maxStocks}),timeout:10*60*1000}).then(function(r){
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
  api('/quant/admin/api/data-warehouse/warmup',{method:'POST',body:JSON.stringify({symbols:symbols,interval:interval,start:start,end:end}),timeout:10*60*1000}).then(function(r){
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
  api('/quant/admin/api/local-data/sync',{method:'POST',body:JSON.stringify({}),timeout:3600000}).then(function(r){
    if(r.status==='error'||r.ok===false){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.message||r.error||'同步失败')+'</span>';toast('❌ 同步失败');return}
    var html='<div>✅ 同步完成 (耗时 '+r.elapsed_seconds+'s)</div>';
    html+='<div>日线: <b>'+r.daily_months_synced+'</b> 月  复权因子: <b>'+r.adj_factor_months_synced+'</b> 月</div>';
    if(r.stock_basic)html+='<div>股票列表: '+esc(r.stock_basic.status)+(r.stock_basic.stocks?' ('+r.stock_basic.stocks+'只)':'')+(r.stock_basic.message?' <span style="color:var(--yellow)">'+esc(r.stock_basic.message)+'</span>':'')+(r.stock_basic.error&&!r.stock_basic.message?' <span style="color:var(--red)">'+esc(r.stock_basic.error)+'</span>':'')+'</div>';
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
  var end=document.getElementById('kline-end').value||_localDate();
  if(!symbol){toast('请输入代码');return}
  if(!start){var d=new Date();d.setMonth(d.getMonth()-6);start=_localDate(d)}
  toast('⏳ 查询中…');
  api('/quant/admin/api/query-klines?symbol='+encodeURIComponent(symbol)+'&interval='+interval+'&start='+encodeURIComponent(start)+'&end='+encodeURIComponent(end)).then(function(r){
    var bars=r.data||r.bars||r||[];
    var src=r.source==='cache'?'📦 缓存':'🌐 在线';
    if(!Array.isArray(bars)||!bars.length){document.getElementById('kline-result').innerHTML='<span style="color:var(--text-dim)">无数据</span>';return}
    var h='<div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">来源: '+src+' | 共 '+bars.length+' 条</div>';
    h+='<table><thead><tr><th>日期</th><th>开</th><th>高</th><th>低</th><th>收</th><th>量</th></tr></thead><tbody>';
    bars.slice(-50).forEach(function(b){
      var dt=b.date||b.timestamp;if(typeof dt==='number'){dt=_tsToDate(dt)}
      h+='<tr><td>'+esc(dt)+'</td><td>'+num(b.open)+'</td><td>'+num(b.high)+'</td><td>'+num(b.low)+'</td><td>'+num(b.close)+'</td><td>'+(b.volume||0).toLocaleString()+'</td></tr>';
    });
    h+='</tbody></table>';
    if(bars.length>50)h+='<div style="font-size:11px;color:var(--text-dim);margin-top:4px">显示最近50条 / 共'+bars.length+'条</div>';
    document.getElementById('kline-result').innerHTML=h;toast('✅ '+src+' 查到 '+bars.length+' 条');
  });
}

/* ── Tick Data Download / Query ── */
function downloadTickData(){
  var sym=(document.getElementById('tick-symbol').value||'').trim();
  var dt=(document.getElementById('tick-date').value||'').trim();
  if(!sym){toast('请输入股票代码');return}
  if(!dt){dt=_localDate().replace(/-/g,'')}
  // Normalize symbol → add suffix if needed
  if(!/\\.(SH|SZ|BJ)$/i.test(sym)){
    var c=sym.replace(/\\D/g,'');
    if(c.length===6){sym=c+(c[0]==='6'?'.SH':c.startsWith('0')||c.startsWith('3')?'.SZ':'.BJ')}
  }
  var el=document.getElementById('tick-result');
  var stEl=document.getElementById('tick-bridge-status');
  el.innerHTML='<div style="color:var(--text-dim)">⏳ 正在从 QMT 下载 tick 数据…</div>';
  stEl.innerHTML='';
  toast('📥 下载 tick 数据: '+sym+' '+dt);
  api('/quant/admin/api/tick-download',{method:'POST',body:JSON.stringify({codes:[sym],date:dt}),timeout:120000}).then(function(r){
    if(r.error){el.innerHTML='<span style="color:var(--red)">❌ '+esc(r.error)+'</span>';return}
    // Response: {"status":"ok", "600519.SH": {"count":N, "valid":M, "ticks":[...], "message":"..."}}
    var symData=r[sym]||{};
    var ticks=symData.ticks||r.ticks||[];
    if(!ticks.length){
      // Show info from symData if available
      var msg=symData.message||symData.error||r.message||'无数据';
      el.innerHTML='<span style="color:var(--text-dim)">'+esc(msg)+'</span>';
      return;
    }
    // Show summary info
    var info=symData.count?('总 '+symData.count+' 条, 有效 '+symData.valid+' 条'):'';
    if(symData.cache)info+=(info?' | ':'')+esc(symData.cache);
    if(info)stEl.innerHTML='<span style="color:var(--green)">'+info+'</span>';
    _renderTickTable(el,ticks,sym,dt);
    toast('✅ 下载 '+ticks.length+' 条 tick');
  }).catch(function(err){
    el.innerHTML='<span style="color:var(--red)">❌ '+esc(String(err.message||err))+'</span>';
  });
}
function queryTickData(){
  var sym=(document.getElementById('tick-symbol').value||'').trim();
  var dt=(document.getElementById('tick-date').value||'').trim();
  if(!sym){toast('请输入股票代码');return}
  if(!dt){dt=_localDate().replace(/-/g,'')}
  if(!/\\.(SH|SZ|BJ)$/i.test(sym)){
    var c=sym.replace(/\\D/g,'');
    if(c.length===6){sym=c+(c[0]==='6'?'.SH':c.startsWith('0')||c.startsWith('3')?'.SZ':'.BJ')}
  }
  var el=document.getElementById('tick-result');
  el.innerHTML='<div style="color:var(--text-dim)">⏳ 查询缓存…</div>';
  toast('🔍 查询已缓存 tick: '+sym+' '+dt);
  api('/quant/admin/api/tick-query?symbol='+encodeURIComponent(sym)+'&date='+encodeURIComponent(dt)).then(function(r){
    if(r.error||r.status==='error'){el.innerHTML='<span style="color:var(--text-dim)">'+(r.message||r.error||'无缓存数据')+'</span>';return}
    var ticks=r.ticks||[];
    if(!ticks.length){el.innerHTML='<span style="color:var(--text-dim)">无缓存 ('+(r.message||'文件不存在')+')</span>';return}
    _renderTickTable(el,ticks,sym,dt);
    toast('✅ 缓存中有 '+ticks.length+' 条 tick');
  }).catch(function(err){
    el.innerHTML='<span style="color:var(--red)">❌ '+esc(String(err.message||err))+'</span>';
  });
}
function _renderTickTable(el,ticks,sym,dt){
  var valid=ticks.filter(function(t){return t.price>0||t.lastPrice>0});
  var h='<div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">'+esc(sym)+' '+esc(dt)+' | 总 tick: '+ticks.length+', 有效(price>0): '+valid.length+'</div>';
  h+='<table><thead><tr><th>时间</th><th>价格</th><th>量</th><th>额</th><th>买1价</th><th>买1量</th><th>卖1价</th><th>卖1量</th></tr></thead><tbody>';
  var show=valid.length>100?valid.slice(-100):valid;
  show.forEach(function(t){
    var p=t.price||t.lastPrice||0;
    var ts=t.timestamp||t.time||'';
    if(typeof ts==='number'&&ts>1e12){var d=new Date(ts);ts=d.toLocaleTimeString('zh-CN',{hour12:false})}
    else if(typeof ts==='string'&&ts.length>10){ts=ts.substring(11,19)}
    var bid1=(t.bid_levels&&t.bid_levels[0])?t.bid_levels[0].price:(Array.isArray(t.bidPrice)?t.bidPrice[0]:'-');
    var bid1v=(t.bid_levels&&t.bid_levels[0])?t.bid_levels[0].volume:(Array.isArray(t.bidVol)?t.bidVol[0]:'-');
    var ask1=(t.ask_levels&&t.ask_levels[0])?t.ask_levels[0].price:(Array.isArray(t.askPrice)?t.askPrice[0]:'-');
    var ask1v=(t.ask_levels&&t.ask_levels[0])?t.ask_levels[0].volume:(Array.isArray(t.askVol)?t.askVol[0]:'-');
    h+='<tr><td style="white-space:nowrap">'+esc(ts)+'</td><td>'+num(p)+'</td><td>'+(t.volume||0).toLocaleString()+'</td><td>'+num(t.amount||0)+'</td><td>'+num(bid1)+'</td><td>'+(bid1v||0).toLocaleString()+'</td><td>'+num(ask1)+'</td><td>'+(ask1v||0).toLocaleString()+'</td></tr>';
  });
  h+='</tbody></table>';
  if(valid.length>100)h+='<div style="font-size:11px;color:var(--text-dim);margin-top:4px">显示最近100条 / 共'+valid.length+'条有效tick</div>';
  el.innerHTML=h;
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
    var orderSyms=orders.map(function(od){return od.symbol}).filter(Boolean);
    resolveNames(orderSyms).then(function(){
      document.querySelector('#orders-table tbody').innerHTML=orders.slice(0,50).map(function(od){
        var sideMap={buy:'买入',sell:'卖出',short:'做空',cover:'平仓',buy_open:'买开',sell_close:'卖平',buy_close:'买平',sell_open:'卖开'};
        var sideLabel=sideMap[od.side]||od.side||'-';
        var sideClass=(od.side==='buy'||od.side==='buy_open'||od.side==='cover'||od.side==='buy_close')?'green':'red';
        var canCancel=(od.status==='pending'||od.status==='submitted');
        var nm=_nameCache[od.symbol]||'';
        return '<tr><td style="white-space:nowrap">'+fmtTime(od.createdAt)+'</td><td style="font-family:monospace">'+esc(od.symbol)+'</td><td>'+esc(nm)+'</td>'+
          '<td><span class="badge badge-'+sideClass+'">'+sideLabel+'</span></td>'+
          '<td>'+num(od.price!=null?od.price:(od.avgFillPrice!=null?od.avgFillPrice:od.filledPrice))+'</td><td>'+(od.quantity||'-')+'</td>'+
          '<td><span class="badge badge-'+(od.status==='filled'?'green':od.status==='cancelled'?'red':'yellow')+'">'+esc(od.status)+'</span></td>'+
          '<td>'+(canCancel?'<button class="btn btn-sm btn-danger" onclick="cancelOrder(\\''+esc(od.orderId)+'\\')">撤单</button>':'')+'</td></tr>';
      }).join('')||'<tr><td colspan="8" style="text-align:center;color:var(--text-dim)">暂无委托</td></tr>';
    });
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

/* ══════ LIVE STRATEGIES (活跃策略管理) ══════ */
function loadLiveStrategies(){
  api('/quant/api/strategies').then(function(d){
    if(!d||d.status!=='ok')return;
    var list=d.data||[];
    document.getElementById('live-count').textContent=list.length;
    var tbody=document.querySelector('#live-strategies-table tbody');
    if(!list.length){
      tbody.innerHTML='<tr><td colspan="8" style="text-align:center;color:var(--text-dim)">暂无活跃策略</td></tr>';
      return;
    }
    tbody.innerHTML=list.map(function(s){
      var params=s.parameters||{};
      var source=params.source||'-';
      var sourceLabel=source==='pool-backtest'?'<span class="badge badge-blue">池回测</span>':
        source==='fitness'?'<span class="badge badge-green">适配</span>':
        '<span class="badge badge-yellow">'+esc(source)+'</span>';
      var symbols=s.symbols||[];
      var symText=symbols.length<=3?symbols.join(', '):(symbols.slice(0,3).join(', ')+' +'+( symbols.length-3));
      var enableBadge=s.enabled?'<span class="badge badge-green">运行中</span>':'<span class="badge badge-red">已禁用</span>';
      if(s.regimePaused)enableBadge='<span class="badge badge-yellow">体制暂停</span>';
      var budgetText=s.capitalBudget?'¥'+(s.capitalBudget/10000).toFixed(1)+'万':'<span style="color:var(--text-dim)">全账户</span>';
      var addedAt=s.addedAt?s.addedAt.replace('T',' ').slice(0,16):'';
      var toggleBtn=s.enabled
        ?'<button class="btn btn-sm btn-warning" onclick="toggleLiveStrategy(\\''+esc(s.id)+'\\',false)" title="禁用">⏸</button>'
        :'<button class="btn btn-sm btn-primary" onclick="toggleLiveStrategy(\\''+esc(s.id)+'\\',true)" title="启用">▶</button>';
      var removeBtn='<button class="btn btn-sm btn-danger" onclick="removeLiveStrategy(\\''+esc(s.id)+'\\',\\''+esc(s.strategyId)+'\\','+symbols.length+')" title="删除" style="margin-left:4px">🗑️</button>';
      return '<tr>'+
        '<td style="font-size:11px;font-family:monospace">'+esc(s.id)+'</td>'+
        '<td>'+esc(s.strategyId)+'</td>'+
        '<td title="'+esc(symbols.join(', '))+'">'+symbols.length+' <span style="font-size:10px;color:var(--text-dim)">('+esc(symText)+')</span></td>'+
        '<td>'+sourceLabel+'</td>'+
        '<td>'+budgetText+'</td>'+
        '<td>'+enableBadge+'</td>'+
        '<td style="font-size:11px;white-space:nowrap">'+esc(addedAt)+'</td>'+
        '<td style="white-space:nowrap">'+toggleBtn+removeBtn+'</td></tr>';
    }).join('');
  }).catch(function(){});
}
function toggleLiveStrategy(id,enabled){
  api('/quant/api/strategies',{method:'PATCH',body:JSON.stringify({id:id,enabled:enabled})}).then(function(d){
    toast(d&&d.status==='ok'?'✅ '+(enabled?'已启用':'已禁用'):'❌ 操作失败');
    loadLiveStrategies();
  }).catch(function(e){toast('❌ '+e)});
}
function removeLiveStrategy(id,strategyId,symbolCount){
  if(!confirm('确认删除策略？\\n\\nID: '+id+'\\n策略: '+strategyId+'\\n股票数: '+symbolCount+'\\n\\n⚠️ 删除后将停止该策略的信号生成'))return;
  api('/quant/api/strategies?id='+encodeURIComponent(id),{method:'DELETE'}).then(function(d){
    toast(d&&d.status==='ok'?'✅ 已删除':'❌ '+(d&&d.message||'删除失败'));
    loadLiveStrategies();
  }).catch(function(e){toast('❌ '+e)});
}

/* ══════ STRATEGIES ══════ */
function loadStrategies(){
  loadLiveStrategies();
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
    var syms=pa.alerts.map(function(a){return a.symbol}).filter(Boolean);
    resolveNames(syms).then(function(){
      document.querySelector('#pa-table tbody').innerHTML=pa.alerts.map(function(a){
        var nm=_nameCache[a.symbol]||'';
        var typeLabel=_condLabels[a.type]||a.type;
        return '<tr><td style="font-family:monospace">'+esc(a.symbol)+'</td><td>'+esc(nm)+'</td><td>'+esc(typeLabel)+'</td><td>'+a.threshold+'</td>'+
          '<td><span class="badge badge-'+(a.triggered?'green':a.active!==false?'yellow':'red')+'">'+(a.triggered?'已触发':a.active!==false?'监控中':'已停止')+'</span></td>'+
          '<td><button class="btn btn-sm btn-danger" onclick="removePriceAlert(\\''+esc(a.id)+'\\')">删除</button></td></tr>';
      }).join('')||'<tr><td colspan="6" style="text-align:center;color:var(--text-dim)">暂无预警</td></tr>';
    });
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
      var ts=new Date(e.timestamp).toLocaleString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'});
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
    document.getElementById('sector-members-title').textContent=sector+' ('+members.length+' 只)';
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

/* ══════ UNIFIED AI CHAT ══════ */
var _chatConversationId=null;
var _chatBusy=false;
function chatRenderConversations(rows){
  var box=document.getElementById('chat-conversations');if(!box)return;
  window._chatRows=rows||[];
  box.innerHTML=(rows||[]).map(function(c){
    return '<div style="display:flex;gap:3px;align-items:center;padding:5px;border-radius:5px;background:'+(_chatConversationId===c.id?'var(--bg2)':'transparent')+';margin-top:4px">'+
      '<button class="btn btn-sm chat-select" data-id="'+esc(c.id)+'" style="flex:1;min-width:0;text-align:left;overflow:hidden;text-overflow:ellipsis">'+esc(c.title)+'</button>'+
      '<button class="btn btn-sm chat-rename" data-id="'+esc(c.id)+'" title="重命名">✎</button>'+
      '<button class="btn btn-sm btn-danger chat-delete" data-id="'+esc(c.id)+'" title="删除">×</button></div>';
  }).join('')||'<div style="padding:10px;color:var(--text-dim);font-size:12px">暂无会话</div>';
  box.querySelectorAll('.chat-select').forEach(function(b){b.addEventListener('click',function(){chatSelectConversation(b.dataset.id)})});
  box.querySelectorAll('.chat-rename').forEach(function(b){b.addEventListener('click',function(){var c=window._chatRows.find(function(x){return x.id===b.dataset.id});chatRenameConversation(b.dataset.id,c?c.title:'')})});
  box.querySelectorAll('.chat-delete').forEach(function(b){b.addEventListener('click',function(){chatDeleteConversation(b.dataset.id)})});
}
function chatLoadConversations(){
  return api('/quant/admin/api/chat/conversations').then(function(d){chatRenderConversations(d.conversations);return d.conversations||[]});
}
function chatLoadSkills(){
  api('/quant/admin/api/chat/skills').then(function(d){
    var sel=document.getElementById('chat-skill');if(!sel)return;
    sel.innerHTML='<option value="">自动选择 Skill</option>'+(d.skills||[]).map(function(s){return '<option value="'+esc(s.id)+'">'+esc(s.name)+' · @'+esc(s.id)+'</option>'}).join('');
    if(!d.configured){document.getElementById('chat-status').textContent='未配置大模型：请在 llm 配置中设置 API Key';document.getElementById('chat-status').style.color='var(--yellow)'}
  }).catch(function(){});
}
function chatNewConversation(){
  api('/quant/admin/api/chat/conversations',{method:'POST',body:'{}'}).then(function(d){return chatLoadConversations().then(function(){chatShowConversation(d.conversation,[])})});
}
function chatShowConversation(c,messages){
  _chatConversationId=c.id;document.getElementById('chat-title').textContent=c.title;
  var box=document.getElementById('chat-messages');box.innerHTML='';
  (messages||[]).forEach(function(m){
    var row=document.createElement('div');row.style.marginBottom='14px';
    var role=document.createElement('div');role.style.cssText='font-size:11px;color:'+(m.role==='user'?'var(--accent)':'var(--green)')+';font-weight:600';role.textContent=m.role==='user'?'你':'AI 助手'+((m.skills||[]).length?' · Skill: '+m.skills.join(', '):'');
    var body=document.createElement('div');body.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere';body.textContent=m.content+(m.status==='error'?'（本次回答失败）':'');
    row.appendChild(role);row.appendChild(body);box.appendChild(row);
  });
  if(!messages||!messages.length)box.innerHTML='<div style="text-align:center;color:var(--text-dim);padding:35px">发送问题开始对话。可选自动 Skill，或使用 @skill-name 指定。</div>';
  box.scrollTop=box.scrollHeight;chatRenderConversations(window._chatRows||[]);
}
function chatSelectConversation(id){
  api('/quant/admin/api/chat/conversation?id='+encodeURIComponent(id)).then(function(d){chatShowConversation(d.conversation,d.messages)});
}
function chatRenameConversation(id,title){
  var next=prompt('输入新的会话名称',title);if(!next||!next.trim())return;
  api('/quant/admin/api/chat/conversation',{method:'PATCH',body:JSON.stringify({id:id,title:next.trim()})}).then(function(){return chatLoadConversations()}).then(function(rows){window._chatRows=rows;if(_chatConversationId===id)document.getElementById('chat-title').textContent=next.trim()});
}
function chatDeleteConversation(id){
  if(!confirm('删除此会话及其消息？'))return;
  api('/quant/admin/api/chat/conversation',{method:'DELETE',body:JSON.stringify({id:id})}).then(function(){
    if(_chatConversationId===id){_chatConversationId=null;document.getElementById('chat-title').textContent='选择或新建对话';document.getElementById('chat-messages').innerHTML=''}
    return chatLoadConversations();
  }).then(function(rows){window._chatRows=rows});
}
function chatLoad(){
  chatLoadSkills();
  chatLoadConversations().then(function(rows){
    window._chatRows=rows;
    var id=location.hash==='#ai-chat'&&sessionStorage.getItem('qt-chat-id');
    var chosen=rows.find(function(c){return c.id===id})||rows[0];
    if(chosen)chatSelectConversation(chosen.id);
  }).catch(function(err){document.getElementById('chat-status').textContent='聊天服务不可用: '+err.message});
}
function chatSend(){
  if(_chatBusy)return;
  var input=document.getElementById('chat-input'),question=input.value.trim();if(!question)return;
  var selected=document.getElementById('chat-skill').value,mention=/^@([a-z0-9-]+)\\s*/i.exec(question),skill=selected||(mention&&mention[1])||null;
  if(mention&&!selected)question=question.slice(mention[0].length).trim();
  if(!question){toast('请输入问题');return}
  var ensure=_chatConversationId?Promise.resolve():api('/quant/admin/api/chat/conversations',{method:'POST',body:'{}'}).then(function(d){_chatConversationId=d.conversation.id;document.getElementById('chat-title').textContent=d.conversation.title;return chatLoadConversations()});
  _chatBusy=true;document.getElementById('chat-send').disabled=true;document.getElementById('chat-status').textContent='思考中…';input.value='';
  var box=document.getElementById('chat-messages');if(box.querySelector('div[style*="text-align:center"]'))box.innerHTML='';
  var user=document.createElement('div');user.style.cssText='white-space:pre-wrap;margin:8px 0 14px;color:var(--accent)';user.textContent='你'+String.fromCharCode(10)+question;box.appendChild(user);
  var answer=document.createElement('div');answer.style.cssText='white-space:pre-wrap;overflow-wrap:anywhere;margin-bottom:14px';answer.textContent='AI 助手'+String.fromCharCode(10);box.appendChild(answer);box.scrollTop=box.scrollHeight;
  var headers={'Content-Type':'application/json'};if(_T)headers.Authorization='Bearer '+_T;
  ensure.then(function(){sessionStorage.setItem('qt-chat-id',_chatConversationId);return fetch(B+'/quant/admin/api/chat/send',{method:'POST',headers:headers,body:JSON.stringify({conversationId:_chatConversationId,message:question,skill:skill})})})
  .then(function(response){if(!response.ok)throw new Error('HTTP '+response.status);var reader=response.body.getReader(),decoder=new TextDecoder(),buffer='';
    function read(){return reader.read().then(function(r){if(r.done)return;buffer+=decoder.decode(r.value,{stream:true});var nl=String.fromCharCode(10),chunks=buffer.split(nl+nl);buffer=chunks.pop();chunks.forEach(function(frame){var line=frame.split(nl).find(function(x){return x.indexOf('data: ')===0});if(!line)return;var ev;try{ev=JSON.parse(line.slice(6))}catch(e){return}
      if(ev.type==='skill'){var names=(ev.skills||[]);answer.firstChild.textContent='AI 助手'+(names.length?' · Skill: '+names.join(', '):' · 通用模式')+String.fromCharCode(10)}
      else if(ev.type==='tool_start')document.getElementById('chat-tool-status').textContent='正在查询：'+ev.name;
      else if(ev.type==='tool_result')document.getElementById('chat-tool-status').textContent='已完成：'+ev.name;
      else if(ev.type==='delta'){answer.textContent+=ev.text;box.scrollTop=box.scrollHeight}
      else if(ev.type==='error'){answer.textContent+=String.fromCharCode(10)+'❌ '+ev.message;document.getElementById('chat-status').textContent='回答失败'}
      else if(ev.type==='done'){document.getElementById('chat-status').textContent='完成';document.getElementById('chat-tool-status').textContent=''}
    });return read()})}return read();
  }).catch(function(err){answer.textContent+=String.fromCharCode(10)+'❌ '+err.message;document.getElementById('chat-status').textContent='连接失败'}).finally(function(){
    _chatBusy=false;document.getElementById('chat-send').disabled=false;box.scrollTop=box.scrollHeight;
    chatLoadConversations().then(function(rows){window._chatRows=rows});
  });
}
var _chatInputEl=document.getElementById('chat-input');if(_chatInputEl)_chatInputEl.addEventListener('keydown',function(e){if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();chatSend()}});

/* ── Auto-refresh overview every 15s ── */
setInterval(function(){
  var at=document.querySelector('.tab.active');
  if(at&&at.dataset.tab==='overview')loadOverview();
},15000);

/* ── Initial load ── */
loadOverview();
`;
}
