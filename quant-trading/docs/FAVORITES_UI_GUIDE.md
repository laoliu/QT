# 股票收藏功能指南

## 功能概述

**股票收藏** (Favorites) 系统提供完整的股票收藏管理能力，包含两个层级：

- **收藏 (Favorites)** — 带丰富元数据的股票收藏（笔记、标签、优先级、目标价、信心分等）
- **自选股 (Watchlist)** — 轻量自选股列表，快速跟踪股票行情

两者均已完整实现，包含后端存储、AI 工具、聊天命令和 Web 管理界面。

## 文件结构

```
quant-trading/
├── src/
│   ├── services/
│   │   ├── favorites-store.ts          # 收藏数据存储核心（FavoritesStore 类）
│   │   └── favorites-store.test.ts     # 单元测试
│   ├── tools/
│   │   ├── favorites.ts                # AI 收藏工具（8 种操作）
│   │   └── watchlist.ts                # AI 自选股工具（4 种操作）
│   ├── commands/
│   │   └── index.ts                    # 聊天命令 /自选
│   └── http/
│       ├── admin-routes.ts             # HTTP API 端点
│       └── pages/
│           └── favorites-page.ts       # Web 管理界面
└── index.ts                            # 插件入口，注册工具
```

## 数据模型

### FavoriteStock 接口

```typescript
interface FavoriteStock {
  code: string;            // 股票代码，如 "600519"
  name?: string;           // 股票名称，如 "茅台"
  notes?: string;          // 用户笔记
  tags?: string[];         // 标签，如 ["蓝筹", "消费"]
  category?: string;       // 分类: watchlist / ideas / portfolio / shortlist
  priority?: number;       // 优先级 1-5（5 最高）
  addedAt: string;         // 添加时间（ISO 格式）
  updatedAt: string;       // 更新时间（ISO 格式）
  priceTarget?: number;    // 目标价格
  convictionScore?: number; // 信心分 1-10
}
```

## AI 工具

### favorites 工具

支持 8 种操作：

| 操作 | 说明 | 必需参数 |
| --- | --- | --- |
| `list` | 查看收藏列表 | 无（可选 category, tags, priority 筛选） |
| `add` | 添加股票到收藏 | codes |
| `update` | 更新收藏元数据 | codes |
| `remove` | 移除收藏 | codes |
| `search` | 按代码或名称搜索 | query |
| `tags` | 获取所有标签 | 无 |
| `categories` | 获取所有分类及统计 | 无 |
| `clear` | 清空所有收藏 | 无 |

示例：

```json
{ "action": "add", "codes": ["600519"], "name": "茅台", "category": "blue-chip", "priority": 5, "tags": ["消费", "蓝筹"], "priceTarget": 2500, "convictionScore": 9 }
```

```json
{ "action": "list", "category": "blue-chip", "priority": 4 }
```

### watchlist 工具

轻量自选股管理，支持 4 种操作：

| 操作 | 说明 | 必需参数 |
| --- | --- | --- |
| `list` | 查看自选股列表 | 无 |
| `add` | 添加股票 | codes |
| `remove` | 移除股票 | codes |
| `clear` | 清空自选股 | 无 |

## 聊天命令

`/watchlist`（别名 `/自选`、`/wl`）支持以下子命令：

```
/自选                   — 查看自选股列表（含实时行情）
/自选 add 600519 000858 — 添加股票（自动创建涨跌幅预警）
/自选 del 600519        — 删除股票
/自选 clear             — 清空自选股
```

- 查看时自动拉取实时行情，显示价格和涨跌幅（最多 30 只）
- 添加时自动调用 PriceAlertService 创建涨跌幅预警

## Web 管理界面

访问路径：`/quant/admin/favorites`

### 界面功能

**统计面板**
- 收藏总数、分类数、标签数

**添加/编辑表单**
- 股票代码、名称
- 分类选择、优先级（1-5）
- 目标价、信心分（1-10）
- 标签（逗号分隔）
- 备注

**收藏列表**
- 代码/名称、分类/标签、优先级
- 实时价格 & 涨跌幅（自动获取行情）
- 目标价 / 信心分、备注
- 编辑 / 删除按钮
- 按涨跌幅排序

**筛选功能**
- 实时搜索（代码、名称、备注）
- 分类筛选下拉框
- 标签筛选下拉框
- 优先级筛选下拉框
- 无行情股票筛选（标记缺少行情数据的股票）

**响应式设计**：自适应移动端和桌面端。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/quant/admin/api/favorites` | 查询收藏列表（带实时行情），支持 query/category/tag/minPriority 参数 |
| POST | `/quant/admin/api/favorites` | 添加收藏 `{ code, name, notes, category, tags, priority, priceTarget, convictionScore }` |
| PATCH | `/quant/admin/api/favorites` | 更新收藏（合并已有数据）`{ code, ... }` |
| DELETE | `/quant/admin/api/favorites` | 删除收藏 `{ code }` |

GET 返回示例：

```json
{
  "count": 2,
  "favorites": [
    {
      "code": "600519",
      "name": "茅台",
      "category": "blue-chip",
      "priority": 5,
      "tags": ["消费", "蓝筹"],
      "currentPrice": 1680.00,
      "change": 15.20,
      "changePct": 0.91
    }
  ],
  "categories": ["blue-chip", "ideas"],
  "tags": ["消费", "蓝筹", "科技"],
  "categoryCounts": { "blue-chip": 5, "ideas": 3 }
}
```

## 数据持久化

**收藏数据**保存至：`~/data/favorites.json`
**自选股数据**保存至：`~/data/watchlist.json`

- 使用 `AsyncMutex` 保证并发写入安全
- 每次增删改操作后自动持久化
- 启动时自动加载

## 集成点

| 集成 | 说明 |
| --- | --- |
| 实时行情 | Web UI 和聊天命令自动获取行情数据（通过 `marketDataProvider.getQuote`） |
| 价格预警 | `/自选 add` 命令自动创建涨跌幅预警（通过 `PriceAlertService`） |
| AI 对话 | 用户可通过自然语言管理收藏，如 "把茅台加到蓝筹收藏" |

## 单元测试

`favorites-store.test.ts` 覆盖：

- CRUD 操作（添加 / 更新 / 删除）
- 代码规范化（自动转大写）
- 按分类 / 优先级 / 标签筛选
- 搜索功能（代码 & 名称匹配）
- 标签 / 分类枚举
- 排序逻辑（优先级降序 → 日期降序）
- 文件持久化
- 并发修改安全
