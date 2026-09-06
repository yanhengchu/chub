# Chub 外置模块功能设计

> 状态：第一阶段已验收
> 主要读者：需要维护 Runtime ZIP、评估新增 Runtime 或重新评审任务编排外置的 AI Agent；维护人员用于确认模块管理范围和恢复边界。
> 本文负责：定义当前 Runtime 外置模块的架构、ZIP 协议、安装和移除流程、状态清理边界、设置页行为及复检要求。
> 本文不负责：AI Runtime 的 Adapter/Runner 通用实现规范（见[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)）、AI Session/Worker 的状态机与恢复细节，或微信固定指令和用户可见回复格式（见[Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)第 4 节）。
> 维护说明：第一阶段已完成 Codex Runtime 外置化并通过 macOS 产品流程和自动化验收。本文只描述当前架构，不保留实施阶段记录。Ubuntu 验收、任务编排外置和第二 Runtime 均为冻结的后续事项，不构成当前能力或排期。

## AI 可执行契约

1. Chub 当前外置的是**可信本机 Runtime 模块**。模块以 ZIP 导入、在固定目录安装并由 Web 与 Quick Worker 分别加载；它不是多用户插件市场、Codex Skill、提示词文件或外部通道可调用的扩展机制。
2. 当前安装协议只接受 `runtime` 类型。生产 Runtime 只有第一方 Codex；任务编排模块尚未实现，微信任务润色继续由主项目的固定入口、设置页和既有状态所有权处理。
3. Runtime 模块只提供 Runtime 私有实现和注册信息：Adapter、Worker Runner、能力、显示信息及可选专属设置。Chub 核心继续拥有认证、固定 API、逻辑 AI Session、Quick Worker 任务/租约/终态、通知、操作日志和页面交互。
4. 模块的安装、替换和移除必须在 Quick Worker 可安全维护时进行，且只有 Web 注册表、Worker 注册表和所需状态清理均收敛后才能报告最终结果。HTTP 成功、目录写入或 Worker 重启请求本身都不代表模块切换成功。
5. 单个 ZIP、清单、依赖或入口故障只能令该模块不可用或本次操作失败，不得阻塞 Chub 控制面、无关 Runtime、只读能力或独立服务。外置机制不放宽 loopback/Tailnet 来源校验、固定路由、输入限制、敏感信息保护或失败关闭要求。
6. 模块升级边界只覆盖目标 Runtime 的 Chub 自有状态。不保留旧版本回退、跨版本任务恢复、双读或双写；Chub 通用设置、其他模块设置、第三方原生数据、操作日志和明确要求保留的数据不在清理范围。

## 当前架构

### 当前范围

当前生产从固定安装目录发现 Runtime 模块。第一方 Codex Runtime 的构建源码保留在仓库 `runtime-modules/codex-runtime/`，生成 ZIP 和实际安装目录位于 `data/local/`，不进入 Git。Codex ZIP 是当前唯一完整接入、唯一默认且唯一对外提供 AI 能力的 Runtime；移除后 Chub 不会自动回装它。

设置页的“AI Runtime”分组动态读取已加载 Runtime 的名称和说明，并提供 Runtime ZIP 的预检、导入、替换、移除及状态查看。页面不提供客户端 Runtime、Runner、命令、路径或环境变量选择器。当前微信任务润色仍是独立的固定设置页，不是外置任务编排模块。

```text
维护者选择 Runtime ZIP
          |
          v
受保护的预检和安装入口
  - ZIP、清单、依赖、模块入口、Adapter/Runner wiring
          |
          v
固定 Runtime 安装目录 <----> Web Runtime 注册表
          |                         |
          |                         v
          |                    设置页和 AI Session Manager
          v
Quick Worker 重载并确认同一 Runtime 注册表
          |
          v
目标 Runtime 的 Chub 自有状态清理与最终操作记录
```

Web 与 Quick Worker 各自构造进程内 Runtime 实例，但都从同一个安装目录和同一份 Runtime 模块协议加载。两者不会共享 Python 对象或以某一方的加载成功替代另一方的确认。

### 与其他领域的契约

Runtime 模块只拥有 Runtime 私有实现和清单声明。Adapter/Runner 的共享契约、能力矩阵和新增 Runtime 实现规范以[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)为准；逻辑 Session 和单 writer 语义以[AI Session 状态模型](AI_SESSION_STATE_DESIGN.md)为准；任务、租约、恢复和通知终态以[Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)为准。

