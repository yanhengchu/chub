# OpenClaw 定制集成设计

> 状态：已验收
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认定制范围、版本、部署和验收边界。
> 本文负责：Chub 对 OpenClaw/微信 ClawBot 的最小定制范围、身份与路由边界、插件归属、第三方适配器兼容补丁、Context Token 持久化和验收规则。
> 本文不负责：微信固定指令语法和用户可见回复格式（见[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)），插件协议、构建和部署命令（见[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)），以及 OpenClaw 或腾讯微信插件自身的上游功能。

## 0. 维护基线

按[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)，OpenClaw/微信 ClawBot 属于第三方服务层。它依赖 Chub 核心层的固定入口、身份边界和维护能力；需要 AI 时只调用 AI Runtime 层公开的任务用例。它不拥有、读取或修改核心层、AI Session Manager、Quick Worker 或 Runtime Adapter 的私有状态。核心层和 AI Runtime 均不得反向依赖 OpenClaw 的具体实现。

Chub 对 OpenClaw 的定制只保留以下内容：

1. 仓库内的通用 Chub OpenClaw 插件，负责可信 Hook 适配和固定 dispatch 路由。
2. 第三方微信适配器的可信语音来源兼容，使 Chub 能区分可信语音和普通文本。
3. 第三方微信适配器的 Context Token 持久化、启动恢复、懒恢复和出站回退。
4. 第三方微信适配器日志中的账号/收件人标识脱敏，不改变消息和路由行为。

普通文本原始正文的首尾空格不属于 Chub 兼容承诺。不得为了保留这类空格修改 OpenClaw 核心、微信适配器或 Chub 插件；固定指令由 Chub 按集成能力清单从消息开头解析。

当前本机验收基线如下。版本只表示本次已检查的组合，不表示其他版本自动兼容：

| 对象 | 当前验收版本 | 仓库来源或运行位置 |
| --- | --- | --- |
| Chub OpenClaw 插件 | `0.1.1` | 源码和构建入口：`integrations/openclaw/chub/`；运行副本位置以 `openclaw plugins inspect chub --runtime --json` 为准 |
| 腾讯微信适配器 | `@tencent-weixin/openclaw-weixin@2.4.6` | 运行副本的 `rootDir` 和 `source` 以 `openclaw plugins inspect openclaw-weixin --runtime --json` 为准 |
| OpenClaw Gateway | `2026.7.1-2` | 由 OpenClaw 官方安装路径提供，不在本仓库维护 |
| 微信适配器可信语音补丁 | `weixin-voice-transcript-origin@1.0.0` | `integrations/openclaw/patches/weixin-clawbot-voice-transcript-origin.patch` |
| 微信适配器 Context Token 补丁 | `weixin-context-token-persistence@1.0.0` | `integrations/openclaw/patches/weixin-clawbot-context-token-persistence.patch` |
| 微信适配器日志脱敏补丁 | `weixin-log-redaction@1.0.0` | `integrations/openclaw/patches/weixin-clawbot-log-redaction.patch` |

补丁 ID 和独立版本以 [`integrations/openclaw/patches/manifest.json`](../integrations/openclaw/patches/manifest.json) 为唯一元数据来源；补丁版本与 Chub 插件版本、微信适配器版本分别管理，但每个补丁仍只对清单记录的目标包版本、包完整性和 OpenClaw 版本负责。版本、来源、补丁锚点或实际加载目录变化时，当前基线立即失效，必须按第 7 节重新检查。仓库补丁是版本相关的参考变更，不是对所有 OpenClaw/微信插件版本通用的脚本。

## 1. AI 可执行契约

以下规则不可推断、不可放宽：

1. Chub 是业务控制面和可靠协调者；OpenClaw 负责 Gateway、通道、账号、Owner 和可信消息上下文；Codex/Worker 负责执行。
2. 微信请求只能走：`微信 -> OpenClaw 微信适配器 -> Chub 插件 -> Chub dispatch -> Codex/Worker -> Chub -> OpenClaw -> 微信`。
3. Chub 插件和 Chub 服务不得调用 `openclaw agent`、Gateway Agent 或模型来决定微信请求路由。
4. 微信高权限入口必须来自同机真实 loopback socket、当前绑定的单一微信 Owner、私聊消息和稳定消息 ID；任一条件不满足都失败关闭。
5. 请求正文不得选择 Session、workspace、权限、模型、路径、命令或收件人；这些对象由 Chub 固定规则选择。
6. 同一稳定消息 ID 与同一路由只能产生一个决定和一个派生任务；重复请求返回首次决定，路由冲突拒绝，未知副作用不自动重试。
7. 任务结果和通知结果是两个状态；任务成功不等于通知成功，原保存路由失效时不得回退到全局收件人。
8. Context Token 必须按 `accountId + userId` 持久化，Gateway 启动恢复，内存未命中时懒恢复；文件权限必须为 `600`。
9. API action `restart` 和微信固定指令 `restart clawbot` 都是 ClawBot 的重启与恢复入口：发现固定插件或补丁基线不一致时先同步，再重启 Gateway；未知版本、包完整性或补丁锚点不得盲目覆盖。
10. “进程已启动”“HTTP 200”“任务已创建”或“通知已开始”都不是最终成功；必须确认业务终态、健康状态或通知终态。

