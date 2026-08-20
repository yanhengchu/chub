# OpenClaw 定制集成设计

> 状态：已验收。
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认边界、部署和验收。
> 本文负责：Chub 对 OpenClaw/微信 ClawBot 的业务身份、权限、路由、插件定制、Context Token 持久化和结果通知边界，作为唯一设计总览；实现时先读本文，再按“权威来源”表读取具体契约。
> 本文不负责：微信固定指令语法和用户可见回复格式（见[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)），以及插件源码、协议字段、构建和部署操作（见[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)）。
> 维护说明：Chub 插件 v3 与 Context Token 持久化兼容方案已完成当前范围验收；仅在第三方微信插件升级、重装、加载目录变化或出现 Token/出站异常时触发复检，不代表存在待实现功能。

## 0. AI 快速契约

以下规则是不可推断、不可放宽的约束：

1. Chub 是业务控制面和可靠协调者；OpenClaw 负责 Gateway、通道、账号、Owner 和可信消息上下文；Codex/Worker 负责执行。
2. 微信 ClawBot 设备请求只能走一次固定链路：`微信 -> OpenClaw 插件 -> Chub dispatch -> Codex/Worker -> Chub -> OpenClaw -> 微信`。
3. 插件不得调用 `openclaw agent`、Gateway Agent 或模型来决定路由；Chub 也不得为处理微信设备请求反向调用 Agent。
4. 微信高权限入口只接受同机 OpenClaw 的真实 loopback socket、当前绑定的单一微信 Owner、私聊消息和可验证的稳定消息 ID。任何条件不满足都失败关闭。
5. 请求正文不得由客户端指定 Session、workspace、权限、模型、路径、命令或收件人。Session、Request、Worker 和通知路由由 Chub 根据固定规则选择。
6. 同一稳定消息 ID 与同一路由只能产生一个决定和一个派生任务；重复请求返回首次决定，冲突路由拒绝，未知副作用不自动重试。
7. 任务结果和通知结果是两个状态。任务成功不等于通知成功；原保存的账号、发送者和路由失效时不得回退到全局收件人。
8. 微信 Context Token 必须由微信插件按 `accountId + userId` 持久化，Gateway 启动恢复，内存缺失时懒恢复；状态文件写入权限为 `600`。Chub 不读取、不保存 Token 正文。
9. 普通 Gateway/Chub 重启、重新扫描或重新绑定不是 Context Token 补丁触发条件；仅在插件升级、重装、运行时代码变化或确认 Token 持久化缺失时复检。
10. 任何“已创建任务”“HTTP 200”“已开始通知”都不是最终成功；必须确认任务终态、实例健康状态或通知发送结果。

## 1. 范围与权威来源

本文合并三类内容：Chub/OpenClaw 业务接入、Chub 插件协议边界、微信 ClawBot Context Token 的持久化兼容规则。它不复制其他文档的细节。

| 问题 | 唯一权威来源 |
| --- | --- |
| 当前可调用能力、固定指令、用户可见回复格式 | [`CHUB_INTEGRATION_CAPABILITIES.md`](CHUB_INTEGRATION_CAPABILITIES.md) |
| OpenClaw 业务身份、权限、路由、幂等和通知边界 | 本文 |
| Session、Activity、入口、槽位和单 writer | [`AI_SESSION_STATE_DESIGN.md`](AI_SESSION_STATE_DESIGN.md) |
| Quick Worker 执行、恢复、通知终态和重启协调 | [`CHUB_QUICK_WORKER_DESIGN.md`](CHUB_QUICK_WORKER_DESIGN.md) |
| 插件源码、协议字段、构建、部署和协议升级 | [`integrations/openclaw/chub/README.md`](../integrations/openclaw/chub/README.md) |
| 第三方微信插件 Context Token 复检和安全恢复 | 本文第 7 节 |

冲突处理顺序：本文的安全、身份、路由、持久化边界优先；能力清单优先于本文的指令语法和回复格式；插件 README 优先于本文的具体构建命令。

## 2. 组件职责与消息链路

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| OpenClaw Gateway/通道 | 账号、Owner、微信私聊上下文、入站/出站传输 | Chub 业务授权、Session 选择、任务终态 |
| Chub | 认证、业务规则、Session/Request 选择、幂等、操作状态、通知关联 | 保存微信 Token 正文、调用 OpenClaw Agent |
| Quick Worker/Codex | 非实时任务、执行租约、恢复、终态 | 重新解释微信身份或选择收件人 |
| Chub 插件 | 获取可信上下文、调用固定 dispatch、按返回决定交付 | 自行路由、拼接任意命令、改变权限 |

