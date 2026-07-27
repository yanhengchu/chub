# OpenClaw 与消息通道接入设计

> 状态：接入流程初稿，待维护者验收。本文负责说明如何落地，不替代 `OPENCLAW_RESEARCH.md` 的背景调研。

## 1. 文档目标

第三阶段需要打通三类能力：

1. OpenClaw 接入 Chub 与目标电脑。
2. 两类消息通道：
   - 飞书群机器人 Webhook：单向推送通知和结果；
   - 微信 ClawBot：双向接收指令、返回状态，并为后续受控交互提供入口。
3. 其他 LLM 模型接入 OpenClaw。

本文给出推荐拓扑、安装与连接顺序、消息和任务生命周期、安全边界、异常处理与验收步骤。尚未确认的外部接口不写成既定事实。

## 2. 已确认事实与待定配置

### 2.1 已确认

- OpenClaw 使用常驻 Gateway 统一管理频道、Agent、会话、模型和工具。
- 微信 ClawBot 通过腾讯维护的外部频道插件 `@tencent-weixin/openclaw-weixin` 接入 OpenClaw。
- 插件通过二维码完成微信授权，登录状态保存在 OpenClaw 状态目录。
- 微信入站消息由插件转换为 OpenClaw 频道消息，路由给 Agent；回复再由插件发送回原微信会话。
- 插件与 OpenClaw 存在明确版本兼容要求，部署时必须固定并核对版本。
- OpenClaw 可以配置不同模型供应商、默认模型和回退模型。
- Chub 继续独立运行；OpenClaw 或微信通道不可用时，原有 Web、Codex 和自动化入口仍应可用。
- 单向群通知只接入普通飞书群的自定义机器人 Webhook，不接入企业微信或普通微信群 Webhook。
- 微信仅通过 ClawBot 双向收发指令、状态和结果。
- ClawBot 首期只开放状态检查、白名单任务执行和结果查询。
- 高风险指令必须在执行前获得用户明确确认，不能由模型或通道直接执行。
- OpenClaw Gateway 可部署在 MacBook 或 Ubuntu；两者采用同一逻辑方案，实施时按在线稳定性和访问条件选择。
- 其他 LLM 使用维护者已有的 API、Token 和模型信息。

### 2.2 实施时提供或确定

- 首批开放的状态项、任务白名单及风险等级，在实现具体能力时逐项确定。
- 维护者提供飞书群机器人 Webhook 地址；目标群由该地址固定，不另建群映射。默认发送明确配置的状态、任务最终结果和异常消息；启用签名校验时一并提供签名密钥。
- ClawBot 使用已绑定并通过允许列表的微信账号；高风险能力还可以限制为指定账号。
- 维护者在接入模型时提供 API、Token、Base URL 和模型名称，再验证协议及 Tool Calling 能力。

## 3. 推荐总体拓扑

首轮建议只运行一个 OpenClaw Gateway，避免频道、会话和凭证被多个 Gateway 重复管理。

```text
                         ┌──────────────────────┐
                         │ 其他 LLM 模型服务     │
                         └──────────┬───────────┘
                                    │ 模型请求
                                    ▼
微信用户 ◀──双向──▶ 微信 ClawBot ◀──▶ OpenClaw Gateway
                                      │
                                      ├── Agent / Session / Tool Policy
                                      │
                                      ├── Chub 专用适配工具
                                      │       ├──▶ MacBook Chub
                                      │       └──▶ Ubuntu Chub
                                      │
                                      └── 受控电脑交互能力
                                              └──▶ 选定目标电脑

MacBook / Ubuntu Chub ──最终状态──▶ 通知适配器
                                      │
                                      ▼
                          飞书群机器人 Webhook
                                      │
                                      ▼
                                    飞书群
```

推荐保持两条设备操作路径：

- **Chub 路径**：调用已经存在的固定 API，适合节点状态、自动化、Codex 会话和有明确最终状态的受控操作。
- **OpenClaw 电脑交互路径**：承载需要连续观察、输入和反馈的远程操控或交互式操作。该路径必须按目标电脑单独授权，不借用 Chub Token 获得任意系统权限。

