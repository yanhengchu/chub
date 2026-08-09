# OpenClaw 与消息通道接入设计

> 状态：第三阶段首版已闭环，持续维护。核心功能已在 macOS、Ubuntu 完成验收；低风险状态变更能力按真实需求单独扩展，不作为首版闭环条件。本文只保留当前有效的架构边界、操作流程和维护规则；早期调研见 `archive/phase-3/OPENCLAW_RESEARCH.md`，任务状态见 `TASKS_PHASE_3.md`。

## 1. 当前范围与架构

当前实现包含四项核心能力：

1. 安装和管理本机 OpenClaw Gateway。
2. 通过微信 ClawBot 与 OpenClaw 双向交互。
3. OpenClaw 通过受限 Tool 查询 Chub 状态。
4. Chub 和 OpenClaw 向预先配置的飞书群发送单向通知。

微信设备能力调用采用单向编排：微信 ClawBot 将请求交给 OpenClaw，OpenClaw 通过受限 Chub Tool 调用 Chub，Chub 返回结构化最终状态后由 OpenClaw 回复微信。Chub 不通过 Gateway、`openclaw agent` 或其他 OpenClaw 入口反向处理微信请求。

```text
微信用户
  └── 微信 ClawBot
        └── OpenClaw Gateway
              ├── 模型供应商 API
              ├── chub_get_status
              │     └── Chub API
              └── chub_send_notification
                    └── Chub Notification Service
                          └── 飞书群 Webhook

Chub 快速交互
  └── Codex CLI
```

Chub 直接发送飞书是独立能力：Chub 调用自身通知 Service，不构成 Chub 对 OpenClaw 的反向调用。

必须区分以下状态，不能用其中一个推断其他状态：

| 状态 | 含义 |
|---|---|
| Gateway 正常 | 后台服务、进程、端口和 RPC 正常 |
| Channel 正常 | 微信插件和本地通道进程正常 |
| ClawBot 已绑定 | 微信服务端当前仍绑定这台 Gateway |
| Owner 已配置 | 指定微信身份具有 Owner 权限 |
| Tool 成功 | Tool 已完成，并取得目标服务返回的最终结果 |

同一个微信 ClawBot 同时只能绑定一台 Gateway。在另一台设备重新扫码后，旧设备可能仍保留 Channel 和 Owner 等本地信息，但服务端绑定已经失效；最终应以微信真实收发结果为准。

当前不包含指定人员飞书提醒、自动事件通知和连续电脑交互。这些能力按实际需要独立扩展，不影响本版收尾。

## 2. 安装和连接

### 2.1 安装 OpenClaw 与 Gateway

macOS 和 Ubuntu 优先使用官方安装脚本：

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

已经自行维护受支持的 Node.js 时，也可以使用 npm：

```bash
node -v
npm -v
npm install -g openclaw@latest
```

初始化并安装 Gateway 后台服务：

```bash
openclaw --version
openclaw doctor
openclaw onboard
openclaw gateway install
openclaw gateway start
openclaw gateway status --json
openclaw gateway probe
```

也可以使用 `openclaw onboard --install-daemon` 合并初始化和后台服务安装。macOS 使用 launchd，Ubuntu 使用 systemd user service；同一份配置和端口不能同时运行多个 Gateway。

Gateway 生命周期命令：

```bash
openclaw gateway start --json
openclaw gateway stop --json
openclaw gateway restart --json
```

### 2.2 安装微信插件并绑定 ClawBot

```bash
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
openclaw channels login --channel openclaw-weixin
```

最后一条命令启动登录并显示二维码。Chub 首页“OpenClaw 环境”卡片也可以调用同一登录能力展示二维码，用户不需要进入终端。

扫码只建立 ClawBot 与 Gateway 的通道绑定；允许微信身份访问和授予 Owner 权限是后续两个独立步骤。

## 3. 状态检查

按以下顺序检查，不要只查看 Gateway：

