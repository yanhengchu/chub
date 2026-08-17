# Chub AI 额度与用量采集设计

> 状态：已验收。
>
> 本文记录当前已经落地的 AI 额度与今日用量能力，作为接口、数据口径和维护边界的简明说明。

## 1. 功能概览

Chub 将账号登录和 API Key 两种用量来源收敛为同一份数据，供首页、微信状态、Session 回执和任务通知复用。调用方不选择来源，Chub 根据 Codex 返回的认证类型自动路由：

| Codex 认证类型 | 数据来源 | 主要数据 |
| --- | --- | --- |
| ChatGPT 账号登录 | `account_login` | 账号周额度、账户或本机今日 Token |
| API Key | `provider_api` | 订阅周额度、今日美元用量和今日 Token |
| 未登录或无法确认 | 无 | 返回暂不可用 |

两种来源不会互相降级或拼接。账号接口失败不会改用浏览器数据，浏览器采集失败也不会改用账号数据。

### 1.1 账号登录方式

Chub 通过 Codex 的结构化账户接口获取周额度和每日 Token：

- 只使用与当前日期精确匹配的账户日桶，不拿相邻日期猜测今日用量。
- 当账户当天桶尚未生成时，汇总当前系统用户 `CODEX_HOME` 下 Codex Session 的结构化 Token 计数，返回 `tokens_scope=local_device` 并显示 `(local)`。
- 账户当天桶出现后自动使用正式值，返回 `tokens_scope=account`；正式值与本机值不会相加。
- 本机读取失败只省略今日 Token，不影响已经取得的周额度。

本机值只代表当前设备、当前系统用户可见的 Codex Session，不是跨设备的账户总量。

### 1.2 API Key 方式

Chub 复用已启动且已登录的受管 Debug Chrome：

- 并行打开固定订阅页和仪表盘页，读取页面自身发起的固定用量请求。
- 订阅响应提供周额度和今日美元用量；仪表盘响应补充今日 Token。
- 周额度是形成新快照的必需数据；今日 Token 采集失败时只省略 Token。
- 查询不会自动启动 Chrome、初始化 Profile 或弹出登录流程。

订阅地址和订阅 ID 来自本机固定配置，客户端不能指定。Chub 不读取或保存 Cookie、Authorization 和浏览器存储。

## 2. 统一接口

正式 HTTP 入口：

```http
GET /api/ai/usage
GET /api/ai/usage?refresh=true
```

- 普通请求优先返回共享缓存。
- `refresh=true` 请求一次强制刷新，但仍与并发请求共用同一次采集。
- 接口需要 Hub Token，或在配置允许时接受真实 Tailscale socket 来源。
- 旧 `/api/codex/quota` 仅保留兼容，不作为新调用入口。

内部消费者直接调用共享用量服务，不通过 HTTP 回调 Chub。

### 2.1 响应示例

```json
{
  "success": true,
  "data": {
    "status": "available",
    "provider": "openai",
    "source": "account_login",
    "timezone": "Asia/Shanghai",
    "checked_at": "2026-08-15T10:30:00+08:00",
    "stale": false,
    "message": null,
    "weekly": {
      "remaining_percent": 78,
      "used_usd": null,
      "remaining_usd": null,
      "limit_usd": null,
      "window_duration_minutes": 10080,
      "resets_at": "2026-08-20T14:44:00+08:00"
    },
    "today": {
      "date": "2026-08-15",
      "used_usd": null,
      "tokens": 5600000,
      "tokens_scope": "local_device"
    },
    "display": {
      "long": "Weekly 78% left · Today 5.6M tokens (local) · Resets 8/20 14:44",
      "short": "Weekly 78% · Today 5.6M (local)"
    }
  }
}
```

核心字段：

| 字段 | 说明 |
| --- | --- |
| `status` | `available` 或 `unavailable` |
| `source` | `account_login` 或 `provider_api` |
| `weekly` | 周剩余比例、可选美元额度和精确重置时间 |
| `today` | 当前时区自然日、可选美元用量和 Token |
| `tokens_scope` | `account`、`local_device`；Token 为空时为 `null` |
| `stale` | 当前返回值是否为同一来源的最近有效快照 |
| `display` | 后端统一生成的长、短展示文本 |

金额使用十进制字符串返回。缺失数据使用 `null`，不使用 `0` 冒充未知值。

上游不可用属于业务状态：接口仍可返回 `success=true` 和 `data.status="unavailable"`。只有认证失败、参数错误或 Chub 接口异常才返回 `ApiError`。

## 3. 展示规则

首页使用长格式：

```text
Weekly $781.92 left (78%) · Limit $1,000 · Today $181.02 used 100M tokens · Resets 8/20 15:45
```

