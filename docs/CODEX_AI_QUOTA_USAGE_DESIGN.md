# Codex AI 额度与用量采集设计

> 状态：已验收
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认数据口径、配置和验收。
> 本文负责：Chub 当前 Codex Runtime 的额度和用量采集、Runtime 专属设置、数据口径、安全和维护边界，遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)。
> 本文不负责：其他 Runtime 或任意 API Key 平台的额度适配，以及由调用方选择认证来源、账号、订阅、时区、浏览器页面或本机目录。

## 0. AI Agent 快速理解

把本文当作“Codex Runtime 用量快照服务”的数据契约：

1. Chub 只支持两条已实现路径：当前 Codex Runtime 的 ChatGPT 账号登录，或通过固定 Sub2API 适配器使用 OpenAI API Key；调用方不能选择来源、账号、订阅、时区、浏览器页面或本机目录。
2. ChatGPT 账号登录优先使用 Codex 账户正式日桶；当天日桶尚未生成时，才读取当前系统用户可见的 Codex Session Token，并明确标记为 `local_device`。两者不能相加。
3. Sub2API 路径只能复用已经登录的受管 Debug Chrome，从固定订阅页和仪表盘读取数据；不会自动启动浏览器、弹出登录或读取 Cookie/Authorization。
4. 周额度是形成可用快照的必需数据；今日美元用量和 Token 是可选字段。缺失字段必须是 `null` 或省略展示，不得用 `0` 猜测。
5. 失败降级只能复用同一来源、同一身份且仍在有效周期内的最近快照，并设置 `stale=true`；不能跨认证来源、账号、订阅或自然日复用。
6. 认证失败、配置错误、采集超时和上游不可用要分别保持可诊断，但额度采集失败不得阻塞 Chub 健康检查、Session、任务或其他首页卡片。
7. 后端生成长、短展示文本；微信状态、Session 回执、任务通知和受控调用方消费同一份快照，不自行重算额度或维护第二套缓存。当前首页不展示独立额度卡片。
8. `GET /api/ai/usage` 是受保护的默认 Runtime 只读接口；`refresh=true` 只触发一次共享刷新，不绕过认证、不创建后台任务。响应中的 `runtime_id=codex` 明确快照归属。

AI Agent 排障顺序：先确认认证类型和当前身份，再确认周额度快照，再判断今日 Token 来源，最后检查缓存/过期和浏览器采集；不得通过旧缓存、相邻日期、另一账号或另一来源推断当前值。Runtime 通过 `usage_snapshot` 能力提供快照，通过 `runtime_settings` 能力提供专属设置；新 Runtime 接入时必须自行实现或明确拒绝这两项能力，并同步扩展对应专项设计和验收。

## 1. 功能概览

Codex 的用量提供方固定为 OpenAI，但两条路径的实现边界不同：账号登录复用当前 Codex Runtime 的结构化账户和用量接口，不依赖 Sub2API；API Key 则由固定的 Sub2API 采集器提供，不表示 Chub 已支持任意供应商或任意 API Key 平台。用量时区属于 AI Runtime 通用配置，由“设置 → AI Runtime → 通用配置”维护；Sub2API 地址和可选订阅 ID 属于 Codex Runtime 专属设置，由“设置 → AI Runtime → Codex Runtime”维护。两类设置均保存在未提交的 `config/ai-runtimes.local.yaml`，但按 `general` 与 `codex` 分别保存。采集器固定使用该来源下的订阅页、活跃订阅接口和 Dashboard 统计接口。省略订阅 ID 时按上游返回顺序使用第一条活跃 OpenAI 订阅。Chub 将账号登录和 Sub2API 两种用量来源收敛为同一份 Codex 快照，供微信状态、Session 回执、任务通知和受控调用方复用。调用方不选择来源，Chub 根据 Codex 返回的认证类型自动路由：

| Codex 认证类型 | 数据来源 | 主要数据 |
| --- | --- | --- |
| ChatGPT 账号登录 | `account_login` | 当前 Codex Runtime 的账号周额度、账户或本机今日 Token |
| Sub2API API Key | `sub2api` | 固定 Sub2API 订阅周额度、今日美元用量和今日 Token |
| 未登录或无法确认 | 无 | 返回暂不可用 |

两种来源不会互相降级或拼接。账号接口失败不会改用浏览器数据，浏览器采集失败也不会改用账号数据。

### 1.1 账号登录方式

Chub 通过当前 Codex Runtime 的结构化账户接口获取周额度和每日 Token；这条逻辑不依赖特定 API 平台，但也不表示可适配其他供应商：

- 只使用与当前日期精确匹配的账户日桶，不拿相邻日期猜测今日用量。
- 当账户当天桶尚未生成时，汇总当前系统用户 `CODEX_HOME` 下 Codex Session 的结构化 Token 计数，返回 `tokens_scope=local_device` 并显示 `(local)`。
- 账户当天桶出现后自动使用正式值，返回 `tokens_scope=account`；正式值与本机值不会相加。
- 本机读取失败只省略今日 Token，不影响已经取得的周额度。

