/**
 * Portal Page — unified entry point / home page for all quant-trading web features.
 *
 * Served at `/quant/` — provides a card-based navigation hub linking to:
 *  - Trading Dashboard (real-time portfolio, orders, signals, equity curve)
 *  - Admin Management Panel (services, data, alerts, strategies, config)
 *  - Investment Planner (planning, screening, backtesting, monitoring)
 *  - Stock Detail (per-symbol candlestick, quote, order book, technicals)
 *  - API documentation (endpoint list with descriptions)
 *
 * Also shows a live system status summary (engine, services, connections).
 */

export function getPortalPageHtml(baseUrl: string): string {
  const b = baseUrl.replace(/\/$/, "");
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OpenClaw 量化交易</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><text y='28' font-size='28'>📈</text></svg>">
<style>
:root{--bg:#0d1117;--bg2:#161b22;--bg3:#21262d;--border:#30363d;--text:#c9d1d9;--text-dim:#8b949e;--accent:#58a6ff;--green:#3fb950;--red:#f85149;--yellow:#d29922;--orange:#db6d28;--purple:#bc8cff}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
header{background:var(--bg2);border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
header h1{font-size:20px;font-weight:700;display:flex;align-items:center;gap:8px}
.header-right{display:flex;align-items:center;gap:16px;font-size:13px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;vertical-align:middle}
.dot-green{background:var(--green)}.dot-red{background:var(--red)}.dot-yellow{background:var(--yellow)}.dot-orange{background:var(--orange)}
main{max-width:1100px;margin:0 auto;padding:24px}

/* Status bar */
.status-bar{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:24px}
.status-chip{display:flex;align-items:center;gap:6px;background:var(--bg2);border:1px solid var(--border);border-radius:20px;padding:6px 14px;font-size:12px}

/* Section titles */
.section-title{font-size:14px;font-weight:600;color:var(--text-dim);margin-bottom:12px;display:flex;align-items:center;gap:6px;text-transform:uppercase;letter-spacing:.5px}

/* Navigation cards */
.nav-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px;margin-bottom:32px}
.nav-card{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;cursor:pointer;transition:all .2s;position:relative;overflow:hidden}
.nav-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 16px rgba(0,0,0,.3)}
.nav-card::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;border-radius:12px 12px 0 0;opacity:0;transition:opacity .2s}
.nav-card:hover::before{opacity:1}
.nav-card.card-dashboard::before{background:var(--accent)}
.nav-card.card-admin::before{background:var(--purple)}
.nav-card.card-planner::before{background:var(--green)}
.nav-card.card-stock::before{background:var(--orange)}
.nav-card.card-lianban::before{background:var(--red)}
.nav-card.card-catalyst::before{background:var(--yellow)}
.nav-card.card-datahub::before{background:linear-gradient(90deg,var(--green),var(--accent))}
.nav-card.card-lab::before{background:linear-gradient(90deg,var(--accent),var(--yellow))}
.nav-card.card-routing::before{background:linear-gradient(90deg,var(--purple),var(--accent))}
.nav-card.card-sentiment::before{background:linear-gradient(90deg,var(--yellow),var(--green))}
.nav-card.card-market::before{background:linear-gradient(90deg,var(--accent),var(--green))}
.nav-card.card-risk::before{background:linear-gradient(90deg,var(--red),var(--yellow))}
.nav-card.card-trades::before{background:linear-gradient(90deg,var(--orange),var(--accent))}
.nav-card.card-compare::before{background:linear-gradient(90deg,var(--purple),var(--green))}
.nav-card.card-strestructure::before{background:linear-gradient(90deg,var(--yellow),var(--orange))}
.nav-card.card-combo::before{background:linear-gradient(90deg,var(--accent),var(--purple))}
.nav-card.card-stockopt::before{background:linear-gradient(90deg,var(--green),var(--purple))}
.nav-card.card-risksim::before{background:linear-gradient(90deg,var(--red),var(--orange))}
.nav-card.card-portfolio-wf::before{background:linear-gradient(90deg,var(--purple),var(--accent),var(--green))}
.nav-card .icon{font-size:28px;margin-bottom:8px}
.nav-card h3{font-size:16px;font-weight:600;margin-bottom:6px}
.nav-card p{font-size:13px;color:var(--text-dim);line-height:1.5}
.nav-card .tags{display:flex;flex-wrap:wrap;gap:4px;margin-top:10px}
.nav-card .tag{background:var(--bg3);border:1px solid var(--border);border-radius:4px;padding:2px 7px;font-size:11px;color:var(--text-dim)}

