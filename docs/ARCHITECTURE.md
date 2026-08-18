# 架构说明

Model Price Hub 是一个 Browser/Server 架构的大模型价格与生命周期监控系统。

## 总体架构

```text
浏览器前端
  └─ FastAPI
      ├─ Catalog Service       官方模型目录与生命周期
      ├─ Pricing Service       官方/第三方价格抓取与快照
      ├─ Discovery Service     RSS、公告、搜索和页面监控
      ├─ Agent Worker          OCR、截图、页面差异和上下文复核
      └─ Config Service        来源、Webhook、代理和调度配置
```

## 四层职责

### 1. 官方模型目录层

输入：官方模型目录和模型介绍页。

输出：

- 当前官方模型清单
- 模型上线事件 `model_added`
- 模型下线事件 `model_removed`

规则：

- 只有官方目录能决定模型生命周期。
- 价格抓取失败不能触发模型下线。
- 初次建立基线不发送历史模型通知。

### 2. 官方价格层

输入：官方价格页面。

输出：

- 正式价格快照
- 多维价格条件
- `price_changed`

支持维度：

- 服务档位
- 模态
- 计费单位
- 缓存状态
- 上下文范围
- 时间窗口
- 生效/失效时间

规则：

- 官方结构化抓取成功时自动应用。
- 第三方价格不参与官方价格。
- 官方页面异常进入 Agent 自动复核。

### 3. 监控层

输入：

- RSS
- 官方公告
- 官方页面变化
- SearXNG 搜索
- 第三方价格旁证

输出：

- 变更候选
- 页面证据
- 上下文摘要
- Agent 复核任务

监控层不能直接修改正式模型目录或正式价格。

### 4. 配置层

管理：

- 官方来源
- Agent 类型
- Webhook
- 代理
- 调度周期
- 任务状态

## Agent 复核

当官方页面出现以下情况时：

- 抓取失败
- 返回空结果
- 条目数骤降
- 页面结构变化
- 价格数值异常

系统自动创建任务：

```text
queued → running → verified / retry
```

Worker 会：

1. 使用 Playwright 打开官方页面。
2. 保存渲染 HTML。
3. 保存完整页面截图。
4. 使用同一解析器重新解析。
5. 自动批准或进入重试。

## 通知边界

- `model_added` / `model_removed`：只来自 Catalog。
- `price_changed`：只来自官方价格层。
- 第三方价格：不触发官方通知。
- 测试消息：仅用于 Webhook 配置验证。

## 存储

```text
data/prices.db          正式价格数据库
data/latest.json        当前价格快照
data/snapshots/         历史价格快照
data/catalog.db         官方模型目录快照
data/discovery.db       监控候选与来源记录
data/sources.json       来源配置
data/agent-tasks.json   Agent 任务
data/agent-approvals.json Agent 自动审批结果
```
