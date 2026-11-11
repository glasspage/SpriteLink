"""Recoverable snapshots for multi-room SpriteLink data imports.

The snapshot retains the existing DPAPI-encrypted settings and SQLite payloads;
it never writes a password-free .sldata archive or decrypted chat history.
A pending marker allows recovery on the next launch after an interrupted import.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import uuid


class ImportRecovery:
    MANIFEST = "manifest.json"
    PENDING = "pending"

    def __init__(self, folder: Path, settings_path: Path, history_dir: Path,
                 manifest: dict) -> None:
        self.folder = folder
        self.settings_path = settings_path
        self.history_dir = history_dir
        self.manifest = manifest

    @staticmethod
    def _copy_database(source: Path, destination: Path) -> None:
        """SQLite's backup API includes committed changes in journal files."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro",
                                     uri=True)) as reader:
            reader.execute("PRAGMA busy_timeout = 30000")
            with closing(sqlite3.connect(destination)) as writer:
                writer.execute("PRAGMA busy_timeout = 30000")
                reader.backup(writer)

    @classmethod
    def capture(cls, recovery_root: Path, settings_path: Path,
                history_dir: Path, history_paths: list[Path]) -> "ImportRecovery":
        # Never start changing live data until *every* backup succeeds.
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        folder = recovery_root / (timestamp + "-" + uuid.uuid4().hex[:12])
        folder.mkdir(parents=True, exist_ok=False)
        records = []
        seen = set()
        try:
            for path in history_paths:
                if (path.parent.resolve() != history_dir.resolve()
                        or not re.fullmatch(r"[A-Za-z0-9_-]+\.sqlite3", path.name)):
                    raise ValueError("Invalid history backup path.")
                if path.name in seen:
                    continue
                seen.add(path.name)
                filename = f"room-{len(records)}.sqlite3"
                existed = path.exists()
                if existed:
                    cls._copy_database(path, folder / filename)
                records.append({
                    "file": path.name, "backup": filename, "existed": existed,
                })
            config_existed = settings_path.exists()
            if config_existed:
                shutil.copy2(settings_path, folder / "settings.bin")
            manifest = {
                "version": 1,
                "settings_existed": config_existed,
                "histories": records,
            }
            (folder / cls.MANIFEST).write_text(
                json.dumps(manifest, indent=2), encoding="utf-8",
            )
            (folder / "README.txt").write_text(
                "SpriteLink import recovery snapshot.\n"
                "These files are encrypted for the original Windows user account.\n"
                "Close SpriteLink before manually restoring any files.\n"
                "The manifest identifies the original history filenames.\n",
                encoding="utf-8",
            )
            # Last operation: never leave a pending marker for an incomplete backup.
            (folder / cls.PENDING).write_text("import in progress\n", encoding="utf-8")
            return cls(folder, settings_path, history_dir, manifest)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    @classmethod
    def from_pending(cls, folder: Path, settings_path: Path,
                     history_dir: Path) -> "ImportRecovery":
        manifest = json.loads((folder / cls.MANIFEST).read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("version") != 1
                or type(manifest.get("settings_existed")) is not bool
                or not isinstance(manifest.get("histories"), list)):
            raise ValueError("Invalid SpriteLink recovery manifest.")
        seen = set()
        for index, record in enumerate(manifest["histories"]):
            if (not isinstance(record, dict)
                    or not isinstance(record.get("file"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]+\.sqlite3", record["file"])
                    or record.get("backup") != f"room-{index}.sqlite3"
                    or type(record.get("existed")) is not bool
                    or record["file"] in seen):
                raise ValueError("Invalid SpriteLink recovery history entry.")
            seen.add(record["file"])
        return cls(folder, settings_path, history_dir, manifest)

    def rollback(self) -> None:
        """Restore all affected databases and the exact encrypted settings.

        Keep the pending marker and snapshots intact if any restore fails.
        """
        for record in self.manifest["histories"]:
            path = self.history_dir / record["file"]
            if record["existed"]:
                backup = self.folder / record["backup"]
                if not backup.is_file():
                    raise OSError("Missing history recovery file.")
                # Backup into the live DB instead of copying a potentially
                # uncheckpointed WAL file or replacing an open SQLite handle.
                self._copy_database(backup, path)
            else:
                path.unlink(missing_ok=True)
        if self.manifest["settings_existed"]:
            saved = self.folder / "settings.bin"
            if not saved.is_file():
                raise OSError("Missing settings recovery file.")
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.settings_path.with_name("settings.import-restore.tmp")
            try:
                shutil.copy2(saved, temporary)
                os.replace(temporary, self.settings_path)
            finally:
                temporary.unlink(missing_ok=True)
        else:
            self.settings_path.unlink(missing_ok=True)

    def complete(self, *, keep: bool) -> None:
        # Removing pending before deleting the directory prevents a completed
        # import from being accidentally undone following a later crash.
        (self.folder / self.PENDING).unlink(missing_ok=True)
        if not keep:
            shutil.rmtree(self.folder)

    @classmethod
    def recover_pending(cls, recovery_root: Path, settings_path: Path,
                        history_dir: Path) -> list[Path]:
        """Rollback interrupted imports before normal config/history loading."""
        restored = []
        if not recovery_root.is_dir():
            return restored
        for folder in sorted(recovery_root.iterdir()):
            if not folder.is_dir() or not (folder / cls.PENDING).exists():
                continue
            recovery = cls.from_pending(folder, settings_path, history_dir)
            recovery.rollback()
            recovery.complete(keep=False)
            restored.append(folder)
        return restored
