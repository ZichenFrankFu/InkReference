"""Reviewable chapter diagnostics adapted from InkOctoBot's chapter_parser.

Upstream 51c82b6f01f6a5b5210e1bae9302be257b3b8083, MIT. The author-note,
paragraph-tail/fence and relative-length rules retain that implementation's
conservative dialogue guards. All proposals use exact version-local codepoint
spans; applying them never rebuilds or strips unrelated chapter text.
"""
from __future__ import annotations

import hashlib
import re
import statistics
from collections import Counter, defaultdict
from typing import Any, Callable

import regex

from .database import DataError
from .chapter_layout import chapter_label, chapter_sequence
from .text_processing import diagnose_text

LEGACY_AUTHOR_KEYWORDS = [
    "求收藏", "求订阅", "求推荐", "求月票", "求票", "推荐票", "月票", "打赏",
    "感谢书友", "感谢读者", "感谢支持", "请假", "断更", "章节缺失", "卡文", "码字",
    "作者菌", "作者大大", "本作者", "本章说", "晚点更新", "明天更新", "今日两更",
    "今日三更", "亲爱的读者", "书友们", "对不起大家", "盟主", "白银盟", "感谢打赏",
    "万赏", "万订", "月票榜", "上架感言", "完本感言", "作者说", "作者的话",
]
AUTHOR_KEYWORDS = [*LEGACY_AUTHOR_KEYWORDS, "PS："]
_POSTSCRIPT = re.compile(r"^ps\s*[:：]", re.IGNORECASE)
_TITLE_MARKERS = ["感言", "请假", "通知", "公告", "致歉", "本章说", "闲聊", "杂谈", "作者的话", "作者说"]
_DIALOGUE = re.compile(r"说道|[“”「」『』]")
_RULE = re.compile(r"^[ \t　]*[-=*＝—─]{3,}[ \t　]*$", re.MULTILINE)
_NOISE = [
    ("control_character", "异常控制字符", re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+")),
    ("html_comment", "采集注释", re.compile(r"<!--.*?-->", re.DOTALL)),
    ("html_tag", "HTML 标记", re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*(?:\s[^>]*)?>")),
    ("watermark", "网站水印", re.compile(r"@?追书神器|#追书神器")),
    ("template", "模板占位", re.compile(r"\{\{[^}\n]{1,200}\}\}")),
    ("invisible", "隐藏字符", re.compile(r"[\u200b\u200e\u200f\u202a-\u202e\u2060-\u206f\ufeff]+")),
]
_LABELS = {"author_note": "非正文", "missing_glyph": "字符缺失", "mojibake": "疑似乱码",
           "noise": "采集杂字", "length": "篇幅异常", "gap": "章节缺号", "duplicate": "章节重号"}
_TITLE_PROMOTION = re.compile(r"求(?:订阅|收藏|月票|推荐票?|票|打赏|支持)(?:[！!，,、 \t\u3000]*(?:求(?:订阅|收藏|月票|推荐票?|票|打赏|支持)))*[！!]*(?=$|[\s！!，,、。.）)】\]”」』])")
_TITLE_BRACKET = re.compile(r"[（(【\[][^(（)）【】\[\]\r\n]{1,80}[）)】\]]")


def chapter_title_span(chapter: dict[str, Any]) -> tuple[int, int]:
    """Locate the exact pure-title span within the pinned chapter text."""
    marker = str(chapter.get("raw_marker") or "")
    title = str(chapter.get("title") or "")
    end = len(marker.rstrip())
    if title and marker[:end].endswith(title):
        return end - len(title), end
    return end, end


def _heading_noise_spans(chapter: dict[str, Any]) -> list[tuple[int, int]]:
    """Propose promotional title fragments without touching number/real title."""
    start, end = chapter_title_span(chapter)
    title = chapter["text"][start:end]
    spans = []
    for bracket in _TITLE_BRACKET.finditer(title):
        interior = bracket.group()[1:-1]
        # Only a bracket composed of explicit solicitation phrases qualifies.
        residue = _TITLE_PROMOTION.sub("", interior).strip(" \t\u3000，,、！!。.")
        if not residue and _TITLE_PROMOTION.search(interior):
            spans.append((bracket.start(), bracket.end()))
    for match in _TITLE_PROMOTION.finditer(title):
        if not any(left <= match.start() and match.end() <= right for left, right in spans):
            left = match.start()
            while left > 0 and title[left - 1] in " \t\u3000":
                left -= 1
            spans.append((left, match.end()))
    return [(start + left, start + right) for left, right in spans]


def validate_settings(options: dict[str, Any]) -> dict[str, Any]:
    """Validate declarative preprocess settings; custom regex has real timeouts."""
    result = dict(options)
    formats = result.get("heading_formats", [])
    if not isinstance(formats, list) or len(formats) > 30 or any(not isinstance(x, str) or len(x) > 100 for x in formats):
        raise DataError(422, "章节格式必须为名称列表")
    custom = result.get("custom_formats", [])
    if not isinstance(custom, list) or len(custom) > 20:
        raise DataError(422, "自定义章节格式最多 20 项")
    names = set()
    for item in custom:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not 1 <= len(item["name"].strip()) <= 100:
            raise DataError(422, "请填写自定义格式名称")
        if item["name"] in names:
            raise DataError(422, "自定义格式名称不能重复")
        names.add(item["name"])
        template, expression = item.get("format", ""), item.get("regex", "")
        if not isinstance(template, str) or not isinstance(expression, str) or len(template) > 100 or len(expression) > 500:
            raise DataError(422, "自定义章节格式过长")
        if not template and not expression:
            raise DataError(422, "请输入章节格式或正则表达式")
        if template and template.count("N") != 1:
            raise DataError(422, "章节模板需要且仅需要一个 N 作为章节序号")
        if expression and not template:
            try:
                compiled = regex.compile(expression, regex.MULTILINE | regex.IGNORECASE)
                if compiled.groups < 2:
                    raise DataError(422, "章节正则需含两个捕获组：序号和标题")
            except regex.error:
                raise DataError(422, "章节正则表达式无效") from None
        if not isinstance(item.get("enabled", True), bool):
            raise DataError(422, "格式启用状态无效")
    keywords = result.get("author_keywords", AUTHOR_KEYWORDS)
    if not isinstance(keywords, list) or len(keywords) > 200 or any(not isinstance(x, str) or not 1 <= len(x) <= 50 for x in keywords):
        raise DataError(422, "作者题外话关键词最多 200 项，每项 1 至 50 字")
    rules = result.get("garble_rules", [])
    if not isinstance(rules, list) or len(rules) > 50:
        raise DataError(422, "自定义杂字规则最多 50 项")
    for item in rules:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not 1 <= len(item["name"]) <= 100:
            raise DataError(422, "请填写杂字规则名称")
        if not isinstance(item.get("pattern"), str) or not 1 <= len(item["pattern"]) <= 500:
            raise DataError(422, "杂字规则需为 1 至 500 字")
        if not isinstance(item.get("enabled", True), bool) or not isinstance(item.get("literal", False), bool):
            raise DataError(422, "杂字规则选项无效")
        try:
            regex.compile(re.escape(item["pattern"]) if item.get("literal") else item["pattern"])
        except regex.error:
            raise DataError(422, "杂字正则表达式无效") from None
    result.update(heading_formats=formats, custom_formats=custom,
                  author_keywords=list(dict.fromkeys(keywords)), garble_rules=rules)
    return result


def _proposal(text: str, start: int, end: int, kind: str, label: str, reason: str,
              enabled: bool = True, removable: bool = True, source_hash: str | None = None) -> dict[str, Any]:
    identity = f"{source_hash or hashlib.sha256(text.encode()).hexdigest()}:{start}:{end}:{kind}"
    return {"id": hashlib.sha256(identity.encode()).hexdigest()[:24], "start": start, "end": end,
            "text": text[start:min(end, start + 2000)], "text_truncated": end - start > 2000,
            "kind": kind, "label": label, "reason": reason,
            "enabled": enabled, "removable": removable}


def chapter_proposals(chapter: dict[str, Any], options: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-overlapping removal candidates, plus non-removable loss flags."""
    text = chapter["text"]
    body_offset = chapter["body_start"] - chapter["start"]
    body = text[body_offset:]
    keywords = options.get("author_keywords", AUTHOR_KEYWORDS)
    proposals = []
    source_hash = hashlib.sha256(text.encode()).hexdigest()

    def make_proposal(start: int, end: int, kind: str, label: str, reason: str,
                      enabled: bool = True, removable: bool = True) -> dict[str, Any]:
        proposal = _proposal(text, start, end, kind, label, reason, enabled, removable, source_hash)
        proposal["scope"] = "heading" if end <= body_offset else "body"
        return proposal

    for start, end in _heading_noise_spans(chapter):
        proposals.append(make_proposal(start, end, "author_note", "标题求票内容", "章节标题中的订阅或求票提示"))

    title = chapter.get("raw_marker") or chapter.get("title") or ""
    # Strong author chapters are review candidates as a whole, never silently removed.
    if chapter.get("pattern") == "作者说章节" or any(marker in title for marker in _TITLE_MARKERS):
        proposals.append(make_proposal(0, len(text), "author_note", "非正文章节", "标题包含作者题外话标记"))
    else:
        # Upstream rules are retained; offsets are calculated while splitting,
        # rather than searching repeated paragraph strings during application.
        paragraphs = []
        for match in re.finditer(r"[^\n]+(?:\n(?![ \t　]*\n)[^\n]+)*", body):
            raw = match.group(0)
            # A web novel often has one paragraph per line without blank lines.
            # Preserve a multi-line paragraph if it wraps; meta PS lines can
            # still be considered as their own short final line.
            for line_match in re.finditer(r"[^\n]+", raw):
                line = line_match.group(0)
                left, right = len(line) - len(line.lstrip()), len(line.rstrip())
                if right > left:
                    paragraphs.append((body_offset + match.start() + line_match.start() + left,
                                       body_offset + match.start() + line_match.start() + right,
                                       line[left:right]))
        fence = None
        for index, (_, _, paragraph) in enumerate(paragraphs):
            if _RULE.fullmatch(paragraph) and index >= max(len(paragraphs) // 2, len(paragraphs) - max(3, len(paragraphs) // 4)):
                fence = index
        for index, (start, end, paragraph) in enumerate(paragraphs):
            if len(paragraph) > 160 or _DIALOGUE.search(paragraph):
                continue
            hits = [keyword for keyword in keywords
                    if (_POSTSCRIPT.match(paragraph) if re.fullmatch(r"ps\s*[:：]", keyword, re.IGNORECASE)
                        else keyword in paragraph)]
            tail = index >= len(paragraphs) - 2
            after_fence = fence is not None and index > fence
            if len(hits) >= 2 or (hits and (tail or after_fence)) or (after_fence and tail and len(paragraph) < 80):
                reason = "、".join(hits[:4]) or "章节末分隔线后的短段落"
                proposals.append(make_proposal(start, end, "author_note", "非正文段落", reason))
    whole_author = any(item["start"] == 0 and item["end"] == len(text) for item in proposals)
    if not whole_author:
        for kind, label, pattern in _NOISE:
            for match in pattern.finditer(text):
                proposals.append(make_proposal(match.start(), match.end(), "noise", label, "疑似采集残留"))
        for finding in diagnose_text(text):
            code = finding["code"]
            if code == "random_id":
                proposals.append(make_proposal(finding["start"], finding["end"], "noise", "随机 ID", finding["message"]))
        for item in options.get("garble_rules", []):
            if item.get("enabled", True) is False:
                continue
            expression = re.escape(item["pattern"]) if item.get("literal") else item["pattern"]
            try:
                for match in regex.finditer(expression, text, timeout=0.25):
                    if match.end() > match.start():
                        proposals.append(make_proposal(match.start(), match.end(), "noise", item["name"], "自定义杂字规则"))
            except TimeoutError:
                raise DataError(422, f"杂字规则「{item['name']}」运行超时，请简化表达式") from None
    # Missing character information cannot be restored by deleting a glyph.
    for finding in diagnose_text(text):
        if finding["code"] in {"replacement_character", "missing_glyph", "mojibake_signature"}:
            kind = "missing_glyph" if finding["code"] in {"replacement_character", "missing_glyph"} else "mojibake"
            proposals.append(make_proposal(finding["start"], finding["end"], kind,
                                       _LABELS[kind], finding["message"], False, False))
    # Merge overlapping removals into one exact highlighted range. Keep loss
    # diagnostics separately; the GUI can compose their colored annotations.
    removals = sorted((p for p in proposals if p["removable"]), key=lambda p: (p["start"], -p["end"]))
    merged = []
    for proposal in removals:
        if merged and proposal["start"] < merged[-1]["end"]:
            previous = merged.pop()
            merged.append(make_proposal(previous["start"], max(previous["end"], proposal["end"]),
                                    previous["kind"], previous["label"], previous["reason"] + "；" + proposal["reason"]))
        else:
            merged.append(proposal)
    return sorted(merged + [p for p in proposals if not p["removable"]], key=lambda p: (p["start"], p["end"]))


def annotate_chapters(chapters: list[dict[str, Any]], options: dict[str, Any],
                      progress: Callable[[int, int], None] | None = None,
                      proposals_by_id: dict[str, list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """Annotate structure, reusing exact-version proposal scans when supplied.

    Callers must invalidate changed chapter IDs and all entries after settings
    changes. The mapping is populated for uncached chapters, making an edit cost
    one chapter scan rather than repeatedly rescanning a whole novel.
    """
    cached_ids = set(proposals_by_id or {})
    for index, chapter in enumerate(chapters):
        identifier = chapter.get("id", f"chapter-{index + 1}")
        cached_scan = identifier in cached_ids
        # Editing copies metadata before invalidating that chapter's scan. The
        # cache membership, rather than a metadata key alone, proves validity.
        if not cached_scan or not isinstance(chapter.get("body_length"), int):
            chapter["body_length"] = len(chapter["content"].strip())
        if not cached_scan or not isinstance(chapter.get("char_count"), int):
            chapter["char_count"] = sum(not c.isspace() for c in chapter["content"])
    lengths = [chapter["body_length"] for chapter in chapters if chapter["kind"] == "chapter"]
    median = statistics.median(lengths) if lengths else 0
    structure = chapter_sequence(chapters)
    gaps = structure["gaps"]
    gaps_by_id = {gap["before_chapter_id"]: gap for gap in gaps}
    for index, chapter in enumerate(chapters):
        identifier = chapter.get("id", f"chapter-{index + 1}")
        if proposals_by_id is not None and identifier in proposals_by_id:
            proposals = proposals_by_id[identifier]
        else:
            proposals = chapter_proposals(chapter, options)
            if proposals_by_id is not None:
                proposals_by_id[identifier] = proposals
        counts = Counter(proposal["kind"] for proposal in proposals)
        chapter.update(index=index,
                       display_label=chapter_label(chapter),
                       proposal_count=sum(p["removable"] for p in proposals),
                       proposal_ids=[p["id"] for p in proposals if p["removable"]],
                       enabled_proposal_ids=[p["id"] for p in proposals if p["removable"] and p["enabled"]])
        body_proposals = [proposal for proposal in proposals if proposal.get("scope", "body") == "body"]
        chapter["heading_proposal_count"] = len(proposals) - len(body_proposals)
        chapter["body_proposal_count"] = len(body_proposals)
        chapter["first_body_issue_start"] = min((proposal["start"] for proposal in body_proposals), default=None)
        issues = [{"kind": kind, "label": _LABELS[kind], "count": count,
                   "severity": "warning" if kind in {"author_note", "noise"} else "error"}
                  for kind, count in counts.items()]
        size = chapter["body_length"]
        if chapter["kind"] == "chapter" and median > 0 and (size < median * 0.3 or size > median * 3):
            issues.append({"kind": "length", "label": "篇幅过短" if size < median else "篇幅过长", "count": 1, "severity": "warning"})
        if identifier in structure["duplicates"]:
            issues.append({"kind": "duplicate", "label": "章节重号", "count": 1, "severity": "warning"})
        gap = gaps_by_id.get(identifier)
        if gap:
            issues.append({"kind": "gap", "label": f"缺 {gap['missing_count']} 章", "count": gap["missing_count"], "severity": "warning"})
        chapter["issues"] = issues
        chapter["issue_count"] = int(bool(issues))
        if progress:
            progress(index + 1, len(chapters))
    return gaps
