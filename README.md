# InkReference

Windows／macOS 本地写作参考工作台：整理作品、人物和桥段，接续、清理与编辑全文，使用可定制的 LangGraph 工作流提取剧情、角色、设定和文本特征，审核后保存。

普通用户使用打包后的桌面应用，无需安装 Python、Node.js 或 Conda。源码安装默认使用项目内 `.venv`；开发者可自行配置并复用 Conda 或其他 Python 环境。

## 当前功能

| 工作区 | 已实现的功能 |
|---|---|
| 素材库 | 作品、人物、桥段三类 v8 数据；搜索、分页、新增、编辑、删除；关联卡片显示关系类型、方向和具体说明；保留人物定位、作品使用桥段的原始说明。支持自定义素材关联，禁止桥段关联桥段，共同出现不会自动推断为关系。编辑检查版本，删除保护关联。 |
| 整库导入 | 默认一个 `.db`；也可选择完整 CSV ZIP、全部 CSV 与清单，或一个 `.xlsx`。导入预览逐一列出必要表，指明缺失的文件、字段、引用 ID 及疑似混用版本的两个文件。旧 v8 三张 CSV 必须同时导入；预览通过后按跳过、更新或报错策略整批事务提交。 |
| 整库导出 | DB、CSV、XLSX 三种格式携带相同的五张业务表和格式信息，保留全部素材与关联说明。CSV 使用 UTF-8 BOM；Excel 中编号、日期及正文保持文本，超过单元格长度的正文通过辅助工作表保留。单表及原 v8 CSV API 作为兼容接口保留。 |
| 全文上传与更新 | 主界面只接受 TXT、PDF、EPUB。顶部“新建作品”创建作品并上传文件；选中作品后，“上传文件”直接针对该作品，选择“更新正文”替换当前全部正文，或“补充章节”接到末尾，无需再次选择保存位置。左侧作品列表只展示书名与字数，字数按万字保留一位小数。PDF／EPUB 自动转为 TXT；处理阶段有进度、取消及重试。旧原件与版本保持不可变。 |
| 全文与清洗工作区 | 按“原始文件 → 分卷与章节识别 → 正文”排列；文件来源、识别设置和正文操作分别集中展示。正文只加载目录和当前章，版本历史按需读取；不展示独立转换、诊断或文本分块页。保留“开始分析”和任务进度；“完成清洗”后进入检查与 TXT 导出。 |
| 逐章清洗 | 目录按问题类型高亮整项，标题与正文内显示实际命中的红色清理片段、紫色字符问题，图例集中展示。结构问题显示具体原因；不可见字符也有可见标记。目录不带复选框；“批量清理”弹窗按章节展示真正命中的标题及正文片段，可逐片段选择、按章选择或全选。同章多个问题只算一章。损坏字符由用户修正，缺字不会靠猜测补全。 |
| 章节编辑与识别 | 自动识别中文章／回／节、Chapter、数字编号；识别格式、自定义模板／正则、非正文关键词及杂字规则统一放在“分卷与章节识别”。目录显示“第X章 标题”，正文不重复章号和标题，不保留连续空行。缺号逐章显示空白占位项，可搜索后补写。标题和正文均可编辑，修改后显示未保存状态与保存按钮；同次保存原子提交标题与正文。支持插入、外露删除按钮与历史恢复。长章分页保存，保留未显示的正文；普通正文编辑只重算受影响章节。 |
| 分卷 | 按正文卷标题给出建议，也可手动编辑卷名和章节范围；支持模型检测、复制提示词及粘贴结果预览。方案固定到正文版本，保存前检查连续覆盖，避免遗漏或重叠章节。 |
| 上传内格式转换 | EPUB 按 spine 阅读顺序转 Markdown，再得到纯文本；PDF 使用本地 MinerU 4 提取文字层，或使用 Standard OCR 识别扫描页。转换并入上传流程，完成后自动解析章节。已有转换 API 继续保留 Markdown／TXT 输出，主界面以清洗 TXT 为导出结果。 |
| 模型设置 | 完整 GUI 配置本地 Ollama、LM Studio、vLLM 和兼容 OpenAI Chat Completions 的商业 API；配置地址、模型、凭据、上下文、输出上限、超时及价格；主动读取模型列表、测试当前草稿和管理已存凭据。 |
| 分析与审核 | 对明确的全文版本运行真实 LangGraph 工作流；提取梗概、人物、剧情事件、桥段及作品参考分析。显示分段进度、全文覆盖、失败原因和原文证据；支持取消、恢复、修订和幂等提交。切换任务时保护未保存的审核草稿。 |
| 参考作品子页 | 叙事作品保留特征提取、剧情大纲、角色、设定、文本特征五个分析子页；原始文件与清洗操作统一在全文工作区。剧情有分段与故事时间线，角色保留逐章外貌／性格／经历，设定保留演进记录。文本特征提供风格、开篇模式、章节节奏／信息密度／爽点／钩子，以及全书和分段视图。 |
| 设定集 | 无需上传正文即可新建；原始文本、设定、角色、特征提取、设定特征五类子页。文本条目、静态角色、分类设定及核心冲突／高概念可编辑；四节 TXT 合并／替换导入导出，保留多行和未知字段。原始条目可翻译，先预览后应用。 |
| 作品笔记与分析历史 | 保存个人评分、喜欢原因和章节笔记；每次分析修改保存历史，可恢复为新版本，逐节审核状态和人工风格修订同步恢复。大纲粒度调整、翻译都支持提示词／粘贴或模型调用，使用统一用量台账。 |
| 工作流与 Skills | 内置参考作品分析流程；自定义提取开关、提示词、分块、重试和 token 预算；保存不可变版本。画布支持移动、缩放与布局保存。创建或上传 JSON、SKILL.md、声明式 ZIP Skill，校验输出结构与引文。 |
| 用量与费用 | 每次模型请求及重试独立记账；查看成功／失败／待确认状态、输入和输出 token，区分提供商实测、规划估算及未知；按配置的价格和币种估算费用，可按分析任务筛选。 |
| 备份与恢复 | 导出包含五个 SQLite 数据库及原始素材导入文件的完整项目 ZIP；恢复时校验路径、大小、SHA-256 和数据库完整性，生成独立工作区。全部业务表 CSV 用于查阅和迁移，恢复项目使用完整备份 ZIP。 |
| 快捷修复 | 从问题描述、近期错误与相关源码生成可复制的诊断提示词；粘贴模型返回的结构化 JSON 后预览文件差异。源码版校验原文件哈希、保存原始字节备份、执行固定语法／类型检查与前端构建，失败恢复，成功可回滚；打包版可重建章节索引或导出源码方案。不会自动发送诊断或执行模型附带命令。 |
| 外观与导航 | 蓝色界面支持浅色、深色及跟随系统，选择在本机保存；窄屏布局、键盘操作、进度条与减少动态效果。模型配置集中在“模型设置”，只保留一个“添加模型”入口；侧栏保留“备份与恢复”。 |
| 桌面与运行 | Windows／macOS 桌面应用及浏览器入口；源码一键安装、启动、环境诊断；默认使用项目 `.venv`，也可复用开发者已配置的环境。支持自选端口、数据目录和工作区单实例保护；提供 Windows ZIP／Setup、macOS `.app`／ZIP 构建。 |

