"""Read-only writing diagnostics adapted from the two local writing packs.

These are review candidates, not AI-authorship detection or writing gates.
All offsets refer to the original text; dialogue masking preserves its length.
"""
import argparse
from bisect import bisect_right
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import re
import statistics


STYLE_RULES = (
    ("abstract_emotion", "情绪定性", r"(?:非常|十分|极其|格外|很)(?:紧张|愤怒|悲伤|焦虑|震惊|尴尬|激动|恐惧)",
     "检查同段是否已有动作或感官兑现；必要时换成角色特有的反应。"),
    ("stock_reaction", "惯用反应", r"(?:不由得|不禁|禁不住|陷入了沉思|深吸一口气|平复了一下心情|露出了思索的神情)",
     "确认这次反应有没有新信息；普通习惯用语无需逐个删除。"),
    ("explanatory_contrast", "解释型对举", r"不是[^。！？\n]{1,35}[，,](?:而是|是)[^。！？\n]{1,35}",
     "检查是否在替读者下结论；角色独白、澄清误会和有意对比可以保留。"),
    ("narrator_wrapup", "旁白总结", r"(?:这一刻.{0,12}终于明白|命运的齿轮.{0,8}转动|一切才刚刚开始|这只是一个开始|真正的.{0,8}才刚刚开始)",
     "检查是否重复说明已经呈现的内容；可落到具体变化，也可保留有意的叙述口吻。"),
    ("vague_atmosphere", "抽象氛围", r"(?:空气中|房间里|气氛中)(?:弥漫|充斥|透着)[^。！？\n]{0,25}",
     "检查空间、声音或行为能否承载氛围，不必为替换词语添加无关细节。"),
)
DELIVERY = re.compile(
    r"(?m)^\s*(?:#{1,4}\s*)?(?:【(?:第[^】\n]{0,12}章状态声明|自检报告|质检报告|写作自评)】"
    r"|(?:以下是|下面是)(?:修改后|润色后|续写后|为您生成)的(?:正文|章节)"
    r"|(?:自检清单|技法应用率|活人感五维自评|注射包)[：:])[^\n]{0,100}"
)
QUANTITY = re.compile(r"([零一二两三四五六七八九十百千万半0-9]+)([个只张把件封台辆扇本颗枚])([\u4e00-\u9fff]{1,2})(?=[，。！？；、\s]|$)")
STATES = re.compile(r"(门|窗|灯)(?:仍然|依然|还|已经|是)?(开着|关着|亮着|熄灭了)")
OPPOSITE = {"开着": "关着", "关着": "开着", "亮着": "熄灭了", "熄灭了": "亮着"}
TRANSITION = re.compile(r"打开|关上|推开|拉开|熄灭|点亮|合上|撕|换|丢|捡|拿走|送来|又|后来|随后|片刻后")


def mask_dialogue(text):
    pairs = {"“": "”", "「": "」", "『": "』", '"': '"'}
    stack = []
    chars = []
    for char in text:
        quoted = bool(stack)
        if stack and char == stack[-1]:
            stack.pop()
            quoted = True
        elif char in pairs:
            stack.append(pairs[char])
            quoted = True
        chars.append(" " if quoted and char not in "\r\n" else char)
    return "".join(chars)