本机值只代表当前设备、当前系统用户可见的 Codex Session，不是跨设备的账户总量。

### 1.2 Sub2API API Key 方式

这不是通用 API Key 平台适配。Chub 复用已启动且已登录的受管 Debug Chrome，并只按固定的 Sub2API 路径和响应字段采集：

- 并行打开固定订阅页和仪表盘页，读取页面自身发起的固定用量请求。
- 订阅响应提供周额度和今日美元用量；仪表盘响应补充今日 Token。
- 周额度是形成新快照的必需数据；今日 Token 采集失败时只省略 Token。
- 查询不会自动启动 Chrome、初始化 Profile 或弹出登录流程。

Sub2API 服务来源来自 Codex Runtime 本机设置，客户端不能指定。订阅 ID 未配置时，采集器使用第一条活跃 OpenAI 订阅；需要固定特定订阅时才设置该 ID。通用时区决定额度重置时间和今日用量的日期边界，所有已接入 Runtime 复用同一值。Chub 不读取或保存 Cookie、Authorization 和浏览器存储。全局 `ai_usage` 设置及其旧节点已移除，不做读取、迁移或兼容；本机仍保留该节点时，配置校验失败，维护者应删除该节点后在相应设置页重新填写需要的项。

## 2. 统一接口

正式 HTTP 入口：

```http
GET /api/ai/usage
GET /api/ai/usage?refresh=true
```

- 普通请求优先返回共享缓存。
- `refresh=true` 请求一次强制刷新，但仍与并发请求共用同一次采集。
- 接口需要真实 loopback socket，或在配置允许时接受真实 Tailscale socket 来源。
- 旧 `/api/codex/quota` 仅保留兼容，不作为新调用入口。

内部消费者直接调用共享用量服务，不通过 HTTP 回调 Chub。

### 2.1 响应示例

```json
{
  "success": true,
  "data": {
    "status": "available",
    "runtime_id": "codex",
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
    "five_hour": {
      "remaining_percent": 42,
      "window_duration_minutes": 300,
      "resets_at": "2026-08-15T18:20:00+08:00"
    },
    "today": {
      "date": "2026-08-15",
      "used_usd": null,
      "tokens": 5600000,
      "tokens_scope": "local_device"
    },
    "display": {
      "long": "5h 42% left · Reset 8/15 18:20 · Weekly 78% left · Reset 8/20 14:44 · Today 5.6M tokens (local)",
      "short": "5h 42% · 18:20 · Today 5.6M (local)",
      "home": [
        {"kind": "five_hour", "text": "5h 42% left"},
        {"kind": "reset", "text": "Reset 8/15 18:20"},
        {"kind": "weekly", "text": "Weekly 78% left"},
        {"kind": "reset", "text": "Reset 8/20 14:44"},
        {"kind": "today", "text": "Today 5.6M tokens (local)"}
      ]
    }
  }
}
```

核心字段：

| 字段 | 说明 |
| --- | --- |
| `status` | `available` 或 `unavailable` |
| `source` | `account_login` 或 `sub2api` |
| `weekly` | 周剩余比例、可选美元额度和 weekly 独立重置时间 |
| `five_hour` | 可选的 5 小时窗口剩余比例和独立重置时间；上游未提供时为 `null` |
| `today` | 当前时区自然日、可选美元用量和 Token |
| `tokens_scope` | `account`、`local_device`；Token 为空时为 `null` |
| `stale` | 当前返回值是否为同一来源的最近有效快照 |
| `display` | 后端统一生成的长、短展示文本；`home` 为保留的结构化兼容片段，当前 Web 不使用 |

金额使用十进制字符串返回。缺失数据使用 `null`，不使用 `0` 冒充未知值。

上游不可用属于业务状态：接口仍可返回 `success=true` 和 `data.status="unavailable"`。只有认证失败、参数错误或 Chub 接口异常才返回 `ApiError`。

## 3. 展示规则

完整展示文本按以下顺序生成：

```text
5h 42% left · Reset 8/15 18:20
Weekly $781.92 left (78%) · Limit $1,000 · Reset 8/20 15:45
Today $181.02 used 100M tokens
```

`display.home` 保留为结构化兼容片段，当前首页不渲染它。`display.long` 提供完整纯文本展示；`display.short` 保持微信等短回执的紧凑格式。

完整纯文本使用相同的 `5h → Weekly → Today` 顺序；没有 5h 时以 `Weekly → Today` 展示。微信状态、Session 回执和任务通知使用短格式：有 5h 时显示 5h 剩余百分比、其重置时间和今日 Token；没有 5h 时显示 Weekly 剩余百分比、其重置日期和今日 Token。

```text
5h 42% · 18:20 · Today 100M
```