三张素材表沿用 v8 字段和外部 ID；整库格式另含 `work_cliches` 与 `material_relations` 两张关联表。数据库更新不会按书名合并不同版本。自定义关系的扩展信息保存在 `metadata_json`，整库交换会保留该对象。

## 普通用户：桌面应用

从项目提供的构建产物中选择与系统匹配的包：

| 系统 | 安装与打开 |
|---|---|
| Windows x64 | 运行 Setup 安装程序，或完整解压 Windows ZIP 后双击 `InkReference.exe`。Windows 需要 Microsoft Edge WebView2 Runtime。 |
| macOS 13+ Apple Silicon | 解压 `macos-arm64` ZIP，把 `InkReference.app` 拖入“应用程序”后打开。 |
| macOS 13+ Intel | 选择 `macos-x86_64` ZIP，按相同方式安装。 |

可携带版需要保留整个解压目录。macOS 包按对应架构在 Mac 上构建，构建会添加本机 ad-hoc 签名，未提供 Developer ID 签名或 Apple 公证；首次打开可能需要在“系统设置 → 隐私与安全性”中允许此应用。GitHub Actions 的构建产物位于相应运行的 Artifacts，构建不会自动发布 GitHub Release。各平台的实际验证范围见 [validation.md](docs/validation.md)。

首次使用：

