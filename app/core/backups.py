"""Reading backups on the drive and talking to the host backup script.

The host script (/usr/local/bin/server-backup.sh) does the actual backups.
The bot only reads archives, writes a request file, and reads the status file.
"""
import datetime
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from core import config

# name -> (folder under BACKUP_ROOT, label). Must match TARGETS in the host script.
SOURCES = {
    "foundry-v13": ("foundry/v13", "Foundry v13"),
    "foundry-v14": ("foundry/v14", "Foundry v14"),
    "obsidian-couchdb": ("obsidian/couchdb", "Obsidian notes (CouchDB)"),
    "obsidian-wiki-content": ("obsidian/wiki-content", "Wiki content"),
}


@dataclass
class Archive:
    filename: str
    path: Path
    day: str            # YYYY-MM-DD (server local time, from the name)
    label: str          # "YYYY-MM-DD HH:MM"
    mtime: int          # epoch; the script stamps it with the backup start time
    size: int
    tiers: list[str] = field(default_factory=list)


def _pattern(name: str) -> re.Pattern:
    return re.compile(
        rf"^{re.escape(name)}-(\d{{4}}-\d{{2}}-\d{{2}})_(\d{{2}})-(\d{{2}})\.tar\.gz$"
    )


def archives(name: str) -> list[Archive]:
    """All archives for a source, newest first, each tagged with its retention tiers."""
    folder = Path(config.BACKUP_ROOT) / SOURCES[name][0]
    pat = _pattern(name)
    found = []
    if folder.is_dir():
        for p in folder.iterdir():
            m = pat.match(p.name)
            if not m:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            found.append(Archive(
                filename=p.name, path=p, day=m.group(1),
                label=f"{m.group(1)} {m.group(2)}:{m.group(3)}",
                mtime=int(st.st_mtime), size=st.st_size,
            ))
    found.sort(key=lambda a: a.filename, reverse=True)
    _tag_tiers(found)
    return found


def _tag_tiers(items: list[Archive]) -> None:
    """Same rules as prune() in the host script."""
    for a in items[:config.RETAIN_RECENT]:
        a.tiers.append("recent")
    for key, limit, label in (
        (lambda a: a.day, config.RETAIN_DAILY, "daily"),
        (lambda a: datetime.date.fromisoformat(a.day).isocalendar()[:2], config.RETAIN_WEEKLY, "weekly"),
    ):
        seen = []
        for a in items:
            k = key(a)
            if k not in seen and len(seen) < limit:
                seen.append(k)
                a.tiers.append(label)


def human_size(n: float) -> str:
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit in ("B", "K") else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n}"


def read_json(path: str) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def read_status() -> dict | None:
    return read_json(config.BACKUP_STATUS)


def request_backup(force: bool, only: list[str]) -> str:
    """Write a request for the host's server-backup.path to pick up. Returns its id."""
    for n in only:
        if n not in SOURCES:
            raise ValueError(f"unknown source {n}")
    rid = uuid.uuid4().hex[:12]
    tmp = config.BACKUP_REQUEST + ".tmp"
    with open(tmp, "w") as f:
        f.write(f"id={rid}\nforce={1 if force else 0}\nonly={' '.join(only)}\n")
    os.replace(tmp, config.BACKUP_REQUEST)
    return rid
