# 第一批：中文文本处理复用验证

第一批交付可离线运行的纯函数和可重现实验入口。应用首批 GUI 仍围绕 CSV 素材库；全文上传、模型调用、LangGraph 工作流和 Skill 上传属于后续批次。

## 实际复用

从 InkOctoBot 提交 `51c82b6f01f6a5b5210e1bae9302be257b3b8083` 的 `reference_pipeline/chapter_parser.py` 改造中文数字转换、章节标题正则、章节格式评分与部分乱码诊断签名。来源、改造和 MIT 授权见 [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)。

保留原文与清洗版本，不覆盖原文。纯函数返回版本哈希、逐阶段变换记录、疑似问题范围、章节范围和完整覆盖检查。范围单位为 Python Unicode 字符索引，不是字节、UTF-16 或 token。章节 `text` 包含标题和正文，是覆盖与重建依据；`content` 从标题后开始。前置文本作为独立段保留。

默认清洗仅处理开头 BOM、HTML entity、换行和控制字符。HTML entity 解码为原符号；疑似 ID、乱码替换符、HTML 标签、作者题外话等保留并标记。仅在 `remove_random_ids=True` 时移除匹配到的 ASCII ID，且不会吞掉相邻汉字。该选项需要调用方展示预览并由用户明确选择。

## 运行方法

先按 README 建立并安装本项目的 Python 开发环境。取得可信的原仓库开发副本，切换到上述提交：

```powershell
git clone https://github.com/ZichenFrankFu/InkOctoBot.git ..\InkOctoBot-review
git -C ..\InkOctoBot-review checkout 51c82b6f01f6a5b5210e1bae9302be257b3b8083
python scripts/verify_reuse.py --source-repo ..\InkOctoBot-review --output data/reuse-check.json
python -m pytest tests/test_text_processing.py
```

脚本检查源提交和两个旧文件与 git 的一致性，仅允许 Windows checkout 的 CRLF/LF 差异，同时记录提交内容与本地文件的哈希。调用旧仓库真实函数，不复制一套旧的错误实现作为基线。`--source-repo` 是开发者可信代码路径，不可接受网页用户上传的 Python 或未审查仓库。脚本输出不包含用户小说或上传 CSV，结果文件保存在本地忽略目录。

| 合成边界 | 旧函数 | 新版本要求 |
|---|---|---|
| 无章节标题、超过 3,000 字并含尾标 | `detect_chapters` 的整除兜底遗漏最后不足 3,000 字部分 | 所有字符按范围拼接重建 |
| `&quot;` 和 `&amp;` | `strip_deletable_garbled` 删除引号和 `&` | 解码为符号，保留句意 |
| 汉字紧邻混合字母数字 ID | 同函数的消耗式边界匹配吞掉相邻汉字 | 默认保留；确认移除时只删 ID |
| 超过 32,000 字的单章 | `build_segment_text_chunks` 最终经 `_build_segment_text` 截断 | 字符块完整覆盖单章，尾标保留 |

`all_expected_comparisons_pass=true` 仅表示四个旧缺陷得到复现且新版本对应保真要求通过。`tests/test_text_processing.py` 还检查短篇多章、前置文字、Windows 换行、纯正文、空文档、边界长度、中文数字和诊断范围。内置四章短中文样本只用于保真验证，含线索与身份披露；没有自动判定其剧情抽取质量。

## 集成接口

```python
from inkreference.text_processing import analyze_text, chunk_text

result = analyze_text(source_text)
assert result["chapter_analysis"]["coverage"]["complete"]
cleaned = result["cleaned_text"]
# 每块 start/end/text 可完整重建输入；这里的限制单位是字符。
chunks = chunk_text(cleaned, max_chars=3000)
```

`analyze_text` 不读文件、不调用模型、不写数据库。上传入口后续应保留原始字节并显式、严格解码；文件编码不确定时让用户预览选择，不能用 `errors="replace"` 隐藏损失。执行模型提取前还需使用对应 tokenizer 进行块预算，记录每块输入范围和状态。字符覆盖完整不能证明模型分析覆盖完整。

## 明确边界与后续实验

- 第一批没有运行真实本地模型、API 或 LangExtract，不提供人物／剧情／桥段抽取准确率。
- LangExtract 保留为候选适配器；下一批再用同一中文章节样本比较真实模型结构化结果、证据位置、费用和耗时。
- 没有复用旧系统的全局模型路由、任务完成判定、Skill 执行器或自动乱码重编码。
- 在 Linux 环境通过的纯函数检查不能代表 Windows GUI、模型加载或安装包验证。
- 相似 ID 也可能是密码、编号或故事线索，所以移除始终为可选项。疑似乱码可能无法恢复，原字符会继续保留供核对。
- 标题识别目前优先编号格式；混合格式、无编号标题等可以先保留为完整兜底块，后续再加可配置规则。
