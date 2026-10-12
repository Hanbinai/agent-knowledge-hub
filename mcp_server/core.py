from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CANONICAL_FOLDERS = [
    "10_Knowledge", "20_Projects", "30_Ideas", "40_Decisions",
    "50_Tasks", "60_Daily", "70_Self", "80_Reviews", "90_Assets",
]

SELF_TYPE_FOLDERS = {
    "identity": "71_Identity",
    "value": "72_Values",
    "belief": "73_Beliefs",
    "principle": "74_Principles",
    "pattern": "75_Patterns",
    "self_insight": "76_Self_Insights",
    "human_nature": "77_Human_Nature",
    "life_reflection": "78_Life_Reflections",
    "evolution": "79_Evolution",
}
SELF_TYPES = set(SELF_TYPE_FOLDERS)
MATURITY_LEVELS = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}
GRAPH_RELATIONS = {
    "related_to", "supports", "contradicts", "supersedes", "derives_from",
    "applies_to", "example_of", "depends_on", "evolves_to", "influences",
}

ROUTE_KEYWORDS = {
    "idea": ["#灵感", "灵光一现", "闪念", "idea"],
    "task": ["#待办", "#事务", "待办", "todo"],
    "decision": ["#决策", "决定", "decision"],
    "knowledge": ["#知识", "#方法论", "方法论", "沉淀"],
    "review": ["#复盘", "复盘"],
    "daily": ["#日常", "#日记"],
    "asset": ["#素材"],
    "self": ["#自我", "#自我认知", "个人诊断", "self insight"],
    "value": ["#价值观", "价值观"],
    "belief": ["#信念", "#世界观", "信念"],
    "principle": ["#原则", "人生原则", "做事原则"],
    "pattern": ["#模式", "#行为模式", "#决策模式", "行为模式"],
    "human_nature": ["#人性", "#人性观察", "人性观察"],
    "reflection": ["#人生", "#反思", "人生反思"],
}

PREFIX = {
    "knowledge": "K", "idea": "I", "decision": "D", "project": "P",
    "project_checkpoint": "CP",
    "task": "T", "daily": "N", "timeline": "TL", "meeting": "MTG",
    "review": "R", "asset": "A",
    "identity": "SID", "value": "VAL", "belief": "BLF", "principle": "PRN",
    "pattern": "PAT", "self_insight": "INS", "human_nature": "HNI",
    "life_reflection": "LFR", "evolution": "EVO",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_hub(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("PKH_HOME"):
        candidates.append(Path(os.environ["PKH_HOME"]))
    candidates.append(Path.cwd())
    candidates.extend(Path.cwd().parents)
    for candidate in candidates:
        root = candidate.expanduser().resolve()
        if (root / "knowledge-hub.json").is_file():
            data = json.loads((root / "knowledge-hub.json").read_text(encoding="utf-8"))
            if data.get("hub_type") == "agent-knowledge-hub":
                return root
    raise FileNotFoundError("Knowledge Hub not found. Set PKH_HOME or pass a hub path.")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with tmp.open("x", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: Path, obj: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def discard_lock_file(path: Path) -> None:
    """Release a lock file *without* deleting it.

    Rationale
    ---------
    `Path.unlink(...)` on Windows is not reliably fast here: with the WorkBuddy
    filesystem-protection daemon active, deletes of files under the Hub have been
    measured stalling for exactly 60s roughly half the time (8 sequential
    create/write/unlink cycles -> 239.58s, of which 4 x ~30s sat in `unlink`,
    while `open(O_EXCL)`, `write` and `fsync` each stayed at 0.00s). Because
    `capture_event` takes one lock, that stall showed up as ~29s per imported
    note and dominated sync wall-clock time.

    An atomic rename stays sub-millisecond and frees the lock *name* just as
    effectively as deleting the file. The O_EXCL claim protocol is unchanged
    (file present => held, file absent => free), so this is transparent to every
    other process sharing the Hub. The rename target is a single overwritable
    sink file, so nothing accumulates.
    """
    try:
        os.replace(path, path.with_name(path.name + ".released"))
    except FileNotFoundError:
        pass
    except OSError:
        # Only fall back to a real delete if renaming is genuinely impossible.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _windows_process_is_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    if pid > 0xFFFFFFFF:
        return False
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    # Query a waitable handle only. os.kill(pid, 0) sends CTRL_C_EVENT on Windows.
    handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: PID no longer exists.
            return False
        if error == 5:  # ERROR_ACCESS_DENIED is not evidence of a dead owner.
            return True
        raise ctypes.WinError(error)
    try:
        status = kernel32.WaitForSingleObject(handle, 0)
        if status == 0:  # WAIT_OBJECT_0: process has exited.
            return False
        if status == 258:  # WAIT_TIMEOUT: process is still running.
            return True
        raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel32.CloseHandle(handle)


def _process_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


@dataclass
class FileLock:
    path: Path
    timeout: float = 8.0
    stale_after: float = 90.0
    poll: float = 0.05
    token: str = ""

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.token = f"{os.getpid()}:{uuid.uuid4().hex}"
        deadline = time.monotonic() + self.timeout
        payload = json.dumps({"token": self.token, "pid": os.getpid(), "created_at": now_iso()})
        while True:
            try:
                fd = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(payload)
                    f.flush()
                    os.fsync(f.fileno())
                return self
            except (FileExistsError, PermissionError):
                try:
                    owner = json.loads(self.path.read_text(encoding="utf-8"))
                    owner_pid = int(owner.get("pid") or 0)
                    if owner_pid and not _process_is_alive(owner_pid):
                        discard_lock_file(self.path)
                        continue
                    age = time.time() - self.path.stat().st_mtime
                    if not owner_pid and age > self.stale_after:
                        discard_lock_file(self.path)
                        continue
                except FileNotFoundError:
                    pass
                except (PermissionError, json.JSONDecodeError, ValueError):
                    # A creator may still be writing, or Windows may deny reads
                    # while the lock is being renamed. Never reclaim on ambiguity.
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Timed out acquiring Hub write lock: {self.path}")
                time.sleep(self.poll)

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            current = json.loads(self.path.read_text(encoding="utf-8"))
            if current.get("token") == self.token:
                discard_lock_file(self.path)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass


def lock_for(root: Path) -> FileLock:
    return FileLock(root / ".state" / "mcp-write.lock")


def project_registry_lock_for(root: Path) -> FileLock:
    return FileLock(
        root / ".state" / "project-registry-operation.lock",
        timeout=30.0,
        stale_after=300.0,
    )


def route_hints(text: str, extra: Iterable[str] | None = None) -> list[str]:
    hints: list[str] = list(extra or [])
    lowered = text.lower()
    for route, kws in ROUTE_KEYWORDS.items():
        if any(k.lower() in lowered for k in kws):
            hints.append(route)
    for project in re.findall(r"#项目[:：]([^\s#]+)", text):
        hints.append("project:" + project)
    return sorted(set(hints))


def load_agent_registry(root: Path) -> dict[str, Any]:
    path = root / "00_System" / "AGENTS.json"
    if not path.exists():
        return {"default": {"scopes": ["work"], "self_access": False}, "agents": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("default", {"scopes": ["work"], "self_access": False})
        data.setdefault("agents", {})
        return data
    except Exception:
        return {"default": {"scopes": ["work"], "self_access": False}, "agents": {}}


def agent_policy(root: Path, agent_id: str | None) -> dict[str, Any]:
    reg = load_agent_registry(root)
    base = dict(reg.get("default", {}))
    if agent_id and agent_id in reg.get("agents", {}):
        base.update(reg["agents"][agent_id])
    base.setdefault("scopes", ["work"])
    base.setdefault("self_access", False)
    return base


def _list_value(meta: dict[str, Any], key: str) -> list[str]:
    value = meta.get(key, [])
    if isinstance(value, list):
        return [str(x) for x in value]
    if value in (None, "", "null"):
        return []
    return [str(value)]


def can_read_record(root: Path, meta: dict[str, Any], agent_id: str | None, *, include_self: bool = False) -> bool:
    rtype = str(meta.get("type") or "")
    scopes = _list_value(meta, "context_scopes")
    if not scopes:
        scopes = ["self"] if rtype in SELF_TYPES else ["work"]
    # Legacy Self records were incorrectly marked work. Type is authoritative:
    # a stale scope label must never make the original readable as work knowledge.
    if rtype in SELF_TYPES and "self" not in scopes:
        scopes = ["self"]
    allowed = _list_value(meta, "allowed_agents")
    denied = _list_value(meta, "denied_agents")
    aid = agent_id or "unknown"
    if aid in denied or "*" in denied:
        return False
    if allowed and aid not in allowed and "*" not in allowed:
        return False
    if "self" in scopes:
        if not include_self:
            return False
        pol = agent_policy(root, agent_id)
        if not pol.get("self_access", False):
            return False
    if rtype != "project" and agent_id is not None:
        # A record may have a permissive local ACL while its project is
        # restricted. Every read path must honor the parent project's ACL.
        for project_name in _list_value(meta, "projects"):
            project_path = root / "20_Projects" / _safe_slug(project_name) / "PROJECT.md"
            if not project_path.is_file():
                return False
            project_meta = _parse_frontmatter(project_path.read_text(encoding="utf-8", errors="replace"))
            if str(project_meta.get("type")) != "project" or not can_read_record(root, project_meta, agent_id, include_self=False):
                return False
    return True


def _event_access(event: dict[str, Any]) -> dict[str, Any]:
    access = event.get("access")
    if isinstance(access, dict) and access.get("context_scopes"):
        return access
    # Legacy Self evidence lacked access metadata. A routing label is not an ACL,
    # but it is sufficient to fail closed rather than expose its original text.
    hints = set(event.get("signals", {}).get("routing_hints", []))
    protected = bool(hints & (SELF_TYPES | {"self", "reflection"}))
    return {"context_scopes": ["self" if protected else "work"], "allowed_agents": [], "denied_agents": []}


def can_read_event(
    root: Path, event: dict[str, Any], agent_id: str | None, *, include_self: bool = False,
    record_index: dict[str, dict[str, Any]] | None = None,
) -> bool:
    access = _event_access(event)
    scopes = _list_value(access, "context_scopes") or ["work"]
    allowed = _list_value(access, "allowed_agents")
    denied = _list_value(access, "denied_agents")
    aid = agent_id or "unknown"
    if aid in denied or "*" in denied:
        return False
    if allowed and aid not in allowed and "*" not in allowed:
        return False
    if "self" in scopes:
        if not include_self:
            return False
        if not agent_policy(root, agent_id).get("self_access", False):
            return False
    hints = event.get("signals", {}).get("routing_hints", [])
    for hint in hints if isinstance(hints, list) else []:
        if not isinstance(hint, str) or not hint.startswith("project:"):
            continue
        project_name = hint.removeprefix("project:").strip()
        project_path = root / "20_Projects" / _safe_slug(project_name) / "PROJECT.md"
        if not project_name or not project_path.is_file():
            return False
        project_meta = _parse_frontmatter(project_path.read_text(encoding="utf-8", errors="replace"))
        if not can_read_record(root, project_meta, agent_id, include_self=False):
            return False
    # Processed events can also gain a project through promotion even when
    # their original routing hints were only candidates.
    for record_id in _list_value(event, "canonical_ids"):
        if record_index is not None:
            meta = record_index.get(record_id)
        else:
            path = _find_record_by_id(root, record_id)
            meta = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace")) if path else None
        if not meta or not can_read_record(root, meta, agent_id, include_self=include_self):
            return False
    return True


def _maturity_ok(level: str) -> str:
    if level not in MATURITY_LEVELS:
        raise ValueError(f"Unsupported maturity: {level}")
    return level


def _event_path(root: Path, event_id: str, created_at: str | None = None) -> Path:
    if created_at:
        dt = datetime.fromisoformat(created_at)
        return root / "01_Inbox" / "events" / dt.strftime("%Y/%m/%d") / f"event-{event_id}.json"
    for p in (root / "01_Inbox" / "events").rglob(f"event-{event_id}.json"):
        return p
    raise FileNotFoundError(f"Event not found: {event_id}")


def capture_event(
    root: Path,
    *,
    text: str,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    session_id: str | None = None,
    cwd: str | None = None,
    privacy: str = "private",
    explicit: bool = False,
    routing_hints: list[str] | None = None,
    raw_paths: list[str] | None = None,
    kind: str = "text",
    source_uri: str | None = None,
    external_id: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    temporal: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
    triage: dict[str, Any] | None = None,
    access: dict[str, Any] | None = None,
    event_id: str | None = None,
    created_at_override: str | None = None,
) -> dict[str, Any]:
    if privacy not in {"private", "sensitive", "shareable", "public"}:
        raise ValueError("Invalid privacy value")
    created_at = created_at_override or now_iso()
    eid = event_id or ("evt_" + uuid.uuid4().hex)
    if not re.fullmatch(r"evt_[A-Za-z0-9_-]+", eid):
        raise ValueError("Invalid event_id")
    digest_src = (text + "\n" + "\n".join(raw_paths or [])).encode("utf-8")
    enriched = True
    event = {
        "schema_version": "1.2" if enriched else "1.1",
        "event_id": eid,
        "created_at": created_at,
        "source": {
            "app": source_app,
            "agent": source_agent,
            "session_id": session_id,
            "cwd": cwd,
            "uri": source_uri,
            "external_id": external_id,
            "metadata": source_metadata or {},
        },
        "content": {
            "kind": kind,
            "text": text or None,
            "raw_paths": raw_paths or [],
        },
        "signals": {
            "explicit_capture": explicit,
            "routing_hints": route_hints(text, routing_hints),
            "user_keywords": [],
        },
        "privacy": privacy,
        "content_sha256": sha256_bytes(digest_src),
        "status": "pending",
        "canonical_ids": [],
    }
    if enriched:
        event_temporal = dict(temporal or {
            "occurred_at": created_at, "captured_at": created_at,
            "source_updated_at": None, "time_basis": "ingested_at", "confidence": "low",
        })
        event_temporal.setdefault("ingested_at", created_at)
        event["temporal"] = event_temporal
        event["provenance"] = provenance or {
            "speaker_scope": "unknown", "user_authorship": "unverified", "claim_role": "captured_content",
        }
        event["triage"] = triage or {"lane": "unclassified", "confidence": "low", "requires_review": True}
        event["access"] = access if access and access.get("context_scopes") else _event_access(event)
    path = _event_path(root, eid, created_at)
    with lock_for(root):
        atomic_write_json(path, event)
    return {"event_id": eid, "path": str(path.relative_to(root)).replace("\\", "/"), "event": event}


def mark_event(root: Path, event_id: str, *, status: str, canonical_id: str | None = None) -> None:
    if status not in {"pending", "processed", "ignored", "review"}:
        raise ValueError("Invalid event status")
    path = _event_path(root, event_id)
    with lock_for(root):
        obj = json.loads(path.read_text(encoding="utf-8"))
        obj["status"] = status
        if canonical_id and canonical_id not in obj.setdefault("canonical_ids", []):
            obj["canonical_ids"].append(canonical_id)
        obj["processed_at"] = now_iso() if status != "pending" else None
        atomic_write_json(path, obj)


def _safe_slug(text: str, max_len: int = 64) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "-", text.strip())
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text).strip(".-_")
    return (text[:max_len] or "record")


def new_id(record_type: str) -> str:
    return f"{PREFIX[record_type]}-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if not s or any(c in s for c in ":#[]{}\n\"'"):
        return json.dumps(s, ensure_ascii=False)
    return s


def _frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_yaml_scalar(x) for x in v)}]")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def _parse_frontmatter(text: str) -> dict[str, Any]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}
    out: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            raw = v[1:-1].strip()
            out[k.strip()] = [x.strip().strip('"\'') for x in raw.split(",") if x.strip()] if raw else []
        else:
            out[k.strip()] = v.strip('"\'')
    return out


