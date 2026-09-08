# Chub

## 本文职责

本文是 Chub 的项目入口，负责说明项目定位、当前能力概览、最小启动方式、常用维护入口、数据与安全摘要，以及权威文档导航。

本文不负责定义系统分层、状态所有权、服务恢复、Runtime 契约、任务执行、外置模块、微信指令、插件协议或页面交互规则；这些内容只在对应的专项文档中维护。需要确认“当前能调用什么”时，以[Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准；历史资料不覆盖当前契约。

## 项目介绍

Chub 是面向个人设备、本地优先的轻量 AI 工作站控制面。它协调受信入口、设备能力、AI Session、后台任务、自动化、通知和最终状态；Chub 本身不是模型，也不作为通用对话 Agent 执行任务。

当前 AI 能力由后端固定注册的本机 Runtime 提供，现有部署实例为 Codex。Quick Worker 承载跨 Web 重启继续运行的后台 AI 任务；OpenClaw 在微信链路中承担可信消息网关和通道适配。Chub 支持 macOS LaunchAgent 与 Ubuntu systemd user service。

Chub 按维护者授信的个人工作站运行：优先保证本地可用、局部恢复和最终状态可确认，不按多用户平台或零信任插件市场设计。此定位不放宽外部入口边界，微信、OpenClaw 和远程浏览器仍只能通过固定身份、路由和受控能力访问。

## 当前能力

| 功能域 | 当前可用能力 | 主要入口 |
| --- | --- | --- |
| 设备与维护 | 查看节点状态、执行白名单维护任务、查看受限日志 | 工作台、设置、日志页、`chub` CLI |
| AI Runtime 与会话 | 使用 Codex Runtime 创建实时终端或快速交互 Session，查看用量与任务结果 | 工作台、Session 页、微信 ClawBot |
| 任务执行 | 通过独立 Quick Worker 执行页面、微信和翻译快速任务 | 快速交互页、微信 ClawBot |
| 需求储备 | 管理 R1-R9 轻量需求 | `chub` CLI、微信 ClawBot |
| 自动化与周报 | 使用受管 Debug Chrome 运行固定自动化，准备并生成周报 | 自动化页、周报页、命令行 |
| 外部集成与通知 | 接入 OpenClaw/微信 ClawBot，并向预配置飞书目标发送通知 | 设置页、微信 ClawBot、OpenClaw Tool、CLI |
| 项目资料与外观 | 浏览已登记的项目资料，切换主题和文字大小 | 工作台、设置页 |

任务编排目前仍由主项目提供；其外置目标尚未接入当前能力，见[Chub 任务编排外置设计](docs/CHUB_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md)。

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

日常维护使用 `chub status`、`chub check`、`chub restart`、`chub logs` 和 `chub version`。完整 CLI、Worker 操作、通知命令、平台差异及影响范围以[集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md)为准。

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
- **专项设计**：本领域的当前行为、边界、维护规则和验收范围。
- **当前能力契约**：当前 CLI、插件、固定 API 和微信固定指令。
- **维护与归档资料**：插件构建部署、异常恢复和历史追溯。

当前文档的登记、摘要和状态以 `docs/design_documents.json` 为准。专项设计顶部统一声明状态、主要读者、本文负责和本文不负责；已验收文档还须保留验证范围、未承诺范围和复检触发条件。“第一阶段已验收”只表示该文档定义的第一阶段已通过验收，不代表所有长期目标完成。

## 测试

```bash
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

按改动范围执行相关测试；页面、主题和响应式浏览器回归要求见[Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md)。

## 核心项目文档

### 项目基础与当前契约

| 文档 | 唯一职责 |
| --- | --- |
| [Chub 项目说明](README.md) | 项目概览、启动与维护入口、安全摘要和文档导航 |
| [Chub 总体架构设计](docs/CHUB_ARCHITECTURE_DESIGN.md) | 核心、AI Runtime 与第三方服务三层架构、状态所有权和跨模块约束 |
| [Chub 集成能力清单](docs/CHUB_INTEGRATION_CAPABILITIES.md) | 当前可用命令、插件、固定 API，以及微信固定指令唯一产品契约 |

### AI Runtime

| 文档 | 唯一职责 |
| --- | --- |
| [Chub AI Runtime 架构设计](docs/CHUB_AI_RUNTIME_DESIGN.md) | Runtime 共享契约、能力矩阵、Adapter/Runner 边界与新增 Runtime 实现规范 |
| [Chub AI Runtime 外置模块功能设计](docs/CHUB_EXTERNAL_MODULE_DESIGN.md) | Runtime ZIP 协议、安装/替换/移除、双端注册确认和模块状态清理边界 |
| [Chub Codex Runtime 设计](docs/CHUB_CODEX_RUNTIME_DESIGN.md) | 当前 Codex Runtime 的专属边界、Codex/OpenAI 用量来源、接口、缓存和展示口径 |

### 任务执行

| 文档 | 唯一职责 |
| --- | --- |
| [Chub 任务编排外置设计](docs/CHUB_TASK_ORCHESTRATION_EXTERNALIZATION_DESIGN.md) | 任务编排模块的目标职责、受控任务计划、版本快照和后续接入验收边界 |
| [Chub Session 状态模型设计](docs/AI_SESSION_STATE_DESIGN.md) | Chub Session、Native Session 数据消费与映射、Activity、usage 投影、入口、操作、槽位和单 writer 语义 |
| [Chub Quick Worker 独立服务设计](docs/CHUB_QUICK_WORKER_DESIGN.md) | Quick Worker 独立服务、非实时任务、恢复、通知终态和重启协调 |

### 专项能力与外部集成

| 文档 | 唯一职责 |
| --- | --- |
| [OpenClaw 定制集成设计](docs/OPENCLAW_CUSTOMIZATION_DESIGN.md) | OpenClaw/微信端到端业务、插件定制、Context Token、身份、路由和通知边界 |
| [本期工作周报自动化与生成设计](docs/WEEKLY_REPORT_AUTOMATION_DESIGN.md) | 飞书资料准备、确认门禁、周报生成和复核 |
| [Chub 前端 UI 模块化设计](docs/FRONTEND_UI_DESIGN.md) | 前端分层、公共交互、主题、文字大小注册与视觉 Token 契约 |
| [Chub OpenClaw 插件说明](integrations/openclaw/chub/README.md) | 仓库内维护的 Chub 插件协议、源码、构建、部署和协议验收 |

日常了解项目先阅读本文和总体架构；确认当前可用能力时阅读能力清单；实现、排障或验收时进入对应专项设计。历史资料仅用于追溯，位于 `docs/archive/`。

## 独立学习资料

| 文档 | 职责 |
| --- | --- |
| [本机大模型部署设计](docs/LOCAL_LLM_LEARNING_DEPLOYMENT_DESIGN.md) | 独立于 Chub 的 Ollama 本机模型学习环境；不代表已接入 Chub Runtime 或任务执行能力 |
