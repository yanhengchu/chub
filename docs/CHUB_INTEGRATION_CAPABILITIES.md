# Chub 集成能力清单

> 状态：持续维护。本文是“当前能调用什么”的唯一清单，只登记入口、完整指令、插件和固定 API，不维护实现与协议细节。

具体行为、安全、并发、状态和消息格式规则见对应设计文档；项目整体功能与使用方式见 [README](../README.md)。

## 1. 使用入口

| 入口 | 使用场景 | 主要能力 |
| --- | --- | --- |
| 电脑端 `chub` CLI | 在 Chub 所在电脑安装、维护和排查服务 | 管理 Web 与 Quick Worker、查看日志、发送预配置通知 |
| OpenClaw Agent Tool | OpenClaw TUI 或未进入微信 Chub 模式的 Agent 调用 | 查询 Chub 基础状态、发送预配置飞书通知 |
| 微信 ClawBot | 已授权 Owner 通过私聊远程使用 Chub | 查询摘要、管理 Codex Session、提交文字或语音任务并接收结果 |

电脑端 CLI 与微信 ClawBot 是两套独立指令：前者用于本机服务运维，后者经 OpenClaw 转发到 Chub。

## 2. 电脑端命令

### 2.1 Chub CLI

- `chub`
- `chub help`、`chub -h`、`chub --help`
- `chub install`
- `chub uninstall`
- `chub start`
- `chub stop`
- `chub restart`
- `chub status`
- `chub worker-health`
- `chub logs`

### 2.2 通知指令

- `chub notification validate`
- `chub notification list`
- `chub notification test --target <target>`
- `chub notification send --target <target> --message <message>`
- `chub notification send --target <target> --message <message> --mention-all`
- `chub notification send --target <target> --message <message> --mention-recipient <recipient> [--mention-recipient <recipient> ...]`

完整安装条件、平台差异和命令说明见 [README](../README.md)。

## 3. 插件与固定 API

### 3.1 插件

| 插件 | 状态 | 功能 |
| --- | --- | --- |
| Chub OpenClaw 插件 | 已实现 | 提供受限 Chub Tool、微信消息转发和飞书通知原文保护 |
| 腾讯微信插件 | 已接入 | 提供 ClawBot 账号绑定、微信消息收发和可信语音转写 |

### 3.2 Chub OpenClaw 插件能力

| 能力 | 类型 | 状态 | 场景与功能 |
| --- | --- | --- | --- |
| `chub_get_status` | Agent Tool | 已实现 | OpenClaw Agent 查询 Gateway 所在节点的 Chub 基础状态 |
| `chub_send_notification` | Agent Tool | 已实现 | OpenClaw Agent 向 Chub 预配置的飞书目标发送消息 |
| 微信 `before_dispatch` | Hook | 已实现 | 将可信微信私聊转发到 Chub 统一调度接口 |
| 飞书原文保护 | Hook | 已实现 | 按 `runId` 关联可信原文，约束通知内容来源 |

### 3.3 固定 API

| 请求 | 调用场景 | 功能 |
| --- | --- | --- |
| `GET /api/status` | `chub_get_status` | 查询节点健康和基础状态 |
| `GET /api/ai/usage` | Chub 首页、受控调用方 | 查询统一 AI 周额度、今日用量和重置时间；账号当天桶延迟时返回明确标记的本机 Token |
| `POST /api/notifications/send` | `chub_send_notification` | 向预配置目标发送通知 |
| `POST /api/openclaw/wechat-chub-mode/dispatch` | 微信 `before_dispatch` | 调度可信微信私聊 |

## 4. 微信 ClawBot 指令

英文指令与中文别名按行一一对应。

| 英文指令 | 中文别名 |
| --- | --- |
| `chub` | `查询状态`、`状态查询`、`检查状态`、`状态检查` |
| `help` | `帮助` |
| `restart` | `重启`、`重新启动` |
| `sync` | `同步状态`、`状态同步` |
| `session new [<task>]` | `新建会话 [正文]` |
| `rename <title>` | `重命名 <标题>` |
| `session switch N [<task>]` | `切换N [正文]`、`切换会话N [正文]`、`会话N [正文]` |
| `session stop N` | `停止N`、`停止会话N` |
| `session archive N` | `归档N`、`归档会话N` |
| `session retry` | — |
| `session new retry` | `新建会话执行` |

