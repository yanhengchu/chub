# 微信 Chub 模式设计

> 状态：已实现并通过真实微信端到端验收，作为当前单 Owner、单 ClawBot 基线维护。

## 1. 当前能力

微信 Chub 模式将当前 ClawBot 的普通私聊直接提交为 Chub 快速交互。OpenClaw 在模型调度前拦截消息并提交给 Chub；Chub 完成安全校验、幂等预留和 Session 分配后返回受理状态，由 OpenClaw 立即回复提交成功或受控失败原因。受理成功后，Chub 创建或复用固定的微信专用 Codex Session 异步执行任务，保存最终状态，再通过 OpenClaw 向本次消息来源原路发送结果。

```text
微信用户向 ClawBot 下发任务
  -> OpenClaw before_dispatch 拦截并提交 Chub
  -> Chub 校验、幂等预留并分配专用 Session
  -> OpenClaw 根据 Chub 受理结果立即回复任务提交状态
  -> Chub Codex 专用 Session 异步执行任务
  -> Chub 保存任务最终状态和结果
  -> Chub 调用 openclaw message send
  -> 原 ClawBot 向原发送者回送任务结果
```

因此，从用户体验上可以简化为“ClawBot 下发任务 → 收到带任务摘要的提交状态 → 收到带同一摘要的任务最终结果”。其中提交状态只是 Chub 是否受理并成功建立后台任务，不代表任务已经完成；最终结果以 Chub 快速交互终态为准。若任务触发 Chub 延迟重启，服务恢复后还会按原路发送一条带关联任务摘要的独立重启结果。

任务摘要由 Chub 在提交时从原始任务的首个有效句子确定性提取，统一为单行并限制在 48 个字符，不调用 OpenClaw Agent 或 LLM。摘要会过滤内部语音标记、控制字符并遮蔽明显的 Bearer、Token、Authorization、Cookie、Secret、Password 和 Webhook 值；生成后随快速交互任务持久化，提交状态、最终结果、分段结果和重启结果始终复用同一份摘要。历史任务没有摘要时继续使用原有消息结构，不阻塞通知。

当前“提交状态 + 任务摘要 + 最终结果”消息结构及语音识别原文回显已经验收。部署对应 Chub 与插件产物并重启相关服务后生效，不需要迁移历史任务或修改现有配置。

该模式不调用 OpenClaw Agent 或 LLM。OpenClaw 负责入口身份、通道上下文、即时提交状态回复和结果投递；任务理解、执行与最终状态由 Chub 调用 Codex CLI 完成。

模式关闭时，微信私聊保持原有 OpenClaw Agent 流程。模式开启但 Chub 不可达、配置无效或任务提交失败时，消息在 Chub 路径内失败关闭，不回退到 Agent 或 LLM。

## 2. 信任与权限边界

当前部署固定为单一微信 Owner、单一健康 ClawBot、同节点 OpenClaw 与 Chub：

- 状态接口只接受真实 Tailnet socket 来源，不接受 Hub Token 替代。
- 任务提交除 Tailnet 认证外，还要求请求来源等于 Chub 当前节点地址；其他 Tailnet 节点和转发 Header 均无效。
- 微信消息只能提供任务正文。Session、工作区、权限、模型、推理等级、文件路径、命令和回送目标均不能由正文或客户端单独指定。
- 回送账号和发送者来自 `before_dispatch` 的可信通道上下文，并在提交前确认对应账号是唯一健康 ClawBot。
- 当前已批准微信专用 Session 使用 `Full access`。该例外只适用于固定 Tailnet、单一 Owner 和此专用 Session；关闭模式即撤销入口，不得扩展给其他 Agent、身份、入口或 Session。
- 群聊、缺少稳定消息标识、缺少回送路由或存在多个健康 ClawBot 时不提交任务。

如果未来增加第二个 Owner、多个并行 ClawBot 或跨节点提交，必须重新设计身份、Session 隔离和调用方认证，不能沿用当前单用户边界直接放开。

