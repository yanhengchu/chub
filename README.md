# Chub

## 本文职责

本文是 Chub 的项目入口，负责说明项目定位、当前能力概览、最小启动方式、常用维护入口、数据与安全摘要，以及权威文档导航。

本文不负责定义系统分层、状态所有权、服务恢复、Runtime 契约、任务执行、插件模块、微信指令、插件协议或页面交互规则；这些内容只在对应的模块设计文档中维护。需要确认“当前 Chub 能做什么”时，以[Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准；历史资料不覆盖当前契约。

## 项目介绍

Chub 是面向个人设备、本地优先的轻量 AI 工作站控制面。它协调受信入口、设备能力、AI Session、后台任务、自动化、通知和最终状态；Chub 本身不是模型，也不作为通用对话 Agent 执行任务。

当前 AI 能力由后端固定注册的本机 Runtime 提供，现有部署实例为 Codex。Quick Worker 承载跨 Web 重启继续运行的后台 AI 任务；OpenClaw 在微信链路中承担可信消息网关和通道适配。Chub 支持 macOS LaunchAgent 与 Ubuntu systemd user service。

Chub 按维护者授信的个人工作站运行：优先保证本地可用、局部恢复和最终状态可确认，不按多用户平台或零信任插件市场设计。此定位不放宽外部入口边界，微信、OpenClaw 和远程浏览器仍只能通过固定身份、路由和受控能力访问。

Chub 的长期定位是“个人本地工作站与统一控制面”：核心自身始终可独立运行，并可装载独立演进的插件模块。插件模块拥有自己的业务页面、设置、数据和流程，复用 Chub 的稳定能力与运行环境。核心优先保证可用性、局部恢复和最终状态确认，只在会造成直接数据破坏、安全越界或不可恢复冲突时施加最小门禁；单个插件模块未安装、停用或故障时，只影响其自身，不影响 Chub 核心和其他独立功能。

“插件模块”是 Chub 的统一产品与架构术语；正式交付和安装形态统一称为“插件包”或“插件 ZIP”。Runtime、微信任务编排与 Deliveryline 共用导入、移除、启用和禁用的生命周期入口：导入决定插件管理可见性，移除后设置侧边栏插件菜单一并隐藏；具体加载、校验与后续动作仍由各插件负责。Deliveryline 的首页 Business 入口、业务页面和业务 API 只在已启用且当前可用时开放。Deliveryline 的目标模型以“交付线列表 → 交付线详情 → 交付项列表 → 交付项详情”组织：导入资料先成为待澄清交付线，AI 主动完成整体澄清并提出候选，维护者确认或修正整体目标后才拆分交付项；交付项按适用的澄清、成档、评审、设计、实现、验证和验收检查点循环推进，不使用固定六阶段生命周期。当前已实现的是旧单交付项原型，整体交付线、路线图、目标版本、影响分析和新工作循环尚未接入。

## 当前能力

| 功能域 | 当前可用能力 | 主要入口 |
| --- | --- | --- |
| 设备与维护 | 查看节点状态、执行白名单维护任务、查看受限日志、生成本机正式部署包 | 工作台、设置、日志页、`chub` CLI |
| AI Runtime 与会话 | 使用 Codex Runtime 创建 Chub Session，通过 Quick Worker 连续执行任务、查看原生会话和结果 | 工作台、Session 页、微信 ClawBot |
| 任务执行 | 通过独立 Quick Worker 执行页面、微信和翻译快速任务 | 快速交互页、微信 ClawBot |
| 需求储备 | 管理 R1-R9 轻量需求 | `chub` CLI、微信 ClawBot |
| 自动化与周报 | 使用受管 Debug Chrome 运行固定自动化，准备并生成周报 | 自动化页、周报页、命令行 |
| 今日关注 | 使用固定内部 AI Session 整理固定公开来源的 AI 动态，并展示今日计划与待办区块 | 工作台今日关注页 |
| 外部集成与通知 | 接入 OpenClaw/微信 ClawBot，并向预配置飞书目标发送通知 | 设置页、微信 ClawBot、OpenClaw Tool、CLI |
| 插件模块 | 统一管理 Runtime、微信任务编排与 Deliveryline 的导入、移除、启用和禁用；Deliveryline 已提供需求提出档案与评审前校验 | 设置页、受控维护入口 |
| 项目资料与外观 | 浏览已登记的项目资料，切换主题和文字大小 | 工作台、设置页 |

微信任务编排插件模块已提供直接执行、固定仓库开发实现 `weixin-orchestration-dev`，以及正式 ZIP 的导入、启用、停用和移除。旧 `internal` 阶段仅用于识别历史运行态并失败关闭，不再接受新任务。ZIP 产物按内容摘要不可变保存，已受理任务继续绑定创建时的阶段产物；通用架构见[Chub 任务编排插件模块架构设计](docs/CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md)，微信范围见[Chub 微信任务编排插件模块设计](docs/WEIXIN_TASK_ORCHESTRATION_PLUGIN_DESIGN.md)。

## 快速开始

前置条件：Python 3.12 或更高版本。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp config/settings.example.yaml config/settings.local.yaml
.venv/bin/python main.py
```

`config/settings.local.yaml` 仅保存本机配置，不应提交。按实际设备填写节点信息、Runtime、自动化和可选 OpenClaw 配置；字段说明以 [settings 示例](config/settings.example.yaml) 和对应专项设计为准。

Chub 始终提供 loopback 访问；启用默认的 Tailnet 可信访问后，也会使用当前可用的 Tailscale 地址。不要配置公网监听或普通局域网监听。Android 通过同一 Tailnet 的浏览器访问 Chub；它不是原生 Android 应用或离线 PWA。

## 使用与维护入口

从当前工作区安装用户级后台服务：

```bash
./scripts/chub install
```

日常维护使用 `chub status`、`chub check`、`chub web restart`、`chub web logs` 和 `chub version`。完整 CLI、Worker 操作、通知命令、平台差异及影响范围以[集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准。

普通 Web 重启、Quick Worker 重启、系统升级恢复和 OpenClaw Gateway 维护是相互独立的操作，必须按各自的最终状态确认；不要用一个服务的状态推断另一服务成功。具体范围与恢复方式见[总体架构](docs/CHUB_ARCHITECTURE_DESIGN.md)、[Quick Worker 设计](docs/CHUB_QUICK_WORKER_DESIGN.md)和[OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md)。

## 数据与安全摘要

- `config/settings.local.yaml`、`config/automations.local.yaml` 和凭据文件只保存本机，不提交。
- `data/shared/` 只保存明确允许 Git 同步的共享资料；`data/local/` 保存本机运行态、缓存和产物，不提交。
- 受保护接口只接受真实 loopback，或在启用时的真实 Tailnet socket 来源；不信任客户端转发 Header。
- 客户端和外部通道不能提供任意命令、路径、Runtime、原生 Session、收件人或凭据；公开页面、日志、通知和示例配置不得包含秘密。

数据所有权、升级清理和恢复边界以[Chub 总体架构设计](docs/CHUB_ARCHITECTURE_DESIGN.md)为准；Runtime、Session、Worker 和外部通道的具体安全边界以对应专项设计为准。

## 项目资料维护

文档应各自只维护一个领域的权威规则，跨领域内容通过链接引用，不复制完整正文。

- **项目说明**：项目定位、能力概览、启动与维护入口、安全摘要和文档导航。
- **总体架构**：系统分层、进程边界、状态所有权、依赖方向和跨模块约束。
- **模块设计文档**：各模块的当前行为、边界、维护规则和验收范围。
- **当前能力契约**：当前 CLI、插件、固定 API 和微信固定指令。
- **维护与归档资料**：插件构建部署、异常恢复和历史追溯。

当前文档的登记、摘要、分类和状态以 `docs/design_documents.json` 为准。分类是固定受控元数据：`项目基线`定义项目级规则，`专项需求与设计`是可独立讨论、实施和验收的专题能力，`独立学习资料`不进入 Chub 主交付链路，`历史归档`只用于追溯。分类不等同于生命周期状态：新文档通常从“调研中”开始，`持续维护`只用于长期权威资料；`专项需求与设计`是后续关联 Deliveryline 的候选来源，项目基线、学习资料和归档资料仅作为背景、约束或追溯来源。模块设计文档顶部统一声明状态、主要读者、本文负责和本文不负责；已验收文档还须保留验证范围、未承诺范围和复检触发条件。“第一阶段已验收”只表示该文档定义的第一阶段已通过验收，不代表所有长期目标完成。

### 三份核心文档

理解、设计或调整 Chub 能力时，依次阅读以下三份文档：

| 文档 | 唯一职责 |
| --- | --- |
| 本项目说明 | 说明产品定位、当前能力概览、使用入口与文档导航。 |
| [Chub 总体架构设计](docs/CHUB_ARCHITECTURE_DESIGN.md) | 定义分层、状态所有权、依赖方向，以及能力由谁实际执行和确认。 |
| [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md) | 按使用场景登记当前可用能力、入口映射与微信固定指令契约。 |

三者共同构成当前项目理解基线：项目说明不替代架构边界，架构不重复逐项能力，能力清单不改变状态所有权或专项协议。出现表述冲突时，产品定位、分层/状态所有权、当前能力/入口契约依次以这三份文档中各自负责的范围为准；目标设计不得覆盖当前能力结论。新增能力先在能力清单明确场景与效果；若改变责任、权限、状态或恢复边界，再同步总体架构和对应专项设计。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

按改动范围执行相关测试；页面、主题和响应式浏览器回归要求见[Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md)。

## 模块设计文档

除前述三份核心说明文档外，以下文档均按对应模块维护，不构成新的项目级说明。

### 部署与工作台

| 文档 | 唯一职责 |
| --- | --- |
| [Chub 正式部署包与安装设计](docs/CHUB_DEPLOYMENT_PACKAGE_DESIGN.md) | 正式部署包、随包插件 ZIP、新设备安装与可选 OpenClaw 接入边界 |
| [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md) | 工作台与设置页的前端分层、公共交互、主题、文字大小与视觉 Token 契约 |

### AI Runtime

| 文档 | 唯一职责 |
| --- | --- |
| [Chub AI Runtime 架构设计](docs/CHUB_AI_RUNTIME_DESIGN.md) | Runtime 共享契约、能力矩阵、Adapter/Runner 边界与多 Runtime 接入判定 |
| [Chub AI Runtime 插件模块设计](docs/CHUB_RUNTIME_PLUGIN_DESIGN.md) | Runtime 插件 ZIP 协议、安装/替换/移除、双端注册确认和模块状态清理边界 |
| [Chub Codex Runtime 设计](docs/CHUB_CODEX_RUNTIME_DESIGN.md) | 当前 Codex Runtime 的专属边界、Codex/OpenAI 用量来源、接口、缓存和展示口径 |

### 任务执行

| 文档 | 唯一职责 |
| --- | --- |
| [Chub 任务编排插件模块架构设计](docs/CHUB_TASK_ORCHESTRATION_PLUGIN_DESIGN.md) | 任务编排插件模块的通用执行面、检查点、版本绑定和生命周期边界 |
| [Chub 微信任务编排插件模块设计](docs/WEIXIN_TASK_ORCHESTRATION_PLUGIN_DESIGN.md) | 微信已验证任务正文的内置/插件分流、首个插件范围、阶段目标和验收 |
| [Chub Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md) | Chub Session、Native Session 数据消费与映射、Activity、usage 投影、入口、操作、槽位和单 writer 语义 |
| [Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |

### 工作台业务模块

| 文档 | 唯一职责 |
| --- | --- |
| [Deliveryline 需求交付管理平台设计](docs/DELIVERYLINE_PLATFORM_DESIGN.md) | 定义 Deliveryline 的需求交付阶段、领域规则方向与 Chub 的职责边界。 |

### 外部集成与专项能力

| 文档 | 唯一职责 |
| --- | --- |
| [OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md) | OpenClaw/微信端到端业务、插件定制、Context Token、身份、路由和通知边界 |
| [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) | 飞书资料准备、确认门禁、周报生成和复核 |
| [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md) | 仓库内维护的 Chub 插件协议、源码、构建、部署和协议验收 |

日常了解项目先阅读本文和总体架构；确认当前可用能力时阅读能力清单；实现、排障或验收时进入对应模块设计文档。历史资料仅用于追溯，位于 `docs/archive/`。

## 独立学习资料

| 文档 | 职责 |
| --- | --- |
| [本机大模型部署设计](docs/LOCAL_LLM_LEARNING_DEPLOYMENT_DESIGN.md) | 独立于 Chub 的 Ollama 本机模型学习环境；不代表已接入 Chub Runtime 或任务执行能力 |
