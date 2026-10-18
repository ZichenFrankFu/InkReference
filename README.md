# InkReference

Windows／macOS 本地写作参考工作台：整理作品、人物和桥段，接续、清理与编辑全文，使用可定制的 LangGraph 工作流提取剧情、角色、设定和文本特征，审核后保存。

普通用户使用打包后的桌面应用，无需安装 Python、Node.js 或 Conda。源码安装默认使用项目内 `.venv`；开发者可自行配置并复用 Conda 或其他 Python 环境。

## 当前功能

| 工作区 | 已实现的功能 |
|---|---|
| 素材库 | 作品、人物、桥段三类 v8 数据；搜索、分页、新增、编辑、删除；查看作品与人物／桥段的关联；版本冲突检查和关联删除保护。 |
| CSV 导入 | 同时选择或拖入多个 CSV；按表头自动识别三类表，严格解析 UTF-8／GB18030；预览新增、已有记录、错误和关联；选择跳过、更新或报错后，整批事务提交。重复导入不会重复添加。 |
| CSV 导出 | 单表 CSV、兼容原始 v8 的三个 CSV ZIP、素材库内部表 ZIP，以及覆盖全文／转换／Skills／工作流／分析／用量的全部业务表 CSV ZIP；CSV 为 UTF-8 BOM。 |
| 全文库与续传 | 上传 TXT、Markdown、SRT、DOCX、EPUB，选择编码、标题并关联作品；接续上传的新文件排在已有章节之后。保存不可变原件与 SHA-256，可下载原件和各版本 TXT；大文本按 Unicode 字符范围分页阅读。 |
| 逐章清洗 | 在同一章正文内用红色标出建议删除的非正文、采集噪声，点击可取消清理；单章或整本批量保存清洗版本。字符缺失、疑似乱码、非正文及篇幅／章号异常在目录中用不同颜色标记；缺字不会靠猜测补全。 |
| 章节编辑与识别 | 自动识别中文章／回／节、Chapter、数字编号；选择匹配格式，增改／启停／删除自定义模板或正则，配置非正文关键词及杂字规则。逐章编辑正文、改名、插入、单章或多选删除；保留前言与结尾，检查全文覆盖，所有修改生成新版本。切章缓存与固定进度区域避免短暂加载挤动正文。 |
| 分卷 | 按正文卷标题给出建议，也可手动编辑卷名和章节范围；支持模型检测、复制提示词及粘贴结果预览。方案固定到正文版本，保存前检查连续覆盖，避免遗漏或重叠章节。 |
| 格式转换 | EPUB 按 spine 阅读顺序转 Markdown／TXT；PDF 使用本地 MinerU 4 提取文字层，或使用 Standard OCR 识别扫描页。查看阶段与进度，取消、重试，下载 MD／TXT，或把结果导入全文库。 |
| 模型设置 | 完整 GUI 配置本地 Ollama、LM Studio、vLLM 和兼容 OpenAI Chat Completions 的商业 API；配置地址、模型、凭据、上下文、输出上限、超时及价格；主动读取模型列表、测试当前草稿和管理已存凭据。 |
| 分析与审核 | 对明确的全文版本运行真实 LangGraph 工作流；提取梗概、人物、剧情事件、桥段及作品参考分析。显示分段进度、全文覆盖、失败原因和原文证据；支持取消、恢复、修订和幂等提交。切换任务时保护未保存的审核草稿。 |
| 参考作品子页 | 叙事作品对应原始文件、预处理、特征提取、剧情大纲、角色、设定、文本特征；剧情有分段与故事时间线，角色保留逐章外貌／性格／经历，设定保留演进记录。文本特征提供风格、开篇模式、章节节奏／信息密度／爽点／钩子，以及全书和分段视图。 |
| 设定集 | 无需上传正文即可新建；原始文本、设定、角色、特征提取、设定特征五类子页。文本条目、静态角色、分类设定及核心冲突／高概念可编辑；四节 TXT 合并／替换导入导出，保留多行和未知字段。原始条目可翻译，先预览后应用。 |
| 作品笔记与分析历史 | 保存个人评分、喜欢原因和章节笔记；每次分析修改保存历史，可恢复为新版本，逐节审核状态和人工风格修订同步恢复。大纲粒度调整、翻译都支持提示词／粘贴或模型调用，使用统一用量台账。 |
| 工作流与 Skills | 内置参考作品分析流程；自定义提取开关、提示词、分块、重试和 token 预算；保存不可变版本。画布支持移动、缩放与布局保存。创建或上传 JSON、SKILL.md、声明式 ZIP Skill，校验输出结构与引文。 |
| 用量与费用 | 每次模型请求及重试独立记账；查看成功／失败／待确认状态、输入和输出 token，区分提供商实测、规划估算及未知；按配置的价格和币种估算费用，可按分析任务筛选。 |
| 备份与恢复 | 导出包含五个 SQLite 数据库及原始 CSV 导入文件的完整项目 ZIP；恢复时校验路径、大小、SHA-256 和数据库完整性，生成独立工作区。全部业务表 CSV 用于查阅和迁移，恢复项目使用完整备份 ZIP。 |
| 桌面与运行 | Windows／macOS 桌面应用及浏览器入口；源码一键安装、启动、环境诊断；默认使用项目 `.venv`，也可复用开发者已配置的环境。支持自选端口、数据目录和工作区单实例保护；提供 Windows ZIP／Setup、macOS `.app`／ZIP 构建。蓝色界面提供进度条、键盘操作、窄屏布局与减少动态效果支持。 |

