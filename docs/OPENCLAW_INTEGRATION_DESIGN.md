# OpenClaw 与消息通道接入设计

> 状态：部分实现，持续验收。OpenClaw、微信 ClawBot 基础流程、OpenClaw 外部模型、Chub 基础 LLM、当前可信单用户权限基线和首个 Chub 状态 Tool 已完成 MacBook TUI 与微信验收。状态 Tool 的 Ubuntu 验收、飞书通知和完整异常链路仍待实施。

日常安装和排障优先查看：

- [安装 OpenClaw 和 Gateway](#41-安装-openclaw-和-gateway)
- [状态检查流程](#42-状态检查流程)
- [权限配置流程](#43-权限配置流程)

## 1. 文档定位

本文记录第三阶段当前有效的实现基线、已验收操作流程，以及后续接入 Chub Tool 和消息通知时必须遵守的边界。归档背景调研见 `archive/phase-3/OPENCLAW_RESEARCH.md`，具体任务状态见 `TASKS_PHASE_3.md`。

第三阶段包含四条相关但相互独立的能力：

1. Chub 管理本机 OpenClaw Gateway 状态和固定生命周期操作。
2. 微信 ClawBot 作为 OpenClaw 的双向私聊入口。
3. OpenClaw 与 Chub 共享同一份模型配置，但分别直接调用模型供应商。
4. OpenClaw 后续通过受限 Tool 调用 Chub；飞书群机器人后续只发送单向通知。

本文不把以下状态混为一体：

- Gateway 运行正常；
- 消息通道进程运行；
- 微信服务端仍绑定当前设备；
- Owner 权限已配置；
- 模型调用成功；
- Tool 已执行并取得最终结果。

## 2. 当前实现基线

| 能力 | 当前状态 |
|---|---|
| MacBook、Ubuntu OpenClaw 安装、初始化和 Gateway 基线 | 已实现并完成首轮验收 |
| Chub 首页 OpenClaw 卡片 | 已实现并完成 MacBook、Ubuntu 实机验收 |
| Tailscale Serve HTTPS 控制台入口 | MacBook 已验收 |
| OpenClaw 外部模型 | MacBook、Ubuntu 已完成普通对话验收 |
| Chub 基础 LLM 与快速交互 Bedrock 入口 | 已实现并完成 MacBook、Ubuntu 真实调用验收 |
| 微信插件安装、扫码、配对、Owner 和普通消息 | 已实现并完成双端首轮验收 |
| Chub 首页生成微信绑定二维码 | 已完成 MacBook 真实生成、扫码绑定、状态恢复和取消验收；Ubuntu 待验收 |
| 同一 ClawBot 切换 Gateway | 已确认新绑定使旧设备服务端绑定失效，旧设备可能保留本地信息 |
| OpenClaw Tool Calling | 单次真实调用已跑通，偶发占位回复仍待稳定 |
| OpenClaw 权限基线 | 本人微信身份、Owner、Gateway Shell 审批、敏感路径、Session、Skill 和 Elevated 已完成盘点与收尾 |
| Gateway Shell 审批 | `cautious` 策略、本地 TUI 审批和微信 `/approve allow-once` 已验收 |
| 用户文件与敏感路径规则 | 用户目录正常可操作；敏感路径行为约束已写入 OpenClaw 工作区 `AGENTS.md` |
| OpenClaw 调用 Chub 受限 Tool | `chub_get_status` 已实现并完成 MacBook TUI、微信真实调用验收；Ubuntu 待验收 |
| Chub Tool 幂等、超时、审批和高风险确认 | 当前只读状态 Tool 已完成超时、响应上限和错误收敛；其他能力暂不实施 |
| 飞书群机器人单向通知 | 待实现 |
| 连续电脑交互 | 后续扩展 |

当前总体关系：

```text
微信用户 ◀──双向──▶ 微信 ClawBot ◀──▶ OpenClaw Gateway ──▶ 模型供应商 API
                                      │
                                      ├── Agent / Session / Tool Policy
                                      ├── chub_get_status（已实现，持续验收）
                                      │       ├──▶ MacBook Chub
                                      │       └──▶ Ubuntu Chub
                                      └── 连续电脑交互（后续）

快速交互页面 ──▶ Chub 基础 LLM Service ──直接调用──▶ 同一模型供应商 API
                         │
                         └──只读解析 OpenClaw Provider / 模型 / SecretRef

Chub 最终事件 ──▶ 飞书通知适配器（待实现）──▶ 固定飞书群 Webhook
```

MacBook 与 Ubuntu 可以分别安装 OpenClaw。由于同一个微信 ClawBot 同时只能绑定一个 Gateway，正式使用时只保留一个有效微信入口；另一台设备上的本地通道账号和 Owner 信息不能视为有效绑定证明。

## 3. 组件职责与边界

| 组件 | 负责 | 不负责 |
|---|---|---|
| 微信 ClawBot | 接收私聊消息，返回处理中状态和最终回复 | 判断设备操作是否最终成功 |
| OpenClaw Gateway | 频道、Agent、Session、模型、Tool 路由和策略 | 伪造 Chub 最终状态 |
| 模型供应商 | 提供模型推理 API | 保存设备凭证或绕过 Tool Policy |
| Chub 基础 LLM Service | 只读解析 OpenClaw 模型配置并完成固定文本调用 | 复制 Key、代理 OpenClaw 会话或执行 Tool Calling |
| Chub 受限 Tool | 将固定 Schema 参数映射到固定 Chub 能力 | 接受任意 URL、路径、Shell 命令或 Token |
| Chub | 节点能力、安全校验、操作日志和最终状态 | 管理微信会话或 OpenClaw 模型上下文 |
| 飞书 Webhook | 向固定群发送明确配置的通知 | 接收指令、维护会话或控制设备 |
| 电脑交互适配 | 对选定设备执行已授权的连续交互 | 默认获得所有设备和系统权限 |

核心原则：

- OpenClaw 不可用不能影响 Chub Web、Codex PTY、快速交互和自动化入口。
- Chub LLM 调用不经过 OpenClaw Gateway；OpenClaw Gateway 停止时，只要配置和 Secret 文件仍有效，Chub 仍可直接调用供应商。
- Chub LLM Service 不反向修改 OpenClaw 配置，也不保存第二份 API Key。
- 模型回复、Tool 创建成功和 HTTP 200 都不能单独代表设备操作成功。
- Chub 返回的最终状态是 Chub 操作的事实来源。

## 4. 核心操作手册

本节集中保留安装、检查和权限调整需要执行的命令。实现边界和异常语义见后续章节。

### 4.1 安装 OpenClaw 和 Gateway

macOS 和 Ubuntu 优先使用官方安装脚本：

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

已经自行维护受支持 Node.js 时，也可以使用 npm：

```bash
node -v
npm -v
npm install -g openclaw@latest
```

完成 CLI 检查、首次初始化并安装 Gateway 后台服务：

```bash
openclaw --version
openclaw doctor
openclaw onboard
openclaw gateway install
openclaw gateway start
openclaw gateway status --json
openclaw gateway probe
```

也可以用 `openclaw onboard --install-daemon` 合并初始化和后台服务安装。macOS 使用 launchd，Ubuntu 使用 systemd user service；同一配置和端口不能同时运行多个 Gateway。

安装微信插件并扫码绑定当前微信 ClawBot：

```bash
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
openclaw channels login --channel openclaw-weixin
```

Gateway 生命周期命令固定为：

```bash
openclaw gateway start --json
openclaw gateway stop --json
openclaw gateway restart --json
```

### 4.2 状态检查流程

按以下顺序检查，不要只看 Gateway 进程：

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

- `gateway status/probe`：后台服务、进程、RPC、监听和端口；
- `channels status --probe`：微信账号、通道运行状态和实时探测；
- `commands.ownerAllowFrom`：Owner 是否仅包含预期微信；
- `exec-policy show`：Shell Host、白名单和审批策略；
- `sandbox explain`：沙箱、有效工具和 Elevated 状态；
- `config validate`：配置结构是否有效；
- `tailscale serve status`：HTTPS 是否仍代理到当前 Gateway。

最终必须从当前微信发送消息并收到最终回复。同一 ClawBot 在其他设备重新扫码后，旧设备仍可能保留本地 Channel 和 Owner 信息，因此本地状态不能证明微信服务端仍绑定当前设备。

### 4.3 权限配置流程

#### 微信身份与 Owner

当前插件使用 `pairing` 私聊策略。先检查待配对请求：

```bash
openclaw pairing list openclaw-weixin
```

只有确认是本人当前微信时才批准：

```bash
openclaw pairing approve openclaw-weixin <本人配对码> --notify
```

Owner 是完整列表，写入前必须先读取并保留所有有效条目：

```bash
openclaw config get commands.ownerAllowFrom
openclaw config set commands.ownerAllowFrom \
  '["openclaw-weixin:<本人微信发送者ID>"]' \
  --strict-json
openclaw gateway restart
```

当前个人使用基线：一个微信通道账号、一个允许发送者、一个 Owner、没有待审批的其他微信。不要批准未知配对请求。

#### Shell 执行与审批

当前电脑由 Gateway 执行命令。白名单命令直接执行；未命中时请求审批；审批不可用或超时则拒绝：

```bash
openclaw exec-policy preset cautious
openclaw config set tools.exec.strictInlineEval true --strict-json
openclaw config set tools.exec.commandHighlighting true --strict-json
openclaw exec-policy show
openclaw config validate
```

有效策略：

```text
host=gateway
security=allowlist
ask=on-miss
askFallback=deny
```

OpenClaw 工作区 `AGENTS.md` 同时约束：操作当前电脑时使用 `host="gateway"` 或省略 Host；只有用户明确指定已配对 Node 时才能使用 `node` 或 `auto`；Host 冲突时改用 Gateway 重试一次。

微信验收：

```text
/new
在当前 OpenClaw Gateway 所在电脑上调用 shell 执行 /usr/bin/uptime，并展示结果
/approve <审批ID或短码> allow-once
```

通过标准：真实产生 `exec(host="gateway")`；未批准前不执行；单次允许后成功；最终结果返回微信；命令不进入持久白名单。不要把 `bash`、`sh`、`zsh`、`python`、`node`、`osascript` 等通用解释器整体加入白名单。

#### 用户文件与敏感路径

当前为本人微信控制本人设备的单用户场景，文件权限采用以下基线：

- OpenClaw 继续以当前系统用户身份直接运行，Sandbox 保持关闭；
- 不安装或依赖 Docker，不把文件工具限制到 OpenClaw Workspace；
- 当前用户目录中的普通项目、文档、下载和用户自有文件可按任务需要读写；
- 默认不访问其他用户目录和系统凭证存储；
- `~/.ssh`、`~/.gnupg`、云服务凭证、密码库、系统钥匙串、浏览器登录数据，以及 OpenClaw、Chub、Codex 的 Secret 文件属于敏感路径；
- `.env`、私钥、恢复码、Token 和 API Key 无论位于何处都按敏感数据处理。

敏感路径规则维护在 `~/.openclaw/workspace/AGENTS.md`：无关任务不得读取敏感内容；修改、移动、删除、轮换或对外发送前必须说明准确目标和用途并获得明确确认；秘密不得进入回复、日志、Memory、生成文档或 Git。修改规则后通过 `/new` 创建新 Session，使启动上下文重新加载。

该方案是适合当前可信单用户环境的模型行为约束，不是操作系统级目录隔离。若以后允许其他微信身份、其他用户或不可信输入使用 OpenClaw，必须重新启用强制 Sandbox、独立 Agent 或更严格的文件工具边界。

#### Session 权限

当前只有一个 `main` Agent，跨 Agent 调用默认关闭，Session 工具可见范围使用默认 `tree`。`sessions_list`、`sessions_history`、`sessions_send`、`sessions_spawn`、`sessions_yield`、`subagents` 和 `session_status` 来自当前 Coding 工具基线。

当前入口只有本人微信，`sessions_history` 返回有界、脱敏内容，Sub-agent 也受默认深度和并发限制，因此没有必须立即修复的 Session 权限异常。保留现状可以继续支持微信 `sessions_list` 和后续后台任务；不把可见范围改为 `agent`，避免为了跨入口查询反而扩大读取范围。以后实际启用跨会话发送或 Sub-agent 工作流时，再分别验收目标 Session、资源消耗和结果投递。

#### Skill 与 Elevated 权限

当前可见 Skill 均来自 OpenClaw 内置包，没有用户安装或来源未知的 Skill。Skill 只提供任务说明和调用方式，不自动绕过现有 Tool Policy 与 Gateway Shell 审批；当前可信单用户场景没有必须禁用的 Skill。以后安装第三方 Skill 时，必须先核对来源、内容、所需命令和凭证，再决定是否启用。

Elevated 框架开关当前显示为启用，但没有配置任何 `allowFrom`，有效状态为 `allowedByConfig=false`，微信和本地 Session 都不能使用 Elevated。Sandbox 当前关闭时 Elevated 也不会提供额外宿主机能力，因此没有现存越权，也不需要为了形式上的开关立即修改配置。以后启用 Sandbox 或配置 Elevated 来源前必须重新评审。

`openclaw security audit --json` 当前结果为 0 个 Critical；现有 Warning 不属于本轮权限异常。至此微信身份、Gateway Shell、用户文件、Session、Skill 和 Elevated 权限均已盘点，本轮权限调整完成并收尾。

本结论只适用于当前“本人微信、单一 `main` Agent、本人设备和可信 Tailnet”的边界。新增其他用户或不可信消息入口、启用 Sandbox 或 Elevated、安装第三方 Skill，或者开放新的 Chub Tool 时，必须针对新增能力重新评审，不重新打开已经收尾的基础权限任务。

### 4.4 Chub 首页状态与微信绑定

首页首次连接、普通刷新或手动刷新 OpenClaw 卡片时请求：

```http
GET /api/openclaw/status
```

后端按条件调用 Gateway、Channel、Owner 和 Tailscale 状态命令。页面不会收到原始配置、Owner 身份、模型凭证或命令输出；刷新失败时保留最近一次成功结果。从次级页面历史返回时只恢复缓存，不自动检查 OpenClaw。

Chub 卡片使用固定微信登录接口：

```http
POST   /api/openclaw/weixin/login
GET    /api/openclaw/weixin/login
GET    /api/openclaw/weixin/login/qr
POST   /api/openclaw/weixin/login/verify
DELETE /api/openclaw/weixin/login
```

二维码只保存在 Chub 内存中。页面不会收到二维码原始内容、备用链接、完整终端输出、微信身份或登录凭证。绑定、发送者配对和 Owner 是三个独立状态：

```text
channels login    → ClawBot 绑定当前 Gateway
pairing approve   → 允许指定微信发送者访问
ownerAllowFrom    → 授予指定身份 Owner 权限
```

同一 ClawBot 在另一台设备重新扫码后，原设备服务端绑定失效，但仍可能显示本地通道和 Owner 信息。最终验收必须从微信完成真实消息和最终回复。

### 4.5 OpenClaw 与 Chub 共享基础 LLM

模型配置和 API Key 以当前用户的 OpenClaw 配置与 SecretRef 为唯一来源：

- OpenClaw 使用该配置完成对话和 Tool Calling；
- Chub 通过独立 LLM Service 只读解析配置，并直接请求供应商 API；
- 两端不互相转发请求，也不共享运行时会话；
- Chub 不复制或再次持久化 API Key。

Chub 当前只支持：

- 文件型 `singleValue` SecretRef；
- `openai-completions` 兼容的 `/chat/completions` 文本调用；
- 固定 Provider、模型和 Base URL；
- 远程 HTTPS 或本机 loopback HTTP；
- 有界并发、超时、输出 Token 和响应字节数。

Chub 配置只声明配置来源和选择条件：

```yaml
llm:
  enabled: true
  config_source: "openclaw"
  openclaw_config_file: "~/.openclaw/openclaw.json"
  provider: null
  model: null
```

配置和 Secret 在首次调用时懒加载，并按文件修改状态自动刷新。配置缺失、Secret 权限不安全、认证失败、限流、网络错误、超时、超大响应和无效响应只影响本次 LLM 调用，不阻止 Chub 启动。

快速交互页面提供：

- `Codex CLI` / `Amazon Bedrock API` 单按钮切换；
- 选择只在当前页面有效，刷新、离开重进或浏览器返回时默认 Codex；
- 每次展示和加载 5 条历史；
- 历史保存执行方式及 Provider/模型快照；
- Bedrock 不读取工作区、不停止实时终端、不修改 Codex Session activity；
- Bedrock 运行时允许使用实时终端和调整 Codex 权限；
- 任一快速交互运行时禁止归档或删除对应 Session。

当前不提供独立首页 LLM 卡片、通用公共 LLM API、通用 Chub Tool Calling 或 Codex CLI 替代逻辑。OpenClaw 仅通过独立的 `chub_get_status` Tool 查询当前设备 Chub 状态。

## 5. OpenClaw 调用 Chub 状态 Tool

### 5.1 实现范围与调用结构

当前只开放一个无参数只读能力：

- `chub_get_status()`

调用结构：

```text
微信 ClawBot / OpenClaw TUI
  ↓
OpenClaw main Agent
  ↓ chub_get_status({})
Chub OpenClaw Plugin
  ↓ GET 固定 Tailnet 地址的 /api/status
当前 Gateway 所在设备的 Chub
  ↓
字段校验、筛选和错误收敛
  ↓
OpenClaw 生成最终回复
```

插件源码由 Chub 仓库的 `integrations/openclaw/chub/` 维护，插件 ID 为 `chub`。一个 Chub 插件统一维护连接、认证、错误和多个独立 Tool；当前只通过 OpenClaw `defineToolPlugin` 注册 Optional Tool `chub_get_status`，并由 `tools.alsoAllow` 显式开放。Tool Schema 是严格空对象，调用方不能提交 `node_id`、URL、IP、API 路径、文件路径、Shell 命令、Header 或 Hub Token。

后续若增加明确能力，应在同一插件的 `src/tools/` 下增加独立 Tool，共用 `client.ts` 和 `errors.ts`；不能为每个函数创建插件，也不能增加接受任意 URL、路径或方法的通用 `chub_call`。

插件只保留节点名称、平台、Chub 版本、CPU、内存、磁盘、系统运行时间和检查时间。原始 HTTP 响应、内部地址、异常堆栈和认证信息不进入模型结果。

### 5.2 网络与认证

插件只访问维护者配置的当前设备 Tailnet 地址，并在运行时再次校验目标属于 Tailscale IPv4 `100.64.0.0/10` 或 Tailscale IPv6 地址段。请求路径固定为 `/api/status`：

- Chub 直接监听本机 Tailscale IP；
- 使用 Chub 已有 `security.allow_tailscale` 认证；
- 只判断真实 socket 来源，不信任转发 Header；
- 插件不读取、复制或回退使用 Hub Token；
- 禁止 HTTP 跳转，请求超时默认 3 秒、上限 10 秒，响应上限 64 KiB；
- 网络、认证、超时、超大响应和无效响应映射为固定错误码，不透传原始正文。

该信任边界不替代 Tool 白名单、高风险确认或 Gateway Shell 审批。当前可信单用户场景接受 Sandbox 关闭；以后如果 Tailnet、微信通道或 OpenClaw 加入其他用户或不可信输入，必须重新评估 Sandbox 和文件访问边界。

### 5.3 安装、配置与验收

#### 开发与构建

插件源码固定维护在 `integrations/openclaw/chub/`：

- `src/index.ts`：声明 Chub 插件和 Tool 清单；
- `src/client.ts`：固定 Tailnet 连接、请求、响应上限和字段收敛；
- `src/errors.ts`：统一安全错误码和对外信息；
- `src/tools/get-status.ts`：实现 `chub_get_status`；
- `src/tools/get-status.test.ts`：目标地址、认证、响应格式和大小边界测试；
- `openclaw.plugin.json`：由 OpenClaw 构建器生成的插件与 Tool 契约；
- `package.json` / `package-lock.json`：构建命令和锁定依赖。

首次或干净环境使用锁文件安装，再构建、校验和测试：

```bash
cd integrations/openclaw/chub
npm ci
npm run plugin:build
npm run plugin:validate
npm test
cd ../../..
```

`plugin:build` 编译 TypeScript 并同步 `openclaw.plugin.json`；`plugin:validate` 检查入口、清单和 `chub_get_status` 契约一致。修改源码后必须重新执行以上命令。

#### 安装与注册

首次安装：

```bash
openclaw plugins install ./integrations/openclaw/chub
```

更新已安装插件：

```bash
openclaw plugins install --force ./integrations/openclaw/chub
```

当前采用复制安装，运行副本位于 `~/.openclaw/extensions/chub/`，不是仓库源码链接；源码更新后必须重新构建并 `--force` 安装。

注册包含三层配置：

1. 读取 `plugins.allow`，保留原有值并加入 `chub`；
2. 启用 `plugins.entries.chub`，在其 `config.baseUrl` 中设置当前设备固定 Tailnet 地址；
3. 读取 `tools.alsoAllow`，保留原有值并加入 `chub_get_status`。

```bash
openclaw config get plugins.allow
openclaw config set plugins.allow '<包含原值和chub的JSON数组>' \
  --strict-json --replace
openclaw config set plugins.entries.chub.enabled true --strict-json
openclaw config set plugins.entries.chub.config.baseUrl \
  'http://<当前设备的Tailscale-IP>:<Chub端口>'
openclaw config set plugins.entries.chub.config.timeoutMs 3000 --strict-json
openclaw config get tools.alsoAllow
openclaw config set tools.alsoAllow \
  '<包含原值和chub_get_status的JSON数组>' --strict-json
```

`plugins.allow` 和 `tools.alsoAllow` 都是完整列表，写入时必须合并而不能覆盖已有项目。本机地址只保存在 OpenClaw 本机配置中，不写入仓库，也不配置 Hub Token。

完成配置后加载和检查：

```bash
openclaw config validate
openclaw gateway restart --json
openclaw plugins inspect chub --runtime --json
```

通过标准：插件为 `loaded`、`enabled=true`，只注册 `chub_get_status`，且没有诊断错误。TUI 中使用 `/tools verbose` 可查看当前 Session 的有效 Tool。

#### 实际使用与验收

TUI 或微信发送：

```text
调用 chub_get_status 检查当前设备的 Chub 状态，并展示结果
```

模型调用 `chub_get_status({})`，插件请求固定 `/api/status`、过滤响应，再由模型生成最终回复。该链路不调用 Shell，不触发 Shell 审批，也不读取 Hub Token。

MacBook 已完成插件构建、清单校验、10 项单元测试，以及 TUI、微信的真实 Agent 调用验收。调用能够产生 `chub_get_status` Tool Call 并返回最终中文状态。下一步只需完成 Ubuntu 的构建、安装和真实调用验收。

普通微信聊天不依赖 Chub Tool，不能因为 Tool 尚未实现而标记为未接入。

## 6. 飞书群机器人通知（待实现）

飞书只使用固定群的自定义机器人 Webhook，负责：

- 节点或服务状态通知；
- 自动化任务最终结果；
- ClawBot 长任务最终摘要；
- 需要人工处理的告警。

飞书 Webhook 不接收指令、不维护 Agent Session，也不控制设备。

```text
Chub / OpenClaw 最终事件
  ↓
通知适配器校验事件类型和脱敏规则
  ↓
生成有界文本或 Markdown
  ↓
POST 固定 Webhook
  ↓
记录通知成功、失败或限流
```

要求：

- Webhook URL 和签名密钥按凭证管理；
- 目标地址固定，不接受调用方传入任意 URL；
- 通知内容不包含 Token、内部路径、原始日志或不必要的用户内容；
- 失败使用有界重试和事件 ID 去重；
- 通知失败不能改变原操作结果。

## 7. 状态、权限与失败语义

### 7.1 状态语义

| 状态 | 对外含义 |
|---|---|
| `received` | 微信或 OpenClaw 已收到消息，尚未请求设备操作 |
| `requested` | 已向 Tool 或 Chub 请求操作 |
| `awaiting_confirmation` | 高风险操作等待指定账号确认，尚未执行 |
| `running` | 目标系统确认正在执行 |
| `waiting_input` | 需要用户继续输入 |
| `succeeded` | 目标系统确认最终成功 |
| `failed` | 目标系统确认失败或无法继续 |
| `expired` | 交互会话已过期，不能继续复用 |

禁止把“微信已送达”“正在使用工具”“LLM 已回复”“Tool 已创建”或 HTTP 200 单独解释为设备操作成功。

### 7.2 权限分级

| 等级 | 示例 | 默认处理 |
|---|---|---|
| L0 只读 | 状态、任务结果 | 已配对并获得对应 Tool 权限后执行 |
| L1 可恢复 | 启动白名单任务 | 按任务策略执行，必要时确认 |
| L2 持续交互 | 电脑界面、终端多轮交互 | 建立有时限的交互会话 |
| L3 高风险 | 删除、敏感文件修改、权限修改、重启 | 默认不开放；开放时逐次明确确认 |

ClawBot 登录、发送者配对和 Owner 都不自动授予 Chub 操作权限。具体能力仍由 Tool Policy、节点映射和任务白名单决定。

### 7.3 失败与恢复

| 故障 | 对外反馈 | 恢复原则 |
|---|---|---|
| 微信插件离线或已切换绑定 | 微信入口不可用 | Chub 和 OpenClaw 本地入口继续运行 |
| Gateway 不可用 | ClawBot 无法处理 | 恢复后重新探测，不重复旧操作 |
| LLM 认证、限流或超时 | 明确模型不可用 | 有已验收回退模型时切换，否则停止 |
| Chub 不可达 | 明确目标节点离线 | 不使用任意 Shell 绕过 |
| Tool 超时 | 显示状态未知或失败 | 查询目标最终状态后决定是否重试 |
| 微信重复消息 | 返回已有交互状态 | 使用稳定消息 ID 保证幂等 |
| 交互过期 | 提示重新发起 | 不恢复旧权限或旧确认 |
| Webhook 失败 | 单独记录通知失败 | 不改变原任务结果 |

长操作不能只返回固定的“正在使用工具”。后续实现至少需要 `received`、`running`、需要输入或确认、最终结果四类反馈；最终回复无法投递时记录投递失败，等待用户重新查询。

## 8. 后续实施与验收

### 8.1 下一步顺序

1. 在 Ubuntu 构建、安装并真实调用同一 Tool。
2. 验收认证失败、不可达和超时反馈。
3. 稳定 Tool Calling 进度、最终结果和占位回复失败语义。
4. 飞书通知和连续电脑交互按独立需求实施；其他 Chub Tool 暂不增加。

微信插件安装记录使用 `@latest`，但当前实际加载版本已经验收。固定版本属于供应链稳定性优化，不是当前危险权限问题，暂不列入优先实施顺序。

### 8.2 待验收清单

OpenClaw 与共享基础 LLM：

- [ ] OpenClaw 稳定完成结构化 Tool Calling。
- [ ] 模型认证失败、限流、超时和回退反馈明确。

Chub Tool：

- [x] MacBook 使用无参数 `chub_get_status` 查询成功。
- [ ] Ubuntu 使用无参数 `chub_get_status` 查询成功。
- [x] MacBook TUI、微信成功调用并收到最终状态。
- [ ] 认证失败、不可达和超时状态受控失败。
- [ ] OpenClaw 不可用不影响 Chub 原入口。
- [x] Tool Schema 无参数，不接受任意 URL、路径、命令或凭证。

微信 ClawBot：

- [ ] Chub 首页二维码绑定流程完成 Ubuntu 实机验收。
- [ ] 退出与重新授权流程完成验收。
- [ ] 未授权账号不能调用 Chub Tool。
- [ ] 普通账号和高风险指定账号权限隔离有效。
- [ ] 多账号或多对端 Session 不串线。
- [ ] 重复消息不会重复执行。
- [ ] Tool 超时、断链和最终回复失败语义明确。

飞书和白名单任务：

- [ ] 固定飞书群单向通知链路完成。
- [ ] 每个任务的目标、参数、风险等级和允许账号明确。
- [ ] 高风险任务未经明确确认不会执行。
- [ ] 最终成功来自目标系统，而不是模型推断。

## 9. 参考资料

- [OpenClaw 安装文档](https://docs.openclaw.ai/install)
- [OpenClaw Gateway 文档](https://docs.openclaw.ai/gateway)
- [OpenClaw 频道文档](https://docs.openclaw.ai/channels)
- [OpenClaw 微信频道文档](https://docs.openclaw.ai/channels/wechat)
- [腾讯微信 OpenClaw 插件](https://github.com/Tencent/openclaw-weixin)
- [OpenClaw 模型供应商文档](https://docs.openclaw.ai/concepts/model-providers)
- [OpenClaw Tool 插件文档](https://docs.openclaw.ai/plugins/tool-plugins)
- [飞书自定义机器人指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
