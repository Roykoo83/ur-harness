"""Deterministic SHA-256 helpers."""

from __future__ import annotations

import hashlib
import json
from os import PathLike
from pathlib import Path
from typing import BinaryIO


def _tag(digest: str) -> str:
    return f"sha256:{digest}"


def content_hash(content: bytes | bytearray | memoryview | str) -> str:
    """Hash in-memory content using UTF-8 for strings."""
    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = bytes(content)
    return _tag(hashlib.sha256(data).hexdigest())


def file_content_hash(path: str | PathLike[str] | BinaryIO) -> str:
    """Hash a file without loading it completely into memory."""
    digest = hashlib.sha256()
    if hasattr(path, "read"):
        stream = path
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    else:
        with Path(path).open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    return _tag(digest.hexdigest())


def metadata_hash(rel_path: str, size: int, mtime: str | int | float) -> str:
    """Hash canonical rel_path, size, and mtime metadata."""
    basis = json.dumps(
        [rel_path, size, mtime], ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return _tag(hashlib.sha256(basis).hexdigest())


hash_content = content_hash
hash_metadata = metadata_hash
