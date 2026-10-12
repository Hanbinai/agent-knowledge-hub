from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

# Allow direct execution: python /path/to/mcp_server/server.py
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from core import (  # noqa: E402
    capture_event as core_capture_event,
    find_hub,
    confirm_self_record as core_confirm_self_record,
    get_context as core_get_context,
    get_personalized_context as core_get_personalized_context,
    get_user_profile as core_get_user_profile,
    get_event as core_get_event,
    get_project_context as core_get_project_context,
    get_project_timeline as core_get_project_timeline,
    get_record as core_get_record,
    get_record_neighbors as core_get_record_neighbors,
    get_self_context as core_get_self_context,
    get_self_evolution as core_get_self_evolution,
    get_timeline as core_get_timeline,
    health as core_health,
    get_connector_status as core_get_connector_status,
    link_records as core_link_records,
    list_inbox_events as core_list_inbox_events,
    list_pending as core_list_pending,
    list_projects as core_list_projects,
    promote_event_to_record as core_promote_event_to_record,
    promote_record as core_promote_record,
    rebuild_search_index as core_rebuild_search_index,
    review_event as core_review_event,
    record_decision as core_record_decision,
    record_project_checkpoint as core_record_project_checkpoint,
    record_self_observation as core_record_self_observation,
    record_user_preference as core_record_user_preference,
    search_fulltext as core_search_fulltext,
    search_knowledge as core_search_knowledge,
    list_work_items as core_list_work_items,
    resolve_project as core_resolve_project,
    register_project as core_register_project,
    start_project_session as core_start_project_session,
    suggest_event_projects as core_suggest_event_projects,
    upsert_project as core_upsert_project,
    upsert_task as core_upsert_task,
    update_project_registration as core_update_project_registration,
)

try:
    from mcp.server import MCPServer
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "Missing dependency: install the official MCP Python SDK v2 with `pip install \"mcp>=2,<3\"`."
    ) from exc


def _hub() -> Path:
    return find_hub(os.environ.get("PKH_HOME"))


def _bound_agent_id(requested: str | None = None, *, require_bound: bool = False) -> str:
    bound = os.environ.get("PKH_AGENT_ID", "").strip()
    if bound:
        return bound
    raise PermissionError("PKH_AGENT_ID must be bound in the MCP server process for Hub access")


def _bound_source_app() -> str:
    """Durable provenance comes from the process identity, never caller input."""
    return _bound_agent_id(require_bound=True)


mcp = MCPServer(
    "Agent Knowledge Hub",
    instructions=(
        "Local-first shared memory for multiple Agents. Use capture_event for durable user ideas/knowledge; "
        "call get_context at the start of every conversation to load confirmed shared user preferences. "
        "search/get_context before asking the user to repeat known project context. Self Model content is a "
        "separate protected domain: Agent inferences must remain hypotheses until explicit user confirmation. "
        "Use least-privilege context and never expose Self Model data to unrelated Agents. For every non-trivial "
        "project task, call start_project_session before work. After actual project changes or verified decisions, "
        "call record_project_checkpoint before the final response so Codex, WorkBuddy and ZCode can continue from "
        "the same canonical project state. Use get_personalized_context for a short, task-relevant "
        "snapshot of project facts, shared preferences and confirmed Self principles when authorized; "
        "do not treat personal evidence as project facts. Register only stable project roots; never register transient task folders."
    ),
)


@mcp.tool()
def get_onboarding() -> dict:
    """Read optional onboarding readiness and privacy contract; never saves answers or accesses Self."""
    from onboarding import get_onboarding as read_onboarding
    agent = _bound_agent_id(require_bound=True)
    return read_onboarding(_hub(), agent_id=agent)


@mcp.tool()
def update_onboarding(action: str, expected_revision: int, user_confirmed: bool = False) -> dict:
    """Persist answer-free progress: invite/start/pause/skip/complete/resume. Except invite, explicit consent is required. Use current revision; no identity or answer input."""
    from onboarding import update_onboarding as write_onboarding
    agent = _bound_agent_id(require_bound=True)
    return write_onboarding(_hub(), agent_id=agent, action=action,
                            expected_revision=expected_revision, user_confirmed=user_confirmed)


@mcp.tool()
def get_user_profile(include_inactive: bool = False) -> dict:
    """Read current confirmed preferences shared with this process-bound Agent; never private Self records."""
    return core_get_user_profile(_hub(), agent_id=_bound_agent_id(require_bound=True), include_inactive=include_inactive)


