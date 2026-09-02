# 本机大模型部署设计

> 状态：已验收
> 主要读者：本机维护者；AI Agent 用于评估、准备和验收本机模型学习环境。
> 本文负责：当前 MacBook 上的本地模型运行器、模型选择、资源边界、学习阶段、安全约束和验收标准。
> 本文不负责：将本地模型接入 Chub、替代 Codex Runtime、开放远程模型服务，或定义微信、OpenClaw、Quick Worker 的调用协议。

## 0. AI Agent 快速理解

本方案是独立于 Chub 的本机学习环境，不是新的 Chub Runtime：

1. 当前目标设备为 Apple M2、16GB 统一内存、251GB SSD，已下载两个基线模型后现有可用磁盘约 32GB；同一时间只运行一个本地模型。
2. 已安装 Ollama 0.33.0 并确认其仅监听本机 loopback。它负责下载、存储、命令行交互和仅本机使用的 API；模型由 Ollama 按固定标签下载，不手工混用其他格式的权重。标签不是不可变版本，首次下载后必须登记实际模型 ID 和量化信息。
3. 基线模型 `qwen3:4b` 与 `deepseek-r1:8b` 已下载完成；后者是当前 DeepSeek-R1 的 Qwen3 8B 蒸馏版本。实际模型目录占用约 7.2GB。`gemma3:4b` 仅在需要图片理解学习时下载。
4. 不部署 32B、70B、完整 DeepSeek-R1 或 Llama 4；14B 只可短时体验，不作为日常运行目标。
5. 本阶段不修改 Chub 配置、代码、服务定义、Quick Worker、OpenClaw 或微信路由。未来接入必须另立需求，设计固定 loopback 调用、模型状态、容量、超时和失败关闭边界。

## 1. 目标与非目标

目标是建立一个稳定、可撤销的本机模型实验环境，用于学习：

- 模型下载、启动、停止和删除。
- 中文问答、代码解释、推理和多模态的能力差异。
- 命令行交互与本机 API 的基本调用方式。
- 模型尺寸、量化、上下文长度、响应速度、内存压力和磁盘占用之间的取舍。

非目标：

- 不追求“满血”模型或生产吞吐量。
- 不承诺与云端商业模型相同的质量、速度或上下文能力。
- 不同时常驻多个模型，不提供多人、跨设备、Tailnet 或局域网访问。
- 不自动读取项目代码、Chub 数据、浏览器资料、微信内容或其他本机文件作为模型上下文。

第一阶段以通用模型学习为主：先用较轻的 `qwen3:4b` 建立运行器、命令行和本机 API 基础，再用 `deepseek-r1:8b` 对照推理过程。若目标改为只学习 DeepSeek，可跳过 Qwen，但仍先遵守第 3 节的监听验证和第 4.1 节的记录规则。

## 2. 资源评估与模型边界

Apple Silicon 的 CPU、GPU 和内存共享 16GB 统一内存。本机浏览器、Chub、Quick Worker 和模型会竞争这一资源，因此模型下载体积不是运行内存的充分条件。

| 模型 | 角色 | 模型文件约占用 | 本机结论 |
| --- | --- | ---: | --- |
| `qwen3:4b` | 日常中文、代码、总结和普通对话 | 2.5GB | 已完成本机启动、调用和停止验收 |
| `deepseek-r1:8b` | 推理过程、数学和代码问题拆解 | 5.2GB | 已完成本机启动、调用和停止验收 |
| `gemma3:4b` | 图片理解与多模态实验 | 3.3GB | 有需求再下载 |
| `qwen3:8b` | 更高质量的通用对话 | 5.2GB | 可替换 4B，不与 4B 长期并存 |
| 14B | 短时对比 | 约 9GB | 可能触发内存交换，不作为日常目标 |
| 32B 及以上 | 大模型体验 | 20GB 起 | 不部署 |

