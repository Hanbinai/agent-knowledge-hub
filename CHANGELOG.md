# Changelog

## 3.8.0

- License the Hub software and documentation under PolyForm Noncommercial 1.0.0, with a required copyright notice; retain the original Apache-2.0 or MIT terms for all third-party role references.
- Add portable public role catalog: all 252 skills / 22 categories from pinned knowledge-work-plugins, separately from lessons, marketplace pointers, luobo-ppt and taobao-native.
- Preserve full original SKILL.md text and all source licenses/attribution with source hashes; 187 Apache-2.0 and 65 MIT. No private canonical IDs, overrides, ACLs or favorites in distribution.
- Add five MCP tools for pagination/search, details, task matching and explicitly confirmed per-bound-Agent favorites with revision checks, exact replay, locking and atomic writes.
- Keep public references untrusted and unreviewed; preparation still requires existing ACL/readiness/hash checks. No execution, dependency installation or permission grants.
- Connect optional skippable task-based skill selection to 3.7 onboarding with compact natural-context entry; never store task queries or infer a profession.
- Add isolated safety/STDIO regression tests and portable allowlist packaging. macOS remains fixture-only, not hardware validated.

## 3.7.0

- Add optional profession-neutral progressive onboarding through read-only natural context entries and two explicit MCP tools; existing schema/API remain compatible.
- Persist only fixed per-process-Agent progress, with existing FileLock, atomic writes, revision checks and exact last-operation replay. No raw answers, Self access or permission changes.
- Require consent before questions and transitions; allow pause, skip, completion and explicit resume. Suppress invitations for existing readable confirmed preferences; reuse preference content/audience double confirmation.
- Document inclusive adaptive questions, conversation-only mode, local filesystem/model privacy boundaries, no automatic popup, and portable allowlist packaging.

## 3.6.0

- Add conservative Windows/macOS client discovery and explicit fingerprint-bound installation, local backups, ownership receipts, idempotency, conflict refusal and managed-only removal.
- Support verified Codex, Claude Code, Cursor, WorkBuddy and ZCode configuration shapes; preserve existing connections and unrelated settings. macOS acceptance is fixture-only pending a real device.
- Add safe new-Hub initialization with no predefined identities, platform launchers and portable installation guidance. Existing permissions and core knowledge semantics stay unchanged; schema/API remain 3.4.

## 3.5.3

- Enforce project, record and Self-scope ACLs before maturity writes; retain the source audience on audit events.
- Require readable evidence events and a verification note for L3+, applicability and stability evidence for L4+, and executable steps, inputs, outputs, failure handling and a validation result for L5. These are structural/source checks, not automatic semantic verification.
- Archive authorized Self Inbox events as protected daily evidence or unconfirmed Self observations, retaining the original source and ACL. Serialize publication per event and recover source status and both indexes on retry without duplicate records.
- Include authorized Self events in oldest-first consolidation proposals (default batch 50); retain work/Self domains and apply the same caller permissions to duplicate searches.
- Retry transient Windows lock reads and incomplete lock JSON; never reclaim a known live owner solely because the lock is old.
- Correct the STDIO smoke test to validate only previously delivered digests, allowing omitted sections to fit in a later response.

## 3.5.1

- Preserve stable project summaries and custom Markdown sections during checkpoint publication; explicit project updates retain existing handoffs.
- Retrieve relevant checkpoint history plus the latest handoff, with record IDs, titles, paths and timestamps under existing ACL and context-budget rules.
- Resume unfinished checkpoint transactions even after canonical publication; require both indexes before marking completion and preserve newer handoffs during recovery.
- Require bound identities for health, connector status and index rebuild tools; restrict the optional HTTP transport to loopback.
- Fix Windows lock-owner liveness checks with OpenProcess/WaitForSingleObject instead of os.kill(pid, 0); preserve inaccessible owners and close query handles.
- Add an isolated regression entry point covering existing suites, GET function tests, crash recovery, concurrent idempotency, process safety and fresh three-identity STDIO integration.

## 3.4.1

- Protect original Self evidence and confirmation events with Self scope and record-level audiences; fail closed for legacy Self events without access metadata.
- Add confirmed collaboration preferences with explicit sharing consent, version history, optimistic concurrency and retirement. Private evidence is never included in preference context.
- Load current shared preferences through `get_context` and `start_project_session`; hide superseded or retired preferences from default searches.
- Add `get_user_profile` / `record_user_preference`, cross-Agent tests and Self evidence validation.

