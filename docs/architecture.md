# 架构与数据流

## 全链路

```
 URL / 本地 PDF
      │
      ▼
[fetch-pdf]  ── 断点续传 + 完整性校验 ──>  books/*.pdf
      │                                      │
      │                                      ▼
      │                        [extract.py] 逐页文本
      │                          work/_text/p0001.txt ...
      │                          work/pages.json  {pdf_page, printed_page, source}
      │                                      │
      │                                      ▼
      │                        [segment.py] 按章切单元
      │                          kb/units/*.md  (frontmatter + <!-- p.N --> 标记)
      │                                      │
      │                                      ├──> [figures.py]  → kb/figures/*.png
      │                                      │                   kb/_index/figures.json
      │                                      ▼
      │                        [build.py]  切 chunk
      │                          kb/_index/chunks.jsonl
      │                          kb/_index/stats.json
      │                                      │
      └─────────────── 用户提问 ─────────────┴──> [ask-kb]  kb.py judge/search/verify
```

## 三个数据契约

### 1. `pages.json`（extract → segment）

```json
[{"pdf_page": 11, "printed_page": 1, "chars": 812, "source": "text"}]
```

`source` 取值 `text` / `ocr` / `cache`。`printed_page = pdf_page − printed_offset`。

### 2. 单元 md（segment → build/ask）

frontmatter 见 `skills/pdf-to-kb/references/schema.md`；正文必须保留
`<!-- p.<书上页> (pdf <物理页>) -->` 页标记，这是 `verify` 的锚点。

### 3. `chunks.jsonl`（build → ask）

```json
{"id":"化学九上-第四单元#p79#0","unit_id":"化学九上-第四单元",
 "unit_title":"第四单元 自然界的水","chapter":"第四单元",
 "page":79,"pdf_page":89,"heading":"第四单元 自然界的水","text":"..."}
```

每行自包含（带 unit + page），所以下游可以随时换成向量库/别的检索器，不用回头改上游。

## 为什么这样切

| 决策 | 备选 | 为什么不选备选 |
|---|---|---|
| 一页一个文本文件 | 整本一个 txt | 引用要落到页；整本抽完再查页码要重做一遍 |
| 章节由清单驱动 | 纯自动识别标题 | 教材标题层级乱（图表题、栏目标题都会误判），清单一次写对长期受益 |
| chunk 带页号 | chunk 只带 unit | 没有页号就无法校验"引文是否真在这一页" |
| BM25 + 二元组 | 向量检索 | 规模小、要离线、要可解释、要零依赖；关键词型问题 BM25 更强 |
| 图片走索引 + 按需渲染 | 预生成所有页 PNG | 一本 300 页书全渲染几个 GB，且 99% 的页用不到 |

## 失败模式（设计时优先处理这几种）

| 失败 | 表现 | 兜底 |
|---|---|---|
| PDF 没有文字层 | `pdftotext` 全空 | `--ocr auto` 自动逐页 OCR，单元标 `ocr_quality` |
| 书页偏移不一致（插页/缺页） | 引用页码系统性错位 | 一本书一个偏移；不一时拆书 |
| 下载截断 | 文件能打开但尾部缺页 | `%PDF`/`%%EOF`/字节数三重校验，不通过就重下 |
| 检索命中但答非所问 | top1 是相邻小节 | `cov` 加权覆盖 + 强制 `get` 读全文 + verify |
| 引文被"顺手改写" | 答案很顺但原文没有 | verify 逐字子串比对（去空白标点后） |
| 图片内容答不了 | 被问地图/示意图标注 | 明确"图内文字 OCR 抓不到"，改为给原图 |

## 可扩展点

- **换检索器**：`chunks.jsonl` 直接喂 BM25 / ES / 向量库，`ask-kb` 只依赖这一个文件
- **换 OCR**：`extract.py --ocr-cmd "yourcmd {img} {out}"`，任何能输出 txt 的 OCR 都行
- **多书合库**：同一个 `kb/` 下不同 `book` 字段即可，`search --subject` 过滤
- **自定义来源**：`fetch-pdf` 只绑定两个函数（读凭证/签名），其它站点照 `references/sources.md` 改
