# Chub OpenClaw 插件

> 状态：统一消息调度 v3 已部署，普通文字和可信语音已完成 macOS、Ubuntu 真实验收；插件协议边界已完成自动化验证。Chub 内部固定指令和 Requests 的业务验收状态由接入设计维护，未改变 v3 协议时不要求重建插件。

Chub 仓库内的插件、API 和消息路由索引统一见[Chub 集成能力清单](../../../docs/CHUB_INTEGRATION_CAPABILITIES.md)；本文只维护 `chub` 插件自身的协议、源码、部署和验收规则。

## 1. 插件定位

Chub 插件通过固定 Tailnet 地址连接 Chub，承担 OpenClaw 与 Chub 之间的通道适配。微信 Chub 模式下，插件只负责拦截符合条件的 ClawBot 私聊、提取可信通道信息、调用一个固定的 Chub 消息调度接口，并把 Chub 的决定原路交付给微信。

微信消息的业务含义统一由 Chub 判断。插件不识别业务指令，不选择业务接口，不调用 OpenClaw Agent 或 LLM，也不允许消息正文指定 URL、动作、Session、任务编号、命令或文件路径。

插件同时为 OpenClaw Agent 提供独立的 `chub_get_status`、`chub_send_notification` 和飞书通知原文保护白名单能力；这些能力不并入微信消息调度，微信 Chub 私聊不会调用它们完成任务路由。

## 2. 当前链路

```text
微信用户向 ClawBot 发送私聊
  -> 微信插件接收入站消息并保存最新 Context Token
  -> Chub 插件 before_dispatch 校验通道与可信上下文
  -> POST /api/openclaw/wechat-chub-mode/dispatch
  -> Chub 独立完成模式检查和业务路由
     -> 模式关闭：放行原 OpenClaw 流程
     -> 直接回复或提交后台任务
  -> Chub 返回统一调度结果
  -> Chub 插件按结果放行或直接回复微信
  -> 任务结束后由 Chub 使用保存的原路路由发送最终结果
```

插件部署开关只决定当前 OpenClaw 是否启用这条路由。开关启用后，符合条件的微信私聊都进入统一调度接口；Chub 自身配置决定业务模式是否开启。每条消息只调用该统一接口。

## 3. 插件职责

插件必须保留以下通道边界：

- 只处理路由开关已启用的 `openclaw-weixin` 私聊；群聊、其他通道和开关关闭时不拦截。
- 从 Hook 上下文取得可信 `accountId` 和发送者；正文不能覆盖账号或回送目标。
- 使用通道、账号、发送者、时间戳和原始正文生成非敏感幂等消息标识；缺少稳定时间戳时失败关闭。
- 对可信微信语音来源提取干净转写；普通文本不能通过伪造标记声明自己是语音。
- 入站事件没有可处理文字且未取得可信语音转写时，明确提示重新发送文字或稍后重试语音，不再误报为 Chub 消息通道不可用。
- 只访问配置中的固定 Chub Tailnet `baseUrl` 和固定调度路径。
- 对请求字段、响应结构、字节数、单条回复长度、重定向和超时进行严格限制。
- Chub 不可达、响应非法或协议不匹配时返回统一通道失败，不回退 Agent 或 LLM。
- 不记录 Token、Authorization、完整收件人、完整 Session Key 或消息正文。

插件不承担以下业务职责：

- 不维护“指令 → Chub 接口”映射。
- 不检查 Codex、工作区、模型、当前绑定 Session 或任务状态。
- 不判断任务应查询、提交、排队、拒绝或补发。
- 不生成任务摘要、任务状态文案或最终结果语义。
- 不把模型回复、Tool Call 创建或 HTTP 请求开始视为任务成功。

插件只把可信语音来源归一化为干净转写和 `voice` 类型。文字和语音任务的即时回执、Session/Task 快照及失败上下文全部由 Chub 按[能力清单第 4 节](../../../docs/CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)生成；插件不得追加、删减或改写，也不得折叠 Chub 为跨微信客户端兼容而生成的段落换行。插件等待 Chub 超时时只能说明当前提交状态未知并提示不要重复发送，不能据此宣称任务提交失败，也不在插件侧生成 Session 或摘要。

## 4. 唯一调度接口

微信消息只调用：

```text
POST /api/openclaw/wechat-chub-mode/dispatch
```

请求只包含：

- 稳定、有界的消息幂等标识。
- 原始文字正文，或由可信微信语音事件取得的干净转写，并携带对应的 `text` / `voice` 类型。
- 非敏感关联标识。
- Hook 提供的账号和发送者回送路由。

请求不包含动作名称、目标接口、Session ID、任务 ID、权限模式、模型、文件路径或命令。客户端不能用字段绕过 Chub 的固定路由。

