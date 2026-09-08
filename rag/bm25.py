"""轻量中文 BM25 检索器（纯标准库，零第三方依赖）。

针对销售单据行文本（中/日/英混合）:
  - 分词: 对中日文用「二元组 + 单字」切分，对 ASCII 用空格切分后加词边界，
    避免 jieba 依赖且对未知词（日文假名/公司名）鲁棒。
  - 打分: 经典 BM25(k1=1.5, b=0.75)，倒排索引。

用法:
    idx = BM25Index()
    idx.build([{"id": 0, "text": "..."}, ...])
    hits = idx.search("X公司 2024 空调", top_k=5)
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")


def tokenize(text: str) -> list[str]:
    """把文本切成检索词。中日文按字符二元组，ASCII 词保持原样小写。"""
    tokens: list[str] = []
    # 非 ASCII 连续段（中文/日文/韩文等）与 ASCII 词分开处理
    for ascii_word in _ASCII_TOKEN_RE.findall(text):
        tokens.append(ascii_word.lower())
    # 去掉 ASCII 部分后剩下的非空白字符做二元组
    rest = _ASCII_TOKEN_RE.sub(" ", text)
    chars = [c for c in rest if not c.isspace()]
    if chars:
        tokens.append("".join(chars[:1]).lower())  # 首单字
        for i in range(len(chars) - 1):
            tokens.append((chars[i] + chars[i + 1]).lower())
    return tokens


class BM25Index:
    """可增量 build 的 BM25 倒排索引（数据量小，直接全量重建）。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.docs: list[dict[str, Any]] = []  # 原样文档（含 id/text/meta）
        self._doc_terms: list[list[str]] = []  # 每篇词列表
        self._doc_len: list[int] = []
        self._avgdl = 0.0
        self._df: dict[str, int] = defaultdict(int)  # 词 -> 文档频率
        self._postings: dict[str, list[int]] = defaultdict(list)  # 词 -> doc 下标
        self._built = False

    def build(self, docs: list[dict[str, Any]], text_key: str = "text") -> None:
        self.docs = docs
        self._doc_terms = []
        self._doc_len = []
        self._df.clear()
        self._postings.clear()

        for i, doc in enumerate(docs):
            terms = tokenize(str(doc.get(text_key, "")))
            # 去重词用于 df/倒排
            seen: set[str] = set()
            for t in terms:
                if t not in seen:
                    seen.add(t)
                    self._df[t] += 1
                    self._postings[t].append(i)
            self._doc_terms.append(terms)
            self._doc_len.append(len(terms))

        n = len(docs)
        self._avgdl = (sum(self._doc_len) / n) if n else 0.0
        self._built = True

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """返回 [{"id":..., "text":..., "score":..., "rank":...}, ...]（已按分降序）。"""
        if not self._built or not self.docs:
            return []
        q_terms = tokenize(query)
        if not q_terms:
            return []
        # 统计查询词频
        qf: dict[str, int] = defaultdict(int)
        for t in q_terms:
            qf[t] += 1

        n = len(self.docs)
        scores: list[float] = [0.0] * n
        for term, freq in qf.items():
            df = self._df.get(term, 0)
            if df == 0:
                continue
            idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
            for doc_i in self._postings.get(term, []):
                tf = self._doc_terms[doc_i].count(term)
                dl = self._doc_len[doc_i] or 1
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
                scores[doc_i] += idf * (tf * (self.k1 + 1)) / denom * freq

        ranked = sorted(range(n), key=lambda i: scores[i], reverse=True)
        out: list[dict[str, Any]] = []
        for rank, i in enumerate(ranked[:top_k], start=1):
            if scores[i] <= 0:
                continue
            item = dict(self.docs[i])
            item["score"] = round(scores[i], 4)
            item["rank"] = rank
            out.append(item)
        return out
