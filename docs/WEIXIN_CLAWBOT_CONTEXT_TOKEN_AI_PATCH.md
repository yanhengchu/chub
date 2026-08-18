# 微信 ClawBot Context Token 持久化 AI 补丁规范

> 主要读者：负责维护 Chub 与本机 OpenClaw 的 AI Agent。
>
> 当前状态：macOS、Ubuntu 均已应用并完成真实出站验收。本文遵循[Chub 总体架构](CHUB_ARCHITECTURE_DESIGN.md)及[Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)，是插件升级、重装或兼容性异常时的复检与恢复规范，不是日常重启或重新绑定 ClawBot 的固定步骤。

`@tencent-weixin/openclaw-weixin` 升级、重装或安装目录重建后，必须按本文重新检测；不得仅因旧缓存目录仍保留补丁而判断当前加载插件兼容。若上游已经原生满足本文不变量，应保留上游实现，不重复应用本地补丁。

本文只维护腾讯微信插件内部的 Context Token 持久化、恢复和通用出站回退；Chub 的当前统一调度规则维护在[Chub–OpenClaw 接入设计](CHUB_OPENCLAW_INTEGRATION_DESIGN.md)。Chub 和 Chub 插件不读取、保存或刷新 Token 正文。

## 1. 当前兼容契约

保证链路 `Chub 快速交互 → openclaw message send → 微信 ClawBot → 指定微信收件人` 在 Gateway 重启后仍能恢复最近一次有效 context token，并满足以下不变量：

1. 入站消息按 `accountId + userId` 持久化最新 context token。
2. Gateway 启动微信账号时恢复该账号的持久化 token。
3. 出站发送未显式携带 token 时，按调用方明确给出的账号和收件人查询 token。
4. 出站模块的内存 Map 未命中时，从磁盘惰性恢复一次后重新查询。
5. token 持久化文件每次写入后权限为 `600`。
6. 找不到或无法使用 token 时明确失败，不尝试其他账号或收件人，不调用 `openclaw agent`。

满足上述行为即可视为等价实现，不要求上游代码与参考 patch 逐字相同。日常 Gateway 重启应直接依赖已经验收的持久化和惰性恢复能力；只有能力缺失或安装产物变化时才进入后续恢复流程。

当前持久化格式只保存每个账号和用户的最新 Token 字符串，不包含签发时间、到期时间或刷新凭证。不得为本地记录补造 TTL、根据文件修改时间宣称 Token 仍有效，或由 Chub 根据约 10 分钟口径自行删除 Token；有效性最终由微信服务端在真实发送时判断。

## 2. 必须理解的根因

微信通用出站发送需要使用收件人最近一次入站消息产生的 context token。仅使用进程内 Map 会导致：

- Gateway 重启后 token 丢失。
- OpenClaw 可能为通道启动和通用出站分别加载相互隔离的插件模块实例。启动实例恢复了 Map，不代表发送实例拥有相同 Map。

因此，“入站落盘 + 启动恢复”仍不充分。`getContextToken(accountId, userId)` 必须在当前模块实例内存未命中时执行磁盘惰性恢复并重新查询。

错误语义必须区分：

| 错误 | 优先判断 |
|---|---|
| `weixin_context_missing` | 当前出站实例没有取得该账号与收件人的 token；检查持久化、惰性恢复和目标映射 |
| `sendMessage ret=-2 errmsg=prepare failed` | 已取得 token，但微信侧通常不再接受；让对应收件人重新发一条消息刷新 token 后再验收 |

文件存在不代表 token 有效，命令开始执行也不代表微信已经收到消息。

当前维护口径将 context token 视为约 10 分钟有效。该时长来自本地真实链路测试，不是 OpenClaw 或腾讯微信插件公开承诺的固定 TTL，也不用于精确计时：一次测试中，token 落盘约 10 分 43 秒后仍可发送，约 11 分 46 秒后返回 `sendMessage ret=-2 errmsg=prepare failed`。每分钟出站心跳没有延长有效期，只有对应用户的新微信入站消息会刷新并持久化最新 token。

因此，距离对应用户最近一次入站消息超过约 10 分钟后，主动通知就应视为可能失败。恢复方式是让该用户重新向同一 ClawBot 发送一条消息；重启 Chub 或 Gateway、重新读取旧文件以及盲目重试都不能刷新微信服务端状态。

## 3. 触发条件与恢复前检查

只有以下情况需要执行本节及后续恢复流程：微信插件升级或重装、实际加载目录重建、运行时代码来源变化，或持续出现 `weixin_context_missing` 且账号、目标映射和最近入站均已确认正确。普通 Gateway 重启、Chub 重启和 ClawBot 重新扫码不构成重复打补丁的理由。

重新扫码可能产生新的账号映射并清理旧账号 Token，但这属于业务状态更新，不代表补丁丢失。重新绑定后必须让目标收件人向新的对应 ClawBot 发送一条消息，再验证出站；不能期待旧账号文件中的 Token 自动迁移。出现 `weixin_context_missing` 时也先确认本次账号、收件人和最近入站，再检查持久化文件元数据、惰性恢复与实际加载源码，不能直接重新应用补丁。

AI Agent 按以下顺序执行，不得直接修改第一个搜索到的缓存目录：

1. 读取本项目 `AGENTS.md` 和本文，确认当前约束未变化。
2. 使用 `openclaw plugins inspect openclaw-weixin --json` 确定实际加载插件的来源、版本和入口。
3. 从实际入口解析插件根目录，确认将修改的是当前加载实例。
4. 检查工作区和插件文件现状，保留无关修改；不得覆盖用户配置。
5. 检查上游代码是否已实现第 1 节全部不变量。
6. 只有缺少必要行为时才应用最小补丁。