两条路径的日志和事实来源不能混淆：Chub 是 Chub 操作状态的事实来源；OpenClaw 记录 Agent、频道、模型和工具调用过程。

## 4. 组件职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| 微信 ClawBot | 双向接收用户消息、发送处理中状态和结果 | 判断设备操作是否最终成功 |
| 飞书群机器人 Webhook | 单向发送通知、摘要和最终结果 | 接收指令、维护会话、控制电脑 |
| OpenClaw Gateway | 频道、Agent、会话、模型、工具路由和策略 | 伪造 Chub 最终状态 |
| 其他 LLM | 理解、规划、生成回复和工具调用意图 | 直接保存设备凭证或绕过工具策略 |
| Chub 适配工具 | 将结构化 Tool 参数映射到固定 Chub API | 拼装任意 URL、路径或系统命令 |
| Chub | 节点能力、安全校验、操作日志和最终状态 | 管理微信会话、模型上下文或频道凭证 |
| 电脑交互适配 | 对选定电脑执行已授权的连续交互 | 默认获得所有电脑和所有系统权限 |

## 5. 部署与接入顺序

必须按以下顺序逐段验证，不能同时接入全部组件后再排障。

### 5.1 确定 Gateway 主机

首选条件：

- 长期在线；
- 能稳定访问外部模型和微信服务；
- 能通过可信网络访问 MacBook 与 Ubuntu Chub；
- 便于保护 OpenClaw 状态目录、模型凭证和 Hub Token；
- 需要本机电脑交互时，具备相应系统权限。

MacBook 与 Ubuntu 均可作为首个 Gateway 主机，方案和能力边界保持一致。实施时根据长期在线条件、网络可达性和本机交互需求选择，不需要在设计阶段预先固定。

### 5.2 安装并验证 OpenClaw

macOS 和 Ubuntu 首选 OpenClaw 官方安装脚本。脚本会检查运行环境、安装所需 Node.js 和 OpenClaw，并默认进入初始化流程：

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

如果本机已经自行维护符合 OpenClaw 要求的 Node.js，也可以使用 npm 安装 CLI：

```bash
node -v
npm -v
npm install -g openclaw@latest
```

OpenClaw 当前支持 Node.js `22.22.3+`、`24.15+` 或 `25.9+`，推荐使用 Node.js 24，Node.js 23 不受支持。官方没有单独规定 npm 的最低版本；npm 应使用随受支持 Node.js 提供的兼容版本。不要只升级 npm 而保留不兼容的 Node.js。

如果 Node.js 版本不满足要求，优先升级到受支持的 Node.js，再重新检查 `node -v` 和 `npm -v`。只有在 Node.js 已符合要求、但 npm 仍因版本过旧导致安装失败时，再升级 npm：

```bash
npm install -g npm@latest
npm -v
```

使用官方安装脚本时，Node.js 检测和必要升级由脚本处理，通常不需要手动升级 npm。

安装后按以下顺序验证。

第一步确认 CLI 已经安装并能从当前 `PATH` 找到：

```bash
openclaw --version
```

该命令应输出版本号。如果提示 `command not found`，先检查安装日志和 npm 全局可执行目录，不继续安装 Gateway 服务。

第二步运行只读诊断，检查配置和运行环境：

```bash
openclaw doctor
```

首次安装尚未完成初始化时，先执行：

```bash
openclaw onboard
```

Chub 首页提供独立的 OpenClaw 运维卡片，作为 Gateway 安装后的本机管理入口。卡片只读取 OpenClaw CLI 的结构化状态，并将启动、停止、重启映射为后端固定命令；接口继续使用 Hub Token，操作完成后必须再次检查 Gateway 最终状态，并记录 `requested`、`started`、`succeeded` 或 `failed`。Tailscale Serve 可用时，卡片从固定状态命令中校验代理目标和 `*.ts.net` HTTPS 地址，提供单一可点击入口；本机 loopback 地址只用于排障。页面不接收任意命令或路径，也不展示 OpenClaw 配置、模型凭证、频道凭证和原始命令输出。