微信消息处理顺序：插件取得可信上下文 -> 仅调用固定 dispatch 一次 -> Chub 校验入口和身份 -> 匹配固定能力或创建文本/语音任务 -> 同步返回 `pass`、`reply` 或 `handled` -> 异步任务沿保存的账号和发送者路由最终结果。

插件协议版本、请求字段和三种 disposition 以插件 README 为准；插件请求中不得携带业务 Session、任务 ID 或命令字段。

## 3. 身份、权限与失败关闭

当前微信 Chub 模式仅支持单节点、单一健康 ClawBot、单一微信 Owner 和当前绑定 Session。高权限提交必须同时满足：

- 来源是同机真实 loopback socket，不信任客户端转发 Header；
- 通道上下文确认是当前 Owner 的私聊，不接受群聊、未知发送者或不稳定消息 ID；
- 本次请求存在可回送的原始账号、发送者和消息路由。

请求体只能包含协议需要的干净内容、可信消息类型和插件提供的路由上下文。客户端不能选择任意文件、命令、模型、workspace、Session、权限或收件人。绑定关系撤销即失效；`Full access` 只表示当前受控微信 Chub 模式，不是 Session 的永久属性，也不授权其他 Agent。

身份、路由、通道或返回地址不确定时，拒绝请求并记录可关联的非敏感标识；不得猜测、切换目标或使用全局回退。

## 4. 调度、Session 与幂等

Chub 先校验模式、来源、Owner、私聊、稳定消息 ID 和回送路由，再按能力清单匹配固定指令；普通文本或可信语音才进入任务提交。文本优化失败时失败关闭，不执行原文，不切换目标，不重复提交派生任务。语音必须有通道可信来源标记，正文使用干净转写。

Chub 根据 Session 状态选择唯一入口并锁定 Request/槽位版本。Session 忙、writer 状态未知、版本冲突或路由无效时返回明确拒绝。派生任务 ID 必须稳定；相同消息 ID 重放首次决定，冲突路由拒绝，进程重启后无法确认副作用时也拒绝自动重试。

同步响应只表达“插件应如何处理本条消息”，不代表异步任务已经完成。异步结果必须保存任务 ID、账号、发送者和原始路由，最终结果与通知失败分开记录。

## 5. 通知与 OpenClaw Agent 边界

任务完成后只能使用任务保存的账号和发送者调用 `openclaw message send`。结果过长按固定上限最多分 5 段并编号；发送失败不得改发全局目标。页面快速交互使用能力清单规定的全局固定接收人，与微信任务路由隔离。

普通 OpenClaw Agent 只可使用固定、低风险的状态或飞书 Tool；Tool 不接受任意 URL、open id、密钥、路径或命令，也不参与微信路由。首页仅提供受控状态、启动、重启和绑定操作，不展示配置正文或原始日志；操作成功必须以最终实例和健康状态确认。

## 6. Chub 插件定制规则

插件源码、静态清单、测试和构建产物必须在仓库 `integrations/openclaw/chub/` 同步维护。运行时目录只能接收仓库构建并验证的产物，不得直接编辑部署副本。协议升级必须同步 Chub、插件、测试、构建、部署状态和文档；具体命令、配置、安装和诊断步骤只写在插件 README。

插件的最小行为是：读取可信通道上下文、调用 `/api/openclaw/wechat-chub-mode/dispatch` 一次、按 Chub 返回的 disposition 处理消息。超时、响应无效、协议版本不匹配或 Chub 拒绝时必须失败关闭，不自行重试或改走 Agent。

## 7. Context Token 持久化与兼容恢复

### 7.1 必须保持的行为

微信插件以 `accountId + userId` 为键持久化最新 Context Token；账号启动时恢复；出站调用未显式提供 Token 时，按账号和收件人查询；内存未命中时懒从磁盘恢复。每次写入都保证文件权限 `600`，已有文件也要校正权限。缺失或无效 Token 必须报错，不得借用其他账号/收件人，也不得调用 `openclaw agent`。

Context Token 没有本文定义的 TTL 或刷新机制；本地观察到的约 10 分钟只可作为现象，不能写成协议保证。典型错误区分为 `weixin_context_missing` 和 `sendMessage ret=-2 errmsg=prepare failed`；后者通常需要目标先产生一条新的入站消息。

### 7.2 何时复检和如何恢复

