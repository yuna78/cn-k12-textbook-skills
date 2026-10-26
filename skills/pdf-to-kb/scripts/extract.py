#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""extract.py — 把 PDF 逐页抽成文本（有文字层就直取，没有就 OCR），并建立页码映射。

输出目录结构：

    <out>/
      _text/p0001.txt ...        # 每页一个文本文件（页码按 PDF 物理页）
      pages.json                 # [{pdf_page, printed_page, chars, source: text|ocr}]
      meta.json                  # 页数、书名、是否扫描件、OCR 引擎

为什么逐页而不是整本：知识单元要能**引用到页**，整本抽完就没法定位了。

OCR 三种模式（--ocr）：
    auto        有文字层用文字层；空页/极短页自动 OCR（默认）
    tesseract   强制走 tesseract（需 `brew install tesseract tesseract-lang`）
    none        不 OCR（纯文字层 PDF 用这个，快）
    --ocr-cmd   自定义命令模板，占位符 {img} {out}，例如：
                --ocr-cmd "tesseract {img} {out} -l chi_sim"
                --ocr-cmd "myocr --lang chs {img} {out}"
                模板里没有 shell 元字符时直接调用二进制（Windows/macOS/Linux 一致）

用法：
    python3 extract.py book.pdf --out work/ --ocr auto
    python3 extract.py book.pdf --out work/ --printed-offset 10     # PDF第11页 = 书上第1页
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_CHARS = 40          # 一页有效文本少于此值，视为"没有文字层"
OCR_DPI = 250


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


SHELL_CHARS = set("|&;<>()$`*?[]~\n")


def run_shell(cmd: str) -> subprocess.CompletedProcess:
    """执行 --ocr-cmd 模板。

    ① 模板里没有 shell 元字符时 **直接调用二进制**（shlex 拆参数）——
       `tesseract {img} {out} -l chi_sim` 这类命令在 macOS / Linux / Windows 行为完全一致；
    ② 需要重定向或管道（如 `myocr {img} > {out}`）时才走 shell：
       macOS/Linux 用 bash -lc，Windows 用 cmd /c。
    """
    if not (SHELL_CHARS & set(cmd)):
        return run(shlex.split(cmd))
    if os.name == "nt":
        return run(["cmd", "/c", cmd])
    return run(["bash", "-lc", cmd])


def pdf_pages(pdf: Path) -> int:
    r = run(["pdfinfo", str(pdf)])
    for line in r.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split()[1])
    raise SystemExit("读不到 PDF 页数，pdfinfo 是否可用？")


def page_text(pdf: Path, p: int) -> str:
    r = run(["pdftotext", "-layout", "-f", str(p), "-l", str(p), str(pdf), "-"])
    return (r.stdout or "").replace("\x0c", "").strip()


def ocr_available(mode: str) -> tuple[bool, str]:
    if mode in ("none",):
        return False, "none"
    if shutil.which("tesseract"):
        return True, "tesseract"
    return False, "未安装 tesseract（macOS: brew install tesseract；Windows: 装 UB-Mannheim 版并加 PATH）"


def ocr_page(pdf: Path, p: int, cmd_tpl: str | None, lang: str) -> str:
    with tempfile.TemporaryDirectory() as td:
        prefix = str(Path(td) / "p")
        r = run(["pdftoppm", "-r", str(OCR_DPI), "-png", "-gray",
                 "-f", str(p), "-l", str(p), str(pdf), prefix])
        if r.returncode != 0:
            return ""
        imgs = sorted(Path(td).glob("p*.png"))
        if not imgs:
            return ""
        img = imgs[0]
        out = Path(td) / "out"
        if cmd_tpl:
            cmd = cmd_tpl.format(img=str(img), out=str(out))
            run_shell(cmd)
            txt = out.with_suffix(".txt")
            if txt.exists():
                return txt.read_text(encoding="utf-8", errors="replace").strip()
            return out.read_text(encoding="utf-8", errors="replace").strip() if out.exists() else ""
        r = run(["tesseract", str(img), str(out), "-l", lang])
        txt = out.with_suffix(".txt")
        return txt.read_text(encoding="utf-8", errors="replace").strip() if txt.exists() else ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--out", required=True, help="工作目录（会生成 _text/ pages.json meta.json）")
    ap.add_argument("--ocr", choices=["auto", "tesseract", "none"], default="auto")
    ap.add_argument("--ocr-cmd", default=None, help="自定义 OCR 命令模板，占位符 {img} {out}")
    ap.add_argument("--ocr-lang", default="chi_sim+eng")
    ap.add_argument("--printed-offset", type=int, default=None,
                    help="书上页码 = PDF页 - offset；不填则 printed_page = pdf_page")
    ap.add_argument("--max-pages", type=int, default=None, help="只处理前 N 页（试跑用）")
    ap.add_argument("--force", action="store_true", help="已存在的页也重新抽取")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        print(f"找不到 PDF：{pdf}", file=sys.stderr)
        return 2
    out = Path(args.out)
    text_dir = out / "_text"
    text_dir.mkdir(parents=True, exist_ok=True)

    total = pdf_pages(pdf)
    if args.max_pages:
        total = min(total, args.max_pages)
    print(f"PDF：{pdf.name}　页数：{total}")

    ok_ocr, ocr_engine = ocr_available(args.ocr)
    if args.ocr != "none" and not ok_ocr and not args.ocr_cmd:
        print(f"⚠️ {ocr_engine}；没有文字层的页会被跳过（或加 --ocr-cmd 指定自己的 OCR）",
              file=sys.stderr)

    pages, n_text, n_ocr, n_empty = [], 0, 0, 0
    for p in range(1, total + 1):
        f = text_dir / f"p{p:04d}.txt"
        if f.exists() and not args.force:
            txt = f.read_text(encoding="utf-8")
            src = "cache"
        else:
            txt = page_text(pdf, p)
            src = "text"
            if len(txt) < MIN_CHARS and args.ocr != "none":
                if args.ocr_cmd or ok_ocr:
                    ocr_txt = ocr_page(pdf, p, args.ocr_cmd,
                                       args.ocr_lang if args.ocr != "none" else "chi_sim")
                    if len(ocr_txt) > len(txt):
                        txt, src = ocr_txt, "ocr"
            f.write_text(txt, encoding="utf-8")

        if src in ("text", "cache", "ocr"):
            if len(txt) >= MIN_CHARS:
                n_text += src != "ocr"
                n_ocr += src == "ocr"
            else:
                n_empty += 1
        printed = p - args.printed_offset if args.printed_offset is not None else p
        pages.append({"pdf_page": p, "printed_page": printed,
                       "chars": len(txt), "source": src})
        if p % 20 == 0 or p == total:
            print(f"\r  抽页 {p}/{total}  文字 {n_text} / OCR {n_ocr} / 空 {n_empty}",
                  end="", flush=True)
    print()

    meta = {
        "pdf": str(pdf.resolve()),
        "pages": total,
        "ocr_mode": args.ocr,
        "ocr_engine": args.ocr_cmd and "custom" or ocr_engine,
        "ocr_dpi": OCR_DPI,
        "printed_offset": args.printed_offset,
        "scanned": n_text == 0 and n_ocr > 0,
        "counts": {"text": n_text, "ocr": n_ocr, "empty": n_empty},
    }
    (out / "pages.json").write_text(json.dumps(pages, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if n_text == 0 and n_ocr == 0:
        print("✗ 一页文字都没抽到：这个 PDF 是纯扫描件，且没有可用 OCR。\n"
              "  → 装 tesseract 或用 --ocr-cmd 指定你的 OCR。", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
