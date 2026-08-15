# Chub–OpenClaw 接入设计

> 状态：持续维护。
>
> 本文只维护 Chub 与 OpenClaw/微信 ClawBot 的端到端业务、身份、权限、Session 路由和通知边界。当前能力名称以[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)为准；插件协议、构建和部署以[Chub OpenClaw 插件说明](../integrations/openclaw/chub/README.md)为准。

## 1. 当前架构

Chub 当前提供四类 OpenClaw 能力：

1. 在首页查看和维护本机 Gateway、微信通道与 Tailscale 入口。
2. 向普通 OpenClaw Agent 提供受限的状态查询和飞书通知 Tool。
3. 在微信 Chub 模式下，将可信私聊交给 Chub 单一调度入口处理。
4. 将异步任务最终结果按任务保存的微信路由原路送回。

```text
普通 Agent
  -> chub_get_status / chub_send_notification
  -> Chub 固定能力

微信 Chub 模式
  微信私聊 -> Chub 插件 before_dispatch
           -> POST /api/openclaw/wechat-chub-mode/dispatch
           -> 固定路由，或 Quick Worker -> Codex
           -> 同步决定由 Hook 原路交付
           -> 异步结果由 Chub 按任务保存路由发送
```

整条微信 Chub 链路不调用 OpenClaw Agent 或 LLM 做路由，也不把 Hook 已触发、HTTP 200、任务已建立或通知已开始当作最终成功。

## 2. 运行状态与管理入口

以下状态相互独立：

| 状态 | 含义 |
| --- | --- |
| Gateway 正常 | 服务、进程、端口和 RPC 可用 |
| Channel 正常 | 微信插件和本地通道进程可用 |
| ClawBot 已绑定 | 微信服务端当前绑定这台 Gateway |
| Owner 已配置 | 指定微信身份具有 Owner 权限 |
| Chub 模式就绪 | 固定配置、Codex、工作区和通知条件满足 |
| 任务或通知成功 | 对应业务已经到达独立终态 |

同一个 ClawBot 同时只能绑定一台 Gateway；本地 Channel 正常不能代替真实微信收发确认。

首页“OpenClaw 环境”卡片只提供状态、受控启停/重启和微信绑定，不暴露配置正文、任意命令或原始日志。操作成功以最终实例和健康状态为准。

首次接入由 OpenClaw 自身完成 Gateway 初始化、腾讯微信插件安装和 ClawBot 登录；Chub 不接管 OpenClaw 安装或升级：

```bash
openclaw onboard --install-daemon
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
openclaw channels login --channel openclaw-weixin
```

常用只读检查：

```bash
openclaw gateway status --json
openclaw gateway probe
openclaw channels status --probe --json
openclaw config validate
```

## 3. 身份与权限

当前部署边界为：同一节点上的 Chub 与 OpenClaw、单一健康 ClawBot、单一微信 Owner。

- 微信统一调度接口只接受本节点真实 Tailnet socket 来源，不接受 Hub Token 或转发 Header 替代。
- 群聊、未知发送者、缺少稳定消息标识或缺少可信回送路由时不进入 Chub 任务。
- 消息正文只能提供业务内容，不能指定内部 Session ID、工作区、权限、模型、路径、命令、接口或收件人。
- 微信 Chub 模式的 `Full access` 只随当前受控 Owner 和绑定 Session 生效，不成为 Session 永久权限，也不适用于其他入口。
- 当前不支持第二个 Owner、多个并行 ClawBot 或跨节点任务提交；需要扩展时必须重新设计身份隔离。

普通 OpenClaw Agent 仍使用自身 Shell 审批和文件边界，不因微信 Chub 例外而获得额外权限。

## 4. 微信统一调度

Chub 插件只负责取得可信通道上下文、调用一次固定接口并执行 Chub 返回的交付决定。协议版本、请求字段和 `pass` / `reply` / `handled` 语义由[插件说明](../integrations/openclaw/chub/README.md)维护。

Chub 按以下顺序处理消息：

1. 校验模式、同节点来源、Owner、私聊、消息幂等标识和回送路由。
2. 匹配能力清单登记的固定路由。
3. 未命中的非空文字或可信语音转写作为普通快速交互任务提交。
4. 返回放行、固定回复或静默处理决定；异步结果不在同步响应中伪装完成。

固定指令原则上要求整条消息匹配。新建和指定槽位切换允许附带任务正文；归档、重启和续提类指令不接受附带正文。未匹配内容进入普通任务，不由插件解释。

### 4.1 状态与槽位

- `chub` 状态查询每次采集现场状态，只读且不修改槽位或通知状态。
- 状态主体只展示可见 Session 和统一 AI 用量；异常集中列出，单一数据源失败不清空其他内容。
- `sync` 及中文别名是显式写操作：稳定扫描后原子补齐槽位，失败不提交部分结果。
- `S1`–`S9` 是 Chub 持久槽位，不等于列表位置；所有列表按创建时间倒序，排序不改变槽位编号。
- 当前绑定使用 `Current` 标识；任务摘要使用有界、单行、脱敏文本，不调用模型生成。

