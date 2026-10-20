/** Admin Management Page — HTML body template */
export function getAdminBodyHtml(b: string): string {
  return `
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a><span class="sep">/</span><span class="current">🔧 管理面板</span></div>
<header>
  <h1>🔧 量化交易管理面板</h1>
  <div class="header-right">
    <a href="${b}/quant/admin/research">🧪 A 股研究 / Paper</a>
    <a href="${b}/quant/admin/favorites">⭐ 股票收藏</a>
    <a href="${b}/quant/admin/lianban">🔥 连板分析</a>
    <a href="${b}/quant/dashboard/ui">📊 交易仪表盘</a>
    <span style="color:var(--text-dim)" id="clock"></span>
  </div>
</header>
<div class="tabs">
  <div class="tab active" data-tab="overview">📊 概览</div>
  <div class="tab" data-tab="services">⚙️ 服务</div>
  <div class="tab" data-tab="data">📦 数据</div>
  <div class="tab" data-tab="alerts">🔔 告警</div>
  <div class="tab" data-tab="trading">💹 交易</div>
  <div class="tab" data-tab="strategies">📋 策略</div>
  <div class="tab" data-tab="sectors">🏷️ 板块</div>
  <div class="tab" data-tab="tasks">⏰ 任务</div>
  <div class="tab" data-tab="logs">📋 日志</div>
  <div class="tab" data-tab="ai-chat">🤖 AI 聊天</div>
  <div class="tab" data-tab="config">🔑 配置</div>
</div>

<!-- ═══ Overview ═══ -->
<div class="tab-content active" id="tab-overview">
  <div class="kpi-row" id="kpi-row"></div>
  <div class="cards">
    <div class="card event-stream-card"><h3>📡 实时事件流 <span id="event-stream-status" class="event-stream-status">最近事件</span></h3><div class="event-stream-toolbar"><span id="event-stream-meta">加载中…</span><button class="btn btn-sm" onclick="loadOverview()">🔄 刷新</button></div><div class="event-list" id="event-log">加载中…</div></div>
    <div class="card"><h3>⚠️ 最近告警</h3><div id="recent-alerts">加载中…</div></div>
  </div>
</div>

<!-- ═══ Services ═══ -->
<div class="tab-content" id="tab-services"><div class="cards" id="svc-cards"></div></div>

<!-- ═══ Data ═══ -->
<div class="tab-content" id="tab-data">
  <div class="cards" id="data-cards"></div>
  <div class="cards">
    <div class="card">      <h3>🔍 个股详情</h3>
      <div class="form-row">
        <label>代码</label><input type="text" id="stock-detail-sym" placeholder="600519" style="width:120px">
        <button class="btn btn-primary" onclick="openStockDetail()">查看详情</button>
        <span style="font-size:11px;color:var(--text-dim);margin-left:8px">K线图 / 盘口 / 技术分析 / 买卖建议</span>
      </div>
    </div>
    <div class="card">      <h3>📈 投资规划</h3>
      <div class="form-row">
        <span style="font-size:12px;color:var(--text-dim)">设定初始资金与目标，自动生成投资计划、选股建议、交易信号与持仓监控</span>
      </div>
      <div class="form-row">
        <button class="btn btn-primary" onclick="window.open(B+'/quant/admin/planner','_blank')">🚀 打开投资规划</button>
      </div>
    </div>
    <div class="card">      <h3>⭐ 股票收藏</h3>
      <div class="form-row">
        <span style="font-size:12px;color:var(--text-dim)">集中管理关注股票，支持增删改查、分类、标签、优先级和投资备注。</span>
      </div>
      <div class="form-row">
        <button class="btn btn-primary" onclick="window.open(B+'/quant/admin/favorites','_blank')">⭐ 打开收藏管理</button>
      </div>
    </div>
    <div class="card">      <h3>📊 持仓策略回测</h3>
      <div class="form-row">
        <span style="font-size:12px;color:var(--text-dim)">输入已持有的股票，用多种策略组合回测，对比不同策略的盈利表现</span>
      </div>
      <div class="form-row">
        <button class="btn btn-primary" onclick="window.open(B+'/quant/admin/portfolio-backtest','_blank')">📊 打开持仓回测</button>
      </div>
    </div>
    <div class="card">      <h3>🎯 个股组合优化</h3>
      <div class="form-row">
        <span style="font-size:12px;color:var(--text-dim)">每只股票找最佳策略组合，参数网格搜索，输出交易计划并应用条件单监控</span>
      </div>
      <div class="form-row">
        <button class="btn btn-primary" onclick="window.open(B+'/quant/admin/stock-optimization','_blank')">🎯 打开组合优化</button>
      </div>
    </div>
    <div class="card">      <h3>📡 数据同步</h3>
      <div id="sync-bridge-status" style="font-size:12px;margin-bottom:8px"></div>
      <div class="form-row">
        <label>通道</label>
        <select id="sync-source"><option value="auto">自动 (优先迅投)</option><option value="bridge">迅投桥接</option><option value="akshare">AKShare</option></select>
        <label>数量限制</label><input type="number" id="sync-max" value="0" style="width:60px" placeholder="0=全部">
      </div>
      <div class="form-row">
        <button class="btn btn-sm" onclick="checkSync()">🔍 检查更新</button>
        <button class="btn btn-primary" onclick="doSync()">📥 同步最新数据</button>
      </div>
      <div id="sync-result" style="margin-top:8px;font-size:12px"></div>
    </div>
    <div class="card">      <h3>💾 离线数据 (TuShare Parquet)</h3>
      <div id="local-data-status" style="font-size:12px;margin-bottom:8px"></div>
      <div class="form-row">
        <button class="btn btn-sm" onclick="checkLocalData()">🔍 检查状态</button>
        <button class="btn btn-primary" onclick="syncLocalData()">📥 补全最新数据</button>
      </div>
      <div id="local-data-result" style="margin-top:8px;font-size:12px"></div>
    </div>
    <div class="card">
      <h3>🔥 数据预热</h3>
      <div class="form-row"><label>代码</label><input type="text" id="warmup-symbols" placeholder="000300,600519" style="width:200px"><label>周期</label>
        <select id="warmup-interval"><option value="1d">日线</option><option value="1h">1h</option><option value="30m">30m</option><option value="15m">15m</option><option value="5m">5m</option></select>
      </div>
      <div class="form-row"><label>起始</label><input type="text" id="warmup-start" placeholder="2024-01-01" style="width:120px"><label>截止</label><input type="text" id="warmup-end" placeholder="2025-03-05" style="width:120px">
        <button class="btn btn-primary" onclick="doWarmup()">预热</button></div>
      <div id="warmup-result" style="margin-top:8px;font-size:12px"></div>
    </div>
  </div>
  <div class="cards">
    <div class="card">
      <h3>📈 K 线查询</h3>
      <div class="form-row"><label>代码</label><input type="text" id="kline-symbol" placeholder="600519" style="width:120px"><label>周期</label>
        <select id="kline-interval"><option value="1d">日线</option><option value="1h">1h</option><option value="30m">30m</option><option value="15m">15m</option><option value="5m">5m</option></select>
      </div>
      <div class="form-row"><label>起始</label><input type="text" id="kline-start" placeholder="2025-01-01" style="width:120px"><label>截止</label><input type="text" id="kline-end" style="width:120px">
        <button class="btn btn-primary" onclick="queryKlines()">查询</button></div>
      <div id="kline-result" style="margin-top:8px;overflow-x:auto"></div>
    </div>
    <div class="card" style="grid-column:1/-1">
      <h3>🔬 Tick 数据下载</h3>
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px">通过 QMT 迅投桥接下载逐笔 tick 数据，支持缓存到本地 Parquet 文件用于 tick 级回测</div>
      <div class="form-row">
        <label>代码</label><input type="text" id="tick-symbol" placeholder="600519" style="width:120px">
        <label>日期</label><input type="text" id="tick-date" placeholder="20260424" style="width:120px">
        <button class="btn btn-primary" onclick="downloadTickData()">📥 下载</button>
        <button class="btn btn-sm" onclick="queryTickData()">🔍 查询已缓存</button>
      </div>
      <div id="tick-bridge-status" style="font-size:11px;margin-top:4px"></div>
      <div id="tick-result" style="margin-top:8px;overflow-x:auto"></div>
    </div>
  </div>
  <div class="card" style="margin-top:4px"><h3>📊 缓存数据明细</h3>
    <div style="overflow-x:auto"><table id="cache-table"><thead><tr><th>代码</th><th>周期</th><th>K线数</th><th>日期范围</th><th>操作</th></tr></thead><tbody></tbody></table></div>
  </div>
</div>

<!-- ═══ Alerts ═══ -->
<div class="tab-content" id="tab-alerts">
  <div id="alert-summary-cards"></div>
  <div class="card" style="margin-top:12px">
    <h3>📋 告警列表
      <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="ackAllAlerts()">✅ 全部确认</button>
      <button class="btn btn-sm btn-danger" onclick="clearAlerts()">🗑️ 清空</button>
    </h3>
    <div style="overflow-x:auto"><table id="alert-table"><thead><tr><th>时间</th><th>级别</th><th>来源</th><th>消息</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table></div>
  </div>
</div>

<!-- ═══ Trading ═══ -->
<div class="tab-content" id="tab-trading">
  <div id="trading-summary-cards"></div>
  <div class="card" style="margin-bottom:14px"><h3>📊 持仓列表</h3>
    <div style="overflow-x:auto"><table id="positions-table"><thead><tr><th>代码</th><th>名称</th><th>方向</th><th>数量</th><th>成本</th><th>现价</th><th>盈亏</th><th>盈亏%</th></tr></thead><tbody></tbody></table></div>
  </div>
  <div class="card" style="margin-bottom:14px"><h3>📝 委托单</h3>
    <div style="overflow-x:auto"><table id="orders-table"><thead><tr><th>时间</th><th>代码</th><th>名称</th><th>方向</th><th>价格</th><th>数量</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table></div>
  </div>
  <div class="card" style="margin-top:4px"><h3>📈 信号日志</h3>
    <div style="overflow-x:auto"><table id="signals-table"><thead><tr><th>时间</th><th>代码</th><th>方向</th><th>价格</th><th>策略</th><th>置信度</th><th>原因</th></tr></thead><tbody></tbody></table></div>
  </div>
</div>

<!-- ═══ Strategies ═══ -->
<div class="tab-content" id="tab-strategies">
  <input type="text" class="search-box" placeholder="🔍 搜索策略…" id="strat-search">

  <h3 style="margin-bottom:10px;font-size:14px">🤖 活跃策略 (<span id="live-count">0</span>) <button class="btn btn-sm" onclick="loadLiveStrategies()" style="margin-left:8px">🔄 刷新</button></h3>
  <div style="overflow-x:auto;margin-bottom:16px">
    <table id="live-strategies-table">
      <thead><tr><th>ID</th><th>策略</th><th>股票数</th><th>来源</th><th>资金池</th><th>状态</th><th>添加时间</th><th>操作</th></tr></thead>
      <tbody></tbody>
    </table>
  </div>

  <h3 style="margin-bottom:10px;font-size:14px">📈 内置策略 (<span id="builtin-count">0</span>)</h3>
  <div class="cards" id="builtin-cards"></div>
  <h3 style="margin:16px 0 10px;font-size:14px">📁 用户策略 (<span id="user-count">0</span>)</h3>
  <div class="cards" id="user-cards"></div>
  <h3 style="margin:16px 0 10px;font-size:14px">📋 策略模板 (<span id="template-count">0</span>)</h3>
  <div class="cards" id="template-cards"></div>
</div>

<!-- ═══ Tasks ═══ -->
<div class="tab-content" id="tab-tasks">
  <div class="card" style="margin-bottom:14px"><h3>⏰ 定时任务</h3>
    <div style="overflow-x:auto"><table id="tasks-table"><thead><tr><th>ID</th><th>描述</th><th>状态</th><th>计划时间</th><th>创建时间</th><th>操作</th></tr></thead><tbody></tbody></table></div>
  </div>
  <div class="card">
    <h3>🔔 价格预警 (<span id="pa-count">0</span> 活跃)</h3>
    <div class="form-row">
      <input type="text" id="pa-symbol" placeholder="代码 600519" style="width:100px">
      <select id="pa-type"><option value="price_above">价格≥</option><option value="price_below">价格≤</option><option value="change_pct_above">涨幅≥%</option><option value="change_pct_below">跌幅≤%</option><option value="volume_above">量≥</option></select>
      <input type="number" id="pa-threshold" placeholder="阈值" style="width:80px">
      <button class="btn btn-primary btn-sm" onclick="addPriceAlert()">+ 添加</button>
    </div>
    <div style="margin-top:8px"><table id="pa-table"><thead><tr><th>代码</th><th>名称</th><th>条件</th><th>阈值</th><th>状态</th><th>操作</th></tr></thead><tbody></tbody></table></div>
  </div>
</div>

<!-- ═══ Logs ═══ -->
<div class="tab-content" id="tab-logs">
  <div class="kpi-row" id="log-summary-row"></div>
  <div class="card">
    <h3>📋 系统日志
      <span style="flex:1"></span>
      <button class="btn btn-sm" onclick="loadLogs()" style="margin-right:6px">🔄 刷新</button>
      <button class="btn btn-sm" id="log-auto-btn" onclick="toggleLogAuto()" style="margin-right:6px">▶ 自动刷新</button>
      <button class="btn btn-sm btn-danger" onclick="clearLogs()">🗑️ 清空</button>
    </h3>
    <div class="form-row" style="margin-top:10px">
      <label>级别</label>
      <select id="log-level" onchange="loadLogs()">
        <option value="">全部</option>
        <option value="debug">debug</option>
        <option value="info" selected>info</option>
        <option value="warn">warn</option>
        <option value="error">error</option>
      </select>
      <label>来源</label>
      <input type="text" id="log-source" placeholder="如 quant-trading" style="width:140px">
      <label>搜索</label>
      <input type="text" id="log-search" placeholder="关键词…" style="width:160px">
      <label>数量</label>
      <input type="number" id="log-limit" value="200" style="width:60px" min="10" max="2000">
      <button class="btn btn-primary btn-sm" onclick="loadLogs()">查询</button>
    </div>
    <div id="log-entries" class="log-box" style="max-height:600px;margin-top:10px">加载中…</div>
    <div id="log-status" style="font-size:11px;color:var(--text-dim);margin-top:6px"></div>
  </div>
</div>

<!-- ═══ Sectors ═══ -->
<div class="tab-content" id="tab-sectors">
  <div class="cards">
    <div class="card">
      <h3>🏷️ 板块索引概览 <span style="flex:1"></span><button class="btn btn-sm" onclick="loadSectors()">🔄 刷新</button><button class="btn btn-sm" style="margin-left:6px" onclick="reloadSectorCSV()">📥 重新加载 CSV</button></h3>
      <div id="sector-stats" style="font-size:12px;margin-bottom:8px">加载中…</div>
      <div style="margin-bottom:8px">
        <input type="text" id="sector-search" placeholder="🔍 搜索板块…" style="width:200px;padding:4px 8px;font-size:12px;border:1px solid var(--border);border-radius:4px;background:var(--bg);color:var(--text)" oninput="filterSectors()">
      </div>
      <div style="overflow-x:auto"><table id="sector-table"><thead><tr><th>板块</th><th>股票数</th><th>自由流通市值(亿)</th><th>操作</th></tr></thead><tbody></tbody></table></div>
    </div>
  </div>
  <div class="cards">
    <div class="card">
      <h3>📋 板块成员 <span id="sector-members-title" style="font-size:12px;color:var(--text-dim);margin-left:8px">点击上方板块查看</span></h3>
      <div style="overflow-x:auto;max-height:500px"><table id="sector-members-table"><thead><tr><th>代码</th><th>名称</th><th>板块</th><th>自由流通市值(亿)</th></tr></thead><tbody></tbody></table></div>
    </div>
  </div>
  <div class="cards">
    <div class="card">
      <h3>📤 上传 CSV <span style="font-size:11px;color:var(--text-dim);margin-left:8px">格式: stock_code,stock_name,sector,free_float_mv</span></h3>
      <div class="form-row">
        <input type="file" id="sector-csv-file" accept=".csv" style="font-size:12px">
        <button class="btn btn-primary" onclick="uploadSectorCSV()">上传并更新</button>
      </div>
      <div id="sector-upload-result" style="margin-top:8px;font-size:12px"></div>
    </div>
  </div>
</div>

<div class="tab-content" id="tab-ai-chat">
  <div style="display:flex;gap:10px;height:calc(100vh - 150px);min-height:420px">
    <aside class="card" style="width:240px;min-width:190px;display:flex;flex-direction:column;overflow:auto">
      <button class="btn btn-primary" onclick="chatNewConversation()">＋ 新建对话</button>
      <div id="chat-conversations" style="margin-top:10px"></div>
    </aside>
    <section class="card" style="flex:1;min-width:0;display:flex;flex-direction:column">
      <div style="display:flex;align-items:center;gap:8px;border-bottom:1px solid var(--border);padding-bottom:8px">
        <b id="chat-title">选择或新建对话</b><span style="flex:1"></span><span id="chat-status" style="font-size:11px;color:var(--text-dim)">就绪</span>
      </div>
      <div id="chat-messages" style="flex:1;overflow:auto;padding:12px;line-height:1.65"></div>
      <div id="chat-tool-status" style="font-size:11px;color:var(--text-dim);min-height:18px"></div>
      <div style="display:flex;gap:8px;align-items:flex-end">
        <select id="chat-skill" aria-label="Skill 选择" title="Skill 选择" onchange="chatDescribeSkill()" style="max-width:260px;padding:8px;background:var(--bg2);color:var(--text);border:1px solid var(--border);border-radius:6px"><option value="">自动选择 Skill</option></select>
        <textarea id="chat-input" placeholder="输入问题，或以 @skill-name 指定 Skill（Enter 发送，Shift+Enter 换行）" rows="2" style="flex:1;background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px 12px;color:var(--text);font:inherit;resize:vertical"></textarea>
        <button class="btn btn-primary" id="chat-send" onclick="chatSend()">发送</button>
      </div>
      <div id="chat-skill-description" role="status" style="font-size:12px;color:var(--text-dim);padding-top:5px">自动结合当前问题和最近对话选择主 Skill，跨领域问题可搭配辅助 Skill。</div>
      <div style="font-size:10px;color:var(--text-dim);margin-top:4px">项目 Skill 为分析指引；可调用只读行情与分析工具，不执行交易或配置写操作。</div>
    </section>
  </div>
</div>

<!-- ═══ Config ═══ -->
<div class="tab-content" id="tab-config">
  <div class="card"><h3>🔑 当前配置 (只读) <span style="flex:1"></span><button class="btn btn-sm" onclick="loadConfig()">🔄 刷新</button></h3>
    <div class="json-box" id="config-json">加载中…</div>
  </div>
</div>

<div class="toast" id="toast"></div>
`;
}
