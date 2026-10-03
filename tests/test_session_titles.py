"""History labels use the initial question, including conversations before title migration."""
import pytest


@pytest.fixture
def memory(monkeypatch,tmp_path):
    from db import database,chat_memory
    monkeypatch.setattr(database,'_DATA_DIR',tmp_path)
    monkeypatch.setattr(database,'DB_PATH',tmp_path/'agent.db')
    database.init_db()
    return database,chat_memory


def legacy_session(database,sid,question,title=''):
    conn=database.get_connection()
    with conn:
        conn.execute('INSERT INTO chat_sessions(id,title) VALUES(?,?)',(sid,title))
        conn.execute("INSERT INTO chat_messages(session_id,role,content) VALUES(?,'user',?)",(sid,question))
    conn.close()


def test_startup_backfills_old_titles_without_changing_history_or_recency(memory):
    database,chat=memory
    legacy_session(database,'old','  请统计2022年\n  销售额  ')
    before=chat.list_sessions()[0]['updated_at']
    database.init_db();database.init_db()
    assert chat.list_sessions()[0]['title']=='请统计2022年 销售额'
    assert chat.list_sessions()[0]['updated_at']==before
    assert chat.message_page('old')['title']=='请统计2022年 销售额'
    assert chat.load_messages('old')[0]['content']=='  请统计2022年\n  销售额  '


def test_continuing_untitled_session_uses_original_question(memory):
    database,chat=memory
    legacy_session(database,'old','请生成销售报告')
    chat.save_turn('old','现在改成Excel','已生成')
    assert chat.list_sessions()[0]['title']=='请生成销售报告'


def test_manual_titles_survive_migration_and_later_messages(memory):
    database,chat=memory
    legacy_session(database,'custom','请生成销售报告','季度销售汇报')
    database.init_db()
    chat.save_turn('custom','重新统计','已完成')
    assert chat.message_page('custom')['title']=='季度销售汇报'


def test_image_only_question_has_readable_label_and_long_title_is_bounded(memory):
    _,chat=memory
    sid,_=chat.resolve_session(None)
    chat.save_turn(sid,'[已上传图片: 销售单据.png]','识别完成')
    assert chat.message_page(sid)['title']=='识别单据 · 销售单据.png'
    second,_=chat.resolve_session(None)
    chat.save_turn(second,'请生成年度销售报告'*20,'已生成')
    assert chat.message_page(second)['title']==('请生成年度销售报告'*20)[:36]
