#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""figures.py — 把 PDF 里的图抽出来，并和图题（「图12-4-1 ……」）对上，建图索引。

为什么单独抽图：教材/资料里的图（示意图、地图、表格、时间轴）**OCR 抓不到内容**，
但如果能按「图号 → 页 → 单元 → 图片文件」定位，问答时就能把原图贴给用户，
而不是让模型凭图号瞎猜图意。

产物：
    kb/figures/*.png
    kb/_index/figures.json   [{fig_id, caption, pdf_page, printed_page, unit_id, file, w, h, type}]

用法：
    python3 figures.py --pdf book.pdf --work work/ --kb kb/
    python3 figures.py --pdf book.pdf --work work/ --kb kb/ --render-scan   # 扫描件：整页当图存
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

CAPTION_RE = re.compile(r"图\s*(\d+(?:\s*[-－—.．]\s*\d+){1,2})")
MIN_PX = 60 * 60          # 小于这个面积的图多半是图标/装饰


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def list_images(pdf: Path, p: int) -> list[dict]:
    r = run(["pdfimages", "-list", "-f", str(p), "-l", str(p), str(pdf)])
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 14 or not parts[0].isdigit():
            continue
        try:
            w, h = int(parts[3]), int(parts[4])
        except (ValueError, IndexError):
            continue
        out.append({"w": w, "h": h, "type": parts[2]})
    return out


def find_caption(text: str) -> tuple[str, str]:
    """返回 (图号, 图题整行)。图号形如 12-4-1；找不到返回 ('', '')。"""
    for line in text.splitlines():
        m = CAPTION_RE.search(line)
        if m:
            fid = "图" + re.sub(r"\s*[-－—.．]\s*", "-", m.group(1))
            return fid, line.strip()[:120]
    return "", ""


def unit_of(units: list[dict], p: int) -> str:
    for u in units:
        rng = u.get("pdf_pages", "")
        for part in str(rng).split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit() and int(a) <= p <= int(b):
                    return u["id"]
            elif part.isdigit() and int(part) == p:
                return u["id"]
    return ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--work", required=True)
    ap.add_argument("--kb", required=True)
    ap.add_argument("--min-px", type=int, default=MIN_PX)
    ap.add_argument("--render-scan", action="store_true",
                    help="没有内嵌位图的页面，整页渲染成图（扫描件用）")
    ap.add_argument("--render-dpi", type=int, default=150)
    ap.add_argument("--max-pages", type=int, default=None)
    args = ap.parse_args()

    pdf, work, kb = Path(args.pdf), Path(args.work), Path(args.kb)
    pages = json.loads((work / "pages.json").read_text(encoding="utf-8"))
    if args.max_pages:
        pages = [p for p in pages if p["pdf_page"] <= args.max_pages]
    upath = kb / "_index" / "units.json"
    units = json.loads(upath.read_text(encoding="utf-8"))["units"] if upath.exists() else []

    fig_dir = kb / "figures"
    fig_dir.mkdir(parents=True, exist_ok=True)
    index = []

    for pg in pages:
        p = pg["pdf_page"]
        txt_f = work / "_text" / f"p{p:04d}.txt"
        txt = txt_f.read_text(encoding="utf-8") if txt_f.exists() else ""
        imgs = [i for i in list_images(pdf, p) if i["w"] * i["h"] >= args.min_px]
        cap_id, cap = find_caption(txt)

        if imgs:
            with __import__("tempfile").TemporaryDirectory() as td:
                pref = str(Path(td) / "i")
                run(["pdfimages", "-f", str(p), "-l", str(p), "-png", "-p", str(pdf), pref])
                files = sorted(Path(td).glob("i-*.png"))
                for n, (meta, src) in enumerate(zip(imgs, files), 1):
                    name = f"{cap_id or 'p' + str(p)}-{n}.png" if cap_id else f"p{p}-{n}.png"
                    dest = fig_dir / name
                    shutil.copy(src, dest)
                    index.append({
                        "fig_id": cap_id if (cap_id and n == 1) else f"p{p}-{n}",
                        "caption": cap, "pdf_page": p, "printed_page": pg["printed_page"],
                        "unit_id": unit_of(units, p), "file": f"figures/{name}",
                        "w": meta["w"], "h": meta["h"], "type": "embedded",
                    })
        elif args.render_scan:
            with __import__("tempfile").TemporaryDirectory() as td:
                pref = str(Path(td) / "page")
                run(["pdftoppm", "-r", str(args.render_dpi), "-png", "-f", str(p),
                     "-l", str(p), str(pdf), pref])
                got = sorted(Path(td).glob("page*.png"))
                if got:
                    name = f"page-{p}.png"
                    shutil.copy(got[0], fig_dir / name)
                    index.append({
                        "fig_id": cap_id or f"p{p}", "caption": cap, "pdf_page": p,
                        "printed_page": pg["printed_page"], "unit_id": unit_of(units, p),
                        "file": f"figures/{name}", "w": 0, "h": 0, "type": "page_scan",
                    })
        if p % 20 == 0 or p == pages[-1]["pdf_page"]:
            print(f"\r  扫页 {p}/{pages[-1]['pdf_page']}　已抽图 {len(index)}", end="", flush=True)
    print()

    (kb / "_index" / "figures.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    named = sum(1 for f in index if f["fig_id"].startswith("图"))
    print(f"✅ 图 {len(index)} 张（其中 {named} 张有图号图题）→ {fig_dir}")
    if not index:
        print("   提示：这本书没有可用位图；若是扫描件加 --render-scan，"
              "或干脆不用图（只做文本问答）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
