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
    cleaned=[]
    monkeypatch.setattr(office_preview,'cleanup_owned',lambda marker:cleaned.append(marker))
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs['timeout'])
    monkeypatch.setattr(office_preview.subprocess, 'run', timeout)
    office_preview.convert_artifact(ident)
    assert artifacts.get(ident)['preview_status']=='failed'
    assert source.read_bytes()==b'synthetic'
    office_preview.convert_artifact(ident)
    assert artifacts.get(ident)['preview_status']=='failed'
    assert len(cleaned)==2


def test_office_cleanup_refuses_reused_or_foreign_process(file_store,monkeypatch):
    import json
    from services import office_process
    from unittest.mock import Mock
    marker=file_store/'owned.json'
    marker.write_text(json.dumps({'pid':123,'name':'powerpnt.exe','created':1}),encoding='utf-8')
    process=Mock()
    process.name.return_value='POWERPNT.EXE'
    process.create_time.return_value=2
    monkeypatch.setattr(office_process.psutil,'Process',lambda pid:process)
    assert office_process.cleanup_owned(marker) is False
    process.kill.assert_not_called()
    process.create_time.return_value=1
    assert office_process.cleanup_owned(marker) is True
    process.kill.assert_called_once()


def test_powerpoint_preview_refuses_existing_user_instance(file_store,monkeypatch):
    from services import office_process,office_worker
    import win32com.client
    from unittest.mock import Mock
    monkeypatch.setattr(office_process,'existing_office',lambda name:{777})
    dispatch=Mock()
    monkeypatch.setattr(win32com.client,'DispatchEx',dispatch)
    with pytest.raises(RuntimeError,match='Close existing PowerPoint'):
        office_worker.convert(file_store/'test.pptx',file_store/'test.pdf',file_store/'owned.json')
    dispatch.assert_not_called()


def test_word_owns_process_before_open_and_quits_on_open_failure(file_store,monkeypatch):
    from services import office_process,office_worker
    import win32com.client
    from unittest.mock import Mock
    order=[]
    inventory=iter([set(),{123}])
    monkeypatch.setattr(office_process,'existing_office',lambda name:next(inventory))
    monkeypatch.setattr(office_process,'register_owned',lambda *args:order.append('registered'))
    app=Mock()
    def fail(*args,**kwargs):
        order.append('open')
        raise RuntimeError('Synthetic open failure')
    app.Documents.Open.side_effect=fail
    monkeypatch.setattr(win32com.client,'DispatchEx',lambda name:app)
    with pytest.raises(RuntimeError,match='Synthetic open failure'):
        office_worker.convert(file_store/'test.docx',file_store/'test.pdf',file_store/'owned.json')
    assert order==['registered','open']
    app.Quit.assert_called_once()


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


def test_pdf_image_preview_has_page_bounds_and_zoom_limits(file_store):
    import pypdfium2
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from web.artifact_api import router
    from db import artifacts
    source=file_store/'synthetic.docx';source.write_bytes(b'synthetic')
    ident=artifacts.register(source,'docx')
    path=file_store/'preview.pdf'
    pdf=pypdfium2.PdfDocument.new()
    page=pdf.new_page(200,300);page.close()
    pdf.save(str(path));pdf.close()
    artifacts.preview_state(ident,'ready',path=str(path))
    app=FastAPI();app.include_router(router)
    client=TestClient(app)
    assert client.get(f'/api/artifacts/{ident}/preview/pages').json()=={'pages':1}
    image=client.get(f'/api/artifacts/{ident}/preview/page/0')
    assert image.headers['content-type']=='image/png'
    assert image.content.startswith(b'\x89PNG')
    assert client.get(f'/api/artifacts/{ident}/preview/page/1').status_code==404
    assert client.get(f'/api/artifacts/{ident}/preview/page/0?scale=999').status_code==422
