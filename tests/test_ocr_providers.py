import json
import httpx
import pytest


FIELDS = ('desc', 'date', 'from', 'item', 'amount', 'price', 'tax', 'sum')


@pytest.fixture
def cloud(monkeypatch):
    import config
    from tools import ocr_client
    monkeypatch.setattr(config, 'OCR_PROVIDER', 'qwen', raising=False)
    monkeypatch.setattr(config, 'QWEN_OCR_BASE_URL', 'https://example.test/v1', raising=False)
    monkeypatch.setattr(config, 'QWEN_OCR_MODEL', 'qwen-vl-ocr', raising=False)
    monkeypatch.setattr(config, 'QWEN_OCR_API_KEY', 'cloud-test-secret', raising=False)
    monkeypatch.setattr(config, 'QWEN_OCR_TIMEOUT_SECONDS', 60, raising=False)
    monkeypatch.setattr(config, 'QWEN_OCR_MAX_TOKENS', 4096, raising=False)
    return ocr_client


def reply(content, finish='stop'):
    return httpx.Response(200, json={'choices': [{'finish_reason': finish, 'message': {'content': content}}],
                                     'usage': {'prompt_tokens': 100, 'completion_tokens': 20}})


def test_cloud_uses_separate_credentials_and_preserves_invoice_values(cloud, monkeypatch):
    row = dict.fromkeys(FIELDS, '') | {'desc': '合成公司', 'amount': '2', 'price': '9999', 'sum': '-25'}
    observed = {}

    def post(url, **kwargs):
        observed.update(url=url, **kwargs)
        return reply(json.dumps({'rows': [row]}, ensure_ascii=False))

    monkeypatch.setattr(cloud.httpx, 'post', post)
    result = cloud.recognize(b'synthetic', 'png')
    assert observed['url'] == 'https://example.test/v1/chat/completions'
    assert observed['headers']['Authorization'] == 'Bearer cloud-test-secret'
    assert observed['json']['model'] == 'qwen-vl-ocr'
    assert result['rows'] == [row]
    assert result['provider'] == 'qwen'


@pytest.mark.parametrize('content,finish', [
    ('{"rows":[{"sum":"1"}]}', 'stop'),
    ('not json', 'stop'),
    ('{"rows":[]}', 'length'),
    ('{"rows":[{"desc":{"nested":"value"}}]}', 'stop'),
])
def test_cloud_rejects_invalid_or_truncated_results(cloud, monkeypatch, content, finish):
    monkeypatch.setattr(cloud.httpx, 'post', lambda *a, **k: reply(content, finish))
    with pytest.raises(cloud.OcrError):
        cloud.recognize(b'synthetic')


def test_cloud_http_error_does_not_echo_response_body(cloud, monkeypatch):
    monkeypatch.setattr(cloud.httpx, 'post', lambda *a, **k: httpx.Response(401, text='cloud-test-secret'))
    with pytest.raises(cloud.OcrError) as caught:
        cloud.recognize(b'synthetic')
    assert '401' in str(caught.value)
    assert 'cloud-test-secret' not in str(caught.value)


def test_missing_cloud_key_never_calls_network(cloud, monkeypatch):
    import config
    monkeypatch.setattr(config, 'QWEN_OCR_API_KEY', '')
    monkeypatch.setattr(cloud.httpx, 'post', lambda *a, **k: pytest.fail('unexpected network'))
    with pytest.raises(cloud.OcrError):
        cloud.recognize(b'synthetic')


def test_legacy_internvl_contract_is_retained(cloud, monkeypatch):
    import config
    monkeypatch.setattr(config, 'OCR_PROVIDER', 'internvl')
    monkeypatch.setattr(config, 'OCR_BASE_URL', 'http://127.0.0.1:9052/v1')
    monkeypatch.setattr(config, 'OCR_API_KEY', 'local-test-secret')
    observed = {}

    def post(url, **kwargs):
        observed.update(url=url, **kwargs)
        return reply('甲,2024-01-01,乙,商品,1,2,0,2')

    monkeypatch.setattr(cloud.httpx, 'post', post)
    assert cloud.recognize(b'synthetic')['rows'][0]['sum'] == '2'
    assert observed['url'].startswith('http://127.0.0.1:9052/')
    assert observed['headers']['Authorization'] == 'Bearer local-test-secret'


def test_invalid_cloud_result_does_not_replace_existing_database_rows(cloud, monkeypatch, tmp_path):
    from db import database, crud
    from tools.ocr_tool import ocr_recognize
    monkeypatch.setattr(database, '_DATA_DIR', tmp_path)
    monkeypatch.setattr(database, 'DB_PATH', tmp_path/'agent.db')
    database.init_db()
    image = tmp_path/'existing.png'
    image.write_bytes(b'synthetic')
    doc_id = crud.upsert_document(image.name, None)
    crud.insert_rows(doc_id, [{'item': 'existing', 'sum': '42'}])
    monkeypatch.setattr(cloud.httpx, 'post', lambda *a, **k: reply('not json'))
    assert ocr_recognize.invoke({'image_path': str(image)}).startswith('识别失败:')
    assert crud.rows_by_doc(doc_id)[0]['sum'] == '42'
