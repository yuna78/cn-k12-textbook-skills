#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kb.py — 本地知识库检索与**带出处问答**（零依赖，离线 BM25）。

设计原则只有一条：**宁可说"没找到"，不许编一句。**
所以它不是一个"检索→让模型自由发挥"的检索器，而是带三道闸门的问答工具：

  ① judge  —— 先判能不能答：可靠 / 存疑 / 无
  ② search —— 检索并给出「单元 + 页码 + 逐字原文」，供引用
  ③ verify —— 交付前硬校验草稿里的每条 `[引]` 是否真能落到原文

命令：
  search "<关键词 换 2-3 种说法>" [--subject 物理] [--top 8] [--json]
  judge  "<问题>" [--subject 物理]
  get    <unit_id>                       # 打印单元全文
  cite   <unit_id>                       # 出处：源文件 + 页码 + 绝对路径
  fig    "<图号或关键词>"                # 找图（图号/图题/单元）
  page   <unit_id> --page 89             # 按需渲染该页 PNG（带缓存）
  verify <草稿.md> --question "<原问题>" # 引用闸门，exit 1 = 不通过
  stats

退出码：verify 通过 0 / 不通过 1；其它命令 0 成功、2 参数错、3 找不到。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")
WORD = re.compile(r"[a-zA-Z][a-zA-Z0-9]+|\d+(?:\.\d+)?")
PUNCT = re.compile(r"[\s，。、；：？！“”‘’（）《》〈〉【】…—·,.;:?!\"'()\[\]<>/\\|@#$%^&*_+=~`-]+")
CITE = re.compile(r"^\[引\]\s*(\S+?)\s*@p(\d+)(?:-\d+)?\s*::\s*(.+?)\s*$", re.M)

K1, B = 1.2, 0.75


# ---------- 文本处理 ----------

def norm(s: str) -> str:
    """引用比对用：去空白与标点，只留文字（容忍排版差异）。"""
    return PUNCT.sub("", s or "")


CJK_GAP = re.compile(r"(?<=[\u3400-\u9fff\uf900-\ufaff])\s+(?=[\u3400-\u9fff\uf900-\ufaff])")


def tokens(text: str) -> list[str]:
    """中文按二元组（+单字兜底），英文数字按词。

    先去掉汉字之间的空白：OCR 和 pdftotext -layout 经常把「汉 谟 拉 比」拆成单字，
    不合并的话二元组全断，检索会莫名其妙地查不到。
    """
    text = CJK_GAP.sub("", text or "")
    out: list[str] = []
    for run in CJK.findall(text or ""):
        if len(run) == 1:
            out.append(run)
        else:
            out.extend(run[i:i + 2] for i in range(len(run) - 1))
    out.extend(w.lower() for w in WORD.findall(text or ""))
    return out


# ---------- 语料与索引 ----------

