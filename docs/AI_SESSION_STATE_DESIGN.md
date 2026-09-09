# Chub Session 状态模型设计

> 状态：已验收
> 主要读者：AI Agent、实现和排障 Agent；维护人员用于确认 Session 的核心状态和展示边界。
> 本文负责：Chub Session 的存储模型、Native Session 绑定、状态投影、Session 操作和恢复边界。
> 本文不负责：Runtime 私有发现格式、Worker 任务恢复与通知、外置 Runtime 生命周期和微信消息路由。

## AI 可执行契约

Chub 只管理一种 `Chub Session`。页面、API、微信槽位和任务入口统一使用 `session_id`；调用方不能提交或替换 `native_session_id`、`runtime_id` 或 `implementation_id`。

Session Store 的当前格式为 v3，严格校验文件类型、所有者、权限、大小和字段结构。读取失败时，Session 写入失败关闭；不根据页面缓存、标题或工作目录猜测状态。Chub 自有的旧格式状态不兼容时整体初始化为当前格式，Codex 原生数据不在该边界内。

每条 Session 固定保存以下业务事实：

| 字段组 | 所有者与含义 |
| --- | --- |
| 身份与工作区 | `id`、`runtime_id`、固定的 `implementation_id`、工作区和工作目录，由 Chub 创建并持久化。 |
| Native 映射 | `native_session_id` 与兼容组，只能由可信 Worker 结果绑定；同一 `(runtime_id, native_session_id)` 最多属于一个 Chub Session。 |
| 任务认领 | 当前 Quick Worker 的任务 ID 与执行代次，只用于首个 Native ID 的原子认领和迟到结果拒绝。 |
| 用户配置 | 标题、权限、模型和推理等级；`ask` 不能用于后台任务提交。 |
| 展示投影 | `status`、`activity`、活动来源、最近活动时间和有界错误文本；它们不替代 Worker 任务终态或 Runtime 原生状态。 |

## 创建、绑定与续接

1. 创建 Session 时只写入 Chub 记录和默认 Runtime 实现槽位，不创建 Native Session。
2. 首个任务由 Quick Worker 使用无 Native ID 的请求执行。受理任务时 Session 登记该任务为唯一 Native ID 认领者。
3. Worker 从可信结构化结果取得 Native ID 后，必须同时提供任务和执行代次。Session Manager 只接受仍匹配当前认领的结果，并校验 Runtime ID、实现槽位、Native ID 格式和全局唯一映射。
4. 绑定成功后写入 Native ID 与兼容组。后续任务使用同一 Native ID 调用 Runtime 的 `resume`；默认实现、模型或其他 Session 的变化不得改投已绑定 Session。
5. Native ID 冲突、过期结果、无法确认的结果或 Store 写入失败均不得改写映射。任务终态会释放未完成的认领。

内部翻译使用独立工作区和固定用途的 Session。它不进入用户 Session 列表或微信槽位；只有确认旧 Native writer 已释放时，才允许其在同一逻辑 Session 内轮换 Native ID。

## 状态与使用投影

`status` 是 Chub 对当前可确认状态的投影：

| 状态 | 当前含义 |
| --- | --- |
| `new` | 尚未绑定 Native Session。 |
| `running` | Quick Worker 正在处理该 Session 的任务。 |
| `stopped` | 已绑定 Native Session，且当前没有 Chub 任务。 |
| `error` | Chub 无法可靠收敛当前 Session 状态。 |

`activity` 仅表示 Chub 已确认的 Turn 阶段：`working` 的来源固定为 `quick`；`idle` 表示没有已确认的 Turn；`unknown` 不能视为空闲。非 `working` 状态的活动来源固定为 `none`。

`usage` 是每次读取时生成的短期使用投影，不持久化为 writer 事实：

| owner | phase | 含义 |
| --- | --- | --- |
| `none` | `idle` | 没有 Chub 任务，且 Runtime 未发现 Native writer。 |
| `quick_worker` | `waiting_result` | Quick Worker 正在处理该 Session。 |
| `external` | `unknown` | Runtime 明确发现其他 writer；Chub 不接管。 |
| `unknown` | `unknown` | Runtime、实现槽位或 writer 检查无法确认。 |

判断顺序固定为：Session 绑定实现是否可用、Quick Worker 当前任务、是否存在 Native 映射、Runtime writer probe。`unknown` 只阻止当前会产生双写或不可恢复破坏的操作，不阻塞其他 Session、只读列表、Runtime 设置或无关服务。

## Native Session 列表

