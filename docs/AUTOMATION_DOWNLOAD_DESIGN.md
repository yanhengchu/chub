# 配置驱动的网页下载自动化方案

> 状态：方案确认，尚未开始实现。

## 1. 目标

通过配置描述固定网站的页面操作流程，复用已启动的 Debug Chrome 登录状态，完成文件下载、校验和安全落盘。

首版聚焦单网站、单任务、单文件下载。网站差异放在配置中，浏览器连接、下载处理、日志和任务互斥由公共执行器统一负责。

## 2. 核心流程

1. 调度器或命令行触发指定任务。
2. 获取任务锁，避免同一任务并发执行。
3. 检查并连接已启动的 Debug Chrome。
4. 通过 Playwright `session()` 复用现有浏览器上下文和登录状态。
5. 校验任务配置、目标域名和登录状态。
6. 按 `steps` 顺序执行页面操作。
7. 对明确标记 `expect: download` 的步骤使用 `page.expect_download()` 捕获下载。
8. 将文件保存到目标目录下的临时文件。
9. 校验文件非空、大小、扩展名和基础格式。
10. 按冲突策略原子替换或保留目标文件。
11. 更新执行结果并记录运行日志。
12. 释放 Playwright 连接和任务锁，不关闭共享 Debug Chrome。

## 3. 配置示例

网站和下载流程统一配置在 `sites.yaml`：

```yaml
version: 1

sites:
  monthly-report:
    name: 月度报表下载
    enabled: true

    browser:
      session: debug-chrome
      start_url: https://example.com/reports
      allowed_hosts:
        - example.com

    login:
      check:
        selector: "[data-testid='user-avatar']"
        timeout_ms: 10000
      expired_message: 登录状态已失效，请重新登录 Debug Chrome

    steps:
      - action: wait
        selector: "#report-list"
        timeout_ms: 15000

      - action: hover
        selector: "[data-report='monthly']"

      - action: click
        selector: "button.open-download-menu"

      - action: click
        selector: "button.download-pdf"
        expect: download
        timeout_ms: 60000

    output:
      directory: data/automations/monthly-report/downloads
      filename: "monthly-report-{date:%Y-%m}.pdf"
      conflict: replace

    validation:
      non_empty: true
      extensions:
        - .pdf
      min_bytes: 1024

    execution:
      timeout_ms: 120000
      retries: 1
      lock: true
```

## 4. 配置约定

### `browser`

- `session`：首版固定使用 `debug-chrome`。
- `start_url`：任务入口页面。
- `allowed_hosts`：允许访问的域名白名单；跳转或配置超出白名单时终止任务。

### `login`

- `check.selector`：进入任务页面后用于确认登录状态的元素。
- `check.timeout_ms`：登录状态检查超时。
- `expired_message`：登录失效时返回的明确提示。

登录密码、Token 和 Cookie 不写入 YAML，登录状态由独立 Debug Chrome profile 保存。

### `steps`

步骤严格按声明顺序执行。首版支持：

- `goto`：导航到指定地址。
- `wait`：等待元素进入指定状态。
- `hover`：移动鼠标到指定元素。
- `click`：点击元素。
- `dispatch_event`：向元素派发固定名称的浏览器事件。

触发下载的步骤必须显式设置 `expect: download`，执行器不再假定最后一步必然触发下载。首版每个任务只允许一个下载步骤。

配置不允许执行任意 JavaScript、Shell 或 Python。等待优先使用元素状态和页面条件，不使用固定时长休眠作为常规同步手段。

### `output`

- `directory`：任务固定下载目录，必须位于自动化数据根目录内。
- `filename`：最终文件名，允许使用受控的日期变量。
- `conflict`：文件冲突策略；首版支持 `replace`、`skip` 和 `fail`。

浏览器提供的文件名和配置生成的文件名都必须经过路径清理，禁止绝对路径和目录穿越。

### `validation`

- `non_empty`：拒绝空文件。
- `extensions`：允许的文件扩展名。
- `min_bytes`：最小文件大小。

后续可以按实际文件类型增加文件头、MIME 或内容完整性校验，但不在首版预置复杂解析器。

### `execution`

- `timeout_ms`：整个任务的最大执行时间。
- `retries`：失败后的重试次数；下载已经成功落盘后不得重复重试。
- `lock`：是否启用同任务互斥锁，首版默认开启。

## 5. 文件落盘规则

文件处理由公共执行器负责，不作为页面步骤暴露：

```text
捕获下载事件
  → 保存到目标目录内的临时文件
  → 校验非空、大小和类型
  → 应用冲突策略
  → 原子 rename 为最终文件
  → 记录执行结果
```

- 临时文件与目标文件位于同一文件系统，确保 rename 可以原子完成。
- 校验失败或执行异常时清理临时文件。
- 最终文件写入成功后再把任务标记为成功。
- 可在后续通过文件哈希和状态文件实现内容去重。

## 6. 配置与调度边界

`sites.yaml` 只描述网站、页面操作和下载结果，不承载平台定时表达式。

调度配置独立管理，负责定义任务 ID、执行时间和启用状态，再调用统一任务入口。macOS、Ubuntu 和 Windows 的系统调度器只负责触发任务，不直接包含网站操作逻辑。

## 7. 日志与执行结果

每次执行至少记录：

- 任务 ID、开始时间、结束时间和最终状态。
- 当前步骤和失败原因。
- 下载文件名、大小和最终路径。
- 是否发生重试、跳过或覆盖。

日志不得记录 Cookie、Authorization、页面敏感字段或完整浏览器上下文。配置校验错误、登录失效、下载超时、文件校验失败应使用可区分的错误状态。

## 8. 首版范围

包含：

- 复用一个已启动的 Debug Chrome。
- YAML 配置驱动的串行页面步骤。
- Playwright 标准 `Download` 事件。
- 单任务单文件下载。
- 登录状态检查、任务互斥、超时和有限重试。
- 临时文件校验和原子落盘。
- 命令行手动执行，为后续系统定时任务提供统一入口。

暂不包含：

- 验证码和多因素认证自动处理。
- 任意脚本执行。
- 多下载步骤或并行下载。
- PDF 预览器、Blob、接口直链等非标准下载适配。
- 自动启动、停止或接管用户日常 Chrome。
- 跨设备任务编排和集中式任务队列。

## 9. 验收标准

- 配置合法时，可以复用 Debug Chrome 的既有登录状态完成一次真实下载。
- 页面步骤严格按配置执行，下载步骤无需依赖“最后一步”约定。
- 登录失效、元素超时和未触发下载时有明确结果，不产生最终文件。
- 下载内容先写入临时文件，校验通过后才原子生成目标文件。
- 空文件、错误类型、非法路径和越界域名会被拒绝。
- 同一任务重复触发时不会并发执行。
- Playwright 断开后 Debug Chrome 及其中的登录状态仍然保留。
- 日志能够定位失败步骤，且不泄露登录凭据和浏览器敏感状态。

## 10. 后续实现顺序

1. 确定自动化任务目录和 `sites.yaml` 配置模型。
2. 实现配置校验和受控步骤执行器。
3. 接入 Chrome CDP `session()` 和标准下载捕获。
4. 实现临时文件校验、冲突策略、任务锁和结果记录。
5. 用一个真实网站完成手动执行验收。
6. 稳定后再接入各平台系统定时任务。