def _canonical_path(
    root: Path, record_type: str, record_id: str, title: str,
    project: str | None = None, occurred_at: str | None = None,
) -> Path:
    slug = _safe_slug(title)
    year = datetime.now().strftime("%Y")
    if occurred_at:
        try:
            occurred = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
            year = occurred.strftime("%Y")
        except ValueError:
            occurred = None
    else:
        occurred = None
    if record_type == "knowledge":
        return root / "10_Knowledge" / year / f"{record_id}-{slug}.md"
    if record_type == "idea":
        return root / "30_Ideas" / year / f"{record_id}-{slug}.md"
    if record_type == "decision":
        return root / "40_Decisions" / year / f"{record_id}-{slug}.md"
    if record_type == "task":
        return root / "50_Tasks" / "records" / f"{record_id}-{slug}.md"
    if record_type == "project":
        return root / "20_Projects" / _safe_slug(project or title) / "PROJECT.md"
    if record_type == "project_checkpoint":
        month = occurred.strftime("%m") if occurred else datetime.now().strftime("%m")
        return root / "20_Projects" / _safe_slug(project or "unresolved") / "checkpoints" / year / month / f"{record_id}-{slug}.md"
    if record_type in {"daily", "timeline", "meeting"}:
        month = occurred.strftime("%m") if occurred else datetime.now().strftime("%m")
        return root / "60_Daily" / year / month / f"{record_id}-{slug}.md"
    if record_type == "review":
        return root / "80_Reviews" / year / f"{record_id}-{slug}.md"
    if record_type == "asset":
        return root / "90_Assets" / year / f"{record_id}-{slug}.md"
    if record_type in SELF_TYPES:
        return root / "70_Self" / SELF_TYPE_FOLDERS[record_type] / year / f"{record_id}-{slug}.md"
    raise ValueError(f"Unsupported direct canonical type: {record_type}")


def _write_canonical(
    root: Path,
    *,
    record_type: str,
    title: str,
    body: str,
    project: str | None,
    source_event_ids: list[str],
    source_agents: list[str],
    status: str = "active",
    privacy: str = "private",
    confidence: str = "high",
    record_id: str | None = None,
    maturity: str = "L2",
    visibility: str = "work",
    context_scopes: list[str] | None = None,
    allowed_agents: list[str] | None = None,
    denied_agents: list[str] | None = None,
    origin: str | None = None,
    user_confirmed: bool | None = None,
    evidence_event_ids: list[str] | None = None,
    counter_evidence: list[str] | None = None,
    extra_meta: dict[str, Any] | None = None,
    occurred_at: str | None = None,
    actor_agent: str | None = None,
) -> dict[str, Any]:
    _maturity_ok(maturity)
    if visibility not in {"private", "work", "project", "shareable", "public"}:
        raise ValueError("Invalid visibility")
    writer = actor_agent or (source_agents[0] if source_agents else "unknown")
    if project:
        _require_project_write_access(root, project, writer)
    rid = record_id or new_id(record_type)
    path = _canonical_path(root, record_type, rid, title, project, occurred_at)
    created = now_iso()
    if path.exists():
        fm = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if not can_read_record(root, fm, writer, include_self=True):
            raise PermissionError(f"Agent '{writer}' is not authorized to update {rid}")
        created = fm.get("created_at", created)
    scopes = context_scopes or (["self"] if record_type in SELF_TYPES else ["work"])
    meta: dict[str, Any] = {
        "schema": "pkh-canonical-v3",
        "id": rid,
        "type": record_type,
        "status": status,
        "created_at": created,
        "updated_at": now_iso(),
        "privacy": privacy,
        "visibility": visibility,
        "context_scopes": scopes,
        "allowed_agents": allowed_agents or [],
        "denied_agents": denied_agents or [],
        "projects": [project] if project else [],
        "source_event_ids": source_event_ids,
        "source_agents": sorted(set(source_agents)),
        "confidence": confidence,
        "maturity": maturity,
        "origin": origin or "user_or_work_record",
        "supersedes": [],
        "related": [],
    }
    if user_confirmed is not None:
        meta["user_confirmed"] = user_confirmed
    if evidence_event_ids is not None:
        meta["evidence_event_ids"] = evidence_event_ids
    if counter_evidence is not None:
        meta["counter_evidence"] = counter_evidence
    if extra_meta:
        meta.update(extra_meta)
    with lock_for(root):
        atomic_write_text(path, _frontmatter(meta) + body.rstrip() + "\n")
    return {"id": rid, "path": str(path.relative_to(root)).replace("\\", "/"), "type": record_type}


def _require_project_write_access(
    root: Path, project: str, source_agent: str, *, allow_create: bool = False,
) -> None:
    """Honor an existing project's ACL before writing linked content."""
    project_path = root / "20_Projects" / _safe_slug(project) / "PROJECT.md"
    if not project_path.is_file() and not allow_create:
        raise ValueError(f"Project '{project}' has no canonical record; create or register it before writing linked content")
    if project_path.is_file():
        meta = _parse_frontmatter(project_path.read_text(encoding="utf-8", errors="replace"))
        if str(meta.get("type")) != "project" or not can_read_record(root, meta, source_agent):
            raise PermissionError(f"Agent '{source_agent}' is not authorized to write project '{project}'")

def record_decision(
    root: Path,
    *,
    title: str,
    decision: str,
    rationale: str = "",
    project: str | None = None,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    privacy: str = "private",
) -> dict[str, Any]:
    if project:
        _require_project_write_access(root, project, source_agent)
    event = capture_event(
        root,
        text=f"#决策 {title}\n\n{decision}\n\n理由：{rationale}".strip(),
        source_app=source_app,
        source_agent=source_agent,
        privacy=privacy,
        explicit=True,
        routing_hints=["decision"] + ([f"project:{project}"] if project else []),
    )
    body = f"# {title}\n\n## 决策\n{decision.strip()}\n"
    if rationale.strip():
        body += f"\n## 理由\n{rationale.strip()}\n"
    body += f"\n## 来源\n- {event['event_id']}\n"
    result = _write_canonical(
        root,
        record_type="decision",
        title=title,
        body=body,
        project=project,
        source_event_ids=[event["event_id"]],
        source_agents=[source_agent],
        privacy=privacy,
    )
    mark_event(root, event["event_id"], status="processed", canonical_id=result["id"])
    rebuild_json_index(root)
    return {**result, "source_event_id": event["event_id"]}


def _find_record_by_id(root: Path, record_id: str) -> Path | None:
    for folder in CANONICAL_FOLDERS:
        for p in (root / folder).rglob("*.md"):
            if record_id in p.name:
                return p
            try:
                if _parse_frontmatter(p.read_text(encoding="utf-8", errors="replace")).get("id") == record_id:
                    return p
            except OSError:
                pass
    return None


def upsert_task(
    root: Path,
    *,
    title: str,
    project: str | None = None,
    status: str = "open",
    details: str = "",
    due: str | None = None,
    task_id: str | None = None,
    source_app: str = "mcp",
    source_agent: str = "unknown",
    privacy: str = "private",
) -> dict[str, Any]:
    if status not in {"open", "in_progress", "blocked", "done", "cancelled"}:
        raise ValueError("Unsupported task status")
    old_path = _find_record_by_id(root, task_id) if task_id else None
    old_meta: dict[str, Any] = {}
    if old_path:
        old_meta = _parse_frontmatter(old_path.read_text(encoding="utf-8", errors="replace"))
        if str(old_meta.get("type")) != "task" or not can_read_record(root, old_meta, source_agent):
            raise PermissionError(f"Agent '{source_agent}' is not authorized to update task '{task_id}'")
        if project is None:
            prior_projects = _list_value(old_meta, "projects")
            project = prior_projects[0] if prior_projects else None
    if project:
        _require_project_write_access(root, project, source_agent)
    event = capture_event(
        root,
        text=f"#待办 {title}\n状态：{status}\n{details}".strip(),
        source_app=source_app,
        source_agent=source_agent,
        privacy=privacy,
        explicit=True,
        routing_hints=["task"] + ([f"project:{project}"] if project else []),
    )
    rid = task_id or new_id("task")
    with lock_for(root):
        existing = _find_record_by_id(root, rid) if task_id else None
        source_events = [event["event_id"]]
        source_agents = [source_agent]
        created_at = now_iso()
        if existing:
            old_text = existing.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(old_text)
            if str(fm.get("type")) != "task" or not can_read_record(root, fm, source_agent):
                raise PermissionError(f"Agent '{source_agent}' is not authorized to update task '{rid}'")
            created_at = fm.get("created_at", created_at)
            old_events = fm.get("source_event_ids", [])
            if isinstance(old_events, list):
                source_events = list(dict.fromkeys(old_events + source_events))
            old_agents = fm.get("source_agents", [])
            if isinstance(old_agents, list):
                source_agents = list(dict.fromkeys(old_agents + source_agents))
        body = f"# {title}\n\n## 状态\n{status}\n"
        if project:
            body += f"\n## 项目\n{project}\n"
        if due:
            body += f"\n## 截止\n{due}\n"
        if details.strip():
            body += f"\n## 说明\n{details.strip()}\n"
        body += f"\n## 最近来源\n- {event['event_id']}\n"
        path = existing or _canonical_path(root, "task", rid, title, project)
        meta = {**(fm if existing else {}), **{
            "schema": "pkh-canonical-v3", "id": rid, "type": "task", "status": status,
            "created_at": created_at, "updated_at": now_iso(), "privacy": privacy,
            "projects": [project] if project else [], "source_event_ids": source_events,
            "source_agents": source_agents, "due": due, "confidence": "high",
            "maturity": (fm if existing else {}).get("maturity", "L2"),
            "visibility": (fm if existing else {}).get("visibility", "work"),
            "context_scopes": (fm if existing else {}).get("context_scopes", ["work"]),
            "allowed_agents": (fm if existing else {}).get("allowed_agents", []),
            "denied_agents": (fm if existing else {}).get("denied_agents", []),
            "origin": "user_or_work_record",
            "supersedes": [], "related": [],
        }}
        atomic_write_text(path, _frontmatter(meta) + body.rstrip() + "\n")
    mark_event(root, event["event_id"], status="processed", canonical_id=rid)
    rebuild_json_index(root)
    return {"id": rid, "path": str(path.relative_to(root)).replace("\\", "/"), "type": "task", "source_event_id": event["event_id"]}


OPEN_TASK_STATUSES = ("open", "in_progress", "blocked")
ALL_TASK_STATUSES = ("open", "in_progress", "blocked", "done", "cancelled")


