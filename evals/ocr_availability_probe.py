"""Read-only configured OCR availability probe; never prints keys or endpoint."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    import config
    import requests
    report = {'checked_at': datetime.now(timezone.utc).isoformat(), 'timeout_seconds': 3,
              'limit': 'GET /models availability only; not OCR recognition quality'}
    headers = {'Authorization': f'Bearer {config.OCR_API_KEY}'} if config.OCR_API_KEY else {}
    try:
        response = requests.get(config.OCR_BASE_URL.rstrip('/')+'/models', headers=headers, timeout=3)
        report['http_status'] = response.status_code
        report['ocr_endpoint_probe'] = 'reachable' if response.ok else 'http_error'
    except requests.RequestException as exc:
        report.update(ocr_endpoint_probe='unavailable', error_class=type(exc).__name__)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
