# InkReference

Windows 优先的本地写作参考工作台：整理作品、人物和桥段，管理全文及清洗版本，用可定制的 LangGraph 工作流提取剧情与证据，审核后入库。

React 中文界面提供素材库、全文清洗、EPUB／MinerU PDF 转换、分析任务、工作流与 Skills、模型用量和项目备份。可在指定 Conda 环境中打开 pywebview 桌面窗口或浏览器，也提供 Windows 安装包构建入口。

## Windows 安装与启动

先安装 Miniconda、Miniforge 或 Anaconda，然后在仓库根目录的 PowerShell 中执行：

```powershell
.\scripts\install.ps1 -EnvironmentName InkReference
.\scripts\open.ps1 -EnvironmentName InkReference
```

安装会复用指定环境；环境不存在时创建 Python 3.12、pip 和 Node.js 22，随后安装应用依赖并构建前端。已有环境必须使用 Python 3.11–3.13；补装缺失工具时保留原 Python 小版本。Conda 模式不再创建额外 `.venv`，不需要预先 `conda activate` 或修改 `conda init`。下载日志直接展示，阶段进度为不定进度，不估算百分比。

如需使用可选的 MinerU 4 PDF 转换，在同一环境显式安装：

```powershell
.\scripts\install.ps1 -EnvironmentName InkReference -MinerU
```

MinerU 使用独立的 `requirements-mineru.txt`，不会加入基础依赖锁。标准 OCR 首次使用可能下载模型，具体取决于所选后端；安装日志与转换进度会分别展示。EPUB 转换不需要 MinerU。

`EnvironmentName` 默认是 `InkReference`，也可改成其他环境名。默认打开 pywebview 桌面窗口；Windows 需要 Microsoft Edge WebView2 Runtime。若只需要浏览器界面：

```powershell
.\scripts\install.ps1 -EnvironmentName InkReference -BrowserOnly
.\scripts\open.ps1 -EnvironmentName InkReference -Browser
```

启动选项：

```powershell
# 指定端口与数据目录，支持空格和中文路径
.\scripts\open.ps1 -EnvironmentName InkReference -Port 8001 -DataDir "D:\写作资料\InkReference"

# 仅启动本地服务，手动打开 http://127.0.0.1:8000
.\scripts\open.ps1 -EnvironmentName InkReference -Browser -NoOpen
```

启动终端需要保持运行，按 `Ctrl+C` 可停止服务。端口占用时脚本会提示更换端口，不结束其他程序。代码更新后重新执行同一条安装命令即可更新依赖和前端。

脚本支持 Windows PowerShell 5.1。如果当前终端找不到 Conda，可从 Anaconda PowerShell Prompt 运行，或让 `CONDA_EXE` 指向自己的 `conda.exe`。脚本不修改全局执行策略；若策略阻止脚本，可仅为这次子进程使用：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -EnvironmentName InkReference
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\open.ps1 -EnvironmentName InkReference
```

安装使用 `requirements-lock.txt` 约束 Python 依赖；默认安装 `.[dev,desktop]`，`-BrowserOnly` 使用 `.[dev]`。前端存在锁文件时使用 `npm ci`，随后执行生产构建。仓库的 `environment.yml` 描述默认 Conda 环境，推荐使用上面的安装入口完成包含前端的安装。

已有 `.venv` 用户可以继续双击 `scripts\setup.cmd`、`scripts\start.cmd`，或使用 `install.ps1 -LegacyVenv`、`open.ps1 -LegacyVenv`。该模式需要系统安装 Python 3.11–3.13 和 Node.js 22/24；旧 `start.cmd` 默认打开浏览器，新的 `open.ps1` 默认打开桌面窗口，`-Browser` 可回退浏览器。

## Windows 打包

在 Windows 中用同一个 Conda 环境执行：

```powershell
# 可携带的应用目录和 ZIP
.\scripts\package.ps1 -EnvironmentName InkReference

# 同时生成 Setup 安装程序，需要先安装 Inno Setup 6.3 或更高版本
.\scripts\package.ps1 -EnvironmentName InkReference -Installer

