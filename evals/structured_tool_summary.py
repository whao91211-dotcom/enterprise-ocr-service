"""Paired offline probe of real SQL tool outputs on fixed synthetic rows."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tiktoken
from db import crud, database
from tools.rag_tool import rag_query, rag_summarize


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    encoding = tiktoken.get_encoding("cl100k_base")
    previous = database._DATA_DIR, database.DB_PATH
    cases = []
    try:
        with tempfile.TemporaryDirectory(prefix="structured_tool_eval_") as tmp:
            database._DATA_DIR = Path(tmp)
            database.DB_PATH = Path(tmp) / "agent.db"
            database.init_db()
            doc_id = crud.upsert_document("synthetic-sales-2024.png", None)
            crud.insert_rows(doc_id, [
                {"desc": "甲公司", "date": "2024-01-10", "from": "示例供货商",
                 "item": f"商品{i % 40:02d}", "amount": "1", "price": "10",
                 "tax": "0", "sum": "10"} for i in range(400)
            ] + [{"desc": "乙公司", "date": "2025-01-10", "item": "其他年份",
                  "amount": "1", "sum": "9999"}])
            crud.confirm_doc(doc_id, "synthetic-eval")
            pending_id = crud.upsert_document("pending.png", None)
            crud.insert_rows(pending_id, [{"date": "2024-01-10", "sum": "9999"}])
            for name, tool, params in (
                ("default_detail", rag_query, {"year": "2024"}),
                ("large_detail", rag_query, {"year": "2024", "top_k": 400}),
                ("forty_groups", rag_summarize, {"year": "2024"}),
            ):
                output = tool.invoke(params)
                case = {"case": name, "args": params, "characters": len(output),
                        "proxy_tokens": len(encoding.encode(output)), "output": output}
                try:
                    data = json.loads(output)
                except json.JSONDecodeError:
                    data = None
                if data is not None:
                    assert data["source"]["status"] == "confirmed"
                    assert data["filters"]["year"] == "2024"
                    if name == "forty_groups":
                        assert data["summary"]["total_sum"] == 4000
                        assert data["summary"]["total_rows"] == 400
                        assert data["total_groups"] == 40
                        assert data["omitted_groups"] == 20
                    else:
                        assert data["matched_rows"] == 400
                        assert data["omitted_rows"] == 380
                        assert len(data["rows"]) == 20
                        records = [dict(zip(data["columns"], row)) for row in data["rows"]]
                        assert all(r["sum"] == "10" and r["doc_id"] == doc_id
                                   and r["file_name"] == "synthetic-sales-2024.png"
                                   for r in records)
                    case["critical_fields_verified"] = True
                cases.append(case)
    finally:
        database._DATA_DIR, database.DB_PATH = previous
    report = {
        "dataset": "400 confirmed 2024 rows, 40 products, quantity 400, total 4000; excluded: one 2025 row and one pending row",
        "metric": "real SQL tool output text proxy tokens (cl100k_base)",
        "cases": cases,
        "limitations": ["Not DeepSeek billing tokens or answer accuracy",
                        "No model, OCR, latency or API cost measured",
                        "Original 10400-character fake tool fixture is a separate baseline"],
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: v for k, v in case.items() if k != "output"}
                      for case in cases], ensure_ascii=False))


if __name__ == "__main__":
    main()