```bash
openclaw --version
openclaw gateway status --json
openclaw gateway probe
openclaw channels status --json
openclaw channels status --probe --json
openclaw config get commands.ownerAllowFrom
openclaw exec-policy show
openclaw sandbox explain --json
openclaw config validate
```

启用 Tailscale Serve 时追加：

```bash
TAILSCALE_BE_CLI=1 tailscale serve status --json
```

| 检查项 | 重点 |
|---|---|
| `gateway status/probe` | 后台服务、进程、监听、RPC 和端口 |
| `channels status --probe` | 微信账号、本地通道和实时探测 |
| `commands.ownerAllowFrom` | Owner 是否只包含预期微信身份 |
| `exec-policy show` | Shell Host、白名单和审批策略 |
| `sandbox explain` | Sandbox、有效工具和 Elevated 状态 |
| `config validate` | 配置结构是否合法 |
| `tailscale serve status` | HTTPS 是否仍代理到当前 Gateway |

最后必须从当前微信发送一条消息并收到最终回复。本地显示 Channel 正常，不足以证明微信服务端仍绑定当前设备。

Chub 首页“OpenClaw 环境”卡片分别展示 Gateway、Channel、Owner 和 Tailscale 状态。该卡片位于“自动化环境”之后，不会因从任意二级页面返回首页而自动刷新，用户需要时手动刷新；启动、停止、重启等卡内操作只刷新本卡片，并以最终状态结束操作。

## 4. 权限配置

### 4.1 锁定微信身份和 Owner

当前采用单用户策略：只允许本人微信配对，并只将本人微信设为 Owner。

查看待配对请求：

```bash
openclaw pairing list openclaw-weixin
```

确认是本人当前微信后批准：

```bash
openclaw pairing approve openclaw-weixin <本人配对码> --notify
```

`commands.ownerAllowFrom` 是完整列表。修改前先读取现值，写入时保留所有仍然有效的条目：

```bash
openclaw config get commands.ownerAllowFrom
openclaw config set commands.ownerAllowFrom \
  '["openclaw-weixin:<本人微信发送者ID>"]' \
  --strict-json
openclaw gateway restart
```

不要批准未知配对请求。当前期望状态是：一个微信通道账号、一个允许发送者、一个 Owner。

### 4.2 Shell 执行和审批

当前电脑上的命令由 Gateway 执行。白名单命令可以直接执行，未命中时必须审批，审批不可用或超时则拒绝：

```bash
openclaw exec-policy preset cautious
openclaw config set tools.exec.strictInlineEval true --strict-json
openclaw config set tools.exec.commandHighlighting true --strict-json
openclaw exec-policy show
openclaw config validate
```

有效基线：

```text
host=gateway
security=allowlist
ask=on-miss
askFallback=deny
```

OpenClaw 工作区 `~/.openclaw/workspace/AGENTS.md` 应明确：操作当前电脑时使用 `host="gateway"` 或省略 Host；只有用户明确指定已配对 Node 时才使用 `node` 或 `auto`；发生 Host 冲突时改用 Gateway 重试一次。

微信验收命令：

```text
/new
在当前 OpenClaw Gateway 所在电脑上调用 shell 执行 /usr/bin/uptime，并展示结果
/approve <审批ID或短码> allow-once
```

验收标准：未批准前不执行；单次允许后在 Gateway 执行；结果最终返回微信；命令不进入持久白名单。不要将 `bash`、`sh`、`zsh`、`python`、`node`、`osascript` 等通用解释器整体加入白名单。

### 4.3 文件访问边界

当前是本人微信控制本人设备的可信单用户场景：

- OpenClaw 以当前系统用户身份运行，Sandbox 保持关闭，不依赖 Docker。
- 当前用户目录内的普通项目、文档、下载和用户自有文件可以按任务需要操作。
- 默认不访问其他用户目录和系统凭证存储。
- `~/.ssh`、`~/.gnupg`、云服务凭证、密码库、系统钥匙串、浏览器登录数据，以及 OpenClaw、Chub、Codex 的 Secret 文件属于敏感路径。
- `.env`、私钥、恢复码、Token 和 API Key 无论位于何处都属于敏感数据。

