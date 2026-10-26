#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""eval.py — 知识库问答的回归评估（改索引/改阈值后必跑）。

测三件事，对应三个最容易悄悄退化的地方：

    Recall@1        问到了对的地方吗          （检索质量）
    误拒率           库里有、却判「无」吗       （judge 太严）
    拒答准确率       库里没有、有没有老实说没有   （judge 太松 / 幻觉入口）

评估集格式（JSON 数组）：
    [
      {"q": "戊戌变法是什么性质的运动", "expect": ["历史-第一周-1022"]},
      {"q": "这本书里有量子力学吗",     "expect": []},
      {"q": "…", "expect": ["某单元"], "draft": "drafts/xx.md"}   # 可选：顺带测引用闸门
    ]

`expect: []` = 库里应该没有；用来专门测"该拒答时不编"。

用法：
    python3 eval.py --kb kb/ --set eval.json
    python3 eval.py --kb kb/ --set eval.json --verbose
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kb as KBmod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True)
    ap.add_argument("--set", required=True, dest="eval_set")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    kb = KBmod.KB(Path(args.kb))
    items = json.loads(Path(args.eval_set).read_text(encoding="utf-8"))

    hit1 = miss = 0
    reject_wrong = reject_right = 0
    cite_pass = cite_fail = 0
    rows = []

    for it in items:
        q, expect = it["q"], it.get("expect", [])
        us = kb.unit_scores(q)
        top1 = list(us)[0] if us else None
        verdict = kb.judge(q)["verdict"]

        if expect:
            if top1 in expect:          # expect = 可接受单元集合（多候选也算对）
                hit1 += 1
            else:
                miss += 1
            if verdict == "无":
                reject_wrong += 1
        else:
            if verdict == "无":
                reject_right += 1
            else:
                reject_wrong += 1

        row = {"q": q, "expect": expect, "top1": top1, "verdict": verdict}
        if it.get("draft"):
            rc = KBmod.cmd_verify(kb, Path(it["draft"]), q, 0.6)
            row["cite_gate"] = "pass" if rc == 0 else "fail"
            if rc == 0:
                cite_pass += 1
            else:
                cite_fail += 1
        rows.append(row)
        if args.verbose:
            ok = "✓" if (top1 in expect if expect else verdict == "无") else "✗"
            print(f"{ok} [{verdict:<2}] top1={top1} expect={expect}  {q}")

    n_has = sum(1 for it in items if it.get("expect"))
    n_none = sum(1 for it in items if not it.get("expect"))
    report = {
        "total": len(items),
        "with_answer": n_has,
        "without_answer": n_none,
        "recall_at_1": round(hit1 / n_has, 3) if n_has else None,
        "false_reject_rate": round(reject_wrong / n_has, 3) if n_has else None,
        "reject_accuracy": round(reject_right / n_none, 3) if n_none else None,
        "citation_gate": (f"{cite_pass}/{cite_pass + cite_fail}"
                          if (cite_pass + cite_fail) else None),
        "misses": [r["q"] for r in rows if r.get("expect") and r["top1"] not in r["expect"]],
        "false_rejects": [r["q"] for r in rows if r.get("expect") and r["verdict"] == "无"],
        "wrong_accepts": [r["q"] for r in rows if not r.get("expect") and r["verdict"] != "无"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    ok = True
    if report["recall_at_1"] is not None and report["recall_at_1"] < 0.9:
        print("❌ Recall@1 < 90%，检索质量退化", file=sys.stderr)
        ok = False
    if report["false_reject_rate"] not in (None, 0.0):
        print("❌ 有误拒：库里有却判无", file=sys.stderr)
        ok = False
    if report["reject_accuracy"] not in (None, 1.0):
        print("❌ 拒答准确率不是 100%：库里没有的也敢答", file=sys.stderr)
        ok = False
    print("✅ 评估通过" if ok else "⚠️ 评估未通过（见上）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
