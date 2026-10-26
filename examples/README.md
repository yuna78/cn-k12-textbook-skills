# examples/

跑得通的最小样例。**不需要真实教材**，按下面顺序跑一遍就能看清整条流水线。

| 文件 | 是什么 | 谁读它 |
|---|---|---|
| [`manifest.example.json`](manifest.example.json) | 书的元信息 + 章节页范围（PDF 物理页） | `segment.py --manifest` |
| [`eval.example.json`](eval.example.json) | 评估集：问题 → 应该命中的单元 / 应该拒答 | `ask-kb/scripts/eval.py --set` |
| [`draft.example.md`](draft.example.md) | 一份带 `[引]` 的答案草稿（正例 + 反例都在里面） | `ask-kb/scripts/kb.py verify` |
| [`vision_ocr.swift`](vision_ocr.swift) | macOS 系统 Vision 的 50 行 CLI 包装（扫描件 OCR 用） | `extract.py --ocr-cmd` |

## 一条命令跑完全流程

前提：已装 poppler（`pdftotext`/`pdftoppm`/`pdfinfo`），以及一本你有权访问的教材 PDF。

```bash
# ① 抽文字（有文字层；没有就加 OCR，见 README「跨平台」）
python3 skills/pdf-to-kb/scripts/extract.py books/化学九上.pdf --out work/化学

# ② 按章切分（页码用 examples/manifest.example.json 里声明的物理页范围）
python3 skills/pdf-to-kb/scripts/segment.py --work work/化学 \
        --manifest examples/manifest.example.json --out kb

# ③ 抽图 + 建索引
python3 skills/pdf-to-kb/scripts/figures.py --kb kb --pdf books/化学九上.pdf
python3 skills/pdf-to-kb/scripts/build.py --kb kb

# ④ 判能不能答 → 检索 → 拿全文
python3 skills/ask-kb/scripts/kb.py --kb kb judge "水的净化有哪几步？"
python3 skills/ask-kb/scripts/kb.py --kb kb search "水的净化 步骤" --top 6
python3 skills/ask-kb/scripts/kb.py --kb kb get "化学九上-第四单元"

# ⑤ 写答案草稿（照 draft.example.md 的格式）→ 交付前硬校验
python3 skills/ask-kb/scripts/kb.py --kb kb verify examples/draft.example.md \
        --question "水的净化有哪几步？"

# ⑥ 回归：检索质量 + 引用闸门
python3 skills/ask-kb/scripts/eval.py --kb kb --set examples/eval.example.json
```

## 三个 json 的字段含义

- `manifest.example.json`：`book`（id/title/subject/publisher/pdf/printed_offset）+ `units[]`
  （id/title/chapter/**pdf_pages 物理页数组**/tags）。`printed_offset` 决定引用里的「书上页码」——
  `printed = pdf_page - printed_offset`，**错了所有页码都错**，见 `skills/pdf-to-kb/references/schema.md`。
- `eval.example.json`：每条 `{question, expect_unit, expect_refuse}`。跑 `eval.py` 会输出
  Recall@1、误拒率、拒答准确率、引用闸门通过率。
- `draft.example.md`：正确引用的写法和几种会被拦下的写法（编造数字、引文改写、页码错），
  拿来演示 `verify` 的 exit code 语义。

## vision_ocr.swift

macOS 专有：Vision 是系统框架、没有命令行工具，所以这里包了一层。编译用法见 README 的
[「macOS：用系统自带的 Vision 做 OCR」](../README.md#macos用系统自带的-vision-做-ocr推荐不用装任何东西)。
