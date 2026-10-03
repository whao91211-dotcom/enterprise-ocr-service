"""Record process identity; cleanup only an Office process created by this worker."""
import json
from pathlib import Path
import psutil


def existing_office(name):
    return {p.pid for p in psutil.process_iter(['name']) if (p.info['name'] or '').lower()==name}


def register_owned(pid,name,prior,marker):
    if pid in prior:
        raise RuntimeError('Office instance belongs to another session')
    process=psutil.Process(pid)
    if process.name().lower()!=name:
        raise RuntimeError('Unexpected Office process')
    identity={'pid':pid,'name':name,'created':process.create_time()}
    temp=Path(str(marker)+'.tmp')
    temp.write_text(json.dumps(identity),encoding='utf-8')
    temp.replace(marker)


def cleanup_owned(marker):
    try:
        identity=json.loads(Path(marker).read_text(encoding='utf-8'))
        if identity['name'] not in ('winword.exe','powerpnt.exe'):
            return False
        process=psutil.Process(identity['pid'])
        if process.name().lower()!=identity['name'] or process.create_time()!=identity['created']:
            return False
        process.kill()
        process.wait(timeout=5)
        return True
    except (OSError,ValueError,KeyError,psutil.Error):
        return False
