#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""make_material.py — 生成公众号/README 用的配图（可复现）。

做五张图，全部来自**真实跑出来的结果**，不是画的示意图：

    01-教材目录.png     真实下载下来的教材清单（数据由 --inventory 传 JSON）
    02-知识单元.png     真实的单元 md（frontmatter + 页码锚点，锚点高亮）
    03-检索与引用.png   真实的 judge / search / cite 终端输出
    04-闸门对比.png     真实 verify：正确引用通过 vs 编造引用被拦下
    05-流程与闸门.png   三个 skill 与三道闸门的结构图

依赖：Chrome（headless 截图）+ Pillow（裁白边）+ poppler（跑流水线）。
用法：
    python3 make_material.py --pdf books/历史_第一周基本史实过关.pdf
    python3 make_material.py --pdf books/x.pdf --inventory inventory.json --out docs/material

`--inventory` 格式见 inventory.example.json；不传则跳过 01。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FONT = "-apple-system, 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif"
MONO = "'SF Mono', Menlo, Consolas, monospace"

BG_LIGHT = "#ffffff"
INK = "#1a1a1a"
MUTED = "#6b7280"
LINE = "#e5e7eb"
ACCENT = "#2563eb"
GREEN = "#059669"
RED = "#dc2626"


# ---------- 渲染 ----------

