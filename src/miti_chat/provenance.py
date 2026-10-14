"""Reproducibility record: code revision, library versions and protocol file hashes."""

from __future__ import annotations

import hashlib
import platform
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .config import Protocol

PACKAGES = ("miti-chat", "inspect-ai", "openai", "anthropic", "jsonschema", "rapidfuzz", "tiktoken")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def code_revision(root: Path) -> dict[str, Any]:
    """Git commit (and whether the tree has uncommitted changes), or a hash of src/ if not a git checkout."""
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "src", "assets", "reference", "config"],
                               cwd=root, capture_output=True, text=True, check=True).stdout.strip()
        return {"git_commit": commit, "uncommitted_changes": bool(dirty)}
    except Exception:
        h = hashlib.sha256()
        for p in sorted((root / "src").rglob("*.py")):
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
        return {"git_commit": None, "source_archive_sha256": h.hexdigest()}


def versions() -> dict[str, str | None]:
    out: dict[str, str | None] = {"python": platform.python_version()}
    for pkg in PACKAGES:
        try:
            out[pkg] = version(pkg)
        except PackageNotFoundError:
            out[pkg] = None
    return out


def file_hashes(protocol: Protocol) -> dict[str, str]:
    return {rel: sha256_file(protocol.path(rel)) for rel in protocol.input_files()}


def record(protocol: Protocol) -> dict[str, Any]:
    return {
        "protocol_version": protocol.version,
        "code": code_revision(protocol.root),
        "versions": versions(),
        "platform": platform.platform(),
        "file_sha256": file_hashes(protocol),
    }


def manual_check(protocol: Protocol) -> dict[str, Any]:
    """Compare the judge manual text and source PDF against reference/manual_preparation.json."""
    prep = protocol.json("reference/manual_preparation.json")
    manual = protocol.judging["manual_text_file"]
    pdf = "reference/" + prep["source_file"]
    got_text, got_pdf = sha256_file(protocol.path(manual)), sha256_file(protocol.path(pdf))
    return {
        "manual_text": manual,
        "manual_text_matches_record": got_text == prep["prepared_text_sha256"],
        "source_pdf_matches_record": got_pdf == prep["source_sha256"],
    }