# 指定输出位置
.\scripts\package.ps1 -EnvironmentName InkReference -Installer -OutputDirectory "D:\打包结果\InkReference"
```

默认输出为 `release\app\InkReference\InkReference.exe`、`release\InkReference-windows-x64.zip`，安装程序位于 `release\installer`。可携带版需要保留整个应用目录；双击 EXE 默认打开桌面窗口，Windows 仍需 WebView2 Runtime。安装程序建立开始菜单入口，卸载时保留用户数据目录。

打包会收集已构建的前端、LangGraph SQLite 与 pywebview 资源，包含相应运行依赖；MinerU、模型权重和用户资料不放入安装包。MinerU 转换可通过上面的 Conda 源码运行方式使用。

GitHub Actions 工作流 **Build Windows desktop package** 会在相关 PR 中构建，也可手动启动：构建后用 `--no-open` 检查 EXE 的本地 HTTP 服务与界面，并把 ZIP／Setup 放在该次运行的 Artifacts；它不会创建 GitHub Release。该烟雾检查及 fake-window 测试不能替代原生窗口的 Windows 实机验收。

## 数据与导入规则

- 数据默认存放在 `%LOCALAPPDATA%\InkReference`，与源码分开。也可以设置 `INKREFERENCE_DATA_DIR`，或传入 `-DataDir`。
- 数据库启动时自动迁移。真实 CSV、数据库、全文、模型和密钥不提交到仓库；提交的测试仅使用合成数据。
- 以外部 ID 识别记录，同名作品的不同版本分别保存；日期精度、换行、空白和“不确定”等内容保留为字符串。
- 导入先预览，存在校验错误时不会写入。提交在一个事务内执行；重复记录可选择跳过、更新或报错。
- 编辑使用版本检查。存在关联的记录默认禁止直接删除，级联删除需要在界面确认。

一键导出提供两种 ZIP：兼容模式包含 v8 格式的三个 CSV；完整模式为每张业务表生成 CSV，并附格式说明 `manifest.json`。也可以单独导出一张表。CSV 使用 UTF-8 BOM，便于在 Windows Excel 中打开。

素材库的兼容导出保留 v8 的三个 CSV；备份页的“全部业务表”会为素材、全文、转换、Skills、工作流、分析与用量等每张业务表生成 CSV。完整项目 ZIP 另外包含五个 SQLite 数据库、原始导入文件与校验清单，可恢复为独立工作区。会话 API Key 和系统环境变量不进入备份。

## 全文、转换与分析

1. 在“全文与清洗”上传 TXT、Markdown、SRT、DOCX 或 EPUB。原件不可变；编码严格解码，无法确认时选择编码后重新预览。清洗结果作为新版本保存，可以查看章节、疑似乱码、修改差异及原文。默认不删除题外话、疑似编号或故事线索；移除随机 ID 需明确勾选。
2. 在“格式转换”选择 EPUB 或 PDF。EPUB 按书籍 spine 顺序转 Markdown，再生成 TXT。PDF 使用 **MinerU 4**；默认 `flash + txt` 提取已有文字层，扫描件选择 `standard + ocr` 并准备相应模型。转换完成后可下载 `.md`、`.txt`，或把 TXT 导入全文库。原件一直保留。PDF 完整块渲染保留可能被识别为页眉的正文，避免默认渲染省略内容。
3. 在“模型与用量”保存模型配置。Ollama 使用 `http://127.0.0.1:11434`；LM Studio／vLLM 等使用 OpenAI-compatible 服务地址，填写本机实际已部署的模型名称。远程 API 使用其服务地址和模型名；密钥可仅保存于当前会话，或通过 `key_env` 引用进程环境变量。
4. 在“工作流与 Skills”选择声明式 Skill，调整人物／剧情／桥段开关、自定义提示词、字符块上限、重试和 token 预算；保存为新版本。React Flow 画布布局单独保存，提取行为由工作流定义控制。Skill 可上传 JSON、带简单元数据的 SKILL.md，或 `manifest.json` 加提示词的 ZIP；不会执行上传代码。
5. 选择明确的全文版本和模型，开始分析。LangGraph 保存每块状态与 checkpoint；失败、取消或关闭后可手动继续，已完成块跳过。每条候选保留原文引用和位置；查看证据、修订并确认入库后才写入素材表。已有作品的人工字段不会被分析覆盖。尚未完成的任务不会在下次打开时自动调用模型。

所有模型请求及重试独立记账。提供商实际计数、UTF-8 字节规划预估和未知值分开显示；未配置价格时费用显示未知，已配置价格时仍作为费用预估。规划预估不等同特定模型的 tokenizer。取消会在当前请求边界停止后续调用，已发出的请求仍可能产生费用。

指定密钥环境变量的 PowerShell 示例：

```powershell
$env:INKREFERENCE_LLM_KEY = '填写自己的 API Key'
.\scripts\open.ps1 -EnvironmentName InkReference
# 模型配置中的 key_env 填 INKREFERENCE_LLM_KEY
```

MinerU 也可安装在独立 Python 环境。源码和安装包均支持指定该环境的可信 Python 路径：

```powershell
$env:INKREFERENCE_MINERU_PYTHON = 'D:\Miniforge3\envs\MinerU\python.exe'
.\scripts\open.ps1 -EnvironmentName InkReference
```

同一数据目录只允许一个进程打开。恢复项目返回独立目录后，可用 `open.ps1 -DataDir '恢复目录'` 打开；当前工作区会保留。SQLite WAL／SHM／journal、原件、全文、权重、密钥、日志、截图、备份及打包结果都由 `.gitignore` 排除；合成 fixtures、环境配置、依赖锁和构建脚本保留在仓库。

## 开发

安装完成后，在仓库根目录运行：

```powershell
conda run --no-capture-output -n InkReference python -m pytest -q
conda run --no-capture-output -n InkReference python -m inkreference --no-open
```

前端开发在 `frontend` 目录运行 `conda run -n InkReference npm run dev`；Python API 与前端构建的约定见 [API contract](docs/api-contract.md)。发布前检查：

```powershell
Push-Location frontend
conda run --no-capture-output -n InkReference npm run typecheck
conda run --no-capture-output -n InkReference npm run build
Pop-Location
```

可信 InkOctoBot 检出版本的离线复用检查可通过下列命令重现；固定来源版本见第三方记录：

```powershell
conda run --no-capture-output -n InkReference python scripts\verify_reuse.py --source-repo "D:\projects\InkOctoBot"
```

说明文档：

- [第一批范围与验收](docs/batch-01.md)
- [已完成的检查与复现方法](docs/validation.md)
- [架构与数据规则](docs/architecture.md)
- [后续开发计划](docs/development-plan.md)
- [第三方来源和复用记录](THIRD_PARTY_NOTICES.md)
- [开发约束](AGENTS.md)

验证范围见 [validation.md](docs/validation.md)。真实模型提取质量、扫描件 OCR 和 Windows 原生交互应与自动化测试分别验收。
