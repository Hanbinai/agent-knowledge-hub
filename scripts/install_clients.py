#!/usr/bin/env python3
"""Conservative local MCP installation. Preview by default; no network or Hub writes."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import tomllib
import uuid

ROOT = Path(__file__).resolve().parents[1]
NAME = "agent-knowledge-hub"


class Refused(ValueError):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def safe_path(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise Refused("Linked configuration paths are not supported")
    if path.exists() and not path.is_file():
        raise Refused("Expected a regular file")


def read_bytes(path):
    safe_path(path)
    return path.read_bytes() if path.exists() else None


def strict_json(text):
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                raise Refused("Duplicate JSON keys")
            out[k] = v
        return out
    return json.loads(text, object_pairs_hook=pairs)


def adapters(home, platform, environ=None):
    if platform not in ("win32", "darwin"):
        raise Refused("Supported platforms: Windows and macOS")
    env = environ or {}
    codex = Path(env.get("CODEX_HOME", home / ".codex"))
    return [
        ("codex", codex / "config.toml", ("mcp_servers",), "toml"),
        ("claude-code", home / ".claude.json", ("mcpServers",), "json"),
        ("cursor", home / ".cursor/mcp.json", ("mcpServers",), "json"),
        ("workbuddy", home / ".workbuddy/mcp.json", ("mcpServers",), "json"),
        ("zcode", home / ".zcode/cli/config.json", ("mcp", "servers"), "json"),
    ]


def connection(client, python, server, hub):
    entry = {"command": str(python), "args": [str(server), "--hub", str(hub)],
             "env": {"PKH_HOME": str(hub), "PKH_AGENT_ID": client}}
    if client != "codex":
        entry["type"] = "stdio"
    if client == "zcode":
        entry["enabled"] = True
    return entry


def parse(raw, fmt):
    text = (raw or b"").decode("utf-8-sig")
    data = tomllib.loads(text) if fmt == "toml" else strict_json(text or "{}")
    if not isinstance(data, dict):
        raise Refused("Configuration root must be an object")
    return data


def table(data, keys):
    value = data
    for key in keys:
        value = value.setdefault(key, {})
        if not isinstance(value, dict):
            raise Refused("Configuration server table is not an object")
    return value


def same_path(a, b):
    return isinstance(a, str) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def matches(actual, wanted):
    if not isinstance(actual, dict):
        return False
    if actual.get("disabled") is True or actual.get("enabled") is False or actual.get("type", "stdio") != "stdio":
        return False
    aa, wa = actual.get("args"), wanted["args"]
    env = actual.get("env", {})
    return (same_path(actual.get("command"), wanted["command"]) and isinstance(aa, list)
            and len(aa) == 3 and aa[1] == "--hub" and same_path(aa[0], wa[0]) and same_path(aa[2], wa[2])
            and isinstance(env, dict) and env.get("PKH_AGENT_ID") == wanted["env"]["PKH_AGENT_ID"]
            and same_path(env.get("PKH_HOME"), wanted["env"]["PKH_HOME"]))


def encode(data):
    return (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def render(raw, data, keys, fmt, wanted, remove=False, owned_block=None):
    if fmt == "json":
        entries = table(data, keys)
        if remove:
            del entries[NAME]
        else:
            entries[NAME] = wanted
        return encode(data), None
    text = (raw or b"").decode("utf-8-sig")
    if remove:
        if not owned_block or text.count(owned_block) != 1:
            raise Refused("Managed TOML block was edited")
        result = text.replace(owned_block, "", 1)
        expected = copy.deepcopy(data)
        del table(expected, keys)[NAME]
        parsed = tomllib.loads(result)
        # An empty implicit parent can disappear when its last child is removed.
        if parsed.get("mcp_servers", {}) == {}:
            parsed.setdefault("mcp_servers", {})
        if expected != parsed:
            raise Refused("TOML removal would change unrelated settings")
        return result.encode("utf-8"), None
    block = "\n# Agent Knowledge Hub managed entry\n[mcp_servers.agent-knowledge-hub]\n"
    block += "command = " + json.dumps(wanted["command"]) + "\n"
    block += "args = " + json.dumps(wanted["args"]) + "\n"
    block += "[mcp_servers.agent-knowledge-hub.env]\n"
    for key, value in wanted["env"].items():
        block += key + " = " + json.dumps(value) + "\n"
    result = text + block
    expected = copy.deepcopy(data)
    table(expected, keys)[NAME] = wanted
    if tomllib.loads(result) != expected:
        raise Refused("Unsupported TOML layout; refusing lossy rewrite")
    return result.encode("utf-8"), block


def plan(adapter, python, server, hub, uninstall=False):
    client, path, keys, fmt = adapter
    raw = read_bytes(path)
    receipt_path = path.with_name(path.name + ".pkh-install.json")
    receipt_raw = read_bytes(receipt_path)
    result = {"client": client, "path": str(path), "evidence": "config-file" if raw is not None else "not-detected",
              "before": digest(raw), "receipt_before": digest(receipt_raw), "status": "not-detected"}
    if raw is None:
        return result
    data = parse(raw, fmt)
    actual = table(data, keys).get(NAME)
    wanted = connection(client, python, server, hub)
    if uninstall:
        if receipt_raw is None:
            result["status"] = "unmanaged-preserved"
            return result
        receipt = strict_json(receipt_raw.decode("utf-8"))
        if receipt.get("path") != str(path) or receipt.get("client") != client or actual != receipt.get("entry"):
            raise Refused("Managed entry changed or receipt mismatch")
        new, _ = render(raw, data, keys, fmt, wanted, True, receipt.get("block"))
        result.update(status="remove", after=digest(new))
    elif actual is not None:
        result["status"] = "existing-preserved" if matches(actual, wanted) else "conflict"
        return result
    else:
        # Avoid creating a duplicate under another server name.
        for entry in table(data, keys).values():
            if isinstance(entry, dict) and (matches(entry, wanted) or any("agent-knowledge-hub" in str(a).lower() for a in entry.get("args", []) if isinstance(entry.get("args"), list))):
                result["status"] = "alias-conflict"
                return result
        if receipt_raw is not None:
            raise Refused("Stale installation receipt requires manual recovery")
        new, block = render(raw, data, keys, fmt, wanted)
        result.update(status="add", after=digest(new))
    return result


def replace_checked(path, expected, content):
    safe_path(path)
    if digest(read_bytes(path)) != expected:
        raise Refused("Concurrent configuration change detected")
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, tmp = tempfile.mkstemp(prefix=".pkh-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, mode)
        if digest(read_bytes(path)) != expected:
            raise Refused("Concurrent configuration change detected")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.replace(tmp, str(tmp) + ".unused")


def apply_one(adapter, expected, python, server, hub, uninstall=False):
    client, path, keys, fmt = adapter
    if expected["status"] not in ("add", "remove"):
        return expected
    lock = path.with_name(path.name + ".pkh-install.lock")
    safe_path(lock)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise Refused("Installer lock exists; inspect interrupted installation before retry")
    os.close(fd)
    try:
        current = plan(adapter, python, server, hub, uninstall)
        if current != expected:
            raise Refused("Preview is stale; generate a new preview")
        raw = read_bytes(path)
        receipt_path = path.with_name(path.name + ".pkh-install.json")
        wanted = connection(client, python, server, hub)
        receipt = strict_json(read_bytes(receipt_path).decode()) if uninstall else None
        new, block = render(raw, parse(raw, fmt), keys, fmt, wanted, uninstall, receipt.get("block") if receipt else None)
        backup = path.with_name(path.name + ".pkh-backup-" + uuid.uuid4().hex)
        fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if not uninstall:
            receipt = {"version": 1, "client": client, "path": str(path), "entry": wanted,
                       "block": block, "backup": str(backup), "before": digest(raw), "after": digest(new)}
            replace_checked(receipt_path, expected["receipt_before"], encode(receipt))
        replace_checked(path, expected["before"], new)
        if uninstall:
            if digest(read_bytes(receipt_path)) != expected["receipt_before"]:
                raise Refused("Receipt changed during removal; inspect manually")
            os.replace(receipt_path, receipt_path.with_name(receipt_path.name + ".removed-" + uuid.uuid4().hex))
        return dict(expected, status="removed" if uninstall else "installed", backup=str(backup))
    finally:
        os.replace(lock, lock.with_name(lock.name + ".released"))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hub", required=True, type=Path)
    ap.add_argument("--python", type=Path, default=Path(sys.executable))
    ap.add_argument("--client", action="append", help="Limit to named clients (repeatable)")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--expect", help="Required preview fingerprint with --apply")
    args = ap.parse_args()
    hub, python = args.hub.expanduser().resolve(), args.python.expanduser().resolve()
    server = ROOT / "mcp_server/server.py"
    manifest = strict_json((hub / "knowledge-hub.json").read_text("utf-8-sig"))
    if manifest.get("hub_type") != NAME or not python.is_file() or not server.is_file():
        raise Refused("Existing Hub, Python executable and server are required")
    selected = adapters(Path.home(), sys.platform, os.environ)
    if args.client:
        if set(args.client) - {a[0] for a in selected}:
            raise Refused("Unknown client")
        selected = [a for a in selected if a[0] in args.client]
    plans = []
    for adapter in selected:
        try:
            plans.append(plan(adapter, python, server, hub, args.uninstall))
        except (ValueError, OSError, TypeError) as exc:
            plans.append({"client": adapter[0], "path": str(adapter[1]), "status": "refused", "reason": type(exc).__name__})
    policy_raw = read_bytes(hub / "00_System/AGENTS.json")
    policy = strict_json(policy_raw.decode("utf-8-sig")) if policy_raw else {}
    for item in plans:
        if item["status"] == "add":
            effective = dict(policy.get("default", {"scopes": ["work"], "self_access": False}))
            effective.update(policy.get("agents", {}).get(item["client"], {}))
            if effective.get("self_access", False) or effective.get("scopes", ["work"]) != ["work"]:
                item.update(status="refused", reason="identity-not-work-only")
    token = digest(encode({"plans": plans, "hub": str(hub), "python": str(python), "policy": digest(policy_raw), "uninstall": args.uninstall}))
    if args.apply:
        if args.expect != token:
            raise Refused("--expect must match a fresh preview fingerprint")
        if any(p["status"] in ("conflict", "alias-conflict", "refused") for p in plans):
            raise Refused("Resolve conflicts or explicitly select other clients before applying")
        plans = [apply_one(a, p, python, server, hub, args.uninstall) for a, p in zip(selected, plans)]
    print(json.dumps({"mode": "apply" if args.apply else "preview", "fingerprint": token,
                      "platform": sys.platform, "clients": plans}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, TypeError) as exc:
        # Config parse errors may contain credentials: never print their raw text.
        print("Installation refused (" + type(exc).__name__ + "). Inspect configuration or regenerate preview locally.", file=sys.stderr)
        raise SystemExit(2)
