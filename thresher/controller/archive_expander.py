"""Archive expansion — extracts archive members and uploads them to the source provider."""

from __future__ import annotations

import bz2
import gzip
import hashlib
import json
import logging
import lzma
import os
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import IO, Any, Iterator

from thresher.providers.source import SourceProvider
from thresher.types import ExpansionRecord, FileInfo

logger = logging.getLogger("thresher.controller.archive_expander")

_ARCHIVE_EXTENSIONS: frozenset[str] = frozenset({".zip", ".tar", ".gz", ".bz2", ".xz", ".tgz"})

_COMPOUND_TAR_EXTENSIONS: tuple[str, ...] = (".tar.gz", ".tar.bz2", ".tar.xz")

_NON_EXTRACTABLE_EXTENSIONS: frozenset[str] = frozenset({".jar", ".war", ".whl", ".egg"})

_SKIP_FILENAMES: frozenset[str] = frozenset({"Thumbs.db", "desktop.ini", ".DS_Store"})


def is_archive(path: str) -> bool:
    """Check if *path* has a supported archive extension."""
    lower = path.lower()
    for compound in _COMPOUND_TAR_EXTENSIONS:
        if lower.endswith(compound):
            return True
    ext = os.path.splitext(lower)[1]
    return ext in _ARCHIVE_EXTENSIONS


def _should_skip_member(member_name: str) -> bool:
    """Return True if the archive member should be filtered out."""
    if not member_name or member_name.endswith("/"):
        return True
    if "__MACOSX/" in member_name:
        return True
    basename = os.path.basename(member_name)
    if basename.startswith("._"):
        return True
    if basename in _SKIP_FILENAMES:
        return True
    ext = os.path.splitext(basename.lower())[1]
    if ext in _NON_EXTRACTABLE_EXTENSIONS:
        return True
    return False


def _archive_stem(archive_path: str) -> str:
    """Strip archive extension(s) to produce the expansion folder stem."""
    p = PurePosixPath(archive_path)
    name = p.name
    lower_name = name.lower()
    for compound in _COMPOUND_TAR_EXTENSIONS:
        if lower_name.endswith(compound):
            stripped = name[: -len(compound)]
            return str(p.parent / stripped) if str(p.parent) != "." else stripped
    return str(p.parent / p.stem) if str(p.parent) != "." else p.stem


