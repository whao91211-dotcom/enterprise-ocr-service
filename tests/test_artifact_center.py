from pathlib import Path
import subprocess
import pytest


@pytest.fixture
def file_store(monkeypatch, tmp_path):
    from db import database
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    return tmp_path


def test_registered_files_only_and_missing_files(file_store):
    from db import artifacts
    source = file_store/'synthetic.docx'
    source.write_bytes(b'synthetic')
    ident = artifacts.register(source, 'docx', {'total_sum': '390'})
    assert artifacts.get(ident)['snapshot']['total_sum']=='390'
    with pytest.raises(LookupError):
        artifacts.get('../../.env')
    source.unlink()
    with pytest.raises(FileNotFoundError):
        artifacts.get(ident)


def test_preview_timeout_keeps_original_and_can_retry(file_store, monkeypatch):
    from db import artifacts
    from services import office_preview
    source = file_store/'synthetic.docx'
    source.write_bytes(b'synthetic')
    ident = artifacts.register(source, 'docx')
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(office_preview.subprocess, 'run', timeout)
    office_preview.convert_artifact(ident)
    assert artifacts.get(ident)['preview_status']=='failed'
    assert source.read_bytes()==b'synthetic'
    office_preview.convert_artifact(ident)
    assert artifacts.get(ident)['preview_status']=='failed'


def test_download_path_and_preview_api(file_store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.artifact_api import router
    from db import artifacts
    source = file_store/'synthetic.docx'
    source.write_bytes(b'synthetic')
    ident = artifacts.register(source, 'docx')
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    assert client.get(f'/api/artifacts/{ident}/download').content == b'synthetic'
    info = client.get(f'/api/artifacts/{ident}').json()
    assert 'path' not in info
    assert client.get('/api/artifacts/unknown/download').status_code==404
    assert client.get(f'/api/artifacts/{ident}/preview.pdf').status_code==409