def scan_writing_text(text, *, profile="all", excerpt=False, offset=0, limit=40):
    if not isinstance(text, str) or not text.strip():
        raise ValueError("没有可检查的正文")
    if not isinstance(profile, str) or profile not in {"all", "style", "consistency"}:
        raise ValueError("检查类型无效")
    if not isinstance(excerpt, bool):
        raise ValueError("片段标记无效")
    if any(not isinstance(v, int) or isinstance(v, bool) for v in (offset, limit)) or offset < 0 or not 1 <= limit <= 100:
        raise ValueError("检查结果分页无效")
    narrative = mask_dialogue(text)
    newlines = [match.start() for match in re.finditer("\n", text)]
    findings = []
    seen = set()

    def add(rule, label, start, end, advice, category="style", related=None):
        key = (rule, start, end)
        if key in seen:
            return
        seen.add(key)
        item = {"rule": rule, "label": label, "category": category, "status": "candidate",
                "line": bisect_right(newlines, start) + 1, "start": start, "end": end,
                "evidence": text[start:min(end, start + 240)], "suggestion": advice}
        if related is not None:
            item["related_line"] = bisect_right(newlines, related) + 1
        findings.append(item)

    if profile in {"all", "style"}:
        for rule, label, pattern, advice in STYLE_RULES:
            for match in re.finditer(pattern, narrative):
                add(rule, label, match.start(), match.end(), advice)
        for match in DELIVERY.finditer(narrative):
            start = match.start() + len(match.group()) - len(match.group().lstrip())
            add("delivery_markup", "交付杂质候选", start, match.end(),
                "确认这段是否属于作品；模型说明、自检表通常应留在对话中。", "delivery")

    sentences = [(m.start(), m.end(), m.group().strip()) for m in re.finditer(r"[^。！？；!?;\n]+[。！？；!?;]?", narrative)
                 if m.group().strip()]
    if profile in {"all", "consistency"}:
        recent = deque(maxlen=12)
        for start, end, sentence in sentences:
            clean = re.sub(r"[\s，。！？；、,.!?;]", "", sentence)
            line = bisect_right(newlines, start)
            if len(clean) >= 8:
                for old_start, old_line, old_clean in reversed(recent):
                    if line - old_line > 5:
                        break
                    if clean == old_clean:
                        add("nearby_repeat", "邻近句子重复", start, end,
                            "对照前一次出现，检查是否有意复沓或是否可以删去重复信息。", "consistency", old_start)
                        break
                recent.append((start, line, clean))
        for pattern, rule, label in ((QUANTITY, "quantity_change", "数量变化待核对"), (STATES, "state_change", "状态变化待核对")):
            previous = {}
            for match in pattern.finditer(narrative):
                if rule == "quantity_change":
                    value = match.group(1).replace("两", "二")
                    key = match.group(2) + match.group(3)
                else:
                    key, value = match.groups()
                old = previous.get(key)
                if old and match.start() - old[1] <= 400 and old[0] != value:
                    changed = rule == "quantity_change" or OPPOSITE.get(old[0]) == value
                    if changed and not TRANSITION.search(narrative[old[2]:match.start()]):
                        add(rule, label, match.start(), match.end(),
                            "核实两处是否为同一对象、同一时点，或中间是否已交代变化；仅词面相似不能认定矛盾。",
                            "consistency", old[1])
                previous[key] = (value, match.start(), match.end())

    if profile in {"all", "style"}:
        for group in zip(sentences, sentences[1:], sentences[2:]):
            if group[-1][0] - group[0][0] > 350:
                continue
            heads = [item[2][0] for item in group]
            if heads[0] in "他她我你" and len(set(heads)) == 1:
                add("repeated_opening", "连续句式雷同", group[0][0], group[-1][1],
                    "检查动作是否机械排列；有意形成节奏或持续聚焦同一角色时可以保留。")

    lengths = [len(re.sub(r"\s", "", item[2])) for item in sentences if len(item[2]) >= 2]
    stats = {"characters": len(text), "narrative_sentences": len(lengths),
             "sentence_length_mean": round(statistics.mean(lengths), 2) if lengths else None,
             "sentence_length_sd": round(statistics.pstdev(lengths), 2) if len(lengths) >= 2 else None}
    findings.sort(key=lambda item: (item["start"], item["rule"]))
    total = len(findings)
    return {"profile": profile, "excerpt": excerpt, "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "stats": stats, "counts": dict(Counter(item["category"] for item in findings)),
            "findings": findings[offset:offset + limit], "total": total, "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
            "notice": "本地规则仅提供复核候选，不判断作者身份，不自动改稿或阻止写作。对白已从词面风格检查中排除；跨章事实和语义仍需结合上下文检查。"}


def main():
    parser = argparse.ArgumentParser(description="Read-only writing diagnostics")
    parser.add_argument("file", type=Path)
    parser.add_argument("--profile", choices=("all", "style", "consistency"), default="all")
    parser.add_argument("--excerpt", action="store_true")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=40)
    args = parser.parse_args()
    try:
        result = scan_writing_text(args.file.read_text(encoding="utf-8-sig"), profile=args.profile,
                                   excerpt=args.excerpt, offset=args.offset, limit=args.limit)
    except (OSError, ValueError) as exc:
        parser.exit(2, str(exc) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