@mcp.tool()
def record_user_preference(
    key: str, statement: str, category: str = "communication",
    user_confirmed: bool = False, sharing_confirmed: bool = False,
    share_with_agents: list[str] | None = None, expected_record_id: str | None = None,
    active: bool = True,
) -> dict:
    """Save only user-confirmed collaboration preferences and explicitly approved audience. Pass the current record id for updates; active=false retires a preference. Never copy private evidence here."""
    return core_record_user_preference(
        _hub(), key=key, statement=statement, category=category, user_confirmed=user_confirmed,
        sharing_confirmed=sharing_confirmed, share_with_agents=share_with_agents,
        expected_record_id=expected_record_id, active=active,
        source_agent=_bound_agent_id(require_bound=True), source_app=_bound_source_app(),
    )


@mcp.tool()
def health_check() -> dict:
    """Check whether the shared local Knowledge Hub is reachable and report pending-event count."""
    _bound_agent_id(require_bound=True)
    return core_health(_hub())


@mcp.tool()
def get_connector_status(connector: str = "getnote") -> dict:
    """Read local connector sync status without exposing credentials or calling external services."""
    _bound_agent_id(require_bound=True)
    return core_get_connector_status(_hub(), connector)


@mcp.tool()
def capture_event(
    text: str,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    session_id: str | None = None,
    privacy: str = "private",
    explicit: bool = False,
    routing_hints: list[str] | None = None,
    temporal: dict | None = None,
    provenance: dict | None = None,
    triage: dict | None = None,
    access: dict | None = None,
) -> dict:
    """Capture a durable idea, lesson, decision candidate, task candidate, project update, or user-authored note into the append-only Inbox."""
    return core_capture_event(
        _hub(), text=text, source_app=_bound_source_app(), source_agent=_bound_agent_id(source_agent),
        session_id=session_id, privacy=privacy, explicit=explicit,
        routing_hints=routing_hints, temporal=temporal, provenance=provenance,
        triage=triage, access=access,
    )


@mcp.tool()
def search_knowledge(
    query: str,
    record_types: list[str] | None = None,
    project: str | None = None,
    limit: int = 8,
    agent_id: str | None = None,
) -> list[dict]:
    """Search normal work knowledge. Self Model is excluded; use get_self_context only when authorized."""
    return core_search_knowledge(_hub(), query=query, record_types=record_types, project=project, limit=limit, agent_id=_bound_agent_id(agent_id), include_self=False)


