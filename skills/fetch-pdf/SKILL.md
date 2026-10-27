---
name: fetch-pdf
description: >-
  把一个 PDF 链接（[国家中小学智慧教育平台](https://basic.smartedu.cn) 教材页、机构资料站、
  任何直链）稳定地存到本地。当用户给一个 PDF 链接说「帮我下载这个 / 存下来 / 教材在哪下」，
  或抱怨「点下载没反应 / 下下来打不开 / 只有半截」时使用。三条路径按需选：
  公开直链用 curl 式下载器（断点续传 + 头尾校验），要登录态的资源在**页面内**取数
  （凭证不出浏览器），平台隐藏直链则先解析公开元数据拿到真实 URL。
---

# fetch-pdf — 把 PDF 稳稳地搬到本地

## 先判断走哪条路

| 现象 | 用哪条 |
|---|---|
| 直链 `curl -I` 就是 200/206 | **A. 直链下载**（`fetch.py`） |
| 页面没有下载按钮，但你知道 contentId / 详情页 URL | **B. 先解析真实 URL，再回到 A** |
| 真实 URL 一探就是 401/403 | **C. 页面内取数**（登录态资源，凭证不导出） |

**判断 401 的最省事办法**：解析出 URL 后先探 1KB——

```bash
curl -s -m 20 -r 0-99 -o /dev/null -w "%{http_code} %{content_type}\n" "<url>"
# 200/206 → 公开，走 A；401 → 需要登录，走 C；403 → 域名/镜像用错了
```

## A. 直链下载（最常用）

```bash
python3 scripts/fetch.py "<url>" -o books/xxx.pdf --resume
# 已知大小（比如元数据里的 ti_size）时加 --expect-size，字节不符会重试而不是留个坏文件
python3 scripts/fetch.py "<url>" -o books/xxx.pdf --expect-size 123456789 --resume
```

它做三件很多人会忘的事：

1. **断点续传**：中断后重跑同一条命令，从 `.part` 接着下（实测几百 MB 的教材很有用）
2. **重试 + 退避**
3. **下完就验**：`%PDF-` 头 + 尾部 `%%EOF` + 字节数；不通过就删残片重试，绝不把半截文件交出去

退出码：`0` 成功｜`3` 下载失败（含 401/403）｜`4` 校验不通过。

## B. 先解析真实 URL（以国家中小学智慧教育平台为例）

平台教材详情页常常**没有下载按钮**，但元数据挂在公开镜像上：

```bash
python3 scripts/resolve_smartedu.py "<详情页URL>" --probe
```

输出 JSON：`title` / `suggested_filename` / `pdfs[]`（每个条目有 `url`、`host`、
`decoded_path`、`size_bytes`，通常 3 个 `r1/r2/r3` 镜像；`--probe` 会附上
`{status, need_login}`）。

拿到 `status: 200/206` 就回 A 下载；`401` 走 C。

> 元数据结构（跨资源类型稳定）：
> `https://s-file-{1,2,3}.ykt.cbern.com.cn/zxx/ndrv2/resources/<type>/details/<contentId>.json`
> → `ti_items[]` 里 `ti_format == "pdf"` 那条 → `ti_storages`（镜像 URL 列表）+ `ti_size`。
> 资源类型不在默认列表时，从页面网络请求里找 `/resources/<TYPE>/details/`，用 `--type <TYPE>`。

## C. 登录态受限资源：在页面内取数

**前提**：用户**自己**已经在浏览器里登录该站；你有办法在该站页面里执行 JS
（Playwright/Puppeteer、浏览器扩展、CDP 都行）。本 skill 不替用户登录、不碰密码/验证码、
不把凭证导出到模型或磁盘。

```bash
# 1) 把 scripts/fetch_in_page.js 整个注入到该站页面
# 2) 探一下（1KB）
await window.pfProbe("<真实URL>")      // → {"status":206,"ctype":"application/pdf"}
# 3) 开始下载（异步，立即返回）
window.pfDownload("<真实URL>", "书名.pdf")
# 4) 轮询进度（每次调用别超过 40s，长下载会被工具层打断，但页面里的下载继续跑）
JSON.stringify(window.__dl)            // {status, received, total, done}
```

`scripts/fetch_in_page.js` 里**通用的是**：流式 fetch + 进度上报 + `Blob`/`<a download>` 保存。
**站点相关的是**两个函数：`readToken()`（凭证从哪读）和 `authHeader()`（怎么签名）。
换成别的站点时只改这两个，注释里标了边界。

平台这套是 HTTP MAC（RFC 草案变体）：

```
normalized = nonce \n GET \n <encodeURI(path)> \n <host> \n <port> \n <ext=""> \n
mac        = base64(HMAC-SHA256(mac_key, normalized))
header     = MAC id="<access_token>",nonce="<nonce>",mac="<mac>"
nonce      = "<unix秒>:<8位随机>"
```

**签名覆盖的 path 必须与真正请求的 path 完全一致**（两边都用 `encodeURI`，
不要一边 `encodeURIComponent`），否则一定 401——这是最常见的坑。

## D. 落地与核对（无论走哪条路）

```bash
pdfinfo out.pdf | head -5          # 页数、页面尺寸
ls -l out.pdf                      # 与元数据里的 size_bytes 对齐
```

报告给用户时给：**文件路径 + 大小 + 页数 + 是否需要登录**。别只说"下好了"。

## 常见坑

| 症状 | 原因 / 处理 |
|---|---|
| 401 | 凭证过期：让用户**在该站刷新页面**（会重写 localStorage），再重新注入 |
| 403 | 用了非私有镜像，或把 `-private` 域名去掉了；用解析器给的原样 host |
| 下到 100% 但打不开 | 半截文件：一定走 `fetch.py` 的头尾校验；`--resume` 要配合校验用 |
| `~/.pdf.part` 残留 | 正常，续传会用到；确认成功后 `fetch.py` 会自动改名 |
| 下载到一半工具调用超时 | 页面内下载不会停，继续短轮询即可；不要用单个 >40s 的等待 |
| 中文文件名成乱码 | 保存前把 `/ \\ : * ? " < > |` 替换掉；别用 URL 里的原始名 |

## 边界（请写进 README）

- 只处理**用户自己提供的链接**、且用户已有权访问的内容
- 不绕过付费墙 / 不批量爬取 / 不公开分发受版权保护的原文
- 平台接口是逆向所得的**未公开接口**，随时可能变更

## 参考资料

- [references/sources.md](references/sources.md) —— 平台元数据入口、直链/签名路径、失败对照表、
  其它站点的替换点（`readToken()` / `authHeader()`）。