方括号表示可选正文；`N` 支持 `1`–`9`，所有带槽位的指令均可写成 `N` 或 `SN`（例如 `3` 与 `S3` 等价），中文别名同时支持一至九。`help` 和“帮助”返回不附带状态的精简命令清单。`rename` 或“重命名”后的标题用于重命名当前绑定 Session，标题不能为空且最多 48 个字符。`session stop N` 及其中文别名会先返回简短的已安排提示，再在后台取消目标 Session 的运行任务并停止底层会话，最终结果按本次保存的微信路由独立发送；停止保留槽位、历史和当前绑定，不等同于归档。微信 Session 列表标题与 `chub` 状态中的任务名称分别由 `openclaw.weixin_chub_mode.session_name_max_width` 和 `task_name_max_width` 控制，默认显示宽度为 30 和 64，最大均为 96。半角字符按 1、汉字与全角字符按 2 计算，Emoji 和组合字符按完整字形处理，超出时预留并显示省略号。原始 Session 标题和任务摘要仍保留独立的 48 字符安全上限。重命名成功后按切换和归档的统一格式返回操作结果、Session 列表与 Codex 用量。新建/切换并提交任务、恢复待续提任务，以及其他非空文字或可信语音任务，统一按“状态、可信的 `[▶ ]S<槽位> · <标题>`、`Task · <摘要>`”返回即时结果；Session 固定在 Task 前，`▶` 只标记当前绑定。明确失败使用相同层级，并只展示可靠确认的任务目标 Session；目标未建立或无法确认时省略 Session，降级为“状态 + Task”。任务型固定指令不再重复附加完整 Session 和额度尾部。重复回执不重复执行，但会刷新 `▶`，原槽位已释放或复用时移除 Session 行。插件等待 Chub 超时时只提示提交状态未知，不在插件侧复制 Session 或摘要规则。普通任务默认提交到当前绑定 Session；翻译和主任务终态继续作为关联该任务的后续独立通知，仅主任务成功通知在最后一段底部追加统一额度行。详细的匹配、槽位、状态、幂等、通知和安全规则见 [Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。

表内固定指令的文案统一使用英文，只有 Session 标题和任务标题保留来源原文；中文别名不改变回复语言。除帮助、`Usage` 用法错误、提交或恢复任务的新建/切换/续提结果以及重启/停止的首次受理或进行中提示外，固定回复以 Session 状态和单行 `Weekly <quota> · Today <usage>` 结尾。未展示的 Session 数量使用英文 `<N> more Sessions`。Session 使用 `▶` 标记当前绑定、`!` 标记不可用或状态未知，不使用圆点标记运行状态；正在运行时必须在 Session 行下一行显示 `Task · <摘要>`，没有可信任务名时显示 `Task · Running`，没有 `Task` 行即表示当前没有运行任务。状态采集失败时显示 `Unavailable` 和 `Weekly Unavailable`；所有空列表都直接显示 `No sessions`，不保留 `Sessions` 标题。尾部状态不覆盖指令本身的真实结果。重启和停止的完成通知仍附带最终状态，且不再单列关联 Session 和关联任务；普通任务提交回执、失败、超时和翻译结果不附加状态尾部，主任务成功通知只在结果底部追加统一额度行。后续新增微信 Chub 固定路由必须满足英文固定文案、精简回复边界、状态尾部及配套测试，再登记到本清单。

## 5. 相关文档

| 文档 | 负责内容 |
| --- | --- |
| [README](../README.md) | 项目概览、安装、主要入口和文档导航 |
| [Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md) | 微信端到端业务、身份、权限、Session 路由和通知 |
| [Chub AI Session 状态模型设计](AI_SESSION_STATE_DESIGN.md) | Session、Activity、入口、槽位和单 writer 语义 |
| [Chub AI 额度与用量采集设计](AI_QUOTA_USAGE_DESIGN.md) | AI 用量来源、统一接口、缓存和展示口径 |
| [快速交互独立 Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md) | 非实时任务、恢复、通知终态和协调重启 |
| [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md) | 插件协议、源码、构建、部署和协议验收 |
