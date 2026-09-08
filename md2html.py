"""把 docs/technical-doc.md 转成带样式的 HTML（供 Edge headless 打印 PDF）。

用极简 markdown 子集解析(标题/表格/代码块/列表/粗体/分隔线), 不引第三方库。
CSS: 中文字体、表格边框、代码块底色、页码。
"""

from __future__ import annotations

import html
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent / "docs" / "technical-doc.md"
OUT = Path(__file__).resolve().parent / "docs" / "technical-doc.html"

CSS = """
@page { size: A4; margin: 16mm 14mm; }
body { font-family: "Microsoft YaHei", "SimHei", sans-serif; font-size: 12px; line-height: 1.65; color: #1f2430; }
h1 { font-size: 24px; border-bottom: 3px solid #0b57d0; padding-bottom: 6px; color: #0b57d0; }
h2 { font-size: 18px; border-bottom: 1.5px solid #b9c6dd; padding-bottom: 4px; margin-top: 26px; color: #123; }
h3 { font-size: 15px; margin-top: 18px; color: #234; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; page-break-inside: avoid; }
th, td { border: 1px solid #c4cbd6; padding: 5px 8px; text-align: left; font-size: 11px; }
th { background: #eef2f8; }
pre { background: #f6f8fa; border: 1px solid #e1e5ea; border-radius: 6px; padding: 10px; font-size: 10.5px;
      font-family: Consolas, "Courier New", monospace; overflow-wrap: break-word; white-space: pre-wrap; }
code { font-family: Consolas, "Courier New", monospace; font-size: 10.5px; background: #f2f4f7; padding: 1px 4px; border-radius: 3px; }
pre code { background: none; padding: 0; }
blockquote { border-left: 4px solid #c9d4e8; margin: 8px 0; padding: 4px 12px; color: #445; background: #fafbfd; }
hr { border: none; border-top: 1px solid #d5dbe4; margin: 18px 0; }
ul, ol { margin: 6px 0 6px 0; padding-left: 22px; }
li { margin: 2px 0; }
strong { color: #10305c; }
"""


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def render_table(lines: list[str], start: int) -> tuple[str, int]:
    """渲染 markdown 表格。lines 全文, start 表头行索引。返回 (html, 下一行索引)。"""
    header = [c.strip() for c in lines[start].strip().strip("|").split("|")]
    body: list[str] = []
    i = start + 2  # 跳过分隔行
    while i < len(lines) and lines[i].strip().startswith("|"):
        cells = [esc(c.strip()) for c in lines[i].strip().strip("|").split("|")]
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        i += 1
    thead = "<tr>" + "".join(f"<th>{esc(c)}</th>" for c in header) + "</tr>"
    return f"<table><thead>{thead}</thead><tbody>{''.join(body)}</tbody></table>", i


def inline(text: str) -> str:
    t = esc(text)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)
    return t


def convert(md: str) -> str:
    lines = md.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            out.append("")
            i += 1
            continue
        # 代码块
        if s.startswith("```"):
            buf = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # skip closing
            out.append("<pre><code>" + esc("\n".join(buf)) + "</code></pre>")
            continue
        # 表格
        if s.startswith("|") and i + 1 < len(lines) and re.match(r"^\|?[\s:\-|]+\|?$", lines[i + 1].strip()):
            tbl, ni = render_table(lines, i)
            out.append(tbl)
            i = ni
            continue
        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)", s)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        # 分隔线
        if re.match(r"^-{3,}$", s) or re.match(r"^\*{3,}$", s):
            out.append("<hr>")
            i += 1
            continue
        # 引用
        if s.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                buf.append(inline(lines[i].strip().lstrip(">").strip()))
                i += 1
            out.append("<blockquote>" + "<br>".join(buf) + "</blockquote>")
            continue
        # 列表
        if re.match(r"^[-*]\s+", s) or re.match(r"^\d+[.)]\s+", s):
            ordered = bool(re.match(r"^\d+[.)]\s+", s))
            items = []
            tag = "ol" if ordered else "ul"
            while i < len(lines):
                t = lines[i].strip()
                m2 = re.match(r"^(\d+[.)]|[-*])\s+(.*)", t)
                if not m2:
                    break
                items.append("<li>" + inline(m2.group(2)) + "</li>")
                i += 1
            out.append(f"<{tag}>{''.join(items)}</{tag}>")
            continue
        # 普通段落(合并连续行)
        buf = [inline(s)]
        i += 1
        while i < len(lines) and lines[i].strip() and not lines[i].strip().startswith(("#", "|", "```", ">", "-", "*")) \
                and not re.match(r"^\d+[.)]\s+", lines[i].strip()) and not re.match(r"^-{3,}$", lines[i].strip()):
            buf.append(" " + inline(lines[i].strip()))
            i += 1
        out.append(f"<p>{''.join(buf)}</p>")
    return "\n".join(out)


def main() -> None:
    md = SRC.read_text(encoding="utf-8")
    body = convert(md)
    html_doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>企业文档处理智能体 - 技术方案文档</title><style>{CSS}</style></head>
<body>{body}</body></html>"""
    OUT.write_text(html_doc, encoding="utf-8")
    print(f"HTML written: {OUT} ({len(html_doc)} chars)")


if __name__ == "__main__":
    main()