## 3. 配置与启停

Chub 配置 `openclaw.weixin_chub_mode` 决定业务状态和专用 Session 默认值：

```yaml
openclaw:
  weixin_chub_mode:
    enabled: false
    workspace_id: "chub"
    permission_mode: "full-access"
    model: null
    reasoning_effort: null
```

OpenClaw 插件配置 `wechatChubStatusMode` 是部署级路由开关。该名称为兼容已部署配置而保留，不再表示仅查询状态。

| OpenClaw 路由 | Chub 模式 | 微信私聊行为 |
| --- | --- | --- |
| 关闭 | 任意 | 保持 OpenClaw Agent / LLM 流程 |
| 开启 | 关闭 | 保持 OpenClaw Agent / LLM 流程 |
| 开启 | 已启用且就绪 | 提交 Chub 任务，不调用 Agent / LLM |
| 开启 | 已启用但异常 | 返回受控失败，不回退 Agent / LLM |

日常启停只修改 Chub 模式。插件路由开关用于安装、迁移或整体撤销，修改后需要重启 OpenClaw Gateway。

`Ask for approval` 不支持后台微信任务。启用模式前，Chub 会校验工作区、权限、Codex、模型、推理等级和微信完成通知能力。模式启用期间，Session 关键配置被锁定；需要修改时先关闭模式。

## 4. Session 与并发

微信模式长期复用一个 Chub 管理的专用 Session，以保留 Codex 上下文。Session ID 只保存在 Chub 私有状态中，OpenClaw 和微信消息都不能读取或指定。

- 没有有效 Session 时按固定配置创建一个新 Session；提交确认统一保持简短，不展示 Session 创建或内部恢复细节。
- 配置中的模型或推理等级为 `null` 时，只在首次创建时跟随 Codex 默认值；已有 Session 继续使用其实际配置。
- 同一原生 Codex Session 同时只允许一个 writer，不维护额外消息队列。
- 已有快速交互、明确执行中或 writer 仍被占用时拒绝新任务，不中断现有任务。
- 专用 Session 状态为 `unknown` 时，Chub 可以撤销页面票据、停止残留终端，并在确认 writer 释放后提交；失败时保持关闭，不并行 `resume`。
- Codex writer 探测依赖 `CODEX_HOME/thread-writer-locks/` 的只读兼容边界，Codex CLI 升级后需要回归此行为。

快速交互要求重启 Chub 时沿用延迟重启机制：先保存结果并完成本次微信通知，再等待其他快速交互结束。多个任务提出的重启合并为一次节点操作，但各任务保留独立关联。新实例通过本机健康接口确认实例 ID 已变化后，页面时间线追加 Chub 系统回复；微信来源任务还使用本次保存的账号和发送者原路发送第二条重启结果，页面来源不发送。重启结果通知采用独立的至多一次状态，发送中再次中断时标记失败、不自动重试，也不回退全局收件人。

## 5. 幂等与状态

当前 OpenClaw Hook 不提供微信平台原始消息 ID。插件使用固定通道上下文、账号、会话、发送者、消息时间戳和原始正文生成 SHA-256 消息标识；缺少稳定时间戳时拒绝提交。

Chub 在创建任务前持久化预留记录：

- 相同消息标识和相同路由只重放首次结果，不重复执行 Codex。
- 相同消息标识携带不同路由时返回冲突。
- 首次失败会被记录，重复消息只重放同一失败，不自动重试。
- 服务重启时未完成的预留记录转为固定失败，避免不确定状态下重复执行。

幂等历史最多保留 5000 条且不超过 8 MiB，只保存回送路由摘要，不保存任务正文或原始账号。完整任务和任务级路由保存在权限为 `600` 的快速交互私有状态中；接口、页面和操作日志不返回原始路由。

任务提交和 Session 回收均记录 `requested`、`started`、`succeeded` 或 `failed`。任务已提交只表示后台执行已经建立，最终成功、失败、超时和通知状态仍分别以快速交互最终状态为准。

