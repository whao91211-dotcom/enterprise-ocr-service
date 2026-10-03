import pytest


@pytest.fixture
def store(monkeypatch, tmp_path):
    from db import database, crud
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    doc = crud.insert_document('synthetic.png')
    crud.insert_rows(doc, [{'item': 'A', 'amount': '2', 'sum': '100'}, {'item': 'B', 'sum': '240'}])
    return crud, doc


def test_missing_row_update_does_not_report_success(store):
    crud, _ = store
    assert crud.update_row(99999, {'sum': '150'}) is False


def test_chat_edit_reports_verified_before_and_after(store):
    from tools.correct_tool import correct_update_row
    crud,doc=store
    row=crud.rows_by_doc(doc)[0]
    result=correct_update_row.invoke({'row_id':row['id'],'fields':'{"sum":"150"}'})
    assert 'sum: 100 → 150' in result
    assert result.cards==[{'type':'ocr_review','doc_id':doc}]
    assert crud.rows_by_doc(doc)[0]['sum']=='150'


def test_missing_row_in_batch_rolls_back_prior_changes(store):
    from db import review
    crud,doc=store
    current=review.get_review(doc)
    with pytest.raises(LookupError):
        review.save_edits(doc,current['version'],[{'id':current['rows'][0]['id'],'fields':{'sum':'150'}},
                                                {'id':99999,'fields':{'sum':'200'}}])
    assert crud.rows_by_doc(doc)[0]['sum']=='100'
    assert review.get_review(doc)['version']==current['version']


def test_full_width_currency_matches_chat_and_office(store):
    from services.sales_snapshot import capture
    crud,doc=store
    assert crud.update_row(crud.rows_by_doc(doc)[0]['id'],{'sum':'￥1,200.00'})
    crud.confirm_doc(doc)
    assert sum(r['total'] for r in crud.summarize_confirmed())==capture()['summary']['total_sum']==1440


def test_migration_preserves_legacy_records_and_is_idempotent(tmp_path,monkeypatch):
    from db import database,chat_memory
    from db.schema import SQL_SCHEMA
    monkeypatch.setattr(database,'_DATA_DIR',tmp_path)
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'legacy.db')
    conn=database.get_connection()
    with conn:
        conn.executescript(SQL_SCHEMA)
        conn.execute("INSERT INTO documents(file_name) VALUES('legacy.png')")
        conn.execute("INSERT INTO chat_sessions(id) VALUES('legacy')")
        conn.execute("INSERT INTO chat_messages(session_id,role,content) VALUES('legacy','user','保留旧消息')")
    conn.close()
    database.init_db();database.init_db()
    assert chat_memory.message_page('legacy')['messages'][0]['content']=='保留旧消息'
    assert chat_memory.message_page('legacy')['messages'][0]['meta']=={}
    conn=database.get_connection()
    assert dict(conn.execute('SELECT file_name,version FROM documents').fetchone())=={'file_name':'legacy.png','version':0}
    conn.close()


def test_edit_confirmed_row_requires_new_review(store):
    crud, doc = store
    crud.confirm_doc(doc)
    row = crud.rows_by_doc(doc)[0]
    assert crud.update_row(row['id'], {'sum': '150'})
    assert crud.rows_by_doc(doc)[0]['status'] == 'pending'
    assert crud.confirmed_count() == 1


def test_batch_edit_is_atomic_and_versioned(store):
    from db import review
    crud, doc = store
    rows = crud.rows_by_doc(doc)
    version = review.get_review(doc)['version']
    with pytest.raises(ValueError):
        review.save_edits(doc, version, [{'id': rows[0]['id'], 'fields': {'sum': '150'}},
                                        {'id': rows[1]['id'], 'fields': {'sum': 'not-a-number'}}])
    assert crud.rows_by_doc(doc)[0]['sum'] == '100'
    result = review.save_edits(doc, version, [{'id': rows[0]['id'], 'fields': {'sum': '150'}}])
    assert result['changes'][0]['before']['sum'] == '100'
    assert result['changes'][0]['after']['sum'] == '150'
    with pytest.raises(review.VersionConflict):
        review.save_edits(doc, version, [{'id': rows[0]['id'], 'fields': {'sum': '999'}}])
    assert crud.rows_by_doc(doc)[0]['sum'] == '150'


def test_repeat_confirmation_is_idempotent_and_stale_confirmation_rejected(store):
    from db import review
    crud, doc = store
    first = review.confirm(doc, review.get_review(doc)['version'])
    again = review.confirm(doc, first['version'])
    assert again['changed'] == 0
    with pytest.raises(review.VersionConflict):
        review.confirm(doc, 0)


def test_history_pagination_restores_cards_without_expanding_model_context(store):
    from db import chat_memory
    sid, _ = chat_memory.resolve_session(None)
    for i in range(35):
        chat_memory.save_turn(sid, f'用户问题{i}', f'回答{i}', assistant_meta={'cards': [{'type':'ocr_review','doc_id':1}]})
    page = chat_memory.message_page(sid, limit=20)
    assert len(page['messages']) == 20 and page['has_more']
    assert page['messages'][-1]['meta']['cards'][0]['doc_id'] == 1
    older = chat_memory.message_page(sid, before_id=page['messages'][0]['id'], limit=20)
    assert older['messages'][-1]['id'] < page['messages'][0]['id']
    assert len(chat_memory.load_messages(sid, limit=10)) == 10
    assert chat_memory.list_sessions('用户问题0')[0]['id'] == sid
    chat_memory.rename_session(sid, '季度核对')
    assert chat_memory.list_sessions('季度核对')[0]['title'] == '季度核对'