敏感路径规则写入 `~/.openclaw/workspace/AGENTS.md`：无关任务不得读取；修改、移动、删除、轮换或对外发送前，必须说明准确目标和用途并取得明确确认；秘密不得进入回复、日志、Memory、文档或 Git。修改规则后使用 `/new` 创建新 Session，使规则重新载入。

这是模型行为约束，不是操作系统级隔离。如果以后允许其他微信身份、其他用户或不可信输入访问，必须重新评估 Sandbox、独立 Agent 和文件工具边界。

### 4.4 当前权限结论

- 当前仅保留一个 `main` Agent，Session 可见范围沿用默认 `tree`，用于支持本人跨入口查询会话。
- Elevated 开关即使存在，没有配置允许来源时也不可用，当前无需额外开放。
- Skill 只使用已确认的内置或本地能力，不授予任意外部代码默认权限。
- 权限基线已经完成首轮收尾；日常可用 `openclaw security audit --json` 复查，发现真实异常后再调整。

## 5. Chub 接入

### 5.1 首页“OpenClaw 环境”卡片

首页通过 `GET /api/openclaw/status` 分别检查 Gateway、Channel、Owner 和 Tailscale。启动、停止、重启、刷新和微信登录均使用后端固定操作，不接受任意系统命令。

微信登录二维码和临时登录状态只保存在 Chub 进程内存中，接口受 Hub Token 或可信 Tailscale 来源保护，不向前端返回原始身份配置或其他 Secret。

页面必须遵守以下规则：

- 刷新失败时保留最近一次成功内容并单独提示。
- 重启过程中暂停本卡片刷新，防止“正在重启”被旧状态覆盖。
- Gateway 状态标签是唯一主要状态描述，不重复展示语义相同的文字。
- “连接微信”是否可用由当前真实操作状态决定，不能因页面恢复缓存而永久置灰。

快速交互完成通知采用独立出站链路：`Chub 快速交互 → openclaw message send → 微信 ClawBot → 固定微信收件人`。它不是微信请求调用设备能力的反向链路，不运行 `openclaw agent`，也不允许通过通知触发新的 Chub 操作。Chub 只在任务成功、失败或超时后异步发送有界结果摘要；通知状态单独记录为发送中、已发送、失败或跳过，任何通知故障都不改变任务最终状态。

收件人必须在本机 Chub 配置的 `openclaw.quick_interaction_completion.weixin_recipient` 中固定，并先主动向 ClawBot 发送过消息，以便微信插件持久化当前会话的 context token。可选的 `weixin_account_id` 用于固定账号；未配置时仅允许恰好一个正常运行的微信账号。OpenClaw 微信插件的通用出站实现需要在调用方未显式传入 context token 时，按账号和固定收件人恢复已持久化 token；插件升级或重装后需确认该兼容修改仍然存在。