Runtime discovery 返回独立的只读 Native Session 列表。已绑定 Chub Session 的原生项不重复展示；未关联项不公开 Native ID，也不能被认领或导入为 Chub Session。

对于未关联项，页面只可持有后端签发的短期不透明引用执行归档或删除。同一原生项在有效期内复用引用，服务端只保留固定数量的未过期引用。执行时服务端必须重新发现该项，确认仍未关联并复核 writer；任一条件不满足时失败关闭，页面在成功或失败后刷新列表。Native 操作成功只代表 Runtime 原生状态，Chub 不会因此创建、删除或改写其他 Session。

每次实际读取 Native Session 列表都直接调用 Runtime discovery；页面不将 Native 列表写入会话缓存，因为列表反映当前原生来源和短期操作引用。发现到的未关联项只提供标题、工作目录、创建/更新时间和可操作状态。发现不创建或认领 Chub Session，也不覆盖已绑定 Chub Session 的权限、模型或推理等级；这些字段即使存在于 Runtime 通用元数据，也只有明确的产品用途才能被消费。翻译工作目录中的内部 Native Session 默认不进入工作台列表；维护者可在“微信任务润色”中开启显示，仅改变列表投影，不改变任务、Session 或原生状态。

Native discovery 采用逐项尽力读取：一个原生项或标题辅助信息不可读时，其他可读项继续返回，下一次刷新直接重试原生来源。发现刷新不清空 Chub Session、不阻塞 Quick Worker 或无关能力。已绑定 Chub Session 的 Native 项本次缺席或不可读只表示未知：保留映射、已保存工作目录和任务历史，后续提交仍由 Runtime 直接尝试 `resume` 并按 writer/执行终态收敛。只有原生状态库明确归档，或完整发现与可用状态库共同确认该 ID 已删除时，发现才同步清理 Chub Session；清理先移除已结束的 Quick Worker 任务记录并释放已关联微信槽位，任一环节无法确认则保留 Session。不完整发现绝不从“未发现”推断删除。

首次 Chub 任务正在认领 Native ID 时，Runtime 仍可执行只读发现，但页面暂不展示任何未关联 Native Session。该抑制只持续到当前认领 Session 已写入 Native ID，或对应任务不再运行；认领结束后的下一次列表读取立即重新执行 Native discovery。它不创建、认领或导入发现结果。这样由 Chub 创建的 Native Session 不会在绑定回写窗口短暂显示为外部 Session。该窗口内外部新建的 Native Session 也会延后到下一次刷新展示。

## Session 操作

| 操作 | 直接作用 | 最终成功条件 | 失败边界 |
| --- | --- | --- | --- |
| 重命名 | Chub 标题 | Store 持久化成功 | 外部 writer 时拒绝；不修改 Native 数据。 |
| 配置更新 | 权限、模型、推理等级 | 空闲且配置校验、Store 持久化均成功 | 正在执行、外部占用或未知状态不修改配置。 |
| 停止 | 当前 Quick Worker 任务与 Chub 投影 | Worker 取消终态确认后将 Session 收敛为可继续使用的状态 | 外部或未知 writer 不执行停止。 |
| 归档 | Native Session（如已绑定）、任务记录、Chub 记录和微信槽位 | 原生归档确认后完成 Chub 清理和槽位释放 | 原生结果或槽位释放未知时保留 Chub 记录。 |
| 删除 | Native Session（如已绑定）、任务记录、Chub 记录和微信槽位 | 原生删除确认后完成 Chub 清理和槽位释放 | 原生结果或槽位释放未知时保留 Chub 记录。 |

归档和删除先处理当前任务与 Native 操作，再清理 Chub 自有状态。原生项已处于目标终态时可按幂等完成；进程启动、HTTP 成功或任务受理均不能替代原生操作、Worker 或槽位的最终确认。删除 Native 失败且任务停止已确认时，删除流程可在第二次危险确认后仅清理 Chub Session、任务记录和微信槽位；该降级不声称 Native 已归档或删除。

## 当前页面行为

首页按 Runtime 展示 Chub Sessions 与 Native Sessions。用户进入任何 Chub Session 都进入同一对话页面；任务历史、提交、重命名、停止、归档和删除都围绕该 Session 执行。外部占用和状态未知的 Session 仍允许查看历史，但写入和破坏性操作按服务端最终门禁收敛。

## 验收范围与复检

已验证：v3 Store、单一 Session 入口、首次 Native 绑定、后续 `resume`、Native 列表投影和 Session 页面回归。

修改 Store 格式、Session 身份与实现槽位、Native ID 认领、writer 判断、操作终态或用户可见状态时，必须重新验收相关 API、Worker 恢复和页面行为。
