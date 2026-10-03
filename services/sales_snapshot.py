"""One consistent confirmed-sales snapshot shared by all Office renderers."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from db import crud, artifacts
from db.database import get_connection


def number(value):
    try:
        result=Decimal(str(value or '0').replace(',','').replace('¥','').replace('￥',''))
        return result if result.is_finite() else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def capture(year='',keyword='',snapshot_id=''):
    year=str(year).strip().removesuffix('年')
    if year and not re.fullmatch(r'\d{4}',year):
        raise ValueError('年份须为四位年份或留空')
    if snapshot_id:
        snapshot=artifacts.get(snapshot_id)['snapshot']
        if not snapshot.get('summary'):
            raise ValueError('此文件没有可复用的销售数据快照')
        if (year and year!=snapshot['filters']['year']) or (keyword and keyword!=snapshot['filters']['keyword']):
            raise ValueError('快照与指定过滤条件不一致，请使用新查询或原快照条件')
        return snapshot
    conn=get_connection()
    try:
        with conn:
            conn.execute('BEGIN')
            where,params=crud._where_params(keyword or None,year or None)
            rows=[crud._user_row(dict(r)) for r in conn.execute(
                'SELECT r.*,d.file_name FROM ocr_rows r JOIN documents d ON r.doc_id=d.id WHERE '+where+
                ' ORDER BY r.date_,r.doc_id,r.seq,r.id',params).fetchall()]
    finally:
        conn.close()
    groups={}
    for key in ('item','desc','date'):
        grouped={}
        for row in rows:
            name=str(row[key] or '')
            entry=grouped.setdefault(name,{'group':name,'rows':0,'amount':Decimal(0),'total':Decimal(0)})
            entry['rows']+=1
            entry['amount']+=number(row['amount'])
            entry['total']+=number(row['sum'])
        groups[key]=[{**g,'amount':float(g['amount']),'total':float(g['total'])}
                     for g in sorted(grouped.values(),key=lambda g:g['total'],reverse=True)]
    return {'filters':{'year':year,'keyword':keyword},'created_at':datetime.now().isoformat(timespec='seconds'),
            'rows':rows,'groups':groups,'summary':{'total_rows':len(rows),
                'total_docs':len({r['doc_id'] for r in rows}),
                'total_amount':float(sum((number(r['amount']) for r in rows),Decimal(0))),
                'total_sum':float(sum((number(r['sum']) for r in rows),Decimal(0)))}}
