# 公众号配图素材包

全部由 [`make_material.py`](make_material.py) **跑真实流水线后截图生成**，不是画的示意图。
改了 skill 之后重跑一次，图会跟着更新，不会出现"图里是旧版行为"。

| 图 | 内容 | 建议放文章位置 |
|---|---|---|
| [`01-教材目录.png`](01-教材目录.png) | 真实下载下来的 11 册教材清单（页数/大小/版本） | 「我做了什么」之后，证明"真跑通了" |
| [`02-知识单元.png`](02-知识单元.png) | 一个真实的知识单元 md（frontmatter + 页码锚点高亮） | 「pdf-to-kb」那节，配文"页码就是出处的根" |
| [`03-检索与引用.png`](03-检索与引用.png) | 真实 judge / search / cite 输出 | 「ask-kb」那节，说明三个命令各拿什么 |
| [`04-闸门对比.png`](04-闸门对比.png) | 真实 verify：正确引用通过 vs 编造引用被拦下 | **全文最有说服力的一张**，建议放大 |
| [`05-流程与闸门.png`](05-流程与闸门.png) | 三个 skill + 三道闸门结构图 | 开头或结尾做总览 |

尺寸均为 1240 逻辑像素宽 @2x（实际 2480px），公众号正文宽度够用，点开也清晰。

## 怎么复现

```bash
# 依赖：Chrome（headless 截图）+ Pillow + poppler
pip install pillow

# 用一本有文字层的 PDF 生成 02/03/04，用清单生成 01
python3 docs/material/make_material.py \
        --pdf books/历史_九年级_节选.pdf \
        --inventory docs/material/inventory.example.json

# 只重做某一张
python3 docs/material/make_material.py --pdf books/x.pdf --only 04
```

脚本做的事都是真跑：

1. 复制 PDF 到临时目录并**改名**（截图里不出现本机真实路径）
2. `extract → segment → build` 建一个小知识库
3. 调 `kb.py judge / search / cite / verify`，把**真实 stdout** 塞进 HTML
4. Chrome headless 截图 → Pillow 裁掉底部空白

## 脱敏

素材要公开发，所以脚本里有一道 `scrub()`：

- 所有 `/Users/…`、临时目录、任意 `…/books/xxx.pdf` 统一洗成 `books/历史_九年级_节选.pdf`
- 单元标题用页面首行（真实内容），不写本机目录名
- `cite` 输出只取 `source_file` / `source_pages` / `abs_path` 三个字段，不整段贴 JSON

发之前建议再肉眼过一遍每张图，确认没有出现真实姓名、学校、目录名。

## 不出现在素材里的东西

- 任何教材原文 PDF（版权）
- 孩子的成绩、作业、姓名、学校班级
- 本机用户名与绝对路径