def render(html_text: str, out_png: Path, width: int = 1240, pad: int = 40) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="mat-"))
    html_file = tmp / "p.html"
    html_file.write_text(html_text, encoding="utf-8")
    raw = tmp / "shot.png"
    cmd = [
        CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--force-device-scale-factor=2",
        "--disable-crash-reporter", "--disable-breakpad",
        f"--crash-dumps-dir={tmp}", f"--user-data-dir={tmp / 'ud'}",
        f"--window-size={width},2600",
        f"--screenshot={raw}", html_file.as_uri(),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not raw.exists():
        print(f"Chrome 截图失败：{r.stderr[-400:]}", file=sys.stderr)
        raise SystemExit(4)

    # 裁掉底部空白：找出最后一行非背景像素
    try:
        from PIL import Image
        im = Image.open(raw).convert("RGB")
        w, h = im.size
        px = im.load()
        bg = px[2, h - 2]
        last = h - 1
        for y in range(h - 1, 0, -1):
            row_has = any(px[x, y] != bg for x in range(0, w, 7))
            if row_has:
                last = y
                break
        bottom = min(h, last + pad * 2)
        im.crop((0, 0, w, bottom)).save(out_png)
    except ImportError:
        shutil.copy(raw, out_png)
    print(f"  ✓ {out_png.relative_to(REPO) if out_png.is_relative_to(REPO) else out_png}")
    shutil.rmtree(tmp, ignore_errors=True)


def page(body: str, width: int = 1240, bg: str = BG_LIGHT,
         padding: str = "44px 48px", extra_css: str = "") -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
    * {{ box-sizing: border-box; }}
    body {{ margin:0; width:{width}px; background:{bg}; color:{INK};
           font-family:{FONT}; padding:{padding}; -webkit-font-smoothing:antialiased; }}
    {extra_css}
    </style></head><body>{body}</body></html>"""


def esc(s: str) -> str:
    return html.escape(s)


# ---------- 01 教材目录 ----------

def fig_inventory(inv: dict, out: Path) -> None:
    rows = []
    for i, b in enumerate(inv["books"], 1):
        rows.append(f"""<tr>
          <td class="num">{i}</td>
          <td class="subj">{esc(b['subject'])}</td>
          <td>{esc(b['volume'])}</td>
          <td class="ver">{esc(b['edition'])}</td>
          <td class="num">{b['pages']}</td>
          <td class="num">{esc(b['size'])}</td></tr>""")
    body = f"""
    <div class="h1">{esc(inv['title'])}</div>
    <div class="h2">{esc(inv['subtitle'])}</div>
    <table>
      <thead><tr><th>#</th><th>学科</th><th>册次</th><th>版本 / 出版社</th>
      <th>页数</th><th>大小</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <div class="foot">{esc(inv.get('footnote', ''))}</div>"""
    css = f"""
    .h1 {{ font-size:34px; font-weight:700; letter-spacing:-.5px; }}
    .h2 {{ font-size:17px; color:{MUTED}; margin:10px 0 26px; }}
    table {{ width:100%; border-collapse:collapse; font-size:17px; }}
    th {{ text-align:left; font-weight:600; color:{MUTED}; font-size:14px;
          letter-spacing:.06em; padding:0 10px 10px; border-bottom:2px solid {INK}; }}
    td {{ padding:11px 10px; border-bottom:1px solid {LINE}; }}
    tr:nth-child(even) td {{ background:#fafafa; }}
    td.num {{ text-align:right; font-variant-numeric:tabular-nums; color:{MUTED}; }}
    th:nth-child(5), th:nth-child(6) {{ text-align:right; }}
    td.subj {{ font-weight:600; }}
    td.ver {{ color:#374151; font-size:16px; }}
    .foot {{ margin-top:22px; font-size:15px; color:{MUTED}; line-height:1.6;
             padding-top:16px; border-top:1px solid {LINE}; }}
    """
    render(page(body, extra_css=css), out)


# ---------- 02 知识单元 md ----------

def fig_unit(md_path: Path, out: Path, caption: str | None = None,
             max_lines: int = 34, deep: bool = False, redact: bool = False) -> None:
    text = md_path.read_text(encoding="utf-8")
    fm, _, body = text.partition("\n---\n")
    fm = fm.replace("---", "", 1)
    fm_lines = []
    for line in fm.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() == "source_file":
                v = f" books/{Path(v.strip()).name}"
            fm_lines.append(f"<div><span class='k'>{esc(k)}:</span> {esc(v.strip())}</div>")
    body_lines, n_text = [], 0
    lines = [ln.rstrip() for ln in body.splitlines()]
    for line in lines:
        s = line
        if not s:
            continue
        if not s.startswith("<!-- p.") and not s.startswith("#"):
            n_text += 1
            if n_text > max_lines:
                body_lines.append("<div class='more'>…（余下正文省略）</div>")
                break
        if s.startswith("<!-- p."):
            body_lines.append(f"<div class='mark'>{esc(s)}"
                              f"<span class='tip'>← 页码锚点：引用校验靠它定位</span></div>")
        elif s.startswith("# "):
            body_lines.append(f"<div class='h'>{esc(s[2:])}</div>")
        elif redact:
            w = max(24, min(96, len(s) * 1.6))
            body_lines.append(f"<div class='bar' style='width:{w}%'></div>")
        else:
            body_lines.append(f"<div class='t'>{esc(s)}</div>")
    body = f"""
    <div class="h1">{'课本 → 知识单元 md' if deep else '知识单元（markdown）'}</div>
    <div class="h2">{esc(caption) if caption else '每个单元一个文件：元信息 + 逐页正文，页码留在正文里'}</div>
    <div class="fm">{''.join(fm_lines)}</div>
    <div class="body">{''.join(body_lines)}</div>"""
    css = f"""
    .h1 {{ font-size:30px; font-weight:700; }}
    .h2 {{ font-size:16px; color:{MUTED}; margin:8px 0 26px; }}
    .fm {{ background:#0f172a; color:#cbd5e1; border-radius:12px; padding:18px 22px;
           font-family:{MONO}; font-size:15px; line-height:1.85; }}
    .fm .k {{ color:#7dd3fc; }}
    .body {{ margin-top:22px; font-size:18px; line-height:2.0; }}
    .h {{ font-size:23px; font-weight:700; margin:6px 0 14px; }}
    .t {{ color:#262626; }}
    .mark {{ font-family:{MONO}; font-size:15px; color:{ACCENT};
             background:#eff6ff; border-left:4px solid {ACCENT};
             padding:7px 12px; margin:16px 0 4px; border-radius:0 6px 6px 0; }}
    .mark .tip {{ color:{MUTED}; font-family:{FONT}; font-size:14px; margin-left:12px; }}
    .more {{ color:{MUTED}; font-size:15px; margin-top:10px; }}
    .bar {{ height:15px; border-radius:4px; background:#e8eaed; margin:11px 0; }}
    """
    render(page(body, extra_css=css), out)


# ---------- 03 / 04 终端 ----------

def term(blocks: list[tuple[str, str, str]]) -> str:
    """blocks: [(提示符, 输出, 色调)]"""
    out = []
    for cmd, out_text, tone in blocks:
        out.append(f"<div class='cmd'><span class='ps'>$</span> {esc(cmd)}</div>")
        color = {"ok": "#a7f3d0", "bad": "#fecaca", "dim": "#94a3b8"}.get(tone, "#e2e8f0")
        for line in out_text.splitlines():
            cls = "out"
            if line.startswith("✅"):
                cls += " good"
            elif line.startswith("❌") or line.startswith("     -"):
                cls += " bad"
            out.append(f"<div class='{cls}'>{esc(line) or '&nbsp;'}</div>")
    return "<div class='term'>" + "".join(out) + "</div>"


TERM_CSS = f"""
.term {{ background:#0b1220; border-radius:12px; padding:22px 24px; font-family:{MONO};
         font-size:15px; line-height:1.75; color:#e2e8f0; overflow:hidden; }}
.cmd {{ color:#e2e8f0; margin-top:14px; }} .cmd:first-child {{ margin-top:0; }}
.ps {{ color:{GREEN}; font-weight:700; }}
.out {{ color:#94a3b8; white-space:pre-wrap; }}
.out.good {{ color:#6ee7b7; }}
.out.bad {{ color:#fca5a5; }}
h1 {{ font-size:27px; font-weight:700; margin:0 0 6px; }}
.sub {{ font-size:15px; color:{MUTED}; margin-bottom:20px; font-family:{FONT}; }}
"""


def fig_terminal(blocks, out: Path, title: str, sub: str, dark: bool = False) -> None:
    body = f"<h1>{esc(title)}</h1><div class='sub'>{esc(sub)}</div>{term(blocks)}"
    bg = "#ffffff"
    extra = TERM_CSS
    if dark:
        bg = "#0b1220"
        extra = TERM_CSS + f"h1, .sub {{ color:#e2e8f0; }} .sub {{ color:#94a3b8; }}"
    render(page(body, bg=bg, extra_css=extra), out)


# ---------- 06 原页 ↔ md 对照 ----------

def fig_compare(page_png: Path, md_path: Path, out: Path, page_label: str,
                max_lines: int = 26, proof: list[tuple[str, str]] | None = None) -> None:
    """左边课本原页（页图），右边同一页 OCR 出来的 md —— 一眼看出这条流水线做了什么。"""
    import base64
    from PIL import Image

    # 裁掉四周白边，避免图里大片空白
    im = Image.open(page_png).convert("RGB")
    w, h = im.size
    px = im.load()
    def row_ink(y):
        return any(sum(px[x, y]) < 700 for x in range(0, w, 5))
    def col_ink(x):
        return any(sum(px[x, y]) < 700 for y in range(0, h, 5))
    top = next((y for y in range(h) if row_ink(y)), 0)
    bot = next((y for y in range(h - 1, -1, -1) if row_ink(y)), h - 1)
    left = next((x for x in range(w) if col_ink(x)), 0)
    right = next((x for x in range(w - 1, -1, -1) if col_ink(x)), w - 1)
    im = im.crop((max(0, left - 12), max(0, top - 12),
                  min(w, right + 12), min(h, bot + 12)))
    tmp = Path(tempfile.mkdtemp(prefix="cmp-")) / "page.png"
    im.save(tmp)
    b64 = base64.b64encode(tmp.read_bytes()).decode()

    text = md_path.read_text(encoding="utf-8")
    fm, _, body = text.partition("\n---\n")
    keep_keys = {"id", "title", "book", "source_pages", "pdf_pages", "ocr_quality"}
    fm_html = []
    for line in fm.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            if k.strip() in keep_keys:
                fm_html.append(f"<div><span class='k'>{esc(k)}:</span> {esc(v.strip())}</div>")
    body_html, n = [], 0
    for line in body.splitlines():
        t = line.rstrip()
        if not t:
            continue
        if t.startswith("<!-- p."):
            body_html.append(f"<div class='mark'>{esc(t)}<span class='tip'>← 页码锚点</span></div>")
        elif t.startswith("# "):
            continue
        else:
            n += 1
            if n > max_lines:
                body_html.append("<div class='more'>…（余下正文省略）</div>")
                break
            body_html.append(f"<div class='t'>{esc(t)}</div>")

    proof_html = ""
    if proof:
        rows = []
        for cmd, res in proof:          # 别用 out：会覆盖 out_png
            rows.append(f"<div class='cmd'><span class='ps'>$</span> {esc(cmd)}</div>"
                        f"<div class='out'>{esc(res)}</div>")
        proof_html = ("<div class='proof'><div class='pt'>同一份内容，两种取法（实测）</div>"
                      + "".join(rows) + "</div>")
    body = f"""
    <div class="h1">对照：课本原页 ↔ 知识单元 md</div>
    <div class="h2">同一页、同一条流水线。左边是 PDF 里的页图（<b>没有文字层</b>），
      右边是 OCR 装配后的 md（页码锚点留在正文里）</div>
    <div class="two">
      <div class="col">
        <div class="cap">① 课本 PDF · {esc(page_label)}（页图，存不了、搜不了）</div>
        <img src="data:image/png;base64,{b64}">
      </div>
      <div class="col">
        <div class="cap">② 同页 → 知识单元 md（可检索、可引用）</div>
        <div class="md">
          <div class="fm">{''.join(fm_html)}</div>
          <div class="body">{''.join(body_html)}</div>
        </div>
      </div>
    </div>
    {proof_html}
    <div class="foot">左边那页搜不到「三角贸易」四个字；右边能搜到，而且每条引用都能落到 <b>p.89</b>。
      <div class="cr">页图取自国家中小学智慧教育平台的公开电子课本，版权归原出版社；
      此处仅用于演示 OCR 与检索能力，不提供、不传播教材 PDF 原件。</div></div>"""
    css = f"""
    .h1 {{ font-size:31px; font-weight:700; }}
    .proof {{ margin-top:22px; background:#0b1220; border-radius:10px; padding:16px 20px;
              font-family:{MONO}; font-size:13.5px; line-height:1.7; }}
    .proof .pt {{ color:#64748b; font-family:{FONT}; font-size:13px; margin-bottom:8px; }}
    .proof .cmd {{ color:#e2e8f0; margin-top:8px; }}
    .proof .ps {{ color:{GREEN}; font-weight:700; }}
    .proof .out {{ color:#6ee7b7; white-space:pre-wrap; }}
    .h2 {{ font-size:15px; color:{MUTED}; margin:8px 0 24px; line-height:1.6; }}
    .two {{ display:flex; gap:26px; align-items:flex-start; }}
    .col {{ flex:1; }}
    .cap {{ font-size:14px; color:{MUTED}; margin-bottom:10px; }}
    img {{ width:100%; border:1px solid {LINE}; border-radius:8px; display:block; }}
    .md {{ background:#fbfbfc; border:1px solid {LINE}; border-radius:10px; padding:16px 18px; }}
    .fm {{ font-family:{MONO}; font-size:12.5px; line-height:1.7; color:#334155;
           background:#0f172a; color:#cbd5e1; border-radius:8px; padding:12px 14px; }}
    .fm .k {{ color:#7dd3fc; }}
    .body {{ font-size:14.5px; line-height:1.85; margin-top:12px; color:#262626; }}
    .mark {{ font-family:{MONO}; font-size:12px; color:{ACCENT}; background:#eff6ff;
             border-left:3px solid {ACCENT}; padding:5px 9px; margin:10px 0 6px;
             border-radius:0 5px 5px 0; }}
    .mark .tip {{ color:{MUTED}; font-family:{FONT}; margin-left:8px; }}
    .more {{ color:{MUTED}; font-size:13px; }}
    .foot {{ margin-top:24px; padding-top:14px; border-top:1px solid {LINE};
             font-size:15px; color:#374151; }}
    .foot b {{ color:{ACCENT}; }}
    .cr {{ font-size:12.5px; color:#9ca3af; margin-top:8px; }}
    """
    render(page(body, extra_css=css), out)


# ---------- 05 结构图 ----------

def fig_flow(out: Path) -> None:
    body = """
    <div class="h1">三个 skill，三道闸门</div>
    <div class="h2">下载 → 拆成可引用的知识 → 只回答有出处的问答</div>
    <div class="row">
      <div class="card"><div class="n">1</div><div class="t">fetch-pdf</div>
        <div class="d">国家中小学智慧教育平台<br>电子课本没有下载按钮</div>
        <div class="k">元数据解析 · 1KB 探测 · 断点续传</div></div>
      <div class="arrow">→</div>
      <div class="card"><div class="n">2</div><div class="t">pdf-to-kb</div>
        <div class="d">300 页读不动<br>抽出来没页码</div>
        <div class="k">逐页抽取 · 页码锚点 · 图索引</div></div>
      <div class="arrow">→</div>
      <div class="card"><div class="n">3</div><div class="t">ask-kb</div>
        <div class="d">答得像样<br>但查不到出处</div>
        <div class="k">judge · 检索 · verify</div></div>
    </div>
    <div class="gates">
      <div class="g"><span class="gt">闸门 ①</span><b>judge</b> 先判能不能答<br>
        <span class="gd">可靠 / 存疑 / 无</span></div>
      <div class="g"><span class="gt">闸门 ②</span><b>检索</b> 拿到单元 + 页码 + 逐字原文<br>
        <span class="gd">不许凭记忆</span></div>
      <div class="g"><span class="gt">闸门 ③</span><b>verify</b> 引文必须是原文子串<br>
        <span class="gd">改一个字就 exit 1</span></div>
    </div>
    <div class="foot">判「可靠」但 verify 不过 → <b>以 verify 为准，拒答</b></div>"""
    css = f"""
    .h1 {{ font-size:32px; font-weight:700; }}
    .h2 {{ font-size:16px; color:{MUTED}; margin:8px 0 30px; }}
    .row {{ display:flex; align-items:stretch; gap:16px; }}
    .card {{ flex:1; border:1.5px solid {LINE}; border-radius:14px; padding:20px 20px 18px; }}
    .n {{ width:26px; height:26px; border-radius:50%; background:{INK}; color:#fff;
          font-size:15px; font-weight:700; display:flex; align-items:center;
          justify-content:center; margin-bottom:12px; }}
    .t {{ font-family:{MONO}; font-size:21px; font-weight:700; }}
    .d {{ font-size:15px; color:#374151; margin:10px 0 14px; line-height:1.6; }}
    .k {{ font-size:14px; color:{ACCENT}; background:#eff6ff; padding:7px 10px;
          border-radius:7px; line-height:1.5; }}
    .arrow {{ display:flex; align-items:center; color:#9ca3af; font-size:30px; }}
    .gates {{ display:flex; gap:16px; margin-top:30px; }}
    .g {{ flex:1; background:#f8fafc; border-radius:12px; padding:16px 18px;
          font-size:15px; line-height:1.8; color:#334155; }}
    .gt {{ display:block; font-size:13px; letter-spacing:.1em; color:{MUTED}; }}
    .gd {{ color:{MUTED}; font-size:14px; }}
    .foot {{ margin-top:26px; padding-top:16px; border-top:1px solid {LINE};
             font-size:16px; color:{MUTED}; }}
    .foot b {{ color:{RED}; }}
    """
    render(page(body, extra_css=css), out)


# ---------- 主流程：跑 demo，拿真实输出 ----------

DEMO_PDF_NAME = "历史_九年级_节选.pdf"


def scrub(text: str) -> str:
    """把真实绝对路径洗成中性相对路径 —— 素材要发公众号，不能带出本机用户名。"""
    import re
    text = re.sub(r"\"[^\"]*?/books/[^\"]*\"", f'\"books/{DEMO_PDF_NAME}\"', text)
    text = re.sub(r"[^\s\"']*?/books/[^\s\"',]+", f"books/{DEMO_PDF_NAME}", text)
    text = re.sub(r"/Users/[^\s]+?/(books/[^\s]+)", r"\1", text)
    text = re.sub(r"/Users/[^\s]+?/([^\s]*)", r"…/\1", text)
    return text


def build_demo(pdf: Path, work: Path) -> Path:
    py = sys.executable
    kb = work / "kb"
    books = work / "books"
    books.mkdir(parents=True, exist_ok=True)
    demo_pdf = books / DEMO_PDF_NAME
    shutil.copy(pdf, demo_pdf)          # 复制并改名：截图里只出现中性路径
    pdf = demo_pdf
    run = lambda c: subprocess.run(c, capture_output=True, text=True, cwd=REPO)
    run([py, "skills/pdf-to-kb/scripts/extract.py", str(pdf), "--out", str(work), "--ocr", "none"])
    pages = json.loads((work / "pages.json").read_text(encoding="utf-8"))
    digits = [p["pdf_page"] for p in pages if p["chars"] > 40]
    units = []
    for p in digits:
        first = (work / "_text" / f"p{p:04d}.txt").read_text(encoding="utf-8").strip().splitlines()
        title = (first[0].strip() if first else f"第 {p} 页")[:24] or f"第 {p} 页"
        import re as _re
        safe = _re.sub(r"[^\w\u4e00-\u9fff]+", "", title) or f"p{p}"
        units.append({"id": f"历史-{safe}", "title": title,
                      "chapter": "第一周", "pdf_pages": [p]})
    mf = work / "manifest.json"
    mf.write_text(json.dumps({"book": {"id": "demo", "title": "历史 九年级（示例）",
                                       "subject": "历史", "pdf": str(pdf)},
                              "units": units}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    run([py, "skills/pdf-to-kb/scripts/segment.py", "--work", str(work),
         "--manifest", str(mf), "--out", str(kb)])
    run([py, "skills/pdf-to-kb/scripts/build.py", "--kb", str(kb)])
    return kb


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pdf", required=True, help="用于生成 02/03/04 的 PDF（需有文字层）")
    ap.add_argument("--inventory", default=None, help="教材清单 JSON（生成 01）")
    ap.add_argument("--out", default=str(HERE))
    ap.add_argument("--only", default=None, help="只生成某一张，如 05")
    ap.add_argument("--unit-md", default=None,
                    help="直接用这个知识单元 md 生成 02 号图（课本流水线的产物）")
    ap.add_argument("--unit-caption", default=None, help="02 号图副标题")
    ap.add_argument("--redact", action="store_true", help="02 号图把课本正文换成灰条（公开发用）")
    ap.add_argument("--unit-lines", type=int, default=34, help="02 号图正文最多显示多少行（课本用 9 行＝短节选）")
    ap.add_argument("--page-png", default=None, help="06 号对照图用的课本原页 PNG（pdftoppm 渲染）")
    ap.add_argument("--page-label", default="p.89", help="06 号图左侧标注，如 p.89")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    pdf = Path(args.pdf).resolve()
    py = sys.executable

    work = Path(tempfile.mkdtemp(prefix="mat-demo-"))
    kb = build_demo(pdf, work)
    K = [py, "skills/ask-kb/scripts/kb.py", "--kb", str(kb)]
    def kb_cmd(*a):
        raw = subprocess.run(K + list(a), capture_output=True, text=True, cwd=REPO).stdout
        return scrub(raw.strip())

    if not args.only or args.only == "01":
        if args.inventory:
            fig_inventory(json.loads(Path(args.inventory).read_text(encoding="utf-8")),
                          out / "01-教材目录.png")
        else:
            print("  - 跳过 01（没给 --inventory）")

    unit_id = None
    ulist = json.loads((kb / "_index" / "units.json").read_text(encoding="utf-8"))["units"]
    for u in ulist:
        if u["chars"] > 300:
            unit_id = u["id"]
            break
    unit_file = kb / "units" / f"{unit_id}.md"

    if not args.only or args.only == "02":
        if args.unit_md:
            name = "02-课本知识单元-脱敏版.png" if args.redact else "02-课本知识单元.png"
            fig_unit(Path(args.unit_md), out / name,
                     caption=args.unit_caption, deep=True, redact=args.redact,
                     max_lines=args.unit_lines)
        else:
            fig_unit(unit_file, out / "02-知识单元.png")

    q = "戊戌变法是什么性质的运动"
    if not args.only or args.only == "03":
        judge_out = scrub(kb_cmd("judge", q))
        # search 用 --json 自己排版，避免把整段原文糊上去
        try:
            hits = json.loads(kb_cmd("search", "洋务运动 甲午中日战争", "--top", "2", "--json"))
        except json.JSONDecodeError:
            hits = []
        s_lines = []
        for h in hits:
            snippet = " ".join(h["text"].split())[:58]
            s_lines.append(f'[ {h["score"]:5.2f} ] {h["unit_id"]}  @p{h["page"]}  '
                           f'{h["unit_title"]}')
            s_lines.append(f"          {snippet}…")
        # cite 用 JSON 抽关键字段
        try:
            c = json.loads(kb_cmd("cite", unit_id))
            c_lines = [f'"source_file" : "{c["source_file"]}"',
                       f'"source_pages": "{c["source_pages"]}"  (pdf {c["pdf_pages"]})',
                       f'"abs_path"   : {"" if not c["abs_paths"] else c["abs_paths"][0]}']
        except json.JSONDecodeError:
            c_lines = [scrub(kb_cmd("cite", unit_id))]
        fig_terminal([
            (f'K judge "{q}"', judge_out, "ok"),
            ('K search "洋务运动 甲午中日战争" --top 2', "\n".join(s_lines), "ok"),
            (f"K cite {unit_id}", "\n".join(c_lines), "dim"),
        ], out / "03-检索与引用.png",
           "检索、判定、出处", "先判能不能答 → 取原文 → 拿文件名和页码（引用要用的三样东西）")

    if not args.only or args.only == "04":
        # 真正找到包含该知识点的页，再从原文里取一句（不能用别处的页去引）
        try:
            hits = json.loads(kb_cmd("search", q, "--top", "1", "--json"))
            h = hits[0]
        except (json.JSONDecodeError, IndexError):
            print("  ✗ 04 需要检索命中，跳过", file=sys.stderr)
            h = None
        if h:
            uid, pgno = h["unit_id"], h["page"]
            md = (kb / "units" / f"{uid}.md").read_text(encoding="utf-8")
            seg, keep = "", False
            for line in md.splitlines():
                m = __import__("re").match(r"<!--\s*p\.(\d+)", line.strip())
                if m:
                    keep = int(m.group(1)) == pgno
                    continue
                if keep:
                    seg += line + "\n"
            flat = "".join(seg.split())
            quote = ""
            for sent in __import__("re").split(r"(?<=[。！？；])", flat):
                if "戊戌变法" in sent and len(sent) > 8:
                    quote = sent.strip()
                    break
            quote = quote or flat[:40]
            fake = quote.replace("思想解放", "推翻清朝") or (quote + "（错）")
            if fake == quote:
                fake = quote.replace("改良", "革命") or (quote + "（错）")
            good = work / "draft_good.md"
            good.write_text(f"# 戊戌变法的性质\n\n戊戌变法是一场思想解放运动。\n\n"
                            f"[引] {uid} @p{pgno} :: {quote}\n", encoding="utf-8")
            bad = work / "draft_bad.md"
            bad.write_text(f"# 戊戌变法的性质\n\n戊戌变法推翻了清朝统治。\n\n"
                           f"[引] {uid} @p{pgno} :: {fake}\n", encoding="utf-8")
            pass_out = scrub(subprocess.run(K + ["verify", str(good), "--question", q],
                                            capture_output=True, text=True, cwd=REPO).stdout.strip())
            fail_out = scrub(subprocess.run(K + ["verify", str(bad), "--question", q],
                                            capture_output=True, text=True, cwd=REPO).stdout.strip())
            body = (f"<h1>引文闸门：真的放行，编的拦下</h1>"
                    f"<div class='sub'>同一条命令、同一页，两份草稿 —— 差别只在引文是否真在原文里</div>"
                    f"<div class='two'>"
                    f"<div>{term([('K verify draft_good.md', pass_out, 'ok')])}</div>"
                    f"<div>{term([('K verify draft_bad.md', fail_out, 'bad')])}</div></div>")
            css = TERM_CSS + ".two { display:flex; gap:18px; align-items:flex-start; } .two > div { flex:1; }"
            render(page(body, extra_css=css), out / "04-闸门对比.png")

    if not args.only or args.only == "05":
        fig_flow(out / "05-流程与闸门.png")

    if (not args.only or args.only == "06") and args.page_png and args.unit_md:
        import re as _re
        unit_md = Path(args.unit_md)
        # ① 原 PDF 这一页抽不出字（页图）
        raw = subprocess.run(["pdftotext", "-f", "1", "-l", "1", str(pdf), "-"],
                             capture_output=True, text=True).stdout
        n_pdf = len(_re.findall("三角贸易", raw))
        # ② md 里能搜到
        n_md = len(_re.findall("三角贸易", unit_md.read_text(encoding="utf-8")))
        # ③ 检索能给出页码
        kb_root = unit_md.parent.parent
        srch = subprocess.run([py, "skills/ask-kb/scripts/kb.py", "--kb", str(kb_root),
                               "search", "三角贸易", "--top", "1"],
                              capture_output=True, text=True, cwd=REPO).stdout.strip()
        first = scrub(srch.splitlines()[0]) if srch else "（无结果）"
        proof = [
            ('pdftotext -f 1 -l 1 历史_九上.pdf - | grep -c "三角贸易"',
             f'{n_pdf}          ← 页图没有文字层，一个字都抽不出来'),
            ('grep -c "三角贸易" kb/units/历史九上-三角贸易.md',
             f'{n_md}          ← OCR 之后能搜到 {n_md} 处'),
            ('python3 kb.py search "三角贸易" --top 1',
             f'{first}   ← 还带页码，引用能落地'),
        ]
        fig_compare(Path(args.page_png), unit_md,
                    out / "06-课本页对照md.png", args.page_label, proof=proof)

    shutil.rmtree(work, ignore_errors=True)
    print(f"\n✅ 素材已生成到 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
