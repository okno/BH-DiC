#!/usr/bin/env python3
"""Atomically apply the read-only HR Discord role boundary to a protected env file."""

from __future__ import annotations

import argparse
import os
import stat
import tempfile
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--role-id", required=True)
    parser.add_argument("--enable-dms", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    env_path = arguments.env_file.resolve(strict=True)
    role_id = arguments.role_id
    if not role_id.isdigit() or int(role_id) < 1:
        raise RuntimeError("invalid Discord role id")
    info = env_path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeError("unsafe env file type")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise RuntimeError("unsafe env file mode")
    lines = env_path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    counts: dict[str, int] = {}
    for line in lines:
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key and key.replace("_", "A").isalnum() and key.upper() == key:
            counts[key] = counts.get(key, 0) + 1
            values[key] = value
    duplicates = sorted(key for key, count in counts.items() if count > 1)
    if duplicates:
        raise RuntimeError("duplicate env keys: " + ",".join(duplicates))
    guild_id = values.get("DISCORD_GUILD_ID", "")
    if not guild_id.isdigit() or int(guild_id) < 1:
        raise RuntimeError("invalid Discord guild id")
    updates = {
        "DISCORD_PII_ROLE_IDS": role_id,
        "DISCORD_PAYROLL_ROLE_IDS": role_id,
        "DISCORD_DOCUMENT_METADATA_ROLE_IDS": role_id,
        "DISCORD_PROTECTED_DOCUMENT_ROLE_IDS": role_id,
        "DISCORD_PUBLISH_SENSITIVE_CHANNEL_RESPONSES": "false",
    }
    if arguments.enable_dms:
        updates.update(
            {
                "DISCORD_DM_ALLOWED_ROLE_IDS": role_id,
                "DISCORD_DM_AUTH_GUILD_ID": guild_id,
                "DISCORD_ALLOW_DMS": "true",
                "DISCORD_SENSITIVE_DELIVERY_MODE": "dm_or_ephemeral",
            }
        )
    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if "=" in line:
            key = line.split("=", 1)[0]
            if key in updates:
                rendered.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        rendered.append(line)
    for key, value in updates.items():
        if key not in seen:
            rendered.append(f"{key}={value}")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".env.update.", dir=env_path.parent, text=True
    )
    try:
        os.fchmod(descriptor, 0o600)
        os.fchown(descriptor, info.st_uid, info.st_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write("\n".join(rendered) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, env_path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
    print(f"UPDATED_KEYS={len(updates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