所有基线模型下载后，磁盘应至少保留约 20GB 可用空间。运行时从短提示词和不超过 8K 的上下文开始；出现明显卡顿、内存压力或频繁交换时，先缩短上下文或停止其他高内存应用，而不是继续提高模型尺寸。

## 3. 运行器、版本与网络边界

第一阶段统一使用 Ollama：

- 由它下载模型、保存模型文件、加载单个模型并提供命令行和本机 API。
- 本地推理不依赖云端模型 API。外网访问只允许发生在运行器安装、显式模型下载或维护者主动更新时；不得因本地推理而配置第三方云端推理凭据。
- 保持服务仅本机访问；不配置局域网、Tailnet、端口转发或反向代理。不得把 `OLLAMA_HOST` 配置为非 loopback 地址。
- 模型提示词、输出和本机 API 调用只在本机实验范围内使用；它们仍可能包含维护者自行输入的敏感内容，因此不得把凭据、Cookie、Token、私钥或生产数据提交给模型。

首次安装和每次修改运行器网络设置后，维护者必须确认监听地址仍是 `127.0.0.1` 或 `::1`。默认服务端口为 `11434`；若运行器改用其他端口，验收记录必须同时登记实际端口和监听地址。局域网或 Tailnet 设备无法连接本机模型 API 才视为网络边界通过。

每次下载或更新模型时，验收记录必须保存：模型标签、Ollama 列出的模型 ID、量化格式、下载大小、下载日期和 Ollama 版本。后续同一标签更新后，必须作为新一轮对比，不得把旧记录与新模型混为同一次结果。

当前本机记录（2026-09-01 核验，下载完成于 2026-08-31 至 2026-09-01）：

| 模型 | Ollama 模型 ID | 参数规模 | 量化 | 上下文 | 状态 |
| --- | --- | ---: | --- | ---: | --- |
| `qwen3:4b` | `359d7dd4bcda` | 4.0B | `Q4_K_M` | 262144 | 已验收 |
| `deepseek-r1:8b` | `6995872bfe4c` | 8.2B | `Q4_K_M` | 131072 | 已验收 |

Ollama 当前版本为 0.33.0，服务已启动并只监听 `127.0.0.1:11434`；两个模型当前均未加载到内存。模型元数据均声明文本生成、工具调用和思考能力。目录占用约 7.2GB，仍保留约 32GB 磁盘空间，满足第 2 节的空间边界。

### 3.1 维护者操作流程

以下命令只管理本机模型环境，不操作 Chub、Quick Worker、OpenClaw 或微信。模型提示词不得包含凭据、Cookie、Token、私钥、真实项目日志或生产数据。

#### 安装与服务

当前 macOS 环境通过 Homebrew 安装和托管 Ollama。首次安装执行：

```bash
brew install ollama
brew services start ollama
ollama --version
brew services list | awk '$1 == "ollama"'
curl --fail --silent --show-error http://127.0.0.1:11434/api/version
lsof -nP -iTCP:11434 -sTCP:LISTEN
```

最后一条必须只显示 `127.0.0.1:11434` 或 `[::1]:11434`。服务已由 `brew services` 托管时，不要在另一个终端重复执行 `ollama serve`；只有未安装用户级服务、并且维护者明确希望前台调试时才直接运行该命令。

#### 下载、恢复与核验模型

下载会在网络中断后保留已完成分块；重复执行同一个 `pull` 会继续下载，不会重新下载完整模型。当前基线模型的命令为：

```bash
ollama pull qwen3:4b
ollama pull deepseek-r1:8b
ollama list
ollama show qwen3:4b --verbose
ollama show deepseek-r1:8b --verbose
```

`ollama list` 中出现模型标签、模型 ID 与完整大小后，才代表该模型可加载；下载中的部分文件不等于模型可用。核验时记录模型 ID、参数规模、量化格式、上下文长度、Ollama 版本、下载日期和磁盘大小；同一标签更新后必须重新记录。

#### 启动模型与交互调用