## 2. 归属与消息链路

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| OpenClaw Gateway/微信适配器 | 账号、Owner、微信上下文、消息收发、可信语音来源和 Context Token | Chub 指令解释、Session 选择、任务终态 |
| Chub OpenClaw 插件 | 可信 Hook 上下文、固定 dispatch 请求、`pass`/`reply`/`handled` 交付 | 业务指令、Session/任务路由、任意命令或正文修复 |
| Chub 核心入口 | 身份校验、固定业务路由、需求储备和维护能力 | 保存微信 Token、调用 OpenClaw Agent、拥有 AI Session 或任务终态 |
| AI Runtime（Quick Worker/Codex） | Session 选择、任务执行、租约、恢复和执行终态 | 重新解释微信身份或选择收件人 |
| OpenClaw 核心 | 上游标准消息、Hook 和指令解析能力 | Chub 定制源码；不得为本项目修改核心 |

微信消息处理顺序是：

```text
微信 -> 微信适配器接收并标准化 -> Chub 插件 before_dispatch
     -> Chub 固定 dispatch -> 同步 pass/reply/handled
     -> Chub 任务或固定指令处理 -> 按保存路由发送最终结果
```

同步结果只表示插件如何处理当前消息，不表示异步任务已完成。任务终态、通知终态和微信客户端实际收件必须分别确认。

## 3. Chub 插件边界

插件源码、静态清单、测试和构建产物的唯一仓库来源是 `integrations/openclaw/chub/`。插件版本以 `package.json` 和 `openclaw.plugin.json` 为准；当前验收版本为 `0.1.1`。

插件只做以下事情：

- 校验微信 Chub 模式、通道、私聊和可信上下文。
- 使用 Hook 的 `event.content` 作为普通文本正文；不从 `event.body` 猜测普通文本原文，不覆盖正文，不替换空格。
- 对可信语音来源取得干净转写并标记为 `voice`；普通文本不能自行声明为语音。
- 向固定的 `/api/openclaw/wechat-chub-mode/dispatch` 调用一次。
- 原样执行 Chub 返回的 `pass`、`reply` 或 `handled`。

插件不得调用 Agent/LLM，不解释 Chub 业务指令，不生成 Session 或任务摘要，不跟踪异步终态，不因 Chub 不可达而回退到 Agent。

插件协议、请求字段、构建、安装、部署和协议升级清单以[插件说明](../integrations/openclaw/chub/README.md)为准；本节不复制协议细节。

## 4. 第三方微信适配器兼容边界

第三方适配器不属于本仓库的插件源码。仓库只保存与已确认版本对应的最小参考补丁，实际运行副本必须先通过版本和锚点检查。

### 4.1 可信语音

补丁 `weixin-voice-transcript-origin@1.0.0` 的文件为：

`integrations/openclaw/patches/weixin-clawbot-voice-transcript-origin.patch`

它只解决来源类型边界：适配器根据通道提供的可信语音消息项设置固定标记，普通文本使用标准化正文；Chub 插件只有看到可信标记时才读取干净转写并发送 `message_type=voice`。不得根据正文内容、客户端字段或用户声明猜测语音来源。

普通文本继续使用上游字段分工和去空格正文。该补丁不保证保留普通文本的首尾空格，也不改变 OpenClaw 核心的指令解析。

### 4.2 Context Token

补丁 `weixin-context-token-persistence@1.0.0` 的文件为：

`integrations/openclaw/patches/weixin-clawbot-context-token-persistence.patch`

必须保持以下行为：

- 以 `accountId + userId` 为键保存最新 Token。
- Gateway 启动时恢复当前账号的 Token。
- 内存未命中时按账号和收件人从磁盘懒恢复。
- 写入和已有文件权限均校正为 `600`。
- 出站未提供 Token 时使用已恢复的对应 Token。
- Token 缺失或无效时明确失败，不借用其他账号/收件人的 Token，不调用 `openclaw agent`。

