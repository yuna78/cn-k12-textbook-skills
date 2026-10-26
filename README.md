# cn-k12-textbook-skills

> **中国中小学教材 · AI 技能系列**（Agent Skills for Chinese K-12 textbooks）
>
> 三个可组合的 skill：把教材 PDF 搬下来 → 变成能引用的知识 → 只回答有出处的问答。
>
> **PDF in, cited answers out.**
>
> 适用环境：**Claude Code / Codex / WorkBuddy** 等能跑 bash 的 agent；也可纯命令行使用。

适用：人教版 / 沪教版 / 统编版 / 沪科技版，五·四学制与六·三学制均可；
语数英理化史道法都行 —— 只要是 PDF 教材或老师发的资料，不绑定特定出版社与平台。

```
┌─────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  fetch-pdf  │ ──> │    pdf-to-kb     │ ──> │     ask-kb       │
│  拿到 PDF    │     │ 单元md+页码+图索引 │     │ 检索 + 三道闸门    │
└─────────────┘     └──────────────────┘     └──────────────────┘
```

## 为什么做这个

把一本 300 页的教材丢给 AI，通常会发生三件事：

1. **下载不了** —— 很多教育/机构网站有阅读器但没有下载按钮，链接一探就 401。
2. **读不动** —— 整本塞进上下文不现实；OCR 出来的文本又没有页码，问到细节只能含糊其辞。
3. **会编** —— 普通 RAG 检索到相邻内容后用常识"补全"，答得很像，但经不起对照原文。

这三个 skill 分别对付一件事，组合起来就是：**让 AI 能翻完整本书，并且说的每句话都能翻到那一页。**

## 快速开始

```bash
# 依赖：python3 + poppler（pdftotext/pdftoppm/pdfimages/pdfinfo）
# macOS: brew install poppler          Ubuntu: apt install poppler-utils
# 扫描件另需 OCR：brew install tesseract tesseract-lang（或用 --ocr-cmd 接自己的）

git clone <this-repo> cn-k12-textbook-skills && cd cn-k12-textbook-skills

# ① 下载（公开直链；需要登录的资源见 skills/fetch-pdf）
python3 skills/fetch-pdf/scripts/fetch.py "https://example.com/book.pdf" -o books/book.pdf --resume

# ② 逐页抽文本 → 切单元 → 抽图 → 建索引
python3 skills/pdf-to-kb/scripts/extract.py books/book.pdf --out work/
python3 skills/pdf-to-kb/scripts/segment.py --work work/ \
        --manifest examples/manifest.example.json --out kb/
python3 skills/pdf-to-kb/scripts/figures.py --pdf books/book.pdf --work work/ --kb kb/
python3 skills/pdf-to-kb/scripts/build.py --kb kb/ --verify

# ③ 问问题（带出处；交付前过闸门）
K="python3 skills/ask-kb/scripts/kb.py --kb kb"
$K judge  "水的净化有哪几种方法"
$K search "净化 过滤 蒸馏" --top 6
$K verify drafts/answer.md --question "水的净化有哪几种方法"
```

## 三个 skill

| Skill | 解决什么 | 关键设计 |
|---|---|---|
| [`fetch-pdf`](skills/fetch-pdf/SKILL.md) | 页面没有下载按钮、下下来只有半截、需要登录态 | 元数据解析 → 直链探测（200/206/401）→ 断点续传 + 头尾校验；登录态资源**在页面内**取数，凭证永不出浏览器 |
| [`pdf-to-kb`](skills/pdf-to-kb/SKILL.md) | 几百页 PDF 不能检索、引用不到页 | 逐页抽取（文字层优先、扫描件 OCR）→ 按章切带 frontmatter 的 md → 抽图并对图号图题 → 建 chunk 索引 |
| [`ask-kb`](skills/ask-kb/SKILL.md) | 答得像但会编、找不到出处 | 零依赖 BM25（中文二元组）→ `judge` 判能不能答 → `verify` 硬校验每条引文是否真在原文 |

### fetch-pdf：三条路，先探测再动手

```
直链 200/206 ──────────────────────> fetch.py（断点续传 + %PDF/%%EOF/字节数校验）
页面无下载按钮 ──> resolve 元数据 ──> 真实 URL ──┬── 401/403 ──> 页面内 fetch（MAC/Bearer 签名）
                                              └── 200/206 ──> fetch.py
```

已适配：国家中小学智慧教育平台（教材，元数据在公开镜像、文件在私有 CDN）。
其它站点只需替换两个函数（`readToken()` / `authHeader()`），见
[references/sources.md](skills/fetch-pdf/references/sources.md)。

### pdf-to-kb：页码是第一优先级

每页单独抽、每页留标记，单元 md 里长这样：

```markdown
---
id: 化学九上-第四单元
source_file: books/化学九上.pdf
pdf_pages: 88-113        # PDF 物理页
source_pages: 78-103     # 书上印刷页
ocr_quality: high
---

<!-- p.78 (pdf 88) -->
（该页正文）
```

`<!-- p.N -->` 不是装饰 —— `verify` 靠它把「页」落成具体一段文字。
没有页码的知识库，等于没有出处。

