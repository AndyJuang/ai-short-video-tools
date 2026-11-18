#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["PyYAML>=6"]
# ///
"""Turn a storyboard table into an ACOS edit plan for render_plan.py.

    uv run storyboard.py <storyboard.md> [--out edit_plan.yaml] [--id CONTENT_ID] [--generate]
    uv run storyboard.py --self-test

Reads the first markdown table whose rows start with a cut number, by column position:
    | cut | timecode | shot | camera move | picture | memo (text / narration) |
which is video-storyboard's table, in any language. A storyboard that arrived another way
(pasted, a PDF, a storyboard sheet image) is copied into that shape first; with no storyboard
at all, video-edit drafts one in it (references/storyboard.md).

    timecode "1.7–3.3s"   -> start_sec / end_sec (a dash, en dash, ~ or 〜 between)
    camera move           -> animation (CAMERA below; unknown -> none, with a warning)
    picture               -> scene `brief` (what to pick footage for), and generate.brief with --generate
    memo                  -> caption; a labelled narration part -> voiceover.lines
    last cut with a logo / CTA in its picture -> kind: card, the memo as its CTA caption
    first cut's caption is style hook, a card's is cta, the rest body

Without --generate every other cut gets `asset_file: null`: video-edit Phase 3 picks the
footage, and render_plan.py refuses to render while one is still null. With --generate each
gets `gen/NN.jpg` and a `generate` block; the panel's English line from a GPT Image prompt
block in the same file (`03 3.3–5.0s MCU/Pan: ...`) becomes its prompt when there is one.
generate.py prices it; nothing is spent here.
"""
import argparse
import os
import re
import sys
import tempfile

import yaml

CAMERA = {"static": "none", "final hold": "none", "push-in": "ken_burns", "pull-back": "ken_burns",
          "zoom-in": "zoom_in_punch", "pan": "ken_burns", "tilt": "ken_burns", "tracking": "ken_burns"}
TC = re.compile(r"(\d+(?:\.\d+)?)\s*s?\s*[-–—~〜～]\s*(\d+(?:\.\d+)?)\s*s?")
CARD = re.compile(r"logo|ロゴ|標誌|商標|品牌標|cta", re.I)
TEXT = r"テキスト|テロップ|字幕|字卡|文字|text|caption"
NARR = r"ナレーション|ナレ|旁白|口白|vo|narration"
LABEL = re.compile(rf"(?:^|[／/|｜\n]|\s)\s*({TEXT}|{NARR})\s*(?:案)?\s*[:：]\s*", re.I)
QUOTES = "「」『』\"“”'"
RATIO = re.compile(r"\b(9:16|16:9|4:5|1:1)\b")


def cells(line):
    return [c.strip() for c in line.strip().strip("|").split("|")]


def rows(md):
    """[(cut, start, end, shot, camera, picture, memo)] from the first cut-numbered table."""
    out = []
    for line in md.splitlines():
        if not line.lstrip().startswith("|"):
            if out:
                break                      # the table ended
            continue
        c = cells(line)
        if len(c) < 6 or not re.fullmatch(r"[Cc]?\d{1,2}", c[0]):
            continue
        m = TC.search(c[1])
        if not m:
            sys.exit(f"cut {c[0]}: no timecode in {c[1]!r} (want e.g. 1.7–3.3s)")
        out.append((int(re.sub(r"\D", "", c[0])), float(m[1]), float(m[2]), c[2], c[3], c[4], " | ".join(c[5:])))
    if not out:
        sys.exit("no storyboard table: want rows like | 01 | 0–1.7s | Wide | Static | picture | memo |")
    return out


def panel_prompts(md):
    """{cut: English panel line} from a GPT Image prompt block, if the file has one."""
    return {int(m[1]): m[2].strip() for m in re.finditer(
        r"^\s*(\d{2})\s+\d+(?:\.\d+)?\s*[-–—]\s*\d+(?:\.\d+)?s\s+[^:\n]*:\s*(.+)$", md, re.M)}


def split_memo(memo):
    """(caption text, narration) from a memo cell. Labelled parts ('テキスト：…／ナレ：…') split;
    an unlabelled memo is all caption."""
    memo = memo.replace("<br>", "\n").strip()
    parts = LABEL.split(memo)
    if len(parts) < 3:
        return memo.strip(QUOTES + " ") or None, None
    text, narr = [], []
    for label, val in zip(parts[1::2], parts[2::2]):
        val = val.strip(" ／/|｜\n" + QUOTES)
        (narr if re.fullmatch(NARR, label, re.I) else text).append(val)
    return " ".join(text) or None, " ".join(narr) or None


def plan_of(md, content_id, generate=False):
    cuts, prompts, warns = rows(md), panel_prompts(md), []
    timeline, lines = [], []
    for i, (n, a, b, shot, cam, pic, memo) in enumerate(cuts):
        last = i == len(cuts) - 1
        anim = CAMERA.get(cam.strip().lower())
        if anim is None:
            warns.append(f"cut {n}: camera move {cam!r} unknown — animation none")
            anim = "none"
        text, narr = split_memo(memo)
        sc = {"scene": n, "start_sec": a, "end_sec": b}
        if last and CARD.search(pic + memo):
            sc.update(kind="card", transition_in="fade")
            if text:
                sc["caption"] = {"text": text, "style": "cta"}
        else:
            sc.update(asset_file=f"gen/{n:02d}.jpg" if generate else None, transition_in="hard_cut",
                      animation=anim, brief=f"{shot} / {cam}: {pic}")
            if generate:
                sc["generate"] = {"brief": pic, **({"prompt": prompts[n]} if n in prompts else {})}
            if text:
                sc["caption"] = {"text": text, "style": "hook" if i == 0 else "body"}
        if narr:
            lines.append({"id": f"s{n:02d}", "scene": n, "text": narr})
        timeline.append(sc)
    plan = {"content_id": content_id, "total_duration_sec": cuts[-1][2],
            "render": {"mode": "ffmpeg"}, "timeline": timeline}
    if lines:
        plan["voiceover"] = {"lines": lines}
    ratio = RATIO.search(md.split("|", 1)[0])           # the header, before the table
    plan["export_checklist"] = [f"比例：{ratio[1] if ratio else '9:16'}"]
    return plan, warns


