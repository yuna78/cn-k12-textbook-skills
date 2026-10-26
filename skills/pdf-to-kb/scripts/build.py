#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build.py — 把知识单元切成检索用的 chunk，并生成索引统计。

不做「向量库」：教材问答大多数问题是**关键词明确**的（"图12-4-1""赤壁之战的时间"），
BM25 + 中文二元组 在这个规模（几千 chunk）上又快又准，而且零依赖、可离线、可解释。
真需要语义检索时再换 chunk 落库即可（chunks.jsonl 就是干净的数据边界）。

产物：
    kb/_index/chunks.jsonl   [{id, unit_id, unit_title, chapter, page, pdf_page, heading, text}]
    kb/_index/stats.json     {units, chunks, chars, pages, figures, avg_chunk_chars}

用法：
    python3 build.py --kb kb/ --chunk-chars 420
    python3 build.py --kb kb/ --verify        # 额外检查 frontmatter 必填字段是否齐全
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REQUIRED = ["id", "title", "source_file", "pdf_pages"]
PAGE_MARK = re.compile(r"^<!--\s*p\.(\d+)(?:\s*\(pdf\s*(\d+)\))?\s*-->\s*$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    raw = text[3:end].strip("\n")
    body = text[end + 4:]
    fm: dict = {}
    for line in raw.splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            fm[k] = [x.strip() for x in v[1:-1].split(",") if x.strip()]
        else:
            fm[k] = v
    return fm, body


def split_pages(body: str) -> list[tuple[int, int | None, str]]:
    """按 <!-- p.N (pdf M) --> 切页，返回 [(printed, pdf, text)]。"""
    out, cur_p, cur_pdf, buf = [], 0, None, []
    for line in body.splitlines():
        m = PAGE_MARK.match(line.strip())
        if m:
            if buf and any(x.strip() for x in buf):
                out.append((cur_p, cur_pdf, "\n".join(buf).strip()))
            cur_p = int(m.group(1))
            cur_pdf = int(m.group(2)) if m.group(2) else None
            buf = []
        else:
            buf.append(line)
    if buf and any(x.strip() for x in buf):
        out.append((cur_p, cur_pdf, "\n".join(buf).strip()))
    return out


def chunk_page(text: str, target: int) -> list[str]:
    """按段落聚合成 ~target 字的块，不切碎句子。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf, n = [], [], 0
    for p in paras:
        # 超长段落（OCR 常把整页当一段）再按句子切
        if len(p) > target * 2:
            sents = re.split(r"(?<=[。！？；])", p)
            for s in sents:
                s = s.strip()
                if not s:
                    continue
                if n + len(s) > target and buf:
                    chunks.append("".join(buf))
                    buf, n = [], 0
                buf.append(s)
                n += len(s)
            continue
        if n + len(p) > target and buf:
            chunks.append("\n".join(buf))
            buf, n = [], 0
        buf.append(p)
        n += len(p)
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", required=True)
    ap.add_argument("--chunk-chars", type=int, default=420)
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()

    kb = Path(args.kb)
    unit_dir = kb / "units"
    files = sorted(unit_dir.glob("*.md"))
    if not files:
        print(f"{unit_dir} 里没有 .md，先跑 segment.py", file=sys.stderr)
        return 2

    chunks, bad, total_chars = [], [], 0
    for f in files:
        fm, body = parse_frontmatter(f.read_text(encoding="utf-8"))
        missing = [k for k in REQUIRED if not fm.get(k)]
        if missing:
            bad.append((f.name, missing))
            if args.verify:
                continue
        uid = fm.get("id") or f.stem
        title = fm.get("title", uid)
        chapter = fm.get("chapter", "")
        pages = split_pages(body)
        # 首个页码锚点之前的正文（通常是单元标题块）原来会落到 page=0，
        # 引用时页码就没法用；改成继承本单元第一个已知页码。
        if pages and pages[0][0] == 0:
            nxt = next(((pp, pd) for pp, pd, _ in pages if pp > 0), None)
            if nxt:
                pages[0] = (nxt[0], nxt[1], pages[0][2])
            else:
                first = str(fm.get("source_pages") or fm.get("pdf_pages") or "").split("-")[0]
                if first.isdigit():
                    pages[0] = (int(first), None, pages[0][2])
        for printed, pdf, txt in pages:
            total_chars += len(re.sub(r"\s", "", txt))
            for i, ch in enumerate(chunk_page(txt, args.chunk_chars)):
                chunks.append({
                    "id": f"{uid}#p{printed}#{i}",
                    "unit_id": uid,
                    "unit_title": title,
                    "chapter": chapter,
                    "page": printed,
                    "pdf_page": pdf,
                    "heading": title,
                    "text": ch,
                })

    (kb / "_index").mkdir(parents=True, exist_ok=True)
    with (kb / "_index" / "chunks.jsonl").open("w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    figs = 0
    fj = kb / "_index" / "figures.json"
    if fj.exists():
        figs = len(json.loads(fj.read_text(encoding="utf-8")))
    units = json.loads((kb / "_index" / "units.json").read_text(encoding="utf-8"))["units"] \
        if (kb / "_index" / "units.json").exists() else []

    stats = {
        "units": len(files),
        "chunks": len(chunks),
        "chars": total_chars,
        "avg_chunk_chars": round(total_chars / max(len(chunks), 1)),
        "figures": figs,
        "books": sorted({u.get("book") for u in units if u.get("book")}),
        "bad_frontmatter": [{"file": n, "missing": m} for n, m in bad],
    }
    (kb / "_index" / "stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))

    if bad:
        print(f"\n⚠️ {len(bad)} 个单元 frontmatter 缺必填字段 {REQUIRED}：", file=sys.stderr)
        for n, m in bad[:5]:
            print(f"   {n}: 缺 {m}", file=sys.stderr)
        return 3 if args.verify else 0
    print(f"\n✅ 索引完成：{len(chunks)} 个 chunk / {len(files)} 个单元")
    return 0


if __name__ == "__main__":
    sys.exit(main())
