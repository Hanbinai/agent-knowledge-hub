from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


MEETING_WORDS = ("会议", "周会", "月会", "面试", "沟通", "对接", "讨论", "会谈", "电话", "汇报")
WORK_WORDS = (
    "同事", "客户", "排期", "项目", "公司", "部门", "工作", "运营", "业务", "招聘", "岗位",
    "电商", "商品", "推广", "财务", "开票", "直播", "天猫", "抖音", "供应商", "数据", "销售",
)
LEARNING_WORDS = ("学习", "课程", "课堂", "老师", "读书", "书摘", "知识", "教程", "方法论", "培训", "training", "lesson")
REFLECTION_WORDS = ("日记", "反思", "复盘自己", "我意识到", "我觉得", "我感到", "心情", "感受")
DAILY_WORDS = ("日常", "生活", "家人", "家庭", "吃饭", "运动", "睡眠", "健康", "旅行", "医生", "医院", "就诊")
REFERENCE_WORDS = ("素材", "收藏", "链接", "网页", "参考", "摘录")

WORK_LANES = {"company_meeting", "work_project", "learning", "reference"}


def load_timeline_policy(root: Path | None) -> dict[str, Any]:
    if root is None:
        return {}
    path = root / "00_System" / "TIMELINE_POLICY.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _normalize_time(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.isoformat(timespec="seconds")
    except ValueError:
        return raw


def _parse_time(value: Any) -> datetime | None:
    normalized = _normalize_time(value)
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _mentioned_times(text: str, anchor: datetime | None) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(raw: str, resolved: datetime | None, basis: str, confidence: str) -> None:
        key = f"{raw}|{resolved.isoformat() if resolved else ''}"
        if key in seen:
            return
        seen.add(key)
        found.append({
            "text": raw, "resolved_at": resolved.isoformat(timespec="seconds") if resolved else None,
            "basis": basis, "confidence": confidence,
        })

    for match in re.finditer(r"(?<!\d)(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?", text):
        try:
            add(match.group(0), datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)), tzinfo=anchor.tzinfo if anchor else None), "explicit_date", "high")
        except ValueError:
            pass
    if anchor:
        for match in re.finditer(r"(?<!\d)(\d{1,2})月(\d{1,2})日", text):
            try:
                add(match.group(0), datetime(anchor.year, int(match.group(1)), int(match.group(2)), tzinfo=anchor.tzinfo), "source_year_inferred", "medium")
            except ValueError:
                pass
        offsets = {"前天": -2, "昨天": -1, "今天": 0, "明天": 1, "后天": 2}
        for raw, days in offsets.items():
            if raw in text:
                add(raw, anchor + timedelta(days=days), "relative_to_source_created_at", "medium")
    for raw in ("上周", "上个月", "去年", "过去", "此前", "之前", "当时"):
        if raw in text:
            add(raw, None, "unresolved_relative_reference", "low")
    return found[:20]


def _select_lane(title: str, text: str, tags: list[str], topics: list[str]) -> tuple[str, str]:
    combined = "\n".join([title, text, *tags, *topics]).lower()
    labels = " ".join(tags + topics).lower()
    has_work = any(word.lower() in combined for word in WORK_WORDS)
    has_meeting = any(word.lower() in combined for word in MEETING_WORDS)
    if has_work and has_meeting:
        confidence = "high" if any(word.lower() in labels for word in WORK_WORDS + MEETING_WORDS) else "medium"
        return "company_meeting", confidence
    if any(word.lower() in combined for word in LEARNING_WORDS):
        return "learning", "high" if any(word.lower() in labels for word in LEARNING_WORDS) else "medium"
    if has_work:
        return "work_project", "high" if any(word.lower() in labels for word in WORK_WORDS) else "medium"
    if any(word.lower() in combined for word in REFLECTION_WORDS):
        return "personal_reflection", "high" if any(word.lower() in labels for word in REFLECTION_WORDS) else "medium"
    if any(word.lower() in combined for word in DAILY_WORDS):
        return "personal_daily", "high" if any(word.lower() in labels for word in DAILY_WORDS) else "medium"
    if any(word.lower() in combined for word in REFERENCE_WORDS):
        return "reference", "high" if any(word.lower() in labels for word in REFERENCE_WORDS) else "medium"
    return "unclassified", "low"


