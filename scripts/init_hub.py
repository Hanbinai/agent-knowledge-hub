#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, uuid
from pathlib import Path
from datetime import datetime, timezone

DIRS = [
    "00_System", "01_Inbox/events", "01_Inbox/sessions/raw", "01_Inbox/sources/getnote", "01_Inbox/drop/getnote",
    "01_Inbox/drop/meeting", "01_Inbox/drop/chat", "01_Inbox/drop/web", "01_Inbox/drop/files",
    "01_Inbox/review", "10_Knowledge", "20_Projects", "30_Ideas", "40_Decisions",
    "50_Tasks", "50_Tasks/records", "60_Daily",
    "70_Self", "70_Self/71_Identity", "70_Self/72_Values", "70_Self/73_Beliefs",
    "70_Self/74_Principles", "70_Self/75_Patterns", "70_Self/76_Self_Insights",
    "70_Self/77_Human_Nature", "70_Self/78_Life_Reflections", "70_Self/79_Evolution",
    "80_Reviews", "90_Assets", "99_Archive", ".state", ".state/connectors"
]

PROJECT_ROUTING_RULES = {
    "schema_version": "1.0",
    "policy": {
        "automatic_binding": "only explicit project hints or exact project tags/topics",
        "title_keywords": "review only",
        "speaker_identity": "never used for project routing",
        "ambiguous_action": "review",
        "unmatched_action": "leave unbound",
    },
    "projects": [],
}