模型不需要常驻启动。执行 `ollama run` 时会按需加载模型，退出后 Ollama 会在一段空闲时间后卸载，或可由 `ollama stop` 立即卸载。

```bash
ollama run qwen3:4b
ollama run deepseek-r1:8b
ollama run qwen3:4b "用三条要点解释递归。"
```

前两条进入终端交互；输入问题后等待回答，输入 `/bye` 或按 `Ctrl-D` 退出。单次调用适合重复性测试和脚本。单次只运行一个模型；切换模型前先执行 `ollama stop <模型标签>`。

本机 API 默认地址为 `http://127.0.0.1:11434/api`。下面的请求会等待完整 JSON 结果并限制为 120 秒，适合记录最终耗时：

```bash
curl --fail --silent --show-error --max-time 120 \
  http://127.0.0.1:11434/api/generate \
  -d '{
    "model": "qwen3:4b",
    "prompt": "用一句话解释什么是递归。",
    "stream": false,
    "think": false,
    "keep_alive": "2m"
  }' | jq '{done, response, total_duration, load_duration, prompt_eval_count, eval_count, eval_duration}'
```

`think:false` 是请求意图，不是所有模型的强制保证。当前 `qwen3:4b` Thinking 变体仍会输出 `<think>` 内容；验收时必须按实际输出和耗时判断，不能假设该字段一定隐藏推理内容。

### 3.2 常用本机 API

以下 API 仅用于本机开发、学习或受控脚本。基础地址固定为 `http://127.0.0.1:11434/api`；当前服务没有对局域网、Tailnet 或互联网开放。`curl` 示例中的 `jq` 只用于筛选返回字段，省略管道仍可查看完整 JSON。

| 用途 | 方法与路径 | 关键结果 | 使用边界 |
| --- | --- | --- | --- |
| 服务版本 | `GET /version` | `version` | 健康与版本核验 |
| 已下载模型 | `GET /tags` | 标签、大小、量化、参数规模 | 不加载模型 |
| 已加载模型 | `GET /ps` | 内存占用、上下文、到期时间 | 空数组表示无模型加载 |
| 模型详情 | `POST /show` | 架构、参数、量化、模板与能力 | 用于记录模型身份 |
| 单轮生成 | `POST /generate` | `response`、`thinking`、终态与耗时 | 适合固定测试和单次任务 |
| 多轮对话 | `POST /chat` | `message`、终态与耗时 | 调用方负责保存消息历史 |
| 卸载模型 | `POST /generate` | `keep_alive: 0` | 仅释放内存，不删除模型文件 |

服务、已下载模型与当前加载模型的查询：

```bash
curl --fail --silent http://127.0.0.1:11434/api/version
curl --fail --silent http://127.0.0.1:11434/api/tags \
  | jq '.models[] | {name, size, parameter_size: .details.parameter_size, quantization: .details.quantization_level}'
curl --fail --silent http://127.0.0.1:11434/api/ps \
  | jq '.models[] | {name, size, size_vram, context_length, expires_at}'
```

读取指定模型详情：

```bash
curl --fail --silent http://127.0.0.1:11434/api/show \
  -d '{"model":"deepseek-r1:8b"}' \
  | jq '{details, model_info, capabilities}'
```

单轮生成以 `stream:false` 获取一个完整 JSON 终态。`done: true` 才代表本次请求完成；`response` 是最终回答，支持的模型可将思考内容单独放在 `thinking`。`total_duration`、`load_duration`、`prompt_eval_duration` 与 `eval_duration` 均为纳秒：

```bash
curl --fail --silent --show-error --max-time 120 \
  http://127.0.0.1:11434/api/generate \
  -d '{
    "model": "deepseek-r1:8b",
    "prompt": "用一句话解释什么是递归。",
    "stream": false,
    "think": false,
    "keep_alive": "2m"
  }' \
  | jq '{done, done_reason, response, thinking, total_duration, load_duration, prompt_eval_count, prompt_eval_duration, eval_count, eval_duration}'
```

