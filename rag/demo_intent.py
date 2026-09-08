"""无 Key 可运行的框架演示：意图识别 → Tool 路由映射。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rag.agent import run_turn  # noqa: E402
from rag.intent import classify, suggest_route  # noqa: E402

if __name__ == "__main__":
    print("=== 意图识别演示(规则层, 无Key可跑) ===")
    questions = [
        r"帮我识别 C:\Users\wuhao\Documents\Sample100.png 里的销售单据",
        "SETソフトウェア株式会社 2024年买了多少台空気清浄機?",
        "统计一下每张图有几行商品",
        "你好，你能做什么？",
        "今天天气怎么样",
    ]
    for q in questions:
        it = classify(q)
        route = suggest_route(it)
        print(f"  [{it:>14}] -> {str(route):<16} | {q[:50]}")

    print()
    print("=== agent 无Key时的行为(应为清晰提示) ===")
    try:
        run_turn("查一下冷蔵庫")
    except RuntimeError as e:
        print("  RuntimeError:", e)
