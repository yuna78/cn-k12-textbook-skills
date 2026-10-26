#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""resolve_smartedu.py — 把国家中小学智慧教育平台（国家中小学智慧教育平台 / smartedu）
的资源页面解析成**真实 PDF 直链**（不需要登录）。

原理：平台上「教材详情页」没有下载按钮，但资源元数据挂在**公开镜像**上：

    https://s-file-{1,2,3}.ykt.cbern.com.cn/zxx/ndrv2/resources/<type>/details/<contentId>.json

JSON 里 `ti_items[]` 的每一项带 `ti_format` / `ti_size` / `ti_storages`（多个镜像 URL）。
挑 `ti_format == "pdf"` 的那项，就拿到 3 个 r1/r2/r3 镜像直链。文件本身在
`r{1,2,3}-ndr-private.ykt.cbern.com.cn`（**私有 CDN，可能要登录态**）——
是否需要登录，用 --probe 一试便知：200/206 公开、401 需要登录。

用法：
    python3 resolve_smartedu.py "<详情页URL>"
    python3 resolve_smartedu.py "<contentId>" --probe
    python3 resolve_smartedu.py "<URL>" --type assets_document
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
# 已知的资源类型目录；找不到时会提示你从页面网络请求里发现正确的那一个
DETAIL_TYPES = ["tch_material", "assets_document", "elite_lesson", "syncClassroom"]
MIRRORS = ["s-file-1", "s-file-2", "s-file-3"]


def extract_content_id(s: str) -> str:
    m = re.search(r"contentId=([0-9a-fA-F-]{36})", s)
    if m:
        return m.group(1)
    m = re.search(r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                  r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b", s)
    if m:
        return m.group(1)
    raise SystemExit(f"在给的内容里找不到 contentId：{s!r}")


def fetch_json(url: str, timeout: int = 25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            return None
        return json.loads(r.read().decode("utf-8"))


def find_metadata(cid: str, preferred: str | None):
    types = ([preferred] if preferred else []) + [t for t in DETAIL_TYPES if t != preferred]
    for t in types:
        for m in MIRRORS:
            url = f"https://{m}.ykt.cbern.com.cn/zxx/ndrv2/resources/{t}/details/{cid}.json"
            try:
                data = fetch_json(url)
            except Exception:
                data = None
            if data:
                return t, url, data
    return None, None, None


def probe(url: str, timeout: int = 20) -> tuple[int, str]:
    """只取前 1KB，判断公开/需要登录。"""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Range": "bytes=0-1023"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else ""
    except Exception as e:  # 网络问题
        return 0, str(e)[:120]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="详情页 URL 或裸 contentId")
    ap.add_argument("--type", default=None, help="资源类型目录，如 tch_material")
    ap.add_argument("--probe", action="store_true", help="顺手探测是否需要登录")
    args = ap.parse_args()

    cid = extract_content_id(args.target)
    rtype, meta_url, data = find_metadata(cid, args.type)
    if not data:
        print(f"没找到 contentId={cid} 的元数据。\n"
              f"可能资源类型不在默认列表 {DETAIL_TYPES} 里。\n"
              f"做法：打开详情页 → 看网络请求里 "
              f"`/zxx/ndrv2/resources/<TYPE>/details/<id>.json` 的 <TYPE>，"
              f"然后用 --type <TYPE> 重试。", file=sys.stderr)
        return 3

    title = data.get("title") or (data.get("global_title") or {}).get("zh-CN") or cid
    pdfs = []
    for it in data.get("ti_items", []):
        if (it.get("ti_format") or "").lower() != "pdf":
            continue
        for storage in it.get("ti_storages", []):
            path = re.sub(r"^https?://[^/]+", "", storage)
            host = re.sub(r"^https?://([^/]+).*$", r"\1", storage)
            entry = {
                "url": storage,
                "host": host,
                "decoded_path": urllib.parse.unquote(path),
                "size_bytes": it.get("ti_size"),
            }
            if args.probe:
                code, ctype = probe(storage)
                entry["probe"] = {"status": code, "content_type": ctype,
                                  "need_login": code in (401, 403)}
            pdfs.append(entry)

    if not pdfs:
        print(f"找到元数据（类型 {rtype}）但没有 PDF 条目。标题：{title!r}", file=sys.stderr)
        return 3

    out = {
        "content_id": cid,
        "resource_type": rtype,
        "metadata_url": meta_url,
        "title": title,
        "pdf_count": len(pdfs),
        "pdfs": pdfs,
        "suggested_filename": re.sub(r"[/:\\\\\\n\\r]", "_", title).strip() + ".pdf",
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