/* Quick stock lookup */
.stock-lookup{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:32px}
.stock-lookup h3{font-size:14px;font-weight:600;margin-bottom:10px}
.lookup-row{display:flex;gap:8px;align-items:center}
.lookup-input{flex:1;max-width:280px;background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:8px 12px;color:var(--text);font-size:14px}
.lookup-input:focus{outline:none;border-color:var(--accent)}
.lookup-input::placeholder{color:var(--text-dim)}
.btn{display:inline-flex;align-items:center;gap:5px;padding:7px 16px;border-radius:6px;border:1px solid var(--border);background:var(--bg3);color:var(--text);font-size:13px;cursor:pointer;transition:all .15s}
.btn:hover{border-color:var(--accent);color:var(--accent)}
.btn-primary{background:#238636;border-color:#2ea043;color:#fff}
.btn-primary:hover{background:#2ea043}

/* KPI row */
.kpi-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:12px;margin-bottom:24px}
.kpi{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center;cursor:pointer;transition:all .2s;text-decoration:none;color:inherit;display:block}
.kpi:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 12px rgba(0,0,0,.3)}
.kpi .label{font-size:11px;color:var(--text-dim);margin-bottom:4px}
.kpi .value{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.kpi .sub{font-size:11px;color:var(--text-dim);margin-top:2px}

/* API reference */
.api-section{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:20px;margin-bottom:32px}
.api-section h3{font-size:14px;font-weight:600;margin-bottom:12px;cursor:pointer;display:flex;align-items:center;gap:6px}
.api-group{margin-bottom:16px}
.api-group h4{font-size:12px;color:var(--accent);margin-bottom:6px;font-weight:600}
.api-item{display:flex;gap:8px;align-items:baseline;padding:3px 0;font-size:12px}
.api-method{font-weight:700;min-width:40px;font-family:monospace;font-size:11px}
.api-method.get{color:var(--green)}.api-method.post{color:var(--orange)}.api-method.del{color:var(--red)}.api-method.patch{color:#a78bfa}
.api-path{font-family:monospace;color:var(--text);font-size:11px}
.api-desc{color:var(--text-dim);font-size:11px;margin-left:auto;text-align:right}
.api-list{display:none}
.api-list.open{display:block}
.toggle-arrow{transition:transform .2s;display:inline-block}
.toggle-arrow.open{transform:rotate(90deg)}

/* Footer */
footer{text-align:center;padding:24px;font-size:12px;color:var(--text-dim);border-top:1px solid var(--border)}

@media(max-width:700px){
  .nav-cards{grid-template-columns:1fr}
  .kpi-row{grid-template-columns:repeat(2,1fr)}
  .api-item{flex-direction:column;gap:2px}
  .api-desc{margin-left:0;text-align:left}
}
.breadcrumb{background:var(--bg);padding:6px 24px;font-size:12px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:0;flex-wrap:wrap}
.breadcrumb .current{color:var(--text)}
</style>
</head>
<body>
<div class="breadcrumb"><span class="current">🏠 首页</span></div>
<header>
  <h1>🦞 OpenClaw 量化交易</h1>
  <div class="header-right">
    <select id="modeSwitch" onchange="switchViewMode(this.value)" style="cursor:pointer;appearance:none;-webkit-appearance:none;display:inline-block;padding:4px 28px 4px 12px;border-radius:6px;font-size:13px;font-weight:600;background:rgba(210,153,34,0.15);color:#d29922;border:1px solid rgba(210,153,34,0.3);background-image:url('data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 width=%2212%22 height=%2212%22 viewBox=%220 0 12 12%22><path d=%22M3 5l3 3 3-3%22 fill=%22%238b949e%22/></svg>');background-repeat:no-repeat;background-position:right 8px center;">
      <option value="">当前模式</option>
      <option value="paper">🟡 模拟盘</option>
      <option value="live">🟢 实盘</option>
    </select>
    <span id="clock" style="color:var(--text-dim)"></span>
    <span id="engineChip" class="status-chip"><span class="dot dot-yellow"></span> 加载中…</span>
  </div>
</header>
<main>

<!-- System status -->
<div class="status-bar" id="statusBar">
  <div class="status-chip"><span class="dot dot-yellow" id="dotGateway"></span> <span id="lblGateway">网关</span></div>
  <div class="status-chip"><span class="dot dot-yellow" id="dotEngine"></span> <span id="lblEngine">引擎</span></div>
  <div class="status-chip"><span class="dot dot-yellow" id="dotData"></span> <span id="lblData">数据</span></div>
  <div class="status-chip"><span class="dot dot-yellow" id="dotAlerts"></span> <span id="lblAlerts">警报</span></div>
</div>

<!-- KPI summary -->
<div class="kpi-row" id="kpiRow">
  <a class="kpi" href="${b}/quant/dashboard/ui" target="_blank" title="查看资产详情"><div class="label">总资产</div><div class="value" id="kpiEquity">--</div></a>
  <a class="kpi" href="${b}/quant/dashboard/ui" target="_blank" title="查看资金详情"><div class="label">可用资金</div><div class="value" id="kpiCash">--</div></a>
  <a class="kpi" href="${b}/quant/dashboard/ui" target="_blank" title="查看持仓明细"><div class="label">持仓数</div><div class="value" id="kpiPositions">--</div></a>
  <a class="kpi" href="${b}/quant/dashboard/ui" target="_blank" title="查看盈亏详情"><div class="label">今日盈亏</div><div class="value" id="kpiPnl">--</div><div class="sub" id="kpiPnlPct"></div></a>
  <a class="kpi" href="${b}/quant/admin/ui" target="_blank" title="查看策略管理"><div class="label">活跃策略</div><div class="value" id="kpiStrategies">--</div></a>
  <a class="kpi" href="${b}/quant/admin/ui" target="_blank" title="查看警报中心"><div class="label">待处理警报</div><div class="value" id="kpiAlerts">--</div></a>
</div>

<!-- Navigation cards -->
<div class="section-title">📌 功能入口</div>
<div class="nav-cards">
  <a href="${b}/quant/dashboard/ui" class="nav-card card-dashboard" style="color:inherit;text-decoration:none">
    <div class="icon">📊</div>
    <h3>交易仪表盘</h3>
    <p>实时投资组合概览，持仓与订单管理，权益曲线，交易信号，SSE 实时推送</p>
    <div class="tags">
      <span class="tag">持仓</span><span class="tag">订单</span><span class="tag">权益曲线</span>
      <span class="tag">信号</span><span class="tag">引擎控制</span><span class="tag">ʵʱ</span>
    </div>
  </a>
  <a href="${b}/quant/admin/ui" class="nav-card card-admin" style="color:inherit;text-decoration:none">
    <div class="icon">🔧</div>
    <h3>管理面板</h3>
    <p>系统服务管理，数据源维护，警报中心，策略浏览，定时任务，配置查看</p>
    <div class="tags">
      <span class="tag">服务</span><span class="tag">数据</span><span class="tag">警报</span>
      <span class="tag">策略</span><span class="tag">任务</span><span class="tag">配置</span>
    </div>
  </a>
  <a href="${b}/quant/admin/planner" class="nav-card card-planner" style="color:inherit;text-decoration:none">
    <div class="icon">🚀</div>
    <h3>投资规划</h3>
    <p>投资目标设定，多策略选股，回测验证，模拟持仓监控，AI 推荐，交易信号</p>
    <div class="tags">
      <span class="tag">规划</span><span class="tag">选股</span><span class="tag">回测</span>
      <span class="tag">推荐</span><span class="tag">里程碑</span>
    </div>
  </a>
  <a href="${b}/quant/admin/favorites" class="nav-card card-admin" style="color:inherit;text-decoration:none">
    <div class="icon">⭐</div>
    <h3>股票收藏</h3>
    <p>维护你的关注股票池，支持增删改查、标签分类、优先级、目标价和投资备注。</p>
    <div class="tags">
      <span class="tag">收藏池</span><span class="tag">标签</span><span class="tag">分类</span>
      <span class="tag">备注</span><span class="tag">目标价</span>
    </div>
  </a>
  <a href="${b}/quant/admin/lianban" class="nav-card card-lianban" style="color:inherit;text-decoration:none">
    <div class="icon">🔥</div>
    <h3>连板分析</h3>
    <p>连板梯队总览，二进三候选评分，个股连板轨迹与板质评分，晋级率与炸板率</p>
    <div class="tags">
      <span class="tag">连板梯队</span><span class="tag">二进三</span><span class="tag">板质评分</span>
      <span class="tag">晋级率</span><span class="tag">情绪</span>
    </div>
  </a>
  <a href="${b}/quant/admin/auction-sniper" class="nav-card card-sniper" style="color:inherit;text-decoration:none">
    <div class="icon">🎯</div>
    <h3>竞价抢筹狙击</h3>
    <p>集合竞价（9:15-9:25）主力异动量大单抢筹检测，尾部跳涨+成交额骤增+开盘点火确认</p>
    <div class="tags">
      <span class="tag">竞价检测</span><span class="tag">大单抢筹</span><span class="tag">尾部跳涨</span>
      <span class="tag">点火确认</span><span class="tag">四维评分</span>
    </div>
  </a>
  <a href="${b}/quant/admin/event-catalyst" class="nav-card card-catalyst" style="color:inherit;text-decoration:none">
    <div class="icon">⚡</div>
    <h3>事件催化</h3>
    <p>美股科技龙头异动监控，A股对标映射，主力资金验证，稀缺性评分，催化选股</p>
    <div class="tags">
      <span class="tag">美股异动</span><span class="tag">对标映射</span><span class="tag">资金验证</span>
      <span class="tag">稀缺性</span><span class="tag">事件驱动</span>
    </div>
  </a>
  <a href="${b}/quant/admin/st-restructuring" class="nav-card card-strestructure" style="color:inherit;text-decoration:none">
    <div class="icon">🏗️</div>
    <h3>ST重整跟踪</h3>
    <p>跟踪ST股票破产重整全生命周期，预重整→正式重整→执行完毕→摘帽，公告自动分类</p>
    <div class="tags">
      <span class="tag">重整阶段</span><span class="tag">执行中</span><span class="tag">执行完毕</span>
      <span class="tag">公告跟踪</span><span class="tag">摘帽候选</span>
    </div>
  </a>
  <a href="${b}/quant/admin/datahub" class="nav-card card-datahub" style="color:inherit;text-decoration:none">
    <div class="icon">🌐</div>
    <h3>金融数据中心</h3>
    <p>宏观经济、财报日历、机构持仓、舆情分析 — 通过 AI 工具或聊天命令直接调用</p>
    <div class="tags">
      <span class="tag">/macro 宏观</span><span class="tag">/earnings 财报</span><span class="tag">/institutional 机构</span>
      <span class="tag">/sentiment 舆情</span><span class="tag">/hot-stocks 热股</span>
    </div>
  </a>
  <a href="${b}/quant/admin/strategy-lab" class="nav-card card-lab" style="color:inherit;text-decoration:none">
    <div class="icon">🧪</div>
    <h3>策略实验室</h3>
    <p>AI 驱动策略组合与探索 — 动态组合多策略、批量回测对比、自动寻优</p>
    <div class="tags">
      <span class="tag">策略组合</span><span class="tag">批量回测</span><span class="tag">AI 探索</span>
      <span class="tag">权重优化</span><span class="tag">ͶƱ/评分/级联</span>
    </div>
  </a>
  <a href="${b}/quant/admin/strategy-routing" class="nav-card card-routing" style="color:inherit;text-decoration:none">
    <div class="icon">🧭</div>
    <h3>策略路由</h3>
    <p>股票特征画像 + 自适应策略匹配 — 不同股票自动分配最优策略组合</p>
    <div class="tags">
      <span class="tag">股票画像</span><span class="tag">自适应路由</span><span class="tag">组合匹配</span>
      <span class="tag">数据驱动</span><span class="tag">规则路由</span>
    </div>
  </a>
  <a href="${b}/quant/admin/strategy-fitness" class="nav-card" style="color:inherit;text-decoration:none;border-left:3px solid var(--purple)">
    <div class="icon">🎯</div>
    <h3>策略适配分析</h3>
    <p>全市场策略×股票回测适配 — 每只股票找最优策略，全局策略排名</p>
    <div class="tags">
      <span class="tag">策略适配</span><span class="tag">全局排名</span><span class="tag">个股最优</span>
      <span class="tag">回测驱动</span><span class="tag">综合选股</span>
    </div>
  </a>
  <a href="${b}/quant/admin/sentiment-dashboard" class="nav-card card-sentiment" style="color:inherit;text-decoration:none">
    <div class="icon">🌡️</div>
    <h3>市场情绪</h3>
    <p>涨跌比、涨停跌停统计、两融余额、北向资金 — 综合市场情绪监控面板</p>
    <div class="tags">
      <span class="tag">涨跌比</span><span class="tag">涨停跌停</span><span class="tag">两融</span>
      <span class="tag">北向资金</span><span class="tag">情绪评分</span>
    </div>
  </a>
  <a href="${b}/quant/admin/market-overview" class="nav-card card-market" style="color:inherit;text-decoration:none">
    <div class="icon">📊</div>
    <h3>大盘分析</h3>
    <p>主要指数实时行情、板块热力图、领涨领跌板块、成交额统计</p>
    <div class="tags">
      <span class="tag">上证/深证/创业板</span><span class="tag">板块热力图</span><span class="tag">成交额</span>
    </div>
  </a>
  <a href="${b}/quant/admin/risk-dashboard" class="nav-card card-risk" style="color:inherit;text-decoration:none">
    <div class="icon">🛡️</div>
    <h3>风险管理</h3>
    <p>回撤曲线、VaR/夏普比率、持仓集中度、风控规则状态、风控警报一览</p>
    <div class="tags">
      <span class="tag">回撤</span><span class="tag">VaR</span><span class="tag">集中度</span>
      <span class="tag">风控规则</span><span class="tag">警报</span>
    </div>
  </a>
  <a href="${b}/quant/admin/trade-history" class="nav-card card-trades" style="color:inherit;text-decoration:none">
    <div class="icon">📋</div>
    <h3>交易记录</h3>
    <p>完整历史交易记录查询，支持日期/代码/方向筛选，CSV导出</p>
    <div class="tags">
      <span class="tag">交易历史</span><span class="tag">ɸѡ</span><span class="tag">CSV导出</span>
      <span class="tag">统计汇总</span>
    </div>
  </a>
  <a href="${b}/quant/admin/stock-compare" class="nav-card card-compare" style="color:inherit;text-decoration:none">
    <div class="icon">⚖️</div>
    <h3>股票对比</h3>
    <p>多股涨跌幅叠加对比、基本面指标横向比较、技术指标对照</p>
    <div class="tags">
      <span class="tag">涨跌对比</span><span class="tag">基本面</span><span class="tag">技术指标</span>
      <span class="tag">最多5只</span>
    </div>
  </a>
  <a href="${b}/quant/admin/portfolio-combo" class="nav-card card-combo" style="color:inherit;text-decoration:none">
    <div class="icon">🧩</div>
    <h3>跨资产组合构建</h3>
    <p>多策略/多资产组合构建，风险平价 / 波动率目标权重分配，DSR/HLZ/PBO 统计检验</p>
    <div class="tags">
      <span class="tag">组合构建</span><span class="tag">风险平价</span><span class="tag">统计检验</span>
      <span class="tag">权重扰动</span>
    </div>
  </a>
  <a href="${b}/quant/admin/stock-optimization" class="nav-card card-stockopt" style="color:inherit;text-decoration:none">
    <div class="icon">🎯</div>
    <h3>个股组合优化</h3>
    <p>每只股票找最佳策略组合，参数网格搜索，输出交易计划（买卖区间 / 止损止盈 / 仓位管理），一键应用条件单监控</p>
    <div class="tags">
      <span class="tag">多策略组合</span><span class="tag">参数优化</span><span class="tag">交易计划</span>
      <span class="tag">条件单</span><span class="tag">绩效跟踪</span>
    </div>
  </a>
  <a href="${b}/quant/admin/risk-simulator" class="nav-card card-risksim" style="color:inherit;text-decoration:none">
    <div class="icon">🛡️</div>
    <h3>风控模拟器</h3>
    <p>MDD 梯度控仓 + 波动率缩放交互式模拟，回撤阈值/恢复策略可视化调参</p>
    <div class="tags">
      <span class="tag">MDD梯度</span><span class="tag">波动率缩放</span><span class="tag">仓位控制</span>
      <span class="tag">交互模拟</span>
    </div>
  </a>
  <a href="${b}/quant/admin/conditional-orders" class="nav-card" style="color:inherit;text-decoration:none;border-left:3px solid #60a5fa">
    <div class="icon">📡</div>
    <h3>条件单监控</h3>
    <p>查看所有条件单状态，按股票分组展示止损、止盈、买卖区间提醒，支持手动触发检查与批量管理</p>
    <div class="tags">
      <span class="tag">条件单</span><span class="tag">止损止盈</span><span class="tag">买卖提醒</span>
      <span class="tag">波段网格</span>
    </div>
  </a>
  <a href="${b}/quant/admin/auto-trade" class="nav-card" style="color:inherit;text-decoration:none;border-left:3px solid #fbbf24">
    <div class="icon">🤖</div>
    <h3>自动交易调度</h3>
    <p>每日自动筛选 → 推荐 → 下单全流程，支持试运行/实盘切换、手动触发、紧急停止</p>
    <div class="tags">
      <span class="tag">自动筛选</span><span class="tag">智能推荐</span><span class="tag">定时下单</span>
      <span class="tag">紧急停止</span>
    </div>
  </a>
  <a href="${b}/quant/admin/portfolio-workflow" class="nav-card card-portfolio-wf" style="color:inherit;text-decoration:none">
    <div class="icon">🔄</div>
    <h3>组合工作流</h3>
    <p>一站式向导：策略适配分析 → Top-N 组合回测 → 跨资产组合构建 → 模拟盘应用，四步完成投资组合</p>
    <div class="tags">
      <span class="tag">策略适配</span><span class="tag">组合回测</span><span class="tag">组合构建</span>
      <span class="tag">模拟盘</span><span class="tag">全流程</span>
    </div>
  </a>
  <div class="nav-card card-stock" onclick="focusStockInput()" style="cursor:pointer">
    <div class="icon">📈</div>
    <h3>个股详情</h3>
    <p>K 线图表，实时报价，盘口深度，技术分析指标，买卖建议</p>
    <div class="tags">
      <span class="tag">K 线</span><span class="tag">报价</span><span class="tag">盘口</span>
      <span class="tag">技术分析</span><span class="tag">RSI / MACD</span>
    </div>
  </div>
</div>

<!-- Quick stock lookup -->
<div class="stock-lookup" id="stockLookup">
  <h3>🔍 快速查询个股</h3>
  <div class="lookup-row">
    <input type="text" class="lookup-input" id="symbolInput" placeholder="输入代码，如 600519、AAPL" autofocus
      onkeydown="if(event.key==='Enter')goStockInput()">
    <button class="btn btn-primary" onclick="goStockInput()">查看详情</button>
  </div>
</div>

<!-- API reference (collapsible) -->
<div class="api-section">
  <h3 onclick="toggleApi()"><span class="toggle-arrow" id="apiArrow">▶</span> API 接口文档</h3>
  <div class="api-list" id="apiList">

    <div class="api-group">
      <h4>页面入口</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/</span><span class="api-desc">管理后台首页（本页）</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/dashboard/ui</span><span class="api-desc">交易仪表盘</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/ui</span><span class="api-desc">管理面板</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/stock?symbol=…</span><span class="api-desc">个股详情页</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/planner</span><span class="api-desc">投资规划</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/lianban</span><span class="api-desc">连板分析</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/event-catalyst</span><span class="api-desc">事件催化分析</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/st-restructuring</span><span class="api-desc">ST重整跟踪</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/datahub</span><span class="api-desc">金融数据中心</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/sentiment-dashboard</span><span class="api-desc">市场情绪面板</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/market-overview</span><span class="api-desc">大盘分析</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/risk-dashboard</span><span class="api-desc">风险管理仪表板</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/trade-history</span><span class="api-desc">交易记录</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/stock-compare</span><span class="api-desc">股票对比</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/portfolio-workflow</span><span class="api-desc">组合工作流（四步向导）</span></div>
    </div>

    <div class="api-group">
      <h4>个股详情</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/stock-detail?symbol=…&market=…</span><span class="api-desc">个股详情数据 (行情/K线/财务)</span></div>
    </div>

    <div class="api-group">
      <h4>事件催化与情绪</h4>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/event-catalyst</span><span class="api-desc">事件催化引擎 (scan/check_us/map_event/catalyst_picks)</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/sentiment-signal</span><span class="api-desc">情绪信号引擎 (analyze/scan_signals/check_position/tech_frontier)</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/datahub</span><span class="api-desc">金融数据中心 (macro/earnings/institutional/sentiment/hot_stocks)</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/scan-history</span><span class="api-desc">扫描历史列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/scan-history</span><span class="api-desc">保存扫描结果</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/scan-history?id=…</span><span class="api-desc">删除扫描记录</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/us-mover-history</span><span class="api-desc">美股异动历史</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/us-mover-history</span><span class="api-desc">保存美股异动</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/us-mover-history?id=…</span><span class="api-desc">删除美股异动记录</span></div>
    </div>

    <div class="api-group">
      <h4>机构活动</h4>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/institution-activity/scan</span><span class="api-desc">机构活动扫描</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/institution-activity/detail</span><span class="api-desc">机构活动详情</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/institution-activity/history</span><span class="api-desc">机构活动历史</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/institution-activity/history</span><span class="api-desc">保存机构活动</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/institution-activity/history?id=…</span><span class="api-desc">删除机构活动记录</span></div>
    </div>

    <div class="api-group">
      <h4>连板分析</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/lianban?action=…</span><span class="api-desc">连板分析 (scan/detail/stats)</span></div>
    </div>

    <div class="api-group">
      <h4>投资规划</h4>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/investment-plan</span><span class="api-desc">生成投资计划</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/plan-history</span><span class="api-desc">投资计划历史</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/plan-history</span><span class="api-desc">保存投资计划</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/plan-history?id=…</span><span class="api-desc">删除投资计划</span></div>
      <div class="api-item"><span class="api-method patch">PATCH</span><span class="api-path">/quant/admin/api/plan-history</span><span class="api-desc">更新计划状态</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/investment-screen</span><span class="api-desc">智能选股筛选</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/investment-monitor</span><span class="api-desc">持仓监控分析</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/rebalance</span><span class="api-desc">再平衡建议</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/recommend</span><span class="api-desc">股票推荐</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/recommend-execute</span><span class="api-desc">执行推荐下单</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/daily-picks</span><span class="api-desc">每日综合精选</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/optimize-pool</span><span class="api-desc">股票池优化</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/optimize-history</span><span class="api-desc">寻优历史记录</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/backtest</span><span class="api-desc">回测引擎</span></div>
    </div>

    <div class="api-group">
      <h4>条件单</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/conditional-orders</span><span class="api-desc">条件单列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/conditional-orders</span><span class="api-desc">创建条件单</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/conditional-orders?id=…</span><span class="api-desc">删除条件单</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/engine-status</span><span class="api-desc">条件单引擎状态</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/order-fill-status</span><span class="api-desc">订单成交状态轮询</span></div>
    </div>

    <div class="api-group">
      <h4>突破监控</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/breakout-orders</span><span class="api-desc">突破监控列表 + EOD 规则</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/breakout-orders</span><span class="api-desc">取消突破监控</span></div>
    </div>

    <div class="api-group">
      <h4>行情与数据</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/quote?symbol=…</span><span class="api-desc">实时报价</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/klines?symbol=…</span><span class="api-desc">K 线数据</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/sectors</span><span class="api-desc">板块分析</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/fund-flow</span><span class="api-desc">资金流向</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/dragon-tiger</span><span class="api-desc">龙虎榜</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/query-klines?symbol=…</span><span class="api-desc">缓存优先 K 线查询</span></div>
    </div>

    <div class="api-group">
      <h4>交易与订单</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/portfolio</span><span class="api-desc">投资组合</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/orders</span><span class="api-desc">订单列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/orders</span><span class="api-desc">下单</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/api/orders?id=…</span><span class="api-desc">撤单</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/trades</span><span class="api-desc">成交记录</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/pnl</span><span class="api-desc">盈亏统计</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/confirmations</span><span class="api-desc">待确认订单</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/signal</span><span class="api-desc">信号 Webhook</span></div>
    </div>

    <div class="api-group">
      <h4>股票池</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/stocklist</span><span class="api-desc">股票池列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/stocklist</span><span class="api-desc">更新股票池</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/stocklist/history</span><span class="api-desc">股票池变更历史</span></div>
    </div>

    <div class="api-group">
      <h4>引擎控制</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/live/status</span><span class="api-desc">引擎状态</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/engine/start</span><span class="api-desc">启动引擎</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/engine/stop</span><span class="api-desc">停止引擎</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/engine/pause</span><span class="api-desc">暂停引擎</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/engine/resume</span><span class="api-desc">恢复引擎</span></div>
    </div>

    <div class="api-group">
      <h4>Admin 服务管理</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/services</span><span class="api-desc">服务状态总览</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/receiver/start</span><span class="api-desc">启动行情接收</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/receiver/stop</span><span class="api-desc">停止行情接收</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/receiver/restart</span><span class="api-desc">重启行情接收</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/engine/start</span><span class="api-desc">启动交易引擎</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/engine/stop</span><span class="api-desc">停止交易引擎</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/engine/pause</span><span class="api-desc">暂停交易引擎</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/service/engine/resume</span><span class="api-desc">恢复交易引擎</span></div>
    </div>

    <div class="api-group">
      <h4>策略管理</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/strategies</span><span class="api-desc">活跃策略列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/strategies</span><span class="api-desc">添加策略</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/templates</span><span class="api-desc">策略模板</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/user-strategies</span><span class="api-desc">自定义策略列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/user-strategies</span><span class="api-desc">创建自定义策略</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/api/user-strategies?id=…</span><span class="api-desc">删除自定义策略</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/user-strategy?id=…</span><span class="api-desc">单策略详情</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/user-strategy</span><span class="api-desc">更新单策略</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/api/user-strategy?id=…</span><span class="api-desc">删除单策略</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/strategy-compare</span><span class="api-desc">策略对比</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/strategy-ranking</span><span class="api-desc">策略排行</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/strategy-export</span><span class="api-desc">导出策略</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/strategy-import</span><span class="api-desc">导入策略</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/strategies</span><span class="api-desc">Admin 策略管理</span></div>
    </div>

    <div class="api-group">
      <h4>监控与事件</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/events</span><span class="api-desc">SSE 事件流</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/events/recent</span><span class="api-desc">最近事件 (JSON)</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/alerts</span><span class="api-desc">系统警报</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/api/alerts?id=…</span><span class="api-desc">删除警报</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/alerts/summary</span><span class="api-desc">警报摘要</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/alerts/acknowledge</span><span class="api-desc">确认警报</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/signals</span><span class="api-desc">交易信号</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/price-alerts</span><span class="api-desc">价格提醒列表</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/price-alerts</span><span class="api-desc">创建价格提醒</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/api/price-alerts?id=…</span><span class="api-desc">删除价格提醒</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/scheduled-tasks</span><span class="api-desc">定时任务</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/health</span><span class="api-desc">连接健康</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/staleness</span><span class="api-desc">数据时效</span></div>
    </div>

    <div class="api-group">
      <h4>Admin 监控</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/overview</span><span class="api-desc">系统总览</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/config</span><span class="api-desc">配置查看</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/alerts</span><span class="api-desc">系统报警</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/logs</span><span class="api-desc">日志查看</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/admin/api/logs</span><span class="api-desc">清除日志</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/events</span><span class="api-desc">最近事件</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/price-alerts</span><span class="api-desc">价格提醒</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/scheduled-tasks</span><span class="api-desc">定时任务</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/data</span><span class="api-desc">数据管理</span></div>
    </div>

    <div class="api-group">
      <h4>数据仓库</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/data-warehouse/stats</span><span class="api-desc">缓存统计</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/data-warehouse/klines</span><span class="api-desc">查询 K 线</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/data-warehouse/warmup</span><span class="api-desc">预热数据</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/data-quality/validate</span><span class="api-desc">数据质量校验</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/data-warehouse/invalidate</span><span class="api-desc">缓存失效</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/data-warehouse/warmup</span><span class="api-desc">数据预热 (Admin)</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/data-warehouse/sync</span><span class="api-desc">批量同步状态</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/admin/api/data-warehouse/sync</span><span class="api-desc">触发批量同步</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/admin/api/data-warehouse/bridge-status</span><span class="api-desc">桥接状态</span></div>
    </div>

    <div class="api-group">
      <h4>数据桥接 (Mac↔Windows)</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/data-request</span><span class="api-desc">拉取数据请求</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/data-request</span><span class="api-desc">提交数据请求</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/data-response</span><span class="api-desc">拉取数据响应</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/data-response</span><span class="api-desc">提交数据响应</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/trading-request</span><span class="api-desc">拉取交易请求</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/trading-request</span><span class="api-desc">提交交易请求</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/trading-response</span><span class="api-desc">拉取交易响应</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/trading-response</span><span class="api-desc">提交交易响应</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/trading-events</span><span class="api-desc">拉取交易事件</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/trading-events</span><span class="api-desc">推送交易事件</span></div>
    </div>

    <div class="api-group">
      <h4>回测</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/backtests</span><span class="api-desc">回测历史</span></div>
      <div class="api-item"><span class="api-method del">DEL</span><span class="api-path">/quant/api/backtests?id=…</span><span class="api-desc">删除回测</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/backtests/detail?id=…</span><span class="api-desc">回测详情</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/backtests/compare</span><span class="api-desc">回测对比</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/backtests/summary</span><span class="api-desc">回测摘要</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/backtests/star</span><span class="api-desc">收藏/取消收藏</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/backtests/tags</span><span class="api-desc">标签管理</span></div>
      <div class="api-item"><span class="api-method post">POST</span><span class="api-path">/quant/api/backtests/notes</span><span class="api-desc">回测笔记</span></div>
    </div>

    <div class="api-group">
      <h4>权益与绩效</h4>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/equity-history</span><span class="api-desc">权益历史</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/equity-history/summary</span><span class="api-desc">权益摘要</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/benchmarks</span><span class="api-desc">基准列表</span></div>
      <div class="api-item"><span class="api-method get">GET</span><span class="api-path">/quant/api/benchmark/compare</span><span class="api-desc">基准对比</span></div>
    </div>

  </div>
</div>

</main>

<footer>
  OpenClaw Quant Trading Extension · <span id="footerVersion">v1</span> · <a href="https://docs.openclaw.ai" target="_blank">文档</a>
</footer>

<script>
const B = '${b}';

// Clock
function updateClock(){
  const d = new Date();
  document.getElementById('clock').textContent =
    d.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit',weekday:'short',timeZone:'Asia/Shanghai'}) + ' ' +
    d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',second:'2-digit',timeZone:'Asia/Shanghai'});
}
updateClock(); setInterval(updateClock, 1000);

// Stock lookup
function goStockInput(){
  const sym = document.getElementById('symbolInput').value.trim();
  if(!sym) return;
  window.open(B+'/quant/admin/stock?symbol='+encodeURIComponent(sym), '_blank');
}
function focusStockInput(){
  var el = document.getElementById('stockLookup');
  if(el) el.scrollIntoView({behavior:'smooth',block:'center'});
  setTimeout(function(){ document.getElementById('symbolInput').focus(); }, 300);
}

// Toggle API docs
function toggleApi(){
  const list = document.getElementById('apiList');
  const arrow = document.getElementById('apiArrow');
  list.classList.toggle('open');
  arrow.classList.toggle('open');
}

// Fetch overview data
async function fetchJSON(url){
  try{
    const r = await fetch(url);
    if(!r.ok) return null;
    return await r.json();
  } catch{ return null; }
}

function setDot(id, color){ document.getElementById(id).className = 'dot dot-'+color; }
function fmt(n){ return n == null ? '--' : Number(n).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2}); }
function fmtInt(n){ return n == null ? '--' : String(n); }