没有 5h 时：

```text
Weekly 78% · 8/20 · Today 100M
```

统一规则：

- 本机 Token 在长、短格式中都追加 `(local)`。
- Token 缺失时省略 Token，不展示虚假零值或不可用占位。
- 周额度不可用时不生成看似有效的百分比。
- 5 小时窗口是可选数据，缺失时省略，不根据 weekly 推算，也不显示为 0。
- Token 使用 `K`、`M`、`B` 紧凑格式，最多保留一位小数。
- 调用方直接使用后端 `display`，不自行维护另一套文案；微信短回执使用 `short`，其他完整文本使用 `long`。
- 当前 Web 不请求、缓存或展示用量快照，主题切换也不触发用量读取。

工作站的真实浏览器回归与用量展示解耦。运行前启动受管 Debug Chrome，然后执行：

```bash
python3 .agents/skills/chrome-cdp/scripts/chrome_debug.py start --headless
CHUB_BROWSER_TESTS=1 .venv/bin/python -m pytest tests/test_workspace_browser.py
```

## 4. 缓存与失败处理

- 后端服务在同一来源、同一身份和有效周期内复用快照；当前 Web 不保存用量会话缓存。
- 同一时间的普通读取与强制刷新共享一次受控采集，避免旧结果覆盖刷新结果。
- 共享快照有效期为 5 分钟，整次刷新总预算为 8 秒。
- 同一时刻只执行一次刷新，并发请求共享结果。
- 自然日变化、weekly 或 5 小时窗口到达重置时间时，旧缓存立即失效，不继续等待 5 分钟。
- 刷新失败时，只能保留同一来源、同一身份且仍在有效周期内的最近成功快照，并设置 `stale=true`。
- 未配置 Sub2API 订阅 ID 时，采集失败后无法确认首条订阅是否变化，因此不复用旧快照。
- 越过 weekly 重置时间后刷新仍失败，返回不可用；越过 5 小时重置时间后刷新仍失败时，不继续显示已过期的 5 小时窗口；越过自然日后不再把昨日用量显示为 `Today`。
- 新快照缺少可选 Token 时不沿用上一份 Token。
- 刷新检测到认证类型或账号身份变化时不复用旧数据；通用时区、Sub2API 地址或订阅配置保存后，下一次用量读取会使用新配置并重建该 Runtime 的采集服务，无需重启 Chub。

## 5. 安全与维护边界

- 接口不返回账号、邮箱、Session ID、文件路径、凭据或上游原始响应。
- 日志只记录固定来源和脱敏错误，不记录请求头、正文或登录信息。
- 本机统计只读取固定 `CODEX_HOME` 下的 `sessions` 和 `archived_sessions`，只解析结构化计数字段；文件数、文件大小、总字节、单行和耗时均有上限。
- Sub2API 采集只接受本机配置的服务来源和后端固定路径，在共享浏览器锁内使用本次专用页面，不影响其他自动化页面；订阅 ID 可选，省略时按固定列表顺序选择第一条活跃 OpenAI 订阅。
- 外部调用方不能切换来源、订阅、时区、文件目录或上游地址。
- 用量采集失败不得影响 Chub 健康检查、Session 管理或其他首页卡片。

## 6. 验收结果与限制

当前实现已经完成：

- 统一接口、微信状态、Session 回执和任务通知共用同一用量服务；当前首页不展示独立额度区域。
- 账号正式日桶优先，本机今日 Token 作为明确标记的降级值。
- Sub2API API Key 方式的周额度与今日 Token 通过受管浏览器采集。
- 缓存、并发合并、跨日、周重置、失败降级和敏感信息边界均有自动化测试覆盖。

真实环境验证：

- Ubuntu：Sub2API API Key 方式已验证周额度和今日 Token。
- macOS：账号登录方式已验证周额度、本机今日 Token，并与账户后续生成的日桶交叉核对；当前 Sub2API 配置已通过受管 Chrome 采集验证周额度和今日 Token。

已知限制：

- 本机 Token 不包含其他设备或不可读取的历史数据，也无法证明全天始终使用同一账号。
- Sub2API API Key 方式依赖受管 Chrome 已运行且目标页面已经登录。

### 6.1 验收范围与复检

- 已验收范围：统一用量接口、账号登录与固定 Sub2API API Key 两种来源、今日 Token 降级标记、共享缓存、跨日/周重置、失败降级和安全边界；Ubuntu 已验收 Sub2API 采集，macOS 已验收账号登录和当前 Sub2API 配置采集。
- 未验证或不承诺：其他认证来源、其他供应商、其他 API Key 平台和跨设备 Token 总量；缺少实机记录的路径不能视为已验收。
- 复检触发：上游接口、认证来源判定、额度/Token 口径、缓存身份键、受管浏览器路径、响应字段或展示契约变化时，必须重新执行自动化测试并复验受影响来源的最终数据。
