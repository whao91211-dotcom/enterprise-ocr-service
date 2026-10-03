"""Injected provider failures must preserve previously reviewed business rows."""
import httpx
import pytest


@pytest.mark.parametrize('failure', ['timeout', 'connection', 401, 429, 503,
                                     'bad_response', 'bad_json', 'truncated', 'empty'])
def test_provider_failure_preserves_reviewed_rows(monkeypatch, tmp_path, failure):
    import config
    from db import database, crud
    from tools import ocr_client
    from tools.ocr_tool import ocr_recognize
    monkeypatch.setattr(config, 'OCR_PROVIDER', 'qwen')
    monkeypatch.setattr(config, 'QWEN_OCR_API_KEY', 'synthetic-secret')
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    image = tmp_path/'reviewed.png'
    image.write_bytes(b'synthetic')
    doc_id = crud.insert_document(image.name)
    crud.insert_rows(doc_id, [{'item': 'reviewed', 'sum': '42'}])
    crud.confirm_doc(doc_id)
    before = crud.rows_by_doc(doc_id)
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if failure == 'timeout':
            raise httpx.ReadTimeout('synthetic-secret')
        if failure == 'connection':
            raise httpx.ConnectError('synthetic-secret')
        if isinstance(failure, int):
            return httpx.Response(failure, text='synthetic-secret')
        if failure == 'bad_response':
            return httpx.Response(200, json={'choices': []})
        content = 'not json' if failure == 'bad_json' else '{"rows":[]}'
        return httpx.Response(200, json={'choices': [{'finish_reason':
            'length' if failure == 'truncated' else 'stop', 'message': {'content': content}}]})

    monkeypatch.setattr(ocr_client.httpx, 'post', post)
    result = ocr_recognize.invoke({'image_path': str(image)})
    assert result.startswith(('识别失败:', '识别到 0 行'))
    assert 'synthetic-secret' not in result
    assert calls == [1]
    assert crud.rows_by_doc(doc_id) == before


def test_successful_rerecognition_replaces_all_rows_with_pending(monkeypatch, tmp_path):
    from db import database, crud
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    doc_id = crud.insert_document('existing.png')
    crud.insert_rows(doc_id, [{'item': 'old', 'sum': '42'}])
    crud.confirm_doc(doc_id)
    assert crud.replace_recognized_rows('existing.png', [{'item': 'new', 'sum': '-25'}]) == doc_id
    rows = crud.rows_by_doc(doc_id)
    assert len(rows) == 1
    assert (rows[0]['item'], rows[0]['sum'], rows[0]['status']) == ('new', '-25', 'pending')
    assert crud.confirmed_count() == 0
    assert len(crud.all_docs_with_stats()) == 1


def test_database_insert_failure_during_rerecognition_preserves_old_rows(monkeypatch, tmp_path):
    import sqlite3
    from db import database, crud
    from tools import ocr_tool
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    image = tmp_path/'existing.png'
    image.write_bytes(b'synthetic')
    doc_id = crud.insert_document(image.name)
    crud.insert_rows(doc_id, [{'item': 'old', 'sum': '42'}])
    crud.confirm_doc(doc_id)
    before = crud.rows_by_doc(doc_id)
    conn = database.get_connection()
    conn.execute("CREATE TRIGGER reject_new BEFORE INSERT ON ocr_rows "
                 "WHEN NEW.item='reject' BEGIN SELECT RAISE(ABORT, 'injected insert failure'); END")
    conn.commit()
    conn.close()
    monkeypatch.setattr(ocr_tool, 'recognize', lambda *args: {'rows': [
        {'item': 'first', 'sum': '10'}, {'item': 'reject', 'sum': '20'}], 'latency_ms': 1})
    with pytest.raises(sqlite3.IntegrityError, match='injected insert failure'):
        ocr_tool.ocr_recognize.invoke({'image_path': str(image)})
    assert crud.rows_by_doc(doc_id) == before