首版运维卡片不负责安装、卸载、升级、初始化配置、代理 OpenClaw 控制台或读取 OpenClaw 原始日志。它与后续“OpenClaw 调用 Chub Tool”是两个独立方向：前者由 Chub 管理本机 Gateway 生命周期，后者由 OpenClaw 通过受限 Tool 调用 Chub 能力。

第三步安装并启动后台 Gateway。也可以在初始化时直接使用 `openclaw onboard --install-daemon` 一并完成：

```bash
openclaw gateway install
openclaw gateway start
openclaw gateway status
openclaw gateway probe
```

安装成功至少满足：

- `openclaw --version` 输出已安装版本；
- `openclaw doctor` 没有阻止启动的错误；
- `openclaw gateway status` 显示 Gateway 运行中并且连接探测正常；
- `openclaw gateway probe` 能连接到预期 Gateway；
- 浏览器打开 `http://127.0.0.1:18789/` 可以访问本机控制界面。

macOS 的后台服务由 launchd 管理，Ubuntu 默认使用 systemd user service。不要同时为同一配置和端口启动多个 Gateway 服务，也不要只因为 CLI 能输出版本号就认为 Gateway 已经部署成功。

首轮只允许 Gateway 监听本机或可信私有网络，不直接暴露公网管理端口。

### 5.3 接入第一个其他 LLM

1. 选择供应商和具体模型。
2. 在 Gateway 主机配置供应商凭证。
3. 设置默认模型；如有需要再配置回退模型。
4. 使用 OpenClaw 的模型状态或连接测试确认：
   - 凭证有效；
   - 模型可用；
   - 超时、限流、额度或计费错误能够被区分。
5. 在没有任何设备工具的情况下完成一次普通对话。

模型凭证只由 OpenClaw 或其 SecretRef/环境配置管理，不写入 Chub 配置、Git、消息内容或操作日志。

### 5.4 接入 Chub 专用工具

建议新增一个范围明确的 OpenClaw Tool 适配层，不让 LLM 自行请求任意 Chub URL。

首轮工具只提供固定只读能力，例如：

- `chub_get_health(node_id)`
- `chub_get_status(node_id)`
- `chub_list_codex_sessions(node_id)`
- `chub_get_automation_status(node_id, automation_id)`

适配层维护固定节点表：

```text
node_id -> Chub base URL -> 独立 Hub Token 引用 -> 允许的能力
```

基本调用流程：

```text
用户意图
  ↓
LLM 选择 Chub Tool
  ↓
OpenClaw 校验 Tool Policy 与参数 Schema
  ↓
适配层按 node_id 查固定地址和凭证
  ↓
调用 Chub API
  ↓
解析 ApiResponse / ApiError
  ↓
OpenClaw 生成用户可读结果
```

要求：

- Tool 参数不接受 base URL、文件路径、Shell 命令或 Hub Token。
- 每个节点使用独立 Token；Token 只在 Gateway 主机解析。
- Chub 返回 `requested` 或 `started` 时只能显示“已请求/执行中”。
- 只有 Chub 返回可验证的最终状态后，才能向用户和群机器人宣告成功。
- OpenClaw 不可用时不能影响 Chub 原有入口。

### 5.5 验证 OpenClaw 与 Chub

在接入微信前先从 OpenClaw 本地 WebChat 或 TUI 完成：

1. 查询 MacBook 健康状态。
2. 查询 Ubuntu 健康状态。
3. 查询一个不存在的节点，确认受控失败。
4. 模拟 Chub 不可达和认证失败。
5. 确认模型回复没有泄露 Hub Token、内部 URL 或原始敏感日志。

通过后才能继续接入消息通道。

## 6. 微信 ClawBot 双向通道

### 6.1 安装与登录

微信通道使用腾讯维护的外部插件：

```bash
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw channels login --channel openclaw-weixin
openclaw gateway restart
```

登录时终端显示二维码，由用户使用微信扫码并确认授权。部署前必须先核对 OpenClaw 与插件的当前兼容版本，不直接照搬历史版本号。