多轮对话不由 Ollama 自动保存历史；调用方必须在每次请求中传入需要保留的非敏感 `messages`。不要把 Chub、浏览器、微信或其他真实业务数据作为消息内容：

```bash
curl --fail --silent --show-error --max-time 120 \
  http://127.0.0.1:11434/api/chat \
  -d '{
    "model": "qwen3:4b",
    "messages": [
      {"role":"system","content":"你是一个简洁的编程学习助手。"},
      {"role":"user","content":"解释递归的终止条件。"}
    ],
    "stream": false,
    "think": false,
    "keep_alive": "2m"
  }' \
  | jq '{done, done_reason, message, total_duration, load_duration, eval_count, eval_duration}'
```

需要立即释放模型内存时，优先使用 CLI 的 `ollama stop <模型标签>`。纯 API 调用可对该模型发送一个空提示词并设置 `keep_alive: 0`：

```bash
curl --fail --silent --show-error http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3:4b","prompt":"","keep_alive":0}'
curl --fail --silent http://127.0.0.1:11434/api/ps
```

API 的 `tools`、本地文件读取、网页搜索或外部访问不是当前部署能力。模型可能声明工具调用能力，但只有调用方明确提供受控工具并自行处理结果时才会发生工具调用；本机 Ollama 不会自行联网或执行模型输出。

#### 查看运行、性能与资源指标

模型加载或生成期间，依次查看以下指标：

```bash
ollama ps
ps -p "$(pgrep -xo ollama)" -o pid=,pcpu=,rss=,etime=,command=
memory_pressure -Q
vm_stat | head -12
du -sh "$HOME/.ollama/models"
df -h /System/Volumes/Data | tail -1
```

`ollama ps` 是模型加载状态的权威检查：记录模型名称、`SIZE`、`PROCESSOR`、`CONTEXT` 和自动卸载时间；空列表代表当前没有模型加载。Apple Silicon 下 `PROCESSOR` 显示 `100% GPU` 表示主推理使用 Metal GPU，但仍与 CPU 共用 16GB 统一内存。API 最终 JSON 的 `total_duration`、`load_duration` 和 `eval_duration` 单位为纳秒；可分别除以 `1e9` 换算秒，`eval_count / (eval_duration / 1e9)` 可作为近似生成速度。`memory_pressure -Q`、`vm_stat` 和磁盘可用空间用于记录系统是否发生内存压力或空间不足。

#### 停止、清理与失败恢复

```bash
ollama stop qwen3:4b
ollama ps
ollama rm qwen3:4b
ollama list
```

`stop` 只卸载当前模型，不删除下载文件；`rm` 会删除指定模型，应只在完成对比且确认不再需要时执行。请求超过 120 秒、返回错误、`done` 不为 `true`、模型停止后仍显示在 `ollama ps`、或内存压力持续异常时，本次测试不通过：先停止模型，再记录响应/错误和运行指标；不得重启或修改 Chub 来处理模型问题。

只有确认模型列表、模型详情和监听地址符合本节规则后，才开始交互测试。删除已完成对比且不再使用的模型后，再用 `ollama list` 确认磁盘清理结果。

LM Studio 可作为图形界面对照工具，MLX-LM 与 llama.cpp 可在后续学习量化或 Apple Silicon 优化时单独尝试；它们不与 Ollama 共用模型目录或缓存，也不应在第一阶段重复下载同一模型。

## 4. 分阶段部署与学习

### 4.1 第一阶段：建立最小可用环境

1. 安装 Ollama，并确认其命令行和本机服务可用。
2. 下载并运行 `qwen3:4b`。
3. 分别完成中文问答、代码解释和短文本总结。
4. 记录模型文件占用、模型 ID、量化格式、Ollama 版本、实际监听地址和运行时系统内存压力。

第一阶段使用固定的非敏感样本：一段 150 至 300 字的中文总结、一段不含业务代码的 Python 函数解释，以及一道基础逻辑题。每项记录首字等待、总耗时、输出是否完整、磁盘余量和运行时是否出现内存压力；不使用真实项目代码、日志、账号信息或生产资料。

