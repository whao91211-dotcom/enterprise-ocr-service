"""Extract query conditions from complete, successful structured tool output."""
import json
import re


def from_tool_result(name, result):
    if name not in {'rag_query', 'rag_summarize'}:
        return None
    try:
        data = json.loads(result)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    source, summary, filters = data.get('source', {}), data.get('summary', {}), data.get('filters', {})
    if not all(isinstance(v, dict) for v in (source, summary, filters)) or source.get('status') != 'confirmed':
        return None
    count = data.get('matched_rows') if name == 'rag_query' else summary.get('total_rows')
    if type(count) is not int or count <= 0:
        return None
    keyword, year = filters.get('keyword', ''), filters.get('year', '')
    if not isinstance(keyword, str) or not isinstance(year, str):
        return None
    if len(keyword) > 200:
        return None
    match = re.fullmatch(r'(\d{4})(?:年)?', year)
    if year and not match:
        return None
    group = data.get('group_by', '')
    if group not in ('', 'item', 'desc', 'date'):
        return None
    return {'tool': name, 'year': match[1] if match else '',
            'keyword': keyword, 'group_by': group}
