"""Live cloud OCR on programmatically generated, non-confidential images only."""
import argparse
import json
import sys
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
FIELDS = ('desc', 'date', 'from', 'item', 'amount', 'price', 'tax', 'sum')
CASES = [
    ('clean', [['合成甲公司','2026-10-03','合成供应商','打印纸','2','50','0','100'],
               ['合成乙公司','2026-10-03','合成供应商','墨盒','3','80','0','240']]),
    ('discount_zero', [['测试公司','2026-10-03','测试供应商','商品A','2','999','0','100'],
                       ['测试公司','2026-10-03','测试供应商','折扣','0','0','0','-25'],
                       ['测试公司','2026-10-03','测试供应商','赠品','7','10','0','0']]),
    ('japanese', [['テスト会社','2026-10-03','サンプル商社','コピー用紙','2','50','0','100'],
                  ['テスト会社','2026-10-03','サンプル商社','インク','3','80','0','240']]),
]


def same(actual, expected, numeric):
    if not numeric:
        return actual.strip() == expected
    try:
        return Decimal(actual.replace(',', '')) == Decimal(expected)
    except InvalidOperation:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--artifacts-root', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=[ident for ident, _ in CASES],
                        default=[ident for ident, _ in CASES])
    args = parser.parse_args()
    from PIL import Image, ImageDraw, ImageFont
    from tools.ocr_client import recognize, OcrError
    import config
    if config.OCR_PROVIDER != 'qwen':
        raise RuntimeError('This probe requires explicitly configured qwen provider')
    font_path = next(p for p in [Path('C:/Windows/Fonts/meiryo.ttc'), Path('C:/Windows/Fonts/msyh.ttc')] if p.exists())
    font = ImageFont.truetype(str(font_path), 27)
    args.artifacts_root.mkdir(parents=True, exist_ok=True)
    report = {'dataset': 'synthetic-ocr-v1, generated locally; no real invoices', 'model': config.QWEN_OCR_MODEL,
              'selected_cases': args.cases, 'cases': [], 'limits': ['Clean rendered images; not real invoice accuracy or an InternVL comparison',
                                     'No real documents sent to cloud; row alignment assumes preserved order']}
    headers = ['顾客公司','发注日','源公司','商品','数量','单价','税率','票面金额']
    widths = [260, 230, 260, 240, 130, 130, 130, 180]
    for ident, rows in CASES:
        if ident not in args.cases:
            continue
        image = Image.new('RGB', (sum(widths)+40, 160+80*(len(rows)+1)), 'white')
        draw = ImageDraw.Draw(image)
        draw.text((25, 20), '合成销售单据 - 仅用于接口测试', font=font, fill='black')
        for index, cells in enumerate([headers]+rows):
            x, y = 20, 90+80*index
            for width, cell in zip(widths, cells):
                draw.rectangle((x,y,x+width,y+80), outline='black', width=2)
                draw.text((x+8,y+20), cell, font=font, fill='black')
                x += width
        path = args.artifacts_root/f'{ident}.png'
        image.save(path)
        started = time.perf_counter()
        case = {'id': ident, 'expected': rows, 'image': str(path.resolve())}
        try:
            result = recognize(path.read_bytes())
            observed = result['rows']
            checks = [same(observed[i].get(key,''), row[j], j >= 4)
                      if i < len(observed) else False for i,row in enumerate(rows) for j,key in enumerate(FIELDS)]
            case.update(rows=observed, raw=result['raw'], usage=result['usage'], provider=result['provider'],
                        ocr_latency_ms=result['latency_ms'],
                        field_correct=sum(checks), field_count=len(checks), row_count_correct=len(observed)==len(rows),
                        passed=all(checks) and len(observed)==len(rows))
            # Check the real ingestion tool with this already obtained model result,
            # avoiding an extra billable call; database is isolated from user data.
            from db import database, crud
            from tools import ocr_tool, ocr_client
            with tempfile.TemporaryDirectory(prefix='qwen_ocr_ingest_') as tmp:
                with patch.object(database,'_DATA_DIR',Path(tmp)), patch.object(database,'DB_PATH',Path(tmp)/'agent.db'):
                    database.init_db()
                    with patch.object(ocr_client,'recognize',return_value=result), patch.object(ocr_tool,'recognize',return_value=result):
                        outcome = ocr_tool.ocr_recognize.invoke({'image_path':str(path)})
                    docs = crud.all_docs_with_stats()
                    saved = crud.rows_by_doc(docs[0]['id']) if docs else []
                    case['pending_ingestion'] = (outcome.startswith('✅') and len(saved)==len(observed)
                                                 and all(r['status']=='pending' for r in saved))
                    case['passed'] = case['passed'] and case['pending_ingestion']
        except OcrError as exc:
            case.update(passed=False, error=str(exc))
        case['elapsed_ms'] = round((time.perf_counter()-started)*1000)
        report['cases'].append(case)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in case.items() if k not in ('raw','rows','expected')},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
