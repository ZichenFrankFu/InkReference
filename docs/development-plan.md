# 开发批次与验收状态

## 当前版本：v14

当前功能与入口以 [README](../README.md) 和 [完整使用手册](gui_manual.md) 为准；下面的第一至三批保留当时开发记录，不代表当前仍显示旧画布或强制使用 Conda。

当前为 Windows／macOS 本地数据库工作台，支持源码默认 `.venv`、显式复用已有环境及独立桌面打包。新分析固定四个 LangGraph 阶段，Agent／Skills 只调整阶段内容要求和模型参数；历史任意节点／画布数据兼容保留。当前章节存储、Schema 4 编年史和动态角色、双栏提示词审核、跨库搜索、正文替换、统一任务管理、A/B、章节范围、日志、索引与数据保留规则分别在 [架构](architecture.md) 和 [API](api-contract.md) 记录。

本轮实际测试与平台 CI 状态在 [validation.md](validation.md)；上一轮记录的容量边界与待优化项目见 [review-v13.md](review-v13.md)。后续开发优先真实模型／向量校准、原生系统验收与大规模存储，不能把合成数据通过视为真实模型质量证明。

## 历史批次

## 第一批：结构化素材库

已合并。SQLite、FastAPI、React、三张 v8 CSV 识别/预览/事务导入、冲突策略、编辑、关联与导出。原始三份附件的全部字段与行顺序完成语义往返验收，用户数据不提交。

## 第二批：全文与默认分析

已实现：原件与清洗版本、严格编码、TXT/Markdown/SRT/DOCX/EPUB、章节与块完整覆盖、差异/乱码诊断；EPUB→Markdown→TXT；MinerU 4 PDF→Markdown→TXT。真实 12 页中文 PDF 的原生文本层路径已验证，扫描件 OCR 模型质量另行验收。

已接通真实 LangGraph StateGraph 和 SQLite checkpoint、固定版本与配置、逐块证据验证、重试、预算、失败恢复、关闭后手动继续、人工修订保护与幂等入库。原生 Ollama 和 OpenAI-compatible 接口可配置；自动测试使用明确的 transport 替身，不据此宣称真实模型的剧情提取质量。

## 第三批：自定义与桌面

已实现：工作流节点开关、提示词、分块、重试和预算；React Flow 画布与独立布局版本；声明式 JSON/SKILL.md/ZIP 上传、Schema 校验、不可变 Skill 版本；模型快照、逐请求 token/费用账本、多币种配置；全部业务表 CSV 与完整项目备份/独立恢复。

GUI 统一为中文工作台，耗时操作提供实际进度或不定进度条；长文分页，人工草稿保护，失败可见。PowerShell 指定 Conda 环境安装/复用、桌面/浏览器入口、pywebview 原生窗口、PyInstaller portable 包及 Inno Setup 构建脚本已加入。自动化 Windows 安装/启动与实际原生窗口使用应区分验证。

## 仍需真实环境评价的项目

- 用户本地模型与所选 API 的中文剧情、人物和桥段提取效果；自动测试已覆盖协议与数据保真，不代表模型质量。
- MinerU 扫描件 OCR、公式/复杂多栏的实文档效果与 GPU 性能；不把原生文本层检查视作 OCR 验收。
- 用户 Windows 桌面上的 WebView2 交互与安装包安装/卸载体验；CI 的 HTTP 或 fake-window 检查不能替代这一环节。
- 任意拓扑的通用 Agent 编程、Python Skill 插件和视频转写不在当前参考库功能中。

## 复用原则

基础设施使用已有依赖：LangGraph/SQLiteSaver、React Flow、pywebview、HTTPX、markdownify、markdown-it-py 和可选 MinerU。InkOctoBot 的章节解析、乱码签名与提取提示词按 MIT 授权进行局部改造；精确提交、路径和取舍记录在 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。不为增加复制数量引入平台全局配置、危险重编码、强制猜日期或截断全文的旧行为。
