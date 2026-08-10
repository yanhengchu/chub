# 微信 ClawBot Context Token 持久化 AI 补丁规范

> 主要读者：负责维护 Chub 与本机 OpenClaw 的 AI Agent。
>
> 当前状态：本机已应用并完成真实出站验收。`@tencent-weixin/openclaw-weixin` 升级、重装或安装目录重建后，必须按本文重新检测；不得仅因旧缓存目录仍保留补丁而判断当前插件兼容。

## 1. 恢复目标

保证链路 `Chub 快速交互 → openclaw message send → 微信 ClawBot → 指定微信收件人` 在 Gateway 重启后仍能恢复最近一次有效 context token，并满足以下不变量：

1. 入站消息按 `accountId + userId` 持久化最新 context token。
2. Gateway 启动微信账号时恢复该账号的持久化 token。
3. 出站发送未显式携带 token 时，按调用方明确给出的账号和收件人查询 token。
4. 出站模块的内存 Map 未命中时，从磁盘惰性恢复一次后重新查询。
5. token 持久化文件每次写入后权限为 `600`。
6. 找不到或无法使用 token 时明确失败，不尝试其他账号或收件人，不调用 `openclaw agent`。

满足上述行为即可视为等价实现，不要求上游代码与参考 patch 逐字相同。若上游已原生满足全部不变量，应保留上游实现，不重复打补丁。

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

## 3. 恢复前的强制检查

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

## 5. 修改后的验证流程

### 5.1 静态验证

1. 对所有修改的 JavaScript 文件运行 `node --check`。
2. 确认实际加载的 `channel.js` import 了 `getContextToken`。
3. 确认实际发送调用收到解析后的 `contextToken`。
4. 确认内存未命中会执行惰性恢复，而不是只在 Gateway 启动时恢复。
5. 确认持久化写入和已有文件权限均为 `600`。

静态验证不得输出持久化文件正文或真实标识。

### 5.2 服务验证

1. 使用 OpenClaw 自身的 Gateway 重启命令。
2. 比较重启前后实例或 PID，确认新实例已经替换旧实例。
3. 确认 Gateway 探测成功。
4. 确认配置的微信账号处于 enabled、configured、running 且无待重启或最后错误状态。
5. 从脱敏日志确认持久化 token 已恢复；不得输出 token 或完整收件人。

### 5.3 真实发送验收

真实发送有外部副作用，只在维护者已经授权验收时执行：

1. 让待验收收件人先向对应 ClawBot 发送一条新消息，刷新 token。
2. 使用固定 channel、固定 account 和固定 recipient 发送一条无敏感内容的测试消息。
3. 确认 OpenClaw 命令返回成功。
4. OpenClaw 命令成功只表示通道接受了发送请求；由维护者确认微信真实收到后，才算插件链路完成最终验收。
5. 再使用 Chub `OpenClawCompletionNotifier` 发送一次任务结果摘要，确认 Service 返回 `sent`。
6. 重启 Chub，确认健康接口、OpenClaw Gateway 和快速交互页面正常。
7. 最终由快速交互页面提交短任务，确认任务状态与通知状态独立、微信收到结果摘要。

如果出现 `prepare failed`，先刷新入站 token，再测试；不得通过反复盲发或改用其他收件人规避。

## 6. 安全与禁止事项

- 补丁新增日志不得记录 token 或完整收件人；检查第三方插件原有日志时必须在输出前脱敏账号和收件人，不把第三方现有日志行为误写成已由本补丁全面治理。
- 不在工具输出、文档、测试夹具或 Git 中写入真实 token、账号、收件人和本机绝对安装路径。
- 不读取或回传 token 文件正文来证明补丁有效；只检查存在性、文件类型、归属、权限和脱敏计数。
- 不允许客户端指定插件路径、token 文件路径、账号或收件人。
- 不使用 `openclaw agent`，不把完成通知扩展为反向设备操作。
- 不把通知失败改写成任务失败，也不把子进程创建或 Tool Call 创建当作发送成功。
- 不在首页检测中自动打补丁、重启 Gateway 或发送测试消息。
- 不删除第三方插件、OpenClaw 状态文件或待投递记录来“修复”验收。

## 7. 未来首页只读检测契约

首页 OpenClaw 卡片未来可增加“微信通知兼容性”检查，但后端只能返回脱敏状态：

| 状态 | 判定 |
|---|---|
| `compatible` | 固定配置、账号运行状态、等价代码能力和安全文件权限均通过只读检查 |
| `not_activated` | 代码能力存在，但尚无持久化状态；提示对应收件人先发消息 |
| `repair_required` | 明确缺少必要行为、权限不安全或固定配置无效 |
| `unknown` | 插件来源或结构变化，现有规则无法可靠判断 |

检测优先使用上游未来提供的正式 capability 或诊断接口；不存在正式能力标记时，再组合检查实际加载来源、函数行为特征和文件元数据。不得只匹配版本号或单个源码字符串，也不得通过真实发送探测兼容性。

## 8. 当前验收基线与维护结论

当前 macOS 本机已验证：

- 持久化文件权限为 `600`。
- Gateway 重启后启动实例恢复 token。
- 出站模块内存未命中时可惰性恢复。
- 对应账号与收件人的 `openclaw message send` 返回成功；微信客户端最终送达仍以维护者页面验收反馈为准。
- Chub 通知 Service 返回 `sent`，Chub 重启后健康接口正常。

Ubuntu 尚未针对同一插件版本做实机回归。插件升级后，AI 必须重新执行第 3～5 节，不能继承本节结论。若上游原生实现全部不变量，应移除本地兼容补丁、保留回归验证，并把本文状态更新为“上游已原生支持”。
