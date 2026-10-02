from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import time


def test_profile_preference_limit_is_atomic_under_competing_writers(monkeypatch, tmp_path):
    from db import database, preference_memory
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    profile = preference_memory.resolve_profile(None)
    for i in range(19):
        assert preference_memory.save_preference(profile, f'seed-{i}') == 'saved'
    original = preference_memory.get_connection

    class SlowCount:
        def __init__(self):
            self.conn = original()

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, *args):
            cursor = self.conn.execute(sql, *args)
            if 'COUNT(*)' in sql:
                # Expose a real pause after reading a count, before the insert.
                time.sleep(.05)
            return cursor

    monkeypatch.setattr(preference_memory, 'get_connection', SlowCount)
    start = Barrier(8)

    def write(i):
        start.wait(timeout=5)
        return preference_memory.save_preference(profile, f'new-{i}')

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(write, range(8)))
    assert results.count('saved') == 1
    assert results.count('full') == 7
    assert len(preference_memory.list_preferences(profile)) == 20