验证：

```bash
openclaw plugins list
openclaw channels status --probe
openclaw --version
```

如存在多个微信账号，使用账号、频道和对端隔离私聊上下文：

```bash
openclaw config set session.dmScope per-account-channel-peer
```

当前官方资料显示该插件主要支持微信私聊，群聊能力不能作为本阶段前提。

### 6.2 消息处理流程

```text
微信用户发送消息
  ↓
腾讯微信通道插件接收并标准化
  ↓
OpenClaw 校验发送者配对/白名单
  ↓
按微信账号 + 频道 + 对端定位 Session
  ↓
Agent 调用其他 LLM
  ↓
LLM 回复，或选择 Chub/电脑交互 Tool
  ↓
OpenClaw 返回接收确认或处理中状态
  ↓
工具执行并产生最终状态
  ↓
OpenClaw 生成结果
  ↓
插件回复原微信私聊
```

### 6.3 操作分级与后续交互

ClawBot 是消息和交互通道，不应直接持有电脑权限。实际操控必须经过 OpenClaw 的 Tool Policy 和目标电脑适配。

操作分级建议：

| 等级 | 示例 | 默认处理 |
|---|---|---|
| L0 只读 | 状态、进程摘要、任务结果 | 已配对且获得授权的用户可直接执行 |
| L1 可恢复操作 | 启动已验收的白名单任务 | 按任务风险策略执行；高风险任务必须确认 |
| L2 持续交互 | 电脑界面操作、终端/Agent 多轮交互 | 建立有时限的交互会话 |
| L3 高风险操作 | 删除、权限修改、重启或其他高影响操作 | 默认不开放；开放时必须逐次明确确认 |

交互会话至少包含：

- `interaction_id`
- 微信账号与对端标识
- OpenClaw `session_key`
- 目标设备与能力
- 创建时间、最后活动时间和过期时间
- 当前状态：`requested / awaiting_confirmation / running / waiting_input / succeeded / failed / expired`

同一微信消息不能因为重试而重复执行操作。消息接入层应生成或提取稳定的消息 ID，并与 `interaction_id` 建立幂等关系。

长操作不能保持沉默。建议：

1. 快速返回“已接收”；
2. 真正开始后返回“执行中”；
3. 需要用户输入时明确提问；
4. 完成后返回最终结果；
5. 原会话无法回复时记录投递失败并等待用户重新查询；只有明确配置为群通知的事件才发送到飞书，不把微信私聊内容自动转发到群。

### 6.4 配对与访问控制

- ClawBot 登录绑定只代表通道建立成功，不自动授予设备操作权限。
- 发起指令的微信账号必须通过配对或明确 allowlist；未授权账号即使能联系机器人，也不能连接 Chub 或执行任务。
- 权限可按账号分级。普通授权账号只能使用只读或低风险白名单能力；高风险能力可以仅授予指定固定账号，并且执行前仍需逐次确认。
- 多微信账号使用独立账号配置和 Session 范围。
- 用户身份只能用于 OpenClaw 频道授权；Chub 操作日志继续记录固定来源和操作，不伪造多用户审计能力。
- 二维码、登录凭证、上下文 Token 和插件状态目录不得进入 Git、Chub 日志或 LLM 上下文。
- 解除授权、插件禁用和 Gateway 停止后，微信入口必须立即失效。

## 7. 飞书群机器人 Webhook 单向通道

### 7.1 定位

飞书群机器人 Webhook 只负责：

- 服务和节点状态通知；
- 自动化任务完成或失败通知；
- ClawBot 长任务最终摘要；
- 需要人工处理的告警。

它不负责：

- 接收群消息；
- 解析用户指令；
- 维持 Agent Session；
- 执行电脑操作。

### 7.2 推送流程

```text
Chub / OpenClaw 获得最终事件
  ↓
通知适配器校验事件类型和脱敏规则
  ↓
生成受限长度的文本或 Markdown
  ↓
POST 到固定飞书群机器人 Webhook
  ↓
记录本次通知的成功、失败或限流
```

### 7.3 安全与可靠性