async function loadOverview(){
  var ov;
  try { ov = await fetchJSON(B+'/quant/admin/api/overview'); } catch(e){ console.error('loadOverview fetch error', e); return; }
  if(!ov || ov.error){ console.warn('loadOverview: no data or error', ov); return; }

  // Gateway
  try {
    setDot('dotGateway', 'green');
    document.getElementById('lblGateway').textContent = '网关 运行中';
  } catch(e){ console.error('gateway chip error', e); }

  // Engine
  try {
    var es = ov.engine;
    var running = es && es.status === 'running';
    var paused = es && es.status === 'paused';
    var stopped = es && !running && !paused;
    var engineColor = running ? 'green' : paused ? 'yellow' : 'red';
    var engineLabel = running ? '运行中' : paused ? '已暂停' : '已停止';
    if(es){
      setDot('dotEngine', engineColor);
      document.getElementById('lblEngine').textContent = '引擎 ' + engineLabel;
    }
    // Always update engineChip to clear "加载中"
    var chip = document.getElementById('engineChip');
    if(chip){
      chip.innerHTML = '<span class="dot dot-'+engineColor+'"></span> 引擎 '+engineLabel +
        (es && es.strategiesCount ? ' · '+es.strategiesCount+' 策略' : '');
    }
    var kpiS = document.getElementById('kpiStrategies');
    if(kpiS) kpiS.textContent = fmtInt(es ? (es.strategiesCount || 0) : 0);
  } catch(e){ console.error('engine chip error', e); }

  // Data
  try {
    if(ov.data){
      setDot('dotData', ov.data.connected !== false ? 'green' : 'red');
      document.getElementById('lblData').textContent = '数据 ' + (ov.data.connected !== false ? '已连接' : '断开');
    }
  } catch(e){ console.error('data chip error', e); }

  // Alerts
  try {
    var alertCount = ov.alerts ? (ov.alerts.unacknowledged != null ? ov.alerts.unacknowledged : (ov.alerts.total != null ? ov.alerts.total : 0)) : 0;
    setDot('dotAlerts', alertCount > 0 ? 'orange' : 'green');
    document.getElementById('lblAlerts').textContent = alertCount > 0 ? '警报 '+alertCount+' 条' : '警报 无';
    var kpiA = document.getElementById('kpiAlerts');
    if(kpiA) kpiA.textContent = fmtInt(alertCount);
  } catch(e){ console.error('alerts chip error', e); }
}

