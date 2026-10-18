# InkReference 架构

React/TypeScript 工作台通过同源 `/api` 与 FastAPI 通信。Windows PowerShell 与 macOS shell 入口共用 `scripts/manage.py`，源码默认在项目 `.venv` 安装和启动；开发者可显式复用自行配置的环境，Conda 并非依赖。`--desktop` 用 pywebview 打开 Windows WebView2 或 macOS WebKit 原生窗口，浏览器入口复用相同服务。打包后收集 Python 和前端运行依赖，用户无需安装开发环境。启动器仅允许回环地址，默认 `127.0.0.1`。界面使用 React Flow 处理画布、CSS 动画处理过渡，并尊重 reduced-motion。

## 本地安全边界

HTTP 中间件检查所有请求的回环 Host，API 的读写请求都拒绝外站／`null` Origin 及浏览器 cross-site 请求。默认仅允许同源前端；开发时可通过 `INKREFERENCE_DEV_ORIGIN` 显式增加一个回环来源。Vite 代理仍携带浏览器 Origin，因此开发时也需配置该例外。API 与 HTML 使用 no-store，生产页面 CSP 限制脚本、连接和资源来源，并附带 MIME、referrer 和 frame 限制。CDN 文档页停用，接口结构保留为本地 `/openapi.json`。该服务面向单用户本机使用，不提供局域网监听或多用户认证入口。

素材、全文、转换与规则清洗均在本机处理。模型连接分为 `local` 与 `remote`：local 只接受 localhost／字面量回环地址；remote 强制 HTTPS。用户主动点击测试连接或读取模型列表即执行该操作，不追加确认。新建远程分析时 GUI 只确认一次目标服务与模型，任务保存已确认目标，之后开始／恢复不重复弹窗；提交时仍校验目标指纹，阻止其他窗口修改配置造成无声外发。页面加载或应用重启不触发外发模型请求。

## 数据与版本

应用数据在 Windows 默认使用 `%LOCALAPPDATA%\InkReference`，macOS 使用 `~/Library/Application Support/InkReference`；`INKREFERENCE_DATA_DIR` 或 `--data-dir`／`-DataDir` 可指定另一目录。同一工作区通过系统级文件锁限制一个应用实例，防止另一个端口上的进程错误恢复仍在运行的任务。

| 数据库 | 内容 |
|---|---|
| inkreference.sqlite3 | v8 作品、人物、桥段、权威关联、revision 墓碑和已审核分析的幂等入库记录 |
| workbench.sqlite3 | 不可变全文原件及清洗版本、转换任务、模型配置、逐请求用量、分析任务与块级结果 |
| skills.sqlite3 | 不可变声明式 Skill 版本 |
| workflow_definitions.sqlite3 | 不可变工作流定义、独立的可变画布布局 |
| checkpoints.sqlite3 | LangGraph 状态与中断 checkpoint，禁用 pickle 和任意模块构造 |

上传原件作为字节与 SHA 保存，清洗生成新版本。疑似乱码保留并定位；不能恢复的文字不假装修复。明确的原文、清洗版本、字符范围和块覆盖状态可以追溯。所有范围采用 Unicode codepoint，前端按后端分页读取，避免 UTF-16 偏移错误。大文本不一次挂载到编辑控件。

## 转换与模型

EPUB 转换复用安全 spine 读取、markdownify 和 markdown-it-py。PDF 通过独立可信 Python 子进程调用 MinerU 4 SDK，输入文件路径仅作为参数，不通过 shell。默认全文原生文本解析；扫描件 OCR 需要独立准备的模型。FULL Markdown 渲染保留可能被标为页眉的正文。子进程支持取消，关闭时先终止全部进程树；任务恢复需要用户操作。

ModelService 通过 HTTPX 连接原生 Ollama 或 OpenAI-compatible Chat Completions 服务（含本地 LM Studio/vLLM）。GUI 提供本地／商业服务预设、手填地址和模型、显式模型列表读取、当前草稿测试和可折叠参数。草稿测试使用固定消息，不保存草稿或读取全文。

