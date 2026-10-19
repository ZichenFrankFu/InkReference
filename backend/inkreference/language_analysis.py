"""Deterministic, writer-facing language measures; no model or word tokenizer."""
from __future__ import annotations

from collections import Counter
import re
import io
import math
from typing import Iterable

PUNCTUATION = {"comma": "，,", "full_stop": "。.", "question": "？?", "exclamation": "！!",
               "colon": "：:", "semicolon": "；;", "dash": "—", "ellipsis": "…", "quotes": '“”「」『』\"'}


def _distribution(values: Iterable[int]) -> dict:
    counts = Counter(values)
    total = sum(counts.values())
    def rank(target):
        accumulated = 0
        for value, count in sorted(counts.items()):
            accumulated += count
            if accumulated >= target: return value
        return 0
    median = (rank((total + 1) // 2) + rank(total // 2 + 1)) / 2 if total else 0
    buckets = {"1–10": 0, "11–20": 0, "21–40": 0, "41–80": 0, "81+": 0}
    for value, count in counts.items():
        bucket = "1–10" if value <= 10 else "11–20" if value <= 20 else "21–40" if value <= 40 else "41–80" if value <= 80 else "81+"
        buckets[bucket] += count
    return {"count": total, "mean": round(sum(value * count for value, count in counts.items()) / total, 2) if total else 0,
            "median": median, "p90": rank(math.ceil(total * .9)), "min": min(counts, default=0),
            "max": max(counts, default=0), "distribution": buckets}


def compute_language_statistics(text: str, *, source_version_id: str = "", chapter_count: int | None = None) -> dict:
    """Counts exclude whitespace; sentence/paragraph policies are explicit.

    Paragraphs are each nonempty physical line, matching the application's
    normalized Chinese prose. Sentence delimiters are 。！？!? plus ellipsis runs;
    punctuation remains part of sentence length. Trailing fragments count once.
    """
    paragraph_lengths = Counter()
    chars = 0
    for line in io.StringIO(text):
        size = len(re.sub(r"\s+", "", line))
        if size:
            paragraph_lengths[size] += 1
            chars += size
    lengths = (len(re.sub(r"\s+", "", match[0])) for match in re.finditer(r"[^。！？!?…]+(?:[。！？!?]+|…+|$)|…+", text))
    punctuation = {name: {"count": sum(text.count(char) for char in symbols),
                          "per_1000_chars": round(sum(text.count(char) for char in symbols) * 1000 / chars, 3) if chars else 0}
                   for name, symbols in PUNCTUATION.items()}
    return {"method": "unicode_non_whitespace_v1", "source_version_id": source_version_id,
            "coverage": {"characters": chars, "chapters": chapter_count},
            "sentence_length": _distribution(value for value in lengths if value), "paragraph_length": _distribution(paragraph_lengths.elements()),
            "punctuation_frequency": punctuation}


def era_statistics(era: dict, characters: list[dict], *, character_count: int = 0) -> dict:
    """Frequencies derive from exact, deduplicated occurrence anchors.

    Character usage means meme dialogue/thought only; real-world event
    references stay in story occurrence counts. Average includes all identified
    characters, including zero users. A narration mention is not character use.
    """
    all_ids = {person["id"] for person in characters if person.get("id")}
    names = {person["id"]: person.get("name", person["id"]) for person in characters if person.get("id")}
    counts: Counter = Counter()
    seen, items, total = set(), [], 0
    for item in era.get("items", []):
        count = 0
        for occurrence in item.get("appearances", item.get("occurrences", [])):
            anchor = occurrence.get("revealed_at", {})
            key = (item.get("id"), occurrence.get("source_version_id"), anchor.get("chapter"), anchor.get("offset"), occurrence.get("evidence"))
            if not occurrence.get("source_version_id") and not occurrence.get("evidence"):
                key = (*key, occurrence.get("narrative_progress"), occurrence.get("story_time"), occurrence.get("context"), occurrence.get("usage"), tuple(occurrence.get("character_ids", [])))
            if key in seen: continue
            seen.add(key)
            count += 1
            cids = occurrence.get("character_ids", [occurrence.get("character_id")])
            if item.get("kind") == "meme" and occurrence.get("usage") in {"dialogue", "thought"}:
                for cid in set(cids):
                    if cid in all_ids: counts[cid] += 1
        total += count
        items.append({"id": item.get("id"), "count": count,
                      "per_10000_chars": round(count * 10000 / character_count, 4) if character_count else None})
    highest = max(counts.values(), default=0)
    return {"occurrence_count": total, "per_10000_chars": round(total * 10000 / character_count, 4) if character_count else None,
            "items": items, "character_usage": [{"character_id": key, "name": names[key], "count": counts[key],
            "per_10000_chars": round(counts[key] * 10000 / character_count, 4) if character_count else None} for key in sorted(all_ids)],
            "highest_characters": [{"character_id": key, "name": names[key], "count": counts[key]} for key in sorted(counts) if counts[key] == highest],
            "average_per_character": round(sum(counts.values()) / len(all_ids), 4) if all_ids else 0,
            "denominator_characters": len(all_ids), "coverage_characters": character_count}

# Names outside this conservative Chinese-name pattern are excluded instead of
# guessing surnames for transliterations, titles or aliases. Counts are per stable
# character identity, not appearances or mentions in source text.
_COMPOUND_SURNAMES = tuple(sorted(('欧阳','太史','端木','上官','司马','东方','独孤','南宫','万俟','闻人','夏侯','诸葛','尉迟','公羊','赫连','澹台','皇甫','宗政','濮阳','公冶','太叔','申屠','公孙','慕容','仲孙','钟离','长孙','宇文','司徒','鲜于','司空','闾丘','子车','亓官','司寇','巫马','公西','颛孙','壤驷','公良','漆雕','乐正','宰父','谷梁','拓跋','夹谷','轩辕','令狐','段干','百里','呼延','东郭','南门','羊舌','微生','梁丘','左丘','东门','西门'), key=lambda item: (-len(item), item)))
_COMMON_SURNAMES = set('赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳酆鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊惠甄曲封芮储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲台从鄂索咸籍赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公墨洛')


def naming_statistics(characters: list[dict]) -> dict:
    surnames, given = Counter(), Counter()
    seen, eligible = set(), 0
    for person in characters:
        identity = person.get('id')
        if not identity or identity in seen: continue
        seen.add(identity)
        name = str(person.get('name') or '').strip()
        if not re.fullmatch(r'[\u3400-\u4dbf\u4e00-\u9fff]{2,4}', name): continue
        surname = next((part for part in _COMPOUND_SURNAMES if name.startswith(part) and len(name) > len(part)), None)
        if surname is None:
            if name[0] not in _COMMON_SURNAMES: continue
            surname = name[0]
        eligible += 1
        surnames[surname] += 1
        given.update(name[len(surname):])
    total_given = sum(given.values())
    def top(counts, denominator):
        return [{'character': key, 'count': count, 'percentage': round(count * 100 / denominator, 4) if denominator else 0}
                for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]]
    return {'method': 'unique_character_chinese_name_heuristic_v1', 'eligible_names': eligible,
            'given_character_count': total_given, 'surnames': top(surnames, eligible), 'given_characters': top(given, total_given)}
