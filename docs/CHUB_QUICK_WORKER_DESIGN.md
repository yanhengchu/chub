# Chub Quick Worker 独立服务设计

> 状态：已验收
> 主要读者：AI Agent；维护者通过与 AI Agent 协作，理解并确认本文规则。
> 本文负责：Quick Worker 的任务状态、Session 租约、可信 Native 结果、恢复与通知终态。
> 本文不负责：Runtime 私有 CLI、Session 页面展示、Runtime 插件模块生命周期和微信路由。

## AI 可执行契约

Quick Worker 是与 Web 独立的后台服务，承载页面和微信 Chub 模式提交的物理任务。其 Chub 自有任务和租约状态位于共享 AI Runtime 状态目录 `data/local/state/ai-runtime`。Web 只负责可信入口校验、提交、状态投影和通知协调；不得回退到 Web 内执行 Runner。退役的 `data/local/state/codex` 不参与恢复或重放，升级恢复只会清理。

- 同一 Chub Session 同时最多一个 Worker writer。租约只阻止重复 Chub 提交，不替代 Runtime 的 writer 最终判断，也不因历史租约长期锁住 Session。
- 首个任务使用预分配的 Chub Session ID 与无 Native ID 的 Runner 请求。Worker 返回携带任务 ID 与执行代次的可信 Native ID；Session Manager 才是 Native 映射的唯一 writer，并按当前认领原子绑定。冲突、过期回传或结果不明都不得改写映射。
- 后续任务按已绑定 Native ID 调用 `resume`。默认 Runtime 或 Session 设置之后改变，不改写已受理任务快照。
- 任务状态单向推进：`accepted -> starting -> running -> succeeded|failed|timed_out|cancelled`。进程创建、HTTP 200 或已受理均不等于任务成功。
- Worker 不可用、协议不兼容或恢复未完成时，新的 Session 创建和写入失败关闭；只读 Session/Native 列表、无关服务及已运行任务不受影响。

当前 Worker 协议为 v14。本版本移除了已退役微信文本优化的 `translation` 任务类型、专用队列字段与原生 Session 轮换规则；升级后新 Worker 只使用 `tasks-v14`、`tombstones-v14` 与 `session-leases-v14` 等新版本目录，不读取或重放旧协议目录中的任务。旧 `translation` 任务、排队记录和关联租约均按不兼容 Chub 自有运行态丢弃，不得阻塞当前 Worker 对账和新的 Session 写入。

## 当前行为与边界

Web 重启不停止 Worker、已受理 Runner 或原生 Session。新 Web 先核对 Worker 健康、协议、活动任务、租约、通知和重启状态，完成后才开放新的写入。Worker 或宿主机崩溃使任务结果不确定时按失败收敛，不自动重放。Web 投影不可读或 Worker 存在无可恢复 Web 元数据的任务时，恢复流程立即取消并确认丢弃对应 Chub 自有任务状态，同时清除其 Chub Session 的 Quick Worker 原生声明；若固定状态路径异常为目录等非文件，会隔离旧路径并重建空投影。无法确认取消、丢弃或声明清理时明确报告恢复失败并指向 Chub 工作站重建，不无限等待。

`chub workstation rebuild --force` 是 Worker 边界的破坏性例外，不等同于 Web 或 Worker 重启：它会停止 Worker，结束排队和执行中的 Chub 任务，并清理其 Chub 自有任务、租约和投影状态；这些任务不恢复、不重放。固定维护执行器随后按当前代码和配置启动新的 Worker、导入并启用当前默认 Runtime。只有 Web 健康、Worker 已就绪且协议一致、默认 Runtime 已导入/启用/健康，并且核验时不存在活动、排队、结果不确定或损坏的 Worker 任务，重建才可记为成功。重建操作记录独立于被清理的 AI Runtime 状态；原生 Runtime Session 和浏览器 Profile 不在该清理范围内。

任务终态、通知终态、Web 重启终态和 Worker 重启终态分别确认。页面来源任务只在页面展示结果；微信任务仅按受理时保存的可信账号与发送者回送，路由失效不得回退全局收件人。

文本优化及其独立 FIFO 已退役；Worker 不接受该类任务，也不会为它们保留恢复或重投逻辑。

## 维护者操作

`chub web restart` 只重启 Web；`chub worker reload` 才影响 Worker 任务恢复；`chub workstation rebuild --force` 则会清空 Chub 自有 Worker 运行态并重建整个工作站基线，不能作为普通 Worker 更新的替代。页面、恢复和微信维护入口通过 `QuickWorkerMaintenanceUseCase` 请求固定 `scripts/maintenance/chub-worker-reload` 适配，不重新执行 `scripts/chub`，且不接受可变服务、路径或命令。CLI 保持原有固定语法并转发到同一适配。三类操作都必须通过对应的健康、任务/通知状态等最终结果确认，不能只看服务进程或 HTTP 回应。

## 验收范围与复检

已验证：Web 重启后 Worker 任务恢复、首次 Native 绑定和后续 `resume`；macOS 本机终端执行工作站重建后，Web、Worker 和默认 Runtime 的最终健康确认。未验证或不承诺：Ubuntu 的实际工作站重建服务恢复。修改任务协议、租约所有权、Native 绑定、恢复、工作站重建清理范围或通知终态时，必须重新验收。
