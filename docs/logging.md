# 日志与故障定位

结构化日志用于回答“哪个入口、哪个请求或任务、哪个阶段、哪个源码位置失败”。日志与业务数据分开保存，不把整部正文或模型提示词作为常规日志内容。

## 默认路径与保留

结构化事件位于当前数据目录的 `logs/events.jsonl`；轮转文件为 `events.1.jsonl` 至 `events.4.jsonl`。单文件上限 1,000,000 字节，写入时清理最后修改超过 14 天的日志文件；不是逐事件精确到期。一行一个 JSON 对象，时间使用 UTC。

| 系统／配置 | 结构化事件位置 |
|---|---|
| Windows 默认 | `%LOCALAPPDATA%\InkReference\logs\events.jsonl` |
| macOS 默认 | `~/Library/Application Support/InkReference/logs/events.jsonl` |
| 自定义 `--data-dir`／`-DataDir` | `<所选数据目录>/logs/events.jsonl` |

桌面启动失败也写入同一数据目录的结构化事件，并在终端或错误页显示诊断 ID；窗口尚未建立时可按 ID 查找日志。打包启动输出另使用 `desktop.log`，单文件 500000 字节加两份轮转，同样清理超过 14 天的文件；它不是第二份完整业务日志。旧版本 macOS 的 `~/Library/Logs/InkReference` 目录可能仍保留历史记录，本轮使用上表的数据目录。

修复原字节备份、操作记录和近期故障摘要位于数据目录 `quick-repairs/`。它们用于修复回滚，与事件日志的轮转、正文历史和完整项目备份不是同一种保留策略。

## 关联字段

| 字段 | 用途 |
|---|---|
| `timestamp`、`level` | 发生时间和严重程度。 |
| `component`、`operation` | 业务模块和具体动作。 |
| `status`、`stage` | 任务结果和发生阶段。 |
| `request_id` | 当前 HTTP 请求；响应头为 `X-Request-ID`。 |
| `task_id` | 后台任务、提取或修复操作，跨多个请求关联。 |
| `diagnostic_id` | 故障定位标识；4xx／5xx 响应头为 `X-Diagnostic-ID`。 |
| `duration_ms`、`http_status` | 耗时和 HTTP 结果。 |
| `error_type`、`frames`、`source` | 异常类型及文件／函数／行号；不保存局部变量或异常参数。 |

HTTP 路径使用应用路由信息，不记录 URL 查询值、请求体或请求头。前端记录有界的请求失败、JavaScript 错误和栈帧，可随快捷修复诊断一并核对。后台任务另外保留各自状态与业务事件，任务 ID 用于从页面跳回对应进程。

已接入的后台位置包括提取运行／切块／JSON／Schema／证据与入库、模型请求及连接测试、联网资料研究、搜索服务、来源读取、分卷整理、上传、EPUB／PDF 转换、语义 embedding、模型下载及能力验证、桌面服务／原生窗口，以及修复生成／应用／回滚。来源读取的可恢复失败记为 WARNING，不采集 URL、查询或网页正文。失败记录尽量沿同一任务和阶段定位；代码帧不等于程序能判断所有业务根因，仍需结合复现步骤与业务候选核对。

## 使用顺序

1. 保留页面报错中的诊断 ID，记录当时的作品、任务及操作步骤。
2. 打开“快捷修复”，填写复现步骤、实际结果和预期结果，生成诊断。
3. 检查诊断中的近期错误、请求／任务 ID、源码位置、环境与代码版本。只有用户选择自动模式或复制提示词后，相关诊断才交给模型。
4. 结果返回后预览、修改并确认；源码修复完成后复测原来的操作。检查通过只说明语法／类型／构建一致，不保证逻辑正确。

命令行可直接按标识检索日志。PowerShell：

```powershell
$logDir = Join-Path $env:LOCALAPPDATA 'InkReference\logs'
Get-ChildItem $logDir -Filter 'events*.jsonl' | Select-String -SimpleMatch '复制的诊断ID'
```

macOS：

```bash
rg --fixed-strings '复制的诊断ID' "$HOME/Library/Application Support/InkReference/logs" -g 'events*.jsonl'
```

使用自定义数据目录时替换路径；普通用户无需安装 `rg`，可在编辑器中查找同一 ID。环境问题先运行 `python scripts/manage.py doctor --json`，并核对它检查的解释器是否与启动环境一致。

## 采集边界

常规日志不记录模型 Key、Authorization、Cookie、请求体、提示词、正文、模型回复、异常局部变量或异常参数。诊断提示词可包含用户主动输入的复现描述和有界相关源码，发送前可预览；完整业务候选仍保存在本地业务数据库，不被当作日志复制。

日志写入失败不应使业务操作再次失败；数据目录无权限或磁盘空间不足时，先修复文件系统问题。复制日志不等于项目备份。需要完整恢复时使用“备份与恢复”的 ZIP，不能只复制日志或单个运行中的 SQLite 文件。