凭据层支持 `session`、`vault`、`environment`：会话 Key 只在内存；环境变量只保存名字；系统密钥库通过 keyring 的批准操作系统后端保存，按工作区路径隔离。密钥库不可用时失败关闭，允许用户改选会话或环境变量，禁止明文／第三方后端回退。读取配置只返回凭据状态，保存响应、模型快照、前端浏览器存储及备份不包含 Key。恢复到新目录后重新配置凭据。清除凭据会移除会话／保险库内容与环境变量引用，不修改系统环境变量。

每次真实请求及重试都有独立账本，进程中断的请求标为结果／费用未知。提供商实际计数、规划预估、未知值独立记录，价格与币种由用户配置。保存模型配置本身不会调用模型；任务确认绑定固定目标而非整个配置的未来地址。

## LangGraph 与审核

StateGraph 执行 prepare → extract → validate → advance 循环，最终进入人工 review。执行上下文显式禁用 LangSmith tracing 与父级追踪，避免继承外部环境开关上传正文或模型响应。定义固定 Skill 版本、节点开关、自定义提示词、分块上限、重试与预算；画布布局不改变版本化执行定义。分块前按完整提示词估算上下文预算，保持全文无缺口、无重叠；预算不足在请求前停止。

任务固定全文版本哈希和工作流、Skill、模型快照。每块先持久化响应，再验证结构及精确引文；checkpoint 间隙恢复会复用已保存响应，避免重复请求。失败、取消和关闭都保留状态，重开不自动请求模型。待审核结果只能显式修订；revision 检查防止覆盖别人的修改，已进入人工审核的任务不能被恢复执行覆盖。

审核通过后通过已有 DataStore 的同一事务写素材与 `analysis_commits`。即使主库已提交、任务库尚未标 approved 就退出，再次审批也只返回原映射。关联现有作品时保留其人工字段；新人物和桥段关系仍遵守主库约束。已关联全文的作品 ID 受到删除保护。

## 导出与恢复

兼容 CSV 保留三张 v8 表的字符串、字段、版本、未知值和多行内容。全部业务表导出对四个业务数据库的每张表输出 CSV；BLOB 使用 hex。完整项目备份使用 SQLite backup API 产生一致文件快照，包含五个数据库及原始 CSV 导入文件，并附大小和 SHA 清单。活跃分析/转换结束或取消后才可备份。

恢复验证归档路径、大小、哈希与 SQLite 完整性，创建独立目录，保留当前项目。会话 Key、系统密钥库内容、环境变量值、运行锁、日志、安装环境与模型权重不进入备份；非秘密的模型配置与凭据引用元数据可以保留。数据库与完整项目备份包含原件、正文和分析结果，未整体加密，不能把 Key 隔离等同于全文加密。接口详见 [api-contract.md](api-contract.md)，运行与验证见 README 和 [validation.md](validation.md)。

## 环境与分发

`scripts/manage.py` 统一处理 `install`、`open`、`doctor` 与 `exec`。源码需要 Python 3.11–3.13、Node.js 22／24；默认创建项目 `.venv`。`--current-environment` 明确复用开发者当前解释器，`--conda-environment`／旧 PowerShell `-EnvironmentName` 只查找已有 Conda 环境，不创建环境、修改全局配置或自动下载系统工具。运行依赖默认不包含测试依赖；`--dev` 与 `--mineru` 分别选装。

Windows 通过 PyInstaller 生成应用目录和 ZIP，可选 Inno Setup 安装程序；macOS 在对应架构的 Mac 上生成 `.app` 和 ZIP，可选 DMG，最低系统版本为 macOS 13。包内包含前端、Python、pywebview、LangGraph SQLite 和相应系统凭据后端，不打包用户资料、MinerU 或权重。Windows 依赖 WebView2 Runtime，macOS 复用系统 WebKit；macOS 包添加并验证本机 ad-hoc 签名，当前不提供 Developer ID 签名或 Apple 公证。构建与冻结服务烟雾检查不代表原生窗口交互已验收，具体证据以 [validation.md](validation.md) 为准。