@mcp.tool()
def list_work_items(
    project_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    page_size: int = 50,
    cursor: str | None = None,
) -> dict:
    """Enumerate work task records with stable-snapshot pagination (updated_at DESC, id ASC).

    Permission scope comes from the bound identity; callers cannot widen it.
    statuses defaults to open work items (open/in_progress/blocked); pass e.g.
    ["open","in_progress","blocked","done"] to include finished ones. Cursor binds
    to a snapshot; a changed snapshot returns an explicit SNAPSHOT_EXPIRED error.
    """
    return core_list_work_items(
        _hub(), project_ids=project_ids, statuses=statuses, page_size=page_size,
        cursor=cursor, agent_id=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def search_fulltext(query: str, limit: int = 12, agent_id: str | None = None) -> list[dict]:
    """Use the local SQLite FTS index for fast work-knowledge search; Self Model remains excluded."""
    return core_search_fulltext(_hub(), query=query, limit=limit, agent_id=_bound_agent_id(agent_id), include_self=False)


@mcp.tool()
def get_context(project: str | None = None, query: str | None = None, limit: int = 8, agent_id: str | None = None) -> dict:
    """Load compact work context. Does not include protected Self Model data."""
    return core_get_context(_hub(), project=project, query=query, limit=limit, agent_id=_bound_agent_id(agent_id))


@mcp.tool()
def get_personalized_context(
    query: str, cwd: str | None = None, project: str | None = None,
    include_self: bool = True, limit: int = 4, max_chars: int = 6000,
    known_sections: dict[str, str] | None = None,
) -> dict:
    """Read a bounded task snapshot. Self entries are confirmed, ACL-filtered and never copied into project memory. Pass prior digests to receive only changed sections."""
    return core_get_personalized_context(
        _hub(), query=query, cwd=cwd, project=project,
        agent_id=_bound_agent_id(require_bound=True), include_self=include_self,
        limit=limit, max_chars=max_chars, known_sections=known_sections,
    )


@mcp.tool()
def get_project_context(project: str, limit: int = 8, agent_id: str | None = None) -> dict:
    """Load one project's canonical PROJECT.md plus related work records."""
    return core_get_project_context(_hub(), project=project, limit=limit, agent_id=_bound_agent_id(agent_id))


@mcp.tool()
def list_projects(status: str | None = None) -> list[dict]:
    """List stable shared projects and their aliases/root paths from the canonical project registry."""
    return core_list_projects(_hub(), status=status, agent_id=_bound_agent_id(require_bound=True))


@mcp.tool()
def resolve_project(cwd: str | None = None, project_hint: str | None = None) -> dict:
    """Resolve a stable shared project by exact id/name/alias or the longest matching registered root path."""
    return core_resolve_project(
        _hub(), cwd=cwd, project_hint=project_hint,
        agent_id=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def register_project(
    project: str,
    summary: str,
    root_paths: list[str] | None = None,
    aliases: list[str] | None = None,
    repository_urls: list[str] | None = None,
    status: str = "active",
    source_app: str = "mcp",
) -> dict:
    """Register one stable project identity and root mapping. Do not use transient session or task directories."""
    return core_register_project(
        _hub(), project=project, summary=summary, root_paths=root_paths, aliases=aliases,
        repository_urls=repository_urls, status=status, source_app=_bound_source_app(),
        source_agent=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def update_project_registration(
    project: str,
    add_root_paths: list[str] | None = None,
    remove_root_paths: list[str] | None = None,
    add_aliases: list[str] | None = None,
    remove_aliases: list[str] | None = None,
    add_repository_urls: list[str] | None = None,
    remove_repository_urls: list[str] | None = None,
    status: str | None = None,
) -> dict:
    """Safely add or remove registered project roots, aliases and repository URLs, or archive/reactivate a project."""
    return core_update_project_registration(
        _hub(), project=project, add_root_paths=add_root_paths,
        remove_root_paths=remove_root_paths, add_aliases=add_aliases,
        remove_aliases=remove_aliases, add_repository_urls=add_repository_urls,
        remove_repository_urls=remove_repository_urls, status=status,
        source_agent=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def start_project_session(
    cwd: str | None = None,
    project: str | None = None,
    query: str | None = None,
    limit: int = 8,
) -> dict:
    """Read the canonical project state, recent cross-Agent checkpoints and related work context before project work. This tool is read-only."""
    return core_start_project_session(
        _hub(), cwd=cwd, project=project, query=query, limit=limit,
        agent_id=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def get_project_timeline(cwd: str | None = None, project: str | None = None, limit: int = 20) -> dict:
    """Read recent structured handoff checkpoints for one resolved shared project."""
    return core_get_project_timeline(
        _hub(), cwd=cwd, project=project, limit=limit,
        agent_id=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def record_project_checkpoint(
    title: str,
    summary: str,
    cwd: str | None = None,
    project: str | None = None,
    changes: list[str] | None = None,
    decisions: list[str] | None = None,
    validations: list[str] | None = None,
    open_items: list[str] | None = None,
    files: list[str] | None = None,
    git_ref: str | None = None,
    session_id: str | None = None,
    checkpoint_key: str | None = None,
    project_status: str = "active",
    source_app: str = "mcp",
) -> dict:
    """Write one idempotent structured project handoff after real changes/decisions, then update PROJECT.md for the next Agent."""
    return core_record_project_checkpoint(
        _hub(), title=title, summary=summary, cwd=cwd, project=project, changes=changes,
        decisions=decisions, validations=validations, open_items=open_items, files=files,
        git_ref=git_ref, session_id=session_id, checkpoint_key=checkpoint_key,
        project_status=project_status, source_app=_bound_source_app(),
        source_agent=_bound_agent_id(require_bound=True),
    )


@mcp.tool()
def get_record(record_id: str, agent_id: str | None = None, include_self: bool = False) -> dict:
    """Read one canonical record. Protected Self Model records require include_self=true and an authorized agent_id."""
    return core_get_record(_hub(), record_id=record_id, agent_id=_bound_agent_id(agent_id), include_self=include_self)


@mcp.tool()
def list_pending(limit: int = 50) -> list[dict]:
    """Compatibility view of the first pending Inbox page. Use list_inbox_events for pagination."""
    return core_list_pending(_hub(), limit=limit, agent_id=_bound_agent_id(), include_self=True)


@mcp.tool()
def list_inbox_events(
    statuses: list[str] | None = None,
    source_app: str | None = None,
    lane: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    order: str = "newest",
) -> dict:
    """Page through readable Inbox events. Event-level scopes prevent work Agents from seeing personal GET notes."""
    return core_list_inbox_events(
        _hub(), statuses=statuses, source_app=source_app, lane=lane, limit=limit,
        cursor=cursor, order=order, agent_id=_bound_agent_id(), include_self=True,
    )


@mcp.tool()
def get_event(event_id: str) -> dict:
    """Read one Inbox event with full source, timing, attribution and triage metadata when authorized."""
    return core_get_event(_hub(), event_id, agent_id=_bound_agent_id(), include_self=True)


@mcp.tool()
def suggest_event_projects(event_id: str) -> dict:
    """Suggest deterministic project bindings from explicit hints, exact tags/topics and configured rules; never infer from speaker identity."""
    agent_id = _bound_agent_id(require_bound=True)
    event = core_get_event(_hub(), event_id, agent_id=agent_id, include_self=False)
    return core_suggest_event_projects(_hub(), event=event, agent_id=agent_id)


@mcp.tool()
def review_event(event_id: str, status: str) -> dict:
    """Set an Inbox event to pending, review or ignored without creating canonical knowledge."""
    return core_review_event(_hub(), event_id, status=status, agent_id=_bound_agent_id(), include_self=True)


@mcp.tool()
def promote_event_to_record(
    event_id: str,
    record_type: str,
    title: str,
    project: str | None = None,
    summary: str | None = None,
    origin: str | None = None,
) -> dict:
    """Archive one existing Inbox source and close it idempotently. Self sources require Self access and remain protected: daily evidence or an unconfirmed Self type. origin defaults to agent_inference for Self observations; user_statement/user_reflection remain L2 until separately confirmed."""
    return core_promote_event_to_record(
        _hub(), event_id=event_id, record_type=record_type, title=title,
        project=project, summary=summary, agent_id=_bound_agent_id(), origin=origin,
    )


@mcp.tool()
def get_timeline(
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    include_pending: bool = True,
) -> dict:
    """Read a chronological view while preserving occurrence time, speaker scope and attribution certainty."""
    return core_get_timeline(
        _hub(), start=start, end=end, limit=limit, include_pending=include_pending,
        agent_id=_bound_agent_id(), include_self=True,
    )


@mcp.tool()
def record_decision(
    title: str,
    decision: str,
    rationale: str = "",
    project: str | None = None,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    privacy: str = "private",
) -> dict:
    """Record an explicit durable decision with provenance. This creates an Inbox event and a canonical decision atomically."""
    return core_record_decision(
        _hub(), title=title, decision=decision, rationale=rationale, project=project,
        source_app=_bound_source_app(), source_agent=_bound_agent_id(source_agent), privacy=privacy,
    )


@mcp.tool()
def upsert_task(
    title: str,
    project: str | None = None,
    status: str = "open",
    details: str = "",
    due: str | None = None,
    task_id: str | None = None,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    privacy: str = "private",
) -> dict:
    """Create or update a shared task. Reuse task_id when changing an existing task's status or details."""
    return core_upsert_task(
        _hub(), title=title, project=project, status=status, details=details, due=due,
        task_id=task_id, source_app=_bound_source_app(), source_agent=_bound_agent_id(source_agent), privacy=privacy,
    )


@mcp.tool()
def upsert_project(
    project: str,
    summary: str,
    status: str = "active",
    update: str = "",
    source_app: str = "mcp",
    source_agent: str = "unknown",
    privacy: str = "private",
) -> dict:
    """Create or update one project's canonical PROJECT.md and append a provenance-linked update log entry."""
    return core_upsert_project(
        _hub(), project=project, summary=summary, status=status, update=update,
        source_app=_bound_source_app(), source_agent=_bound_agent_id(source_agent), privacy=privacy,
    )



@mcp.tool()
def record_self_observation(
    title: str,
    statement: str,
    category: str = "self_insight",
    origin: str = "user_statement",
    user_confirmed: bool = False,
    confidence: str = "medium",
    evidence: list[str] | None = None,
    counter_evidence: list[str] | None = None,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    allowed_agents: list[str] | None = None,
    share_with_work: bool = False,
) -> dict:
    """Create a protected Self Model record. Agent inference must remain unconfirmed until the user explicitly confirms it."""
    bound = _bound_agent_id(source_agent, require_bound=True)
    return core_record_self_observation(
        _hub(), title=title, statement=statement, category=category, origin=origin,
        user_confirmed=user_confirmed, confidence=confidence, evidence=evidence,
        counter_evidence=counter_evidence, source_app=_bound_source_app(), source_agent=bound,
        allowed_agents=allowed_agents, share_with_work=share_with_work,
    )


@mcp.tool()
def confirm_self_record(record_id: str, confirmation_note: str = "", source_app: str = "mcp", source_agent: str = "unknown") -> dict:
    """Explicitly confirm a prior Self Model observation/hypothesis as belonging to the user's self model."""
    bound = _bound_agent_id(source_agent, require_bound=True)
    return core_confirm_self_record(_hub(), record_id=record_id, confirmation_note=confirmation_note, source_app=_bound_source_app(), source_agent=bound)


@mcp.tool()
def get_self_context(query: str = "", confirmed_only: bool = False, limit: int = 20) -> dict:
    """Read protected Self Model context. Identity comes only from process-bound PKH_AGENT_ID."""
    return core_get_self_context(_hub(), agent_id=_bound_agent_id(require_bound=True), query=query, confirmed_only=confirmed_only, limit=limit)


@mcp.tool()
def get_self_evolution(limit: int = 100) -> list[dict]:
    """Return a provenance-preserving Self Model timeline. Identity comes from PKH_AGENT_ID."""
    return core_get_self_evolution(_hub(), agent_id=_bound_agent_id(require_bound=True), limit=limit)


@mcp.tool()
def promote_record(record_id: str, target_maturity: str, evidence_note: str = "", source_app: str = "mcp", source_agent: str = "unknown", validation: dict | None = None) -> dict:
    """Promote an authorized record. L3+ requires evidence_note and validation.evidence_event_ids (readable existing events). L4+ also requires validation.applicability and stability_evidence. L5 additionally requires validation.playbook with inputs, outputs, non-empty steps, failure_handling and validation_result. Self L3+ still requires prior user confirmation. These gates check supplied evidence structure, not its real-world truth."""
    return core_promote_record(_hub(), record_id=record_id, target_maturity=target_maturity, evidence_note=evidence_note, source_app=_bound_source_app(), source_agent=_bound_agent_id(source_agent), validation=validation)


@mcp.tool()
def link_records(source_id: str, relation: str, target_id: str, note: str = "", source_agent: str = "unknown") -> dict:
    """Create a graph edge between canonical records, e.g. supports/contradicts/supersedes/evolves_to."""
    return core_link_records(_hub(), source_id=source_id, relation=relation, target_id=target_id, note=note, source_agent=_bound_agent_id(source_agent))


@mcp.tool()
def get_record_neighbors(record_id: str, limit: int = 50, agent_id: str | None = None, include_self: bool = False) -> list[dict]:
    """Read graph edges connected to a canonical record with the same least-privilege filtering used by record reads."""
    return core_get_record_neighbors(_hub(), record_id=record_id, limit=limit, agent_id=_bound_agent_id(agent_id), include_self=include_self)


@mcp.tool()
def rebuild_search_index() -> dict:
    """Rebuild the local SQLite FTS search index from canonical Markdown records."""
    _bound_agent_id(require_bound=True)
    return core_rebuild_search_index(_hub())


from external_resources import register_tools as register_external_tools
register_external_tools(mcp, _hub, _bound_agent_id)
from role_catalog import register_tools as register_role_catalog_tools
register_role_catalog_tools(mcp, _hub, _bound_agent_id)


def _http_bind_host(host: str) -> str:
    """This local release has no remote-client authentication layer."""
    if host.lower() == "localhost":
        return "127.0.0.1"
    try:
        if ipaddress.ip_address(host).is_loopback:
            return host
    except ValueError:
        pass
    raise ValueError("HTTP transport is local-only; use a loopback IP address")

def main() -> None:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--hub", help="Knowledge Hub root. Overrides PKH_HOME for this process.")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "streamable-http"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.transport == "streamable-http":
        try:
            args.host = _http_bind_host(args.host)
        except ValueError as exc:
            parser.error(str(exc))
    if args.hub:
        os.environ["PKH_HOME"] = str(Path(args.hub).expanduser().resolve())
    # Fail fast before entering MCP protocol loop.
    _hub()
    if args.transport == "streamable-http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
