# OpenClaw 与消息通道接入设计

> 状态：核心功能已在 macOS、Ubuntu 和真实微信完成验收，进入持续维护。本文只保留当前架构、运维入口和安全边界；微信专用任务见[微信 Chub 模式设计](WEIXIN_CHUB_MODE_DESIGN.md)。

## 1. 当前架构

Chub 与 OpenClaw 当前提供四类能力：

1. Chub 管理本机 OpenClaw Gateway；首页展示 Gateway、消息通道和访问入口，Owner 检查结果合并到通道与总体状态中。
2. OpenClaw Agent 通过受限 Tool 查询 Chub 状态或发送飞书通知。
3. 微信 Chub 模式在模型调度前将私聊提交到 Chub 专用 Codex Session。
4. Chub 快速交互完成后，通过本机 ClawBot 发送微信结果摘要。

```text
普通 OpenClaw 路径
  微信 / TUI -> OpenClaw Agent -> chub_get_status | chub_send_notification -> Chub

微信 Chub 模式
  微信私聊 -> before_dispatch -> Chub 专用快速交互 -> Codex CLI
           -> openclaw message send -> 原 ClawBot / 原发送者

页面快速交互通知
  Chub 快速交互 -> openclaw message send -> 全局固定微信收件人
```

微信 Chub 模式和页面完成通知只使用 `openclaw message send` 投递结果，不调用 `openclaw agent`，也不得借此触发新的设备操作。Chub 直接发送飞书则调用自身 Notification Service，不经过 OpenClaw。

以下状态必须独立判断：

| 状态 | 含义 |
| --- | --- |
| Gateway 正常 | 后台服务、进程、端口和 RPC 正常 |
| Channel 正常 | 微信插件和本地通道进程正常 |
| ClawBot 已绑定 | 微信服务端当前仍绑定这台 Gateway |
| Owner 已配置 | 指定微信身份具有 Owner 权限 |
| Chub 模式就绪 | 固定配置、Codex、通知和同节点路由可用 |
| 任务或 Tool 成功 | 已取得目标能力的最终结果 |

同一个 ClawBot 同时只能绑定一台 Gateway。在另一台设备重新扫码后，旧设备可能仍保留本地 Channel 和 Owner 信息，最终状态以真实微信收发为准。

## 2. 安装与状态

macOS 使用 launchd，Ubuntu 使用 systemd user service。OpenClaw 可通过官方安装脚本或已有 Node.js 环境安装；初始化后由 OpenClaw 自身管理 Gateway 服务：

```bash
openclaw --version
openclaw doctor
openclaw onboard --install-daemon
openclaw gateway status --json
openclaw gateway probe
```

安装并启用微信插件：

```bash
openclaw plugins install "@tencent-weixin/openclaw-weixin"
openclaw config set plugins.entries.openclaw-weixin.enabled true
openclaw gateway restart
openclaw channels login --channel openclaw-weixin
```

最后一条命令生成绑定二维码；Chub 首页“OpenClaw 环境”卡片也使用同一固定命令提供二维码和验证码流程。绑定只建立消息通道，不自动完成发送者配对或 Owner 授权。

常用只读检查：

```bash
openclaw gateway status --json
openclaw gateway probe
openclaw channels status --probe --json
openclaw config get commands.ownerAllowFrom
openclaw exec-policy show
openclaw config validate
```

Chub 首页卡片只提供 Gateway、消息通道、Tailscale 访问入口、固定启停、重启和微信绑定，不单独展示 Owner 身份或数量，也不提供安装、升级、任意命令、配置正文或原始日志。Owner 未配置或检查失败会进入通道提示和总体“功能受限”状态。卡片刷新失败时保留最近成功内容；卡内操作以最终状态结束，不把子进程创建视为成功。

## 3. 身份与权限

当前采用可信单用户策略：一个微信通道账号、一个允许发送者和一个 Owner。扫码、发送者配对与 Owner 权限是三个独立步骤，不得批准未知请求。

普通 OpenClaw Agent 路径使用 Gateway 的 Shell 审批和文件边界：

- 当前电脑使用 `host=gateway`；只有明确指定已配对 Node 时才使用 Node。
- Shell 默认按白名单执行，未命中时审批，审批不可用或超时则拒绝。
- 不将 `bash`、`sh`、`zsh`、`python`、`node`、`osascript` 等通用解释器整体加入持久白名单。
- 无关任务不得读取凭证、密钥、密码库、系统钥匙串、浏览器登录数据或其他敏感路径。
- 对敏感数据的修改、移动、删除、轮换或外发必须先说明准确目标并取得明确确认。

这些是模型和应用约束，不是操作系统级隔离。如果允许其他微信身份或不可信输入访问，必须重新评估 Sandbox、独立 Agent 和文件工具权限。

微信 Chub 模式是单独批准的例外：固定 Tailnet 内的单一 Owner 可以通过固定专用 Session 使用 `Full access`，不逐条审批。该权限不属于 OpenClaw Agent，也不扩展到其他账号、入口或 Session；关闭微信 Chub 模式即可撤销入口。

## 4. Chub OpenClaw 插件

插件源码位于 `integrations/openclaw/chub/`，统一承载以下能力：

