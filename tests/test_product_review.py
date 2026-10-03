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
