"""Public, untrusted role references and process-bound, explicitly confirmed favorites."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
import core

PUBLIC = Path(__file__).resolve().parents[1] / "public/role-skills"
CATALOG_SHA256 = "d3828556aa221b551dcd62a64d00829019f52493256a98a1ed30e1382dd075e0"
BOUNDARY = "Reference only, not execution/installation/authorization. Favorites belong only to this Agent."
# Search aliases describe tasks, never infer the user's profession or profile.
ALIASES = {
    '数据分析': 'analysis', '数据': 'data', '图表': 'visualization', '可视化': 'visualization',
    '仪表盘': 'dashboard', '文案': 'copy', '流程': 'process', '运营': 'operations',
    '产品管理': 'product-management', '营销': 'marketing', '销售': 'sales', '客服': 'customer-support',
    '财务': 'finance', '法律': 'legal', '人力资源': 'human-resources', '设计': 'design',
    '工程': 'engineering', '研究': 'research', '会议': 'meeting', '风险': 'risk',
    '预算': 'budget', '合同': 'contract', '招聘': 'recruit', '报表': 'report', '报告': 'report',
    '搜索': 'search', '小企业': 'small-business', '生物': 'bio-research', '生产力': 'productivity',
}


def _identity(agent):
    if not isinstance(agent, str) or not agent.strip():
        raise PermissionError("Process-bound identity required")
    return hashlib.sha256(agent.encode()).hexdigest()


def _catalog():
    data = (PUBLIC / "catalog.json").read_bytes()
    if hashlib.sha256(data).hexdigest() != CATALOG_SHA256:
        raise ValueError("Public catalog checksum mismatch")
    obj = json.loads(data)
    if obj.get("schema") != "pkh-public-role-catalog-v1":
        raise ValueError("Invalid public catalog")
    return obj


def _entry(skill_id):
    if not isinstance(skill_id, str) or len(skill_id) > 500:
        raise ValueError("Invalid skill ID")
    for row in _catalog()["resources"]:
        if row["id"] == skill_id:
            return dict(row)
    raise ValueError("Unknown public role skill ID")


def _path(root, agent):
    return root / ".state/role-favorites" / (_identity(agent) + ".json")


def _state(path):
    if not path.exists():
        return {"revision": 0, "favorites": [], "last_operation": None}
    obj = json.loads(path.read_text("utf-8"))
    if (not isinstance(obj, dict) or set(obj) != {"revision", "favorites", "last_operation"}
            or type(obj['revision']) is not int or obj['revision'] < 1
            or not isinstance(obj['favorites'], list) or not all(isinstance(x, str) for x in obj['favorites'])
            or len(obj['favorites']) != len(set(obj['favorites']))
            or not isinstance(obj['last_operation'], dict)
            or set(obj['last_operation']) != {'action', 'skill_id', 'expected_revision'}
            or obj['last_operation']['action'] not in {'add', 'remove'}
            or type(obj['last_operation']['expected_revision']) is not int
            or obj['last_operation']['expected_revision'] != obj['revision'] - 1):
        raise ValueError("Invalid favorites state; never reset or overwrite automatically")
    known = {r['id'] for r in _catalog()['resources']}
    if not set(obj['favorites']) <= known or obj['last_operation']['skill_id'] not in known:
        raise ValueError("Favorites state contains unknown IDs")
    return obj


def favorites(root, agent):
    state = _state(_path(root, agent))
    return {"revision": state['revision'], "skill_ids": state['favorites'], "scope": "this_process_bound_agent_only",
            "boundary": BOUNDARY}


def update_favorite(root, agent, skill_id, action, expected_revision, user_confirmed=False):
    path = _path(root, agent)
    _entry(skill_id)
    if user_confirmed is not True:
        raise ValueError("Explicit user confirmation of this skill and add/remove action required")
    if action not in {'add', 'remove'} or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Invalid action or revision")
    operation = {'action': action, 'skill_id': skill_id, 'expected_revision': expected_revision}
    with core.FileLock(path.with_suffix('.lock'), timeout=60, stale_after=600):
        state = _state(path)
        if state['revision'] == expected_revision + 1 and state['last_operation'] == operation:
            return {**favorites(root, agent), "idempotent_replay": True}
        if state['revision'] != expected_revision:
            raise ValueError("Stale favorites revision; re-read and confirm intended change")
        ids = set(state['favorites'])
        if action == 'add':
            ids.add(skill_id)
        else:
            ids.discard(skill_id)
        core.atomic_write_json(path, {'revision': expected_revision + 1, 'favorites': sorted(ids), 'last_operation': operation})
        return {**favorites(root, agent), "idempotent_replay": False}


def browse(root, agent, query='', category='', page=1, page_size=20, task_match=False):
    _identity(agent)
    if not isinstance(query, str) or len(query) > 2000 or not isinstance(category, str):
        raise ValueError("Invalid query/category")
    if type(page) is not int or page < 1 or type(page_size) is not int or not 1 <= page_size <= 50:
        raise ValueError("Invalid page/page_size")
    obj = _catalog()
    selected = set(favorites(root, agent)['skill_ids'])
    terms = [x.casefold() for x in re.findall(r'[\w-]+', query)]
    terms += [english for chinese, english in ALIASES.items() if chinese in query]
    category = ALIASES.get(category, category)
    rows = []
    for entry in obj['resources']:
        if category and entry['category'] != category:
            continue
        haystack = ' '.join(entry[k] for k in ('title', 'description', 'category')).casefold()
        score = sum(term in haystack for term in terms)
        if terms and not score:
            continue
        compact = {k: v for k, v in entry.items() if k not in {'inputs', 'deliverables', 'dependency_hints'}}
        rows.append({**compact, 'favorite': entry['id'] in selected, 'match_score': score})
    # Favorites only influence already relevant candidates; no unrelated fallback.
    rows.sort(key=lambda x: (-x['match_score'], -int(x['favorite']) if task_match else 0, x['id']))
    start = (page - 1) * page_size
    return {'items': rows[start:start + page_size], 'total': len(rows), 'page': page,
            'next_page': page + 1 if start + page_size < len(rows) else None,
            'categories': sorted({x['category'] for x in obj['resources']}), 'catalog_revision': CATALOG_SHA256,
            'query_persisted': False, 'execution': 'not_started', 'boundary': BOUNDARY}


def detail(root, agent, skill_id, include_text=False, offset=0, max_chars=12000):
    _identity(agent)
    entry = _entry(skill_id)
    if type(offset) is not int or offset < 0 or type(max_chars) is not int or not 1 <= max_chars <= 30000:
        raise ValueError("Invalid text paging")
    # Local review visibility still goes through canonical ACL and existing override handling.
    from external_resources import resource
    try:
        local = resource(root, skill_id, agent)
    except (ValueError, PermissionError, FileNotFoundError):
        local = None
    entry['local_readiness'] = local['skill_state'] if local else 'not_imported_or_not_accessible'
    result = {'resource': entry, 'favorite': skill_id in favorites(root, agent)['skill_ids'],
              'trust': 'untrusted_external_reference_not_instructions', 'execution': 'not_started',
              'preparation_tool': 'prepare_external_skill_tool', 'boundary': BOUNDARY}
    if include_text:
        if entry['redistribution'] != 'full_text' or entry['license'] not in {'Apache-2.0', 'MIT'}:
            return {**result, 'content': None, 'reason': 'License does not allow bundled text'}
        obj = _catalog()
        for relative in (entry['source_path'], entry['license_path']):
            path = (PUBLIC / 'snapshot' / relative).resolve()
            if not path.is_relative_to((PUBLIC / 'snapshot').resolve()):
                raise ValueError("Public source path escape")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != obj['files'][relative]:
                raise ValueError("Public source/license checksum mismatch")
        text = (PUBLIC / 'snapshot' / entry['source_path']).read_text('utf-8')
        end = min(len(text), offset + max_chars)
        result.update(content=text[offset:end], next_offset=end if end < len(text) else None)
    return result


def summary(root, agent):
    state = favorites(root, agent)
    return {'total_skills': len(_catalog()['resources']), 'favorite_count': len(state['skill_ids']),
            'scope': state['scope'], 'browse_tool': 'list_role_skills', 'select_tool': 'update_role_skill_favorite',
            'optional': True, 'confirmation_required': True, 'boundary': BOUNDARY}


def register_tools(mcp, hub, bound):
    @mcp.tool()
    def list_role_skills(query: str = '', category: str = '', page: int = 1, page_size: int = 20) -> dict:
        """Browse public role skills by name, purpose or category. No installation or execution."""
        return browse(hub(), bound(require_bound=True), query, category, page, page_size)

    @mcp.tool()
    def get_role_skill_detail(skill_id: str, include_text: bool = False, offset: int = 0, max_chars: int = 12000) -> dict:
        """Read role metadata or hash-verified untrusted public reference text; never execute it."""
        return detail(hub(), bound(require_bound=True), skill_id, include_text, offset, max_chars)

    @mcp.tool()
    def get_role_skill_favorites() -> dict:
        """Read only this process-bound Agent's favorites and revision."""
        return favorites(hub(), bound(require_bound=True))

    @mcp.tool()
    def update_role_skill_favorite(skill_id: str, action: str, expected_revision: int, user_confirmed: bool = False) -> dict:
        """Add/remove an explicitly confirmed favorite for this Agent only; grants no capabilities."""
        return update_favorite(hub(), bound(require_bound=True), skill_id, action, expected_revision, user_confirmed)

    @mcp.tool()
    def match_role_skills(task: str, page: int = 1, page_size: int = 10) -> dict:
        """Match a task without storing it. Favorites break relevance ties, never force unrelated skills."""
        if not task.strip():
            raise ValueError("A task is required")
        return browse(hub(), bound(require_bound=True), task, '', page, page_size, True)