## 3.4.0

- Made project checkpoint idempotency cross-process safe with per-key locks, deterministic IDs and recoverable transaction journals.
- Enforced project ACLs across project listing, resolution, session startup and checkpoint timelines.
- Added `update_project_registration` for removing stale roots/aliases and archiving/reactivating registrations.
- Added deterministic GET-note project suggestions and `PROJECT_ROUTING_RULES.json`; speaker identity is never a routing signal.
- Bound durable MCP `source_app` provenance to `PKH_AGENT_ID` instead of trusting caller input.
- Expanded Hub validation to cross-check registry, PROJECT.md, checkpoints, source events, transaction state, FTS counts and client configuration.

## 3.3.0

- Added `PROJECT_REGISTRY.json` with stable project IDs, aliases, repository URLs and root-path resolution.
- Added `list_projects`, `resolve_project`, `register_project`, `start_project_session`, `get_project_timeline` and `record_project_checkpoint` MCP tools.
- Added structured, work-scoped project checkpoints under `20_Projects/<project>/checkpoints/YYYY/MM/`.
- Added idempotent checkpoint keys and process-bound Agent provenance.
- Added automatic `PROJECT.md` Current Summary / Latest Handoff / Update Log refresh after each checkpoint.
- Added cross-Agent tests and real STDIO smoke coverage for the project handoff protocol.

## 3.2.0

- Added a single `getnote/raw` capture boundary with deterministic work/self triage.
- Added event schema 1.2 fields for occurrence time, capture/update time, speaker attribution, claim role and event-level access.
- Added privacy-aware, paginated `list_inbox_events`, plus `get_event`, `review_event`, `get_timeline` and idempotent `promote_event_to_record` MCP tools.
- Added canonical meeting/timeline/daily/review/asset promotion from the original Inbox event, avoiding orphaned source events.
- Added non-destructive GET event enrichment with mandatory ZIP backup on apply.
- Updated consolidation to process every readable pending event by default and include evidence metadata in proposals.
- Added real STDIO protocol smoke testing for WorkBuddy and ZCode identities.

## 3.1.0

- Added one-way GET笔记 / 得到大脑 OpenAPI connector.
- Uses official `note/list` for change discovery and `note/detail` for full source data.
- Voice notes prioritize `audio.original` as the evidence/transcript; no separate ASR is required.
- Added local source snapshots at `01_Inbox/sources/getnote/<note_id>/`.
- Added connector state and idempotency at `.state/connectors/getnote.json`; credentials are never stored there.
- Added note-version fingerprints so unchanged notes do not create duplicate Inbox events; updated notes create new provenance events.
- Added conservative request throttling and retry/backoff for 429/5xx/network failures.
- Added connector-level cross-process lock to prevent duplicate concurrent sync jobs.
- Added `get_connector_status` MCP tool (local status only, no external call, no credential exposure).
- Added V3 -> V3.1 non-destructive migration and Windows sync template.
- Kept manual GetNote Drop Zone as fallback.

## 3.0.0

- Added protected Self Model: identity, values, beliefs, principles, patterns, self insights, human-nature observations, life reflections and cognitive evolution.
- Added explicit user-confirmation guardrails for personal inference.
- Added L0-L5 maturity model and promotion rules.
- Added least-privilege Agent access via `AGENTS.json` and process-bound `PKH_AGENT_ID`.
- Added Knowledge Graph (`GRAPH.jsonl`) with contradiction/evolution/influence relations.
- Added local SQLite FTS5 search index.
- Added V2→V3 non-destructive migration with legacy Self candidate review report.
- Added deterministic Inbox consolidation proposals.
- Added agent configuration helper and personal-assistant MCP template.
# 3.5.0

- Added bounded `get_personalized_context` snapshots: project handoff, task-relevant shared preferences, confirmed ACL-filtered Self principles, work evidence, and per-section digests for repeat reads.
- Closed project detail and Self overview ACL gaps; project search now requires explicit project membership. Self type is protected even when legacy metadata says work.
- Enforced parent project ACLs on linked records, source events and writes; project-linked decisions/tasks now require an existing canonical project, and task/project updates retain existing access controls.
- Require process-bound `PKH_AGENT_ID` for every MCP Hub read/write tool; request parameters cannot supply an identity when the server is unbound.
- Self originals can no longer be written directly into work scope. Added backed-up, repeatable migration for legacy Self records and validation of their scope.
- Updated local guidance to distinguish protected personal records, approved work projections, and task-time context assembly.