// ── View mode: paper / live / '' (auto) ─────────────────
var portalViewMode = '';
function switchViewMode(mode) {
  portalViewMode = mode;
  var sel = document.getElementById('modeSwitch');
  if (sel) {
    sel.style.background = mode === 'live' ? 'rgba(63,185,80,0.15)' : 'rgba(210,153,34,0.15)';
    sel.style.color = mode === 'live' ? '#3fb950' : '#d29922';
    sel.style.borderColor = mode === 'live' ? 'rgba(63,185,80,0.3)' : 'rgba(210,153,34,0.3)';
  }
  Promise.all([loadOverview(), loadPortfolio()]);
}

async function loadPortfolio(){
  const modeQ = portalViewMode ? '?mode=' + portalViewMode : '';
  const p = await fetchJSON(B+'/quant/api/portfolio' + modeQ);
  if(!p || p.error) return;
  const d = p.data || p;
  const positions = d.positions || [];
  const cash = d.cash ?? d.available_cash ?? 0;
  const mv = d.marketValue ?? positions.reduce((s,po)=>s+(po.market_value ?? po.marketValue ?? (po.quantity||0)*(po.currentPrice??po.current_price??po.avgCost??0)),0);
  const equity = d.total_equity ?? d.equity ?? d.totalValue ?? (cash + mv);
  const pnl = d.profit ?? d.today_pnl ?? d.unrealized_pnl ?? 0;
  const pnlPct = d.profitPct!=null ? d.profitPct*100 : (equity > 0 ? (pnl / equity * 100) : 0);

  const isPaper = p.source==='paper';
  const isLive = p.source==='qmt'||p.source==='remote-qmt'||p.source==='live';
  const srcLabel = isLive ? ' (QMT实盘)' : isPaper ? ' (模拟盘)' : '';
  document.getElementById('kpiEquity').textContent = '¥'+fmt(equity)+srcLabel;
  // Auto-update mode switch when in auto mode
  if (!portalViewMode) {
    var sel = document.getElementById('modeSwitch');
    if (sel) {
      sel.value = isPaper ? 'paper' : isLive ? 'live' : '';
      sel.style.background = isLive ? 'rgba(63,185,80,0.15)' : 'rgba(210,153,34,0.15)';
      sel.style.color = isLive ? '#3fb950' : '#d29922';
      sel.style.borderColor = isLive ? 'rgba(63,185,80,0.3)' : 'rgba(210,153,34,0.3)';
    }
  }
  document.getElementById('kpiCash').textContent = '¥'+fmt(cash);
  document.getElementById('kpiPositions').textContent = fmtInt(positions.length);

  const pnlEl = document.getElementById('kpiPnl');
  pnlEl.textContent = (pnl>=0?'+':'')+'¥'+fmt(pnl);
  pnlEl.style.color = pnl > 0 ? 'var(--red)' : pnl < 0 ? 'var(--green)' : 'var(--text)';

  const pctEl = document.getElementById('kpiPnlPct');
  pctEl.textContent = (pnlPct>=0?'+':'')+pnlPct.toFixed(2)+'%';
  pctEl.style.color = pnlEl.style.color;
}

// Init
Promise.all([loadOverview(), loadPortfolio()]);
setInterval(() => Promise.all([loadOverview(), loadPortfolio()]), 30000);
</script>
</body>
</html>`;
}