- Webhook URL 等同凭证，只保存于本机秘密配置或环境变量。
- Webhook 地址同时确定目标飞书群，维护者提供地址后不再单独配置群标识。
- 如果机器人启用了签名校验，签名密钥与 Webhook URL 按同等敏感级别管理。
- URL 不进入前端、日志、测试输出、LLM 上下文或 Git。
- 只允许固定 Webhook 目标，不接受用户传入任意通知地址。
- 通知失败不改变原业务操作的成功或失败语义。
- 重试必须有次数和退避上限，并使用事件 ID 防止重复刷屏。
- 消息只包含必要摘要；日志、用户内容、路径和错误堆栈在发送前脱敏或省略。
- 具体消息类型、签名方式、大小和频率限制在接入时以飞书当前规则及机器人配置为准。

## 8. 其他 LLM 模型接入

其他 LLM 只接入 OpenClaw，不直接接入微信插件或 Chub。

推荐配置顺序：

1. 配置一个主模型。
2. 验证普通对话和结构化 Tool 调用。
3. 设置请求超时、上下文和输出上限。
4. 如确有需要，再配置同能力等级的回退模型。
5. 设置供应商费用或额度告警。

模型切换不能改变：

- 可见的 Tool 列表；
- Tool 参数 Schema；
- 用户配对和 allowlist；
- 高风险操作确认；
- Chub 固定节点和能力映射。

验收时分别验证认证失败、模型不存在、限流、额度耗尽、超时和不支持 Tool Calling 等情况。

## 9. 状态与消息语义

对用户和群机器人统一使用以下语义：

| 状态 | 对外含义 |
|---|---|
| `received` | 微信/OpenClaw 已收到消息，尚未执行设备操作 |
| `requested` | 已向工具或 Chub 请求操作 |
| `awaiting_confirmation` | 高风险操作正在等待授权账号明确确认，尚未执行 |
| `running` | 目标系统确认正在执行 |
| `waiting_input` | 需要用户继续输入或确认 |
| `succeeded` | 目标系统确认最终成功 |
| `failed` | 目标系统确认失败或已无法继续 |
| `expired` | 交互会话超时关闭，不能继续复用 |

禁止把“微信已送达”“LLM 已回复”“Tool 已创建”或 HTTP 200 单独解释为设备操作成功。

## 10. 失败与恢复

| 故障 | 用户反馈 | 恢复原则 |
|---|---|---|
| 微信插件离线 | 微信入口不可用 | 原 Chub 与 OpenClaw 本地入口继续运行 |
| Gateway 不可用 | ClawBot 无法处理 | 服务恢复后重新探测，不重复旧操作 |
| LLM 认证/限流失败 | 明确模型不可用 | 可用回退模型时切换，否则停止 |
| Chub 不可达 | 明确目标节点离线 | 不改用任意 Shell 绕过 |
| Tool 超时 | 显示状态未知或失败 | 查询目标最终状态后再决定是否重试 |
| Webhook 限流/失败 | 原任务结果不变 | 有界重试，最终记录通知失败 |
| 微信重复消息 | 返回已有交互状态 | 通过消息 ID 保证幂等 |
| 交互会话过期 | 提示重新发起 | 不恢复旧权限或旧确认 |

## 11. 分阶段实施

### 阶段 A：OpenClaw 基线

- 安装单一 Gateway。
- 通过 Chub 独立卡片检查 Gateway 状态，并完成受控启动、停止和重启。
- 接入一个其他 LLM。
- 完成本地普通对话。
- 不接入微信和电脑工具。

### 阶段 B：Chub 只读工具

- 接入 MacBook 与 Ubuntu 固定节点。
- 只开放健康、状态和任务结果查询。
- 验证认证、失败、超时和脱敏。

### 阶段 C：飞书群机器人单向通知

- 配置一个测试群 Webhook。
- 只发送测试通知和固定最终结果。
- 验证限流、失败和重复通知。

### 阶段 D：ClawBot 双向消息

- 安装腾讯微信插件并扫码授权。
- 只允许已配对且进入允许列表的用户。
- 验证私聊消息、Session 隔离和结果回复。

