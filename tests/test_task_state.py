import json
import tempfile
from pathlib import Path

from db import database
from db import task_state, chat_memory
from agent.task_state import from_tool_result


def test_state_survives_history_trimming_and_is_session_isolated():
    old = database._DATA_DIR, database.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as tmp:
            database._DATA_DIR, database.DB_PATH = Path(tmp), Path(tmp)/'agent.db'
            database.init_db()
            session, _ = chat_memory.resolve_session(None)
            other, _ = chat_memory.resolve_session(None)
            state = {'tool': 'rag_summarize', 'year': '2024', 'keyword': '甲公司', 'group_by': 'item'}
            task_state.save(session, state)
            for i in range(7):
                chat_memory.save_turn(session, str(i), '无关回答')
            assert task_state.load(session) == state
            assert task_state.load(other) == {}
            task_state.save(session, {**state, 'keyword': '', 'year': ''})
            assert task_state.load(session)['year'] == ''
    finally:
        database._DATA_DIR, database.DB_PATH = old


def test_only_successful_query_updates_state():
    payload = {'source': {'status': 'confirmed'}, 'filters': {'year': '2024年', 'keyword': '甲公司'},
               'group_by': 'desc', 'summary': {'total_rows': 2}}
    state = from_tool_result('rag_summarize', json.dumps(payload))
    assert state['year'] == '2024'
    assert state['keyword'] == '甲公司'
    assert from_tool_result('rag_summarize', '工具执行出错: offline') is None
    assert from_tool_result('rag_query', '（未检索到匹配的已确认数据）') is None
    assert from_tool_result('generate_report', json.dumps(payload)) is None
    assert from_tool_result('rag_summarize', '{"source": []}') is None
