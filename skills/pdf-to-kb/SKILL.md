---
name: pdf-to-kb
description: >-
  把一本 PDF（教材/讲义/资料汇编）拆成**可引用、可检索的知识单元**：逐页抽取文本
  （有文字层直取，纯扫描件走 OCR）、按章节切成带 YAML frontmatter 的 markdown、
  抽出插图并和图号图题对上、最后生成检索用 chunk 索引。当用户说「把这本书变成知识库 /
  拆成 md / 做成可搜索的资料 / 整理进知识库」，或让 AI 读一本几百页的教材 PDF 时使用。
  产出可以直接喂给 ask-kb 做带出处问答。
---

# pdf-to-kb — 把 PDF 变成「能引用的知识」

## 一句话

PDF 是给人翻的，知识库是给**检索和引用**用的。这个 skill 只干一件事：
把「一本 PDF」变成 `单元 md + 页级页码 + 图索引 + 检索 chunk`，让下游能回答
「这句话出自哪一页」而不是「我记得书里好像说过」。

## 四步流水线

```bash
# 0) 准备要点：装 poppler（pdftotext / pdftoppm / pdfimages / pdfinfo）
#    macOS: brew install poppler    Debian/Ubuntu: apt install poppler-utils

# 1) 逐页抽文本（有文字层就走文字层；扫描件自动 OCR）
python3 scripts/extract.py book.pdf --out work/ --ocr auto
#   书上页码 ≠ PDF 页码时：--printed-offset 10   （PDF第11页 = 书上第1页）

# 2) 切知识单元（推荐用清单，见 examples/manifest.example.json）
python3 scripts/segment.py --work work/ --manifest book.manifest.json --out kb/
#   没有目录、想让标题自动切：
python3 scripts/segment.py --work work/ --auto "^第[一二三四五六七八九十百]+[章节]" --out kb/

# 3) 抽图 + 图题（扫描件加 --render-scan，整页当图存）
python3 scripts/figures.py --pdf book.pdf --work work/ --kb kb/

# 4) 建检索索引（chunk + 统计 + frontmatter 自检）
python3 scripts/build.py --kb kb/ --verify
```

做完就能直接：

```bash
python3 ../ask-kb/scripts/kb.py --kb kb/ search "光合作用的场所"
```

## 三个必须做对的点

### 1. 页码映射（不然后面引用全是错的）

- `pdf_page` = PDF 物理页（`pdftotext -f N` 的 N）
- `printed_page` = 书上印的页码
- 两者差一个固定偏移 → `--printed-offset`；差得不规律（有插页、缺页）→ 在 manifest 里
  按单元给 `pdf_pages`，并在 `book.printed_offset` 里给基准。

索引里两个都存，下游引用时同时给，用户才能对上纸质书。

### 2. 扫描件必须 OCR，而且结果要标质量

教材 PDF 有两种：带文字层的（轻、可复制）和**页图拼装的**（重、只能看图）。
后者 `pdftotext` 出来是空的，`extract.py` 会自动对空页 OCR：

```bash
--ocr auto              # 有文字层用文字层，空页 OCR（默认）
--ocr tesseract --ocr-lang chi_sim+eng          # 用 tesseract（Win/Linux 常见）
--ocr-cmd "tesseract {img} {out} -l chi_sim"    # 同上，显式给命令
--ocr-cmd "myocr {img} {out}"                   # 任何写 <out>.txt 的引擎
```

`--ocr-cmd` 模板里**没有 shell 元字符时直接调用二进制**，所以同一条命令在
macOS / Windows(cmd, PowerShell) / Linux 下行为一致；有重定向时才切到平台 shell。

macOS 上推荐用**系统自带的 Vision**（不用装软件、离线、中文最准）：仓库带了一个 50 行的
Swift 包装 `examples/vision_ocr.swift`，`swiftc -O -o bin/vision_ocr examples/vision_ocr.swift`
编译后 `--ocr-cmd "bin/vision_ocr {img} > {out}"` 即可。Windows / Linux 没有 Vision，用 tesseract。

单元 frontmatter 里的 `ocr_quality` 是三档：`high`（纯文字层）/ `medium`（半数以下 OCR）/
`low`（大半靠 OCR）。**`low` 的单元回答时要标 ⚠️ 待核对**——OCR 会认错字，
不能当原文铁证。

### 3. 图要单独索引，不能指望 OCR

图里的字（地图地名、示意图标注、表格数字）OCR 基本抓不到。所以单独抽：
`pdfimages -list` 拿到每页内嵌位图 → 抽成 PNG → 用正则把 `图12-4-1` 这类图题和页对上 →
`figures.json` 里记 `{fig_id, caption, pdf_page, printed_page, unit_id, file}`。

问答时就能：`kb.py fig "图12-4-1"` → 定位到页 → `kb.py page <unit> --page 89` 渲染原图给用户，
**而不是让模型凭图号猜图的内容**。

## 产物结构

```
kb/
├── units/
│   ├── 人教版九上-第一单元.md      # frontmatter + <!-- p.N --> 分页标记 + 正文
│   └── ...
├── figures/
│   └── 图12-4-1-1.png
└── _index/
    ├── units.json      # 单元清单（id/title/chapter/pdf_pages/source_pages/chars）
    ├── chunks.jsonl    # 检索用 chunk（unit_id + page + text）
    ├── figures.json    # 图索引
    └── stats.json      # 规模统计 + frontmatter 自检结果
work/                   # 中间产物，可删可重建
├── _text/p0001.txt ...
├── pages.json
└── meta.json
```

单元 md 长这样（`<!-- p.N -->` 是给 verify 定位引文用的，别删）：

```markdown
---
id: 人教版九上-第四单元
title: 第四单元 自然界的水
subject: 化学
book: 化学 九年级上册
chapter: 第四单元
source_file: books/化学九上.pdf
pdf_pages: 88-113
source_pages: 78-103
tags: [水, 净化, 化学式]
ocr_quality: high
updated: 2026-10-26
---

# 第四单元 自然界的水

<!-- p.78 (pdf 88) -->
...
```

## 常见坑

| 症状 | 原因 / 处理 |
|---|---|
| 整本 `pdftotext` 出来是空的 | 页图拼装 PDF，没文字层 → 必须 OCR（`--ocr auto` 会自动做） |
| chunk 检索总差一页 | `--printed-offset` 没给或给反了（是"书上页 = PDF页 − offset"） |
| 单元字数明显偏少 | 该单元首页是章节扉页（只有标题），正常；但如果整章都少，检查 manifest 页范围 |
| 图抽出来一堆小图标 | 用 `--min-px 250000` 提高面积阈值 |
| frontmatter 缺字段 | `build.py --verify` 会列出来（必填：id/title/source_file/pdf_pages，exit 3） |
| 一章几百页、chunk 太大 | `--chunk-chars 300` 调小 |

## 什么时候**不要**用这个 skill

- 只有几页、一次就能读完的资料：直接 `pdftotext` 读，不必建库
- 纯图片扫描件又没有可靠 OCR：宁可先人工核对，别把 `low` 质量的 OCR 当知识库
- 需要精确版面还原（表格、公式排版）：本 skill 只抽文本，不保留版式