### 阶段 E：首批白名单任务

- 在只读查询稳定后接入少量已验收任务。
- 逐项确定目标节点、参数、风险等级和允许账号。
- 验证请求、确认、执行中状态、最终结果和重复消息幂等。

### 后续扩展：远程操控与持续交互

- 不属于首个 PoC 的必需范围。
- 选择一台目标电脑和一个明确场景。
- 按操作等级设计权限、确认和过期。
- 验证持续状态、用户输入、中断和最终结果。
- 通过后再考虑第二台电脑或更多能力。

## 12. 验收清单

### 12.1 OpenClaw 与模型

- [ ] Gateway 可后台运行、重启和诊断。
- [ ] Chub 卡片能区分未安装、未初始化、服务未安装、已停止、运行正常和异常状态。
- [ ] Chub 卡片的启动、停止和重启只执行固定命令，并以 Gateway 最终状态作为结果。
- [ ] Tailscale Serve 可用时，Chub 卡片只展示经过校验的 HTTPS 控制台入口。
- [ ] 一个其他 LLM 完成普通对话与 Tool Calling。
- [ ] 模型失败、超时和限流反馈明确。

### 12.2 Chub

- [ ] MacBook 与 Ubuntu 使用固定 `node_id` 查询成功。
- [ ] 非法节点、参数、Token 和不可达状态受控失败。
- [ ] OpenClaw 不可用不影响 Chub 原入口。

### 12.3 飞书群机器人 Webhook

- [ ] 单向通知成功，群内消息不能反向触发操作。
- [ ] Webhook 凭证未泄露，失败与限流不会改变原任务状态。

### 12.4 微信 ClawBot

- [ ] 插件与 OpenClaw 版本兼容。
- [ ] 二维码登录、退出和重新授权正常。
- [ ] 未授权账号不能访问 Chub 或执行任务。
- [ ] 普通授权账号与高风险指定账号的权限隔离有效。
- [ ] 多账号或多对端 Session 不串线。
- [ ] 重复消息不会重复执行。

### 12.5 首批白名单任务

- [ ] 状态检查、任务执行和结果查询链路完整。
- [ ] 每个任务的目标、参数、风险等级和允许账号明确。
- [ ] 高风险任务未经指定账号明确确认不会执行。
- [ ] 超时、断链、取消和过期行为明确。
- [ ] 最终成功来自目标系统，而不是模型推断。

远程操控与持续交互在进入后续扩展时单独补充验收项，不作为首个 PoC 的通过条件。

## 13. Review 结论

当前方案可以作为第三阶段接入流程基线。Gateway 平台、消息通道分工、首期 ClawBot 能力、高风险确认、微信授权原则和 LLM 来源已经明确，当前没有需要维护者提前决定的架构问题。

任务白名单和风险等级随具体功能落地确定；飞书 Webhook 地址、微信授权账号以及 LLM 连接参数在对应接入步骤提供即可。实施前只需针对当次功能确认实际配置和验收输入，不再改变总体架构。

文档已将飞书单向通知、微信双向会话、Chub 受控操作和后续电脑交互分层，避免把“绑定微信”直接等同于“拥有设备操作权限”。

## 14. 参考资料

- [OpenClaw 官方安装文档](https://docs.openclaw.ai/install)
- [OpenClaw Gateway 运维文档](https://docs.openclaw.ai/gateway)
- [OpenClaw 频道文档](https://docs.openclaw.ai/channels)
- [OpenClaw 微信频道文档](https://docs.openclaw.ai/channels/openclaw-weixin)
- [腾讯微信 OpenClaw 插件](https://github.com/Tencent/openclaw-weixin)
- [OpenClaw 模型供应商文档](https://docs.openclaw.ai/concepts/model-providers)
- [OpenClaw Tool 插件文档](https://docs.openclaw.ai/plugins/tool-plugins)
- [OpenClaw 插件构建文档](https://docs.openclaw.ai/plugins/building-plugins)
- [飞书自定义机器人使用指南](https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot)