请求固定携带 `protocol_version: 3`，正文使用 `content`，来源类型使用 `message_type`。Chub 返回统一调度结果，只表达：

- `protocol_version`：必须与插件支持的版本完全一致。
- `disposition`：固定为 `pass`、`reply` 或 `handled`，分别表示放行原流程、由插件直接回复，或 Chub 已完成同步路由且插件无需额外回复；`handled` 不代表后台通知已经送达。
- 有界 `message`：需要 OpenClaw 原路交付的最终文案；放行或已处理时为空。

响应不返回业务状态码、内部 Session ID、任务 ID、完整历史、账号、收件人或配置。插件只校验和执行统一结果：`pass` 放行，`reply` 将 `message` 原样交付，`handled` 只终止当前消息处理且不产生额外回复；不会再解释业务语义或选择第二个 Chub 接口。协议不匹配、Chub 不可达或响应无效时统一回复“Chub 消息通道暂时不可用，请稍后重试。”；请求超时时回复“Chub 响应超时，当前提交状态未知，请勿重复发送。”；正文为空且没有可信语音转写时，回复“未识别到可处理的文字或语音转写，请重新发送文字，或稍后重试语音。”，且不调用 Chub。

## 5. Chub 业务边界

插件只把一条可信消息交给统一接口，不感知 Chub 最终选择固定路由还是普通任务，也不维护任何业务指令表。当前路由能力、指令语法、用户可见行为和回复格式见[集成能力清单第 4 节](../../../docs/CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)；端到端任务、幂等、安全和完成通知边界见[接入设计](../../../docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。

插件只依赖 `pass`、`reply`、`handled` 三种协议决定，因此 Chub 在不改变请求、响应和交付语义时可以独立增删内部路由，无需修改或重新部署插件。消息正文不能提供任意动作、接口、Session ID、任务、权限、模型、路径或命令；固定指令中的持久槽位参数只由 Chub 解释，不等同于 Session 列表位置。

## 6. 业务依赖边界

Context Token 由腾讯微信插件维护，升级后的持久化与恢复要求见[Context Token 补丁规范](../../../docs/WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。Chub 任务、Session、最终通知和重启结果属于 Chub 业务，见[接入设计](../../../docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。

Chub 插件只传递可信入站路由和交付同步决定，不保存 Token 正文，不跟踪异步任务终态，也不把通知失败改写为任务失败。

## 7. 模式与失败语义

插件配置使用 `weixinChubMode` 微信调度开关。开关语义为：

- 关闭：插件不拦截，消息保持原 OpenClaw 流程。
- 开启：所有符合条件的微信私聊调用统一调度接口。

业务模式关闭由 Chub 返回放行。插件开关开启但 Chub 不可达时必须失败关闭，因为插件无法安全判断 Chub 当前是否允许回到 Agent。

以下结果相互独立：

- 消息已被插件拦截。
- Chub 已返回调度决定。
- 快速交互任务已建立。
- Codex 任务已成功、失败或超时。
- 微信最终结果已实际送达。

任何阶段都不得用前一阶段成功代替后一阶段的最终状态。

## 8. 源码、构建与部署

本目录是 Chub 插件的唯一源码和发布来源。OpenClaw 不直接从本目录运行插件；安装命令会将构建产物复制到当前用户的扩展目录，通常为 `~/.openclaw/extensions/chub/`。实际运行位置以 `openclaw plugins inspect chub --runtime --json` 的 `rootDir` 和 `source` 为准。部分 OpenClaw 版本会同时记录 `install.sourcePath`，该字段存在时应指向本目录；缺失时结合运行路径、加载状态、配置和部署产物版本校验，不单独判定安装失败。

扩展目录只是部署副本，不得直接修改，也不得从部署副本反向维护源码。`dist/` 是生成物，不作为人工维护的源码记录。

```bash
npm ci
npm run plugin:build
npm run plugin:validate
npm test
```

实现完成后同步更新源码、静态清单、测试、本文和 Chub 设计文档，再从本目录执行：

```bash
openclaw plugins install "$PWD" --force
openclaw gateway restart
openclaw plugins inspect chub --runtime --json
openclaw channels status --probe --json
```

验收时确认插件状态为 `loaded`、运行时来源位于 OpenClaw 扩展目录、部署产物与仓库构建版本一致，并确认微信账号恢复 `running`；存在仓库来源记录时还需确认其指向本目录。不兼容协议变更必须与 Chub 配套切换；版本不一致期间只允许统一失败关闭，不能回退 Agent。

### 8.1 首次部署配置

安装插件后，先把 `baseUrl` 配置为运行 Chub 的同节点 Tailnet 地址。示例中的地址和端口必须替换为本机实际值，不能写入 Hub Token：

```bash
openclaw config set plugins.entries.chub.enabled true --strict-json
openclaw config set \
  plugins.entries.chub.config.baseUrl \
  '"http://<CHUB_TAILSCALE_IP>:<PORT>"' \
  --strict-json
openclaw config set \
  plugins.entries.chub.config.weixinChubMode true \
  --strict-json
```

`baseUrl` 同时供两个 Agent Tool 和微信调度使用。`weixinChubMode` 只是插件侧转发开关；
微信 Chub 模式还要求 `config/settings.local.yaml` 中的
`openclaw.weixin_chub_mode.enabled` 为 `true`。修改 Chub 配置后使用 `chub restart`
使 Web 配置生效；修改插件配置后使用 `openclaw gateway restart`。

首次接入还必须确认唯一 Owner：首次获批准的私聊配对在 Owner 为空时可以自动建立 Owner；
已有部署运行 `openclaw doctor`，若报告 Owner 缺失，应使用它根据当前通道生成的现场命令修复，
不要把真实发送者标识写入本文、示例、日志或测试。绑定成功、Channel 正常和 Owner 已配置是
相互独立的状态。

配置完成后执行：

```bash
openclaw config validate
openclaw plugins inspect chub --runtime --json
openclaw gateway probe
openclaw channels status --probe --json
```

确认插件为 `loaded`、Chub 固定地址可达、目标微信账号为 `running`，并在 Chub 首页确认
Owner 和微信 Chub 模式就绪。只启用 Agent Tool 时可保持 `weixinChubMode` 和 Chub 业务开关关闭。

### 8.2 协议升级同步清单

本文第 4 节是微信统一调度协议版本、请求字段和交付决定的主要说明。以后升级协议时，按以下范围一次性同步：

1. Chub 调度接口的版本常量、请求/响应 Schema，以及插件客户端的版本、类型和响应校验。
2. 插件对新增交付决定的执行逻辑；如插件配置或 Tool 契约变化，同时更新静态清单。
3. Chub API、路由服务和插件测试，至少覆盖新版本成功、旧版本拒绝、无效响应失败关闭和重复消息。
4. 重新构建 `dist/`，完成插件校验、安装、Gateway 重载，并确认插件 `loaded`、ClawBot `running`。
5. 更新本文第 4、9 节和顶部状态；仅在端到端业务边界或用户可见产品能力同时变化时，才同步更新[接入设计](../../../docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)或项目 README。
6. 只有插件、API 或路由能力发生增删时，才更新[能力清单](../../../docs/CHUB_INTEGRATION_CAPABILITIES.md)；能力清单不重复维护协议实现细节。

macOS、Ubuntu 分别记录实际部署与验收结果。某平台尚未部署新版本时，应明确写“待同步部署”，不能把仓库实现、自动化验证或另一平台验收等同于该平台已生效。

飞书通知原文保护仍需显式启用会话 Hook 访问：

```bash
openclaw config set \
  plugins.entries.chub.hooks.allowConversationAccess true \
  --strict-json
```

插件配置不得包含 Hub Token。

## 9. 最小验收

自动化验证只覆盖插件边界：

- 路由开关关闭时保持原 OpenClaw 流程，Chub 业务模式关闭时执行 `pass`。
- 普通文字和可信语音各只调用一次统一接口；空正文且无可信转写时不调用 Chub。
- Chub 返回 `reply` 时原样交付有界文案，返回 `handled` 时静默结束同步链路；请求超时时只报告提交状态未知并阻止用户盲目重发。具体业务路由、异步通知和可见格式由 Chub 业务测试覆盖，不在插件验收中复制。
- `pass`、`reply`、`handled` 原样执行，插件不解释业务指令或改写 Chub 文案。
- Chub 不可达、协议不匹配、响应非法或可信路由缺失时失败关闭，不回退 Agent。
- 重复投递不产生第二次业务副作用。
- `chub_get_status`、`chub_send_notification` 与飞书原文保护没有退化。

普通文字、可信语音和 v3 插件已在 macOS、Ubuntu 完成真实验收。Session 指令、状态格式、Quick Worker、翻译和任务级重启是 Chub 内部业务；它们不改变插件协议时无需重建插件，具体回归状态由能力清单和接入设计维护。

真实验收以微信收到的提交状态和最终结果为准，不能只看 HTTP 200、命令退出码、插件 Hook 已触发或通道状态中的 `lastOutboundAt`。Hook 同步回执可结合微信通道 `text sent OK` 判断，异步结果需结合 Chub 任务和通知终态。

真实微信客户端操作必须由维护者本人完成，包括发送文字、发送语音、点击和查看收件结果。编码 Agent 只提供验收步骤并检查 Chub/OpenClaw 后台日志、任务状态和通知终态，不得通过 Computer Use、AppleScript、辅助功能、坐标点击或其他自动化方式读取、操作或控制本机微信。只有维护者确认实际收发结果且后台链路状态一致时，才可将对应真实微信验收标记为通过。
