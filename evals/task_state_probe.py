"""Paired context delivery and optional live follow-up on synthetic data."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from db import database, chat_memory, task_state
    from evals.task_benchmark import load_streaming_agent, ScriptedModel
    from evals.real_text_baseline import _seed_sales
    agent = load_streaming_agent()
    previous = database._DATA_DIR, database.DB_PATH
    report = {'dataset': 'synthetic: 2024甲=100, 2024乙=200, 2025乙=900', 'cases': [],
              'limits': ['Context availability is not model task accuracy',
                         'Live mode is two one-shot diagnostics; no real invoice data',
                         'Latest successful query only; not full conversation summary']}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            _seed_sales()
            session, _ = chat_memory.resolve_session(None)
            first = ScriptedModel([[{'name': 'rag_summarize', 'args': {'year': '2024', 'keyword': '甲公司', 'group_by': 'desc'}, 'id': 'first'}]])
            with patch.object(agent, 'get_llm', return_value=first):
                events = list(agent.run_agent_events('查询2024年甲公司'))
            state = next(e['state'] for e in events if e['type'] == 'task_state')
            task_state.save(session, state)
            chat_memory.save_turn(session, '查询2024年甲公司', '金额100')
            for i in range(7):
                chat_memory.save_turn(session, f'无关对话{i}', '收到')
            history = chat_memory.load_messages(session, 10)
            for mode, saved in [('without_state', None), ('with_state', task_state.load(session))]:
                capture = ScriptedModel()
                with patch.object(agent, 'get_llm', return_value=capture):
                    list(agent.run_agent_events('继续最早那次查询，销售额是多少？', history, task_state=saved))
                text = '\n'.join(str(m.content) for m in capture.messages[0])
                case = {'mode': mode, 'history_messages': len(history),
                        'required_filters_available': '2024' in text and '甲公司' in text}
                if args.live:
                    import config
                    from langchain_deepseek import ChatDeepSeek
                    factory = lambda: ChatDeepSeek(model=config.DEEPSEEK_MODEL, api_key=config.require_deepseek_key(),
                                                   temperature=.3, timeout=20, max_retries=0)
                    start = time.perf_counter()
                    with patch.object(agent, 'get_llm', side_effect=factory):
                        events = list(agent.run_agent_events('继续最早那次查询，销售额是多少？', history, task_state=saved))
                    case['elapsed_ms'] = round((time.perf_counter()-start)*1000)
                    case['events'] = events
                report['cases'].append(case)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
                print(json.dumps({k:v for k,v in case.items() if k!='events'}, ensure_ascii=False), flush=True)
    finally:
        database._DATA_DIR, database.DB_PATH = previous


if __name__ == '__main__':
    main()