Context Token 没有本文定义的 TTL 或刷新保证。约 10 分钟等本机观察只能作为诊断现象，不能写成协议。

### 4.3 日志脱敏

补丁 `weixin-log-redaction@1.0.0` 只将适配器入站/出站日志中的完整 `from/to` 标识替换为既有 `redactToken` 的前缀和长度形式；消息正文、路由、发送目标和 Chub 请求字段不因此改变。Token 仍必须使用同一脱敏函数，日志不得记录完整账号、收件人、正文或凭证。

## 5. 安全、路由与失败边界

微信 Chub 模式只支持单节点、单一健康 ClawBot、单一微信 Owner、私聊和当前绑定 Session。提交必须来自真实 loopback socket，不信任客户端转发 Header。

身份、来源、路由、消息 ID 或回送地址不确定时，拒绝请求并记录非敏感关联标识；不得猜测 Owner、Session 或收件人，不得使用全局回退。

任务完成后只能使用任务保存的账号和发送者回送结果。结果过长按固定上限分段；回送失败不得改发其他目标。任务成功与通知失败必须分开记录。

文本优化失败时失败关闭：不得执行原文、重复提交或生成虚假的任务成功状态。确认模式将译文、目标、可信回送路由、到期时间和确认结果持久化；译文通知成功送达前不解析 `text` 确认命令，确认只提交保存的润色中文。确认队列与单一翻译 FIFO 分离，翻译可以继续而确认按 FIFO 展示；已确认的忙目标仅对该目标重试，不改投、不回退原文。携带正文的 `S#` 槽位指令会先持久化完成固定切换；随后优化失败时保留该切换结果，但正文不得提交。语音必须使用可信来源标记和干净转写。

日志、响应、测试和文档不得包含 Token、Authorization、终端票据、完整账号、完整收件人或完整消息正文。

## 6. 故障处理矩阵

| 状态 | 必须做 | 禁止做 |
| --- | --- | --- |
| 身份、来源或路由不明 | 拒绝并记录关联 ID | 猜测 Owner、Session 或收件人 |
| 同消息 ID 重复 | 返回首次决定 | 再建任务或重复通知 |
| Worker/任务状态未知 | 等待可确认状态并保持失败关闭 | 当作成功或自动重放副作用 |
| Context Token 缺失 | 报明确错误，要求新入站或执行兼容复检 | 借用其他 Token 或全局回退 |
| 任务成功但通知失败 | 保留任务成功，单独记录通知失败 | 把通知失败改写为任务失败 |
| 补丁版本或源码锚点不匹配 | 停止应用，恢复官方副本并重新评估 | 强行套用旧补丁或直接改部署副本 |
| 插件协议不匹配 | 停止提交，按插件升级清单同步验证 | 回退 Agent 或绕过协议校验 |

## 7. 版本、部署与复检流程

### 7.1 发现实际运行版本

先执行以下只读检查：

```bash
openclaw --version
openclaw plugins inspect chub --runtime --json
openclaw plugins inspect openclaw-weixin --runtime --json
```

记录但不写入凭证：插件版本、`packageName`、`resolvedVersion`、`rootDir`、`source`、加载状态和包完整性信息。实际运行目录以命令结果为准；不得假定 `~/.openclaw/extensions/` 或某个用户绝对路径永远不变。

### 7.2 应用第三方兼容补丁

1. 读取 `integrations/openclaw/patches/manifest.json`，对照第 0 节版本基线确认包名、版本、包完整性、OpenClaw 版本、补丁 ID、补丁版本和锚点。
2. 在实际运行目录同时检查源码与运行产物的补丁锚点。源码或运行产物已具备等价行为时不重复应用；只具备单侧差异时不得宣称补丁完成。
3. 手工应用补丁时，版本不匹配、锚点缺失、补丁上下文不一致或构建工具链不可用时停止，不强行修改；“重启与恢复”入口只在 Gateway 版本匹配时，先恢复清单指定的适配器版本，再继续固定补丁同步。
4. 应用后从源码重新构建运行产物，并检查源文件/运行产物、文件权限、非敏感版本信息和关键行为；不得打印 Token 或完整收件人。
5. 重新加载 Gateway，确认 Gateway 可达、微信插件 `loaded`、微信账号 `running`、Context Token 已恢复且无新的兼容错误。
6. 由维护者在真实微信客户端分别验证普通文字、可信语音、Gateway 重启后的出站回送和 Chub 任务最终通知。