def list_work_items(
    root: Path,
    *,
    project_ids: list[str] | None = None,
    statuses: list[str] | None = None,
    page_size: int = 50,
    cursor: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Enumerate task records with stable-snapshot pagination.

    Contract (docs/首批接入与字段契约-v0.1.md §3): permission scope comes from the
    bound identity only; items are sorted by (updated_at DESC, id ASC); the cursor
    binds to a snapshot hash so pages never mix two snapshots - a changed snapshot
    yields an explicit SNAPSHOT_EXPIRED error instead of silently shifted windows.
    """
    page_size = max(1, min(int(page_size), 200))
    wanted_statuses = tuple(str(s) for s in statuses) if statuses else OPEN_TASK_STATUSES
    for s in wanted_statuses:
        if s not in ALL_TASK_STATUSES:
            raise ValueError(f"Unsupported task status: {s}")

    # Registry once, so callers may filter by stable project id or by name/alias.
    id_to_names: dict[str, str] = {}
    for row in list_projects(root, agent_id=agent_id):
        names = {str(row.get("name", "")).strip()}
        for a in row.get("aliases", []) or []:
            names.add(str(a).strip())
        names.discard("")
        id_to_names[str(row.get("id", ""))] = "|".join(sorted(names))
    filter_names: set[str] | None = None
    if project_ids:
        filter_names = set()
        for pid in project_ids:
            if pid in id_to_names:
                filter_names.update(id_to_names[pid].split("|"))
                filter_names.add(pid)
            else:
                filter_names.add(str(pid))  # allow direct name matching too

    rows: list[dict[str, Any]] = []
    for folder in CANONICAL_FOLDERS:
        base = root / folder
        if not base.exists():
            continue
        for p in base.rglob("*.md"):
            if p.name in {"INDEX.md", "TASKS.md", "00_SELF_CONTEXT.md"}:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            if str(fm.get("type")) != "task":
                continue
            if not can_read_record(root, fm, agent_id, include_self=False):
                continue
            status = str(fm.get("status") or "open")
            if status not in wanted_statuses:
                continue
            proj_names = _list_value(fm, "projects")
            if filter_names is not None and not any(n in filter_names for n in proj_names):
                continue
            title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
            updated = str(fm.get("updated_at") or fm.get("created_at") or "")
            rows.append({
                "id": str(fm.get("id") or p.stem),
                "title": title,
                "status": status,
                "projects": proj_names,
                "created_at": fm.get("created_at"),
                "updated_at": fm.get("updated_at"),
                "due": fm.get("due"),
                "source_agents": _list_value(fm, "source_agents"),
                "source_ref": str(p.relative_to(root)).replace("\\", "/"),
            })
    # Stable snapshot order: updated_at DESC, id ASC. Python sorts are stable,
    # so sort by the secondary key first, then the primary.
    rows.sort(key=lambda r: r["id"], reverse=False)
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)

    ids = "|".join(r["id"] for r in rows)
    snapshot_id = hashlib.sha1(f"{ids}::{sorted(wanted_statuses)}::{sorted(filter_names or [])}".encode("utf-8")).hexdigest()[:12]

    offset = 0
    if cursor:
        try:
            payload = json.loads(base64.b64decode(cursor.encode("ascii")).decode("utf-8"))
            if str(payload.get("s")) != snapshot_id:
                return {"error": {"code": "SNAPSHOT_EXPIRED", "message": "snapshot changed; restart pagination"}}
            offset = max(0, int(payload.get("o", 0)))
        except Exception:
            return {"error": {"code": "INVALID_CURSOR", "message": "cursor is malformed"}}

    page = rows[offset : offset + page_size]
    next_cursor = None
    if offset + page_size < len(rows):
        next_cursor = base64.b64encode(
            json.dumps({"s": snapshot_id, "o": offset + page_size}).encode("utf-8")
        ).decode("ascii")
    return {
        "items": page,
        "next_cursor": next_cursor,
        "total": len(rows),
        "snapshot_id": snapshot_id,
        "generated_at": now_iso(),
        "coverage": {"state": "complete", "statuses": list(wanted_statuses)},
    }


def upsert_project(
    root: Path,
    *,
    project: str,
    summary: str,
    status: str = "active",
    update: str = "",
    source_app: str = "mcp",
    source_agent: str = "unknown",
    privacy: str = "private",
    project_id: str | None = None,
) -> dict[str, Any]:
    _require_project_write_access(root, project, source_agent, allow_create=True)
    event = capture_event(
        root,
        text=f"#项目:{project} {summary}\n{update}".strip(),
        source_app=source_app,
        source_agent=source_agent,
        privacy=privacy,
        explicit=True,
        routing_hints=[f"project:{project}"],
    )
    path = _canonical_path(root, "project", "", project, project)
    with lock_for(root):
        rid = project_id or new_id("project")
        created = now_iso()
        old_log = ""
        old_text = ""
        old_meta: dict[str, Any] = {}
        old_events: list[str] = []
        old_agents: list[str] = []
        if path.exists():
            old_text = path.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(old_text)
            if str(fm.get("type")) != "project" or not can_read_record(root, fm, source_agent):
                raise PermissionError(f"Agent '{source_agent}' is not authorized to update project '{project}'")
            old_meta = fm
            rid = fm.get("id", rid)
            created = fm.get("created_at", created)
            if isinstance(fm.get("source_event_ids"), list):
                old_events = fm["source_event_ids"]
            if isinstance(fm.get("source_agents"), list):
                old_agents = fm["source_agents"]
            old_log = _markdown_section(old_text, "Update Log")
        event_ids = list(dict.fromkeys(old_events + [event["event_id"]]))
        agents = list(dict.fromkeys(old_agents + [source_agent]))
        body = _document_body(old_text) if old_text else f"# {project}\n"
        body = _set_markdown_section(body, "Current Summary", summary.strip())
        body = _set_markdown_section(body, "Status", status)
        log = old_log
        if update.strip():
            log = f"- {now_iso()}: {update.strip()} (source: {event['event_id']})\n" + log
        body = _set_markdown_section(body, "Update Log", log)
        meta = {**old_meta, **{
            "schema": "pkh-canonical-v3", "id": rid, "type": "project", "status": status,
            "created_at": created, "updated_at": now_iso(), "privacy": privacy,
            "projects": [project], "source_event_ids": event_ids, "source_agents": agents,
            "confidence": "high", "maturity": old_meta.get("maturity", "L2"),
            "visibility": old_meta.get("visibility", "work"),
            "context_scopes": old_meta.get("context_scopes", ["work"]),
            "allowed_agents": old_meta.get("allowed_agents", []),
            "denied_agents": old_meta.get("denied_agents", []),
            "origin": "user_or_work_record", "supersedes": [], "related": [],
        }}
        atomic_write_text(path, _frontmatter(meta) + body.rstrip() + "\n")
    mark_event(root, event["event_id"], status="processed", canonical_id=rid)
    rebuild_json_index(root)
    return {"id": rid, "path": str(path.relative_to(root)).replace("\\", "/"), "type": "project", "source_event_id": event["event_id"]}


PROJECT_REGISTRY_SCHEMA = "1.0"


def _project_registry_path(root: Path) -> Path:
    return root / "00_System" / "PROJECT_REGISTRY.json"


def _normalize_project_root(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("Project root path cannot be empty")
    return str(Path(raw).expanduser().resolve(strict=False)).replace("\\", "/").rstrip("/")


def _path_key(value: str) -> str:
    return _normalize_project_root(value).casefold()


def _load_project_registry(root: Path) -> dict[str, Any]:
    path = _project_registry_path(root)
    if not path.exists():
        return {"schema_version": PROJECT_REGISTRY_SCHEMA, "projects": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("projects", []), list):
        raise ValueError(f"Invalid project registry: {path}")
    data.setdefault("schema_version", PROJECT_REGISTRY_SCHEMA)
    data.setdefault("projects", [])
    return data


def _project_record_path(root: Path, project: dict[str, Any]) -> Path:
    name = str(project.get("name") or "").strip()
    return _canonical_path(root, "project", "", name, name)


def _can_access_project(root: Path, project: dict[str, Any], agent_id: str | None) -> bool:
    if agent_id is None:
        return True
    path = _project_record_path(root, project)
    if not path.exists():
        return False
    meta = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
    return can_read_record(root, meta, agent_id, include_self=False)


def list_projects(
    root: Path, status: str | None = None, *, agent_id: str | None = None,
) -> list[dict[str, Any]]:
    projects = _load_project_registry(root).get("projects", [])
    rows = [dict(item) for item in projects if isinstance(item, dict)]
    if status:
        rows = [item for item in rows if item.get("status") == status]
    rows = [item for item in rows if _can_access_project(root, item, agent_id)]
    rows.sort(key=lambda item: (str(item.get("status") != "active"), str(item.get("name", "")).casefold()))
    return rows


def resolve_project(
    root: Path,
    *,
    cwd: str | None = None,
    project_hint: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    projects = list_projects(root, agent_id=agent_id)
    hint_match: dict[str, Any] | None = None
    path_match: dict[str, Any] | None = None
    matched_root: str | None = None

    hint = str(project_hint or "").strip().casefold()
    if hint:
        matches = []
        for item in projects:
            keys = [str(item.get("id", "")), str(item.get("name", ""))]
            keys.extend(str(alias) for alias in item.get("aliases", []) if alias)
            if hint in {key.casefold() for key in keys if key}:
                matches.append(item)
        if len(matches) > 1:
            return {"resolved": False, "reason": "ambiguous_project_hint", "project_hint": project_hint, "matches": matches}
        if matches:
            hint_match = matches[0]

    if cwd:
        cwd_key = _path_key(cwd)
        candidates: list[tuple[int, str, dict[str, Any]]] = []
        for item in projects:
            for root_path in item.get("root_paths", []):
                root_key = _path_key(str(root_path))
                if cwd_key == root_key or cwd_key.startswith(root_key + "/"):
                    candidates.append((len(root_key), str(root_path), item))
        if candidates:
            candidates.sort(key=lambda row: row[0], reverse=True)
            best_len = candidates[0][0]
            best = [row for row in candidates if row[0] == best_len]
            best_ids = {str(row[2].get("id")) for row in best}
            if len(best_ids) > 1:
                return {"resolved": False, "reason": "ambiguous_root_path", "cwd": cwd, "matches": [row[2] for row in best]}
            _, matched_root, path_match = best[0]

    if hint_match and path_match and hint_match.get("id") != path_match.get("id"):
        return {
            "resolved": False,
            "reason": "project_hint_cwd_conflict",
            "project_hint": project_hint,
            "cwd": cwd,
            "hint_project": hint_match,
            "cwd_project": path_match,
        }
    project = hint_match or path_match
    if not project:
        return {
            "resolved": False,
            "reason": "project_not_registered",
            "project_hint": project_hint,
            "cwd": cwd,
            "action_required": "Call register_project with an explicit stable project name and root path before writing project memory.",
        }
    return {
        "resolved": True,
        "matched_by": "project_hint+cwd" if hint_match and path_match else ("project_hint" if hint_match else "cwd"),
        "matched_root": matched_root,
        "project": project,
    }


def _register_project_locked(
    root: Path,
    *,
    project: str,
    summary: str,
    root_paths: list[str] | None = None,
    aliases: list[str] | None = None,
    repository_urls: list[str] | None = None,
    status: str = "active",
    source_app: str = "mcp",
    source_agent: str = "unknown",
) -> dict[str, Any]:
    name = project.strip()
    if not name:
        raise ValueError("Project name cannot be empty")
    if not summary.strip():
        raise ValueError("Project summary cannot be empty")
    normalized_roots = list(dict.fromkeys(_normalize_project_root(path) for path in (root_paths or [])))
    normalized_aliases = list(dict.fromkeys(alias.strip() for alias in (aliases or []) if alias.strip()))
    urls = list(dict.fromkeys(url.strip() for url in (repository_urls or []) if url.strip()))

    with lock_for(root):
        registry = _load_project_registry(root)
        projects = registry.setdefault("projects", [])
        name_key = name.casefold()
        existing = next((item for item in projects if str(item.get("name", "")).casefold() == name_key or name_key in {str(x).casefold() for x in item.get("aliases", [])}), None)
        root_owners: dict[str, str] = {}
        for item in projects:
            for path in item.get("root_paths", []):
                root_owners[_path_key(str(path))] = str(item.get("id"))
        if existing:
            project_id = str(existing.get("id"))
            if not _can_access_project(root, existing, source_agent):
                raise PermissionError(f"Agent '{source_agent}' is not authorized to update project {name}")
        else:
            project_path = _canonical_path(root, "project", "", name, name)
            if project_path.exists():
                old_project_meta = _parse_frontmatter(project_path.read_text(encoding="utf-8", errors="replace"))
                if not can_read_record(root, old_project_meta, source_agent):
                    raise PermissionError(f"Agent '{source_agent}' is not authorized to register project {name}")
                project_id = str(old_project_meta.get("id") or new_id("project"))
            else:
                project_id = new_id("project")
        identity_owners: dict[str, str] = {}
        for item in projects:
            owner = str(item.get("id"))
            identities = [str(item.get("name", "")), *(str(x) for x in item.get("aliases", []))]
            for identity in identities:
                if identity:
                    identity_owners[identity.casefold()] = owner
        identity_conflicts = [
            identity for identity in [name, *normalized_aliases]
            if identity.casefold() in identity_owners and identity_owners[identity.casefold()] != project_id
        ]
        if identity_conflicts:
            raise ValueError(f"Project name/alias already belongs to another project: {identity_conflicts}")
        conflicts = [path for path in normalized_roots if _path_key(path) in root_owners and root_owners[_path_key(path)] != project_id]
        if conflicts:
            raise ValueError(f"Project root path already belongs to another project: {conflicts}")
        now = now_iso()
        if existing:
            existing["aliases"] = list(dict.fromkeys([*existing.get("aliases", []), *normalized_aliases]))
            existing["root_paths"] = list(dict.fromkeys([*existing.get("root_paths", []), *normalized_roots]))
            existing["repository_urls"] = list(dict.fromkeys([*existing.get("repository_urls", []), *urls]))
            existing["status"] = status
            existing["updated_at"] = now
        else:
            existing = {
                "id": project_id,
                "name": name,
                "aliases": normalized_aliases,
                "root_paths": normalized_roots,
                "repository_urls": urls,
                "status": status,
                "created_at": now,
                "updated_at": now,
            }
            projects.append(existing)
        registry["schema_version"] = PROJECT_REGISTRY_SCHEMA

    canonical_name = str(existing["name"])
    project_path = _canonical_path(root, "project", "", canonical_name, canonical_name)
    if project_path.exists():
        text = project_path.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        fm["project_roots"] = existing.get("root_paths", [])
        fm["project_aliases"] = existing.get("aliases", [])
        fm["repository_urls"] = existing.get("repository_urls", [])
        fm["updated_at"] = now_iso()
        _rewrite_record(root, project_path, fm, _document_body(text))
        rebuild_json_index(root)
        canonical = {"id": project_id, "path": str(project_path.relative_to(root)).replace("\\", "/"), "type": "project", "source_event_id": None}
    else:
        canonical = upsert_project(
            root, project=canonical_name, summary=summary, status=status,
            update="Registered as a shared cross-Agent project.",
            source_app=source_app, source_agent=source_agent, privacy="private", project_id=project_id,
        )
        text = project_path.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        fm["project_roots"] = existing.get("root_paths", [])
        fm["project_aliases"] = existing.get("aliases", [])
        fm["repository_urls"] = existing.get("repository_urls", [])
        _rewrite_record(root, project_path, fm, _document_body(text))
        rebuild_json_index(root)
    # Publish the registry entry only after the canonical project record is
    # durable. If the process stops earlier, a retry can recover the orphaned
    # canonical record; readers never see a registry row pointing at nothing.
    with lock_for(root):
        atomic_write_json(_project_registry_path(root), registry)
    return {"project": existing, "canonical": canonical}


def register_project(
    root: Path,
    *,
    project: str,
    summary: str,
    root_paths: list[str] | None = None,
    aliases: list[str] | None = None,
    repository_urls: list[str] | None = None,
    status: str = "active",
    source_app: str = "mcp",
    source_agent: str = "unknown",
) -> dict[str, Any]:
    with project_registry_lock_for(root):
        return _register_project_locked(
            root,
            project=project,
            summary=summary,
            root_paths=root_paths,
            aliases=aliases,
            repository_urls=repository_urls,
            status=status,
            source_app=source_app,
            source_agent=source_agent,
        )


def update_project_registration(
    root: Path,
    *,
    project: str,
    add_root_paths: list[str] | None = None,
    remove_root_paths: list[str] | None = None,
    add_aliases: list[str] | None = None,
    remove_aliases: list[str] | None = None,
    add_repository_urls: list[str] | None = None,
    remove_repository_urls: list[str] | None = None,
    status: str | None = None,
    source_agent: str = "unknown",
) -> dict[str, Any]:
    """Update project registration without leaving stale roots or aliases behind."""
    with project_registry_lock_for(root):
        resolved = resolve_project(root, project_hint=project, agent_id=source_agent)
        if not resolved.get("resolved"):
            raise ValueError(f"Cannot update project registration: {resolved.get('reason')}")
        current = dict(resolved["project"])
        project_id = str(current["id"])
        add_roots = [_normalize_project_root(value) for value in _as_list(add_root_paths)]
        remove_root_keys = {_path_key(value) for value in _as_list(remove_root_paths)}
        add_alias_rows = _as_list(add_aliases)
        remove_alias_keys = {value.casefold() for value in _as_list(remove_aliases)}
        add_urls = _as_list(add_repository_urls)
        remove_url_keys = {value.casefold() for value in _as_list(remove_repository_urls)}

        roots = [
            str(value) for value in current.get("root_paths", [])
            if _path_key(str(value)) not in remove_root_keys
        ]
        roots = list(dict.fromkeys([*roots, *add_roots]))
        if not roots:
            raise ValueError("A registered project must retain at least one stable root path")
        aliases = [
            str(value) for value in current.get("aliases", [])
            if str(value).casefold() not in remove_alias_keys
        ]
        aliases = list(dict.fromkeys([*aliases, *add_alias_rows]))
        urls = [
            str(value) for value in current.get("repository_urls", [])
            if str(value).casefold() not in remove_url_keys
        ]
        urls = list(dict.fromkeys([*urls, *add_urls]))

        registry = _load_project_registry(root)
        for item in registry.get("projects", []):
            if str(item.get("id")) == project_id:
                continue
            other_id = str(item.get("id"))
            other_identities = {
                str(value).casefold()
                for value in [item.get("name", ""), *item.get("aliases", [])]
                if value
            }
            conflicts = [value for value in aliases if value.casefold() in other_identities]
            if conflicts:
                raise ValueError(f"Project aliases already belong to {other_id}: {conflicts}")
            other_roots = {_path_key(str(value)) for value in item.get("root_paths", [])}
            root_conflicts = [value for value in roots if _path_key(value) in other_roots]
            if root_conflicts:
                raise ValueError(f"Project roots already belong to {other_id}: {root_conflicts}")

        updated = {
            **current,
            "aliases": aliases,
            "root_paths": roots,
            "repository_urls": urls,
            "status": status or str(current.get("status") or "active"),
            "updated_at": now_iso(),
        }
        project_path = _project_record_path(root, updated)
        if not project_path.exists():
            raise FileNotFoundError(f"Project record not found: {project_path}")
        text = project_path.read_text(encoding="utf-8", errors="replace")
        meta = _parse_frontmatter(text)
        if not can_read_record(root, meta, source_agent, include_self=False):
            raise PermissionError(f"Agent '{source_agent}' is not authorized to update {project}")
        meta.update({
            "project_roots": roots,
            "project_aliases": aliases,
            "repository_urls": urls,
            "status": updated["status"],
        })
        _rewrite_record(root, project_path, meta, _document_body(text))
        with lock_for(root):
            latest = _load_project_registry(root)
            rows = latest.setdefault("projects", [])
            for index, item in enumerate(rows):
                if str(item.get("id")) == project_id:
                    rows[index] = updated
                    break
            else:
                raise ValueError(f"Project disappeared during update: {project_id}")
            atomic_write_json(_project_registry_path(root), latest)
        rebuild_json_index(root)
        return {"project": updated, "canonical_path": str(project_path.relative_to(root)).replace("\\", "/")}


def checkpoint_lock_for(root: Path, project_id: str, checkpoint_key: str) -> FileLock:
    digest = sha256_bytes(f"{project_id}\n{checkpoint_key}".encode("utf-8"))
    return FileLock(
        root / ".state" / "checkpoint-locks" / f"{digest}.lock",
        timeout=60.0,
        stale_after=600.0,
    )


def _checkpoint_transaction_path(root: Path, project_id: str, checkpoint_key: str) -> Path:
    digest = sha256_bytes(f"{project_id}\n{checkpoint_key}".encode("utf-8"))
    return root / ".state" / "project-checkpoints" / _safe_slug(project_id) / f"{digest}.json"


def _checkpoint_rows(
    root: Path,
    project_name: str,
    limit: int | None = 20,
    *,
    agent_id: str | None = None,
) -> list[dict[str, Any]]:
    base = root / "20_Projects" / _safe_slug(project_name) / "checkpoints"
    rows: list[dict[str, Any]] = []
    if not base.exists():
        return rows
    for path in base.rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        if fm.get("type") != "project_checkpoint":
            continue
        if not can_read_record(root, fm, agent_id, include_self=False):
            continue
        title = next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), path.stem)
        rows.append({
            "id": fm.get("id"), "title": title, "project_id": fm.get("project_id"),
            "checkpoint_key": fm.get("checkpoint_key"), "source_agents": fm.get("source_agents", []),
            "source_app": fm.get("source_app"), "session_id": fm.get("session_id"),
            "sequence": fm.get("checkpoint_sequence"),
            "created_at": fm.get("created_at"), "updated_at": fm.get("updated_at"),
            "path": str(path.relative_to(root)).replace("\\", "/"),
            "summary": fm.get("summary"),
        })
    rows.sort(
        key=lambda row: (
            int(str(row.get("sequence") or "0")),
            str(row.get("updated_at") or row.get("created_at") or ""),
        ),
        reverse=True,
    )
    if limit is None:
        return rows
    return rows[:max(1, min(limit, 200))]


def get_project_timeline(
    root: Path,
    *,
    cwd: str | None = None,
    project: str | None = None,
    limit: int = 20,
    agent_id: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_project(root, cwd=cwd, project_hint=project, agent_id=agent_id)
    if not resolved.get("resolved"):
        return {**resolved, "items": []}
    entry = resolved["project"]
    return {
        **resolved,
        "items": _checkpoint_rows(root, str(entry["name"]), limit=limit, agent_id=agent_id),
    }


def start_project_session(
    root: Path,
    *,
    cwd: str | None = None,
    project: str | None = None,
    query: str | None = None,
    limit: int = 8,
    agent_id: str | None = None,
) -> dict[str, Any]:
    from onboarding import get_onboarding

    resolved = resolve_project(root, cwd=cwd, project_hint=project, agent_id=agent_id)
    profile = get_user_profile(root, agent_id=agent_id)
    onboarding = get_onboarding(root, agent_id=agent_id, profile=profile)
    if not resolved.get("resolved"):
        return {**resolved, "project_record": "", "recent_checkpoints": [], "related_records": [], "user_profile": profile, "onboarding": onboarding}
    entry = resolved["project"]
    name = str(entry["name"])
    path = _canonical_path(root, "project", "", name, name)
    project_record = ""
    if path.exists():
        project_text = path.read_text(encoding="utf-8", errors="replace")
        project_meta = _parse_frontmatter(project_text)
        if not can_read_record(root, project_meta, agent_id, include_self=False):
            return {
                "resolved": False,
                "reason": "project_access_denied",
                "project_record": "",
                "recent_checkpoints": [],
                "related_records": [],
            }
        project_record = project_text[:16000]
    search_query = (query or name).strip()
    related = search_knowledge(root, query=search_query, project=name, limit=limit, agent_id=agent_id, include_self=False)
    return {
        **resolved,
        "project_record": project_record,
        "recent_checkpoints": _checkpoint_rows(root, name, limit=limit, agent_id=agent_id),
        "related_records": related,
        "user_profile": profile,
        "onboarding": onboarding,
        "read_only": True,
    }


def _as_list(values: list[str] | None) -> list[str]:
    return [str(value).strip() for value in (values or []) if str(value).strip()]


def _checkpoint_body(title: str, summary: str, sections: list[tuple[str, list[str]]]) -> str:
    body = f"# {title}\n\n## Current State\n{summary.strip()}\n"
    for heading, values in sections:
        body += f"\n## {heading}\n"
        body += "".join(f"- {value}\n" for value in values) if values else "- None recorded.\n"
    return body


def _update_project_handoff(
    root: Path,
    *,
    entry: dict[str, Any],
    checkpoint: dict[str, Any],
    event_id: str,
    source_agent: str,
    title: str,
    summary: str,
    open_items: list[str],
    project_status: str,
    checkpoint_sequence: int,
    updated_at: str,
) -> bool:
    name = str(entry["name"])
    project_path = _project_record_path(root, entry)
    if not project_path.exists():
        raise FileNotFoundError(f"Project record not found: {project_path}")
    with lock_for(root):
        old_text = project_path.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(old_text)
        if not can_read_record(root, fm, source_agent, include_self=False):
            raise PermissionError(f"Agent '{source_agent}' is not authorized to update {name}")
        latest_id = str(fm.get("latest_checkpoint_id") or "")
        if latest_id and latest_id != checkpoint["id"]:
            latest_path = _find_record_by_id(root, latest_id)
            if latest_path:
                latest_meta = _parse_frontmatter(latest_path.read_text(encoding="utf-8", errors="replace"))
                latest_sequence = int(str(latest_meta.get("checkpoint_sequence") or "0"))
                if latest_sequence > checkpoint_sequence:
                    return False
        old_log = _markdown_section(old_text, "Update Log")
        event_ids = list(dict.fromkeys([*_list_value(fm, "source_event_ids"), event_id]))
        agents = list(dict.fromkeys([*_list_value(fm, "source_agents"), source_agent]))
        fm.update({
            "status": project_status,
            "updated_at": updated_at,
            "source_event_ids": event_ids,
            "source_agents": agents,
            "latest_checkpoint_id": checkpoint["id"],
        })
        handoff = (
            f"- checkpoint: {checkpoint['id']}\n"
            f"- title: {title}\n"
            f"- summary: {summary}\n"
            f"- agent: {source_agent}\n"
            f"- updated_at: {updated_at}\n"
        )
        if open_items:
            handoff += "- open_items: " + " | ".join(open_items) + "\n"
        # Checkpoints describe a task. Only explicit upsert_project calls change
        # the stable baseline; retain custom sections and manually maintained links.
        body = _set_markdown_section(_document_body(old_text), "Status", project_status)
        body = _set_markdown_section(body, "Latest Handoff", handoff)
        log_entry = f"- {updated_at}: {title} (checkpoint: {checkpoint['id']}, agent: {source_agent})"
        log = log_entry + "\n"
        if old_log:
            old_rows = [row for row in old_log.splitlines() if checkpoint["id"] not in row]
            if old_rows:
                log += "\n".join(old_rows).rstrip() + "\n"
        body = _set_markdown_section(body, "Update Log", log)
        atomic_write_text(project_path, _frontmatter(fm) + body.rstrip() + "\n")
    return True


def record_project_checkpoint(
    root: Path,
    *,
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
    source_agent: str = "unknown",
) -> dict[str, Any]:
    if not title.strip() or not summary.strip():
        raise ValueError("Checkpoint title and summary are required")
    resolved = resolve_project(root, cwd=cwd, project_hint=project, agent_id=source_agent)
    if not resolved.get("resolved"):
        raise ValueError(f"Cannot record checkpoint: {resolved.get('reason')}. {resolved.get('action_required', '')}".strip())
    entry = resolved["project"]
    name = str(entry["name"])
    project_id = str(entry["id"])
    stable_key = (checkpoint_key or "").strip()
    if not stable_key:
        raw = "\n".join([project_id, source_agent, session_id or "", title.strip(), summary.strip()])
        stable_key = "cpk_" + sha256_bytes(raw.encode("utf-8"))[:24]
    change_rows = _as_list(changes)
    decision_rows = _as_list(decisions)
    validation_rows = _as_list(validations)
    open_rows = _as_list(open_items)
    file_rows = _as_list(files)
    transaction_path = _checkpoint_transaction_path(root, project_id, stable_key)
    with checkpoint_lock_for(root, project_id, stable_key):
        had_transaction = transaction_path.exists()
        state = json.loads(transaction_path.read_text(encoding="utf-8")) if had_transaction else {}
        for row in _checkpoint_rows(root, name, limit=None, agent_id=source_agent):
            if row.get("checkpoint_key") == stable_key:
                # A durable canonical file is not proof that publication finished.
                if not had_transaction or state.get("phase") == "complete":
                    return {**row, "project": entry, "idempotent_replay": True}
                break

        if had_transaction:
            if state.get("phase") != "complete":
                state["recovered"] = True
        else:
            created_at = now_iso()
            digest = sha256_bytes(f"{project_id}\n{stable_key}".encode("utf-8"))
            state = {
                "schema_version": "1.0",
                "operation": "record_project_checkpoint",
                "phase": "reserved",
                "project_id": project_id,
                "project_name": name,
                "checkpoint_key": stable_key,
                "checkpoint_id": f"CP-{datetime.fromisoformat(created_at).strftime('%Y%m%d')}-{digest[:8]}",
                "event_id": f"evt_{digest[:32]}",
                "created_at": created_at,
                "checkpoint_sequence": time.time_ns(),
                "request": {
                    "title": title.strip(), "summary": summary.strip(),
                    "changes": change_rows, "decisions": decision_rows,
                    "validations": validation_rows, "open_items": open_rows,
                    "files": file_rows, "git_ref": git_ref, "session_id": session_id,
                    "cwd": cwd, "project_status": project_status,
                    "source_app": source_app, "source_agent": source_agent,
                },
            }
            atomic_write_json(transaction_path, state)

        request = state["request"]
        event_id = str(state["event_id"])
        checkpoint_id = str(state["checkpoint_id"])
        created_at = str(state["created_at"])
        checkpoint_sequence = int(state["checkpoint_sequence"])
        text_parts = [
            f"#项目:{name} #项目交接 {request['title']}",
            request["summary"],
        ]
        for label, values in (
            ("Changes", request["changes"]), ("Decisions", request["decisions"]),
            ("Validations", request["validations"]), ("Open Items", request["open_items"]),
            ("Files", request["files"]),
        ):
            if values:
                text_parts.append(label + ":\n" + "\n".join(f"- {value}" for value in values))
        try:
            event_path = _event_path(root, event_id)
            event = {
                "event_id": event_id,
                "path": str(event_path.relative_to(root)).replace("\\", "/"),
                "event": json.loads(event_path.read_text(encoding="utf-8")),
            }
        except FileNotFoundError:
            event = capture_event(
                root, text="\n\n".join(text_parts), source_app=request["source_app"],
                source_agent=request["source_agent"], session_id=request["session_id"],
                cwd=request["cwd"], privacy="private", explicit=True,
                routing_hints=[f"project:{name}", "project_checkpoint"],
                access={"context_scopes": ["work"], "allowed_agents": [], "denied_agents": []},
                provenance={"speaker_scope": "agent", "user_authorship": "not_applicable", "claim_role": "project_handoff"},
                triage={"lane": "work_project", "confidence": "high", "requires_review": False},
                event_id=event_id, created_at_override=created_at,
            )
        state["phase"] = "event_written"
        atomic_write_json(transaction_path, state)

        sections = [
            ("Changes", request["changes"]), ("Decisions", request["decisions"]),
            ("Validations", request["validations"]), ("Open Items", request["open_items"]),
            ("Files", request["files"]),
        ]
        if request["git_ref"]:
            sections.append(("Git State", [request["git_ref"]]))
        checkpoint_path = _find_record_by_id(root, checkpoint_id)
        if checkpoint_path:
            checkpoint = {
                "id": checkpoint_id,
                "path": str(checkpoint_path.relative_to(root)).replace("\\", "/"),
                "type": "project_checkpoint",
            }
        else:
            checkpoint = _write_canonical(
                root, record_type="project_checkpoint", title=request["title"],
                body=_checkpoint_body(request["title"], request["summary"], sections),
                project=name, record_id=checkpoint_id,
                source_event_ids=[event_id], source_agents=[request["source_agent"]],
                privacy="private", visibility="project", context_scopes=["work"],
                maturity="L2", origin="agent_project_handoff",
                extra_meta={
                    "project_id": project_id, "checkpoint_key": stable_key,
                    "summary": request["summary"], "checkpoint_sequence": checkpoint_sequence,
                    "source_app": request["source_app"], "session_id": request["session_id"],
                    "cwd": request["cwd"], "git_ref": request["git_ref"],
                },
            )
        state["phase"] = "checkpoint_written"
        state["checkpoint_path"] = checkpoint["path"]
        atomic_write_json(transaction_path, state)

        mark_event(root, event_id, status="processed", canonical_id=checkpoint_id)
        state["phase"] = "event_processed"
        atomic_write_json(transaction_path, state)
        _update_project_handoff(
            root, entry=entry, checkpoint=checkpoint, event_id=event_id,
            source_agent=request["source_agent"], title=request["title"],
            summary=request["summary"], open_items=request["open_items"],
            project_status=request["project_status"], checkpoint_sequence=checkpoint_sequence,
            updated_at=created_at,
        )
        state["phase"] = "project_updated"
        atomic_write_json(transaction_path, state)
        rebuild_json_index(root, require_search_index=True)
        state["phase"] = "complete"
        state["completed_at"] = now_iso()
        atomic_write_json(transaction_path, state)
        return {
            **checkpoint,
            "project": entry,
            "source_event_id": event_id,
            "checkpoint_key": stable_key,
            "source_agents": [request["source_agent"]],
            "source_app": request["source_app"],
            "idempotent_replay": False,
            "transaction_recovered": bool(state.get("recovered")),
        }


def _document_body(text: str) -> str:
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 4)
    return text[end + 5:] if end >= 0 else text


def _rewrite_record(root: Path, path: Path, meta: dict[str, Any], body: str) -> None:
    meta["updated_at"] = now_iso()
    with lock_for(root):
        atomic_write_text(path, _frontmatter(meta) + body.lstrip().rstrip() + "\n")


def record_self_observation(
    root: Path,
    *,
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
    privacy: str = "sensitive",
    allowed_agents: list[str] | None = None,
    share_with_work: bool = False,
) -> dict[str, Any]:
    """Create a provenance-backed Self Model record without turning an Agent inference into a user fact."""
    if category not in SELF_TYPES:
        raise ValueError(f"Unsupported self category: {category}")
    if not agent_policy(root, source_agent).get("self_access", False):
        raise PermissionError(f"Agent '{source_agent}' is not authorized to create canonical Self Model records; capture an Inbox event instead")
    if confidence not in {"low", "medium", "high"}:
        raise ValueError("confidence must be low/medium/high")
    if origin not in {"user_statement", "user_reflection", "agent_inference", "external_observation"}:
        raise ValueError("Unsupported self origin")
    if origin == "agent_inference" and user_confirmed:
        raise ValueError("Agent inference cannot be user_confirmed at creation; confirm it through confirm_self_record")
    if share_with_work:
        raise ValueError(
            "Self records must remain in self scope; publish a separately confirmed, "
            "audience-scoped work preference instead"
        )
    event = capture_event(
        root,
        text=f"#自我认知 #{category} {title}\n\n{statement}".strip(),
        source_app=source_app,
        source_agent=source_agent,
        privacy=privacy,
        explicit=True,
        routing_hints=["self", category],
        access={"context_scopes": ["self"], "allowed_agents": allowed_agents or [], "denied_agents": []},
    )
    evidence_ids = list(dict.fromkeys((evidence or []) + [event["event_id"]]))
    maturity = "L3" if user_confirmed else ("L1" if origin == "agent_inference" else "L2")
    body = f"# {title}\n\n## Statement\n{statement.strip()}\n"
    body += f"\n## Epistemic State\n- origin: {origin}\n- confidence: {confidence}\n- user_confirmed: {str(user_confirmed).lower()}\n- maturity: {maturity}\n"
    if evidence_ids:
        body += "\n## Evidence\n" + "".join(f"- {x}\n" for x in evidence_ids)
    if counter_evidence:
        body += "\n## Counter Evidence\n" + "".join(f"- {x}\n" for x in counter_evidence)
    body += "\n## Change Log\n- " + now_iso() + ": created\n"
    result = _write_canonical(
        root,
        record_type=category,
        title=title,
        body=body,
        project=None,
        source_event_ids=[event["event_id"]],
        source_agents=[source_agent],
        privacy=privacy,
        confidence=confidence,
        maturity=maturity,
        visibility="private",
        context_scopes=["self"],
        allowed_agents=allowed_agents or [],
        origin=origin,
        user_confirmed=user_confirmed,
        evidence_event_ids=evidence_ids,
        counter_evidence=counter_evidence or [],
        extra_meta={"first_observed": now_iso(), "last_observed": now_iso(), "shared_from_self": False},
    )
    mark_event(root, event["event_id"], status="processed", canonical_id=result["id"])
    rebuild_json_index(root)
    return {**result, "source_event_id": event["event_id"], "maturity": maturity, "user_confirmed": user_confirmed}


def confirm_self_record(
    root: Path,
    *,
    record_id: str,
    confirmation_note: str = "",
    source_app: str = "mcp",
    source_agent: str = "unknown",
) -> dict[str, Any]:
    path = _find_record_by_id(root, record_id)
    if not path:
        raise FileNotFoundError(f"Record not found: {record_id}")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = _parse_frontmatter(text)
    if str(meta.get("type")) not in SELF_TYPES:
        raise ValueError("confirm_self_record only supports Self Model records")
    if not agent_policy(root, source_agent).get("self_access", False):
        raise PermissionError(f"Agent '{source_agent}' is not authorized to confirm Self Model records")
    if not can_read_record(root, meta, source_agent, include_self=True):
        raise PermissionError("Agent is not authorized for this Self record")
    event = capture_event(
        root,
        text=f"#确认自我记录 {record_id}\n{confirmation_note}".strip(),
        source_app=source_app,
        source_agent=source_agent,
        privacy="sensitive",
        explicit=True,
        routing_hints=["self", "confirmation"],
        access={"context_scopes": ["self"], "allowed_agents": _list_value(meta, "allowed_agents"), "denied_agents": _list_value(meta, "denied_agents")},
    )
    meta["schema"] = "pkh-canonical-v3"
    meta["user_confirmed"] = True
    current = str(meta.get("maturity") or "L1")
    meta["maturity"] = "L3" if MATURITY_LEVELS.get(current, 0) < 3 else current
    meta["last_observed"] = now_iso()
    src = _list_value(meta, "source_event_ids")
    if event["event_id"] not in src:
        src.append(event["event_id"])
    meta["source_event_ids"] = src
    agents = _list_value(meta, "source_agents")
    if source_agent not in agents:
        agents.append(source_agent)
    meta["source_agents"] = agents
    body = _document_body(text).rstrip() + f"\n\n## Confirmation History\n- {now_iso()}: user confirmed via {source_agent} ({event['event_id']})"
    if confirmation_note.strip():
        body += f" — {confirmation_note.strip()}"
    body += "\n"
    _rewrite_record(root, path, meta, body)
    mark_event(root, event["event_id"], status="processed", canonical_id=record_id)
    rebuild_json_index(root)
    return {"id": record_id, "path": str(path.relative_to(root)).replace("\\", "/"), "user_confirmed": True, "maturity": meta["maturity"], "source_event_id": event["event_id"]}


def _validate_maturity_evidence(
    root: Path, meta: dict[str, Any], target: str, evidence_note: str,
    validation: dict[str, Any] | None, source_agent: str,
) -> dict[str, Any]:
    if MATURITY_LEVELS[target] < 3:
        return {}
    if not evidence_note.strip():
        raise ValueError("L3+ requires a non-empty evidence_note describing the verification")
    if not isinstance(validation, dict):
        raise ValueError("L3+ requires validation.evidence_event_ids")
    ids = validation.get("evidence_event_ids")
    if not isinstance(ids, list) or not ids or any(not isinstance(value, str) or not value.strip() for value in ids):
        raise ValueError("validation.evidence_event_ids must be a non-empty list of event IDs")
    protected = str(meta.get("type")) in SELF_TYPES or "self" in _list_value(meta, "context_scopes")
    proof: dict[str, Any] = {"evidence_event_ids": list(dict.fromkeys(ids))}
    for event_id in proof["evidence_event_ids"]:
        event = get_event(root, event_id, agent_id=source_agent, include_self=True)
        if not protected and "self" in _list_value(_event_access(event), "context_scopes"):
            raise ValueError("Protected Self evidence cannot be published in a work maturity record")
    if MATURITY_LEVELS[target] >= 4:
        for field in ("applicability", "stability_evidence"):
            value = validation.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"L4+ requires validation.{field}")
            proof[field] = value.strip()
    if target == "L5":
        playbook = validation.get("playbook")
        if not isinstance(playbook, dict):
            raise ValueError("L5 requires validation.playbook")
        cleaned: dict[str, Any] = {}
        for field in ("inputs", "outputs", "failure_handling", "validation_result"):
            value = playbook.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"L5 requires validation.playbook.{field}")
            cleaned[field] = value.strip()
        steps = playbook.get("steps")
        if not isinstance(steps, list) or not steps or any(not isinstance(step, str) or not step.strip() for step in steps):
            raise ValueError("L5 requires non-empty validation.playbook.steps")
        cleaned["steps"] = [step.strip() for step in steps]
        proof["playbook"] = cleaned
    return proof


def promote_record(
    root: Path,
    *,
    record_id: str,
    target_maturity: str,
    evidence_note: str = "",
    source_app: str = "mcp",
    source_agent: str = "unknown",
    validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _maturity_ok(target_maturity)
    path = _find_record_by_id(root, record_id)
    if not path:
        raise FileNotFoundError(f"Record not found: {record_id}")
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = _parse_frontmatter(text)
    if not can_read_record(root, meta, source_agent, include_self=True):
        raise PermissionError(f"Agent '{source_agent}' is not authorized to promote this record")
    protected = str(meta.get("type")) in SELF_TYPES or "self" in _list_value(meta, "context_scopes")
    old = str(meta.get("maturity") or "L1")
    if MATURITY_LEVELS[target_maturity] < MATURITY_LEVELS.get(old, 0):
        raise ValueError("Use supersede/archive for regression; promote_record only moves forward")
    if protected and MATURITY_LEVELS[target_maturity] >= 3:
        confirmed = str(meta.get("user_confirmed", "false")).lower() == "true"
        if not confirmed:
            raise ValueError("Self Model records require explicit user confirmation before L3+")
    proof = _validate_maturity_evidence(root, meta, target_maturity, evidence_note, validation, source_agent)
    evidence_body = "\n\n" + json.dumps(proof, ensure_ascii=False, indent=2) if proof else ""
    event = capture_event(
        root,
        text=f"#知识成熟度 {record_id}: {old} -> {target_maturity}\n{evidence_note}{evidence_body}".strip(),
        source_app=source_app,
        source_agent=source_agent,
        privacy=str(meta.get("privacy") or "private"),
        explicit=True,
        routing_hints=["maturity", *(["self"] if protected else []),
                       *("project:" + project for project in _list_value(meta, "projects"))],
        access={"context_scopes": ["self"] if protected else (_list_value(meta, "context_scopes") or ["work"]),
                "allowed_agents": _list_value(meta, "allowed_agents"), "denied_agents": _list_value(meta, "denied_agents")},
    )
    meta["schema"] = "pkh-canonical-v3"
    meta["maturity"] = target_maturity
    src = _list_value(meta, "source_event_ids")
    if event["event_id"] not in src:
        src.append(event["event_id"])
    meta["source_event_ids"] = src
    body = _document_body(text).rstrip() + f"\n\n## Maturity History\n- {now_iso()}: {old} → {target_maturity} ({event['event_id']})"
    if evidence_note.strip():
        body += f" — {evidence_note.strip()}"
    body += "\n"
    if proof:
        body += "\n## Maturity Evidence\n```json\n" + json.dumps(proof, ensure_ascii=False, indent=2) + "\n```\n"
    _rewrite_record(root, path, meta, body)
    mark_event(root, event["event_id"], status="processed", canonical_id=record_id)
    rebuild_json_index(root)
    return {"id": record_id, "maturity": target_maturity, "previous_maturity": old, "source_event_id": event["event_id"]}


def link_records(
    root: Path,
    *,
    source_id: str,
    relation: str,
    target_id: str,
    note: str = "",
    source_agent: str = "unknown",
) -> dict[str, Any]:
    if relation not in GRAPH_RELATIONS:
        raise ValueError(f"Unsupported relation: {relation}")
    source_path = _find_record_by_id(root, source_id)
    target_path = _find_record_by_id(root, target_id)
    if not source_path:
        raise FileNotFoundError(f"Record not found: {source_id}")
    if not target_path:
        raise FileNotFoundError(f"Record not found: {target_id}")
    for rp in (source_path, target_path):
        rmeta = _parse_frontmatter(rp.read_text(encoding="utf-8", errors="replace"))
        if not can_read_record(root, rmeta, source_agent, include_self=True):
            raise PermissionError(f"Agent '{source_agent}' is not authorized to link record '{rmeta.get('id')}'")
    edge = {
        "edge_id": "edge_" + uuid.uuid4().hex,
        "source_id": source_id,
        "relation": relation,
        "target_id": target_id,
        "note": note,
        "created_at": now_iso(),
        "source_agent": source_agent,
    }
    graph = root / "00_System" / "GRAPH.jsonl"
    with lock_for(root):
        graph.parent.mkdir(parents=True, exist_ok=True)
        with graph.open("a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(edge, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return edge


def get_record_neighbors(
    root: Path,
    record_id: str,
    limit: int = 50,
    *,
    agent_id: str | None = None,
    include_self: bool = False,
) -> list[dict[str, Any]]:
    anchor = _find_record_by_id(root, record_id)
    if not anchor:
        raise FileNotFoundError(f"Record not found: {record_id}")
    anchor_meta = _parse_frontmatter(anchor.read_text(encoding="utf-8", errors="replace"))
    if not can_read_record(root, anchor_meta, agent_id, include_self=include_self):
        raise PermissionError(f"Agent '{agent_id or 'unknown'}' is not authorized to read graph neighbors for {record_id}")
    graph = root / "00_System" / "GRAPH.jsonl"
    if not graph.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in graph.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            edge = json.loads(line)
        except Exception:
            continue
        if edge.get("source_id") != record_id and edge.get("target_id") != record_id:
            continue
        other_id = edge.get("target_id") if edge.get("source_id") == record_id else edge.get("source_id")
        other = _find_record_by_id(root, str(other_id)) if other_id else None
        if other:
            other_meta = _parse_frontmatter(other.read_text(encoding="utf-8", errors="replace"))
            if not can_read_record(root, other_meta, agent_id, include_self=include_self):
                continue
        rows.append(edge)
    return rows[-max(1, min(limit, 200)):]


def get_self_context(
    root: Path,
    *,
    agent_id: str,
    query: str = "",
    confirmed_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    pol = agent_policy(root, agent_id)
    if not pol.get("self_access", False):
        raise PermissionError(f"Agent '{agent_id}' is not authorized for Self Model context")
    hits = search_knowledge(
        root,
        query=query,
        record_types=sorted(SELF_TYPES),
        limit=limit,
        agent_id=agent_id,
        include_self=True,
        confirmed_self_only=confirmed_only,
    )
    # The hand-edited overview has no per-record ACL or confirmation metadata.
    # Derive the overview from the same filtered records returned below.
    overview = "# Self Context (filtered)\n" + "".join(
        f"- {hit['title']} ({hit['id']}): {hit['snippet']}\n" for hit in hits
    )
    return {
        "agent_id": agent_id,
        "self_context": overview[:12000],
        "records": hits,
    }


def migrate_self_scope(root: Path, *, dry_run: bool = True, backup_dir: str | Path | None = None) -> dict[str, Any]:
    """Restore legacy Self originals to self scope without changing IDs or evidence."""
    def collect() -> tuple[list[tuple[Path, str, dict[str, Any]]], list[dict[str, Any]]]:
        candidates: list[tuple[Path, str, dict[str, Any]]] = []
        for path in (root / "70_Self").rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = _parse_frontmatter(text)
            if str(meta.get("type")) not in SELF_TYPES:
                continue
            if _list_value(meta, "context_scopes") != ["self"] or str(meta.get("visibility")) != "private" or str(meta.get("shared_from_self", "false")).lower() == "true":
                candidates.append((path, text, meta))
        changes = [{"id": meta.get("id"), "path": str(path.relative_to(root)).replace("\\", "/"),
                    "old_scopes": _list_value(meta, "context_scopes"), "old_visibility": meta.get("visibility")}
                   for path, _, meta in candidates]
        return candidates, changes

    if dry_run:
        _, changes = collect()
        return {"dry_run": True, "changed": len(changes), "records": changes}
    backup = Path(backup_dir).resolve() if backup_dir else (
        root / "99_Archive" / "migrations" / f"self-scope-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    ).resolve()
    for folder in [*(root / name for name in CANONICAL_FOLDERS), root / ".state"]:
        if backup == folder.resolve() or backup.is_relative_to(folder.resolve()):
            raise ValueError("backup_dir must stay outside canonical and index folders")
    with lock_for(root):
        # Re-read under the write lock so a concurrent confirmation cannot be
        # overwritten by a snapshot collected before the lock was acquired.
        candidates, changes = collect()
        if not candidates:
            return {"dry_run": False, "changed": 0, "records": []}
        if backup.exists() and any(backup.iterdir()):
            raise FileExistsError(f"Backup directory must be empty: {backup}")
        # Finish every backup before editing the first original.
        for path, text, _ in candidates:
            atomic_write_text(backup / path.relative_to(root), text)
        atomic_write_json(backup / "manifest.json", {
            "created_at": now_iso(), "hub": str(root.resolve()),
            "records": [{**row, "sha256": sha256_bytes(text.encode("utf-8"))}
                        for row, (_, text, _) in zip(changes, candidates)],
        })
        for path, text, meta in candidates:
            meta["context_scopes"] = ["self"]
            meta["visibility"] = "private"
            meta["shared_from_self"] = False
            meta["scope_migrated_at"] = now_iso()
            atomic_write_text(path, _frontmatter(meta) + _document_body(text).lstrip().rstrip() + "\n")
    rebuild_json_index(root)
    return {"dry_run": False, "changed": len(changes), "records": changes, "backup_dir": str(backup)}


def get_self_evolution(root: Path, *, agent_id: str, limit: int = 100) -> list[dict[str, Any]]:
    pol = agent_policy(root, agent_id)
    if not pol.get("self_access", False):
        raise PermissionError(f"Agent '{agent_id}' is not authorized for Self Model context")
    rows: list[dict[str, Any]] = []
    base = root / "70_Self"
    if not base.exists():
        return rows
    for p in base.rglob("*.md"):
        if p.name == "00_SELF_CONTEXT.md":
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        meta = _parse_frontmatter(text)
        if not can_read_record(root, meta, agent_id, include_self=True):
            continue
        title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
        rows.append({
            "id": meta.get("id"), "type": meta.get("type"), "title": title,
            "created_at": meta.get("created_at"), "updated_at": meta.get("updated_at"),
            "maturity": meta.get("maturity"), "user_confirmed": meta.get("user_confirmed"),
            "confidence": meta.get("confidence"), "origin": meta.get("origin"),
            "path": str(p.relative_to(root)).replace("\\", "/"),
        })
    rows.sort(key=lambda x: (x.get("updated_at") or x.get("created_at") or ""), reverse=True)
    return rows[:max(1, min(limit, 500))]


def _query_tokens(query: str) -> list[str]:
    q = query.lower().strip()
    toks = re.findall(r"[a-z0-9_+.-]{2,}|[\u4e00-\u9fff]+", q)
    out: list[str] = []
    for tok in toks:
        if re.fullmatch(r"[\u4e00-\u9fff]+", tok) and len(tok) > 2:
            out.append(tok)
            out.extend(tok[i:i+2] for i in range(len(tok)-1))
        else:
            out.append(tok)
    return list(dict.fromkeys(out))


def _snippet(text: str, tokens: list[str], size: int = 260) -> str:
    low = text.lower()
    positions = [low.find(t) for t in tokens if low.find(t) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - size // 3)
    s = re.sub(r"\s+", " ", text[start:start + size]).strip()
    return ("…" if start else "") + s + ("…" if start + size < len(text) else "")


def search_knowledge(
    root: Path,
    *,
    query: str,
    record_types: list[str] | None = None,
    project: str | None = None,
    limit: int = 8,
    agent_id: str | None = None,
    include_self: bool = False,
    confirmed_self_only: bool = False,
) -> list[dict[str, Any]]:
    tokens = _query_tokens(query)
    allowed = set(record_types or [])
    rows: list[dict[str, Any]] = []
    preference_ids = {row["id"] for row in get_user_profile(root, agent_id=agent_id)["preferences"]}
    for folder in CANONICAL_FOLDERS:
        base = root / folder
        if not base.exists():
            continue
        for p in base.rglob("*.md"):
            if p.name in {"INDEX.md", "TASKS.md", "00_SELF_CONTEXT.md"}:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            rtype = str(fm.get("type") or folder)
            if not can_read_record(root, fm, agent_id, include_self=include_self):
                continue
            if fm.get("profile_kind") == "collaboration_preference" and fm.get("id") not in preference_ids:
                continue
            if confirmed_self_only and rtype in SELF_TYPES and str(fm.get("user_confirmed", "false")).lower() != "true":
                continue
            if allowed and rtype not in allowed:
                continue
            projects = fm.get("projects", [])
            if not isinstance(projects, list):
                projects = [projects] if projects else []
            if project and project not in projects:
                continue
            title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
            title_l = title.lower()
            text_l = text.lower()
            score = 0.0
            q = query.lower().strip()
            if q and q in title_l:
                score += 20
            if q and q in text_l:
                score += 10
            for t in tokens:
                if t in title_l:
                    score += 6
                count = text_l.count(t)
                score += min(count, 8) * 0.8
            if project and project in projects:
                score += 5
            if not query.strip():
                score = 1
            if score <= 0:
                continue
            rows.append({
                "id": fm.get("id"), "type": rtype, "title": title,
                "path": str(p.relative_to(root)).replace("\\", "/"),
                "updated_at": fm.get("updated_at"), "projects": projects,
                "status": fm.get("status"), "score": round(score, 2),
                "maturity": fm.get("maturity"), "confidence": fm.get("confidence"),
                "user_confirmed": fm.get("user_confirmed"),
                "snippet": _snippet(text, tokens),
            })
    rows.sort(key=lambda r: (r["score"], r.get("updated_at") or ""), reverse=True)
    return rows[: max(1, min(limit, 50))]


def rebuild_search_index(root: Path) -> dict[str, Any]:
    """Build a local SQLite FTS index. Falls back to unicode61 when trigram is unavailable."""
    db = root / ".state" / "search.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_name("search.db.tmp-" + uuid.uuid4().hex)
    tokenizer = "trigram"
    count = 0
    conn = sqlite3.connect(tmp)
    try:
        # journal_mode=MEMORY instead of DELETE: SQLite deletes its rollback journal
        # file on commit, and on this machine `unlink` intermittently stalls for 60s
        # (see discard_lock_file above). Measured: a DELETE-mode rebuild cost
        # 74.8s / 120.1s / 120.0s, i.e. two full 60s stalls per call, and this runs on
        # every canonical write via rebuild_json_index.
        #
        # Crash safety is unaffected: this DB is a throwaway build target. Callers only
        # publish it with an atomic os.replace() after the build succeeds, so a killed
        # process leaves the previous search.db intact and the temp file is discarded.
        conn.execute("PRAGMA journal_mode=MEMORY")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE records (id TEXT PRIMARY KEY, type TEXT, title TEXT, path TEXT, updated_at TEXT, maturity TEXT, user_confirmed TEXT, body TEXT)")
        try:
            conn.execute("CREATE VIRTUAL TABLE records_fts USING fts5(id UNINDEXED, type UNINDEXED, title, body, path UNINDEXED, tokenize='trigram')")
        except sqlite3.OperationalError:
            tokenizer = "unicode61"
            conn.execute("CREATE VIRTUAL TABLE records_fts USING fts5(id UNINDEXED, type UNINDEXED, title, body, path UNINDEXED, tokenize='unicode61')")
        for folder in CANONICAL_FOLDERS:
            base = root / folder
            if not base.exists():
                continue
            for p in base.rglob("*.md"):
                if p.name in {"INDEX.md", "TASKS.md", "00_SELF_CONTEXT.md"}:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                fm = _parse_frontmatter(text)
                rid = str(fm.get("id") or "")
                if not rid:
                    continue
                title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
                row = (rid, str(fm.get("type") or folder), title, str(p.relative_to(root)).replace("\\", "/"), str(fm.get("updated_at") or ""), str(fm.get("maturity") or ""), str(fm.get("user_confirmed") or ""), text)
                conn.execute("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?,?,?,?)", row)
                conn.execute("INSERT INTO records_fts(id,type,title,body,path) VALUES (?,?,?,?,?)", (rid, row[1], title, text, row[3]))
                count += 1
        conn.execute("INSERT INTO meta VALUES ('built_at', ?)", (now_iso(),))
        conn.execute("INSERT INTO meta VALUES ('tokenizer', ?)", (tokenizer,))
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, db)
    return {"path": str(db.relative_to(root)).replace("\\", "/"), "records": count, "tokenizer": tokenizer}


def search_fulltext(
    root: Path,
    *,
    query: str,
    limit: int = 12,
    agent_id: str | None = None,
    include_self: bool = False,
) -> list[dict[str, Any]]:
    db = root / ".state" / "search.db"
    if not db.exists():
        rebuild_search_index(root)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        meta = dict(conn.execute("SELECT key,value FROM meta").fetchall())
        tokenizer = meta.get("tokenizer", "unicode61")
        q = query.strip()
        if not q:
            return search_knowledge(root, query="", limit=limit, agent_id=agent_id, include_self=include_self)
        # FTS query syntax is intentionally conservative; phrase search first.
        safe = q.replace('"', ' ')
        fts_query = f'"{safe}"' if tokenizer == "unicode61" else safe
        try:
            rows = conn.execute(
                "SELECT r.*, bm25(records_fts) AS rank FROM records_fts JOIN records r ON r.id=records_fts.id WHERE records_fts MATCH ? ORDER BY rank LIMIT ?",
                (fts_query, max(limit * 4, 20)),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        out: list[dict[str, Any]] = []
        preference_ids = {item["id"] for item in get_user_profile(root, agent_id=agent_id)["preferences"]}
        for row in rows:
            path = root / row["path"]
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            fm = _parse_frontmatter(text)
            if not can_read_record(root, fm, agent_id, include_self=include_self):
                continue
            if fm.get("profile_kind") == "collaboration_preference" and fm.get("id") not in preference_ids:
                continue
            out.append({
                "id": row["id"], "type": row["type"], "title": row["title"], "path": row["path"],
                "updated_at": row["updated_at"], "maturity": row["maturity"],
                "user_confirmed": row["user_confirmed"], "snippet": _snippet(text, _query_tokens(q)),
            })
            if len(out) >= limit:
                break
        if len(out) < limit:
            seen = {x["id"] for x in out}
            for x in search_knowledge(root, query=q, limit=limit, agent_id=agent_id, include_self=include_self):
                if x.get("id") not in seen:
                    out.append(x)
                if len(out) >= limit:
                    break
        return out
    finally:
        conn.close()


def get_project_context(root: Path, project: str, limit: int = 8, agent_id: str | None = None) -> dict[str, Any]:
    resolved = resolve_project(root, project_hint=project, agent_id=agent_id)
    if not resolved.get("resolved"):
        return {"resolved": False, "reason": resolved.get("reason"), "project_record": "", "related_records": []}
    name = str(resolved["project"]["name"])
    path = _canonical_path(root, "project", "", name, name)
    project_text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    if project_text and not can_read_record(root, _parse_frontmatter(project_text), agent_id, include_self=False):
        return {"resolved": False, "reason": "project_access_denied", "project_record": "", "related_records": []}
    related = search_knowledge(root, query=name, project=name, limit=limit, agent_id=agent_id, include_self=False)
    return {"resolved": True, "project": name, "project_record": project_text[:12000], "related_records": related}


PREFERENCE_CATEGORIES = {"identity", "communication", "workflow", "learning", "goals", "decision_principle"}


def _latest_preferences(root: Path) -> dict[str, tuple[Path, dict[str, Any], str]]:
    latest: dict[str, tuple[Path, dict[str, Any], str]] = {}
    for path in (root / "10_Knowledge").rglob("*.md"):
        text = path.read_text(encoding="utf-8", errors="replace")
        meta = _parse_frontmatter(text)
        if meta.get("profile_kind") != "collaboration_preference":
            continue
        key = str(meta.get("preference_key") or "")
        previous = latest.get(key)
        if previous is None or int(meta["preference_revision"]) > int(previous[1]["preference_revision"]):
            latest[key] = (path, meta, text)
    return latest


def get_user_profile(root: Path, *, agent_id: str | None, include_inactive: bool = False) -> dict[str, Any]:
    """Only current, explicitly confirmed collaboration preferences; never Self text."""
    rows = []
    # Choose the latest revision BEFORE ACL filtering. A narrowed audience must
    # not fall back to an older, more widely shared preference.
    for key, (path, meta, text) in sorted(_latest_preferences(root).items()):
        if not can_read_record(root, meta, agent_id, include_self=False):
            continue
        if str(meta.get("user_confirmed")).lower() != "true":
            continue
        active = str(meta.get("preference_active", "true")).lower() == "true"
        if not active and not include_inactive:
            continue
        rows.append({
            "id": meta["id"], "key": key, "category": meta["preference_category"],
            "revision": int(meta["preference_revision"]), "active": active,
            "content": _document_body(text).strip(),
            "shared_with": _list_value(meta, "allowed_agents"),
            "updated_at": meta["updated_at"], "source_agents": _list_value(meta, "source_agents"),
            "path": str(path.relative_to(root)).replace("\\", "/"),
        })
    return {"agent_id": agent_id, "preferences": rows, "scope": "confirmed_shared_preferences", "read_only": True}


def record_user_preference(
    root: Path, *, key: str, statement: str, category: str = "communication",
    user_confirmed: bool = False, sharing_confirmed: bool = False,
    share_with_agents: list[str] | None = None, expected_record_id: str | None = None,
    active: bool = True, source_agent: str = "unknown", source_app: str = "mcp",
) -> dict[str, Any]:
    """Append a confirmed preference revision with explicit audience and optimistic concurrency."""
    if not re.fullmatch(r"[a-z][a-z0-9_.-]{0,79}", key):
        raise ValueError("key must be a stable lowercase identifier, e.g. communication.answer_style")
    if category not in PREFERENCE_CATEGORIES or not statement.strip() or len(statement) > 3000:
        raise ValueError("A supported category and a concise non-empty statement (max 3000 chars) are required")
    registry = load_agent_registry(root).get("agents", {})
    if source_agent not in registry:
        raise PermissionError("Preference writer must be a registered, process-bound Agent")
    audience = sorted(set(share_with_agents or []))
    if not audience or any(agent not in registry for agent in audience):
        raise ValueError("Explicitly choose registered share_with_agents; wildcard sharing is not supported")
    if not user_confirmed or not sharing_confirmed:
        raise ValueError("Both the preference and its sharing audience require explicit user confirmation")
    key_hash = sha256_bytes(key.encode("utf-8"))
    with FileLock(root / ".state" / "preference-locks" / f"{key_hash}.lock", timeout=60.0, stale_after=600.0):
        previous = _latest_preferences(root).get(key)
        previous_id = str(previous[1]["id"]) if previous else None
        if previous and not can_read_record(root, previous[1], source_agent, include_self=False):
            raise PermissionError("Agent is not authorized to update this preference")
        body = statement.strip() + "\n"
        if previous and (
            previous[1].get("preference_category") == category
            and str(previous[1].get("preference_active")).lower() == str(active).lower()
            and sorted(_list_value(previous[1], "allowed_agents")) == audience
            and _document_body(previous[2]).strip() == statement.strip()
        ):
            # Finish evidence/index publication if an earlier write stopped
            # after the canonical revision became durable.
            for event_id in _list_value(previous[1], "source_event_ids"):
                mark_event(root, event_id, status="processed", canonical_id=previous_id)
            rebuild_json_index(root)
            return {"id": previous_id, "key": key, "revision": int(previous[1]["preference_revision"]), "idempotent_replay": True}
        if expected_record_id != previous_id:
            raise ValueError("Preference changed or already exists; read get_user_profile(include_inactive=true) and pass its current id as expected_record_id")
        revision = int(previous[1]["preference_revision"]) + 1 if previous else 1
        event = capture_event(
            root, text=body, source_app=source_app, source_agent=source_agent, explicit=True,
            routing_hints=["collaboration_preference", key],
            access={"context_scopes": ["work"], "allowed_agents": audience, "denied_agents": []},
        )
        result = _write_canonical(
            root, record_type="knowledge", title=f"Preference {key}", body=body, project=None,
            source_event_ids=[event["event_id"]], source_agents=[source_agent], maturity="L3",
            context_scopes=["work"], allowed_agents=audience, origin="user_statement", user_confirmed=True,
            extra_meta={
                "profile_kind": "collaboration_preference", "preference_key": key,
                "preference_category": category, "preference_revision": revision,
                "preference_active": active, "supersedes": [previous_id] if previous_id else [],
            },
        )
        mark_event(root, event["event_id"], status="processed", canonical_id=result["id"])
        rebuild_json_index(root)
        return {**result, "key": key, "revision": revision, "active": active, "shared_with": audience,
                "source_event_id": event["event_id"], "idempotent_replay": False}


def get_context(root: Path, *, project: str | None = None, query: str | None = None, limit: int = 8, agent_id: str | None = None) -> dict[str, Any]:
    from onboarding import get_onboarding

    current = root / "00_System" / "CURRENT_CONTEXT.md"
    result: dict[str, Any] = {
        "current_context": current.read_text(encoding="utf-8", errors="replace")[:12000] if not project and current.exists() else "",
        "user_profile": get_user_profile(root, agent_id=agent_id),
    }
    result["onboarding"] = get_onboarding(root, agent_id=agent_id, profile=result["user_profile"])
    if project:
        result["project"] = get_project_context(root, project, limit=limit, agent_id=agent_id)
    if query:
        if project and not result["project"]["resolved"]:
            result["search"] = []
        else:
            name = result["project"]["project"] if project else None
            result["search"] = search_knowledge(root, query=query, project=name, limit=limit, agent_id=agent_id, include_self=False)
    return result


def _markdown_section(text: str, heading: str) -> str:
    """Read one section from a canonical document without its frontmatter."""
    lines = _document_body(text).splitlines()
    start = next((i + 1 for i, line in enumerate(lines) if line.strip().casefold() == f"## {heading}".casefold()), None)
    if start is None:
        return ""
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start:end]).strip()


def _set_markdown_section(body: str, heading: str, content: str) -> str:
    """Replace one managed H2 section without dropping other document sections."""
    lines = body.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip().casefold() == f"## {heading}".casefold()), None)
    replacement = f"## {heading}\n{content.strip()}\n\n"
    if start is None:
        return body.rstrip() + "\n\n" + replacement
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "".join(lines[:start]) + replacement + "".join(lines[end:])


def _task_checkpoints(root: Path, project: str, query: str, limit: int, agent_id: str) -> list[dict[str, Any]]:
    """Select relevant readable history plus the latest handoff, with provenance."""
    rows = _checkpoint_rows(root, project, limit=None, agent_id=agent_id)
    tokens = _query_tokens(query)
    ranked = []
    for row in rows:
        text = (root / row["path"]).read_text(encoding="utf-8", errors="replace")
        body = _document_body(text).lower()
        title = str(row["title"]).lower()
        score = sum((4 if token in title else 0) + (1 if token in body else 0) for token in tokens)
        if score:
            ranked.append((score, int(str(row.get("sequence") or "0")), row))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected = [row for _, _, row in ranked[:limit]]
    if rows and rows[0] not in selected:
        if len(selected) < limit:
            selected.append(rows[0])
        elif limit > 1:
            selected[-1] = rows[0]
    return selected


def _context_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(_context_chars(item) for item in value.values())
    if isinstance(value, list):
        return sum(_context_chars(item) for item in value)
    return 0


def get_personalized_context(
    root: Path, *, query: str, agent_id: str, cwd: str | None = None,
    project: str | None = None, include_self: bool = True, limit: int = 4,
    max_chars: int = 6000, known_sections: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a bounded task snapshot with a protected, transient personal lens."""
    query = query.strip()
    if not query or len(query) > 400:
        raise ValueError("query must describe the current task in 1-400 characters")
    limit = max(1, min(int(limit), 10))
    max_chars = max(0, min(int(max_chars), 12000))
    resolved = resolve_project(root, cwd=cwd, project_hint=project, agent_id=agent_id) if (cwd or project) else {"resolved": False, "reason": "no_project_requested"}
    if (cwd or project) and not resolved.get("resolved"):
        return {"resolved": False, "reason": resolved.get("reason"), "sections": {}, "digests": {}, "read_only": True}

    project_entry = resolved.get("project") if resolved.get("resolved") else None
    project_name = str(project_entry["name"]) if project_entry else None
    project_id = str(project_entry["id"]) if project_entry else None
    project_data: dict[str, Any] = {}
    checkpoint_data: list[dict[str, Any]] = []
    if project_entry:
        path = _canonical_path(root, "project", "", project_name, project_name)
        project_text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        if not project_text or not can_read_record(root, _parse_frontmatter(project_text), agent_id, include_self=False):
            return {"resolved": False, "reason": "project_access_denied", "sections": {}, "digests": {}, "read_only": True}
        project_data = {
            "id": project_id, "name": project_name,
            "summary": _markdown_section(project_text, "Current Summary")[:600],
            "status": _markdown_section(project_text, "Status")[:80],
        }
        for row in _task_checkpoints(root, project_name, query, limit, agent_id):
            cp_text = (root / row["path"]).read_text(encoding="utf-8", errors="replace")
            checkpoint_data.append({
                "id": row["id"], "title": row["title"], "path": row["path"],
                "updated_at": row["updated_at"], "summary": str(row.get("summary") or "")[:500],
                "open_items": _markdown_section(cp_text, "Open Items")[:300],
                "decisions": _markdown_section(cp_text, "Decisions")[:200],
                "validations": _markdown_section(cp_text, "Validations")[:200],
                "source_agent": row.get("source_agents", []),
            })

    tokens = _query_tokens(query + (" " + project_name if project_name else ""))
    profile = get_user_profile(root, agent_id=agent_id)["preferences"]
    ranked_profile: list[tuple[float, dict[str, Any]]] = []
    for row in profile:
        content = str(row["content"])
        haystack = (str(row["key"]) + " " + content).lower()
        score = sum(1 for token in tokens if token in haystack)
        if row["category"] == "communication":
            score += 0.5  # Keep a small collaboration baseline when task terms do not match.
        if row["category"] == "decision_principle":
            score += 0.25
        ranked_profile.append((score, row))
    ranked_profile.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
    preference_data = [{"id": row["id"], "key": row["key"], "category": row["category"],
                        "content": str(row["content"])[:300], "revision": row["revision"]}
                       for score, row in ranked_profile[:limit] if score > 0]

    self_data: list[dict[str, Any]] = []
    if include_self and agent_policy(root, agent_id).get("self_access", False):
        allowed_self_types = {"value", "principle", "pattern"}
        for keyword, category in (("信念", "belief"), ("人性", "human_nature"),
                                  ("反思", "life_reflection"), ("经历", "life_reflection"),
                                  ("自我", "self_insight"), ("能力", "self_insight"),
                                  ("职业", "identity"), ("背景", "identity"),
                                  ("履历", "identity"), ("身份", "identity"),
                                  ("经历", "identity")):
            if keyword in query:
                allowed_self_types.add(category)
        linked_ids: set[str] = set()
        if project_id:
            for edge in get_record_neighbors(root, project_id, limit=200, agent_id=agent_id, include_self=True):
                if edge.get("relation") not in {"influences", "applies_to"}:
                    continue
                other_id = edge.get("target_id") if edge.get("source_id") == project_id else edge.get("source_id")
                if other_id:
                    linked_ids.add(str(other_id))
        self_hits = search_knowledge(root, query=query, record_types=sorted(allowed_self_types),
                                     limit=50, agent_id=agent_id, include_self=True,
                                     confirmed_self_only=True)
        candidate_ids = [str(hit["id"]) for hit in self_hits if hit.get("id")] + sorted(linked_ids)
        seen: set[str] = set()
        ranked_self: list[tuple[int, str, dict[str, Any]]] = []
        self_tokens = _query_tokens(query)
        for record_id in candidate_ids:
            if record_id in seen:
                continue
            seen.add(record_id)
            path = _find_record_by_id(root, record_id)
            if not path:
                continue
            self_text = path.read_text(encoding="utf-8", errors="replace")
            meta = _parse_frontmatter(self_text)
            if str(meta.get("type")) not in allowed_self_types or str(meta.get("user_confirmed", "false")).lower() != "true":
                continue
            if not can_read_record(root, meta, agent_id, include_self=True):
                continue
            title = next((line[2:].strip() for line in _document_body(self_text).splitlines() if line.startswith("# ")), "")
            statement = _markdown_section(self_text, "Statement")
            plain = (title + " " + statement).lower()
            relevance = sum(1 for token in self_tokens if token in plain)
            if record_id in linked_ids:
                relevance += 3
            if relevance == 0:
                continue
            item = {
                "id": record_id, "type": meta["type"], "title": title[:120],
                "statement": statement[:320],
                "origin": meta.get("origin"), "confidence": meta.get("confidence"),
                "user_confirmed": True, "relation": "linked_to_project" if record_id in linked_ids else "task_match",
            }
            ranked_self.append((relevance, record_id, item))
        ranked_self.sort(key=lambda row: (-row[0], row[1]))
        self_data = [item for _, _, item in ranked_self[:limit]]

    work_data: list[dict[str, Any]] = []
    if project_name:
        for hit in search_knowledge(root, query=query, project=project_name, limit=limit * 3,
                                    agent_id=agent_id, include_self=False):
            if hit["type"] in {"project", "project_checkpoint"} or hit["id"] in {row["id"] for row in preference_data}:
                continue
            work_data.append({"id": hit["id"], "type": hit["type"], "title": hit["title"],
                              "snippet": hit["snippet"][:260], "status": hit.get("status")})
            if len(work_data) >= limit:
                break

    raw_sections: dict[str, Any] = {
        "project": project_data, "checkpoints": checkpoint_data,
        "self": self_data, "preferences": preference_data, "work": work_data,
    }
    known = known_sections if isinstance(known_sections, dict) else {}
    sections: dict[str, dict[str, Any]] = {}
    digests: dict[str, str] = {}
    remaining = max_chars
    for name, data in raw_sections.items():
        digest = sha256_bytes(json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if known.get(name) == digest:
            sections[name] = {"digest": digest, "unchanged": True}
            digests[name] = digest
            continue
        cost = _context_chars(data)
        if cost > remaining:
            # An omitted section was never delivered. Do not issue a digest
            # that a client could accidentally use as a receipt next time.
            sections[name] = {"omitted_for_budget": True}
            continue
        sections[name] = {"digest": digest, "data": data}
        digests[name] = digest
        remaining -= cost
    return {
        "resolved": bool(project_entry), "project_id": project_id, "project_name": project_name,
        "sections": sections, "digests": digests, "returned_chars": max_chars - remaining,
        "max_chars": max_chars, "read_only": True,
    }

def _event_summary(root: Path, path: Path, event: dict[str, Any]) -> dict[str, Any]:
    text = event.get("content", {}).get("text") or ""
    metadata = event.get("source", {}).get("metadata") or {}
    return {
        "event_id": event.get("event_id"), "created_at": event.get("created_at"),
        "status": event.get("status"), "source": event.get("source"),
        "kind": event.get("content", {}).get("kind"), "title": metadata.get("title"),
        "text": text[:1500], "text_truncated": len(text) > 1500,
        "routing_hints": event.get("signals", {}).get("routing_hints", []),
        "temporal": event.get("temporal", {}), "provenance": event.get("provenance", {}),
        "triage": event.get("triage", {}), "access": _event_access(event),
        "canonical_ids": event.get("canonical_ids", []),
        "path": str(path.relative_to(root)).replace("\\", "/"),
    }


def _canonical_metadata_index(root: Path) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for folder in CANONICAL_FOLDERS:
        for path in (root / folder).rglob("*.md"):
            meta = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
            if meta.get("id"):
                record_id = str(meta["id"])
                # Duplicate ids are invalid. Deny dependent event reads until repaired.
                index[record_id] = {} if record_id in index else meta
    return index


def list_inbox_events(
    root: Path, *, statuses: list[str] | None = None, source_app: str | None = None,
    lane: str | None = None, limit: int = 50, cursor: str | None = None, order: str = "newest",
    agent_id: str | None = None, include_self: bool = False,
) -> dict[str, Any]:
    wanted = set(statuses or ["pending"])
    invalid = wanted - {"pending", "processed", "ignored", "review"}
    if invalid:
        raise ValueError(f"Invalid event statuses: {sorted(invalid)}")
    offset = int(cursor or "0")
    if offset < 0:
        raise ValueError("cursor must be a non-negative integer offset")
    if order not in {"newest", "oldest"}:
        raise ValueError("order must be newest or oldest")
    page_size = max(1, min(limit, 200))
    items: list[dict[str, Any]] = []
    record_index: dict[str, dict[str, Any]] | None = None
    for p in (root / "01_Inbox" / "events").rglob("event-*.json"):
        try:
            e = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if e.get("status") not in wanted:
            continue
        if e.get("canonical_ids") and record_index is None:
            record_index = _canonical_metadata_index(root)
        if not can_read_event(root, e, agent_id, include_self=include_self, record_index=record_index):
            continue
        if source_app and e.get("source", {}).get("app") != source_app:
            continue
        if lane and e.get("triage", {}).get("lane") != lane:
            continue
        items.append(_event_summary(root, p, e))
    items.sort(key=lambda x: x.get("created_at") or "", reverse=order == "newest")
    total = len(items)
    page = items[offset: offset + page_size]
    next_offset = offset + len(page)
    return {"items": page, "total": total, "next_cursor": str(next_offset) if next_offset < total else None}


def list_pending(
    root: Path, limit: int = 50, *, agent_id: str | None = None, include_self: bool = False,
) -> list[dict[str, Any]]:
    return list_inbox_events(
        root, statuses=["pending"], limit=limit, agent_id=agent_id, include_self=include_self,
    )["items"]


def get_event(
    root: Path, event_id: str, *, agent_id: str | None = None, include_self: bool = False,
) -> dict[str, Any]:
    path = _event_path(root, event_id)
    event = json.loads(path.read_text(encoding="utf-8"))
    if not can_read_event(root, event, agent_id, include_self=include_self):
        raise PermissionError(f"Agent '{agent_id or 'unknown'}' is not authorized to read {event_id}")
    return {**event, "path": str(path.relative_to(root)).replace("\\", "/")}


def review_event(
    root: Path, event_id: str, *, status: str, agent_id: str | None = None, include_self: bool = False,
) -> dict[str, Any]:
    if status not in {"pending", "ignored", "review"}:
        raise ValueError("Review status must be pending, ignored, or review")
    get_event(root, event_id, agent_id=agent_id, include_self=include_self)
    mark_event(root, event_id, status=status)
    return {"event_id": event_id, "status": status}


def _find_record_by_source_event(root: Path, event_id: str) -> dict[str, Any] | None:
    for folder in CANONICAL_FOLDERS:
        for path in (root / folder).rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = _parse_frontmatter(text)
            if event_id in _list_value(meta, "source_event_ids"):
                return {
                    "id": meta.get("id"), "type": meta.get("type"),
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                }
    return None


def _project_routing_rules(root: Path) -> dict[str, Any]:
    path = root / "00_System" / "PROJECT_ROUTING_RULES.json"
    if not path.exists():
        return {"schema_version": "1.0", "projects": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("projects", []), list):
        raise ValueError(f"Invalid project routing rules: {path}")
    return data


def suggest_event_projects(
    root: Path, *, event: dict[str, Any], agent_id: str | None = None,
) -> dict[str, Any]:
    """Return deterministic project candidates without inferring from speaker identity."""
    metadata = event.get("source", {}).get("metadata") or {}
    title = str(metadata.get("title") or "")
    tags = {str(value).strip().casefold() for value in metadata.get("tags", []) if str(value).strip()}
    topics = {str(value).strip().casefold() for value in metadata.get("topics", []) if str(value).strip()}
    routing_hints = {str(value).strip().casefold() for value in event.get("signals", {}).get("routing_hints", []) if str(value).strip()}
    source_text = str(event.get("content", {}).get("text") or "")[:4000].casefold()
    rules_by_project = {
        str(row.get("project_id")): row
        for row in _project_routing_rules(root).get("projects", [])
        if isinstance(row, dict) and row.get("project_id")
    }
    candidates: list[dict[str, Any]] = []
    for project in list_projects(root, status="active", agent_id=agent_id):
        project_id = str(project["id"])
        identities = [str(project.get("name") or ""), *(str(value) for value in project.get("aliases", []))]
        identities = [value.strip() for value in identities if value.strip()]
        rule = rules_by_project.get(project_id, {})
        match = rule.get("match") if isinstance(rule.get("match"), dict) else {}
        score = 0
        reasons: list[str] = []
        for identity in identities:
            key = identity.casefold()
            if f"project:{key}" in routing_hints:
                score = max(score, 120)
                reasons.append(f"explicit_project_hint:{identity}")
            if key in tags or key in topics:
                score = max(score, 100)
                reasons.append(f"exact_tag_or_topic:{identity}")
            if len(key) >= 3 and key in title.casefold():
                score = max(score, 75)
                reasons.append(f"title_keyword:{identity}")
        configured = {
            "tags": tags,
            "topics": topics,
            "routing_hints": routing_hints,
        }
        for field, actual in configured.items():
            for value in match.get(field, []) if isinstance(match.get(field, []), list) else []:
                key = str(value).strip().casefold()
                if key and key in actual:
                    field_score = 120 if field == "routing_hints" else 100
                    score = max(score, field_score)
                    reasons.append(f"configured_{field}:{value}")
        for value in match.get("title_keywords", []) if isinstance(match.get("title_keywords", []), list) else []:
            key = str(value).strip().casefold()
            if key and key in title.casefold():
                score = max(score, 80)
                reasons.append(f"configured_title:{value}")
        for value in match.get("content_keywords", []) if isinstance(match.get("content_keywords", []), list) else []:
            key = str(value).strip().casefold()
            if key and key in source_text:
                score = max(score, 50)
                reasons.append(f"configured_content:{value}")
        for field, actual in (("review_tags", tags), ("review_topics", topics)):
            for value in match.get(field, []) if isinstance(match.get(field, []), list) else []:
                key = str(value).strip().casefold()
                if key and key in actual:
                    score = max(score, 70)
                    reasons.append(f"configured_{field}:{value}")
        for value in match.get("review_title_keywords", []) if isinstance(match.get("review_title_keywords", []), list) else []:
            key = str(value).strip().casefold()
            if key and key in title.casefold():
                score = max(score, 60)
                reasons.append(f"configured_review_title:{value}")
        if score:
            candidates.append({
                "project_id": project_id,
                "project": project["name"],
                "score": score,
                "reasons": list(dict.fromkeys(reasons)),
            })
    candidates.sort(key=lambda row: (int(row["score"]), str(row["project"])), reverse=True)
    top_score = int(candidates[0]["score"]) if candidates else 0
    top = [row for row in candidates if int(row["score"]) == top_score]
    action = "none"
    selected = None
    if top_score >= 100 and len(top) == 1:
        action = "auto_bind"
        selected = top[0]
    elif candidates:
        action = "review"
    return {
        "action": action,
        "selected": selected,
        "candidates": candidates[:5],
        "policy": "explicit hint or exact tag/topic required for automatic binding",
    }


def promote_event_to_record(
    root: Path, *, event_id: str, record_type: str, title: str,
    project: str | None = None, summary: str | None = None, agent_id: str | None = None,
    origin: str | None = None,
) -> dict[str, Any]:
    if not re.fullmatch(r"evt_[A-Za-z0-9_-]+", event_id):
        raise ValueError("Invalid event_id")
    # Serialize this source's publication while retaining the existing global
    # lock for individual file/index writes. A retry repairs partial publication.
    key = sha256_bytes(event_id.encode("utf-8"))
    with FileLock(root / ".state" / "event-promotion-locks" / f"{key}.lock"):
        return _promote_event_to_record(
            root, event_id=event_id, record_type=record_type, title=title,
            project=project, summary=summary, agent_id=agent_id, origin=origin,
        )


def _event_promotion_access(root: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Carry the source audience and project restrictions into derived records."""
    access = dict(_event_access(event))
    policies = [access]
    for hint in event.get("signals", {}).get("routing_hints", []):
        if isinstance(hint, str) and hint.startswith("project:"):
            project = hint.removeprefix("project:").strip()
            path = root / "20_Projects" / _safe_slug(project) / "PROJECT.md"
            policies.append(_parse_frontmatter(path.read_text(encoding="utf-8")))
    audience: set[str] | None = None
    denied: set[str] = set()
    for policy in policies:
        allowed = set(_list_value(policy, "allowed_agents"))
        if allowed and "*" not in allowed:
            audience = allowed if audience is None else audience & allowed
        denied.update(_list_value(policy, "denied_agents"))
    if audience is not None and not audience:
        denied.add("*")
    access["allowed_agents"] = sorted(audience) if audience is not None else []
    access["denied_agents"] = sorted(denied)
    return access


def _promote_event_to_record(
    root: Path, *, event_id: str, record_type: str, title: str,
    project: str | None, summary: str | None, agent_id: str | None, origin: str | None,
) -> dict[str, Any]:
    allowed_types = {"knowledge", "idea", "daily", "timeline", "meeting", "review", "asset"} | SELF_TYPES
    if record_type not in allowed_types:
        raise ValueError(f"Unsupported event promotion type: {record_type}")
    if not title.strip():
        raise ValueError("title must not be empty")
    event = get_event(root, event_id, agent_id=agent_id, include_self=True)
    access = _event_promotion_access(root, event)
    protected = "self" in _list_value(access, "context_scopes")
    if protected:
        if record_type not in SELF_TYPES | {"daily"} or project:
            raise ValueError("Self Inbox sources can only become protected daily evidence or unconfirmed Self observations, without a work project")
        origin = origin or ("agent_inference" if record_type in SELF_TYPES else "external_observation")
        if origin not in {"user_statement", "user_reflection", "agent_inference", "external_observation"}:
            raise ValueError("Unsupported self origin")
    elif record_type in SELF_TYPES or origin is not None:
        raise ValueError("Self observation promotion requires a self-scoped source event")
    existing = _find_record_by_source_event(root, event_id)
    if existing:
        get_record(root, existing["id"], agent_id=agent_id, include_self=protected)
        if existing["type"] != record_type:
            raise ValueError("This source event already has a different canonical record type")
        if event.get("status") != "processed" or existing["id"] not in event.get("canonical_ids", []):
            mark_event(root, event_id, status="processed", canonical_id=existing["id"])
        rebuild_json_index(root, require_search_index=True)
        return {**existing, "source_event_id": event_id, "idempotent": True}

    project_routing = {"action": "self_protected", "candidates": []} if protected else suggest_event_projects(root, event=event, agent_id=agent_id)
    if project:
        resolved_project = resolve_project(root, project_hint=project, agent_id=agent_id)
        if not resolved_project.get("resolved"):
            raise ValueError(f"Cannot bind event to project: {resolved_project.get('reason')}")
        project = str(resolved_project["project"]["name"])
    elif project_routing.get("action") == "auto_bind":
        project = str(project_routing["selected"]["project"])
    elif project_routing.get("action") == "review" and str(event.get("triage", {}).get("lane")) in {"company_meeting", "work_project"}:
        mark_event(root, event_id, status="review")
        raise ValueError(f"Project routing requires review: {project_routing['candidates']}")

    source_text = event.get("content", {}).get("text") or ""
    temporal = event.get("temporal") or {}
    provenance = event.get("provenance") or {}
    triage = event.get("triage") or {}
    source_metadata = event.get("source", {}).get("metadata") or {}
    maturity = "L2" if record_type in SELF_TYPES and origin in {"user_statement", "user_reflection"} else "L1"
    heading = "Statement" if record_type in SELF_TYPES else "摘要"
    body = f"# {title.strip()}\n\n## {heading}\n{(summary or source_text).strip()}\n"
    if protected:
        body += f"\n## Epistemic State\n- origin: {origin}\n- user_confirmed: false\n- maturity: {maturity}\n"
    body += "\n## 时间与来源\n"
    body += f"- 发生时间：{temporal.get('occurred_at') or event.get('created_at')}\n"
    body += f"- 时间依据：{temporal.get('time_basis') or 'ingested_at'}\n"
    if source_metadata.get("title"):
        body += f"- 来源标题：{source_metadata.get('title')}\n"
    body += f"- 说话人范围：{provenance.get('speaker_scope') or 'unknown'}\n"
    body += f"- 归因状态：{provenance.get('user_authorship') or 'unverified'}\n"
    body += f"- 陈述角色：{provenance.get('claim_role') or 'captured_content'}\n"
    body += f"- 主体关系：{provenance.get('subject_relation') or 'unknown'}\n"
    body += f"- 归因置信度：{provenance.get('attribution_confidence') or 'low'}\n"
    body += f"- 归因规则：{provenance.get('attribution_rule') or 'none'}\n"
    matched_aliases = provenance.get("matched_user_aliases") or []
    mentioned_aliases = provenance.get("mentioned_user_aliases") or []
    reported_aliases = provenance.get("reported_speech_user_aliases") or []
    if matched_aliases:
        body += f"- 已匹配本人别名：{', '.join(str(value) for value in matched_aliases)}\n"
    if mentioned_aliases:
        body += f"- 仅被提及的本人别名：{', '.join(str(value) for value in mentioned_aliases)}\n"
    if reported_aliases:
        body += f"- 转述语境中的本人别名：{', '.join(str(value) for value in reported_aliases)}\n"
    body += f"- 原始事件：{event_id}\n"
    result = _write_canonical(
        root, record_type=record_type, title=title, body=body, project=project,
        source_event_ids=[event_id], source_agents=[event.get("source", {}).get("agent") or "unknown", agent_id or "unknown"],
        actor_agent=agent_id,
        privacy=event.get("privacy") or "private", confidence=triage.get("confidence") or "medium",
        maturity=maturity, visibility="private" if protected else "work", context_scopes=["self" if protected else "work"],
        allowed_agents=_list_value(access, "allowed_agents"), denied_agents=_list_value(access, "denied_agents"),
        origin=origin if protected else "observed_or_captured_event", occurred_at=temporal.get("occurred_at"),
        user_confirmed=False if protected else None, evidence_event_ids=[event_id] if protected else None,
        extra_meta={
            "occurred_at": temporal.get("occurred_at") or event.get("created_at"),
            "time_basis": temporal.get("time_basis") or "ingested_at",
            "speaker_scope": provenance.get("speaker_scope") or "unknown",
            "user_authorship": provenance.get("user_authorship") or "unverified",
            "claim_role": provenance.get("claim_role") or "captured_content",
            "subject_relation": provenance.get("subject_relation") or "unknown",
            "attribution_confidence": provenance.get("attribution_confidence") or "low",
            "attribution_rule": provenance.get("attribution_rule") or "none",
            "matched_user_aliases": matched_aliases,
            "mentioned_user_aliases": mentioned_aliases,
            "reported_speech_user_aliases": reported_aliases,
            "source_title": source_metadata.get("title") or title,
            "triage_lane": triage.get("lane") or "unclassified",
            "project_routing_action": project_routing.get("action"),
            "project_routing_candidates": [row.get("project_id") for row in project_routing.get("candidates", [])],
        },
    )
    mark_event(root, event_id, status="processed", canonical_id=result["id"])
    rebuild_json_index(root, require_search_index=True)
    return {**result, "source_event_id": event_id, "idempotent": False}


def get_timeline(
    root: Path, *, start: str | None = None, end: str | None = None, limit: int = 100,
    include_pending: bool = True, agent_id: str | None = None, include_self: bool = False,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for folder in CANONICAL_FOLDERS:
        for path in (root / folder).rglob("*.md"):
            text = path.read_text(encoding="utf-8", errors="replace")
            meta = _parse_frontmatter(text)
            if meta.get("type") not in {"timeline", "meeting", "daily"}:
                continue
            if not can_read_record(root, meta, agent_id, include_self=include_self):
                continue
            occurred = str(meta.get("occurred_at") or meta.get("created_at") or "")
            if (start and occurred < start) or (end and occurred > end):
                continue
            items.append({
                "kind": "canonical", "id": meta.get("id"), "type": meta.get("type"),
                "occurred_at": occurred, "time_basis": meta.get("time_basis"),
                "speaker_scope": meta.get("speaker_scope"),
                "user_authorship": meta.get("user_authorship"),
                "claim_role": meta.get("claim_role"),
                "subject_relation": meta.get("subject_relation"),
                "attribution_confidence": meta.get("attribution_confidence"),
                "source_title": meta.get("source_title"),
                "projects": _list_value(meta, "projects"),
                "path": str(path.relative_to(root)).replace("\\", "/"),
            })
    if include_pending:
        page = list_inbox_events(
            root, statuses=["pending", "review"], limit=200, agent_id=agent_id, include_self=include_self,
        )
        cursor = page["next_cursor"]
        events = list(page["items"])
        while cursor:
            page = list_inbox_events(
                root, statuses=["pending", "review"], limit=200, cursor=cursor,
                agent_id=agent_id, include_self=include_self,
            )
            events.extend(page["items"])
            cursor = page["next_cursor"]
        for event in events:
            if "timeline:candidate" not in event.get("routing_hints", []):
                continue
            occurred = str(event.get("temporal", {}).get("occurred_at") or event.get("created_at") or "")
            if (start and occurred < start) or (end and occurred > end):
                continue
            items.append({
                "kind": "inbox", "id": event.get("event_id"), "type": event.get("triage", {}).get("canonical_target"),
                "occurred_at": occurred, "time_basis": event.get("temporal", {}).get("time_basis"),
                "event_relation": event.get("temporal", {}).get("event_relation"),
                "mentioned_times": event.get("temporal", {}).get("mentioned_times", []),
                "speaker_scope": event.get("provenance", {}).get("speaker_scope"),
                "user_authorship": event.get("provenance", {}).get("user_authorship"),
                "title": event.get("title"), "text": event.get("text"), "path": event.get("path"),
            })
    items.sort(key=lambda item: (item.get("occurred_at") or "", item.get("id") or ""), reverse=True)
    return {"items": items[:max(1, min(limit, 500))], "total": len(items)}


def get_record(root: Path, record_id: str, *, agent_id: str | None = None, include_self: bool = False) -> dict[str, Any]:
    p = _find_record_by_id(root, record_id)
    if not p:
        raise FileNotFoundError(f"Record not found: {record_id}")
    text = p.read_text(encoding="utf-8", errors="replace")
    fm = _parse_frontmatter(text)
    if not can_read_record(root, fm, agent_id, include_self=include_self):
        raise PermissionError(f"Agent '{agent_id or 'unknown'}' is not authorized to read {record_id}")
    return {
        "id": record_id,
        "path": str(p.relative_to(root)).replace("\\", "/"),
        "frontmatter": fm,
        "content": text[:30000],
        "neighbors": get_record_neighbors(root, record_id, limit=20, agent_id=agent_id, include_self=include_self),
    }


def rebuild_json_index(root: Path, *, require_search_index: bool = False) -> int:
    with lock_for(root):
        rows: list[dict[str, Any]] = []
        for folder in CANONICAL_FOLDERS:
            base = root / folder
            if not base.exists():
                continue
            for p in base.rglob("*.md"):
                if p.name in {"INDEX.md", "TASKS.md", "00_SELF_CONTEXT.md"}:
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                fm = _parse_frontmatter(text)
                if not fm.get("id"):
                    continue
                title = next((ln[2:].strip() for ln in text.splitlines() if ln.startswith("# ")), p.stem)
                rows.append({
                    "id": fm.get("id"), "type": fm.get("type", folder), "title": title,
                    "path": str(p.relative_to(root)).replace("\\", "/"),
                    "updated_at": fm.get("updated_at"), "status": fm.get("status"),
                    "projects": fm.get("projects", []), "maturity": fm.get("maturity"),
                    "visibility": fm.get("visibility"), "context_scopes": fm.get("context_scopes", []),
                    "user_confirmed": fm.get("user_confirmed"),
                })
        rows.sort(key=lambda x: (x.get("updated_at") or "", x.get("id") or ""), reverse=True)
        out = root / "00_System" / "INDEX.jsonl"
        atomic_write_text(out, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    try:
        rebuild_search_index(root)
    except Exception:
        if require_search_index:
            raise
        # JSON index remains authoritative; doctor/validate will surface FTS problems.
        pass
    return len(rows)

def get_connector_status(root: Path, connector: str) -> dict[str, Any]:
    name = re.sub(r"[^a-zA-Z0-9_.-]+", "-", connector.strip()).strip("-.")
    if not name:
        raise ValueError("connector name required")
    path = root / ".state" / "connectors" / f"{name}.json"
    credentials_visible = bool(os.environ.get("GETNOTE_API_KEY")) and bool(os.environ.get("GETNOTE_CLIENT_ID")) if name == "getnote" else None
    if not path.exists():
        return {
            "connector": name, "configured": credentials_visible, "state_present": False,
            "credentials_visible_to_process": credentials_visible,
            "state_path": str(path.relative_to(root)).replace("\\", "/"),
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"connector": name, "configured": credentials_visible, "state_present": True, "ok": False, "error": str(exc)}
    # Connector state must never contain credentials. Return only operational fields.
    return {
        "connector": name,
        "configured": credentials_visible,
        "state_present": True,
        "credentials_visible_to_process": credentials_visible,
        "operational": data.get("last_sync_status") in {"success", "dry-run"},
        "ok": True,
        "last_sync_started_at": data.get("last_sync_started_at"),
        "last_sync_completed_at": data.get("last_sync_completed_at"),
        "last_sync_status": data.get("last_sync_status"),
        "last_error": data.get("last_error"),
        "known_items": len(data.get("notes", {})) if isinstance(data.get("notes"), dict) else None,
        "last_stats": data.get("last_stats", {}),
    }


def health(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / "knowledge-hub.json").read_text(encoding="utf-8"))
    pending = 0
    for p in (root / "01_Inbox" / "events").rglob("event-*.json"):
        try:
            if json.loads(p.read_text(encoding="utf-8")).get("status") == "pending":
                pending += 1
        except Exception:
            pass
    return {
        "ok": True,
        "hub": str(root),
        "hub_id": manifest.get("hub_id"),
        "schema_version": manifest.get("schema_version"),
        "pending_events": pending,
        "mcp_api": manifest.get("mcp_api_version", "3.4"),
        "server_version": "3.8.0",
        "self_model": (root / "70_Self").exists(),
        "graph": (root / "00_System" / "GRAPH.jsonl").exists(),
        "fulltext_index": (root / ".state" / "search.db").exists(),
    }
