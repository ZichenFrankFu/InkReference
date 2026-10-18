# 第一批架构

## 运行方式

React/TypeScript 前端通过同源 `/api` 请求 FastAPI。Python 负责 SQLite、CSV 校验、事务和文件响应，前端负责导入预览、表格、关联查看及编辑。开发时前端代理 API；构建后的静态文件由 Python 服务。

Windows 入口为 `scripts/setup.cmd` 与 `scripts/start.cmd`；实际脚本兼容 PowerShell 5.1。`python -m inkreference` 默认绑定 `127.0.0.1:8000`，等待健康检查后打开本地浏览器。当前交付是本地 Web GUI，pywebview 桌面窗口和安装包在后续批次提供。

接口及前后端共享类型以 [api-contract.md](api-contract.md) 为准。核心模块为 `inkreference.database`、`inkreference.csvio` 和 `inkreference.api`。

## 数据边界

SQLite 是当前素材数据的权威来源。默认数据目录为 `%LOCALAPPDATA%\InkReference`，可用 `INKREFERENCE_DATA_DIR` 或 CLI 的 `--data-dir` 指定。项目环境和构建产物留在源码目录；用户数据、上传原件和数据库留在私有数据目录。

三个外部素材表以现有 ID 识别记录。字段值按字符串保存，保留单元格换行、日期精度、空白与未知值。作品标题不充当去重键。数据库结构变化通过显式迁移完成。

作品与桥段只维护一份权威关联集合。两侧 CSV 中的关联列从该集合生成；人物通过作品 ID 关联作品。未分类的自由文本原样保存。导入时校验关系目标与两侧声明的一致性，冲突交给用户处理。

## 写入与导出

CSV 经过标准解析器、严格解码、表头识别、ID 与关系校验后生成导入预览。预览本身不修改素材表；提交重新校验当前数据库，再以单个事务写入。任何错误回滚整批写入。

重复 ID 的行为由显式策略控制：跳过、更新或报错。创建和编辑检查关系完整性；编辑与删除需要记录 revision，避免两个页面互相覆盖。有关联的删除默认阻止，级联操作需在 GUI 说明影响并确认。

兼容导出保留 v8 的三份文件与字段。完整导出为所有业务表生成 CSV 和 `manifest.json`。输出为 UTF-8 BOM；标准 CSV quoting 保留分隔符、引号与单元格换行。完整导出目前指结构化素材表，未来的全文、Skill 和运行记录需要单独项目备份格式。

## 后续 AI 扩展位置

未来引入独立 `reference_core`，把清洗、分章和提取函数与 API、全局配置及 UI 解耦。模型调用统一通过可替换适配层，LangGraph 节点接收固定版本的输入，保存块级结果及 checkpoint。工作流定义与画布布局分别管理。

Skill 优先使用声明式包：说明、提示词、Schema 与示例。上传文本是数据；普通 Skill 不自动执行 Python。需要执行代码的开发者插件另行设计权限和运行边界。

全文清洗将保留原始文件并生成新版本；提取结果保留证据位置。修改某章只使相关分析失效。各次运行固定全文、工作流、Skill、模型参数版本，并通过去重键避免恢复时重复写入。

token 账本会区分提供商实际用量、tokenizer 预估和未知值；每次真实请求及重试分别记账。上述模型、LangGraph、全文清洗和 Skills 能力尚未接入第一批素材库。
