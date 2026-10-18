# InkReference 架构

React/TypeScript 工作台通过同源 `/api` 与 FastAPI 通信。Windows PowerShell 在指定 Conda 环境中安装和启动；`--desktop` 用 pywebview 打开原生窗口，浏览器入口复用相同服务。默认只监听 `127.0.0.1`。界面使用 React Flow 处理画布、CSS 动画处理过渡，并尊重 reduced-motion。

## 数据与版本

应用数据默认在 `%LOCALAPPDATA%\InkReference`；`INKREFERENCE_DATA_DIR` 或 `-DataDir` 可以指定另一目录。同一工作区通过系统级文件锁限制一个应用实例，防止另一个端口上的进程错误恢复仍在运行的任务。

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

ModelService 通过 HTTPX 连接原生 Ollama 或 OpenAI-compatible 服务（含本地 LM Studio/vLLM）。模型配置快照不含 Key；会话 Key 只在内存，环境变量通过名字引用。每次真实请求及重试都有独立账本，进程中断的请求标为结果/费用未知。提供商实际计数、规划预估、未知值独立记录，价格与币种由用户配置。

## LangGraph 与审核

StateGraph 执行 prepare → extract → validate → advance 循环，最终进入人工 review。定义固定 Skill 版本、节点开关、自定义提示词、分块上限、重试与预算；画布布局不改变版本化执行定义。分块前按完整提示词估算上下文预算，保持全文无缺口、无重叠；预算不足在请求前停止。

任务固定全文版本哈希和工作流、Skill、模型快照。每块先持久化响应，再验证结构及精确引文；checkpoint 间隙恢复会复用已保存响应，避免重复请求。失败、取消和关闭都保留状态，重开不自动请求模型。待审核结果只能显式修订；revision 检查防止覆盖别人的修改，已进入人工审核的任务不能被恢复执行覆盖。

审核通过后通过已有 DataStore 的同一事务写素材与 `analysis_commits`。即使主库已提交、任务库尚未标 approved 就退出，再次审批也只返回原映射。关联现有作品时保留其人工字段；新人物和桥段关系仍遵守主库约束。已关联全文的作品 ID 受到删除保护。

## 导出与恢复

兼容 CSV 保留三张 v8 表的字符串、字段、版本、未知值和多行内容。全部业务表导出对四个业务数据库的每张表输出 CSV；BLOB 使用 hex。完整项目备份使用 SQLite backup API 产生一致文件快照，包含五个数据库及原始 CSV 导入文件，并附大小和 SHA 清单。活跃分析/转换结束或取消后才可备份。

恢复验证归档路径、大小、哈希与 SQLite 完整性，创建独立目录，保留当前项目。会话 Key、环境变量、运行锁、日志、安装环境与模型权重不进入备份。接口详见 [api-contract.md](api-contract.md)，运行与验证见 README 和 [validation.md](validation.md)。
