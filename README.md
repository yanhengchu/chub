# Hub

Hub 是一个面向个人设备的轻量管理服务。第一阶段使用同一套 Python 工程分别运行在 macOS 和 Ubuntu 上。

第一阶段核心功能与开发目录服务化运行已通过 macOS、Ubuntu 双平台验收。
项目提供移动端优先的 Web 管理页面，以及 Bearer Token 保护的节点状态、
白名单任务和安全日志查看接口。

## 环境要求

- Python 3.12 及以上

## 本地运行

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# 首次运行时，将 .env 中的 HUB_TOKEN 替换为固定的长随机 Token
chmod 600 .env
```

macOS 创建本机节点配置：

```bash
cp config/settings.macos.example.yaml config/settings.local.yaml
```

Ubuntu 创建本机节点配置：

```bash
cp config/settings.ubuntu.example.yaml config/settings.local.yaml
```

从仍使用旧版 `config/settings.yaml` 的部署升级时，应先根据当前平台创建
`config/settings.local.yaml`，再检查其中的节点名称、ID 和平台类型。旧文件
在本版本中已由两个平台模板替代。

启动服务：

```bash
.venv/bin/python main.py
```

长期在当前开发目录运行时，可安装当前用户的后台服务：

```bash
./scripts/chub install
```

首次安装会在 `~/.local/bin/chub` 创建指向当前工作区的命令链接。如果该目录
尚未加入 `PATH`，安装命令会给出提示。Bash 用户可以将其持久加入
`~/.bashrc`：

```bash
case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
```

修改后在当前终端执行一次 `source ~/.bashrc`，后续新终端会自动生效。确认
`command -v chub` 能够找到命令后，即可从任意目录管理：

```bash
chub start
chub stop
chub restart
chub status
chub logs
chub uninstall
```

macOS 使用 LaunchAgent，Ubuntu 使用 systemd user service；两者均在当前用户
登录后启动。服务直接依赖当前工作区和 `.venv`，移动项目目录后需要重新安装。
源码、依赖或配置更新完成后执行 `chub restart`。卸载只移除用户服务和命令
链接，不删除项目、本机配置或日志。`chub logs` 跟踪节点配置指定的活动日志；
macOS 服务管理器的标准输出和错误日志超过 2 MB 后，会在下次启动时保留一份
备份并重新开始记录。

本地只验证健康检查时可以不设置 `HUB_TOKEN`。服务会输出安全警告并继续启动；后续受保护接口在没有 Token 时保持不可用。连接局域网或虚拟组网前应设置一个足够长的随机 Token。

可以使用 Python 生成随机 Token：

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

项目启动时会自动读取根目录 `.env` 和 `config/settings.local.yaml`。系统中
已经存在的环境变量优先，不会被 `.env` 覆盖。每台设备维护自己的这两个本地
文件，它们均已被 Git 忽略：

```dotenv
HUB_TOKEN=replace-with-a-long-random-token
# 可选；默认就是 config/settings.local.yaml
# HUB_CONFIG_FILE=config/settings.local.yaml
```

`HUB_CONFIG_FILE` 可以是绝对路径，也可以是相对项目根目录的路径。`.env.example`
和两个平台的 YAML 示例只保留占位或非敏感内容。macOS 和 Ubuntu 上的真实
`.env` 建议限制为仅当前用户可读：

```bash
chmod 600 .env
```

第一阶段除健康检查外不提供关闭认证的运行模式。不要将 Hub 直接暴露到公网；跨设备访问优先使用可信局域网、VPN 或虚拟组网。

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
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest
```

## 文档

- [第一阶段产品需求](docs/PRD_PHASE_1.md)
- [第一阶段技术架构](docs/ARCHITECTURE_PHASE_1.md)
- [第一阶段任务清单](docs/TASKS_PHASE_1.md)
- [第一阶段验收记录](docs/ACCEPTANCE_PHASE_1.md)