def self_test():
    md = """# 客戶 / 商品 — Type 3
フォーマット：縦型 9:16

| カット | タイムコード | ショットタイプ | カメラムーブ | 映像・シーン説明 | 【制作メモ】テキスト案／ナレ案 |
|---|---|---|---|---|---|
| 01 | 0–1.7s | Wide | Static | 早晨的廚房 | テキスト：「你也這樣嗎？」／ナレ：「每天早上都好趕」 |
| 02 | 1.7–3.3s | Med | Push-in | 手拿起產品 | 只要三分鐘 |
| 03 | 3.3–5.0s | MCU | Whip | 產品特寫 | |
| 09 | 5.0–6.7s | Wide | Final hold | ブランドロゴ + CTA | 立即預約 |

```
01 0–1.7s Wide/Static: A sunlit kitchen in the morning. No text.
02 1.7–3.3s Med/Push-in: A hand lifts a plain jar. No text.
```
"""
    p, w = plan_of(md, "t")
    t = p["timeline"]
    assert [s["start_sec"] for s in t] == [0, 1.7, 3.3, 5.0] and p["total_duration_sec"] == 6.7, t
    assert t[0]["caption"] == {"text": "你也這樣嗎？", "style": "hook"}, t[0]
    assert p["voiceover"]["lines"] == [{"id": "s01", "scene": 1, "text": "每天早上都好趕"}], p["voiceover"]
    assert t[1]["caption"]["text"] == "只要三分鐘" and t[1]["animation"] == "ken_burns", t[1]
    assert "caption" not in t[2] and t[2]["animation"] == "none" and "Whip" in w[0], (t[2], w)
    assert t[3]["kind"] == "card" and t[3]["caption"]["style"] == "cta" and "asset_file" not in t[3], t[3]
    assert all(s.get("asset_file", "x") is None for s in t[:3]), "footage must be left for picking"
    assert p["export_checklist"] == ["比例：9:16"], p["export_checklist"]
    g, _ = plan_of(md, "t", generate=True)
    gt = g["timeline"]
    assert gt[0]["asset_file"] == "gen/01.jpg" and gt[0]["generate"]["prompt"].startswith("A sunlit"), gt[0]
    assert "prompt" not in gt[2]["generate"] and gt[2]["generate"]["brief"] == "產品特寫", gt[2]
    print("  ok  timecodes, memo split, camera map, end card, generate blocks")
    for bad, why in (("| 01 | soon | Wide | Static | x | y |", "no timecode"), ("no table here", "no storyboard")):
        try:
            plan_of(bad, "t")
            raise AssertionError(f"accepted: {bad}")
        except SystemExit as e:
            assert why in str(e), e
    print("  ok  a table without timecodes, or no table, is refused")
    with tempfile.TemporaryDirectory() as d:
        src, out, target = (os.path.join(d, n) for n in ("sb.md", "edit_plan.yaml", "victim"))
        open(src, "w", encoding="utf-8").write(md)
        os.symlink(target, out)  # dangling: os.path.exists() says False
        try:
            cli([src])
            raise AssertionError("wrote through a symlink")
        except SystemExit as e:
            assert "exists" in str(e) and not os.path.exists(target), e
    print("  ok  a planted edit_plan.yaml symlink is refused, its target untouched")
    print("self-test ok")


def cli(argv):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("storyboard", nargs="?")
    ap.add_argument("--out", help="default: edit_plan.yaml beside the storyboard")
    ap.add_argument("--id", help="content_id (default: the storyboard's file name)")
    ap.add_argument("--generate", action="store_true", help="generated stills instead of footage picks")
    ap.add_argument("--self-test", action="store_true")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    if not a.storyboard:
        ap.error("a storyboard file, or --self-test")
    md = open(a.storyboard, encoding="utf-8-sig").read()
    out = a.out or os.path.join(os.path.dirname(os.path.abspath(a.storyboard)), "edit_plan.yaml")
    # lexists + "x": a planted symlink (even a dangling one) is refused, never written through
    if os.path.lexists(out):
        sys.exit(f"{out} exists — pass --out to write elsewhere; an edit plan is never overwritten")
    plan, warns = plan_of(md, a.id or os.path.splitext(os.path.basename(a.storyboard))[0], a.generate)
    with open(out, "x", encoding="utf-8") as fh:
        yaml.safe_dump(plan, fh, allow_unicode=True, sort_keys=False, width=200)
    for w in warns:
        print(f"! {w}")
    todo = sum(1 for s in plan["timeline"] if s.get("asset_file", "") is None)
    print(f"{out}: {len(plan['timeline'])} scenes, {plan['total_duration_sec']} s, "
          f"{len((plan.get('voiceover') or {}).get('lines', []))} narration lines"
          + (f", {todo} to pick footage for" if todo else ""))


if __name__ == "__main__":
    cli(sys.argv[1:])
