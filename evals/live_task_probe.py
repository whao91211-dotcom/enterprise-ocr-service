"""Three live diagnostic tasks on synthetic data only, with evaluation timeouts."""
import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import config
    from db import database
    from langchain_deepseek import ChatDeepSeek
    from evals.task_benchmark import load_streaming_agent
    from evals.real_text_baseline import _seed_sales, _run
    from unittest.mock import patch

    if not config.DEEPSEEK_API_KEY:
        raise SystemExit('DEEPSEEK_API_KEY not configured')
    report = {'dataset': 'synthetic: 2024 甲=100, 2024 乙=200, 2025 乙=900',
              'model': config.DEEPSEEK_MODEL, 'cases': [],
              'limitations': ['Three diagnostic tasks, not 35-case Agent success rate',
                             'No OCR or provided invoice data sent to API',
                             'Evaluation sets SDK timeout=20s, retries=0; production defaults unchanged',
                             'Token usage and API cost not measured; answers require review']}
    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    previous = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory(prefix='live_task_probe_') as tmp:
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp) / 'agent.db'
            _seed_sales()
            agent = load_streaming_agent()
            factory = lambda: ChatDeepSeek(model=config.DEEPSEEK_MODEL,
                                           api_key=config.DEEPSEEK_API_KEY,
                                           temperature=.3, timeout=20, max_retries=0)
            history = []
            for index, (question, expected) in enumerate((
                ('统计2024年甲公司的销售额', 100),
                ('那乙公司呢？', 200),
                ('2024年总销售额是多少？', 300),
            )):
                try:
                    with patch.object(agent, 'get_llm', side_effect=factory):
                        result = _run(question, history if index == 1 else [])
                    result['expected_total'] = expected
                    from evals.answer_checks import field_label_errors
                    result['known_field_label_errors'] = field_label_errors(result['answer'])
                    result['status'] = 'completed_needs_review'
                    if index == 0:
                        history = [{'role': 'user', 'content': question},
                                   {'role': 'assistant', 'content': result['answer'] or ''}]
                except Exception as exc:
                    result = {'question': question, 'expected_total': expected,
                              'status': 'error', 'error_type': type(exc).__name__}
                report['cases'].append(result)
                save()
                print(json.dumps({'case': index+1, 'status': result['status'],
                                  'elapsed_ms': result.get('elapsed_ms')}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
