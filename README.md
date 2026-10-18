# InkReference

Windows 优先的本地写作参考工作台：整理作品、人物和桥段，管理全文及清洗版本，用可定制的 LangGraph 工作流提取剧情与证据，审核后入库。

React 中文界面提供素材库、全文清洗、EPUB／MinerU PDF 转换、分析任务、工作流与 Skills、模型设置和项目备份。可在指定 Conda 环境中打开 pywebview 桌面窗口或浏览器，也提供 Windows 安装包构建入口。

## 当前功能

| 工作区 | 已实现的功能 |
|---|---|
| 素材库 | 作品、人物、桥段三类 v8 数据；搜索、分页、新增、编辑、删除；查看作品与人物／桥段的关联；版本冲突检查和关联删除保护。 |
| CSV 导入 | 同时选择或拖入多个 CSV；按表头自动识别三类表，严格解析 UTF-8／GB18030；预览新增、已有记录、错误和关联；选择跳过、更新或报错后，整批事务提交。重复导入不会重复添加。 |
| CSV 导出 | 单表 CSV、兼容原始 v8 的三个 CSV ZIP、素材库内部表 ZIP，以及覆盖全文／转换／Skills／工作流／分析／用量的全部业务表 CSV ZIP；CSV 为 UTF-8 BOM。 |
| 全文库 | 上传 TXT、Markdown、SRT、DOCX、EPUB；搜索全文条目，选择编码、标题并关联已有作品；保存不可变原件与 SHA-256；下载原件和各版本 TXT。大文本按 Unicode 字符范围分页阅读。 |
| 数据清洗 | 处理开头 BOM、HTML 实体、换行符和控制字符；疑似采集 ID 需明确勾选才删除；标记疑似乱码、HTML 标记和作者题外话。保留原文、新清洗版本、修改记录和差异，可手动编辑生成新版本。 |
| 章节与覆盖 | 识别中文章／回／节、Chapter 和数字编号；无标题时按字符切分；保留序言与结尾，并检查分段是否完整覆盖全文。 |
| 格式转换 | EPUB 按 spine 阅读顺序转 Markdown／TXT；PDF 使用本地 MinerU 4 提取文字层，或使用 Standard OCR 识别扫描页。查看阶段与进度，取消、重试，下载 MD／TXT，或把结果导入全文库。 |
| 模型设置 | 完整 GUI 配置本地 Ollama、LM Studio、vLLM 和兼容 OpenAI Chat Completions 的商业 API；配置地址、模型、凭据、上下文、输出上限、超时及价格；主动读取模型列表、测试当前草稿和管理已存凭据。 |
| 分析与审核 | 对明确的全文版本运行真实 LangGraph 工作流，提取梗概、人物、剧情事件和桥段；搜索任务，显示块级进度、全文覆盖、失败原因和原文证据；取消、恢复、修订结果，确认后幂等入库。切换任务时保护未保存的审核草稿。 |
| 工作流与 Skills | 内置参考作品分析流程；自定义提取开关、提示词、分块、重试和 token 预算；保存不可变版本。画布支持移动、缩放与布局保存。创建或上传 JSON、SKILL.md、声明式 ZIP Skill，校验输出结构与引文。 |
| 用量与费用 | 每次模型请求及重试独立记账；查看成功／失败／待确认状态、输入和输出 token，区分提供商实测、规划估算及未知；按配置的价格和币种估算费用，可按分析任务筛选。 |
| 备份与恢复 | 导出包含五个 SQLite 数据库及原始 CSV 导入文件的完整项目 ZIP；恢复时校验路径、大小、SHA-256 和数据库完整性，生成独立工作区。全部业务表 CSV 用于查阅和迁移，恢复项目使用完整备份 ZIP。 |
| 桌面与运行 | 命名 Conda 环境一键安装／打开；pywebview 桌面或浏览器入口；自选端口与数据目录；工作区单实例保护；Windows 可携带 ZIP 和 Setup 构建。界面提供进度条、键盘操作、窄屏布局与减少动态效果支持。 |