当前微信插件需要额外兼容处理，原因是通道启动和通用出站发送可能运行在相互隔离的插件模块实例中，不能只依赖进程内 Map。补丁同时覆盖 token 落盘、出站实例惰性恢复、固定收件人回退和持久化文件 `600` 权限。面向后续 AI Agent 的恢复顺序、行为不变量、关键代码、参考 patch、升级复检和分层验收见 [微信 ClawBot Context Token 持久化 AI 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。首页检测不得自动修改第三方插件，也不能只按插件版本号判断兼容性；上游版本可能回移或原生实现同等能力，应优先识别明确能力或必要行为特征。

### 5.2 Chub OpenClaw 插件

插件位于 `integrations/openclaw/chub/`，插件 ID 为 `chub`。一个插件统一承载 Chub 相关 Tool 和必要 Hook，不为每个函数创建独立插件。

当前 Tool：

| Tool | 能力 |
|---|---|
| `chub_get_status()` | 查询当前设备的 Chub 健康、版本和基础状态 |
| `chub_send_notification(...)` | 向预先配置的通知目标发送消息 |

Tool 只允许固定 Chub 地址、固定 API 路径和严格 Schema 参数，不接受任意 URL、文件路径、Shell 命令或 Token；同时限制超时和响应大小。

构建和校验：

```bash
cd integrations/openclaw/chub
npm ci
npm run plugin:build
npm run plugin:validate
npm test
cd ../../..
```

首次安装或覆盖更新：

```bash
openclaw plugins install ./integrations/openclaw/chub
openclaw plugins install --force ./integrations/openclaw/chub
```

配置插件：

```bash
openclaw config get plugins.allow
openclaw config set plugins.allow \
  '<包含原值和chub的完整JSON数组>' \
  --strict-json --replace
openclaw config set plugins.entries.chub.enabled true --strict-json
openclaw config set plugins.entries.chub.config.baseUrl \
  'http://<当前设备的Tailscale-IP>:<Chub端口>'
openclaw config set plugins.entries.chub.config.timeoutMs 3000 --strict-json
openclaw config set \
  plugins.entries.chub.hooks.allowConversationAccess true \
  --strict-json

openclaw config get tools.alsoAllow
openclaw config set tools.alsoAllow \
  '<包含原值、chub_get_status和chub_send_notification的完整JSON数组>' \
  --strict-json

openclaw config validate
openclaw gateway restart --json
openclaw plugins inspect chub --runtime --json
```

`plugins.allow` 和 `tools.alsoAllow` 都是完整列表，不能覆盖掉原有有效项。检查结果应显示插件已加载且启用，两个 Tool 可用，`message_received`、`llm_input` 和 `before_tool_call` Hook 已注册，且没有诊断错误。

状态 Tool 验收：

```text
调用 chub_get_status 检查当前设备的 Chub 状态，并展示结果
```

该流程已在 macOS、Ubuntu 的 OpenClaw TUI 和微信 ClawBot 验收通过。

## 6. 飞书通知

### 6.1 配置和调用入口

通知注册表保存在：

```text
~/.config/chub/notifications/registry.yaml
```

每个目标保存群名称、用途、Webhook Secret 引用、允许的提醒方式和可选人员别名；真实 Webhook 单独保存在 `<target>.webhook`。配置目录权限为 `700`，配置和 Secret 文件权限为 `600`，均不得提交到 Git。

Chub 提供三个入口：

```bash
chub notification validate
chub notification list
chub notification test --target <target>
chub notification send --target <target> --message <text>
```

发送命令可追加 `--mention-recipient <alias>` 或 `--mention-all`。受保护 API 为：

```text
GET  /api/notifications/targets
POST /api/notifications/send
```

API 只接受 Hub Token 或真实可信 Tailscale socket 来源，不信任客户端转发 Header。

调用路径固定：

- Chub Codex PTY 和快速交互 Codex 模式：直接使用 `chub notification send` 或 Chub 通知 API。
- OpenClaw TUI 和微信 ClawBot：使用 `chub_send_notification`。
- 不从 Chub 内部通过 `openclaw agent` 间接发送。

### 6.2 消息和提醒规则

`chub_send_notification` 参数为：

```text
target
message
mention_mode: none | recipients | all
recipients: 已配置的人员别名列表
content_mode: verbatim | generated
```

规则：

- 默认不提醒任何人。
- `recipients` 只能使用目标中预先配置并能解析到 OpenID 的别名。
- `all` 必须由用户明确要求，且目标配置允许 `@所有人`。
- 指定人员解析失败时直接报错，不能退化为 `@所有人`。
- 用户给出明确“消息内容”时使用 `verbatim`，必须原样发送。
- 只有用户明确要求 AI 撰写、总结或改写时才使用 `generated`。

OpenClaw 插件通过 Hook 暂存本轮用户原文，并在 Tool 执行前用原文覆盖模型可能改写的 `message` 参数。缓存按 `runId` 隔离，最多保留 5 分钟和 100 条；原文缺失、为空或超过 4000 字时拒绝发送，避免把失真的内容发到群中。

普通消息示例：

```text
调用 chub_send_notification 向 test 飞书群发送普通消息，不要提醒任何人。
消息内容：Chub 飞书通知功能集成验收通过。
```

提醒所有人示例：

```text
调用 chub_send_notification 向 test 飞书群发送普通消息，提醒所有人。
消息内容：Chub 飞书通知功能集成验收通过。
```

### 6.3 结果和日志语义

- `accepted` 只表示飞书 Webhook 已接受请求，不表示群成员已读或通知最终送达。
- 发送使用短期请求 ID 去重；飞书已经接受后不自动重试，避免重复消息。
- 操作日志记录 `requested`、`started`、`succeeded` 或 `failed`，但不记录消息正文、Webhook、Token 或 Authorization。
- `httpx`、`httpcore` 日志级别保持在 `WARNING`，防止完整 Webhook URL 出现在新日志中。
- 历史日志不会被自动改写；如需清理，应作为单独的日志维护操作处理。

飞书普通消息、显式 `@所有人`、原文保护和三种调用入口已经完成真实验收。指定人员提醒待提供并配置群成员 OpenID 后再启用。

## 7. 维护和验收原则

### 7.1 最终结果原则

以下情况都不能单独宣告成功：

- HTTP 返回 200；
- 子进程成功创建；
- Tool Call 已发起；
- 微信显示“正在使用工具”；
- 本地 Channel 状态为正常。

必须等待目标系统的最终结果：Gateway 操作检查最终实例和健康状态；Chub Tool 以 Chub 返回为准；飞书发送以 Webhook 接受结果为准；微信绑定以真实消息收发为准。

“正在使用工具”只能是短暂进度提示。Tool 完成后必须返回实际结果或明确错误，不能把进度文案作为最终回复。

### 7.2 常见异常定位

| 现象 | 优先检查 |
|---|---|
| Gateway 正常但微信无回复 | `channels status --probe`，再做微信真实收发 |
| 旧设备仍显示微信信息 | ClawBot 是否已在另一设备重新扫码 |
| 微信 Shell 提示 Host 不允许 | 是否错误指定为 `node`；当前电脑应使用 `gateway` |
| Shell 一直未执行 | 是否等待 `/approve ... allow-once`，审批是否超时 |
| Tool 只显示“正在使用工具” | Tool 是否返回最终结果，插件是否加载，当前 Session 是否加载最新规则 |
| 飞书文字被改写 | 是否使用 `verbatim`，原文 Hook 是否成功关联当前 `runId` |
| “连接微信”按钮异常置灰 | 手动刷新卡片，检查是否残留登录操作状态 |

### 7.3 变更后的最小验收

根据变更范围选择，不要求每次重复全套流程：

1. `openclaw config validate`。
2. `openclaw gateway status --json` 和 `openclaw gateway probe`。
3. `openclaw channels status --probe --json`。
4. 修改插件后执行构建、校验、测试和 `plugins inspect`。
5. 修改权限后用新 Session 验证 Shell 审批。
6. 修改消息通道或 Tool 后，从 TUI 和微信各做一次真实调用。
7. 修改通知后，分别验证普通消息及本次涉及的提醒模式，并检查日志未泄露 Secret。

当前 macOS、Ubuntu 的安装、Gateway、微信绑定、Owner、Shell 审批、Chub 状态 Tool 和飞书通知核心链路均已完成验收。后续发现真实异常时，将可复现问题补充为回归用例；不提前扩展未使用的权限、通知或连续电脑交互能力。

第三阶段首版已经完成核心调用闭环，不要求为收尾新增状态变更 Tool。后续如有明确价值，再选定参数固定、风险可控且失败可恢复的低风险白名单能力，并补齐微信请求、OpenClaw 会话、Chub 操作 ID、目标节点、最终回复和异常路径设计。飞书指定人员提醒等待 Open ID；连续电脑交互、自动事件通知和更多 Tool 均按真实需求另行设计，不属于当前默认待办。