模块安装流程只负责把已验证的 Runtime 实现切换到 Web 与 Worker 注册表，并处理该 Runtime 的 Chub 自有状态清理。模块不得接管上述领域状态，也不得创建路由、页面导航、外部指令、后台服务、任意命令或任意文件路径。Adapter 和 Runner 必须使用同一个 `runtime_id` 与能力声明，并通过 `validate_runtime_wiring()`。

## Runtime ZIP 协议

### 包形态

模块包固定为 ZIP，包根目录必须有唯一的 `chub-module.json`。当前协议版本为 `1`，只接受 `module_type: "runtime"`。清单包含以下字段：

| 字段 | 规则 |
| --- | --- |
| `protocol_version` | 必须为 `1`；未知协议直接拒绝，不兼容读取旧协议。 |
| `module_id` | 全局唯一的小写 Runtime 标识；必须与模块 Runtime Descriptor 的 `runtime_id` 一致。 |
| `module_type` | 当前仅允许 `runtime`。 |
| `display_name`、`description`、`version` | 用于设置页展示和安装记录；入口返回的展示信息必须与清单一致。 |
| `chub_version` | 必须精确匹配当前 Chub 版本。 |
| `entry` | 固定为 `<python_module>:<factory>`；工厂接收当前 `Settings` 并返回 Runtime 注册对象。 |
| `dependencies` | 可选的包内 requirements 相对路径；依赖安装到该模块自己的目录。 |

ZIP 不以文件名或存放目录决定模块身份。压缩包和清单都受固定大小、文件数量和路径限制；绝对路径、`..` 路径和符号链接会被拒绝。安装目录、暂存目录、清单、安装元数据和恢复记录均以受限权限创建。

### 加载与装配

Chub 仅扫描固定 Runtime 安装目录。每个模块以 Runtime ID 私有 Python 命名空间加载，避免不同 ZIP 的同名包复用解释器缓存。安装前和启动扫描时均会验证：

- 清单格式、Chub 版本和可选依赖清单；
- Python 入口可加载且返回 Runtime 注册对象；
- 清单、显示信息和 `runtime_id` 一致；
- Adapter、Worker Runner、能力矩阵和 Runtime 状态满足共享契约；
- 注册表中不存在重复 Runtime 或重复默认 Runtime。

依赖安装、入口加载或装配校验失败时，候选模块不激活。启动时发现损坏安装目录时，Chub 将它标为不可用并继续加载其他 Runtime；控制面、模块列表和导入入口仍可用。

## 安装、替换与移除

### 维护流程

维护者只能通过设置页的 Runtime 模块入口导入或移除 ZIP。导入前，服务端先完成只读预检；无效 ZIP 在此阶段失败，不会排空 Quick Worker。有效候选才按以下顺序处理：

1. 确认 Quick Worker 为 `ready`，且没有活动、排队、不确定或损坏任务；否则拒绝本次维护，并保留现有模块。
2. 请求 Worker 进入维护排空状态，再安装或暂时移除目标 Runtime。
3. Web 重建注册表，协调 Worker 重载，并确认 Worker 的新 generation、`runtime_ids` 和可用 Runtime 列表已经收敛。
4. 仅在双端注册表确认后，清理目标 Runtime 的 Chub 自有状态，完成安装或移除操作记录。

安装和移除操作均记录 `requested`、`started`、`succeeded` 或 `failed`。设置页只在最终成功后更新模块状态；失败会显示可诊断原因，不把“已接受”或“重载已请求”显示为成功。

### 恢复和失败边界

切换前会记录受限的激活日志，并在替换或移除时保留旧目录作为短暂备份。Web 注册或 Worker 确认失败时，Chub 恢复原模块并重新确认恢复后的 Worker；无法确认恢复时，操作明确进入状态未知，而不宣称模块保持可用。

当 Web 与 Worker 已确认新注册表后，目标模块即成为当前版本。后续状态清理失败不会回退已确认的模块切换；Chub 保留受限的待清理记录，并在当前实例后台恢复或后续启动时继续完成清理。进程在切换中断时，下一次启动根据激活日志恢复旧模块或完成已知恢复动作，不猜测成功。

移除 Runtime 不会自动安装替代 Runtime，不会自动切换已有任务到其他 Runtime，也不影响无关 Runtime、只读能力或独立服务。当前只有 Codex 时，移除后 AI 新建和提交入口会明确不可用，但 Chub 控制面、项目资料和维护入口继续工作。

### 状态清理边界