通过条件：模型以显式关闭思考模式的本机 API 请求，在 120 秒内完成三类固定样本；监听地址满足第 3 节；系统、浏览器和 Chub 保持可用；磁盘保留空间满足第 2 节要求。完成后 `ollama stop qwen3:4b` 必须能释放模型内存。出现请求未完成、持续内存压力、明显交换或浏览器/Chub 无法正常响应时，本阶段不通过。

当前结果：模型文件、GPU 加载和 loopback 服务均已通过。维护者已对 `qwen3:4b` 与 `deepseek-r1:8b` 完成本机启动、调用与停止验收，确认日常使用没有发现问题。Qwen 的最小本机 API 请求在 `think:false`、`stream:false` 下于约 60.9 秒返回 `done: true` 和相关中文回答，模型停止后可正常卸载。该 Qwen Thinking 变体仍可能输出 `<think>` 内容，未完全遵守关闭思考输出的预期；这是已知模型行为限制，不阻断当前部署验收，暂不额外处理。

### 4.2 第二阶段：学习推理模型差异

1. 已确认 `deepseek-r1:8b` 的模型 ID 与第 3 节记录一致。
2. 已完成本机启动、调用和停止的基础验收。
3. 后续需要比较质量、推理输出长度、首字等待、总耗时或内存压力时，使用第一阶段固定样本加一题多步骤推理题重新记录。
4. 保持单次只加载一个模型；比较结束后停止运行模型。

通过条件：可以明确区分通用模型与推理模型的使用场景，并确认 8B 不影响本机日常维护工作。

### 4.3 第三阶段：按需扩展

只有需要图片理解时下载 `gemma3:4b`，并使用非敏感本地测试图片验证描述、文字识别和问答能力。若日常 4B 质量不足，可先用 `qwen3:8b` 替换 `qwen3:4b` 做单模型对比；验证完成后删除不再使用的一个版本。

## 5. 验收与恢复

验收记录至少包含模型标签、模型 ID、量化格式、Ollama 版本、下载大小、下载日期、监听地址和端口；同时记录固定样本的首字等待、总耗时、输出完整性、磁盘余量与内存压力。多模态阶段另记录一张不含敏感信息的测试图片和输出结论。

失败或资源不足时按以下顺序恢复：

1. 停止当前模型，确认系统内存恢复。
2. 缩短上下文并重试，不增加模型尺寸。
3. 删除不再使用的模型，恢复磁盘余量。
4. 如运行器本身异常，停止本机学习环境后排查；不得用重启 Chub、Quick Worker、OpenClaw 或修改其配置来恢复模型运行器。监听地址异常时先停止模型服务、移除非 loopback 配置并重新验证网络边界，再恢复本机实验。

## 6. 后续接入准入

只有维护者明确要求将本地模型接入 Chub 时，才开始新的实现设计。新设计至少要定义：固定模型白名单、本机 loopback 身份边界、模型进程的健康和容量状态、单任务超时与取消、模型输出的大小/敏感信息限制、与 Codex Runtime 的并存规则，以及 Chub 或模型服务重启后的最终状态确认。未完成这些设计前，本机模型不得被微信、OpenClaw 或 Chub 自动化调用。

## 7. 验收范围与复检

当前已完成基础本机部署验收：Ollama 0.33.0 服务健康且只监听 `127.0.0.1:11434`；两个基线模型均已下载、模型元数据与磁盘边界已核验，并已完成维护者的启动、调用和停止验收。已验证范围仅限当前 Apple M2/16GB macOS 设备上的单模型本机使用；不承诺双模型并发、14B 以上模型、局域网开放、联网检索、本地训练或 Chub 集成。模型标签/量化变化、模型服务网络暴露、磁盘容量明显变化、并发策略变化，或任何 Chub 集成需求都会触发重新评估与验收。
