# Hub

Hub 是一个面向个人设备的轻量管理服务。第一阶段使用同一套 Python 工程分别运行在 macOS 和 Ubuntu 上。

当前完成 M1 基础里程碑：配置加载、平台检测、日志初始化、应用启动和健康检查。

## 环境要求

- Python 3.12 及以上

## 本地运行

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export HUB_TOKEN="replace-with-a-long-random-token"
python main.py
```

本地只验证健康检查时可以不设置 `HUB_TOKEN`。服务会输出安全警告并继续启动；后续受保护接口在没有 Token 时保持不可用。连接局域网或虚拟组网前应设置一个足够长的随机 Token。

默认使用 `config/settings.yaml`，也可以指定其他节点配置：

```bash
export HUB_CONFIG_FILE="/absolute/path/to/settings.yaml"
```

启动后访问：

```text
http://127.0.0.1:8080/api/health
```

健康检查不需要 Token。其他受保护能力将在后续里程碑实现。

## 测试

```bash
pytest
```

## 文档

- [第一阶段 PRD](docs/PRD_PHASE_1.md)
- [第一阶段技术架构](docs/ARCHITECTURE_PHASE_1.md)
- [第一阶段任务清单](docs/TASKS_PHASE_1.md)