具体指令和中文别名只在[集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md#4-微信-clawbot-指令)维护。

### 4.2 普通任务与 Session

- 普通文字和可信语音任务成功受理后静默处理，失败立即返回明确原因，完成后再原路发送最终结果。
- 默认复用当前绑定 Session；不同 Session 可以并行，同一原生 Session 始终只有一个 writer。
- 当前 Session 忙时拒绝新任务，并短期保存最近一次待续提正文；用户可在当前 Session 续提或新建 Session 后续提。
- 新建、切换、归档和待续提均由 Chub 固定路由完成；插件不感知 Session 业务。
- 标题、槽位、Activity 和单 writer 语义由[AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)维护。
- 页面、微信和翻译任务统一由 Quick Worker 承载；恢复、任务级重启和通知终态由[Worker 设计](QUICK_INTERACTION_WORKER_DESIGN.md)维护。

文本优化与翻译是可选辅助能力：开启后，普通任务镜像到独立的内部只读 Session 和持久化 FIFO；关闭只阻止新翻译，不中断已接受任务。翻译 Session 不进入微信槽位，结果失败不改变主任务终态。

### 4.3 语音可信来源

只有微信通道确认消息类型为语音且存在非空转写时，才能标记 `voice`。任务正文始终使用干净转写；内部来源标记不进入正文、摘要、日志或对外接口。普通文字伪造标记仍按文字处理，空正文且没有可信转写时不提交 Chub。

腾讯微信插件升级后若缺少等价能力，使用仓库参考补丁 `integrations/openclaw/patches/weixin-clawbot-voice-transcript-origin.patch` 恢复，并重新验证普通文字、真实语音、伪造标记、重复消息和模式关闭场景；不得只修改未确认的运行目录。

## 5. 幂等与状态

插件使用可信通道、账号、发送者、时间戳和正文生成非敏感消息标识；缺少稳定时间戳时失败关闭。Chub 在产生副作用前持久化预留：

- 相同标识和路由复用首次决定，不重复执行任务。
- 相同标识携带不同路由时拒绝。
- 首次失败不会因重复投递自动重试。
- 服务重启时无法确认的副作用按失败收敛，不猜测成功或重放。

统一调度、Session 操作、Quick Worker 任务、Codex 终态和微信通知分别记录状态，前一阶段成功不能代替后一阶段。

## 6. 完成通知

微信 Chub 任务保存本次 Hook 提供的账号和发送者，结束后只按该路由发送：

- 主任务成功、失败或超时后都进入独立通知流程；通知失败不改变任务结果。
- 超长结果按段落有界分段，最多发送 5 条；部分送达后停止，不自动重试。
- 路由缺失、账号停止或发送失败时不回退到全局收件人。
- 页面快速交互使用单独配置的全局固定接收人，与微信任务原路路由不混用。
- 任务级 Web 重启只等待请求任务自身结果和通知终态；重启与恢复细节由 Worker 设计维护。

微信出站依赖收件人最近一次入站产生的 Context Token。当前约 10 分钟只是实测口径，不是公开 TTL；出站消息和 Gateway 重启不会续期，失效后需要收件人重新向同一 ClawBot 发送消息。持久化、惰性恢复和升级复检见[Context Token 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)。

## 7. Agent Tool 与飞书通知

普通 OpenClaw Agent 可以使用：

- `chub_get_status`：读取 Chub 基础状态。
- `chub_send_notification`：向 Chub 预配置飞书目标发送有界纯文本。

通知目标、人员和 Webhook 由 Chub 固定配置；调用方不能指定任意 URL、Open ID 或 Secret 路径。用户要求原文发送时必须使用当前运行的可信原文关联，无法确认时拒绝；只有明确要求撰写、总结或改写时才允许生成内容。

微信 Chub 私聊不通过这两个 Tool 完成路由，Chub 直接发送飞书也不经过 OpenClaw。

## 8. 故障判断与验收

| 现象 | 优先检查 |
| --- | --- |
| Gateway 正常但微信无回复 | Channel 探测、发送日志和真实微信收发 |
| 微信只收到统一通道失败 | Chub 可达性、插件协议版本、运行时来源和 Hook |
| 微信消息进入 Agent | 插件路由开关、Chub 模式和 `before_dispatch` |
| 普通任务被拒绝 | 当前 Session、writer、Worker 和健康 ClawBot |
| 完成通知失败 | 任务保存路由、Context Token 和通道状态 |
| 飞书正文被改写 | 内容模式、可信原文 Hook 和当前运行关联 |

`lastOutboundAt` 只能辅助判断，不能作为真实送达凭据。同步 Hook 回执可结合通道发送日志；异步结果需要同时核对 Chub 任务和通知终态，最终仍以维护者实际收到消息为准。

真实微信客户端操作只能由维护者本人完成。自动化验证负责协议、路由、幂等和失败边界；涉及微信通道、路由、通知格式或插件协议的变化，还需在受影响平台完成真实文字和语音回归。

当前 Gateway 管理、微信绑定、单 Owner、统一调度、固定路由、普通任务、原路通知和飞书 Tool 已纳入 macOS、Ubuntu 验收基线。插件平台部署状态和仍需真实回归的协议变化记录在[插件说明](../integrations/openclaw/chub/README.md)。
