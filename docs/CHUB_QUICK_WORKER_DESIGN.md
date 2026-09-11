# Chub Quick Worker 独立服务设计

> 状态：已验收
> 主要读者：AI Agent；维护者通过与 AI Agent 协作，理解并确认本文规则。
> 本文负责：Quick Worker 的任务状态、Session 租约、Native 绑定、恢复与通知终态。
> 本文不负责：Runtime 私有 CLI、Session 页面展示、Runtime 插件模块生命周期和微信路由。

## AI 可执行契约

Quick Worker 是与 Web 独立的后台服务，承载页面、微信 Chub 模式和翻译任务。Web 只负责可信入口校验、提交、状态投影和通知协调；不得回退到 Web 内执行 Runner。

- 同一 Chub Session 同时最多一个 Worker writer。租约只阻止重复 Chub 提交，不替代 Runtime 的 writer 最终判断，也不因历史租约长期锁住 Session。
- 首个任务使用预分配的 Chub Session ID 与无 Native ID 的 Runner 请求。Worker 接收可信 Native ID 后，按任务 ID 与执行代次原子绑定；冲突、过期回传或结果不明都不得改写映射。
- 后续任务按已绑定 Native ID 调用 `resume`。默认 Runtime 或 Session 设置之后改变，不改写已受理任务快照。
- 任务状态单向推进：`accepted -> starting -> running -> succeeded|failed|timed_out|cancelled`。进程创建、HTTP 200 或已受理均不等于任务成功。
- Worker 不可用、协议不兼容或恢复未完成时，新的 Session 创建和写入失败关闭；只读 Session/Native 列表、无关服务及已运行任务不受影响。

## 当前行为与边界

Web 重启不停止 Worker、已受理 Runner、翻译 FIFO 或确认 FIFO。新 Web 先核对 Worker 健康、协议、活动任务、租约、通知和重启状态，完成后才开放新的写入。Worker 或宿主机崩溃使任务结果不确定时按失败收敛，不自动重放。

任务终态、通知终态、Web 重启终态和 Worker 重启终态分别确认。页面来源任务只在页面展示结果；微信任务仅按受理时保存的可信账号与发送者回送，路由失效不得回退全局收件人。

翻译使用同一 Worker 的独立 FIFO。翻译失败不得执行原文、切换目标或重复提交派生任务；目标暂忙时保留固定目标并按既有恢复规则处理。

## 维护者操作

`chub web restart` 只重启 Web；`chub worker reload` 才影响 Worker 任务恢复。两者都必须通过健康、任务/通知状态等最终结果确认，不能只看服务进程或 HTTP 回应。

## 验收范围与复检

已验证：Web 重启后 Worker 任务恢复、首次 Native 绑定和后续 `resume`。修改任务协议、租约所有权、Native 绑定、恢复或通知终态时，必须重新验收。
