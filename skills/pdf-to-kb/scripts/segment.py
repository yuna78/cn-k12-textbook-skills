#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""segment.py — 把逐页文本切成**知识单元**（一章/一节一个 md），带 frontmatter 元信息。

两种切法：

1. 按清单切（推荐，最准）——写一份 manifest.json 列出每章覆盖哪些 PDF 页：
       python3 segment.py --work work/ --manifest book.manifest.json --out kb/
2. 按标题自动切——页首行命中正则就开新单元：
       python3 segment.py --work work/ --auto "^第[一二三四五六七八九十百]+[章单元节]" --out kb/

产物：
    kb/units/<id>.md      每单元一个 markdown：frontmatter + 正文（含 <!-- p.N --> 页标记）
    kb/_index/units.json  单元索引（id/title/章节/页范围/字数），供下游建检索用

frontmatter 字段（固定这几个，下游 ask-kb 依赖）：
    id / title / subject / book / chapter / source_file / pdf_pages / source_pages /
    tags / ocr_quality / updated
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

SAFE = re.compile(r"[^\w\u4e00-\u9fff.-]+")


def load_manifest(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit("给了 YAML 清单但没装 pyyaml；要么用 JSON，要么 pip install pyyaml")
        return yaml.safe_load(raw)
    return json.loads(raw)


def safe_id(s: str) -> str:
    return SAFE.sub("-", s).strip("-")


def page_range(nums: list[int]) -> str:
    if not nums:
        return ""
    nums = sorted(nums)
    groups, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        groups.append((start, prev))
        start = prev = n
    groups.append((start, prev))
    return ",".join(f"{a}" if a == b else f"{a}-{b}" for a, b in groups)


def build_unit(book: dict, unit: dict, pages: list[dict], work: Path,
               printed_offset: int | None) -> tuple[str, dict]:
    ids = sorted(unit["pdf_pages"])
    chunks = []
    for p in ids:
        f = work / "_text" / f"p{p:04d}.txt"
        txt = f.read_text(encoding="utf-8").strip() if f.exists() else ""
        printed = p - printed_offset if printed_offset is not None else p
        if txt:
            chunks.append(f"<!-- p.{printed} (pdf {p}) -->\n{txt}")
    body = "\n\n".join(chunks)
    printed_pages = [p - printed_offset if printed_offset is not None else p for p in ids]
    chars = len(re.sub(r"\s", "", body))
    ocr_pages = sum(1 for p in pages if p["pdf_page"] in ids and p["source"] == "ocr")
    quality = "high" if ocr_pages == 0 else ("medium" if ocr_pages < len(ids) * 0.5 else "low")

    fm = {
        "id": unit["id"],
        "title": unit["title"],
        "subject": book.get("subject", ""),
        "book": book.get("title", ""),
        "chapter": unit.get("chapter", unit["title"]),
        "source_file": book.get("pdf", ""),
        "pdf_pages": page_range(ids),
        "source_pages": page_range(printed_pages),
        "tags": unit.get("tags", []),
        "ocr_quality": quality,
        "updated": dt.date.today().isoformat(),
    }
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(str(x) for x in v)}]")
        else:
            lines.append(f"{k}: {v}")
    lines += ["---", "", f"# {unit['title']}", "", body, ""]
    return "\n".join(lines), {
        "id": unit["id"], "title": unit["title"], "chapter": fm["chapter"],
        "subject": fm["subject"], "book": fm["book"], "source_file": fm["source_file"],
        "pdf_pages": fm["pdf_pages"], "source_pages": fm["source_pages"],
        "chars": chars, "ocr_quality": quality,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", required=True, help="extract.py 的输出目录")
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--auto", default=None, help="自动切分用的标题正则")
    ap.add_argument("--out", required=True, help="知识库输出目录")
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()

    work = Path(args.work)
    pages = json.loads((work / "pages.json").read_text(encoding="utf-8"))
    meta = json.loads((work / "meta.json").read_text(encoding="utf-8"))
    out = Path(args.out)
    (out / "units").mkdir(parents=True, exist_ok=True)
    (out / "_index").mkdir(parents=True, exist_ok=True)

    offset = meta.get("printed_offset")
    index = []

    if args.manifest:
        mf = load_manifest(Path(args.manifest))
        book = mf.get("book", {})
        if book.get("printed_offset") is not None:
            offset = book["printed_offset"]
        for unit in mf["units"]:
            if args.max_pages:
                unit["pdf_pages"] = [p for p in unit["pdf_pages"] if p <= args.max_pages]
                if not unit["pdf_pages"]:
                    continue
            md, ent = build_unit(book, unit, pages, work, offset)
            name = safe_id(ent["id"]) + ".md"
            (out / "units" / name).write_text(md, encoding="utf-8")
            ent["file"] = f"units/{name}"
            index.append(ent)
            print(f"  ✓ {ent['id']:<28} pdf {ent['pdf_pages']:<10} "
                  f"书页 {ent['source_pages']:<10} {ent['chars']:>6} 字")
    elif args.auto:
        pat = re.compile(args.auto)
        cur = None
        for p in pages:
            if args.max_pages and p["pdf_page"] > args.max_pages:
                break
            f = work / "_text" / f"p{p['pdf_page']:04d}.txt"
            txt = f.read_text(encoding="utf-8").strip() if f.exists() else ""
            if not txt:
                continue
            first = txt.splitlines()[0].strip()
            if pat.search(first):
                if cur:
                    md, ent = build_unit(book_of(meta), cur, pages, work, offset)
                    (out / "units" / (safe_id(ent["id"]) + ".md")).write_text(md, encoding="utf-8")
                    ent["file"] = "units/" + safe_id(ent["id"]) + ".md"
                    index.append(ent)
                    print(f"  ✓ {ent['id']:<28} pdf {ent['pdf_pages']:<10} {ent['chars']:>6} 字")
                cur = {"id": f"p{p['pdf_page']}", "title": first[:40],
                       "pdf_pages": [p["pdf_page"]]}
            elif cur:
                cur["pdf_pages"].append(p["pdf_page"])
        if cur:
            md, ent = build_unit(book_of(meta), cur, pages, work, offset)
            (out / "units" / (safe_id(ent["id"]) + ".md")).write_text(md, encoding="utf-8")
            ent["file"] = "units/" + safe_id(ent["id"]) + ".md"
            index.append(ent)
            print(f"  ✓ {ent['id']:<28} pdf {ent['pdf_pages']:<10} {ent['chars']:>6} 字")
    else:
        print("要么给 --manifest，要么给 --auto <标题正则>", file=sys.stderr)
        return 2

    (out / "_index" / "units.json").write_text(
        json.dumps({"book": meta.get("pdf"), "units": index}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    total = sum(u["chars"] for u in index)
    print(f"\n✅ {len(index)} 个知识单元 / {total} 字 → {out}")
    return 0


def book_of(meta: dict) -> dict:
    return {"title": Path(meta.get("pdf", "")).stem, "pdf": meta.get("pdf", ""),
            "subject": "", "printed_offset": meta.get("printed_offset")}


if __name__ == "__main__":
    sys.exit(main())
