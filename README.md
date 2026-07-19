# Hub

Hub 是一个面向个人设备的轻量管理服务。第一阶段使用同一套 Python 工程分别运行在 macOS 和 Ubuntu 上。

当前进入 M5 验收：提供移动端优先的 Web 管理页面，以及 Bearer Token 保护的
节点状态、白名单任务和安全日志查看接口。

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

可以使用 Python 生成随机 Token：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

第一阶段除健康检查外不提供关闭认证的运行模式。不要将 Hub 直接暴露到公网；跨设备访问优先使用可信局域网、VPN 或虚拟组网。

默认使用 `config/settings.yaml`，也可以指定其他节点配置：

```bash
export HUB_CONFIG_FILE="/absolute/path/to/settings.yaml"
```

启动后访问：

```text
http://127.0.0.1:8080/
http://127.0.0.1:8080/api/health
```

管理页面和健康检查不需要 Token；页面骨架不包含节点数据。节点状态、任务
列表、任务执行和日志接口均受 Token 保护。页面默认仅在当前浏览器会话保存
Token，只有勾选“在此设备记住 Token”后才会长期保存。

节点状态接口需要 Bearer Token：

```bash
curl \
  -H "Authorization: Bearer ${HUB_TOKEN}" \
  http://127.0.0.1:8080/api/status
```

未设置 `HUB_TOKEN` 时服务仍可启动，但受保护接口返回 `503 security_not_configured`。旧配置中的 `security.enabled` 已移除；第一阶段不支持关闭认证。

查看当前平台任务并执行一个任务：

```bash
curl \
  -H "Authorization: Bearer ${HUB_TOKEN}" \
  http://127.0.0.1:8080/api/tasks

curl \
  -H "Authorization: Bearer ${HUB_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"task":"check_system","params":{}}' \
  http://127.0.0.1:8080/api/tasks/run
```

查看当前 Hub 活动日志的最后 50 行：

```bash
curl \
  -H "Authorization: Bearer ${HUB_TOKEN}" \
  "http://127.0.0.1:8080/api/logs?lines=50"
```

## 测试

```bash
pytest
```

## 文档

- [第一阶段 PRD](docs/PRD_PHASE_1.md)
- [第一阶段技术架构](docs/ARCHITECTURE_PHASE_1.md)
- [第一阶段任务清单](docs/TASKS_PHASE_1.md)
