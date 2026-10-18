# InkReference

Windows 优先的本地写作素材库，用于整理作品、人物和桥段，保留具体作品版本及它们之间的关联。

第一批提供 SQLite 数据库、React 管理界面、CSV 导入预览、增删改查和 CSV 导出。程序在本机运行，浏览器打开界面；当前版本尚未打包为 EXE。

## Windows 安装与启动

准备 Python 3.12 或 3.11，以及 Node.js 22 或 24。Python 3.13 也受支持。确认 Git、Python 和 Node.js 可从终端使用；安装依赖时需要网络。

1. 克隆或下载本仓库，解压到可写目录。
2. 双击 `scripts\setup.cmd`。首次运行会在项目目录建立独立 `.venv`，安装 Python 依赖并构建前端。
3. 安装成功后双击 `scripts\start.cmd`，浏览器会打开 <http://127.0.0.1:8000>。
4. 使用导入入口选择三个 CSV，查看预览后提交。不同表可以一起导入；应用根据表头识别类型。

也可以在仓库根目录用 PowerShell 执行：

```powershell
.\scripts\setup.cmd
.\scripts\start.cmd
```

让程序保持运行；关闭启动终端或按 `Ctrl+C` 可停止服务。修改代码或拉取更新后，重新运行 `setup.cmd` 更新依赖和前端构建。

启动选项：

```powershell
# 不自动打开浏览器
.\scripts\start.cmd -NoOpen

# 8000 被其他程序使用时，指定另一个端口
.\scripts\start.cmd -Port 8001

# 使用指定的数据目录；支持空格和中文路径
.\scripts\start.cmd -DataDir "D:\写作资料\InkReference"
```

脚本支持 Windows PowerShell 5.1，包装器仅为这次本地脚本进程设置执行策略，不改变系统全局策略。Python 查找顺序是现有项目环境、`py -3.12`、`py -3.11`、`py -3.13`，再检查 PATH 上的 Python。

安装脚本优先使用 `requirements-lock.txt` 约束已验证的 Python 依赖版本，执行 `pip install -c requirements-lock.txt -e ".[dev]"`；没有该文件时执行 `pip install -e ".[dev]"`。前端存在 `package-lock.json` 时使用 `npm ci`，否则使用 `npm install`，随后执行生产构建。

## 数据与导入规则

- 数据默认存放在 `%LOCALAPPDATA%\InkReference`，与源码分开。也可以设置 `INKREFERENCE_DATA_DIR`，或传入 `-DataDir`。
- 数据库启动时自动迁移。真实 CSV、数据库、全文、模型和密钥不提交到仓库；提交的测试仅使用合成数据。
- 以外部 ID 识别记录，同名作品的不同版本分别保存；日期精度、换行、空白和“不确定”等内容保留为字符串。
- 导入先预览，存在校验错误时不会写入。提交在一个事务内执行；重复记录可选择跳过、更新或报错。
- 编辑使用版本检查。存在关联的记录默认禁止直接删除，级联删除需要在界面确认。

一键导出提供两种 ZIP：兼容模式包含 v8 格式的三个 CSV；完整模式为每张业务表生成 CSV，并附格式说明 `manifest.json`。也可以单独导出一张表。CSV 使用 UTF-8 BOM，便于在 Windows Excel 中打开。

CSV 导出覆盖当前结构化素材。全文、模型调用和工作流版本的完整项目备份属于后续功能。

## 第一批范围

| 已实现的范围 | 后续开发 |
|---|---|
| 三类素材、SQLite 迁移、搜索和关联查看 | 全文上传、保真清洗与章节版本 |
| CSV 自动识别、导入预览和事务写入 | LangGraph 剧情、人物及桥段提取 |
| 创建、编辑、删除和 revision 冲突检查 | 自定义工作流、Skill 上传与试运行 |
| 三表兼容导出及全部业务表导出 | 本地/API 模型接入、token 与费用账本 |
| Windows 安装启动脚本、本机 Web GUI | pywebview 桌面窗口与安装包 |

AI 功能本批尚未接通。离线规则检查和复用比较不能代表真实模型提取质量。

## 开发

安装完成后，在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m inkreference --no-open
```

前端开发在 `frontend` 目录运行 `npm run dev`；Python API 与前端构建的约定见 [API contract](docs/api-contract.md)。发布前检查：

```powershell
Push-Location frontend
npm run typecheck
npm run build
Pop-Location
```

可信 InkOctoBot 检出版本的离线复用检查可通过下列命令重现；固定来源版本见第三方记录：

```powershell
.\.venv\Scripts\python.exe scripts\verify_reuse.py --source-repo "D:\projects\InkOctoBot"
```

说明文档：

- [第一批范围与验收](docs/batch-01.md)
- [已完成的检查与复现方法](docs/validation.md)
- [架构与数据规则](docs/architecture.md)
- [后续开发计划](docs/development-plan.md)
- [第三方来源和复用记录](THIRD_PARTY_NOTICES.md)
- [开发约束](AGENTS.md)

Windows 脚本在当前 Linux 开发环境中只能进行静态检查。Windows 实机安装、启动、中文路径及浏览器行为需要单独验收。
