# 股票收藏功能 - UI实现指南

## 📋 功能概述

新增的**股票收藏** (Favorites) 功能允许用户:

- 添加喜欢的股票到收藏（自动保存）
- 为每个股票添加元数据（笔记、标签、优先级、目标价等）
- 按分类、标签、优先级进行筛选
- 搜索、编辑、删除收藏的股票
- 在 web UI 中有专门的管理面板

## 🗂️ 文件结构

```
extensions/quant-trading/
├── src/
│   ├── services/
│   │   ├── favorites-store.ts          # 收藏数据存储和管理核心逻辑
│   │   └── favorites-store.test.ts     # 单元测试
│   └── tools/
│       └── favorites.ts                # AI可调用工具（支持CRUD操作）
└── index.ts                            # 插件入口，注册favorites工具
```

## 🔧 后端 API（已实现）

### favorites 工具 - 支持以下操作:

#### 1. **list** - 查看收藏列表

```json
{
  "action": "list",
  "category": "blue-chip", // 可选：按分类筛选
  "tags": ["consumer"], // 可选：按标签筛选
  "priority": 4 // 可选：最低优先级
}
```

**返回:**

```json
{
  "count": 2,
  "favorites": [
    {
      "code": "600519",
      "name": "茅台",
      "category": "blue-chip",
      "priority": 5,
      "notes": "看好长期",
      "tags": ["consumer", "blue-chip"],
      "addedAt": "2026-03-01T10:30:00Z",
      "priceTarget": 2500,
      "convictionScore": 9
    }
  ]
}
```

#### 2. **add** - 添加股票到收藏

```json
{
  "action": "add",
  "codes": ["600519", "000858"],
  "name": "茅台",
  "category": "blue-chip",
  "priority": 5,
  "notes": "顶级蓝筹股",
  "tags": ["consumer", "blue-chip"],
  "priceTarget": 2500,
  "convictionScore": 9
}
```

#### 3. **update** - 更新收藏股票

```json
{
  "action": "update",
  "codes": ["600519"],
  "notes": "更新的笔记",
  "priority": 4,
  "tags": ["modified"]
}
```

#### 4. **remove** - 移除收藏

```json
{
  "action": "remove",
  "codes": ["600519"]
}
```

#### 5. **search** - 搜索收藏

```json
{
  "action": "search",
  "query": "600" // 按代码或名称搜索
}
```

#### 6. **tags** / **categories** - 获取所有标签和分类

```json
{
  "action": "tags"
}
```

## 🎨 前端 UI 实现建议

### Panel/View 组件位置

推荐在 `ui/src/ui/views/` 下创建新的 favorites 面板:

```
ui/src/ui/views/
├── favorites.ts             # 主要逻辑组件
├── favorites.test.ts        # 单元测试
└── favorites-panel.ts       # 若需要独立的侧边栏/面板
```

### 1. **主列表视图** (Favorites List)

显示所有收藏的股票的表格:

| 代码   | 名称   | 分类      | 优先级 | 目标价 | 信心分 | 添加时间   | 操作      |
| ------ | ------ | --------- | ------ | ------ | ------ | ---------- | --------- |
| 600519 | 茅台   | blue-chip | ★★★★★  | 2500   | 9/10   | 2026-03-01 | 编辑 删除 |
| 000858 | 五粮液 | blue-chip | ★★★★   | 1500   | 8/10   | 2026-03-02 | 编辑 删除 |

**功能:**

- 显示所有字段（代码、名称、分类、优先级、目标价、信心分、添加时间）
- 可排序列（根据优先级、添加时间、信心分等）
- 可过滤和搜索功能

### 2. **搜索和筛选栏**

```
┌─────────────────────────────────────────┐
│ 🔍 搜索股票代码或名称...   [X]         │
├─────────────────────────────────────────┤
│ 分类: [All▼]  标签: [Add▼]  优先级: [4+]  │
└─────────────────────────────────────────┘
```

**功能:**

- **搜索框**: 实时搜索代码或名称
- **分类筛选**: Dropdown 选择（watchlist/ideas/portfolio/shortlist）
- **标签筛选**: Multi-select 选择多个标签
- **优先级筛选**: Slider 选择最低优先级

### 3. **添加/编辑对话框** (Add/Edit Dialog)

