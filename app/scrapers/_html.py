"""极简 HTML 表格抽取(stdlib,无第三方依赖)。

只做一件事:把 <table> 拆成「行 -> 单元格文本列表」。不解析 colspan/rowspan 的
列对齐——调用方通过「取每行末尾 N 个单元格」的策略来对齐转置表,足以应对
DeepSeek 这类模型为列的定价表。
"""
from __future__ import annotations

from html.parser import HTMLParser


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._cur_table: list[list[str]] | None = None
        self._cur_row: list[str] | None = None
        self._cur_cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._cur_table = []
        elif tag == "tr" and self._cur_table is not None:
            self._cur_row = []
        elif tag in ("td", "th") and self._cur_row is not None:
            self._cur_cell = []
        elif tag == "br" and self._cur_cell is not None:
            self._cur_cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cur_cell is not None:
            text = " ".join("".join(self._cur_cell).split())
            self._cur_row.append(text)  # type: ignore[union-attr]
            self._cur_cell = None
        elif tag == "tr" and self._cur_row is not None:
            self._cur_table.append(self._cur_row)  # type: ignore[union-attr]
            self._cur_row = None
        elif tag == "table" and self._cur_table is not None:
            self.tables.append(self._cur_table)
            self._cur_table = None

    def handle_data(self, data):
        if self._cur_cell is not None:
            self._cur_cell.append(data)


def extract_tables(html: str) -> list[list[list[str]]]:
    """返回所有表格,每个表格是「行 -> 单元格文本列表」。"""
    p = _TableParser()
    p.feed(html)
    return p.tables