版本号只能用于记录环境，不能单独作为补丁是否需要恢复的依据。上游可能在相同版本重新发布、回移实现或改变编译结构。

## 4. 代码恢复要求

### 4.1 目标文件与稳定锚点

当前编译产物中的相对路径和职责：

| 相对路径 | 稳定职责与查找锚点 |
|---|---|
| `dist/src/messaging/inbound.js` | `contextTokenStore`、`persistContextTokens`、`restoreContextTokens`、`getContextToken` |
| `dist/src/channel.js` | `sendWeixinOutbound`、`sendMessageWeixin` 和 `getContextToken` import |

升级后路径可能改变。路径变化时应按函数职责和导出符号重新定位，不得为了匹配本文创建重复文件。

### 4.2 入站存储必须具备的逻辑

持久化写入必须显式使用安全权限，并修正已存在文件可能遗留的权限：

```js
fs.writeFileSync(filePath, JSON.stringify(tokens, null, 0), {
  encoding: "utf-8",
  mode: 0o600,
});
fs.chmodSync(filePath, 0o600);
```

`getContextToken` 必须在当前模块内存未命中时惰性恢复：

```js
export function getContextToken(accountId, userId) {
  const key = contextTokenKey(accountId, userId);
  let value = contextTokenStore.get(key);
  if (value === undefined) {
    restoreContextTokens(accountId);
    value = contextTokenStore.get(key);
  }
  return value;
}
```

允许保留上游日志，但不得记录 token。惰性恢复只读取插件自身固定状态目录中由账号映射得到的文件，不接受 Chub 页面或 API 提供任意路径。

### 4.3 通用出站必须具备的逻辑

`sendWeixinOutbound` 在账号已解析并确认可用后，按以下优先级取得 token：

```js
const contextToken = params.contextToken
  ?? getContextToken(account.accountId, params.to);
if (!contextToken) {
  throw new Error(
    "weixin_context_missing: ask the recipient to message the bot first",
  );
}
```

随后必须把 `contextToken` 传给实际的 `sendMessageWeixin` 调用。显式传入的 token 优先于持久化回退；不得遍历其他用户或将任意历史 token 用于当前收件人。

### 4.4 参考 patch 的使用规则

当前结构对应的最小参考补丁位于：

`integrations/openclaw/patches/weixin-clawbot-context-token-persistence.patch`

该文件用于保留精确改动和帮助 AI 对照，不能无条件执行：

- 先检查目标代码与 patch 上下文是否匹配。
- 先执行 dry-run；任何 hunk 模糊匹配、偏移异常或失败时停止自动应用。
- patch 已应用或上游已有等价实现时不得重复应用。
- 上游结构变化时，以第 1 节行为不变量和第 4 节关键逻辑为准，重新生成最小适配，不强行修改无关代码。
- 修改后读取最终 diff，确认没有账号、收件人、token、绝对用户路径或无关改动。

## 5. 修改后的最小复核

修改或恢复补丁后必须确认：

1. 修改的 JavaScript 通过语法检查，实际加载代码包含 `getContextToken` 的惰性恢复和安全写入。
2. 持久化文件存在时类型、归属和权限正确；只检查元数据，不读取或输出正文。
3. 使用 OpenClaw 自身命令重启 Gateway，并确认实例已替换、探测成功、目标账号处于 enabled、configured、running 且无错误状态。
4. 让目标收件人先发送一条新消息，再进行一次无敏感内容的固定目标发送；命令成功后仍由维护者确认微信真实收到。
5. 通过 Chub 快速交互完成一次结果通知，确认任务状态与通知状态独立，通知返回 `sent`。

真实发送有外部副作用，只在维护者已授权时执行。出现 `prepare failed` 时先刷新入站 token，不通过盲目重试或更换收件人规避。

## 6. 安全与禁止事项

- 补丁新增日志不得记录 token 或完整收件人；检查第三方插件原有日志时必须在输出前脱敏账号和收件人，不把第三方现有日志行为误写成已由本补丁全面治理。
- 不在工具输出、文档、测试夹具或 Git 中写入真实 token、账号、收件人和本机绝对安装路径。
- 不读取或回传 token 文件正文来证明补丁有效；只检查存在性、文件类型、归属、权限和脱敏计数。
- 不允许客户端指定插件路径、token 文件路径、账号或收件人。
- 不使用 `openclaw agent`，不把完成通知扩展为反向设备操作。
- 不把通知失败改写成任务失败，也不把子进程创建或 Tool Call 创建当作发送成功。
- 不在首页检测中自动打补丁、重启 Gateway 或发送测试消息。
- 不删除第三方插件、OpenClaw 状态文件或待投递记录来“修复”验收。

## 7. 当前维护结论

macOS、Ubuntu 当前均已确认持久化文件权限、Gateway 重启恢复、出站模块惰性恢复、`openclaw message send` 真实送达和 Chub 完成通知链路。

2026-08-11 已再次复核当前实际加载的 `@tencent-weixin/openclaw-weixin 2.4.6`：运行产物包含 `600` 安全写入、启动恢复、内存未命中时磁盘惰性恢复和通用出站 Token 回退；持久化文件权限为 `600`，参考 patch 的 dry-run 识别为已应用，Gateway 探测与微信账号运行状态正常。该记录只说明本次实际加载产物，不替代升级或重装后的重新检查。

插件升级、重装或实际加载产物变化后必须重新执行第 3～5 节，不能继承当前结论。若上游原生实现第 1 节全部不变量，应停止应用本地兼容补丁、保留最小复核，并将本文状态更新为“上游已原生支持”；是否删除历史参考 patch 由后续维护决定，不能在运行目录中直接清理。
