// vision_ocr.swift — 用 macOS 系统自带的 Vision 框架做中文 OCR
//
// 为什么单独写这一个文件：
//   macOS 没有现成的 OCR 命令行工具（Vision 是系统框架，不是 CLI），
//   但 Vision 的印刷体中文识别质量比 tesseract 好、速度快一倍、且完全离线。
//   所以这 50 行 Swift 就是「把系统框架包装成 CLI」，给 pdf-to-kb 的
//   `--ocr-cmd "vision_ocr {img} > {out}"` 用。
//
// 编译（需要 Xcode 命令行工具）：
//   swiftc -O -o bin/vision_ocr vision_ocr.swift
//
// 用法：
//   vision_ocr page.png > page.txt          # 单张
//   vision_ocr a.png b.png                  # 多张，用 \u{1} 分隔（可选）
//   vision_ocr -l zh-Hans,en-US page.png    # 指定语言（默认就是这两个）
//
// 输出：纯文本，按版面位置自上而下、自左而右排好行序。
// 局限：只取文字，不保留表格/分栏结构；公式与上下标会退化。
//
// 依赖：macOS 10.15+（Vision 文本识别从 10.15 起可用），无第三方依赖、不联网。

import AppKit
import Foundation
import Vision

func ocr(_ path: String, languages: [String]) -> String {
    guard let img = NSImage(contentsOfFile: path),
          let tiff = img.tiffRepresentation,
          let rep = NSBitmapImageRep(data: tiff),
          let cg = rep.cgImage else { return "" }

    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate          // .accurate 比 .fast 慢一点，但中文准得多
    req.recognitionLanguages = languages
    req.usesLanguageCorrection = true
    req.minimumTextHeight = 0.005             // 小字（页脚、图注）也抓

    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([req]) } catch { return "" }
    guard let obs = req.results else { return "" }

    // Vision 给的是「文本块 + 归一化坐标」，不是行；这里按 y 聚类成行再排序，
    // 否则中文正文会被打乱成碎片（这是直接调 Vision 最容易踩的坑）。
    struct Item { let y: CGFloat; let x: CGFloat; let s: String }
    var items: [Item] = []
    for o in obs {
        guard let c = o.topCandidates(1).first else { continue }
        let bb = o.boundingBox
        items.append(Item(y: bb.origin.y, x: bb.origin.x, s: c.string))
    }
    items.sort { a, b in
        if abs(a.y - b.y) > 0.012 { return a.y > b.y }   // 自上而下（Vision 坐标是左下原点）
        return a.x < b.x                                  // 同一行内自左而右
    }

    var lines: [String] = []
    var cur = ""
    var lastY: CGFloat = -1
    for it in items {
        if lastY < 0 || abs(it.y - lastY) <= 0.012 {
            cur += (cur.isEmpty ? "" : " ") + it.s
        } else {
            lines.append(cur)
            cur = it.s
        }
        lastY = it.y
    }
    if !cur.isEmpty { lines.append(cur) }
    return lines.joined(separator: "\n")
}

var args = Array(CommandLine.arguments.dropFirst())
var langs = ["zh-Hans", "en-US"]
if args.count >= 2, args[0] == "-l" {
    langs = args[1].split(separator: ",").map(String.init)
    args.removeFirst(2)
}
if args.isEmpty {
    FileHandle.standardError.write("usage: vision_ocr [-l zh-Hans,en-US] <image> [...]\n".data(using: .utf8)!)
    exit(1)
}
for (i, p) in args.enumerated() {
    if i > 0 { print("\u{1}") }
    print(ocr(p, languages: langs))
}