当前素材结构采用 v8 的固定字段，CSV 表头需要完整匹配某一类表；文件名和列的排列顺序可以不同。

## 普通用户：桌面应用

从项目提供的构建产物中选择与系统匹配的包：

| 系统 | 安装与打开 |
|---|---|
| Windows x64 | 运行 Setup 安装程序，或完整解压 Windows ZIP 后双击 `InkReference.exe`。Windows 需要 Microsoft Edge WebView2 Runtime。 |
| macOS 13+ Apple Silicon | 解压 `macos-arm64` ZIP，把 `InkReference.app` 拖入“应用程序”后打开。 |
| macOS 13+ Intel | 选择 `macos-x86_64` ZIP，按相同方式安装。 |

可携带版需要保留整个解压目录。macOS 包按对应架构在 Mac 上构建，构建会添加本机 ad-hoc 签名，未提供 Developer ID 签名或 Apple 公证；首次打开可能需要在“系统设置 → 隐私与安全性”中允许此应用。GitHub Actions 的构建产物位于相应运行的 Artifacts，构建不会自动发布 GitHub Release。各平台的实际验证范围见 [validation.md](docs/validation.md)。

首次使用：

1. 打开“素材库”，导入三个 v8 CSV，检查预览后提交；也可以直接新建作品。
2. 在“模型设置”选择本地模型或商业 API，填写地址、模型及凭据，测试连接后保存。
3. 在“全文与清洗”上传全文，逐章检查红色清理标记，点击保留误报的段落；可编辑章节正文、接续新章节，或保存清洗版本。EPUB／PDF 的 Markdown、TXT 输出使用同页“格式转换”。PDF 需要可选的 MinerU 运行环境，见[格式转换与分析](#格式转换与分析)。
4. 选择全文版本、模型和工作流，开始分析。远程分析在开始前简洁确认一次目的服务；任务恢复沿用已确认目标。
5. 在“分析与审核”查看证据、修订结果并确认入库。需要迁移或归档时，在“备份与恢复”下载完整项目 ZIP。

## 源码安装与启动

源码运行需要 **Python 3.11–3.13、Node.js 22 或 24 和 npm**。无需 Conda。以下命令都在仓库根目录执行；安装器检查版本，创建或复用项目 `.venv`，安装依赖并构建前端。下载输出与阶段进度会显示在终端。

### Windows PowerShell

```powershell
.\scripts\install.ps1
.\scripts\open.ps1

# 可选：PDF 转换与测试依赖
.\scripts\install.ps1 -MinerU -Dev

# 只使用浏览器
.\scripts\install.ps1 -BrowserOnly
.\scripts\open.ps1 -Browser
.\scripts\open.ps1 -Browser -NoOpen

# 指定端口和数据目录
.\scripts\open.ps1 -Port 8001 -DataDir "D:\写作资料\InkReference"
```

支持 Windows PowerShell 5.1。脚本不修改全局执行策略；若当前策略阻止运行，可只为此次子进程设置：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\open.ps1
```

### macOS Terminal

```bash
./scripts/install.sh
./scripts/open.sh

# 可选：PDF 转换与测试依赖
./scripts/install.sh --mineru --dev

# 浏览器模式，或指定数据目录
./scripts/open.sh --browser
./scripts/open.sh --port 8001 --data-dir "$HOME/Documents/InkReference"
```

桌面模式使用 macOS 系统 WebKit。浏览器模式复用相同的本机服务。源码启动时保持终端运行，`Ctrl+C` 停止服务；端口占用会提示换端口，不结束其他程序。代码更新后重新运行安装命令。

两套入口共用 Python 管理器，也可直接执行：

```bash
python scripts/manage.py install
python scripts/manage.py open
python scripts/manage.py doctor
```

macOS 中如果命令名为 `python3`，将上面的 `python` 替换为 `python3`。`doctor` 检查解释器、依赖、前端及桌面支持，输出具体修复建议；`doctor --json` 可输出结构化结果。创建 `.venv` 时可用 `install --python PATH`／`install.ps1 -Python PATH` 指定基础解释器。安装使用 `requirements-lock.txt` 约束依赖，前端使用 `npm ci` 和生产构建；可选 MinerU 使用独立的 `requirements-mineru.txt`。默认安装运行及桌面依赖，测试依赖由 `--dev`／`-Dev` 选装。

### 复用开发者自己的环境

先手动激活已配置好的 Python 环境，再明确要求使用当前环境。可以是 Conda、虚拟环境或其他受管理的 Python；安装器不会替你创建 Conda 环境、执行 `conda init`，也不改全局配置。

```powershell
conda activate InkReference
.\scripts\install.ps1 -CurrentEnvironment -Dev
.\scripts\open.ps1 -CurrentEnvironment
```

macOS／通用入口：

```bash
conda activate InkReference
./scripts/install.sh --current-environment --dev
./scripts/open.sh --current-environment
# 等价：python scripts/manage.py install --current-environment --dev
```

旧 `-EnvironmentName InkReference` 参数仅兼容复用**已经存在**的命名 Conda 环境，环境不存在时提示手动配置，不再自动创建。旧 `setup`／`start` 入口保留为兼容入口。Conda 是可选的开发者工具，打包后的应用用户无需配置它。

## 模型配置与基本安全

模型连接均在 GUI 的“模型设置”完成。

| 连接方式 | GUI 配置 |
|---|---|
| Ollama | 选择本地 Ollama，默认 `http://127.0.0.1:11434`；先运行 Ollama 并准备模型，然后读取模型列表或填写模型 ID。 |
| LM Studio／vLLM | 启动相应的本机 OpenAI-compatible 服务，选择预设并填写实际地址和模型 ID。权重由推理服务管理。 |
| 商业 API | 选择 OpenAI、DeepSeek、通义千问或自定义兼容服务，填写 HTTPS 地址、模型 ID 和 API Key。当前支持 OpenAI-compatible Chat Completions 协议。 |

可直接测试当前草稿，无需提前保存。点击“读取模型列表”或“测试连接”就会执行该操作，不再弹出二次确认。上下文、输出上限、超时、价格和币种在详细设置中。新建远程分析时只确认一次目的服务和模型，之后该任务的开始／恢复沿用已确认目标；若目标在提交前被其他窗口修改，会要求刷新后重新选择。

凭据可选**系统密钥库**、**仅本次运行**或**环境变量**。Windows 使用系统凭据后端，macOS 使用 Keychain；系统密钥库不可用时可改选会话或环境变量，不写入明文文件。Key 默认遮罩，保存后清空输入框，可在 GUI 清除。API 响应、模型快照、CSV、项目备份及浏览器存储不包含 Key。凭据绑定服务地址，避免更换服务后沿用原 Key；系统密钥库按工作区隔离，移动或恢复项目后重新配置凭据。

应用保留正常的本机安全边界：服务仅监听回环地址，检查 Host 和请求来源，页面使用 CSP 且不加载外部统计或字体。HTTP 可用于本机模型，远程服务使用 HTTPS；工作流关闭远程追踪。CSV、规则清洗、EPUB 转换、导出和备份在本机执行；远程模型分析会把所选全文版本的正文块与提示词发送至所选服务。模型测试只发送固定测试消息。

如选择环境变量模式，在启动前设置变量，再在 GUI 填写变量名即可。通常直接使用系统密钥库更方便。正文、分析结果和备份没有整体加密，请按自己的资料保管习惯保存。

## 格式转换与分析

上传 TXT、Markdown、SRT、DOCX 或 EPUB 后保留不可变原件。需要更新连载时使用“接续章节”，新文件接在已有正文之后；章节正文可单独编辑、改名、插入或删除。清洗、手动修改和续传均生成新版本。

章节正文只显示一个带标记的版本：红色部分是建议删除的内容，点击可保留，再保存本章或整本的已选标记。目录可筛选非正文、字符缺失／乱码、采集噪声及篇幅／章号异常；标记会覆盖所有章节，长章分页不会只清理当前可见内容。字符缺失和疑似编码问题属于诊断，不能把未知正文凭空补齐。

EPUB 按 spine 阅读顺序转换为 Markdown，再渲染为纯 TXT。PDF 使用 **MinerU 4**：默认 `flash + txt` 提取文字层，扫描件使用 `standard + ocr` 并准备对应模型。转换支持进度、取消、重试、MD／TXT 下载和全文库导入；完整块渲染保留可能被分类为页眉的正文。EPUB 不需要 MinerU。

源码安装可加 `--mineru`／`-MinerU`。MinerU 及 OCR 权重不随桌面包分发；打包版可连接本机独立安装的 MinerU Python。先在该环境按 `requirements-mineru.txt` 安装，再通过环境变量指定解释器；不要求该环境由 Conda 管理：

```powershell
$env:INKREFERENCE_MINERU_PYTHON = 'D:\Tools\MinerU\Scripts\python.exe'
.\scripts\open.ps1
# 打包版：在同一终端运行解压后的 InkReference.exe
```

```bash
export INKREFERENCE_MINERU_PYTHON="$HOME/.venvs/mineru/bin/python"
./scripts/open.sh
# 打包版：在同一终端执行 /Applications/InkReference.app/Contents/MacOS/InkReference
```

OCR 首次运行可能下载模型，转换能力与当前 MinerU 后端及模型有关。

分析使用明确的全文版本、模型、工作流和 Skill。工作流可调整提取开关、提示词、分块、重试及 token 预算并保存新版本；React Flow 画布负责布局与查看。可创建或上传 JSON、SKILL.md、声明式 ZIP Skill，不执行上传代码。

“分析与审核 → 作品分析”以作品为中心，按 InkOctoBot 的七个叙事子页组织功能。先在“特征提取”选择正文版本和工作流，创建模型提取任务或仅用于复制提示词／粘贴结果的任务。每段的剧情、角色、设定、风格分别预览、修订和确认；完成一节不会代替其他节的审核。整批提取只生成候选，确认后的内容才进入作品分析。

“剧情大纲”提供故事时间线、分段浏览和大纲粒度调整；“角色”保留外貌、性格、经历与语言样例；“设定”按类别管理并记录逐章变化；“文本特征”查看全书和分段的风格、开篇及章节节奏。原始文件和预处理子页可以直接打开对应作品的文件历史与章节工作台。“提取任务与素材审核”保留原有梗概、人物、事件和桥段的证据审核及 v8 素材入库。

设定集可从“作品分析 → 新建设定集”直接创建，使用独立的原始文本条目、静态角色、设定及核心冲突／高概念视图。“导出设定集 TXT”包含四类内容，以可读 UTF-8 结构保留换行和字段；“导入设定集 TXT”可选择合并或替换，普通 TXT 作为一个原始条目导入。提取时可把已有条目、设定和角色合成固定的材料版本；翻译和大纲概括都先显示结果预览，由用户决定应用。手工粘贴的参考分析允许经过人审的上游格式；只有满足连续原文证据要求的素材才可写入 v8 素材库。

作品标题旁的“作品笔记”保存评分、喜欢原因和章节笔记；“分析历史版本”可恢复以前的作品分析，恢复后仍可继续修改和审核。当前复刻范围与尚存差异（例如自动编码往返修复、逐上传文件范围删除）见 [逐项审查](docs/reference-parity.md)，不会把缺字诊断当成已经恢复了原文。

LangGraph 持久化每块响应与 checkpoint。失败、取消或关闭后可手动恢复，完成块不会重复请求，重启应用不自动调用模型。模型素材候选附原文引用及位置，修订和确认入库后才写入素材表；已有作品的人工字段保持原值。每次请求与重试单独统计 token，区分提供商实际计数、规划预估和未知值。费用根据配置价格估算；取消会在当前请求边界停止后续调用，已经发出的请求仍可能计费。

## 数据与导出

| 系统 | 默认数据目录 |
|---|---|
| Windows | `%LOCALAPPDATA%\InkReference` |
| macOS | `~/Library/Application Support/InkReference` |

可用 `INKREFERENCE_DATA_DIR` 或启动参数 `--data-dir`／`-DataDir` 指定目录。数据与源码、应用包分离，数据库启动时自动迁移；同一目录只允许一个应用实例。

记录按外部 ID 识别，同名作品的不同版本分别保留；日期精度、换行、空白和“不确定”等内容保持字符串。CSV 导入先预览，校验通过后整批事务提交，可选择跳过、更新或报错。编辑检查 revision，关联记录删除需要明确确认。

导出按用途提供：

- **单表／v8 兼容 CSV**：单表下载或三个原始结构 CSV 的 ZIP，均为 UTF-8 BOM。
- **素材库完整 CSV**：三张素材表（含 ID／revision）、作品桥段关系表和清单。
- **全部业务表 CSV**：素材、全文、转换、Skills、工作流、分析和用量的每张业务表各一个 CSV，用于查阅及迁移。
- **完整项目备份**：五个 SQLite 数据库、原始 CSV 导入文件及大小／SHA-256 清单，可验证并恢复为独立工作区，保留当前项目。备份不包含凭据值、安装环境或模型权重；恢复后重新配置模型凭据。

`.gitignore` 排除真实 CSV、全文、数据库及 WAL／SHM／journal、密钥、模型权重、日志、缓存、截图、备份和构建产物；提交合成 fixtures、依赖锁及构建脚本。

## 开发与构建

### 开发检查

先通过上面的安装入口添加 `--dev`／`-Dev`。项目 `.venv` 默认使用以下命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\verify_reuse.py --source-repo "D:\projects\InkOctoBot"
```

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/verify_reuse.py --source-repo "$HOME/projects/InkOctoBot"
```

复用自己的环境时直接用该环境的 `python`。前端检查：

```bash
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Vite 热更新调试需要为后端显式允许本机开发来源。在第一个终端设置 `INKREFERENCE_DEV_ORIGIN=http://localhost:5173` 后启动 `python -m inkreference --no-open`；PowerShell 用 `$env:INKREFERENCE_DEV_ORIGIN = 'http://localhost:5173'`，macOS 用 `export INKREFERENCE_DEV_ORIGIN='http://localhost:5173'`。第二个终端执行：

```bash
npm --prefix frontend run dev -- --host localhost --port 5173 --strictPort
```

仅开发界面需要该例外，生产包保持同源。接口定义见 [API contract](docs/api-contract.md)，本地 `/openapi.json` 提供请求结构。

### 构建桌面应用

在目标系统和目标架构上构建，不从 Windows 交叉生成 macOS 应用。默认复用项目 `.venv`；开发者手动激活环境后，可显式使用当前环境。

Windows：

```powershell
.\scripts\package.ps1
.\scripts\package.ps1 -Installer
.\scripts\package.ps1 -CurrentEnvironment -Installer -OutputDirectory "D:\打包结果\InkReference"
```

`-Installer` 需要 Inno Setup 6.3+。默认生成 `release\app\InkReference\InkReference.exe`、`release\InkReference-windows-x64.zip`，Setup 位于 `release\installer`。安装程序建立开始菜单入口，卸载时保留用户数据。

macOS：

```bash
./scripts/package.sh
./scripts/package.sh --dmg
./scripts/package.sh --current-environment --output-directory "$HOME/Desktop/InkReference-build"
```

默认输出 `release/app/InkReference.app` 和 `release/InkReference-macos-arm64.zip` 或 `release/InkReference-macos-x86_64.zip`；`--dmg` 额外生成磁盘映像。Apple Silicon 与 Intel 分别构建，最低系统版本为 macOS 13。构建收集前端、Python、LangGraph SQLite、系统密钥库和 pywebview 运行依赖，不包含用户数据、MinerU 或模型权重。macOS 构建使用本机 ad-hoc 签名并验证，正式分发的 Developer ID 签名与公证由发布者另行配置。

打包烟雾检查以 `--no-open` 验证冻结程序的本机服务和前端；不等同于 Windows／macOS 原生窗口的人工交互验收。检查记录和限制见 [validation.md](docs/validation.md)。

## 项目文档

- [检查结果与复现](docs/validation.md)
- [InkOctoBot 参考数据库逐项审查](docs/reference-parity.md)
- [架构与数据规则](docs/architecture.md)
- [API contract](docs/api-contract.md)
- [开发计划](docs/development-plan.md)
- [第三方来源和复用记录](THIRD_PARTY_NOTICES.md)
- [开发约束](AGENTS.md)
