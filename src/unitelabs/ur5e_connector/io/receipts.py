"""Incremental receipt validation; index metadata never replaces changed-file validation."""

import hashlib
import json
import os
import pathlib
import sys
import tempfile
import time

from .errors import CommandRejected

INDEX = ".receipt-index"
TERMINAL = {"verified", "not_sent"}


def signature(path: pathlib.Path) -> list[int]:
    """Bind an index entry to file identity and mutation metadata."""
    stat = path.stat()
    # Windows ctime is creation time, so it cannot detect a rewrite with restored
    # mtime. Hash bytes on Windows; unchanged content still avoids JSON parsing.
    change_marker = (
        int.from_bytes(hashlib.sha256(path.read_bytes()).digest(), "big")
        if sys.platform == "win32"
        else stat.st_ctime_ns
    )
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, change_marker]


def atomic_json(path: pathlib.Path, value: dict) -> None:
    """Replace a derived index atomically, preserving the previous index on failure."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = pathlib.Path(stream.name)
            json.dump(value, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        # Windows cannot open a directory with os.open for fsync. This index is
        # derived: its contents are flushed and the closed file is replaced;
        # a lost index is rebuilt by validating the primary motion receipts.
        if sys.platform != "win32":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def check_records(directory: pathlib.Path) -> dict:
    """
    Validate every receipt, reading JSON bodies only for new or changed files.

    Corrupt indexes, missing previously indexed receipts, changed-during-read
    files and unresolved results all fail closed. A missing index triggers a
    complete initial validation rather than trusting filenames or headers.
    """
    started = time.perf_counter()
    directory = directory.resolve()
    if not directory.is_dir():
        msg = "Motion receipt directory is unavailable."
        raise CommandRejected(msg)
    path = directory / INDEX
    cache = {}
    if path.exists():
        try:
            index = json.loads(path.read_text())
            if index["version"] != 1 or index["directory"] != str(directory) or not isinstance(index["entries"], dict):
                msg_0 = "Invalid receipt index header."
                raise ValueError(msg_0)
            cache = index["entries"]
            for name, entry in cache.items():
                if pathlib.Path(name).name != name or not name.endswith(".json") or not isinstance(entry, dict):
                    msg_0 = "Invalid receipt index entry."
                    raise ValueError(msg_0)
                if (
                    len(entry["signature"]) != 5
                    or any(type(v) is not int for v in entry["signature"])
                    or entry["status"] not in TERMINAL
                ):
                    msg_0 = "Invalid receipt index signature or status."
                    raise ValueError(msg_0)
        except (KeyError, TypeError, ValueError) as error:
            msg = "Receipt index is corrupt; inspect/rebuild it without altering motion receipts."
            raise CommandRejected(msg) from error
    paths = {p.name: p for p in directory.glob("*.json")}
    missing = set(cache) - set(paths)
    if missing:
        msg = f"Previously indexed motion receipt is missing: {sorted(missing)[0]}."
        raise CommandRejected(msg)
    entries = {}
    reread = 0
    for name, record_path in paths.items():
        if record_path.is_symlink():
            msg = f"Symlink motion receipt is not allowed: {name}."
            raise CommandRejected(msg)
        before = signature(record_path)
        old = cache.get(name)
        if old is not None and old["signature"] == before:
            entries[name] = old
            continue
        record = json.loads(record_path.read_text())
        reread += 1
        if not isinstance(record, dict) or record.get("status") not in TERMINAL:
            msg = f"Unresolved motion receipt: {name}. Outcome is unknown; inspect it before any movement."
            raise CommandRejected(msg)
        if signature(record_path) != before:
            msg = f"Motion receipt changed while being checked: {name}."
            raise CommandRejected(msg)
        entries[name] = {"signature": before, "status": record["status"]}
    # Do not lose a concurrently added receipt between enumeration and commit.
    if set(paths) != {p.name for p in directory.glob("*.json")}:
        msg = "Motion receipt set changed during validation; check again."
        raise CommandRejected(msg)
    if entries != cache or not path.exists():
        atomic_json(path, {"version": 1, "directory": str(directory), "entries": entries})
    return {
        "count": len(entries),
        "json_bodies_read": reread,
        "cache_hits": len(entries) - reread,
        "elapsed_ms": (time.perf_counter() - started) * 1000,
    }