def write_new(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(text)
        return True
    except FileExistsError:
        return False

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", required=True, type=Path)
    ap.add_argument("--owner", default="owner")
    ap.add_argument("--safe-install", action="store_true", help="New installations: no pre-authorized identities")
    args = ap.parse_args()
    root = args.hub.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / "knowledge-hub.json"
    if manifest.exists():
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data.get("hub_type") != "agent-knowledge-hub":
            raise SystemExit("Refusing to reuse directory: existing knowledge-hub.json is unrelated.")
    elif any(root.iterdir()):
        raise SystemExit("Refusing to initialize a non-empty unrecognized directory.")
    for d in DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)
    hub_id = "hub_" + uuid.uuid4().hex
    created = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    write_new(manifest, json.dumps({
        "schema_version": "3.4", "hub_type": "agent-knowledge-hub", "hub_id": hub_id,
        "owner": args.owner, "created_at": created, "write_mode": "mcp-or-inbox-first",
        "canonical_writer": "consolidator", "concurrency": "cross-process-file-lock",
        "mcp_api_version": "3.4", "index_file": "00_System/INDEX.jsonl",
        "graph_file": "00_System/GRAPH.jsonl", "search_index": ".state/search.db",
        "self_model": "70_Self", "access_model": "least-privilege",
        "connectors": ["getnote"],
        "project_registry": "00_System/PROJECT_REGISTRY.json",
        "project_handoff": "20_Projects/<project>/checkpoints",
        "project_routing_rules": "00_System/PROJECT_ROUTING_RULES.json",
    }, ensure_ascii=False, indent=2) + "\n")
    write_new(root / "README.md", """# Agent Knowledge Hub V3.4\n\n所有本地 Agent 共用的唯一知识中枢。GET笔记/得到大脑作为统一 raw 采集入口，通过 OpenAPI 单向同步后按 work/self 分流。\n\n- Work Memory：项目、知识、任务、决策、方法论。\n- Project Handoff：稳定项目身份、目录映射、会话开始读取、会话结束结构化交接。\n- Project Routing：只按显式项目提示或精确项目标签自动绑定；宽泛主题进入人工复核。\n- Self Model：身份、价值观、信念、模式、人性观察、人生反思、认知演变。\n- Self Model 默认受保护；共享一个系统，不代表所有 Agent 能读取全部上下文。\n- Inbox 保存原始来源、发生时间和归因不确定性；正式记录保留证据、成熟度、确认状态与演化关系。\n""")
    write_new(root / "00_System/CURRENT_CONTEXT.md", "# Current Work Context\n\n只放当前长期有效的工作身份、核心项目、重要约束和近期优先级。个人价值观/诊断不要放这里。\n")
    write_new(root / "70_Self/00_SELF_CONTEXT.md", "# Self Context\n\n这是受保护的个人认知摘要。只放已确认或高置信度、对个人助手长期有用的内容，并链接详细记录。Agent 推断不得直接写成事实。\n")
    write_new(root / "00_System/TAXONOMY.md", "# Taxonomy\n\n## Work\n- AI工程化 / Agent / 自动化\n- 电商运营 / 增长 / 全域营销\n- 数据分析 / BI / MySQL / 数据中台\n- 电商设计 / 生图 / 视频 / Prompt\n- 产品 / FDE / 软件工程\n- 创业 / OPC / 公司经营 / 政策\n- 内容创作 / X / 个人品牌\n\n## Self\n- Identity / Values / Beliefs / Principles\n- Behavior / Decision / Emotion / Cognitive Patterns\n- Self Insights / Human Nature / Life Reflections / Evolution\n")
    write_new(root / "00_System/ROUTING_RULES.md", "# Routing Rules\n\n`#灵感→30_Ideas`；`#待办/#事务→50_Tasks`；`#决策→40_Decisions`；`#知识/#方法论→10_Knowledge`；`#复盘→80_Reviews`；`#日常→60_Daily`；`#素材→90_Assets`；`#项目:xxx` 建立项目关联。\n\nSelf：`#价值观/#信念/#原则/#行为模式/#人性/#反思/#自我认知` 先按观察/假设处理；除非用户明确表达或确认，不得升级为 confirmed。\n")
    write_new(root / "00_System/ACCESS_POLICY.md", "# Access Policy\n\n- 默认 Agent：work scope，不能读取 Self Model。\n- `personal-assistant` / `consolidator`：可按 `AGENTS.json` 获得 self_access。\n- Self 记录仍可通过 allowed_agents / denied_agents 做单条收紧。\n- 最小上下文原则：能完成任务就不要加载更多。\n")
    write_new(root / "00_System/AGENT_REGISTRY.md", "# Agent Registry\n\n机器权限以 `AGENTS.json` 为准；此文件记录用途与人工说明。\n")
    write_new(root / "00_System/AGENTS.json", json.dumps({
        "default": {"scopes": ["work"], "self_access": False},
        "agents": {} if args.safe_install else {
            "personal-assistant": {"scopes": ["work", "self"], "self_access": True},
            "consolidator": {"scopes": ["work", "self"], "self_access": True},
            "codex": {"scopes": ["work"], "self_access": False},
            "workbuddy": {"scopes": ["work"], "self_access": False},
            "zcode": {"scopes": ["work"], "self_access": False}
        }
    }, ensure_ascii=False, indent=2) + "\n")
    write_new(root / "00_System/TIMELINE_POLICY.json", json.dumps({
        "schema_version": "1.0", "raw_entrypoint": "getnote/raw",
        "user_speaker_aliases": [], "identity_confirmation_required": True,
        "time_fields": ["occurred_at", "captured_at", "source_updated_at", "ingested_at"],
        "lanes": {
            "company_meeting": "work", "work_project": "work", "learning": "work", "reference": "work",
            "personal_daily": "self", "personal_reflection": "self", "unclassified": "self"
        }
    }, ensure_ascii=False, indent=2) + "\n")
    write_new(root / "00_System/INDEX.jsonl", "")
    write_new(root / "00_System/GRAPH.jsonl", "")
    write_new(root / "00_System/PROJECT_REGISTRY.json", json.dumps({
        "schema_version": "1.0", "projects": []
    }, ensure_ascii=False, indent=2) + "\n")
    write_new(root / "00_System/PROJECT_ROUTING_RULES.json", json.dumps(
        PROJECT_ROUTING_RULES, ensure_ascii=False, indent=2,
    ) + "\n")
    write_new(root / "50_Tasks/TASKS.md", "# Tasks\n\n| ID | Task | Project | Status | Source | Updated |\n|---|---|---|---|---|---|\n")
    print(root)

if __name__ == "__main__":
    main()