class ArchiveExpander:
    """Expand archives from a source provider and upload their members back."""

    def __init__(
        self,
        source: SourceProvider,
        expanded_prefix: str = "expanded/",
        max_depth: int = 2,
    ) -> None:
        self._source = source
        self._expanded_prefix = expanded_prefix
        self._max_depth = max_depth

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def expand_archives(self, file_infos: list[FileInfo]) -> list[dict]:
        """Expand every archive in *file_infos*, returning expanded-file dicts."""
        results: list[dict] = []
        for fi in file_infos:
            if not is_archive(fi.path):
                continue
            expanded = self._expand_single(fi.path, depth=0)
            results.extend(expanded)
        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _expand_single(self, archive_path: str, depth: int) -> list[dict]:
        """Download an archive from the source provider, expand, and upload members."""
        if depth >= self._max_depth:
            return []

        stem = _archive_stem(archive_path)
        expansion_folder = f"{self._expanded_prefix}{stem}"

        record = self._load_expansion_record(archive_path)
        if record is not None:
            logger.info(
                "Skipping already-expanded archive: %s (%d members)",
                archive_path,
                record.member_count,
            )
            return self._list_existing_expanded(record, archive_path)

        results: list[dict] = []
        with tempfile.TemporaryDirectory(prefix="thresher_expand_") as tmp:
            tmp_path = Path(tmp)
            local_archive = tmp_path / os.path.basename(archive_path)
            self._source.download_to_path(archive_path, local_archive)

            archive_hash = hashlib.md5(local_archive.read_bytes()).hexdigest()
            members = self._extract_archive(local_archive, tmp_path / "extracted")
            member_count = 0

            for member_name, member_local_path in members:
                if _should_skip_member(member_name):
                    continue

                remote_path = f"{expansion_folder}/{member_name}"
                self._source.upload_from_path(remote_path, member_local_path)
                member_count += 1

                if is_archive(member_name) and depth + 1 < self._max_depth:
                    nested = self._expand_local_archive(
                        member_local_path,
                        member_name,
                        expansion_folder,
                        archive_path,
                        depth + 1,
                    )
                    results.extend(nested)
                else:
                    results.append(
                        {
                            "path": remote_path,
                            "source_type": "expanded",
                            "archive_path": archive_path,
                        }
                    )

            self._save_expansion_record(
                ExpansionRecord(
                    archive_path=archive_path,
                    expansion_folder=expansion_folder,
                    member_count=member_count,
                    expanded_at=time.time(),
                    archive_hash=archive_hash,
                )
            )

        logger.info(
            "Expanded %s: %d members → %s",
            archive_path,
            member_count,
            expansion_folder,
        )
        return results

    def _expand_local_archive(
        self,
        local_path: Path,
        member_name: str,
        parent_folder: str,
        original_archive: str,
        depth: int,
    ) -> list[dict]:
        """Expand a nested archive already available on the local filesystem."""
        if depth >= self._max_depth:
            return []

        nested_stem = _archive_stem(member_name)
        expansion_folder = f"{parent_folder}/{nested_stem}"

        extract_dir = local_path.parent / f"_nested_{depth}_{local_path.stem}"
        extract_dir.mkdir(parents=True, exist_ok=True)

        members = self._extract_archive(local_path, extract_dir)
        results: list[dict] = []

        for name, path in members:
            if _should_skip_member(name):
                continue

            remote_path = f"{expansion_folder}/{name}"
            self._source.upload_from_path(remote_path, path)

            if is_archive(name) and depth + 1 < self._max_depth:
                nested = self._expand_local_archive(
                    path, name, expansion_folder, original_archive, depth + 1
                )
                results.extend(nested)
            else:
                results.append(
                    {
                        "path": remote_path,
                        "source_type": "expanded",
                        "archive_path": original_archive,
                    }
                )

        return results

    # ------------------------------------------------------------------
    # Archive format extraction
    # ------------------------------------------------------------------

    def _extract_archive(self, local_path: Path, dest_dir: Path) -> list[tuple[str, Path]]:
        """Extract archive contents, returning ``(member_name, local_path)`` pairs."""
        dest_dir.mkdir(parents=True, exist_ok=True)
        lower = local_path.name.lower()

        if lower.endswith(".zip"):
            return self._extract_zip(local_path, dest_dir)
        if lower.endswith((".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar")):
            return self._extract_tar(local_path, dest_dir)
        if lower.endswith(".gz"):
            return self._extract_standalone_compressed(local_path, dest_dir, gzip.open)
        if lower.endswith(".bz2"):
            return self._extract_standalone_compressed(local_path, dest_dir, bz2.open)
        if lower.endswith(".xz"):
            return self._extract_standalone_compressed(local_path, dest_dir, lzma.open)

        logger.warning("Unsupported archive format: %s", local_path.name)
        return []

    @staticmethod
    def _extract_zip(local_path: Path, dest_dir: Path) -> list[tuple[str, Path]]:
        members: list[tuple[str, Path]] = []
        with zipfile.ZipFile(local_path, "r") as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                name = info.filename
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                extracted = dest_dir / name
                extracted.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(extracted, "wb") as dst:
                    dst.write(src.read())
                members.append((name, extracted))
        return members

    @staticmethod
    def _extract_tar(local_path: Path, dest_dir: Path) -> list[tuple[str, Path]]:
        members: list[tuple[str, Path]] = []
        with tarfile.open(str(local_path), "r:*") as tf:
            for member in tf.getmembers():
                if not member.isfile():
                    continue
                name = member.name
                if name.startswith("/") or ".." in name.split("/"):
                    continue
                extracted = dest_dir / name
                extracted.parent.mkdir(parents=True, exist_ok=True)
                f = tf.extractfile(member)
                if f is None:
                    continue
                with open(extracted, "wb") as dst:
                    dst.write(f.read())
                members.append((name, extracted))
        return members

    @staticmethod
    def _extract_standalone_compressed(
        local_path: Path,
        dest_dir: Path,
        opener: Callable[..., IO[Any]],
    ) -> list[tuple[str, Path]]:
        """Extract a standalone compressed file (.gz / .bz2 / .xz)."""
        stem = local_path.stem  # e.g. "file.txt.gz" → "file.txt"
        output = dest_dir / stem
        with opener(local_path, "rb") as src, open(output, "wb") as dst:
            dst.write(src.read())
        return [(stem, output)]

    # ------------------------------------------------------------------
    # Expansion records (idempotency)
    # ------------------------------------------------------------------

    def _load_expansion_record(self, archive_path: str) -> ExpansionRecord | None:
        stem = _archive_stem(archive_path)
        record_path = f"{self._expanded_prefix}{stem}/.expansion-record.json"
        if not self._source.exists(record_path):
            return None
        try:
            raw = json.loads(self._source.download_content(record_path))
            return ExpansionRecord(
                archive_path=raw["archive_path"],
                expansion_folder=raw["expansion_folder"],
                member_count=raw["member_count"],
                expanded_at=raw["expanded_at"],
                archive_hash=raw.get("archive_hash"),
            )
        except Exception:
            logger.warning("Corrupt expansion record for %s — re-expanding", archive_path)
            return None

    def _save_expansion_record(self, record: ExpansionRecord) -> None:
        record_path = f"{record.expansion_folder}/.expansion-record.json"
        payload = json.dumps(
            {
                "archive_path": record.archive_path,
                "expansion_folder": record.expansion_folder,
                "member_count": record.member_count,
                "expanded_at": record.expanded_at,
                "archive_hash": record.archive_hash,
            },
            indent=2,
        )
        self._source.upload_content(record_path, payload.encode("utf-8"))

    def _list_existing_expanded(self, record: ExpansionRecord, archive_path: str) -> list[dict]:
        """Re-list files already present in an expansion folder."""
        results: list[dict] = []
        file_iter: Iterator[FileInfo] = self._source.list_files(
            prefix=record.expansion_folder, recursive=True
        )
        for fi in file_iter:
            if fi.path.endswith("/") or fi.path.endswith(".expansion-record.json"):
                continue
            results.append(
                {
                    "path": fi.path,
                    "source_type": "expanded",
                    "archive_path": archive_path,
                }
            )
        return results