## 6. 回送规则

页面来源和微信来源使用两类互不兜底的路由：

| 来源 | 账号 | 收件人 |
| --- | --- | --- |
| Chub 页面快速交互 | 默认选择唯一健康 ClawBot，可由兼容配置覆盖 | 全局固定 `weixin_recipient` |
| 微信 Chub 任务 | 本次 Hook 提供的 `accountId` | 本次私聊发送者 |

文字任务提交成功时回复“任务已提交”、任务摘要和“完成后将原路发送结果”。语音任务首次提交成功时使用相同结构，并在末尾追加微信提供的“语音识别内容”，便于维护者核对转写准确性；整条提交回执最多 3000 个字符，超出时只截断识别内容并明确提示。重复消息和提交失败保持简短，不显示任务摘要，也不追加识别内容。

任务成功、失败或超时后，最终通知使用对应状态标题、提交时保存的同一任务摘要和结果或错误说明。普通结果完整单条发送，超长结果编号分段且最多 5 条，每段都重复任务摘要并将其计入单条长度上限；整批共用一个超时，中途失败记录部分送达并停止。Chub 使用任务保存的账号和发送者调用 `openclaw message send`；路由失效、账号停止或 Context Token 不可用时只将通知标记为失败，不改变 Codex 任务结果、不自动重试，也不改投全局收件人。

Context Token 由微信插件按账号和发送者持久化，Chub 不读取或保存 Token。兼容补丁及升级恢复规则见[微信 ClawBot Context Token 持久化 AI 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。微信插件当前还需保留可信语音来源标记，规则及升级恢复方式见[微信 ClawBot 语音转写来源标记补丁规范](WEIXIN_CLAWBOT_VOICE_TRANSCRIPT_ORIGIN_PATCH.md)。

## 7. 接口与源码边界

固定接口为：

```text
GET  /api/openclaw/wechat-chub-mode/status
POST /api/openclaw/wechat-chub-mode/submit
```

状态响应只包含 `enabled`、`ready` 和固定状态码。提交请求只接受有界的消息标识、任务正文、非敏感关联标识，以及 Hook 提供的账号和发送者；首次提交成功响应额外返回由 Chub 生成的有界脱敏任务摘要，供即时回执使用，但不包含 Session ID、任务 ID、完整正文、账号、收件人或配置。重复提交不重复返回摘要。

`integrations/openclaw/chub/` 是 Chub 插件的唯一源码。OpenClaw 实际加载目录只是构建产物；插件变更必须先同步维护仓库源码、静态清单、测试和说明，构建校验通过后再部署并重载 Gateway。第三方微信插件的 Context Token 与语音来源标记补丁单独维护，不合并进 Chub 插件。

## 8. 当前结论

微信 Chub 模式已完成插件加载、真实私聊提交、专用 Session 创建与复用、忙时拒绝、最终结果原路回送和重新绑定后的链路验收；快速交互触发延迟重启时，任务完成结果与新实例恢复后的独立重启结果也已完成真实微信原路回送验收。当前实现作为单 Owner、单 ClawBot、同节点部署的长期入口持续维护。

## 9. 低优先级待评估项

记录“多个 Session 并行处理微信 Chub 任务”的体验优化需求。当前单专用 Session 在长任务执行期间会拒绝新任务，存在等待效率问题，但现阶段使用频率和收益不足以支持立即扩展，继续保持现有单 Session 基线。

后续只有在并发需求稳定出现时再重新评估。优先方向是保留一个承载连续上下文的长期主 Session，并增加一个明确用于独立任务的受控工作槽位；不直接采用多个 `Full access` Session 自动轮询。设计时必须同时解决上下文归属、共享工作区写冲突、任务与微信结果关联、并发上限、幂等、停止与重启协调，以及多 Session 权限例外边界。是否增加队列、只读工作 Session 或隔离工作区，留待真实使用数据支持后决定。
