/**
 * HTML body for the Event Catalyst page.
 */
export function getEventCatalystBodyHtml(b: string): string {
  return `
<div class="breadcrumb"><a href="${b}/quant/">🏠 首页</a> / <span style="color:var(--text)">⚡ 事件催化</span></div>
<header>
  <h1>⚡ 事件催化分析</h1>
  <div class="header-right">
    <a href="${b}/quant/admin/planner">📈 投资规划</a>
    <a href="${b}/quant/admin/ui">🔧 管理</a>
    <span style="color:var(--text-dim)" id="clock"></span>
  </div>
</header>
<div class="tabs">
  <div class="tab active" data-tab="scan">🔍 全局扫描</div>
  <div class="tab" data-tab="calendar">📅 事件日历</div>
  <div class="tab" data-tab="us">📊 美股异动</div>
  <div class="tab" data-tab="mapping">🔗 对标映射</div>
  <div class="tab" data-tab="picks">🎯 催化选股</div>
  <div class="tab" data-tab="sentiment">📰 利好利空</div>
  <div class="tab" data-tab="frontier">🔬 科技前沿</div>
</div>
<main>

<!-- ═══ Tab: Full Scan ═══ -->
<div class="tab-content active" id="tab-scan">
  <div class="param-row">
    <label>异动阈值 (%)</label>
    <input type="number" class="input" id="scanThreshold" value="2" step="0.5" min="0.5" max="10" style="width:80px">
    <label>资金验证</label>
    <select class="input" id="scanFlowReq" style="width:100px">
      <option value="true" selected>开启</option>
      <option value="false">关闭</option>
    </select>
    <label>ǰN</label>
    <input type="number" class="input" id="scanTopN" value="10" min="3" max="30" style="width:70px">
    <button class="btn btn-primary" onclick="runScan()">🚀 开始扫描</button>
  </div>
  <div id="scan-history-panel" style="display:none;margin-bottom:16px">
    <div class="card">
      <h3 style="display:flex;align-items:center;justify-content:space-between">
        <span>📋 扫描历史</span>
        <span id="scan-history-count" style="font-size:12px;color:var(--text-dim);font-weight:400"></span>
      </h3>
      <div id="scan-history-top-picks" style="display:none;margin-bottom:12px"></div>
      <div id="scan-history-list" style="max-height:300px;overflow-y:auto"></div>
    </div>
  </div>
  <div class="loading" id="scan-loading" style="display:none"><div class="spinner"></div><br>扫描美股异动 + 映射A股对标 + 验证资金流向…</div>
  <div id="scan-content" style="display:none"></div>
  <div id="scan-placeholder" class="empty-state">
    <div class="icon">⚡</div>
    <h3>事件催化全局扫描</h3>
    <p>扫描15只美股科技龙头异动，自动映射A股对标股，叠加主力资金流验证</p>
    <p style="margin-top:8px;font-size:12px;color:var(--text-dim)">覆盖 NVDA · TSLA · AAPL · MSFT · GOOGL · META · AMD · AVGO · ASML · TSM · SMCI · ARM · MRVL · PLTR · QCOM</p>
  </div>
</div>

<!-- ═══ Tab: Event Calendar ═══ -->
<div class="tab-content" id="tab-calendar">
  <div class="param-row">
    <label>查询范围</label>
    <select class="input" id="calScope" style="width:130px" onchange="toggleCalSymbolInput()">
      <option value="upcoming" selected>全市场事件</option>
      <option value="watchlist">自选股事件</option>
      <option value="portfolio">持仓股事件</option>
      <option value="stock">指定股票</option>
    </select>
    <span id="calSymbolWrap" style="display:none">
      <label>股票代码</label>
      <input type="text" class="input" id="calSymbols" placeholder="如 600519,000858" style="width:180px">
    </span>
    <label>天数</label>
    <input type="number" class="input" id="calDays" value="30" min="7" max="180" style="width:70px">
    <label>事件类型</label>
    <select class="input" id="calTypes" style="width:130px" multiple>
      <option value="all" selected>全部事件</option>
      <option value="unlock">限售解禁</option>
      <option value="dividend">分红除权</option>
      <option value="earnings">财报披露</option>
      <option value="insider">增减持</option>
      <option value="shareholder_meeting">股东大会</option>
      <option value="repurchase">股份回购</option>
    </select>
    <button class="btn btn-primary" onclick="loadCalendar()">📅 查询事件</button>
  </div>
  <div class="loading" id="cal-loading" style="display:none"><div class="spinner"></div><br>加载事件日历…</div>
  <div id="cal-content" style="display:none"></div>
  <div id="cal-placeholder" class="empty-state">
    <div class="icon">📅</div>
    <h3>个股事件日历与催化剂追踪</h3>
    <p>聚合限售股解禁 · 股东大会 · 财报披露 · 分红除权 · 增减持公告 · 股份回购</p>
    <p style="margin-top:8px;font-size:12px;color:var(--text-dim)">支持自选股/持仓股自动关联，负面事件自动预警</p>
  </div>
</div>

<!-- ═══ Tab: US Movers ═══ -->
<div class="tab-content" id="tab-us">
  <div class="param-row">
    <label>异动阈值 (%)</label>
    <input type="number" class="input" id="usThreshold" value="2" step="0.5" min="0.5" max="10" style="width:80px">
    <button class="btn btn-primary btn-sm" onclick="loadUS()">🔄 刷新美股</button>
  </div>
  <div class="loading" id="us-loading" style="display:none"><div class="spinner"></div><br>获取美股行情和新闻…</div>
  <div id="us-content" style="display:none"></div>
  <div id="us-placeholder" class="empty-state">
    <div class="icon">📊</div>
    <h3>美股科技龙头异动监控</h3>
    <p>实时监控美股科技龙头涨跌、新闻事件、主题检测</p>
  </div>
</div>

<!-- ═══ Tab: Mapping ═══ -->
<div class="tab-content" id="tab-mapping">
  <div class="param-row">
    <label>美股代码</label>
    <select class="input" id="mapSymbol" style="width:140px">
      <option value="NVDA">NVDA (英伟达)</option>
      <option value="TSLA">TSLA (特斯拉)</option>
      <option value="AAPL">AAPL (苹果)</option>
      <option value="MSFT">MSFT (微软)</option>
      <option value="GOOGL">GOOGL (谷歌)</option>
      <option value="META">META (Meta)</option>
      <option value="AMD">AMD (AMD)</option>
      <option value="AVGO">AVGO (博通)</option>
      <option value="ASML">ASML (ASML)</option>
      <option value="TSM">TSM (台积电)</option>
      <option value="SMCI">SMCI (超微)</option>
      <option value="ARM">ARM (ARM)</option>
      <option value="MRVL">MRVL (美满)</option>
      <option value="PLTR">PLTR (Palantir)</option>
      <option value="QCOM">QCOM (高通)</option>
    </select>
    <label>主题筛选</label>
    <input type="text" class="input" id="mapThemes" placeholder="如 AIоƬ,光模块 (可选)" style="width:180px">
    <button class="btn btn-primary btn-sm" onclick="loadMapping()">🔗 查看映射</button>
  </div>
  <div class="loading" id="map-loading" style="display:none"><div class="spinner"></div><br>加载对标映射…</div>
  <div id="map-content" style="display:none"></div>
  <div id="map-placeholder" class="empty-state">
    <div class="icon">🔗</div>
    <h3>美股 → A股对标映射</h3>
    <p>选择一只美股，查看对应的A股产业链标的，按稀缺性评分排序</p>
  </div>
</div>

<!-- ═══ Tab: Catalyst Picks ═══ -->
<div class="tab-content" id="tab-picks">
  <div class="param-row">
    <label>异动阈值 (%)</label>
    <input type="number" class="input" id="picksThreshold" value="2" step="0.5" min="0.5" max="10" style="width:80px">
    <label>资金验证</label>
    <select class="input" id="picksFlowReq" style="width:100px">
      <option value="true" selected>开启</option>
      <option value="false">关闭</option>
    </select>
    <label>ǰN</label>
    <input type="number" class="input" id="picksTopN" value="10" min="3" max="30" style="width:70px">
    <button class="btn btn-primary" onclick="loadPicks()">🎯 催化选股</button>
  </div>
  <div class="loading" id="picks-loading" style="display:none"><div class="spinner"></div><br>执行催化选股全流程…</div>
  <div id="picks-content" style="display:none"></div>
  <div id="picks-placeholder" class="empty-state">
    <div class="icon">🎯</div>
    <h3>事件催化选股</h3>
    <p>完整流程: 美股异动检测 → A股对标映射 → 主力资金验证 → 综合评分排序</p>
  </div>
</div>

<!-- ═══ Tab: Sentiment Signal ═══ -->
<div class="tab-content" id="tab-sentiment">
  <div class="param-row">
    <label>股票代码</label>
    <input type="text" class="input" id="sentSymbol" placeholder="如 002281" style="width:140px"
      onkeydown="if(event.key==='Enter')loadSentiment()">
    <button class="btn btn-primary btn-sm" onclick="loadSentiment()">📰 分析利好利空</button>
  </div>
  <div class="loading" id="sent-loading" style="display:none"><div class="spinner"></div><br>分析新闻可信度 + 利好利空信号 + 市场位置…</div>
  <div id="sent-content" style="display:none"></div>
  <div id="sent-placeholder" class="empty-state">
    <div class="icon">📰</div>
    <h3>利好利空多维分析</h3>
    <p>消息可信度评分 · 实质内容 vs 概念炒作 · 市场位置研判 · 利好出尽/利空出尽判断</p>
    <p style="margin-top:8px;font-size:12px;color:var(--text-dim)">一看实质(是否真金白银) · 二看位置(高位还是低位) · 三看大势(多头还是空头)</p>
  </div>
</div>

<!-- ═══ Tab: Tech Frontier ═══ -->
<div class="tab-content" id="tab-frontier">
  <div class="param-row">
    <button class="btn btn-primary btn-sm" onclick="loadFrontier()">🔬 查看科技前沿</button>
  </div>
  <div class="loading" id="frontier-loading" style="display:none"><div class="spinner"></div><br>加载科技前沿数据库…</div>
  <div id="frontier-content" style="display:none"></div>
  <div id="frontier-placeholder" class="empty-state">
    <div class="icon">🔬</div>
    <h3>科技前沿追踪</h3>
    <p>硅光技术 · CPO封装 · 液冷散热 · 自主可控材料 · 先进封装 · HBM · 人形机器人 · 卫星互联网</p>
    <p style="margin-top:8px;font-size:12px;color:var(--text-dim)">追踪科技前沿突破 → 映射A股正宗标的 → 降低踩雷概率</p>
  </div>
</div>

</main>`;
}