| 触发 | 处理 |
| --- | --- |
| 微信插件升级、重装、安装目录重建、运行时代码变化 | 读取实际加载目录，检查上游实现和持久化状态；缺失才做最小补丁 |
| 已确认账号/目标近期入站后仍持续缺 Token | 按同样流程复检并恢复 |
| 普通 Gateway/Chub 重启、重新扫描、重新绑定 | 不自动打补丁；只做正常状态确认 |

AI Agent 执行顺序：先读 `AGENTS.md` 和本文；用 `openclaw plugins inspect openclaw-weixin --json` 找到实际加载根目录；保留无关改动和现有配置；检查上游不变量；仅在功能缺失时应用与当前源码匹配的最小改动。稳定检查锚点通常是 `dist/src/messaging/inbound.js` 的保存/恢复/getContextToken 和 `dist/src/channel.js` 的出站 Token 回退，但升级后必须重新确认，不能盲套旧补丁。参考补丁只能先 dry-run，源码已等价时不重复应用。

验证只打印元数据，不打印 Token 或完整收件人：检查语法、文件类型/所有者/权限、Gateway 最终健康状态；由维护者在微信客户端先发送入站消息，再确认真实出站收件结果。Chub 任务终态、通知状态和微信实际收件是三个独立验收点。

## 8. 故障处理矩阵

| 状态 | 必须做 | 禁止做 |
| --- | --- | --- |
| 身份、来源、路由不明 | 拒绝并记录关联 ID | 猜测 Owner、Session、收件人 |
| 同消息 ID 重复 | 返回首次决定 | 再建任务或重复通知 |
| Worker/任务状态未知 | 保持失败关闭，等待可确认状态 | 当作成功或自动重试副作用 |
| Context Token 缺失 | 报明确错误，要求新入站或执行兼容复检 | 借用别的账号 Token、全局回退 |
| 任务成功但通知失败 | 保留任务成功，单独记录通知失败 | 把通知失败改写为任务失败 |
| 插件升级后协议不匹配 | 停止提交，按升级清单同步验证 | 直接编辑运行时副本或跳过构建 |

日志、响应、测试和文档不得包含 Token、Authorization、终端票据、完整账号或收件人信息。第三方状态文件不得任意删除；恢复失败时保留可诊断状态并等待维护者确认。

## 9. 验收与变更清单

插件自动化至少覆盖：协议版本和字段拒绝、真实 loopback 来源边界、Owner/私聊校验、重复消息幂等、路由冲突、失败关闭、通知与任务状态分离。Context Token 持久化属于第三方微信插件运行时兼容能力；仓库补丁提供变更基线，实际验收以实际加载目录代码、Token 文件权限、Gateway 健康和维护者真实微信出站结果为准，不能把 Chub 插件测试当作 Token 补丁的自动化覆盖。插件变更另执行 README 的构建、校验、测试和部署检查。

真实微信文字和语音的发送、点击和收件只能由维护者在微信客户端完成；AI Agent 只检查 Chub/OpenClaw 后台日志、任务状态和通知终态，后台记录不能替代客户端确认。验收还应确认 Gateway/Chub/Worker 的普通重启相互独立，升级或恢复操作按其自身锁定规则最终完成，不把“请求已受理”当作成功。

每次变更回答四个问题：改变了哪个权威边界？是否同步 Chub、插件、测试、构建产物和部署状态？是否改变能力清单中的用户契约？是否完成最终状态和真实微信收件验证？当前已验收范围没有待实现的功能优化；只有外部插件变更或 Token/出站异常才触发复检。

### 9.1 验收范围与复检

- 已验收范围：Chub 插件 v3 的协议、身份、路由、幂等、失败关闭、通知终态，以及 Context Token 持久化兼容方案的当前加载目录检查、权限检查、Gateway 健康和维护者真实微信收件确认。
- 未验证或不承诺：未由维护者在微信客户端执行的文字/语音发送、点击和收件结果；其他微信插件版本、加载目录或未经构建验证的运行产物也不视为已验收。
- 复检触发：第三方插件升级/重装、实际加载目录变化、Token 持久化或出站行为异常、插件协议字段/版本、身份路由、幂等、通知格式或能力清单用户契约变化时，必须同步插件源码、构建产物、测试和部署状态，并重新完成后台终态与维护者微信收件验收。

## 10. 相关文档

- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前能力和微信固定指令唯一产品契约。
- [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)：插件源码、构建、安装、部署和协议升级操作手册。
- [AI Session 状态模型设计](AI_SESSION_STATE_DESIGN.md) 与 [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：执行内部状态和恢复边界。
- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：Chub Runtime 分层与演进路线。
