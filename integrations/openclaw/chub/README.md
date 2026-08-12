# Chub OpenClaw 插件

> 状态：统一消息调度 v3 已在 macOS、Ubuntu 完成部署和核心链路验收。Codex 状态与绑定切换均由 Chub 在现有统一接口内处理，不改变插件协议或业务逻辑；两项能力已在当前节点完成真实微信验收，Ubuntu 尚未同步。

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

插件只把可信语音来源归一化为干净转写和 `voice` 类型。是否回显识别原文、如何截断以及最终回执文案全部由 Chub 决定；插件不得追加、删减或改写 Chub 返回的消息。

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

响应不返回业务状态码、内部 Session ID、任务 ID、完整历史、账号、收件人或配置。插件只校验和执行统一结果：`pass` 放行，`reply` 将 `message` 原样交付，`handled` 只终止当前消息处理且不产生额外回复；不会再解释业务语义或选择第二个 Chub 接口。协议不匹配、Chub 不可达或响应无效时统一回复“Chub 消息通道暂时不可用，请稍后重试。”

## 5. Chub 业务边界

插件只把一条可信消息交给统一接口，不感知 Chub 最终选择固定路由还是普通任务，也不维护任何业务指令表。当前路由、匹配条件、顺序和验收状态统一见[集成能力清单](../../../docs/CHUB_INTEGRATION_CAPABILITIES.md)；端到端任务、幂等、安全和完成通知语义见[接入设计](../../../docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。

插件只依赖 `pass`、`reply`、`handled` 三种协议决定，因此 Chub 在不改变请求、响应和交付语义时可以独立增删内部路由，无需修改或重新部署插件。消息正文不能提供任意动作、接口、Session ID、任务、权限、模型、路径或命令；固定绑定切换指令中的列表编号只由 Chub 解释。

## 6. Context Token 与长任务结果

Context Token 由微信插件在真实入站消息中取得并持久化。当前按约 10 分钟有效的实测口径维护；出站消息不会刷新或延长有效期。

不得使用出站心跳、Gateway 重启、旧 Token 重载或伪造入站事件尝试续期。最终通知失败仍不改变 Codex 任务终态，也不切换到全局收件人。

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

### 8.1 协议升级同步清单

本文第 4 节是微信统一调度协议版本、请求字段和交付决定的主要说明。以后升级协议时，按以下范围一次性同步：

1. Chub 调度接口的版本常量、请求/响应 Schema，以及插件客户端的版本、类型和响应校验。
2. 插件对新增交付决定的执行逻辑；如插件配置或 Tool 契约变化，同时更新静态清单。
3. Chub API、路由服务和插件测试，至少覆盖新版本成功、旧版本拒绝、无效响应失败关闭和重复消息。
4. 重新构建 `dist/`，完成插件校验、安装、Gateway 重载，并确认插件 `loaded`、ClawBot `running`。
5. 更新本文第 4、9 节和顶部状态；同步更新[接入设计](../../../docs/CHUB_OPENCLAW_INTEGRATION_DESIGN.md)中的当前架构、协议摘要和验收基线，以及项目 README 的产品概述。
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

自动化验证至少覆盖：

- 路由开关关闭时，微信私聊保持原 OpenClaw 流程。
- Chub 业务模式关闭时，统一接口返回放行。
- Chub 不可达、响应非法或缺少可信路由时失败关闭。
- 普通文字和可信语音只调用一次统一调度接口并成功提交任务。
- Chub 返回的提交、重复、失败和语音回显文案均由插件逐字交付，插件不再拼装业务回复。
- 重复消息不重复创建任务。
- Chub 的固定路由和普通任务均能复用现有 `reply` / `handled` 决定，插件不解释具体业务语义。
- 最终结果、超长结果、部分送达、Context Token 失效和重新入站刷新均保持受控。
- `chub_get_status`、`chub_send_notification` 与飞书原文保护没有退化。

验收基线：普通文本、可信语音和 v3 插件已在 macOS、Ubuntu 完成真实验收。Codex 状态路由扩展已在当前节点验收且不需要重新部署插件；Ubuntu 同步 Chub 后，按能力清单检查用量、兼容 Session、`[Current]` 标记和状态显示。协议版本不一致期间按失败关闭处理。

真实验收以微信收到的提交状态和最终结果为准，不能只看 HTTP 200、命令退出码、插件 Hook 已触发或通道状态中的 `lastOutboundAt`。Hook 同步回执可结合微信通道 `text sent OK` 判断，异步结果需结合 Chub 任务和通知终态。