```
┌──────────────────────────────────────┐
│ ✏️  编辑收藏: 600519 (茅台)           │
├──────────────────────────────────────┤
│                                      │
│ 股票代码: [600519]        (仅读)    │
│ 股票名称: [茅台]                    │
│ 分类: [blue-chip ▼]                 │
│ 优先级: [⭐⭐⭐⭐⭐] (5星)            │
│ 目标价: [2500]                      │
│ 信心分: [9] / 10                    │
│ 标签: [consumer] [blue-chip] [X]    │
│       [Add Tag... ▼]                │
│ 笔记: [看好长期投资，           ]  │
│       [顶级蓝筹股质地...]           │
│                                      │
│ [取消]  [保存]                       │
└──────────────────────────────────────┘
```

**字段说明:**

- **股票代码**: 文本输入，格式验证
- **股票名称**: 可选，API 可预填
- **分类**: 下拉选择（或自定义）
- **优先级**: 1-5 星级评分
- **目标价**: 数字输入
- **信心分**: 1-10 滑块
- **标签**: Multi-select + 可创建新标签
- **笔记**: Textarea，支持 Markdown 预览

### 4. **快速添加按钮** (Quick Add)

在行情数据视图或股票详情中添加"添加到收藏"按钮:

```
┌─────────────────────────────────────┐
│ 600519 (茅台)  $2480                │
├─────────────────────────────────────┤
│ [❤️ 添加到收藏] [📊 行情] [📈 详情]  │
└─────────────────────────────────────┘
```

点击后跳出快速添加对话框（预填股票代码）。

### 5. **统计仪表板** (Dashboard Widget)

侧边栏显示收藏统计:

```
┌──────────────────────┐
│ 📌 股票收藏          │
├──────────────────────┤
│ 总数: 24             │
│ 🔵 Blue-chip: 8      │
│ 💡 Ideas: 10         │
│ 📊 Portfolio: 6      │
│ 🎯 平均优先级: 4.2   │
│ 🎯 平均信心分: 7.8   │
│ [查看全部 >]         │
└──────────────────────┘
```

## 💾 数据持久化

数据自动保存到:

```
~/.openclaw-dev/quant-trading/favorites.json
```

格式:

```json
[
  {
    "code": "600519",
    "name": "茅台",
    "category": "blue-chip",
    "priority": 5,
    "tags": ["consumer", "blue-chip"],
    "notes": "看好长期",
    "addedAt": "2026-03-01T10:30:00Z",
    "updatedAt": "2026-03-20T15:45:00Z",
    "priceTarget": 2500,
    "convictionScore": 9
  }
]
```

## 🔌 集成点

### 与其他工具的交互

1. **market_data**: 在收藏列表中显示实时行情
2. **stock_screen**: 将筛选结果添加到收藏
3. **trading_signal**: 为收藏股票生成交易信号提示
4. **price_alert**: 为收藏股票设置价格预警

### 与 AI Agent 的交互

用户可以让 AI 帮助管理收藏:

- "把茅台和五粮液加到我的蓝筹股收藏"
- "显示我优先级最高的5只股票"
- "搜索我收藏中的OLED概念股"
- "把这个股票从收藏中删除"

## 🧪 测试建议

### 单元测试 (已实现)

- FavoritesStore 的 CRUD 操作
- 筛选和搜索逻辑
- 持久化功能

### 集成测试 (建议补充)

```typescript
// 示例
test("should add stock via favorites tool and display in UI", async () => {
  const result = await favoritesTool.execute({
    action: "add",
    codes: ["600519"],
    name: "茅台",
    category: "blue-chip",
  });
  expect(result.added).toHaveLength(1);

  // 验证 UI 显示
  const listResult = await favoritesTool.execute({ action: "list" });
  expect(listResult.favorites[0].code).toBe("600519");
});
```

## 📱 移动端适配

对于 iOS/Android 应用，建议:

- 使用底部 Tab 或侧滑菜单导航到"收藏"
- 简化表格为卡片式布局
- 快速编辑：长按卡片进行编辑
- 支持拖拽排序（按优先级）

## 🎯 下一步工作

1. **实现前端 UI 面板**
   - 创建 React/Vue 组件
   - 集成表格、搜索、筛选功能
   - 实现添加/编辑对话框

2. **增强后端功能**（可选）
   - 导出为 CSV/Excel
   - 批量操作（批量修改标签、优先级等）
   - 收藏分享功能（导出为 URL）
   - 定时报告（周报、月报）

3. **AI 智能化**
   - 根据持仓推荐相关收藏
   - 自动分类建议
   - 收藏优化建议

4. **性能优化**
   - 对大量收藏（1000+）进行分页
   - 虚拟滚动支持
   - 本地缓存优化