### ask-kb：三道闸门，宁可拒答

| judge | 允许怎么答 |
|---|---|
| 可靠 | 正常答 + 每条 `[引] unit @p页 :: 原文` + verify 通过 |
| 存疑 | 列候选、逐条给页码，不许下单一结论 |
| 无 | 只能说"本地资料没有"；给常识必须标 `⚠️ 非本地资料` |

引文必须**逐字**是原文子串（去空白标点后比对）；**判可靠但 verify 不过 → 以 verify 为准拒答**。

## 实测

在作者自己的项目上（一套初中教材 + 老师资料的知识库）：

| 指标 | 结果 |
|---|---|
| 知识库规模 | 609 个单元 / 563 条图索引 |
| Recall@1（问到对的地方） | **97.4%** |
| 误拒率（库里有却判无） | **0%** |
| 拒答准确率（库里没有时老实说没有） | **100%** |
| 引用闸门（引文能落到原文） | **12/12** |

本仓库自带评估脚本，换你自己的库可以直接跑：

```bash
python3 skills/ask-kb/scripts/eval.py --kb kb/ --set examples/eval.example.json --verbose
```

## 设计取舍（为什么不用向量库）

| 选择 | 理由 |
|---|---|
| **BM25 + 中文二元组** | 教材/资料的问题关键词明确（"图12-4-1""赤壁之战"），几千 chunk 规模下 BM25 又快又准；零依赖、离线、可解释、能定位到页。需要语义检索时 `chunks.jsonl` 就是干净的接入点 |
| **一页一文件** | 页码是引用的最小单位；整本抽取后再定位页码要重做，代价更高 |
| **图单独索引** | 图里的字 OCR 基本抓不到，但"图号 → 页 → 图片"能做到，就能把原图给用户而不是猜 |
| **闸门而不是提示词** | "请不要编造"写在 prompt 里不可验证；`verify` 是程序，给了 exit code |

## 目录结构

```
skills/
  fetch-pdf/     SKILL.md + fetch.py + resolve_smartedu.py + fetch_in_page.js
  pdf-to-kb/     SKILL.md + extract.py + segment.py + figures.py + build.py
  ask-kb/        SKILL.md + kb.py + eval.py
examples/        manifest / eval / draft 三个样例
docs/            architecture.md（数据流与失败模式）、公众号稿.md
```

## 怎么装进你的 Agent

脚本是**纯 CLI**，任何能跑 bash 的 agent 都能用；`SKILL.md` 只是给 agent 看的说明书。
三种接法按你的环境选：

| 环境 | 接法 |
|---|---|
| **Claude Code** | 放进 `~/.claude/skills/`（用户级）或项目 `.claude/skills/`，自动识别 `SKILL.md` |
| **Codex**（OpenAI Codex CLI） | 把 `SKILL.md` 里"命令怎么跑"那段写进仓库根的 `AGENTS.md`，或作为自定义指令加载；脚本照常 `python3` 调用 |
| **WorkBuddy** | WorkBuddy 的技能就是 `<skill-name>/SKILL.md` 结构（[官方说明](https://workbuddy.homes/bluebook/)），与本仓库一致——把 `skills/` 下三个目录整个拷进它的技能目录即可 |
| 其它 agent / 自己用 | 直接当命令行工具，三条命令串成流水线 |

```bash
git clone <repo> cn-k12-textbook-skills && cd cn-k12-textbook-skills

cp -r skills/* ~/.claude/skills/            # Claude Code 用户级
cp -r skills/* <your-project>/.claude/skills/   # Claude Code 项目级
cp -r skills/* <your-project>/.agents/skills/   # 支持 .agents/skills 约定的 agent
```

> 三个 skill 都是 `<skill-name>/SKILL.md + scripts/` 的标准结构，Claude Code 与 WorkBuddy
> 都按这个约定加载，无需改任何文件；Codex 只吃 `AGENTS.md`，把命令段落搬过去就行。
>
> 不装 skill 也能用：`SKILL.md` 里的命令原样复制到终端即可。

## 局限（先说清楚）

- **OCR 质量决定上限**：`ocr_quality: low` 的单元，引用时必须提示"OCR 可能有误，请对纸质书"
- **不保留版式**：表格/公式只抽文本，复杂版面会退化
- **平台接口是逆向的**：smartedu 的私有 CDN 签名方案随时可能变；只应下载你有权访问的内容
- **闸门不是万能**：它保证"引文真实存在于原文"，不保证"原文本身是对的"（错题答案、OCR 误字仍需人工判断）

## 边界与合规

- 只处理**你自己提供、且你有权访问**的文件
- 不绕过付费墙、不破解 DRM、不批量爬站
- **不随仓库分发任何受版权保护的原文**（教材、讲义等请自行获取，勿提交）
- 下载后的内容仅供个人学习使用

## License

MIT（见 [LICENSE](LICENSE)）。脚本可自由用于商业项目；**你的数据与下载内容不在授权范围内**。

## 公众号/文章

见 [docs/公众号稿.md](docs/公众号稿.md)（附发布前检查清单）。
