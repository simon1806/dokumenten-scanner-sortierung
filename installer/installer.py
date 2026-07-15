"""Minimaler Windows-Installer für die portable Anwendung.

PyInstaller legt die eigentliche Anwendung als eingebettete Datei im Setup ab.
Ein erneuter Start derselben Setup-EXE ersetzt die Anwendung und dient damit auch
als Update-Mechanismus.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

if __package__:
    from .product import (
        APPLICATION_FILENAME,
        APPLICATION_FOLDER,
        DISPLAY_NAME,
        ICON_FILENAME,
        INSTALL_ACTION_DOWNGRADE,
        INSTALL_ACTION_DOWNGRADE_BLOCKED,
        INSTALL_ACTION_INSTALL,
        INSTALL_ACTION_REPAIR,
        INSTALL_ACTION_UNKNOWN_VERSION_BLOCKED,
        INSTALL_ACTION_UPDATE,
        LEGACY_UNINSTALLER_FILENAME,
        NOTICE_FILENAME,
        PAYLOAD_ICON_FILENAME,
        PAYLOAD_MANIFEST_FILENAME,
        PAYLOAD_UNINSTALLER_FILENAME,
        PUBLISHER,
        SETUP_MUTEX_NAME,
        SHORTCUT_FILENAME,
        SUPPORT_EMAIL,
        UNINSTALL_REGISTRY_PATH,
        UNINSTALLER_FILENAME,
        VERSION_FILENAME,
    )
    from .windows_dialog import powershell_quote, show_completion, show_confirmation
else:
    from product import (  # type: ignore[no-redef]
        APPLICATION_FILENAME,
        APPLICATION_FOLDER,
        DISPLAY_NAME,
        ICON_FILENAME,
        INSTALL_ACTION_DOWNGRADE,
        INSTALL_ACTION_DOWNGRADE_BLOCKED,
        INSTALL_ACTION_INSTALL,
        INSTALL_ACTION_REPAIR,
        INSTALL_ACTION_UNKNOWN_VERSION_BLOCKED,
        INSTALL_ACTION_UPDATE,
        LEGACY_UNINSTALLER_FILENAME,
        NOTICE_FILENAME,
        PAYLOAD_ICON_FILENAME,
        PAYLOAD_MANIFEST_FILENAME,
        PAYLOAD_UNINSTALLER_FILENAME,
        PUBLISHER,
        SETUP_MUTEX_NAME,
        SHORTCUT_FILENAME,
        SUPPORT_EMAIL,
        UNINSTALL_REGISTRY_PATH,
        UNINSTALLER_FILENAME,
        VERSION_FILENAME,
    )
    from windows_dialog import powershell_quote, show_completion, show_confirmation  # type: ignore[no-redef]


def payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / APPLICATION_FILENAME


def notice_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / NOTICE_FILENAME


def icon_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / PAYLOAD_ICON_FILENAME


def version_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / VERSION_FILENAME


def uninstaller_payload_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / PAYLOAD_UNINSTALLER_FILENAME


def payload_manifest_path() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / "payload" / PAYLOAD_MANIFEST_FILENAME


@dataclass(frozen=True, slots=True)
class PayloadFile:
    manifest_name: str
    source: Path
    destination: Path
    expected_size: int | None = None
    expected_sha256: str | None = None


@dataclass(slots=True)
class FileReplacement:
    destination: Path
    backup: Path
    had_original: bool
    stage: Path | None = None
    original_size: int | None = None
    original_sha256: str | None = None
    payload_size: int | None = None
    payload_sha256: str | None = None
    backup_created: bool = False
    destination_replaced: bool = False


@dataclass(slots=True)
class InstallationTransaction:
    stage_directory: Path
    backup_directory: Path
    replacements: list[FileReplacement]
    journal_path: Path | None = None
    finished: bool = False
    committed: bool = False

    def commit(self) -> str | None:
        if self.finished:
            return None
        mark_transaction_committed(self)
        self.committed = True
        self.finished = True
        try:
            shutil.rmtree(self.stage_directory)
        except OSError as error:
            return (
                "Installation wurde festgeschrieben, aber Recovery-Daten konnten nicht bereinigt werden. "
                f"Stage={self.stage_directory}; Backup={self.backup_directory}; {error}"
            )
        try:
            shutil.rmtree(self.backup_directory)
        except OSError as error:
            return (
                "Installation wurde festgeschrieben, aber das Commit-Backup konnte nicht bereinigt werden. "
                f"Backup={self.backup_directory}; {error}"
            )
        return None

    def rollback(self) -> None:
        if self.finished:
            return
        if self.committed:
            raise RuntimeError(
                "Eine festgeschriebene Installation darf nicht zurückgerollt werden. "
                f"Recovery-Daten: {self.backup_directory}"
            )
        errors: list[str] = []
        for replacement in reversed(self.replacements):
            try:
                if replacement.backup_created:
                    assert_safe_regular_file(
                        replacement.backup,
                        "Installationsbackup",
                        replacement.original_size,
                        replacement.original_sha256,
                    )
                    assert_not_reparse(replacement.destination, "Rollback-Ziel")
                    if replacement.destination.exists():
                        assert_safe_regular_file(
                            replacement.destination,
                            "zu ersetzende Payload-Datei",
                            replacement.payload_size,
                            replacement.payload_sha256,
                        )
                    os.replace(replacement.backup, replacement.destination)
                    replacement.backup_created = False
                    replacement.destination_replaced = False
                elif replacement.destination_replaced:
                    assert_not_reparse(replacement.destination, "Rollback-Ziel")
                    if replacement.destination.exists():
                        assert_safe_regular_file(
                            replacement.destination,
                            "zu entfernende Payload-Datei",
                            replacement.payload_size,
                            replacement.payload_sha256,
                        )
                        replacement.destination.unlink()
                    replacement.destination_replaced = False
            except OSError as error:
                errors.append(f"{replacement.destination}: {error}")
        if errors:
            raise RuntimeError(
                "Rollback unvollständig. Recovery-Daten wurden nicht gelöscht; "
                f"Backup: {self.backup_directory}; Stage: {self.stage_directory}. "
                + " | ".join(errors)
            )
        try:
            assert_replacements_rolled_back(self.replacements)
            journal = self.journal_path or self.backup_directory / TRANSACTION_JOURNAL_FILENAME
            if journal.exists():
                mark_transaction_rolled_back(self)
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                "Rollback-Zustand konnte nicht sicher festgeschrieben werden; "
                "Recovery-Daten wurden nicht gelöscht. "
                f"Backup: {self.backup_directory}; Stage: {self.stage_directory}. {error}"
            ) from error
        self.finished = True
        cleanup_transaction_directories(
            self.stage_directory,
            self.backup_directory,
            "Rollback wurde vollständig ausgeführt",
            lambda: assert_replacements_rolled_back(self.replacements),
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


TRANSACTION_JOURNAL_FILENAME = "transaction.json"
TRANSACTION_JOURNAL_SCHEMA = 1
TRANSACTION_STATE_PREPARED = "prepared"
TRANSACTION_STATE_COMMITTED = "committed"
TRANSACTION_STATE_ROLLED_BACK = "rolled_back"
PAYLOAD_DESTINATION_NAMES = (
    APPLICATION_FILENAME,
    NOTICE_FILENAME,
    ICON_FILENAME,
    VERSION_FILENAME,
    UNINSTALLER_FILENAME,
)
PAYLOAD_DESTINATION_NAMES_CASEFOLD = {name.casefold() for name in PAYLOAD_DESTINATION_NAMES}
PAYLOAD_LAYOUT = {
    APPLICATION_FILENAME: APPLICATION_FILENAME,
    NOTICE_FILENAME: NOTICE_FILENAME,
    PAYLOAD_ICON_FILENAME: ICON_FILENAME,
    VERSION_FILENAME: VERSION_FILENAME,
    PAYLOAD_UNINSTALLER_FILENAME: UNINSTALLER_FILENAME,
}


def file_matches(path: Path, expected_size: int | None, expected_sha256: str | None) -> bool:
    if expected_size is None or expected_sha256 is None:
        return False
    try:
        if is_reparse_point(path):
            return False
        return path.is_file() and path.stat().st_size == expected_size and sha256_file(path) == expected_sha256
    except OSError:
        return False


def is_reparse_point(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return False
    attributes = int(getattr(file_stat, "st_file_attributes", 0))
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400))
    return path.is_symlink() or bool(attributes & reparse_flag)


def assert_not_reparse(path: Path, label: str) -> None:
    if is_reparse_point(path):
        raise OSError(f"{label} ist ein Symlink, Junction oder Reparse-Point: {path}")


def assert_direct_child(parent: Path, child: Path, label: str) -> None:
    normal_parent = os.path.normcase(os.path.abspath(parent))
    normal_child_parent = os.path.normcase(os.path.abspath(child.parent))
    if normal_child_parent != normal_parent:
        raise OSError(f"{label} liegt nicht direkt im erwarteten Ordner: {child}")


def assert_safe_regular_file(
    path: Path,
    label: str,
    expected_size: int | None,
    expected_sha256: str | None,
) -> None:
    assert_not_reparse(path, label)
    if not file_matches(path, expected_size, expected_sha256):
        raise OSError(f"{label} fehlt, ist beschädigt oder wurde verändert: {path}")


def assert_original_destination_state(
    destination: Path,
    had_original: bool,
    original_size: int | None,
    original_sha256: str | None,
) -> None:
    assert_not_reparse(destination, "Rollback-Ziel")
    if had_original:
        assert_safe_regular_file(
            destination,
            "wiederhergestelltes Original",
            original_size,
            original_sha256,
        )
    elif destination.exists():
        raise OSError(f"Rollback-Ziel müsste nicht vorhanden sein: {destination}")


def assert_replacements_rolled_back(replacements: list[FileReplacement]) -> None:
    for replacement in replacements:
        assert_original_destination_state(
            replacement.destination,
            replacement.had_original,
            replacement.original_size,
            replacement.original_sha256,
        )


def cleanup_transaction_directories(
    stage_directory: Path,
    backup_directory: Path,
    completed_action: str,
    verify_before_backup: Callable[[], None] | None = None,
) -> None:
    if stage_directory.exists():
        try:
            shutil.rmtree(stage_directory)
        except OSError as error:
            raise RuntimeError(
                f"{completed_action}, aber der Stage-Ordner konnte nicht entfernt werden. "
                f"Recovery-Zustand bleibt auswertbar: Backup={backup_directory}; "
                f"Stage={stage_directory}; {error}"
            ) from error
    if backup_directory.exists():
        if verify_before_backup is not None:
            verify_before_backup()
        try:
            shutil.rmtree(backup_directory)
        except OSError as error:
            raise RuntimeError(
                f"{completed_action}, aber der Backup-Ordner konnte nicht entfernt werden. "
                f"Recovery-Zustand bleibt auswertbar: Backup={backup_directory}; "
                f"Stage={stage_directory}; {error}"
            ) from error


def write_transaction_journal(transaction: InstallationTransaction) -> None:
    records: list[dict[str, object]] = []
    for replacement in transaction.replacements:
        if replacement.stage is None:
            raise RuntimeError(f"Stage-Pfad fehlt für {replacement.destination}.")
        records.append(
            {
                "destination_name": replacement.destination.name,
                "backup_name": replacement.backup.name,
                "stage_name": replacement.stage.name,
                "had_original": replacement.had_original,
                "original_size": replacement.original_size,
                "original_sha256": replacement.original_sha256,
                "payload_size": replacement.payload_size,
                "payload_sha256": replacement.payload_sha256,
            }
        )
    journal = transaction.backup_directory / TRANSACTION_JOURNAL_FILENAME
    with journal.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(
            {
                "schema": TRANSACTION_JOURNAL_SCHEMA,
                "state": TRANSACTION_STATE_PREPARED,
                "records": records,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    transaction.journal_path = journal


def replace_transaction_journal_state(
    journal: Path,
    expected_state: str,
    new_state: str,
    expected_records: list[object] | None = None,
) -> None:
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Transaktionsjournal kann nicht festgeschrieben werden: {journal}: {error}") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != TRANSACTION_JOURNAL_SCHEMA
        or payload.get("state") != expected_state
        or not isinstance(payload.get("records"), list)
    ):
        raise RuntimeError(
            f"Transaktionsjournal besitzt nicht den erwarteten Zustand {expected_state!r}: {journal}"
        )
    if expected_records is not None and payload["records"] != expected_records:
        raise RuntimeError(f"Transaktionsjournal wurde während der Recovery verändert: {journal}")
    payload["state"] = new_state
    temporary = journal.with_name(f"{journal.name}.{new_state}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, journal)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def mark_transaction_committed(transaction: InstallationTransaction) -> None:
    journal = transaction.journal_path or transaction.backup_directory / TRANSACTION_JOURNAL_FILENAME
    replace_transaction_journal_state(
        journal,
        TRANSACTION_STATE_PREPARED,
        TRANSACTION_STATE_COMMITTED,
    )


def mark_transaction_rolled_back(transaction: InstallationTransaction) -> None:
    journal = transaction.journal_path or transaction.backup_directory / TRANSACTION_JOURNAL_FILENAME
    replace_transaction_journal_state(
        journal,
        TRANSACTION_STATE_PREPARED,
        TRANSACTION_STATE_ROLLED_BACK,
    )


def _safe_transaction_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value or value in {".", ".."}:
        raise RuntimeError(f"Ungültiger {label} im Recovery-Journal: {value!r}")
    return value


def _journal_integer(value: object, label: str, *, optional: bool = False) -> int | None:
    if optional and value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RuntimeError(f"Ungültiger Wert für {label} im Recovery-Journal: {value!r}")
    return value


def _journal_hash(value: object, label: str, *, optional: bool = False) -> str | None:
    if optional and value is None:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9A-Fa-f]{64}", value) is None:
        raise RuntimeError(f"Ungültiger Wert für {label} im Recovery-Journal: {value!r}")
    return value.upper()


def assert_recovery_records_rolled_back(
    records: list[dict[str, object]],
    journal_path: Path,
) -> None:
    if len(records) != len(PAYLOAD_DESTINATION_NAMES):
        raise RuntimeError(f"Rollback-Prüfung erwartet exakt fünf Recovery-Einträge: {journal_path}")
    for record in records:
        destination = record.get("destination")
        had_original = record.get("had_original")
        if not isinstance(destination, Path) or not isinstance(had_original, bool):
            raise RuntimeError(f"Interner Recovery-Fehler in {journal_path}")
        assert_original_destination_state(
            destination,
            had_original,
            record.get("original_size") if isinstance(record.get("original_size"), int) else None,
            record.get("original_sha256") if isinstance(record.get("original_sha256"), str) else None,
        )


def assert_recovery_records_committed(
    records: list[dict[str, object]],
    journal_path: Path,
) -> None:
    if len(records) != len(PAYLOAD_DESTINATION_NAMES):
        raise RuntimeError(f"Commit-Prüfung erwartet exakt fünf Recovery-Einträge: {journal_path}")
    for record in records:
        destination = record.get("destination")
        if not isinstance(destination, Path):
            raise RuntimeError(f"Interner Recovery-Fehler in {journal_path}")
        assert_safe_regular_file(
            destination,
            "festgeschriebene Payload-Datei",
            record.get("payload_size") if isinstance(record.get("payload_size"), int) else None,
            record.get("payload_sha256") if isinstance(record.get("payload_sha256"), str) else None,
        )


def recover_transaction(
    installation_directory: Path,
    stage_directory: Path,
    backup_directory: Path,
) -> None:
    assert_not_reparse(installation_directory, "Installationsordner")
    assert_direct_child(installation_directory, stage_directory, "Stage-Ordner")
    assert_direct_child(installation_directory, backup_directory, "Backup-Ordner")
    if stage_directory.exists():
        assert_not_reparse(stage_directory, "Stage-Ordner")
    assert_not_reparse(backup_directory, "Backup-Ordner")
    journal_path = backup_directory / TRANSACTION_JOURNAL_FILENAME
    assert_direct_child(backup_directory, journal_path, "Recovery-Journal")
    assert_not_reparse(journal_path, "Recovery-Journal")
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            "Verwaiste Installation kann nicht automatisch wiederhergestellt werden. "
            f"Recovery-Daten nicht verändern: Backup={backup_directory}; Stage={stage_directory}; {error}"
        ) from error
    if (
        not isinstance(journal, dict)
        or journal.get("schema") != TRANSACTION_JOURNAL_SCHEMA
        or journal.get("state")
        not in {
            TRANSACTION_STATE_PREPARED,
            TRANSACTION_STATE_COMMITTED,
            TRANSACTION_STATE_ROLLED_BACK,
        }
    ):
        raise RuntimeError(
            "Unbekanntes Recovery-Journal. Recovery-Daten nicht verändern: "
            f"Backup={backup_directory}; Stage={stage_directory}"
        )
    records = journal.get("records")
    if not isinstance(records, list) or len(records) != len(PAYLOAD_DESTINATION_NAMES):
        raise RuntimeError(
            f"Recovery-Journal muss exakt {len(PAYLOAD_DESTINATION_NAMES)} Payload-Einträge enthalten. "
            "Recovery-Daten nicht verändern: "
            f"Backup={backup_directory}; Stage={stage_directory}"
        )

    parsed_records: list[dict[str, object]] = []
    destination_names: set[str] = set()
    backup_names: set[str] = set()
    stage_names: set[str] = set()
    for raw_record in records:
        if not isinstance(raw_record, dict) or not isinstance(raw_record.get("had_original"), bool):
            raise RuntimeError(f"Ungültiger Recovery-Eintrag in {journal_path}")
        destination_name = _safe_transaction_name(raw_record.get("destination_name"), "Zielname")
        backup_name = _safe_transaction_name(raw_record.get("backup_name"), "Backupname")
        stage_name = _safe_transaction_name(raw_record.get("stage_name"), "Stagename")
        destination_key = destination_name.casefold()
        backup_key = backup_name.casefold()
        stage_key = stage_name.casefold()
        if destination_key in destination_names or backup_key in backup_names or stage_key in stage_names:
            raise RuntimeError(f"Doppelter oder mehrdeutiger Recovery-Eintrag in {journal_path}")
        if destination_key not in PAYLOAD_DESTINATION_NAMES_CASEFOLD:
            raise RuntimeError(f"Nicht erlaubtes Installationsziel im Recovery-Journal: {destination_name}")
        expected_index = next(
            index
            for index, allowed_name in enumerate(PAYLOAD_DESTINATION_NAMES)
            if allowed_name.casefold() == destination_key
        )
        expected_transaction_name = f"{expected_index:02d}-{PAYLOAD_DESTINATION_NAMES[expected_index]}"
        if backup_key != expected_transaction_name.casefold() or stage_key != expected_transaction_name.casefold():
            raise RuntimeError(
                f"Backup-/Stagename passt nicht zum erlaubten Installationsziel {destination_name}: "
                f"{backup_name} / {stage_name}"
            )
        destination_names.add(destination_key)
        backup_names.add(backup_key)
        stage_names.add(stage_key)
        destination = installation_directory / destination_name
        backup = backup_directory / backup_name
        stage_path = stage_directory / stage_name
        assert_direct_child(installation_directory, destination, "Recovery-Ziel")
        assert_direct_child(backup_directory, backup, "Recovery-Backup")
        assert_direct_child(stage_directory, stage_path, "Recovery-Stage")
        for path, label in (
            (destination, "Recovery-Ziel"),
            (backup, "Recovery-Backup"),
            (stage_path, "Recovery-Stage"),
        ):
            assert_not_reparse(path, label)
        had_original = raw_record["had_original"]
        original_size = _journal_integer(
            raw_record.get("original_size"),
            "Originalgröße",
            optional=not had_original,
        )
        original_sha256 = _journal_hash(
            raw_record.get("original_sha256"),
            "Originalhash",
            optional=not had_original,
        )
        if not had_original and (original_size is not None or original_sha256 is not None):
            raise RuntimeError(
                f"Recovery-Eintrag ohne Original enthält unerwartete Originaldaten: {destination_name}"
            )
        parsed_records.append(
            {
                "destination": destination,
                "backup": backup,
                "stage": stage_path,
                "had_original": had_original,
                "original_size": original_size,
                "original_sha256": original_sha256,
                "payload_size": _journal_integer(raw_record.get("payload_size"), "Payloadgröße"),
                "payload_sha256": _journal_hash(raw_record.get("payload_sha256"), "Payloadhash"),
            }
        )

    if destination_names != PAYLOAD_DESTINATION_NAMES_CASEFOLD:
        raise RuntimeError(f"Recovery-Journal enthält nicht exakt die fünf erlaubten Installationsziele: {journal_path}")

    if journal["state"] == TRANSACTION_STATE_COMMITTED:
        try:
            assert_recovery_records_committed(parsed_records, journal_path)
            cleanup_transaction_directories(
                stage_directory,
                backup_directory,
                "Festgeschriebene Installation wurde geprüft",
                lambda: assert_recovery_records_committed(parsed_records, journal_path),
            )
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                "Festgeschriebene Installation oder ihre Recovery-Reste konnten nicht sicher geprüft und "
                "bereinigt werden; Recovery-Daten bleiben erhalten: "
                f"Backup={backup_directory}; Stage={stage_directory}; {error}"
            ) from error
        return

    if journal["state"] == TRANSACTION_STATE_ROLLED_BACK:
        try:
            assert_recovery_records_rolled_back(parsed_records, journal_path)
            cleanup_transaction_directories(
                stage_directory,
                backup_directory,
                "Persistierter Rollback-Zustand wurde vollständig geprüft",
                lambda: assert_recovery_records_rolled_back(parsed_records, journal_path),
            )
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                "Persistierter Rollback-Zustand konnte nicht sicher geprüft und bereinigt werden; "
                "Recovery-Daten bleiben erhalten: "
                f"Backup={backup_directory}; Stage={stage_directory}; {error}"
            ) from error
        return

    if not stage_directory.is_dir():
        try:
            assert_recovery_records_rolled_back(parsed_records, journal_path)
            replace_transaction_journal_state(
                journal_path,
                TRANSACTION_STATE_PREPARED,
                TRANSACTION_STATE_ROLLED_BACK,
                records,
            )
            cleanup_transaction_directories(
                stage_directory,
                backup_directory,
                "Backup-Rest eines vollständig ausgeführten Rollbacks wurde geprüft",
                lambda: assert_recovery_records_rolled_back(parsed_records, journal_path),
            )
        except (OSError, RuntimeError) as error:
            raise RuntimeError(
                "Vorbereitete Transaktion besitzt keinen Stage-Ordner und ist nicht eindeutig vollständig "
                "zurückgerollt. Automatische Bereinigung blockiert; Recovery-Daten bleiben erhalten: "
                f"Backup={backup_directory}; Stage={stage_directory}; {error}"
            ) from error
        return

    try:
        for record in reversed(parsed_records):
            destination = record["destination"]
            backup = record["backup"]
            if not isinstance(destination, Path) or not isinstance(backup, Path):
                raise RuntimeError(f"Interner Recovery-Fehler in {journal_path}")
            if record["had_original"]:
                if backup.is_file():
                    assert_safe_regular_file(
                        backup,
                        "Installationsbackup",
                        record["original_size"],
                        record["original_sha256"],
                    )
                    if destination.exists():
                        assert_safe_regular_file(
                            destination,
                            "Recovery-Payload",
                            record["payload_size"],
                            record["payload_sha256"],
                        )
                    os.replace(backup, destination)
                elif not file_matches(
                    destination,
                    record["original_size"],
                    record["original_sha256"],
                ):
                    raise RuntimeError(
                        f"Weder Original noch Backup sind eindeutig: Ziel={destination}; Backup={backup}"
                    )
            elif destination.exists():
                assert_safe_regular_file(
                    destination,
                    "Recovery-Payload",
                    record["payload_size"],
                    record["payload_sha256"],
                )
                destination.unlink()
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "Automatische Wiederherstellung fehlgeschlagen. Recovery-Daten bleiben erhalten: "
            f"Backup={backup_directory}; Stage={stage_directory}; {error}"
        ) from error

    try:
        assert_recovery_records_rolled_back(parsed_records, journal_path)
        replace_transaction_journal_state(
            journal_path,
            TRANSACTION_STATE_PREPARED,
            TRANSACTION_STATE_ROLLED_BACK,
            records,
        )
        cleanup_transaction_directories(
            stage_directory,
            backup_directory,
            "Altzustand wurde vollständig wiederhergestellt und geprüft",
            lambda: assert_recovery_records_rolled_back(parsed_records, journal_path),
        )
    except (OSError, RuntimeError) as error:
        raise RuntimeError(
            "Altzustand wurde wiederhergestellt, aber sein persistierter Zustand oder die Recovery-Bereinigung "
            "ist fehlgeschlagen. Recovery-Daten bleiben erhalten: "
            f"Backup={backup_directory}; Stage={stage_directory}; {error}"
        ) from error


def recover_orphaned_transactions(installation_directory: Path) -> tuple[str, ...]:
    if not installation_directory.exists():
        return ()
    assert_not_reparse(installation_directory, "Installationsordner")
    transaction_paths = [
        path
        for path in installation_directory.iterdir()
        if path.name.startswith((".stage-", ".backup-"))
    ]
    if not transaction_paths:
        return ()
    stages: dict[str, Path] = {}
    backups: dict[str, Path] = {}
    for path in transaction_paths:
        prefix = ".stage-" if path.name.startswith(".stage-") else ".backup-"
        transaction_id = path.name[len(prefix) :]
        assert_direct_child(installation_directory, path, "verwaister Installationsordner")
        if not transaction_id:
            raise RuntimeError(f"Ungültiger verwaister Installationspfad: {path}")
        assert_not_reparse(path, "verwaister Installationsordner")
        if not path.is_dir():
            raise RuntimeError(f"Ungültiger verwaister Installationspfad: {path}")
        collection = stages if prefix == ".stage-" else backups
        collection[transaction_id] = path

    recovered: list[str] = []
    for transaction_id in sorted(set(stages) | set(backups)):
        stage_directory = stages.get(transaction_id)
        backup_directory = backups.get(transaction_id)
        if backup_directory is None:
            existing = stage_directory
            raise RuntimeError(
                "Unvollständige verwaiste Installation erkannt. Keine Dateien wurden gelöscht; "
                f"manuelle Prüfung erforderlich: {existing}"
            )
        if stage_directory is None:
            stage_directory = installation_directory / f".stage-{transaction_id}"
        recover_transaction(installation_directory, stage_directory, backup_directory)
        recovered.append(transaction_id)
    return tuple(recovered)


def payload_files(target: Path) -> tuple[PayloadFile, ...]:
    return (
        PayloadFile(APPLICATION_FILENAME, payload_path(), target),
        PayloadFile(NOTICE_FILENAME, notice_payload_path(), target.parent / NOTICE_FILENAME),
        PayloadFile(PAYLOAD_ICON_FILENAME, icon_payload_path(), target.parent / ICON_FILENAME),
        PayloadFile(VERSION_FILENAME, version_payload_path(), target.parent / VERSION_FILENAME),
        PayloadFile(
            PAYLOAD_UNINSTALLER_FILENAME,
            uninstaller_payload_path(),
            target.parent / UNINSTALLER_FILENAME,
        ),
    )


def validate_payload_bundle(version: str, files: tuple[PayloadFile, ...]) -> tuple[PayloadFile, ...]:
    if len(files) != len(PAYLOAD_LAYOUT):
        raise RuntimeError(f"Payload muss exakt {len(PAYLOAD_LAYOUT)} Installationsdateien enthalten.")
    manifest_names = {payload.manifest_name for payload in files}
    if manifest_names != set(PAYLOAD_LAYOUT):
        raise RuntimeError("Payload-Dateiliste entspricht nicht der erlaubten Installations-Layout-Definition.")
    installation_directory = files[0].destination.parent
    if installation_directory.exists():
        assert_not_reparse(installation_directory, "Installationsordner")
    destination_keys: set[str] = set()
    for payload in files:
        expected_destination = PAYLOAD_LAYOUT[payload.manifest_name]
        if payload.destination.name.casefold() != expected_destination.casefold():
            raise RuntimeError(
                f"Nicht erlaubtes Payload-Ziel: {payload.manifest_name} -> {payload.destination.name}"
            )
        destination_key = payload.destination.name.casefold()
        if destination_key in destination_keys:
            raise RuntimeError(f"Doppeltes Payload-Ziel: {payload.destination}")
        destination_keys.add(destination_key)
        assert_direct_child(installation_directory, payload.destination, "Payload-Ziel")
        assert_not_reparse(payload.destination, "Payload-Ziel")
        assert_not_reparse(payload.source, "Payload-Quelle")
    try:
        manifest = json.loads(payload_manifest_path().read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Payload-Manifest ist nicht lesbar: {error}") from error

    if manifest.get("schema") != 1:
        raise RuntimeError("Payload-Manifest verwendet ein unbekanntes Format.")
    if manifest.get("version") != version:
        raise RuntimeError(
            f"Payload-Version stimmt nicht: Manifest={manifest.get('version')!r}, Setup={version!r}."
        )
    manifest_files = manifest.get("files")
    if not isinstance(manifest_files, dict):
        raise RuntimeError("Payload-Manifest enthält keine gültige Dateiliste.")

    validated_files: list[PayloadFile] = []
    for payload in files:
        entry = manifest_files.get(payload.manifest_name)
        if not isinstance(entry, dict):
            raise RuntimeError(f"Payload-Manifest enthält keinen Eintrag für {payload.manifest_name}.")
        try:
            expected_size = int(entry["size"])
            expected_hash = str(entry["sha256"]).upper()
            actual_size = payload.source.stat().st_size
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise RuntimeError(f"Payload-Datei ist ungültig: {payload.manifest_name}: {error}") from error
        if expected_size != actual_size or expected_hash != sha256_file(payload.source):
            raise RuntimeError(f"Payload-Prüfung fehlgeschlagen: {payload.manifest_name}")
        validated_files.append(
            PayloadFile(
                payload.manifest_name,
                payload.source,
                payload.destination,
                expected_size,
                expected_hash,
            )
        )

    application = next(item.source for item in files if item.manifest_name == APPLICATION_FILENAME)
    with application.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise RuntimeError("Die eingebettete Anwendung ist keine gültige Windows-EXE.")
    embedded_version = version_payload_path().read_text(encoding="utf-8-sig").strip()
    if embedded_version != version:
        raise RuntimeError(
            f"Eingebettete Versionsdatei stimmt nicht: {embedded_version!r} statt {version!r}."
        )
    return tuple(validated_files)


def install_files_transactionally(files: tuple[PayloadFile, ...]) -> InstallationTransaction:
    if not files:
        raise ValueError("Keine Installationsdateien angegeben.")
    unverified = [payload.manifest_name for payload in files if payload.expected_size is None or not payload.expected_sha256]
    if unverified:
        raise ValueError("Payload-Dateien wurden nicht gegen das Manifest validiert: " + ", ".join(unverified))
    installation_directory = files[0].destination.parent
    installation_directory.mkdir(parents=True, exist_ok=True)
    assert_not_reparse(installation_directory, "Installationsordner")
    for payload in files:
        assert_direct_child(installation_directory, payload.destination, "Payload-Ziel")
        assert_not_reparse(payload.destination, "Payload-Ziel")
        assert_not_reparse(payload.source, "Payload-Quelle")
    transaction_id = uuid4().hex
    stage_directory = installation_directory / f".stage-{transaction_id}"
    backup_directory = installation_directory / f".backup-{transaction_id}"
    stage_directory.mkdir()
    backup_directory.mkdir()
    assert_direct_child(installation_directory, stage_directory, "Stage-Ordner")
    assert_direct_child(installation_directory, backup_directory, "Backup-Ordner")
    assert_not_reparse(stage_directory, "Stage-Ordner")
    assert_not_reparse(backup_directory, "Backup-Ordner")
    transaction = InstallationTransaction(stage_directory, backup_directory, [])

    try:
        staged: list[tuple[PayloadFile, Path]] = []
        for index, payload in enumerate(files):
            stage_path = stage_directory / f"{index:02d}-{payload.destination.name}"
            shutil.copy2(payload.source, stage_path)
            assert_not_reparse(stage_path, "Stage-Datei")
            if not file_matches(stage_path, payload.expected_size, payload.expected_sha256):
                raise OSError(
                    "Payload nach Staging nicht mehr manifestkonform: "
                    f"{payload.manifest_name}; erwartet {payload.expected_size} Bytes / {payload.expected_sha256}"
                )
            staged.append((payload, stage_path))

        for index, (payload, stage_path) in enumerate(staged):
            backup_path = backup_directory / f"{index:02d}-{payload.destination.name}"
            had_original = payload.destination.exists()
            original_size = payload.destination.stat().st_size if had_original else None
            original_sha256 = sha256_file(payload.destination) if had_original else None
            replacement = FileReplacement(
                payload.destination,
                backup_path,
                had_original,
                stage=stage_path,
                original_size=original_size,
                original_sha256=original_sha256,
                payload_size=payload.expected_size,
                payload_sha256=payload.expected_sha256,
            )
            transaction.replacements.append(replacement)

        write_transaction_journal(transaction)
        for replacement in transaction.replacements:
            if replacement.had_original:
                if not file_matches(
                    replacement.destination,
                    replacement.original_size,
                    replacement.original_sha256,
                ):
                    raise OSError(f"Installierte Datei wurde während des Updates verändert: {replacement.destination}")
                os.replace(replacement.destination, replacement.backup)
                replacement.backup_created = True
            if replacement.stage is None or not file_matches(
                replacement.stage,
                replacement.payload_size,
                replacement.payload_sha256,
            ):
                raise OSError(f"Stage-Datei wurde vor der Installation verändert: {replacement.stage}")
            os.replace(replacement.stage, replacement.destination)
            replacement.destination_replaced = True
    except Exception as installation_error:
        try:
            transaction.rollback()
        except Exception as rollback_error:
            raise RuntimeError(
                f"Installation fehlgeschlagen: {installation_error}. {rollback_error}"
            ) from rollback_error
        raise
    return transaction


def application_version() -> str:
    try:
        return version_payload_path().read_text(encoding="utf-8-sig").strip() or "Unbekannt"
    except OSError:
        return "Unbekannt"


def installation_path() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "Programs" / APPLICATION_FOLDER / APPLICATION_FILENAME


def create_desktop_shortcut(
    target: Path,
    icon_path: Path | None = None,
    backup_path: Path | None = None,
) -> Path:
    icon_path = icon_path or target
    quoted_target = powershell_quote(str(target))
    quoted_working_directory = powershell_quote(str(target.parent))
    quoted_shortcut_name = powershell_quote(SHORTCUT_FILENAME)
    quoted_icon = powershell_quote(str(icon_path))
    backup_script = ""
    recovery_script = (
        "if (-not $shortcutExisted -and (Test-Path -LiteralPath $shortcutPath)) { "
        "Remove-Item -LiteralPath $shortcutPath -Force }; "
    )
    if backup_path is not None:
        quoted_backup = powershell_quote(str(backup_path))
        backup_script = (
            f"$backupPath = {quoted_backup}; "
            "$shortcutExisted = Test-Path -LiteralPath $shortcutPath; "
            "if ($shortcutExisted) { "
            "Copy-Item -LiteralPath $shortcutPath -Destination $backupPath -Force }; "
        )
        recovery_script = (
            "if ($shortcutExisted -and (Test-Path -LiteralPath $backupPath)) { "
            "Copy-Item -LiteralPath $backupPath -Destination $shortcutPath -Force "
            "} elseif (-not $shortcutExisted -and (Test-Path -LiteralPath $shortcutPath)) { "
            "Remove-Item -LiteralPath $shortcutPath -Force }; "
        )
    else:
        backup_script = "$shortcutExisted = Test-Path -LiteralPath $shortcutPath; "
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$desktop = [Environment]::GetFolderPath('Desktop'); "
        f"$shortcutPath = Join-Path $desktop {quoted_shortcut_name}; "
        f"{backup_script}"
        "try { "
        "$shell = New-Object -ComObject WScript.Shell; "
        "$shortcut = $shell.CreateShortcut($shortcutPath); "
        f"$shortcut.TargetPath = {quoted_target}; "
        f"$shortcut.WorkingDirectory = {quoted_working_directory}; "
        f"$shortcut.IconLocation = {quoted_icon}; "
        "$shortcut.Description = 'Dokumenten-Scanner-Sortierung'; "
        "$shortcut.Save(); Write-Output $shortcutPath "
        "} catch { "
        "$shortcutError = $_; "
        f"{recovery_script}"
        "throw $shortcutError "
        "}"
    )
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=creation_flags,
    )
    output = result.stdout.strip() if isinstance(result.stdout, str) else ""
    if output:
        return Path(output.splitlines()[-1])
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop" / SHORTCUT_FILENAME


def is_application_running(target: Path) -> bool:
    if os.name != "nt":
        return False
    quoted_process_name = powershell_quote(target.stem)
    quoted_target = powershell_quote(str(target))
    script = (
        f"$target = {quoted_target}; "
        f"$running = @(Get-Process -Name {quoted_process_name} -ErrorAction SilentlyContinue | "
        "Where-Object { $_.Path -and $_.Path -ieq $target }); "
        "if ($running.Count -gt 0) { exit 0 } else { exit 1 }"
    )
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        check=False,
        capture_output=True,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return result.returncode == 0


def notify_shell_icon_change(path: Path) -> None:
    if os.name != "nt":
        return
    import ctypes

    shell32 = ctypes.windll.shell32
    shell32.SHChangeNotify.argtypes = [ctypes.c_long, ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_void_p]
    shell32.SHChangeNotify.restype = None
    shell32.SHChangeNotify(0x00002000, 0x0005, str(path), None)
    shell32.SHChangeNotify(0x08000000, 0x0000, None, None)


def show_message(kind: str, title: str, message: str) -> None:
    if "--silent" in sys.argv:
        if sys.stdout is not None:
            print(f"{title}: {message}")
        return
    import ctypes

    icon = 0x10 if kind == "showerror" else 0x40
    ctypes.windll.user32.MessageBoxW(None, message, title, icon)


def _normalise_action(action: str | bool) -> str:
    if isinstance(action, bool):
        return INSTALL_ACTION_UPDATE if action else INSTALL_ACTION_INSTALL
    return action


def parse_version(version: str | None) -> tuple[int, ...] | None:
    if not version or not re.fullmatch(r"\d+(?:\.\d+){1,3}", version.strip()):
        return None
    return tuple(int(part) for part in version.strip().split("."))


def compare_versions(left: str, right: str) -> int | None:
    left_parts = parse_version(left)
    right_parts = parse_version(right)
    if left_parts is None or right_parts is None:
        return None
    width = max(len(left_parts), len(right_parts))
    normalised_left = left_parts + (0,) * (width - len(left_parts))
    normalised_right = right_parts + (0,) * (width - len(right_parts))
    return (normalised_left > normalised_right) - (normalised_left < normalised_right)


def determine_install_action(
    target_exists: bool,
    installed_version: str | None,
    new_version: str,
    allow_downgrade: bool = False,
    allow_unknown_version: bool = False,
) -> str:
    if not target_exists:
        return INSTALL_ACTION_INSTALL
    comparison = compare_versions(new_version, installed_version or "")
    if comparison is None:
        if allow_unknown_version:
            return INSTALL_ACTION_UPDATE
        return INSTALL_ACTION_UNKNOWN_VERSION_BLOCKED
    if comparison > 0:
        return INSTALL_ACTION_UPDATE
    if comparison == 0:
        return INSTALL_ACTION_REPAIR
    if allow_downgrade:
        return INSTALL_ACTION_DOWNGRADE
    return INSTALL_ACTION_DOWNGRADE_BLOCKED


def _base_prompt_text(action: str | bool, version: str) -> tuple[str, str, str, str]:
    action = _normalise_action(action)
    if action == INSTALL_ACTION_UPDATE:
        return (
            "Update bestätigen",
            f"Update auf Version {version} ausführen?",
            "Die vorhandene Anwendung wird ersetzt. Einstellungen, Protokolle und archivierte Dokumente "
            "bleiben erhalten. Bitte schließen Sie die laufende Anwendung vor dem Update vollständig.",
            "Update ausführen",
        )
    if action == INSTALL_ACTION_REPAIR:
        return (
            "Reparatur bestätigen",
            f"Version {version} erneut installieren?",
            "Die bereits installierte Version wird geprüft und ersetzt. Einstellungen, Protokolle und "
            "archivierte Dokumente bleiben erhalten.",
            "Reparatur ausführen",
        )
    if action == INSTALL_ACTION_DOWNGRADE:
        return (
            "Downgrade bestätigen",
            f"Downgrade auf Version {version} ausführen?",
            "Eine neuere installierte Version wird durch diese ältere Version ersetzt. Einstellungen, Protokolle "
            "und archivierte Dokumente bleiben erhalten. Verwenden Sie dies nur bewusst.",
            "Downgrade ausführen",
        )
    return (
        "Installation bestätigen",
        f"Dokumenten-Scanner-Sortierung {version} installieren?",
        "Die Anwendung wird für den aktuell angemeldeten Windows-Benutzer installiert. "
        "Zusätzlich wird eine Verknüpfung auf dem Desktop erstellt.",
        "Installation ausführen",
    )


def installed_application_version(target: Path | None = None) -> str | None:
    target = target or installation_path()
    version_file = target.parent / VERSION_FILENAME
    try:
        version = version_file.read_text(encoding="utf-8-sig").strip()
        if version:
            return version
    except OSError:
        pass

    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_PATH) as key:
            value, _value_type = winreg.QueryValueEx(key, "DisplayVersion")
    except (ImportError, OSError):
        pass
    else:
        version = str(value).strip()
        if version:
            return version

    app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    log_folder = app_data / APPLICATION_FOLDER / "logs"
    log_files = [log_folder / "dokumentensortierer.log"]
    log_files.extend(sorted(log_folder.glob("dokumentensortierer-*.log"), reverse=True))
    pattern = re.compile(r"Anwendung gestartet;\s*(?:Version\s+|version=)(\d+(?:\.\d+)+)", re.IGNORECASE)
    for log_file in log_files:
        try:
            matches = pattern.findall(log_file.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if matches:
            return matches[-1]
    return None


def prompt_text(action: str | bool, version: str, installed_version: str | None = None) -> tuple[str, str, str, str]:
    action = _normalise_action(action)
    title, instruction, content, action_text = _base_prompt_text(action, version)
    if action in {INSTALL_ACTION_UPDATE, INSTALL_ACTION_REPAIR, INSTALL_ACTION_DOWNGRADE}:
        current_version = installed_version or "Unbekannt"
        transition_label = {
            INSTALL_ACTION_UPDATE: "Update",
            INSTALL_ACTION_REPAIR: "Reparatur",
            INSTALL_ACTION_DOWNGRADE: "Downgrade",
        }[action]
        content = (
            f"Installierte Version: {current_version}\n"
            f"Neue Version: {version}\n"
            f"{transition_label}: {current_version} → {version}\n\n"
            f"{content}"
        )
    return title, instruction, content, action_text


def confirm_installation(action: str | bool, version: str, installed_version: str | None = None) -> bool:
    if "--silent" in sys.argv:
        return True
    title, instruction, content, action_text = prompt_text(action, version, installed_version)
    return show_confirmation(title, instruction, content, action_text, icon_payload_path())


def completion_text(
    action: str | bool,
    version: str,
    installed_version: str | None,
    target: Path,
) -> tuple[str, str, str]:
    action = _normalise_action(action)
    if action in {INSTALL_ACTION_UPDATE, INSTALL_ACTION_REPAIR, INSTALL_ACTION_DOWNGRADE}:
        current_version = installed_version or "Unbekannt"
        labels = {
            INSTALL_ACTION_UPDATE: ("Update abgeschlossen", "Update erfolgreich abgeschlossen", "Update"),
            INSTALL_ACTION_REPAIR: (
                "Reparatur abgeschlossen",
                "Reparatur erfolgreich abgeschlossen",
                "Reparatur",
            ),
            INSTALL_ACTION_DOWNGRADE: (
                "Downgrade abgeschlossen",
                "Downgrade erfolgreich abgeschlossen",
                "Downgrade",
            ),
        }
        title, instruction, transition_label = labels[action]
        return (
            title,
            instruction,
            f"Installierte Version: {current_version}\n"
            f"Neue Version: {version}\n"
            f"{transition_label}: {current_version} → {version}\n\n"
            f"Installationsordner:\n{target.parent}",
        )
    return (
        "Installation abgeschlossen",
        "Installation erfolgreich abgeschlossen",
        f"Installierte Version: {version}\n\n"
        f"Installationsordner:\n{target.parent}\n"
        "Eine Verknüpfung wurde auf dem Desktop erstellt.",
    )


def installed_app_values(target: Path, uninstaller: Path, version: str, estimated_size_kb: int) -> dict[str, str | int]:
    uninstall_command = (
        f'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{uninstaller}"'
    )
    return {
        "DisplayName": DISPLAY_NAME,
        "DisplayVersion": version,
        "Publisher": PUBLISHER,
        "Contact": SUPPORT_EMAIL,
        "HelpLink": f"mailto:{SUPPORT_EMAIL}",
        "InstallLocation": str(target.parent),
        "DisplayIcon": f'"{target.parent / ICON_FILENAME}"',
        "UninstallString": uninstall_command,
        "QuietUninstallString": f"{uninstall_command} -Silent",
        "InstallDate": datetime.now().strftime("%Y%m%d"),
        "EstimatedSize": estimated_size_kb,
        "NoModify": 1,
        "NoRepair": 1,
        "Language": 1031,
    }


def register_installed_application(target: Path, uninstaller: Path, version: str, files: tuple[Path, ...]) -> None:
    import winreg

    estimated_size_kb = max(1, (sum(path.stat().st_size for path in files) + 1023) // 1024)
    values = installed_app_values(target, uninstaller, version, estimated_size_kb)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_PATH) as key:
        for name, value in values.items():
            value_type = winreg.REG_DWORD if isinstance(value, int) else winreg.REG_SZ
            winreg.SetValueEx(key, name, 0, value_type, value)


def installed_application_registration() -> dict[str, tuple[object, int]] | None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_PATH) as key:
            values: dict[str, tuple[object, int]] = {}
            index = 0
            while True:
                try:
                    name, value, value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                values[name] = (value, value_type)
                index += 1
            return values
    except OSError:
        return None


def restore_installed_application_registration(snapshot: dict[str, tuple[object, int]] | None) -> None:
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_PATH)
    except OSError:
        pass
    if snapshot is None:
        return
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_REGISTRY_PATH) as key:
        for name, (value, value_type) in snapshot.items():
            winreg.SetValueEx(key, name, 0, value_type, value)


@dataclass(slots=True)
class SetupMutex:
    handle: int | None
    kernel32: object | None

    def release(self) -> None:
        if self.handle is None or self.kernel32 is None:
            return
        try:
            self.kernel32.ReleaseMutex(self.handle)  # type: ignore[attr-defined]
        finally:
            self.kernel32.CloseHandle(self.handle)  # type: ignore[attr-defined]
            self.handle = None


def acquire_setup_mutex() -> SetupMutex | None:
    if os.name != "nt":
        return SetupMutex(None, None)
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.ReleaseMutex.argtypes = [ctypes.c_void_p]
    kernel32.ReleaseMutex.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, True, SETUP_MUTEX_NAME)
    last_error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(last_error)
    if last_error == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        return None
    return SetupMutex(handle, kernel32)


def setup_self_test() -> int:
    try:
        version = application_version()
        if parse_version(version) is None:
            raise RuntimeError(f"Ungültige Setup-Version: {version!r}")
        dummy_target = Path("self-test") / APPLICATION_FILENAME
        validated = validate_payload_bundle(version, payload_files(dummy_target))
        if len(validated) != 5:
            raise RuntimeError(f"Unerwartete Payload-Anzahl: {len(validated)}")
    except Exception as error:
        if sys.stderr is not None:
            print(f"Setup-Selbsttest fehlgeschlagen: {error}", file=sys.stderr)
        return 1
    if sys.stdout is not None:
        print(f"Setup-Selbsttest erfolgreich: Version {version}; {len(validated)} Payload-Dateien")
    return 0


def run_installation() -> int:
    target = installation_path()
    try:
        recovered = recover_orphaned_transactions(target.parent)
    except (OSError, RuntimeError) as error:
        show_message(
            "showerror",
            "Installation blockiert",
            "Eine frühere Installation wurde nicht sauber abgeschlossen. "
            f"Die vorhandenen Recovery-Daten wurden nicht stillschweigend gelöscht.\n\n{error}",
        )
        return 1
    if recovered and "--silent" in sys.argv and sys.stdout is not None:
        print("Wiederhergestellte Installationsvorgänge: " + ", ".join(recovered))
    target_exists = target.exists()
    version = application_version()
    if parse_version(version) is None:
        show_message("showerror", "Installation abgebrochen", f"Ungültige Setup-Version: {version!r}")
        return 1
    installed_version = installed_application_version(target) if target_exists else None
    action = determine_install_action(
        target_exists,
        installed_version,
        version,
        allow_downgrade="--allow-downgrade" in sys.argv,
        allow_unknown_version="--allow-unknown-version" in sys.argv,
    )
    if action == INSTALL_ACTION_UNKNOWN_VERSION_BLOCKED:
        show_message(
            "showerror",
            "Installation blockiert",
            "Die vorhandene Anwendung besitzt keine lesbare, gültige Versionsinformation. "
            "Aus Sicherheitsgründen wurde sie nicht ersetzt.\n\n"
            "Nur eine bewusste administrative Reparatur darf mit dem Startparameter "
            "--allow-unknown-version fortfahren.",
        )
        return 2
    if action == INSTALL_ACTION_DOWNGRADE_BLOCKED:
        show_message(
            "showerror",
            "Downgrade blockiert",
            f"Installiert ist Version {installed_version or 'Unbekannt'}, dieses Setup enthält die ältere "
            f"Version {version}. Die vorhandene Anwendung wurde nicht verändert.\n\n"
            "Ein bewusster administrativer Downgrade ist nur mit dem Startparameter --allow-downgrade möglich.",
        )
        return 2
    if target_exists and is_application_running(target):
        show_message(
            "showerror",
            "Installation abgebrochen",
            "Die Anwendung läuft noch und muss vor dem Update vollständig beendet werden. "
            "Beenden Sie sie über das Symbol im Windows-Infobereich und starten Sie das Setup anschließend erneut.",
        )
        return 1
    if not confirm_installation(action, version, installed_version):
        return 0

    transaction: InstallationTransaction | None = None
    registry_snapshot: dict[str, tuple[object, int]] | None = None
    registry_touched = False
    shortcut_path: Path | None = None
    shortcut_backup: Path | None = None
    cleanup_warning: str | None = None
    try:
        files = validate_payload_bundle(version, payload_files(target))
        if "--silent" in sys.argv:
            if sys.stdout is not None:
                print(f"Installiere {payload_path()} nach {target}")
        registry_snapshot = installed_application_registration()
        transaction = install_files_transactionally(files)
        installed_notice = target.parent / NOTICE_FILENAME
        installed_icon = target.parent / ICON_FILENAME
        installed_version_file = target.parent / VERSION_FILENAME
        installed_uninstaller = target.parent / UNINSTALLER_FILENAME
        shortcut_backup = transaction.backup_directory / SHORTCUT_FILENAME
        shortcut_path = create_desktop_shortcut(target, installed_icon, shortcut_backup)
        registry_touched = True
        register_installed_application(
            target,
            installed_uninstaller,
            version,
            (target, installed_notice, installed_icon, installed_version_file, installed_uninstaller),
        )
        cleanup_warning = transaction.commit()
        transaction = None
        registry_touched = False
        shortcut_path = None
        shortcut_backup = None
    except Exception as error:
        rollback_errors: list[str] = []
        if registry_touched:
            try:
                restore_installed_application_registration(registry_snapshot)
            except Exception as rollback_error:
                rollback_errors.append(f"Windows-Registrierung: {rollback_error}")
        if shortcut_path is not None:
            try:
                if shortcut_backup is not None and shortcut_backup.exists():
                    os.replace(shortcut_backup, shortcut_path)
                elif shortcut_path.exists():
                    shortcut_path.unlink()
            except OSError as rollback_error:
                rollback_errors.append(f"Desktop-Verknüpfung: {rollback_error}")
        if transaction is not None:
            try:
                transaction.rollback()
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        rollback_text = ""
        if rollback_errors:
            rollback_text = "\n\nRollback-Hinweis: " + " | ".join(rollback_errors)
        title = "Update nicht möglich" if target_exists else "Installation fehlgeschlagen"
        show_message("showerror", title, f"Es wurden keine unvollständigen Programmdateien übernommen.\n\n{error}{rollback_text}")
        return 1

    if cleanup_warning:
        show_message(
            "showerror",
            "Installation abgeschlossen – Bereinigung unvollständig",
            cleanup_warning
            + "\n\nDie Anwendung wurde installiert. Beim nächsten Setup-Start wird die "
            "festgeschriebene Recovery-Ablage erneut sicher bereinigt.",
        )

    try:
        (target.parent / LEGACY_UNINSTALLER_FILENAME).unlink(missing_ok=True)
    except OSError:
        # Eine gesperrte Altdatei darf eine bereits atomar abgeschlossene
        # Installation nicht nachträglich als fehlgeschlagen markieren.
        pass

    try:
        notify_shell_icon_change(installed_icon)
        notify_shell_icon_change(Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop" / SHORTCUT_FILENAME)
    except Exception:
        pass

    if "--silent" in sys.argv:
        should_launch = "--no-launch" not in sys.argv
        show_message(
            "showinfo",
            "Installation abgeschlossen",
            f"Version {version} wurde erfolgreich installiert.",
        )
    else:
        title, instruction, content = completion_text(action, version, installed_version, target)
        should_launch = show_completion(
            title,
            instruction,
            content,
            icon_payload_path(),
            start_application="--no-launch" not in sys.argv,
        )
    if should_launch:
        subprocess.Popen([str(target)], close_fds=True)
    return 0


def main() -> int:
    if "--self-test" in sys.argv:
        return setup_self_test()
    try:
        mutex = acquire_setup_mutex()
    except OSError as error:
        show_message(
            "showerror",
            "Installation blockiert",
            f"Die serverweite Installationssperre konnte nicht erstellt werden.\n\n{error}",
        )
        return 3
    if mutex is None:
        show_message(
            "showerror",
            "Installation läuft bereits",
            "Eine andere Installation oder ein Update der Dokumenten-Scanner-Sortierung läuft bereits. "
            "Bitte warten Sie bis zum Abschluss und starten Sie dieses Setup danach erneut.",
        )
        return 3
    try:
        return run_installation()
    finally:
        mutex.release()


if __name__ == "__main__":
    raise SystemExit(main())