1. 打开“素材库”，导入一个 `.db`、完整 CSV 文件包或 `.xlsx`，检查必要表与版本提示后提交；首次迁移旧资料时同时选择三个 v8 CSV。也可以直接新建作品。
2. 在“模型设置”选择本地模型或商业 API，填写地址、模型及凭据，测试连接后保存。
3. 在“全文与清洗”顶部点击“新建作品”并上传文件；更新已有作品时，先选中作品，再“上传文件”，选择替换全部正文或补充章节。转换、章节识别和标记扫描自动衔接，在“正文”编辑标题与正文，或打开“批量清理”核对命中片段并勾选处理，最后点击“完成清洗”导出 TXT。PDF 需要可选的 MinerU 运行环境，见[格式转换与分析](#格式转换与分析)。
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

桌面模式使用 macOS 系统 WebKit。浏览器模式复用相同的本机服务。源码启动时保持终端运行，`Ctrl+C` 停止服务；端口占用会提示换端口，不结束其他程序。源码更新后，启动器检查前端内容指纹；构建过期且已有前端依赖时自动重建并显示进度，不再继续打开过期界面。首次安装、依赖变更或提示缺少依赖时重新运行安装命令；启动过程不会自动安装 npm 依赖。

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
conda activate InkOctoBot
.\scripts\install.ps1 -CurrentEnvironment -Dev
.\scripts\open.ps1 -CurrentEnvironment
```

macOS／通用入口：

```bash
conda activate InkOctoBot
./scripts/install.sh --current-environment --dev
./scripts/open.sh --current-environment
# 等价：python scripts/manage.py install --current-environment --dev
```

旧 `-EnvironmentName InkReference` 参数仅兼容复用**已经存在**的命名 Conda 环境，环境不存在时提示手动配置，不再自动创建。旧 `setup`／`start` 入口保留为兼容入口。Conda 是可选的开发者工具，打包后的应用用户无需配置它。

### 更新后仍看到旧界面或安装失败

新版启动器会检查源码与 `frontend/dist` 的内容指纹，过期时使用已有前端依赖自动重建；依赖缺失或构建失败会停止并给出安装指引。旧的已运行服务仍需重启：先关闭旧窗口，并在启动终端按 `Ctrl+C` 停止旧服务，再使用同一套环境参数启动。更新依赖时可完整执行以下流程，例如当前手动激活的 `InkOctoBot`：

```powershell
git switch main
git pull --ff-only origin main
.\scripts\install.ps1 -CurrentEnvironment
# 等依赖安装和前端构建全部成功后启动
.\scripts\open.ps1 -CurrentEnvironment
```

默认项目 `.venv` 则省略两条命令中的 `-CurrentEnvironment`。旧的独立 `InkReference.exe` 或桌面快捷方式不会随源码 `pull` 更新；源码使用仓库内的启动脚本，打包应用使用新的构建包。

若提示 `pip is missing in project .venv`，而你需要使用已激活的 Conda 环境，请加 `-CurrentEnvironment`；当前 Conda 环境也缺少 pip 时可执行 `conda install -n InkOctoBot pip`。若需要继续使用项目 `.venv`，可在 Windows 执行 `.\.venv\Scripts\python.exe -m ensurepip --upgrade`，然后重试默认安装命令。

若日志明确显示 `Cannot uninstall packaging None` / `no RECORD file was found`，且修复提示对应 `packaging==26.0`，在发生错误的当前环境修复安装记录后重试：

```powershell
python -m pip install --ignore-installed --no-deps packaging==26.0
.\scripts\install.ps1 -CurrentEnvironment
.\scripts\open.ps1 -CurrentEnvironment
```

此命令针对损坏的安装记录；项目安装仍遵循 `requirements-lock.txt`，无需修改锁定版本。修复其他包时按实际报错的包名和版本处理。

## 快捷修复

侧栏底部的“快捷修复”适用于报错和界面行为不符合预期的反馈：

1. 选择功能位置，填写复现步骤、实际结果和预期结果，生成诊断提示词。提示词包含本机环境、近期请求／错误及相关项目源码；正文、API Key 和请求内容不自动采集。可先查看内容，再复制到 ChatGPT 或其他模型。
2. 将模型返回的 `inkreference.repair.v1` JSON 粘贴回应用，点击“检查修复方案”。预览会列出修改文件、具体差异与检查项目；文件已变化或替换内容不唯一时停止，重新生成诊断即可。
3. 源码版点击“应用修复”后保存原始字节备份，执行 Python／JSON 检查；涉及前端时再运行 TypeScript 检查和 Vite 构建。进度和结果在弹窗中展示。失败自动恢复本次已写入的文件；成功后可回滚。前端构建成功可刷新页面，修改后端或脚本后需重启应用。

修复只针对现有项目源码，不修改资料库、原件、凭据或依赖清单，也不执行模型返回的命令。语法和构建通过后仍需复测发生问题的操作。变更预览是应用入口，不增加重复确认。

打包应用不能直接改写 EXE／APP 内的 Python 或前端源码；可生成诊断并导出方案。开发者在对应版本的源码环境生成新诊断，再粘贴该方案，应用会使用当前会话并重新核对每个原文件哈希，之后按相同流程预览和应用。源码不匹配时应把新诊断交给模型重新生成方案。完整协议与字段示例见 [api-contract.md](docs/api-contract.md#local-diagnostics-and-reviewed-repairs)。

诊断会话与预览有效期为两小时，重启后需重新生成；源码备份与修复记录在应用数据目录的 `quick-repairs` 中保留，可在“本机恢复”中查看与回滚。回滚先核对所有备份，只恢复仍匹配修复前后哈希的文件；有后续修改时停止，不覆盖新工作。“本机恢复”也可为选定作品重建章节索引，只重算派生缓存，保留原件、正文和历史。完整项目资料迁移继续使用“备份与恢复”，修复源码备份与项目备份分别处理。

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

“全文与清洗”使用一条连续流程：**上传 TXT／PDF／EPUB → 必要时转换为 TXT → 自动识别章节 → 扫描损坏和非正文 → 人工核对与保存 → 导出清洗后 TXT**。

顶部“新建作品”用于创建作品并上传文件；先选中已有作品，再点击“上传文件”，打开后目标固定，不必选择保存位置。左侧列表只显示书名与一位小数的万字数。选择“更新正文”会用新文件替换当前全部正文；“补充章节”会将新文件接在当前末尾。TXT 可选择编码，PDF 按需选择 MinerU 文字层或 OCR 设置，EPUB 自动按阅读顺序转换。两种上传方式都生成新版本，原始文件字节与历史正文保持不变。上传后的转换、解析和扫描有阶段与进度，失败可重试，处理中的任务可取消。

工作区依次为“原始文件、分卷与章节识别、正文”。原始文件展示来源、上传时间和格式；历史版本通过“历史”按需打开。章节格式、自定义规则与分卷方案集中在第二页。正文集中显示目录、标题、正文、删章与“批量清理”入口；目录没有复选框。开始分析后可查看进度并跳转“分析与审核”。

目录统一显示“第X章 标题”；正文区只含正文，连续空行折叠为一个换行。章号跳过时，缺少的每一章有独立空白项，例如第 11 章后直接出现第 17 章，会补出第 12 至 16 章的目录占位；可搜索章号、填写标题和正文后保存。占位只用于目录，补写前不会改变历史版本或已有分析任务的章节索引。分卷重新编号与同号章节分别处理，原文不会因重复编号被丢弃。

正文图例集中在同一区域：红色为可清理内容，紫色为需手动修复的字符，空章表示待补充；目录整项同步高亮。标题和正文显示原有内容及命中范围，字符问题可定位到所在页，结构问题列出具体原因。“批量清理”弹窗按章节列出标题中的求订阅、求月票等明确提示及正文中的命中片段，可逐片段保留或清理，也可按章选择或全选；确认清理后生成新版本，勾选本身不会删除正文。问题数量按章节统计。编辑标题或正文后出现未保存标识和保存按钮；切章或离开时保护草稿。分页保存原子提交本次标题和正文修改，保留分隔换行及未显示的正文。

完成处理并保存后，点击“完成清洗”，检查提示后导出清洗后的 TXT。最终 TXT 使用规范章号与标题，正文内和章节之间都只使用一个换行；尚未补齐的章节保留空章标题，便于后续补充。原始下载与精确版本下载继续作为兼容 API 保留，不作为常驻界面按钮。

EPUB 按 spine 顺序转换为 Markdown，再渲染为纯 TXT，不需要 MinerU。PDF 使用 **MinerU 4**：默认 `flash + txt` 提取文字层，扫描件使用 `standard + ocr` 并准备对应模型；完整块渲染保留可能被分类为页眉的正文。后端继续保留已有 Markdown／TXT 转换输出 API，GUI 将转换并入上传设置与进度。

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

“分析与审核 → 作品分析”以作品为中心，保留“特征提取、剧情大纲、角色、设定、文本特征”五个叙事子页。先在“特征提取”选择正文版本和工作流，创建模型提取任务或仅用于复制提示词／粘贴结果的任务。每段的剧情、角色、设定、风格分别预览、修订和确认；完成一节不会代替其他节的审核。整批提取只生成候选，确认后的内容才进入作品分析。

“剧情大纲”提供故事时间线、分段浏览和大纲粒度调整；“角色”保留外貌、性格、经历与语言样例；“设定”按类别管理并记录逐章变化；“文本特征”查看全书和分段的风格、开篇及章节节奏。分析区已移除原始文件和预处理子页，相关操作在“全文与清洗”完成。“提取任务与素材审核”保留原有梗概、人物、事件和桥段的证据审核及 v8 素材入库。

设定集可从“作品分析 → 新建设定集”直接创建，使用独立的原始文本条目、静态角色、设定及核心冲突／高概念视图。“导出设定集 TXT”包含四类内容，以可读 UTF-8 结构保留换行和字段；“导入设定集 TXT”可选择合并或替换，普通 TXT 作为一个原始条目导入。提取时可把已有条目、设定和角色合成固定的材料版本；翻译和大纲概括都先显示结果预览，由用户决定应用。手工粘贴的参考分析允许经过人审的上游格式；只有满足连续原文证据要求的素材才可写入 v8 素材库。

作品标题旁的“作品笔记”保存评分、喜欢原因和章节笔记；“分析历史版本”可恢复以前的作品分析，恢复后仍可继续修改和审核。当前复刻范围与尚存差异（例如自动编码往返修复、逐上传文件范围删除）见 [逐项审查](docs/reference-parity.md)，不会把缺字诊断当成已经恢复了原文。

LangGraph 持久化每块响应与 checkpoint。失败、取消或关闭后可手动恢复，完成块不会重复请求，重启应用不自动调用模型。模型素材候选附原文引用及位置，修订和确认入库后才写入素材表；已有作品的人工字段保持原值。每次请求与重试单独统计 token，区分提供商实际计数、规划预估和未知值。费用根据配置价格估算；取消会在当前请求边界停止后续调用，已经发出的请求仍可能计费。

## 数据与导出

| 系统 | 默认数据目录 |
|---|---|
| Windows | `%LOCALAPPDATA%\InkReference` |
| macOS | `~/Library/Application Support/InkReference` |

可用 `INKREFERENCE_DATA_DIR` 或启动参数 `--data-dir`／`-DataDir` 指定目录。数据与源码、应用包分离，数据库启动时自动迁移；同一目录只允许一个应用实例。

记录按外部 ID 识别，同名作品的不同版本分别保留；日期精度、换行、空白和“不确定”等内容保持字符串。所有素材库格式都先预览，校验通过后整批事务提交，可选择跳过、更新或报错。编辑检查 revision，关联记录删除需要明确确认。

“导出素材库”提供三种互通格式：

- **DB（推荐）**：一个 `InkReference-library.db`，包含 `works`、`characters`、`cliches`、`work_cliches`、`material_relations` 和 `_manifest`。这是可移植素材数据文件；内部工作数据库请通过项目备份迁移。
- **CSV**：一个 ZIP，包含五张表各自的 CSV 与 `manifest.json`。导入整个 ZIP，或同时选择这六个文件；空表也应保留表头。清单与批次标记帮助识别混用的文件。遇到 NUL 控制字符会提示改选 DB／XLSX 保留原文。旧格式仅兼容同时选择 `works_library_v8.csv`、`character_database_v8.csv`、`cliche_database_v8.csv` 三张完整表，旧格式不包含新增的自定义关系表。
- **XLSX**：一个工作簿，含五张数据工作表与隐藏的 `_manifest`；长文本必要时保存到 `_long_text`。请保留辅助工作表、文本格式和全部数据表，不能单独导入其中一张；公式、被 Excel 改写的日期及错误单元格会得到具体位置提示。

完整批次保证表间引用可核对。导入若缺少作品或桥段表，会直接列出缺失文件；人物引用的作品 ID、名称或版本不一致时，会指出人物文件与作品文件及相关 ID。素材库交换保留业务数据和关系；本机编辑 revision 由目标库管理，历史与全文通过完整项目备份保留。

其他迁移入口：

- **兼容 CSV API**：原有单表／v8 CSV API 保留给旧集成，主界面使用上面的整库流程。
- **全部业务表 CSV**：素材、全文、转换、Skills、工作流、分析和用量的每张业务表各一个 CSV，用于查阅及迁移。
- **完整项目备份**：五个 SQLite 数据库、原始素材导入文件及大小／SHA-256 清单，可验证并恢复为独立工作区，保留当前项目。备份不包含凭据值、安装环境或模型权重；恢复后重新配置模型凭据。

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
