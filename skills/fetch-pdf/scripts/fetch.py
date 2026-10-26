#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch.py — 通用 PDF 下载器（断点续传 + 完整性校验，只用标准库）。

为什么单独写一个：教材/资料站的直链经常「看起来能下、下下来是半截」，
或者中途中止后要重下几百 MB。这里做三件事：
  1. Range 断点续传（--resume，中断了重跑同一条命令即可接着下）
  2. 重试 + 退避
  3. 下完立刻验三样：`%PDF` 头、`%%EOF` 尾、字节数（可与服务器 Content-Length 比对）

用法：
    python3 fetch.py "<url>" -o out.pdf
    python3 fetch.py "<url>" -o out.pdf --expect-size 12345678 --resume
    python3 fetch.py "<url>" -o out.pdf --header "X-ND-AUTH: MAC id=...,nonce=...,mac=..."

退出码：0 成功；2 参数问题；3 下载失败；4 校验不通过
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
CHUNK = 1 << 20  # 1 MiB


def ascii_url(url: str) -> str:
    """URL 里的中文/空格必须百分号编码，否则 urllib 直接抛 ascii codec 错。"""
    import urllib.parse as up
    parts = up.urlsplit(url)
    return up.urlunsplit((parts.scheme, parts.netloc,
                          up.quote(parts.path, safe="/%"),
                          up.quote(parts.query, safe="=&%"), ""))


def human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def head_tail_ok(path: Path) -> tuple[bool, str]:
    """PDF 完整性初筛：头必须是 %PDF-，尾 64 字节里应出现 %%EOF。"""
    size = path.stat().st_size
    if size < 1024:
        return False, f"文件太小（{size} B），不像 PDF"
    with path.open("rb") as f:
        if not f.read(5).startswith(b"%PDF-"):
            return False, "文件头不是 %PDF-"
        f.seek(max(0, size - 4096))
        tail = f.read()
    if b"%%EOF" not in tail:
        return False, "文件尾找不到 %%EOF（可能下载被截断）"
    return True, "头尾正常"


def download(url: str, out: Path, headers: dict[str, str], resume: bool,
             retries: int, expect_size: int | None,
             timeout: int = 60) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    part = out.with_suffix(out.suffix + ".part")

    for attempt in range(1, retries + 1):
        have = part.stat().st_size if (resume and part.exists()) else 0
        if resume and part.exists() and have:
            print(f"  ↻ 断点续传：已有 {human(have)}，从该处继续")
        elif part.exists():
            part.unlink()

        req_headers = {"User-Agent": UA, **headers}
        if have:
            req_headers["Range"] = f"bytes={have}-"

        try:
            req = urllib.request.Request(ascii_url(url), headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = resp.status
                total = None
                cr = resp.headers.get("Content-Length")
                if cr is not None:
                    total = int(cr) + (have if status == 206 else 0)
                ctype = resp.headers.get("Content-Type", "")
                if "pdf" not in ctype.lower() and not url.lower().endswith(".pdf"):
                    print(f"  ⚠️ Content-Type 是 {ctype!r}，可能不是 PDF", file=sys.stderr)
                if status == 200 and have:
                    print("  ⚠️ 服务器不支持 Range，从头下载")
                    have = 0
                mode = "ab" if have else "wb"
                t0 = time.time()
                with part.open(mode) as f:
                    done = have
                    last = 0.0
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last > 1.0:
                            last = now
                            speed = (done - have) / max(now - t0, 1e-6)
                            if total:
                                pct = done * 100.0 / total
                                print(f"\r  ↓ {pct:5.1f}%  {human(done)}/{human(total)}  "
                                      f"{human(speed)}/s", end="", flush=True)
                            else:
                                print(f"\r  ↓ {human(done)}  {human(speed)}/s",
                                      end="", flush=True)
                print()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as e:
            code = getattr(e, "code", None)
            print(f"\n  ✗ 第 {attempt}/{retries} 次失败：{e}", file=sys.stderr)
            if code in (401, 403):
                print("    → 401/403 说明该资源需要登录态。见 references/sources.md 的"
                      "『登录态受限资源』一节（在页面内取数，不要把 token 导出来）。",
                      file=sys.stderr)
                return 3
            time.sleep(min(2 ** attempt, 20))
            continue

        ok, why = head_tail_ok(part)
        size = part.stat().st_size
        if not ok:
            print(f"  ✗ 校验失败：{why}", file=sys.stderr)
            if attempt < retries:
                part.unlink(missing_ok=True)
                print("    → 已删除残片，重试")
                continue
            return 4
        if expect_size and size != expect_size:
            print(f"  ✗ 字节数不符：得到 {size}，期望 {expect_size}", file=sys.stderr)
            if attempt < retries:
                part.unlink(missing_ok=True)
                print("    → 已删除，重试")
                continue
            return 4

        part.replace(out)
        md5 = hashlib.md5(out.read_bytes()).hexdigest() if size < 256 << 20 else ""
        print(json.dumps({
            "file": str(out), "size_bytes": size, "size_human": human(size),
            "md5": md5, "verified": True, "reason": why,
        }, ensure_ascii=False, indent=2))
        return 0

    return 3


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--expect-size", type=int, default=None)
    ap.add_argument("--resume", action="store_true", help="断点续传（推荐）")
    ap.add_argument("--header", action="append", default=[],
                    help='额外请求头，形如 "Key: Value"，可重复')
    ap.add_argument("--retries", type=int, default=3)
    args = ap.parse_args()

    headers: dict[str, str] = {}
    for h in args.header:
        if ":" not in h:
            print(f'请求头格式应为 "Key: Value"：{h!r}', file=sys.stderr)
            return 2
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()

    print(f"下载：{args.url}\n   → {args.out}")
    return download(args.url, Path(args.out), headers, args.resume,
                    args.retries, args.expect_size)


if __name__ == "__main__":
    sys.exit(main())