微信状态、Session 回执和任务通知使用短格式：

```text
Weekly 78% · Today 100M
```

统一规则：

- 本机 Token 在长、短格式中都追加 `(local)`。
- Token 缺失时省略 Token，不展示虚假零值或不可用占位。
- 周额度不可用时不生成看似有效的百分比。
- Token 使用 `K`、`M`、`B` 紧凑格式，最多保留一位小数。
- 页面和通知直接使用后端 `display`，不各自维护另一套文案。
- 浏览器端由统一的 AI Usage Core 管理受保护请求、五分钟会话缓存、并发合并、强制刷新和清理；Codex 卡片与 Cyber 主题共享同一快照，不分别请求或缓存额度。
- Codex 卡片只按 `display.long` 渲染额度文本和响应式结构；Cyber 主题消费同一份只读快照，不单独请求或缓存。额度雨列的布局和动效由[前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)维护；公开页面未通过认证时不使用浏览器旧值。

首页响应式展示保持完整 `display.long` 文本，只调整分行位置：

- 完整额度包含 `Limit`、Today 美元用量和 Token。桌面双列等窄卡片显示为 `Weekly + Limit / Today + Resets` 两行；平板宽屏单列且卡片宽度至少 `40rem` 时合并为一行。
- 手机视口不超过 `420px` 且 Today 同时包含美元用量和 Token 时，完整额度显示为 `Weekly + Limit / Today / Resets` 三行。
- 账号简版没有 `Limit` 和 Today 美元用量，默认保持 `Weekly · Today tokens · Resets` 一行；手机视口不超过 `420px` 时仅将 `Resets` 放到第二行。
- `420px` 应用手机专用 `Resets` 换行，`421px` 不应用；任何视口都不得截断、遮挡或产生横向溢出。

真实浏览器回归加载正式首页、JavaScript 和 CSS，只模拟受保护 API。运行前启动受管 Debug Chrome，然后执行：

```bash
python3 .agents/skills/chrome-cdp/scripts/chrome_debug.py start --headless
CHUB_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_web_quota_browser.py
```

## 4. 缓存与失败处理

- 浏览器会话缓存沿用 `hub.aiUsageCache`，升级后无需迁移；解析失败、认证失效和退出时由统一 Core 清除，并同时通知所有页面消费者移除旧展示。
- 同一页面的普通加载并发合并；普通加载期间收到强制刷新时，在普通请求结束后只补发一次强制请求，避免旧结果覆盖刷新结果。
- 所有消费者共享 5 分钟缓存，整次刷新总预算为 8 秒。
- 同一时刻只执行一次刷新，并发请求共享结果。
- 自然日变化或周额度到达重置时间时，旧缓存立即失效，不继续等待 5 分钟。
- 刷新失败时，只能保留同一来源、同一身份且仍在有效周期内的最近成功快照，并设置 `stale=true`。
- 越过周重置时间后刷新仍失败，返回不可用；越过自然日后不再把昨日用量显示为 `Today`。
- 新快照缺少可选 Token 时不沿用上一份 Token。
- 刷新检测到认证类型或账号身份变化时不复用旧数据；订阅配置变更随 Chub 重启生效。

## 5. 安全与维护边界

- 接口不返回账号、邮箱、Session ID、文件路径、凭据或上游原始响应。
- 日志只记录固定来源和脱敏错误，不记录请求头、正文或登录信息。
- 本机统计只读取固定 `CODEX_HOME` 下的 `sessions` 和 `archived_sessions`，只解析结构化计数字段；文件数、文件大小、总字节、单行和耗时均有上限。
- 浏览器采集只接受后端固定主机、路径和订阅 ID，在共享浏览器锁内使用本次专用页面，不影响其他自动化页面。
- 外部调用方不能切换来源、订阅、时区、文件目录或上游地址。
- 用量采集失败不得影响 Chub 健康检查、Session 管理或其他首页卡片。

## 6. 验收结果与限制

当前实现已经完成：

- 首页、统一接口、微信状态和任务通知共用同一用量服务。
- 账号正式日桶优先，本机今日 Token 作为明确标记的降级值。
- API Key 方式的周额度与今日 Token 通过受管浏览器采集。
- 缓存、并发合并、跨日、周重置、失败降级和敏感信息边界均有自动化测试覆盖。

真实环境验证：

- Ubuntu：API Key 方式已验证周额度和今日 Token。
- macOS：账号登录方式已验证周额度、本机今日 Token，并与账户后续生成的日桶交叉核对。

已知限制：

- 本机 Token 不包含其他设备或不可读取的历史数据，也无法证明全天始终使用同一账号。
- API Key 方式依赖受管 Chrome 已运行且目标页面已经登录。
- macOS 的真实浏览器采集路径尚未单独复验。