| 能力 | 用途 |
| --- | --- |
| `chub_get_status` | 查询当前设备的 Chub 健康和基础状态 |
| `chub_send_notification` | 向预先配置的飞书目标发送消息 |
| `before_dispatch` Hook | 在微信 Chub 模式下拦截私聊并提交固定任务 |
| 原文保护 Hook | 在明确要求原样发送时覆盖模型生成的通知正文 |

插件只使用固定 Tailnet `baseUrl`、固定 API 路径、严格 Schema、受控超时和有界响应，不配置 Hub Token，也不接受任意 URL、文件路径或命令。

仓库目录是插件的唯一源码，OpenClaw 实际加载目录只是部署产物。修改后必须先执行：

```bash
cd integrations/openclaw/chub
npm ci
npm run plugin:build
npm run plugin:validate
npm test
```

然后将构建产物和 `openclaw.plugin.json` 一起安装或覆盖到 `openclaw plugins inspect chub --json` 确认的实际插件目录，重启 Gateway，并用 `openclaw plugins inspect chub --runtime --json` 确认运行时已加载。不得直接修改运行目录后遗漏仓库源码、清单、测试或说明。

`wechatChubStatusMode` 是为兼容已有部署保留的微信路由开关。开启后由 Chub 状态决定消息进入专用任务还是保持普通 Agent 流程；完整规则见[微信 Chub 模式设计](WEIXIN_CHUB_MODE_DESIGN.md)。

## 5. 微信完成通知

快速交互完成通知只在任务成功、失败或超时后发送有界摘要，通知状态独立记录为发送中、已发送、失败或跳过。通知故障不改变任务最终状态。

两类路由严格隔离：

- 页面来源使用 `openclaw.quick_interaction_completion.weixin_recipient`；账号默认选择唯一健康 ClawBot，`weixin_account_id` 只作为兼容性覆盖。
- 微信 Chub 任务保存本次 Hook 提供的账号和发送者，完成时只按该路由回送。

微信任务的路由缺失、账号停止或投递失败时不切换到全局目标。收件人需要先主动向对应 ClawBot 发送消息，使微信插件获得该账号与收件人的 Context Token。

当前微信插件需要持久化 Context Token，并在通道启动实例与出站模块实例隔离时支持磁盘惰性恢复。日常 Gateway 重启和 ClawBot 重新绑定不要求重复打补丁；插件升级、重装、安装目录重建或兼容性检查失败时，按[微信 ClawBot Context Token 持久化 AI 补丁规范](WEIXIN_CLAWBOT_CONTEXT_TOKEN_AI_PATCH.md)重新识别能力并决定是否恢复。Chub 首页不得自动修改第三方插件、重启 Gateway 或发送探测消息。

## 6. 飞书通知

通知目标注册表和 Secret 保存在本机用户配置目录，权限分别为 `700` 和 `600`，不得提交到 Git。Chub 提供固定 CLI 和受保护 API：

```bash
chub notification validate
chub notification list
chub notification test --target <target>
chub notification send --target <target> --message <text>
```

- Codex PTY 和 Chub 快速交互直接使用 Chub CLI 或通知 API。
- OpenClaw TUI 和普通微信 Agent 使用 `chub_send_notification`。
- 默认不提醒任何人；指定人员只能使用目标预配置的别名，`@所有人` 必须由用户明确要求且目标允许。
- 用户明确提供“消息内容”时使用 `verbatim`，插件按当前 `runId` 关联进入模型前的原文；无法取得可信原文时拒绝发送。
- 只有用户要求 AI 撰写、总结或改写时才使用 `generated`。
- 飞书返回接受只表示 Webhook 已接受请求，不表示群成员已读。
- 日志不记录正文、Webhook、Token、Authorization 或完整 Open ID。

## 7. 运维与故障判断

HTTP 200、进程创建、Tool Call 发起、微信进度提示或本地 Channel 正常都不能单独证明操作成功。Gateway 操作检查最终实例和健康状态；微信绑定检查真实收发；Chub 任务、通知和飞书发送分别使用各自最终状态。

| 现象 | 优先检查 |
| --- | --- |
| Gateway 正常但微信无回复 | `channels status --probe`，再做真实微信收发 |
| 旧设备仍显示微信信息 | ClawBot 是否已在另一台设备重新绑定 |
| 微信 Chub 消息进入 Agent | 插件路由开关、Chub 模式状态和运行时 Hook |
| 微信 Chub 任务被拒绝 | 专用 Session 是否执行中、writer 是否占用、路由账号是否唯一健康 |
| 完成通知失败 | 任务保存的账号与发送者、Context Token 和当前通道状态 |
| 飞书正文被改写 | `content_mode`、原文 Hook 和当前 `runId` 关联 |

涉及插件、微信通道、权限或通知行为的变更，至少执行相应静态测试和配置校验，并在受影响平台做一次真实最终结果检查。当前 macOS、Ubuntu 的 Gateway、微信绑定、Owner、状态 Tool、飞书通知和微信 Chub 模式均已完成核心链路验收。

## 8. 后续边界

指定人员飞书提醒、多 Owner、多 ClawBot、跨节点微信提交、连续电脑交互、自动事件通知和更多 Chub Tool 均由真实需求单独设计。不得因当前 Owner、Tailnet 或微信 Chub `Full access` 例外自动扩大其他入口权限。