def classify_getnote(
    *,
    title: str,
    text: str,
    tags: list[str],
    topics: list[str],
    created_at: Any,
    updated_at: Any,
    root: Path | None = None,
) -> dict[str, Any]:
    """Classify capture context without claiming that transcript statements belong to the user."""
    policy = load_timeline_policy(root)
    lane, confidence = _select_lane(title, text, tags, topics)
    is_work = lane in WORK_LANES
    aliases = [str(x).strip() for x in policy.get("user_speaker_aliases", []) if str(x).strip()]
    labelled_speakers = sorted(set(re.findall(r"(?:^|\n)\s*([^\n：:]{1,20})[：:]", text)))
    alias_matches = [
        name for name in aliases
        if re.search(r"(?:^|\n)\s*[【\[]?" + re.escape(name) + r"[】\]]?\s*[：:]", text, re.IGNORECASE)
    ]
    mentioned_aliases = [name for name in aliases if name.lower() in text.lower()]
    reported_speech_aliases = [
        name for name in aliases
        if re.search(re.escape(name) + r"\s*(?:说|表示|认为|提到|介绍|回答|回应|建议|强调|指出)", text)
    ]

    if lane == "company_meeting" or len(labelled_speakers) > 1:
        speaker_scope = "multi_party"
    elif lane == "learning":
        speaker_scope = "external_or_unknown"
    elif lane in {"personal_reflection", "personal_daily"}:
        speaker_scope = "first_person_unverified"
    else:
        speaker_scope = "unverified"

    if lane == "learning":
        claim_role = "learned_or_quoted_content"
    elif lane == "company_meeting":
        claim_role = "observed_conversation"
    elif lane in {"personal_reflection", "personal_daily"}:
        claim_role = "self_candidate_unconfirmed"
    else:
        claim_role = "captured_content"

    if alias_matches:
        user_authorship = "partial_explicit_speaker_attribution" if speaker_scope == "multi_party" else "explicit_speaker_attribution"
        attribution_confidence = "high"
    elif reported_speech_aliases:
        user_authorship = "transcript_attributed_to_user"
        attribution_confidence = "medium"
    else:
        user_authorship = "unverified"
        attribution_confidence = "low"

    occurred_at = _normalize_time(created_at)
    source_updated_at = _normalize_time(updated_at)
    anchor = _parse_time(created_at)
    mentioned_times = _mentioned_times(text, anchor)
    resolved = [_parse_time(item.get("resolved_at")) for item in mentioned_times]
    if anchor and any(value and value.date() < anchor.date() for value in resolved):
        event_relation = "retrospective"
    elif anchor and any(value and value.date() > anchor.date() for value in resolved):
        event_relation = "planned_or_future"
    elif any(item["basis"] == "unresolved_relative_reference" for item in mentioned_times):
        event_relation = "retrospective_unresolved"
    else:
        event_relation = "at_or_near_capture"
    return {
        "temporal": {
            "occurred_at": occurred_at,
            "captured_at": occurred_at,
            "source_updated_at": source_updated_at,
            "time_basis": "source_created_at",
            "confidence": "medium" if occurred_at else "low",
            "mentioned_times": mentioned_times,
            "event_relation": event_relation,
        },
        "provenance": {
            "speaker_scope": speaker_scope,
            "detected_speaker_labels": labelled_speakers,
            "matched_user_aliases": alias_matches,
            "mentioned_user_aliases": mentioned_aliases,
            "reported_speech_user_aliases": reported_speech_aliases,
            "user_authorship": user_authorship,
            "attribution_confidence": attribution_confidence,
            "subject_relation": "user_explicitly_mentioned" if mentioned_aliases else "unknown_or_not_explicit",
            "identity_aliases_confirmed": not bool(policy.get("identity_confirmation_required", True)),
            "claim_role": claim_role,
            "attribution_rule": "A recording proves that content was captured; it does not prove every statement is the user's belief.",
        },
        "triage": {
            "lane": lane,
            "confidence": confidence,
            "requires_review": lane in {"company_meeting", "personal_reflection", "unclassified"},
            "requires_user_confirmation": lane in {"personal_reflection", "personal_daily", "unclassified"},
            "canonical_target": {
                "company_meeting": "meeting",
                "work_project": "timeline",
                "learning": "knowledge",
                "reference": "asset",
                "personal_daily": "daily",
                "personal_reflection": "self_candidate",
                "unclassified": "review",
            }[lane],
        },
        "access": {
            "context_scopes": ["work"] if is_work else ["self"],
            "allowed_agents": [],
            "denied_agents": [],
        },
    }


def routing_hints_for_getnote(context: dict[str, Any], *, kind: str, has_audio: bool) -> list[str]:
    lane = str(context.get("triage", {}).get("lane") or "unclassified")
    scopes = context.get("access", {}).get("context_scopes") or ["self"]
    hints = {
        "getnote",
        "getnote:transcript" if has_audio else f"getnote:{kind}",
        f"lane:{lane}",
        "timeline:candidate",
        f"scope:{scopes[0]}",
    }
    return sorted(hints)
