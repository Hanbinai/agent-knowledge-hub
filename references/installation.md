# Safe client installation (3.7.0)

The installer is independent of the knowledge engine. Schema and MCP API stay at 3.4. It neither migrates existing knowledge nor edits AGENTS.json, downloads software, sends configuration to a service, kills clients, or grants Self permissions.

## Supported adapters and discovery evidence

Only an existing configuration file is evidence of a configured client. An absent file is reported as not-detected; this is not proof the app is absent. Launch/configure that app once, or create its documented empty configuration yourself, then preview again. There is no recursive scan of private chats, credentials or arbitrary disks.

| Client | Windows and macOS user configuration | Structure | Basis |
|---|---|---|---|
| Codex | CODEX_HOME/config.toml, otherwise ~/.codex/config.toml | mcp_servers | Existing local config and bundled example |
| Claude Code | ~/.claude.json | mcpServers | https://code.claude.com/docs/en/mcp |
| Cursor | ~/.cursor/mcp.json | mcpServers | https://cursor.com/docs/context/mcp |
| WorkBuddy | ~/.workbuddy/mcp.json | mcpServers | Windows local installation verified; macOS fixture only |
| ZCode | ~/.zcode/cli/config.json | mcp.servers | Windows local installation verified; macOS fixture only |

Claude Desktop and Windsurf are deliberately not auto-written in this release. Their current deployment/configuration locations have not been verified here; the Windsurf documentation URL now redirects to a different product/path. Unknown client layouts require a separately reviewed adapter, not guessed overwrites. macOS paths and serialization have fixture coverage, not physical macOS app acceptance.

## Prepare a portable source installation

Use Python 3.11 or newer. Do not share an installed directory wholesale: it may contain private operational scripts, historical backups, .venv, credentials or user-specific documentation. Ship only reviewed runtime source, the safe initializer, install_clients.py, launchers, this guide and requirements. Never include a live Hub, client config, SOURCE metadata with local paths, .state, backups, local logs or a copied venv. Dependencies are installed by the recipient explicitly, not by discovery.

Windows PowerShell, from the reviewed source directory:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r .\mcp_server\requirements.txt
& .\.venv\Scripts\python.exe .\scripts\init_hub.py --hub "$HOME\KnowledgeHub" --safe-install
.\install.ps1 --hub "$HOME\KnowledgeHub"
```

macOS Terminal, from the reviewed source directory:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r mcp_server/requirements.txt
.venv/bin/python scripts/init_hub.py --hub "$HOME/KnowledgeHub" --safe-install
sh install.command --hub "$HOME/KnowledgeHub"
```

For an existing Hub skip initialization and pass its existing absolute path. Always use --safe-install for a NEW shared installation: agents is empty and the fallback is work-only. The legacy initializer remains available for backward compatibility and has privileged predefined roles; it is not the distribution default. Safe initialization never overwrites an existing policy.

## Preview, apply, verify

Preview prints client, exact path, discovery basis, action and hashes, not existing values or secrets. Save/review its fingerprint. To apply repeat identical options plus `--apply --expect <fingerprint>`. `--client codex --client zcode` limits selection. Conflicting/invalid configurations block the batch; no unknown entry is overwritten. Already-correct entries are preserved byte-for-byte and are NOT adopted as installer-owned. New identities must resolve to work-only in the existing policy or installation is refused.

Each new entry gets a sibling `.pkh-install.json` ownership receipt and an exclusive `.pkh-backup-<id>` original-byte backup. Backups can contain credentials: keep them local, restrict the directory permissions and never include them in a distribution. JSON unrelated values are retained (formatting is normalized); TOML is appended without changing existing bytes, with a semantic equality guard that rejects unsupported layouts. Duplicate JSON keys, linked paths, malformed tables, changed managed entries, stale receipts and existing lock files fail closed.

Per-file exclusive installer locks and hash checks immediately before atomic replacement protect against cooperating installers and changes observed since preview. Clients do not honor this lock; there remains an unavoidable narrow check-to-replace race with arbitrary external writers. Avoid editing/saving the same config during apply. The operation is atomic per file, NOT a cross-client transaction: a later failure may leave earlier clients installed. Inspect each receipt/backup after interruption; do not automatically replay over unknown changes. A crash can leave a receipt before config publication; manual recovery is intentional. There is no automatic rollback over a subsequently modified file.

After apply, start/reconnect only the MCP connection when convenient and call health_check. Already-running MCP processes retain their previously loaded version; a fresh process check does not certify those processes. Installer never forces a client restart.

## Uninstall

Preview with the same hub and client selection plus `--uninstall`; apply its new fingerprint with `--apply --expect <fingerprint>`. Only unchanged receipt-owned entries are removed. Unmanaged/pre-existing connections remain. Other servers/settings, dependencies, all knowledge and the Hub directory are preserved. The receipt is archived with `.removed-<id>`; backups remain available locally. Edited TOML blocks or entry objects are refused rather than deleted.

## First use: optional onboarding

After connecting, ask the Agent to read get_context or say "start onboarding". There is no automatic popup. The read-only readiness contract offers an invitation for a new user; questions begin only with consent, 1-2 per turn. Existing confirmed preferences suppress repeat invitations. Occupation is optional; students, retired people, multiple roles and uncertain goals are supported. Pause, skip, complete, resume or use answers only in this conversation. Raw answers are not stored by onboarding. Only fixed progress is persisted per bound Agent unless conversation-only mode is chosen. Sharing a preference still requires separate content and recipient consent through record_user_preference; no Self permission is enabled. Local files are not an encrypted vault, and chat/retrieved information enters the client model context. See [the onboarding guide](onboarding.md).