补丁只针对第三方适配器的实际运行副本；不得把补丁逻辑复制到 Chub 插件，不得从运行副本反向维护仓库源码。第三方 npm 重装或升级会覆盖补丁，升级后必须从第 7.1 节重新开始。

### 7.3 回滚

补丁应用失败或最终验收失败时，停止继续提交，保留诊断信息，使用与目标版本匹配的官方微信插件副本恢复；不得对版本不匹配的源码盲目执行反向 patch。重启与恢复入口只使用固定目标版本和仓库补丁清单自动同步；无法确认目标版本、包完整性或锚点时失败关闭。恢复后重新确认 Gateway、微信通道和 Chub 插件状态。

### 7.4 重启与恢复

API action `restart` 和微信 `restart clawbot` 的用户可见语义都是“重启与恢复”，底层仍调用固定的 `openclaw gateway restart`。它的目标是恢复 Gateway 和微信消息通道，不承担任意版本升级或任意补丁操作。

- **执行前**：检查 Gateway 版本、Chub 插件加载状态、微信适配器版本与完整性，以及清单中补丁的目标基线和运行时锚点。
- **同步范围**：Chub 插件只从仓库固定源码构建并安装；微信适配器只同步清单指定版本；补丁文件先校验清单中的 `sha256` 和 `validated` 状态，再在目标版本、完整性和锚点均匹配时按固定补丁文件应用，并同时校验源码和运行产物。
- **最小门禁**：可信入口、OpenClaw 可执行文件、固定服务定义、目标版本可确认以及 OpenClaw 维护操作互斥。Gateway 停止、未知、未配置、消息通道异常或 Agent 任务存在，不阻止进入恢复流程。
- **最终结果**：Gateway 就绪、已配置消息通道恢复运行、插件为 `loaded` 且补丁运行产物一致，才记录完整恢复成功；未配置消息通道时只确认 Gateway 重启成功并明确提示“消息通道未配置”，不把它宣称为 ClawBot 已就绪；同步不完整或最终状态未知时记录失败或降级，不把 Gateway 进程启动当作 ClawBot 可用。

页面按钮显示“重启与恢复”，API action 和底层操作命名继续使用 `restart`，微信固定指令使用 `restart clawbot`。重启会短暂中断消息通道和 Agent 任务；微信实际收发仍由维护者在客户端确认。

Chub 插件升级只从 `integrations/openclaw/chub/` 构建并安装；插件源码、`dist/`、清单和测试必须同版本验证。插件协议变化时按插件 README 的同步清单，同时更新 Chub API、插件、测试、构建产物、部署状态和必要文档。

## 8. 验收范围与复检

自动化验收至少覆盖：插件协议版本和字段拒绝、真实 loopback 来源边界、Owner/私聊校验、普通文字与可信语音的单次 dispatch、重复消息幂等、路由冲突、失败关闭和通知与任务状态分离。

适配器复检至少覆盖：

- 当前包名、版本、实际加载目录和运行产物。
- `manifest.json` 中三个补丁的 ID、版本、目标基线和文件哈希。
- 可信语音来源标记、干净转写和普通文本回退边界。
- Context Token 保存、启动恢复、懒恢复、出站回退和权限 `600`。
- Gateway 健康、微信通道运行状态和补丁后的真实微信收发。

补丁验收必须确认源码差异与构建后的运行产物差异语义一致；仅修改部署目录中的 `dist/` 不算完成，补丁版本也不能代替真实微信收发验收。

已确认范围：当前验收基线中的 Chub 插件协议、身份、路由、幂等、失败关闭、通知终态、Context Token 兼容行为和适配器日志脱敏行为。普通文本原始首尾空格不承诺；其他 OpenClaw/微信插件版本、其他加载目录和未经重新构建验证的运行产物不承诺。

真实微信文字、语音、点击和收件只能由维护者本人完成。Agent 只能检查 Chub/OpenClaw 后台日志、任务状态和通知终态，后台记录不能替代微信客户端验收。

以下变化必须重新复检：OpenClaw 或微信插件版本/包完整性变化、实际加载目录变化、适配器源码或运行产物变化、Hook/指令解析契约变化、Chub 插件协议或能力清单用户契约变化、身份/路由/幂等/通知边界变化。

## 9. 相关文档

- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前微信固定指令和用户可见回复格式的唯一产品契约。
- [Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)：插件源码、构建、安装、部署和协议升级操作手册。
- [Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)：系统边界和状态所有权。
- [Chub Quick Worker 设计](CHUB_QUICK_WORKER_DESIGN.md)：任务执行、恢复、通知终态和重启协调。