目标 Runtime 安装、替换、移除或不兼容升级后，Chub 可以清理与该 Runtime 关联的逻辑 Session 映射、Runtime 启停偏好、Quick Worker 持久任务、Tombstone、租约、缓存和模块专属设置，并从新模块重新初始化。这是固定升级还原边界，不承诺保留旧任务、旧队列、旧设置、旧版本代码或跨版本恢复。

清理不得覆盖 Chub 通用设置、其他 Runtime 的设置或状态、第三方原生数据、操作日志、用户工作区和明确要求保留的数据。首次安装没有目标 Runtime 数据时不执行无关清理。

## 页面与外部边界

设置页中的 Runtime 模块区域只面向维护者可信网络开放。它展示已发现模块的名称、版本、状态和受控失败原因；替换或移除使用确认交互，说明目标 Runtime 的 Chub 自有运行态和模块专属设置将被清理。

微信、OpenClaw、自动化和其他外部入口不能上传、安装、替换、移除或执行模块，也不能借模块取得更高权限。Runtime 不可用、停用或依赖不足时，只拒绝直接依赖它的新提交；Quick Worker 健康、已有任务查询、无关 Runtime 和独立服务按各自规则继续工作。

## 冻结的后续方向

以下内容不属于当前实现，不得在页面、API、设置或用户可见文案中宣称可用：

- **Ubuntu 验收**：当前第一阶段的实机服务验收冻结，未由 macOS 验收或自动化覆盖替代。
- **任务编排外置**：微信任务润色仍在主项目内。未来如恢复，必须先重新定义任务模块的入口、设置、Runtime 能力依赖、业务状态、Worker/通知边界和真实微信验收，而不是把现有页面直接包装为 ZIP。
- **第二 Runtime**：尚未实现。未来接入必须按[Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)完成能力评估、Adapter/Runner、固定注册、状态边界和目标平台验收；不得由客户端动态指定 Runtime 或自动切换。

## 维护者操作

第一方 Codex ZIP 通过 `python scripts/build_codex_runtime_zip.py` 构建，再从设置页的“AI Runtime → Runtime 模块”导入。不要直接复制、替换或删除固定安装目录中的文件；这会绕过 Web/Worker 注册确认和状态清理。

模块操作失败时，以页面最终提示和操作日志为准。仅在提示状态无法确认或后续启动仍显示待恢复时，再按具体错误调查；不要手动清理其他 Runtime、Worker 或用户数据来解除单个模块故障。

本文只在 Runtime 模块协议、安装恢复、状态清理、设置页交互、安全边界或验收范围变化时更新；Runtime 私有 Adapter 细节、微信业务规则和 Quick Worker 状态机由各自权威文档维护。

## 验收范围与复检

已验证：第一阶段覆盖 macOS 本机的 Codex ZIP 构建、移除和重新导入、Web/Worker 注册确认、快速交互提交、实时终端创建、设置页桌面与手机布局，以及无模块、损坏 ZIP、损坏安装目录隔离、依赖失败保留旧版本、激活/Worker 恢复和状态清理边界的自动化测试。

未验证或不承诺：Ubuntu 实机服务、真实微信收发、人为破坏本机安装目录或依赖环境后的实机恢复、任务编排模块和第二个真实 Runtime。上述范围恢复后必须另行定义验收，不能沿用本阶段结论。

Runtime ZIP 协议、安装或移除恢复、Web/Worker 注册确认、状态清理边界、固定 API、设置页模块管理交互，或任何用户可见 Runtime 行为变化时，必须重新执行本阶段相关自动化测试和 macOS 产品回归；涉及微信路由、通知或用户可见行为时，由维护者在真实微信客户端完成验收。

## 相关文档

- [Chub 总体架构设计](CHUB_ARCHITECTURE_DESIGN.md)：系统边界、领域分层和全局状态所有权。
- [Chub AI Runtime 架构设计](CHUB_AI_RUNTIME_DESIGN.md)：Runtime 共享契约、Adapter/Runner 与新增 Runtime 实现规范。
- [Chub Quick Worker 独立服务设计](CHUB_QUICK_WORKER_DESIGN.md)：任务、租约、恢复、通知终态和重启协调。
- [Chub 集成能力清单](CHUB_INTEGRATION_CAPABILITIES.md)：当前可调用能力与微信用户可见产品契约。
- [Chub 前端 UI 模块化设计](FRONTEND_UI_DESIGN.md)：设置页动态展示与交互规范。