class KB:
    def __init__(self, root: Path):
        self.root = root
        self.index = root / "_index"
        self.chunks = [json.loads(l) for l in
                       (self.index / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
                       if l.strip()]
        self.units_meta = json.loads((self.index / "units.json").read_text(encoding="utf-8"))["units"] \
            if (self.index / "units.json").exists() else []
        self.by_id = {u["id"]: u for u in self.units_meta}
        figs_p = self.index / "figures.json"
        self.figures = json.loads(figs_p.read_text(encoding="utf-8")) if figs_p.exists() else []
        self._build()

    def _build(self) -> None:
        self.tf: list[Counter] = []
        self.len: list[int] = []
        self.post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, c in enumerate(self.chunks):
            toks = tokens(c["text"] + " " + c.get("unit_title", ""))
            cnt = Counter(toks)
            self.tf.append(cnt)
            self.len.append(len(toks))
            for t, f in cnt.items():
                self.post[t].append((i, f))
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        self.N = len(self.chunks)

    def idf(self, t: str) -> float:
        df = len(self.post.get(t, ()))
        return math.log(1 + (self.N - df + 0.5) / (df + 0.5)) if self.N else 0.0

    def score(self, q: str, subject: str | None = None) -> list[tuple[int, float]]:
        qt = Counter(tokens(q))
        if not qt:
            return []
        res: list[tuple[int, float]] = []
        for i, c in enumerate(self.chunks):
            if subject and subject not in (self.by_id.get(c["unit_id"], {}).get("subject", "")):
                continue
            s = 0.0
            cl = self.len[i] or 1
            for t, qf in qt.items():
                f = self.tf[i].get(t, 0)
                if not f:
                    continue
                s += self.idf(t) * (f * (K1 + 1)) / (f + K1 * (1 - B + B * cl / (self.avg or 1))) * qf
            if s > 0:
                res.append((i, s))
        res.sort(key=lambda x: -x[1])
        return res

    QUESTION_STOP = re.compile(
        r"(什么|怎么|怎样|如何|哪些|哪个|哪种|是否|多少|为何|为什么|请问|一下|这个|那个|"
        r"可以|需要|告诉|帮我|解释|说说|讲讲|看看|区别|关系|意思|含义|指的是|是指|"
        r"是什|么性|质的|的运|么意|有哪|有什)")

    def content_tokens(self, q: str) -> list[str]:
        """问题里的"实词"：在语料词表中出现、且不是疑问/指令词。用于判断答没答到点上。"""
        toks = [t for t in tokens(q) if t in self.post and not self.QUESTION_STOP.search(t)]
        return toks or tokens(q)

    def judge(self, q: str, subject: str | None = None, min_rel: float = 0.22,
              min_cov: float = 0.5, min_margin: float = 0.12) -> dict:
        """唯一判定实现：可靠 / 存疑 / 无。cmd_judge 和 eval 都调这里。"""
        us = self.unit_scores(q, subject)
        if not us:
            return {"verdict": "无", "top": [], "rel": 0.0, "cov": 0.0, "margin": 0.0,
                    "content_tokens": []}
        items = list(us.items())
        top1 = items[0][1]
        top2 = items[1][1] if len(items) > 1 else 0.0
        ct = [t for t in self.content_tokens(q) if t in self.post]
        qt = set(ct)
        u1 = [c for c in self.chunks if c["unit_id"] == items[0][0]]
        hit = {t for t in qt if any(t in tokens(c["text"] + c["unit_title"]) for c in u1)}
        denom = (sum(self.idf(t) for t in qt) or 1.0) * (K1 + 1)
        cov = (sum(self.idf(t) for t in hit) / denom) * (K1 + 1) / (K1 + 1) if qt else 0.0
        cov = sum(self.idf(t) for t in hit) / (sum(self.idf(t) for t in qt) or 1.0)
        rel = top1 / denom
        margin = (top1 - top2) / top1 if top1 else 0.0
        if not ct or rel < min_rel:
            verdict = "无"
        elif cov < min_cov or (len(items) > 1 and margin < min_margin):
            verdict = "存疑"
        else:
            verdict = "可靠"
        return {"verdict": verdict, "top": items[:3], "rel": rel, "cov": cov,
                "margin": margin, "content_tokens": ct}

    def unit_scores(self, q: str, subject: str | None = None) -> dict[str, float]:
        agg: dict[str, float] = defaultdict(float)
        for i, s in self.score(q, subject)[:60]:
            c = self.chunks[i]
            agg[c["unit_id"]] = max(agg[c["unit_id"]], s)
        return dict(sorted(agg.items(), key=lambda kv: -kv[1]))


# ---------- 单元正文（verify 用） ----------

def unit_pages(kb: KB, uid: str) -> dict[int, str]:
    """读单元 md，返回 {书上页码: 该页正文}。"""
    meta = kb.by_id.get(uid)
    cand = []
    if meta and meta.get("file"):
        cand.append(kb.root / meta["file"])
    cand.append(kb.root / "units" / f"{uid}.md")
    for p in cand:
        if p.exists():
            text = p.read_text(encoding="utf-8")
            body = text.split("\n---", 2)[-1] if text.startswith("---") else text
            out, cur, buf = {}, None, []
            for line in body.splitlines():
                m = re.match(r"^<!--\s*p\.(\d+)", line.strip())
                if m:
                    if cur is not None:
                        out[cur] = "\n".join(buf)
                    cur, buf = int(m.group(1)), []
                elif cur is not None:
                    buf.append(line)
            if cur is not None:
                out[cur] = "\n".join(buf)
            return out
    return {}


def unit_meta(kb: KB, uid: str) -> dict:
    """单元元信息：优先索引，缺字段（如 source_file）时回落到读 md frontmatter。"""
    m = dict(kb.by_id.get(uid, {}))
    if m.get("source_file") and m.get("source_pages"):
        return m
    p = kb.root / m.get("file", f"units/{uid}.md")
    if not p.exists() and not (kb.root / "units" / f"{uid}.md").exists():
        return m
    p = p if p.exists() else kb.root / "units" / f"{uid}.md"
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return m
    end = text.find("\n---", 3)
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        m.setdefault(k.strip(), v.strip())
    return m


def source_paths(kb: KB, uid: str) -> list[Path]:
    """单元 frontmatter 里的 source_file，可能是相对库根或相对 PDF 所在，尽量都试。"""
    meta = unit_meta(kb, uid)
    sf = meta.get("source_file", "")
    cands = [Path(sf), kb.root / sf, kb.root.parent / sf, Path(sf).expanduser()]
    return [p for p in cands if p and str(p) not in ("", ".") and p.exists()]


# ---------- 命令 ----------

def cmd_search(kb: KB, q: str, subject: str | None, top: int, as_json: bool) -> int:
    scored = kb.score(q, subject)
    if not scored:
        print(f"没有命中：{q!r}", file=sys.stderr)
        return 3
    seen, rows = Counter(), []
    for i, s in scored:
        c = kb.chunks[i]
        if seen[c["unit_id"]] >= 2:
            continue
        seen[c["unit_id"]] += 1
        rows.append((c, s))
        if len(rows) >= top:
            break
    if as_json:
        print(json.dumps([{"score": round(s, 2), **c} for c, s in rows],
                         ensure_ascii=False, indent=1))
        return 0
    for c, s in rows:
        print(f"\n[{s:6.2f}] {c['unit_id']}  @p{c['page']}")
        print(f"         {c['unit_title']}" + (f" · {c['chapter']}" if c.get("chapter") else ""))
        snippet = re.sub(r"\s+", " ", c["text"])[:220]
        print(f"         {snippet}")
    print(f"\n提示：引用写 `[引] <unit_id> @p<页码> :: <逐字原文>`")
    return 0


def cmd_judge(kb: KB, q: str, subject: str | None) -> int:
    j = kb.judge(q, subject)
    if j["verdict"] == "无" and not j["top"]:
        print("判定：无　　这条问题在本地知识库里没有依据 → 只能回答「本地资料没有」。")
        return 0
    print(f"判定：{j['verdict']}　(相关度 {j['rel']:.0%}，加权覆盖 {j['cov']:.0%}，"
          f"与第二名差 {j['margin']:.0%})")
    print(f"首选单元：{j['top'][0][0]}  {kb.by_id.get(j['top'][0][0], {}).get('title', '')}")
    for uid, sc in j["top"]:
        m = kb.by_id.get(uid, {})
        print(f"  {sc:6.2f}  {uid}  @p{m.get('source_pages', '?')}  {m.get('title', '')}")
    if j["verdict"] == "无":
        print("\n→ 只能回答「本地资料没有这部分内容」，可给常识但必须标 ⚠️ 非本地资料。")
    elif j["verdict"] == "存疑":
        print("\n→ 有多个相近候选，必须逐条给页码说明，不许只挑一个当定论。")
    else:
        print("\n→ 可以答，但每个结论都要配 [引] 并在交付前跑 verify。")
    return 0


def cmd_get(kb: KB, uid: str) -> int:
    m = kb.by_id.get(uid)
    if not m:
        print(f"没有这个单元：{uid}", file=sys.stderr)
        return 3
    p = kb.root / m.get("file", f"units/{uid}.md")
    print(p.read_text(encoding="utf-8"))
    return 0


def cmd_cite(kb: KB, uid: str) -> int:
    m = unit_meta(kb, uid)
    if not m:
        print(f"没有这个单元：{uid}", file=sys.stderr)
        return 3
    paths = source_paths(kb, uid)
    print(json.dumps({
        "unit_id": uid,
        "title": m.get("title"),
        "chapter": m.get("chapter"),
        "source_file": m.get("source_file"),
        "pdf_pages": m.get("pdf_pages"),
        "source_pages": m.get("source_pages"),
        "abs_paths": [str(p.resolve()) for p in paths],
        "file_url": [p.resolve().as_uri() for p in paths],
        "unit_file": str((kb.root / m.get("file", "")).resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_fig(kb: KB, q: str) -> int:
    if not kb.figures:
        print("这个库里没有图索引（figures.json 不存在或为空）", file=sys.stderr)
        return 3
    qq = q.strip()
    hits = [f for f in kb.figures
            if qq in f.get("fig_id", "") or qq in f.get("caption", "")
            or (f.get("unit_id") and qq in f["unit_id"])]
    if not hits:
        print(f"没找到图：{qq}", file=sys.stderr)
        return 3
    for f in hits[:12]:
        print(f"{f['fig_id']:<14} p.{f['printed_page']:<5} (pdf {f['pdf_page']}) "
              f"{f['unit_id']:<28} {f['caption'][:60]}")
        print(f"                 {kb.root / f['file']}")
    return 0


def cmd_page(kb: KB, uid: str, page: int | None, dpi: int) -> int:
    m = unit_meta(kb, uid)
    paths = source_paths(kb, uid)
    if not paths:
        print(f"找不到单元 {uid} 的源 PDF（frontmatter.source_file）", file=sys.stderr)
        return 3
    pdf = paths[0]
    if page is None:
        print(f"该单元覆盖书上第 {m.get('source_pages')} 页（pdf {m.get('pdf_pages')}），"
              f"用 --page 指定要渲染的书上页码", file=sys.stderr)
        return 2
    # 书上页码 → PDF 物理页：用 source_pages/pdf_pages 的起点差
    def first(s: str) -> int:
        s = str(s).split(",")[0]
        return int(s.split("-")[0]) if re.match(r"^\d", s) else 0
    off = first(m.get("pdf_pages", "0")) - first(m.get("source_pages", "0"))
    pdf_page = page + off
    cache = kb.root / "_cache" / "pages"
    cache.mkdir(parents=True, exist_ok=True)
    out = cache / f"{uid}-p{page}.png"
    if not out.exists():
        subprocess.run(["pdftoppm", "-r", str(dpi), "-png", "-singlefile",
                        "-f", str(pdf_page), "-l", str(pdf_page), str(pdf), str(out)[:-4]],
                       check=False, capture_output=True)
    if out.exists():
        print(out)
        return 0
    print(f"渲染失败：{pdf} 第 {pdf_page} 页", file=sys.stderr)
    return 3


def cmd_verify(kb: KB, draft: Path, question: str | None, min_cov: float) -> int:
    text = draft.read_text(encoding="utf-8")
    cites = CITE.findall(text)
    print(f"引用核验：{draft.name}（原问题：{(question or '')[:40]}…）")
    if not cites:
        print("❌ 一处 [引] 都没有 —— 无出处不许交付")
        return 1
    fails: list[str] = []
    units = set()
    for uid, page_s, quote in cites:
        page = int(page_s)
        units.add(uid)
        if uid not in kb.by_id and not (kb.root / "units" / f"{uid}.md").exists():
            fails.append(f"单元不存在：{uid}")
            continue
        pages = unit_pages(kb, uid)
        if not pages:
            fails.append(f"读不到单元正文：{uid}")
            continue
        got = pages.get(page)
        if got is None:
            fails.append(f"{uid} 没有第 {page} 页（该单元覆盖 {sorted(pages)[:1]}–{sorted(pages)[-1:]}）")
            continue
        if norm(quote) not in norm(got):
            # 容忍跨页引文：查相邻页
            near = norm(pages.get(page - 1, "") + got + pages.get(page + 1, ""))
            if norm(quote) in near:
                print(f"  ⚠️ {uid} @p{page} 引文跨行/跨页，已在相邻页命中")
                continue
            fails.append(f"{uid} @p{page} 引文对不上原文：{quote[:40]!r}")
    print(f"  引用 {len(cites)} 处，涉及单元 {len(units)} 个")
    if question:
        qt = set(kb.content_tokens(question))
        cov = len([t for t in qt if t in norm(text)]) / max(len(qt), 1)
        print(f"  问题覆盖：{cov:.0%}")
        if cov < min_cov:
            missing = [t for t in qt if t not in norm(text)][:8]
            fails.append(f"引文没有覆盖问题的核心概念（覆盖率 {cov:.0%}，缺 {missing}）"
                         f" —— 属于答非所问")
    if fails:
        print(f"  ❌ 不通过（{len(fails)} 条硬错误）：")
        for f in fails[:12]:
            print(f"     - {f}")
        return 1
    print("  ✅ 通过：所有引用都能落到原文，无凭空数字")
    return 0


def cmd_stats(kb: KB) -> int:
    p = kb.index / "stats.json"
    if p.exists():
        print(p.read_text(encoding="utf-8"))
    else:
        print(json.dumps({"units": len(kb.units_meta), "chunks": len(kb.chunks),
                          "figures": len(kb.figures)}, ensure_ascii=False, indent=2))
    subs = Counter(u.get("subject", "?") for u in kb.units_meta)
    print("学科分布：" + "，".join(f"{k} {v}" for k, v in subs.most_common()))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="本地知识库检索 + 带出处问答闸门")
    ap.add_argument("--kb", default="kb", help="知识库根目录（含 units/ 和 _index/）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search"); s.add_argument("q"); s.add_argument("--subject")
    s.add_argument("--top", type=int, default=8); s.add_argument("--json", action="store_true")
    j = sub.add_parser("judge"); j.add_argument("q"); j.add_argument("--subject")
    g = sub.add_parser("get"); g.add_argument("unit_id")
    c = sub.add_parser("cite"); c.add_argument("unit_id")
    f = sub.add_parser("fig"); f.add_argument("q")
    pg = sub.add_parser("page"); pg.add_argument("unit_id")
    pg.add_argument("--page", type=int, default=None); pg.add_argument("--dpi", type=int, default=170)
    v = sub.add_parser("verify"); v.add_argument("draft"); v.add_argument("--question", default=None)
    v.add_argument("--min-cov", type=float, default=0.6)
    sub.add_parser("stats")

    args = ap.parse_args()
    root = Path(args.kb)
    if not (root / "_index" / "chunks.jsonl").exists():
        print(f"{root}/_index/chunks.jsonl 不存在，先跑 pdf-to-kb 的 build.py", file=sys.stderr)
        return 2
    kb = KB(root)

    if args.cmd == "search":
        return cmd_search(kb, args.q, args.subject, args.top, args.json)
    if args.cmd == "judge":
        return cmd_judge(kb, args.q, args.subject)
    if args.cmd == "get":
        return cmd_get(kb, args.unit_id)
    if args.cmd == "cite":
        return cmd_cite(kb, args.unit_id)
    if args.cmd == "fig":
        return cmd_fig(kb, args.q)
    if args.cmd == "page":
        return cmd_page(kb, args.unit_id, args.page, args.dpi)
    if args.cmd == "verify":
        return cmd_verify(kb, Path(args.draft), args.question, args.min_cov)
    return cmd_stats(kb)


if __name__ == "__main__":
    sys.exit(main())
