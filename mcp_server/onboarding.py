"""Optional, answer-free onboarding progress; no Self reads or ACL changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import core

STATES = {"not_started", "invited", "active", "paused", "skipped", "completed"}
TRANSITIONS = {
    "invite": ({"not_started"}, "invited"),
    "start": ({"not_started", "invited"}, "active"),
    "pause": ({"active"}, "paused"),
    "skip": ({"not_started", "invited", "active", "paused"}, "skipped"),
    "complete": ({"active"}, "completed"),
    "resume": ({"paused", "skipped", "completed", "invited"}, "active"),
}
CONTRACT = {
    "version": 1,
    "trigger": "No automatic popup. A consuming Agent must read get_context/start_project_session/get_onboarding, or the user must ask to start onboarding.",
    "invitation": "Offer once only when should_invite is true. Claim invite with the current revision BEFORE presenting it; only the caller receiving idempotent_replay=false may present the invitation. On replay or conflict do not repeat; re-read if needed. Ask permission, never start questions without consent.",
    "questions_per_turn": [1, 2],
    "topics": ["present purpose", "current situation or scenario", "what success looks like", "current difficulties", "how AI should collaborate"],
    "adaptation": "The consuming Agent follows the user's answers in this conversation, asks only 1-2 useful questions each turn, and checks its understanding. No profession-keyword branching or mandatory occupation/profile. Students, retired people, multiple roles, undisclosed occupation and uncertain goals are all valid. If goals are unclear, explore a small present need; never invent a career goal.",
    "controls": ["correct an answer", "skip any question", "pause", "skip onboarding", "complete without saving preferences", "resume explicitly", "use only in this conversation"],
    "persistence": "Raw answers stay in the conversation by default. Never capture onboarding answers into Inbox/work/Self/.state or a summary automatically. This tool accepts no answers. Only fixed status, revision and last action persist, separately per process-bound Agent. Conversation-only mode must not call the progress writer. Resume does not retrieve past answers: explain this and ask what to continue.",
    "preferences": "Optionally propose a concise collaboration preference. Save only after the user confirms both exact content and specific registered receiving Agents, using record_user_preference with user_confirmed=true and sharing_confirmed=true and share_with_agents. Do not assume an audience. Corrections use the existing expected_record_id protocol. Declining storage is valid and does not block completion.",
    "boundaries": "Explain before questions: Hub provides local memory and handoffs, not automatic execution or guaranteed results. The Agent can make mistakes. Local files accessible to the same OS account are not an encrypted vault; MCP access control is not filesystem encryption. Conversation answers and retrieved context enter the client's model context and follow its provider/privacy settings. Do not promise the chat stays only on disk.",
    "self": "Onboarding never enables, reads or writes Self Model, changes identity or grants permissions. Self requires separate explicit authorization through existing permission administration; onboarding consent is not Self consent.",
    "role_skills": "Optionally offer skippable task/goal-based role skill recommendations via match_role_skills/list_role_skills. Never classify the user into a profession. Show exact skills and explain favorites belong only to this Agent and grant no execution or dependency installation. Save only explicit confirmed selections via update_role_skill_favorite using current revision. Skipping does not block onboarding. Task queries are not stored.",
}


def _path(root: Path, agent_id: str) -> Path:
    if not isinstance(agent_id, str) or not agent_id.strip():
        raise PermissionError("A process-bound Agent identity is required")
    return root / ".state" / "onboarding" / (hashlib.sha256(agent_id.encode("utf-8")).hexdigest() + ".json")


def _read(path: Path) -> dict:
    if not path.exists():
        return {"status": "not_started", "revision": 0, "last_action": None}
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or set(state) != {"status", "revision", "last_action"}
            or state["status"] not in STATES or type(state["revision"]) is not int
            or state["revision"] < 1 or state["last_action"] not in TRANSITIONS
            or TRANSITIONS[state["last_action"]][1] != state["status"]):
        raise ValueError("Invalid onboarding state; do not reset or overwrite it automatically")
    return state


def get_onboarding(root: Path, *, agent_id: str | None, profile: dict | None = None) -> dict:
    if not agent_id:
        return {"available": False, "should_invite": False, "reason": "bound_identity_required"}
    state = _read(_path(root, agent_id))
    from role_catalog import summary
    role_skills = summary(root, agent_id)
    profile = profile if profile is not None else core.get_user_profile(root, agent_id=agent_id)
    existing = bool(profile["preferences"])
    return {**state, "available": True, "should_invite": state["status"] == "not_started" and not existing,
            "readiness": "existing_preferences" if existing else state["status"],
            "scope": "process_bound_agent", "read_only": True, "contract": CONTRACT, "role_skills": role_skills}


def update_onboarding(root: Path, *, agent_id: str, action: str, expected_revision: int,
                      user_confirmed: bool = False) -> dict:
    path = _path(root, agent_id)
    if action not in TRANSITIONS or type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("Unsupported action or revision")
    if action != "invite" and user_confirmed is not True:
        raise ValueError("Explicit user consent to this action is required")
    with core.FileLock(path.with_suffix(".lock"), timeout=60, stale_after=600):
        state = _read(path)
        # Only an exact immediately preceding operation can be replayed.
        if state["revision"] == expected_revision + 1 and state["last_action"] == action:
            return {**state, "idempotent_replay": True}
        if state["revision"] != expected_revision:
            raise ValueError("Stale onboarding revision; re-read before deciding")
        sources, target = TRANSITIONS[action]
        if state["status"] not in sources:
            raise ValueError("Illegal onboarding transition")
        if action == "invite" and core.get_user_profile(root, agent_id=agent_id)["preferences"]:
            raise ValueError("Existing preferences suppress invitations; explicit start remains available")
        state = {"status": target, "revision": state["revision"] + 1, "last_action": action}
        core.atomic_write_json(path, state)
        return {**state, "idempotent_replay": False}