当前素材结构采用 v8 的固定字段，CSV 表头需要完整匹配某一类表；文件名和列的排列顺序可以不同。

## 第一次使用

1. 按下面的 PowerShell 命令安装并打开应用。
2. 在“素材库”导入三个 v8 CSV，检查预览后提交；也可以直接新建作品。
3. 打开“模型设置”，选择本地模型或商业 API，在界面填写地址、模型和凭据，测试连接后保存。详见[模型配置与数据边界](#模型配置与数据边界)。
4. 在“全文与清洗”上传全文，查看章节、诊断和清洗版本。EPUB／PDF 的 MD、TXT 输出在该页面的“格式转换”中完成。
5. 选择全文版本、模型及工作流，开始分析；远程请求需确认目的服务。到“分析与审核”查看证据、修订并确认入库。
6. 在“备份与恢复”下载完整项目 ZIP。迁移到另一台机器或另一目录后，重新配置模型凭据。

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

素材库导出提供两种 ZIP：兼容模式包含 v8 格式的三个 CSV；完整模式包含三张素材表（附记录 ID／revision）、作品桥段关联表及 `manifest.json`。也可以单独导出一张素材表。CSV 使用 UTF-8 BOM，便于在 Windows Excel 中打开。

素材库的兼容导出保留 v8 的三个 CSV；备份页的“全部业务表”会为素材、全文、转换、Skills、工作流、分析与用量等每张业务表生成 CSV。完整项目 ZIP 另外包含五个 SQLite 数据库、原始导入文件与校验清单，可恢复为独立工作区。模型凭据的备份边界见下一节。

## 模型配置与数据边界

模型连接在 GUI 的“模型设置”中完成，无需通过代码或命令行填写模型参数。

| 连接方式 | GUI 配置 |
|---|---|
| 本地 Ollama | 选择“本地模型”中的 Ollama，默认地址 `http://127.0.0.1:11434`；确保 Ollama 已运行且已下载所需模型，然后主动读取模型列表或填写模型 ID。 |
| 本地 LM Studio／vLLM | 选择相应预设，启动本机 OpenAI-compatible 服务，填写实际监听地址与模型 ID。应用连接已有推理服务；模型权重由该服务管理。 |
| 商业 API | 选择 OpenAI、DeepSeek、通义千问或自定义兼容服务，核对 HTTPS 服务地址、模型 ID 和 API Key。当前适配 OpenAI-compatible Chat Completions 协议；其他协议需对应的兼容端点。 |

填写配置名称、地址和模型后，可直接“测试连接”，测试使用当前草稿，无需提前保存。模型列表只在点击“读取模型列表”时获取；商业服务的列表权限取决于提供商，也可手动输入模型 ID。上下文窗口、最大输出、超时、每百万 token 的输入／输出价格和币种位于详细设置中。

凭据支持三种保存方式：

- **仅本次运行**：API Key 仅保存在后端内存，退出应用后重新填写。
- **系统密钥库**：通过操作系统凭据后端保存。可用性在 GUI 显示；不可用时禁止保存，不回退到明文文件。凭据按工作区路径隔离，移动／恢复到新目录后需重新填写。
- **环境变量**：GUI 只填写变量名，后端读取进程中的变量值。清除配置中的凭据会移除该引用，保留系统中原有的环境变量。

密码输入默认遮罩，保存后清空输入框；配置返回值、模型快照、CSV 和项目备份不包含 API Key。可以在 GUI 中清除已存凭据。浏览器不使用 localStorage／sessionStorage 保存 Key 或配置草稿。

本地模型地址只接受 `localhost` 或字面量回环 IP，商业服务只接受 HTTPS。每次商业 API 连接测试、读取模型列表及启动／恢复远程分析，界面都会显示目标地址并要求明确同意。分析确认绑定当前服务地址和模型；其他窗口修改配置后，创建任务会被阻止，需要刷新并重新确认。连接测试使用固定测试消息；分析请求会把所选版本的正文块、Skill 提示词与自定义提示词发送给所选服务。CSV 整理、规则清洗、EPUB 转换、导出和备份均在本机执行；MinerU OCR 首次运行可能需要下载其模型。

应用服务仅允许本机回环监听，并检查 Host、请求来源与浏览器同源边界；页面使用 CSP，不加载外部字体、脚本或统计服务。LangGraph 工作流显式关闭远程追踪，不继承外部 LangSmith tracing 开关。正文、分析结果和本地数据库没有做整体加密，完整备份也包含这些内容，应存放在自己的受保护目录。系统密钥库保护凭据，不能隔离同一账户下已经获得系统访问权限的其他程序。

若选择环境变量模式，可在启动前设置变量；这是可选凭据方式：

```powershell
$inkReferenceKey = Read-Host 'API Key' -AsSecureString
$env:INKREFERENCE_LLM_KEY = [System.Net.NetworkCredential]::new('', $inkReferenceKey).Password
# 在 GUI 的“环境变量”方式中填 INKREFERENCE_LLM_KEY
try {
    .\scripts\open.ps1 -EnvironmentName InkReference
}
finally {
    Remove-Item Env:\INKREFERENCE_LLM_KEY -ErrorAction SilentlyContinue
    Remove-Variable inkReferenceKey -ErrorAction SilentlyContinue
}
```

遮罩输入避免把 Key 写进 PowerShell 命令历史；程序退出后上面的命令会清除当前终端中的变量。

## 全文、转换与分析

1. 在“全文与清洗”上传 TXT、Markdown、SRT、DOCX 或 EPUB。原件不可变；编码严格解码，无法确认时选择编码后重新预览。清洗结果作为新版本保存，可以查看章节、疑似乱码、修改差异及原文。默认不删除题外话、疑似编号或故事线索；移除随机 ID 需明确勾选。
2. 在“格式转换”选择 EPUB 或 PDF。EPUB 按书籍 spine 顺序转 Markdown，再生成 TXT。PDF 使用 **MinerU 4**；默认 `flash + txt` 提取已有文字层，扫描件选择 `standard + ocr` 并准备相应模型。转换完成后可下载 `.md`、`.txt`，或把 TXT 导入全文库。原件一直保留。PDF 完整块渲染保留可能被识别为页眉的正文，避免默认渲染省略内容。
3. 在“模型设置”配置并测试模型，选择本地推理服务或商业 API，再回到全文页面选择该配置。
4. 在“工作流与 Skills”选择声明式 Skill，调整人物／剧情／桥段开关、自定义提示词、字符块上限、重试和 token 预算；保存为新版本。React Flow 画布布局单独保存，提取行为由工作流定义控制。Skill 可上传 JSON、带简单元数据的 SKILL.md，或 `manifest.json` 加提示词的 ZIP；不会执行上传代码。
5. 选择明确的全文版本和模型，开始分析。LangGraph 保存每块状态与 checkpoint；失败、取消或关闭后可手动继续，已完成块跳过。每条候选保留原文引用和位置；查看证据、修订并确认入库后才写入素材表。已有作品的人工字段不会被分析覆盖。尚未完成的任务不会在下次打开时自动调用模型。

所有模型请求及重试独立记账。提供商实际计数、UTF-8 字节规划预估和未知值分开显示；未配置价格时费用显示未知，已配置价格时仍作为费用预估。规划预估不等同特定模型的 tokenizer。取消会在当前请求边界停止后续调用，已发出的请求仍可能产生费用。

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

Vite 开发界面需要显式允许对应的本机来源。在第一个 PowerShell 终端中设置后再启动后端：

```powershell
$env:INKREFERENCE_DEV_ORIGIN = 'http://localhost:5173'
conda run --no-capture-output -n InkReference python -m inkreference --no-open
```

在第二个终端启动前端，然后打开 `http://localhost:5173`：

```powershell
Push-Location frontend
conda run --no-capture-output -n InkReference npm run dev -- --host localhost --port 5173 --strictPort
Pop-Location
```

此环境变量仅供开发调试，生产界面保持同源。Python API 与前端构建的约定见 [API contract](docs/api-contract.md)，接口结构可在本地 `/openapi.json` 查看。发布前检查：

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
