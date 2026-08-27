"""Lokale Diagnoseberichte aus den taeglichen Anwendungsprotokollen."""

from __future__ import annotations

import html
import json
import math
import os
import re
import statistics
import tempfile
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from . import __version__


SCHEMA_VERSION = 2
DEFAULT_REPORT_DAYS = 30
ALLOWED_REPORT_DAYS = (7, 30, 90)
LEGACY_REASON_CODE = "legacy_nicht_spezifiziert"

_LOG_FILE_RE = re.compile(r"^dokumentensortierer-(\d{4}-\d{2}-\d{2})\.log$")
_LOG_LINE_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2}) "
    r"(?P<time>\d{2}:\d{2}:\d{2}),(?P<millis>\d{3}) "
    r"(?P<level>[A-Z]+) (?P<logger>\S+) \[[^\]]+\]: (?P<message>.*)$"
)
_FIELD_RE = re.compile(
    r"(?:^|;\s*)(?P<key>[a-z][a-z0-9_]*)="
    r"(?P<value>.*?)(?=;\s*[a-z][a-z0-9_]*=|$)"
)

_EVENT_KIND_BY_CODE = {
    "processing_started": "processing_started",
    "processing_completed": "processing",
    "processing_failed": "processing",
    "processing_deferred": "processing",
    "monitor_heartbeat": "heartbeat",
    "monitor_started": "monitor_start",
    "monitor_stopped": "monitor_stop",
    "application_started": "start",
    "application_stopped": "stop",
    "folder_error": "folder_error",
    "folder_error_continues": "folder_error_continues",
    "folder_recovered": "folder_recovery",
}

_COMMON_EVENT_FIELDS = {"schema", "ereignis", "sitzung", "version"}
_KNOWN_FIELDS_BY_KIND = {
    "processing_started": {"id", "datei", "groesse_bytes", "quelle"},
    "processing": {
        "id",
        "status",
        "grundcode",
        "stufe",
        "phase",
        "seite",
        "datei",
        "groesse_bytes",
        "seiten",
        "dokumente",
        "typen",
        "archiv_s",
        "erkennung_s",
        "ausgabe_s",
        "gesamt_s",
        "ausgaben",
        "ziel",
        "pruefkopie",
        "grund",
        "fehlerklasse",
    },
    "heartbeat": {
        "anwendung",
        "ueberwachung",
        "prozess_id",
        "tesseract",
        "leptonica",
        "modus",
        "intervall_s",
        "laufzeit_s",
        "verarbeitung",
        "wartende_pdfs",
        "eingang",
        "ziel",
        "archiv",
        "pruefordner",
        "fortlaufende_ordnerfehler",
        "letzter_vorgang",
        "letzter_status",
        "letzte_datei",
    },
    "monitor_start": {
        "modus",
        "eingang",
        "ziel",
        "archiv",
        "pruefordner",
        "archiv_tage",
        "dateistabilitaet_s",
        "defekt_timeout_s",
        "abfrage_s",
        "stapel_grenze",
        "stapel_pause_s",
        "verarbeitungs_limit_s",
        "ocr_sprachen",
        "tesseract",
    },
    "monitor_stop": {"modus", "laufzeit_s"},
    "start": {
        "modus",
        "python",
        "tesseract",
        "leptonica",
        "system",
        "architektur",
        "prozess_id",
        "protokoll",
    },
    "stop": {"modus", "grund", "exit_code", "laufzeit_s"},
    "folder_error": {"versuch", "erneut_in_s", "fehlerklasse", "fehler"},
    "folder_error_continues": {
        "versuche",
        "erster_fehler",
        "dauer_s",
        "erneut_in_s",
        "fehlerklasse",
        "fehler",
    },
    "folder_recovery": {"fehlerdauer_s", "versuche"},
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        result = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_fields(message: str) -> dict[str, str]:
    return {
        match.group("key"): match.group("value").strip()
        for match in _FIELD_RE.finditer(message)
    }


def _statistics(values: Iterable[float]) -> dict[str, float | int | None]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {
            "count": 0,
            "average": None,
            "median": None,
            "p95": None,
            "maximum": None,
        }
    p95_index = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return {
        "count": len(ordered),
        "average": round(sum(ordered) / len(ordered), 3),
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "maximum": round(ordered[-1], 3),
    }


def _recognition_rate(successful: int, not_recognized: int) -> float | None:
    total = successful + not_recognized
    if not total:
        return None
    return round(successful / total, 4)


def _new_result_bucket() -> dict[str, int]:
    return {
        "total": 0,
        "successful": 0,
        "not_recognized": 0,
        "technical_errors": 0,
        "deferred": 0,
    }


def _update_bucket(bucket: dict[str, int], status: str) -> None:
    bucket["total"] += 1
    if status == "erfolgreich":
        bucket["successful"] += 1
    elif status == "nicht_erkannt":
        bucket["not_recognized"] += 1
    elif status == "fehler":
        bucket["technical_errors"] += 1
    elif status == "offen":
        bucket["deferred"] += 1


def _finish_bucket(bucket: dict[str, int]) -> dict[str, int | float | None]:
    result: dict[str, int | float | None] = dict(bucket)
    result["recognition_rate"] = _recognition_rate(
        bucket["successful"], bucket["not_recognized"]
    )
    return result


def _log_files(log_directory: Path, start_date: date, end_date: date) -> list[Path]:
    files: list[tuple[date, Path]] = []
    if not log_directory.is_dir():
        return []
    try:
        children = list(log_directory.iterdir())
    except OSError:
        return []
    for child in children:
        match = _LOG_FILE_RE.match(child.name)
        if not match or not child.is_file():
            continue
        try:
            file_date = date.fromisoformat(match.group(1))
        except ValueError:
            continue
        if start_date <= file_date <= end_date:
            files.append((file_date, child))
    return [path for _, path in sorted(files)]


def _parse_log_line(line: str) -> tuple[datetime, str, str, str] | None:
    match = _LOG_LINE_RE.match(line.rstrip("\r\n"))
    if not match:
        return None
    try:
        timestamp = datetime.strptime(
            f"{match.group('date')} {match.group('time')}.{match.group('millis')}",
            "%Y-%m-%d %H:%M:%S.%f",
        )
    except ValueError:
        return None
    return timestamp, match.group("level"), match.group("logger"), match.group("message")


def _event_kind(message: str, fields: dict[str, str]) -> str | None:
    event_code = fields.get("ereignis")
    if event_code:
        return _EVENT_KIND_BY_CODE.get(event_code)
    for prefix, kind in (
        ("Vorgang gestartet", "processing_started"),
        ("Vorgang abgeschlossen", "processing"),
        ("Vorgang fehlgeschlagen", "processing"),
        ("Vorgang zurückgestellt", "processing"),
        ("Betriebsstatus", "heartbeat"),
        ("Überwachung gestartet", "monitor_start"),
        ("Überwachung beendet", "monitor_stop"),
        ("Anwendung gestartet", "start"),
        ("Anwendung beendet", "stop"),
        ("Fehler bei der Ordnerüberwachung", "folder_error"),
        ("Ordnerfehler besteht fort", "folder_error_continues"),
        ("Ordnerüberwachung wiederhergestellt", "folder_recovery"),
    ):
        if message.startswith(prefix):
            return kind
    return None


def _document_types(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    result: list[str] = []
    for item in raw_value.split(","):
        document_type = item.partition(":")[0].strip()
        if document_type and document_type.lower() not in {"keine", "none"}:
            result.append(document_type)
    return result


def build_diagnostic_report(
    log_directory: str | Path,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    include_filenames: bool = False,
    end_date: date | None = None,
    created_at: datetime | None = None,
) -> dict[str, Any]:
    """Liest Tageslogs und erstellt die maschinenlesbare Berichtsdatenstruktur."""

    if days not in ALLOWED_REPORT_DAYS:
        raise ValueError(f"Zeitraum muss einer von {ALLOWED_REPORT_DAYS} sein")

    report_end = end_date or date.today()
    report_start = report_end - timedelta(days=days - 1)
    files = _log_files(Path(log_directory), report_start, report_end)

    parser: dict[str, Any] = {
        "log_files_found": len(files),
        "log_files_read": 0,
        "log_files_unreadable": 0,
        "lines_read": 0,
        "parsed_log_lines": 0,
        "recognized_events": 0,
        "ignored_lines": 0,
        "malformed_lines": 0,
        "legacy_records": 0,
        "records_with_unknown_fields": 0,
        "unknown_fields_ignored": 0,
    }
    processing_events: list[dict[str, Any]] = []
    queue_values: list[int] = []
    heartbeat_events: list[dict[str, Any]] = []
    starts = 0
    stops = 0
    monitor_starts = 0
    monitor_stops = 0
    folder_errors = 0
    folder_error_continues = 0
    folder_recoveries = 0
    active_version = "unbekannt"
    level_counts: Counter[str] = Counter()
    unclassified_by_level: Counter[str] = Counter()
    unclassified_by_logger: Counter[str] = Counter()
    unknown_fields: Counter[str] = Counter()
    starts_by_day: Counter[str] = Counter()
    stops_by_day: Counter[str] = Counter()
    shutdown_reasons: Counter[str] = Counter()
    started_sessions: set[str] = set()
    stopped_sessions: set[str] = set()

    for path in files:
        try:
            stream = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            parser["log_files_unreadable"] += 1
            continue
        parser["log_files_read"] += 1
        with stream:
            for line in stream:
                parser["lines_read"] += 1
                parsed = _parse_log_line(line)
                if parsed is None:
                    parser["malformed_lines"] += 1
                    continue
                parser["parsed_log_lines"] += 1
                timestamp, level, logger_name, message = parsed
                level_counts[level] += 1
                fields = _parse_fields(message)
                kind = _event_kind(message, fields)
                if kind is None:
                    parser["ignored_lines"] += 1
                    unclassified_by_level[level] += 1
                    unclassified_by_logger[logger_name] += 1
                    continue
                parser["recognized_events"] += 1
                allowed_fields = _COMMON_EVENT_FIELDS | _KNOWN_FIELDS_BY_KIND.get(kind, set())
                event_unknown_fields = [key for key in fields if key not in allowed_fields]
                if event_unknown_fields:
                    parser["records_with_unknown_fields"] += 1
                    unknown_fields.update(event_unknown_fields)

                if fields.get("version"):
                    active_version = fields["version"]
                if kind == "start":
                    starts += 1
                    starts_by_day[timestamp.date().isoformat()] += 1
                    if fields.get("sitzung"):
                        started_sessions.add(fields["sitzung"])
                    continue
                if kind == "stop":
                    stops += 1
                    stops_by_day[timestamp.date().isoformat()] += 1
                    shutdown_reasons[fields.get("grund") or LEGACY_REASON_CODE] += 1
                    if fields.get("sitzung"):
                        stopped_sessions.add(fields["sitzung"])
                    continue
                if kind == "heartbeat":
                    queued = _safe_int(fields.get("wartende_pdfs"))
                    if queued is not None and queued >= 0:
                        queue_values.append(queued)
                    heartbeat_events.append(
                        {
                            "timestamp": timestamp,
                            "session_id": fields.get("sitzung"),
                            "interval_seconds": _safe_float(fields.get("intervall_s")) or 600.0,
                        }
                    )
                    continue
                if kind == "monitor_start":
                    monitor_starts += 1
                    continue
                if kind == "monitor_stop":
                    monitor_stops += 1
                    continue
                if kind == "processing_started":
                    continue
                if kind == "folder_error":
                    folder_errors += 1
                    continue
                if kind == "folder_error_continues":
                    folder_error_continues += 1
                    continue
                if kind == "folder_recovery":
                    folder_recoveries += 1
                    continue

                status = fields.get("status", "unbekannt")
                if status == "unbekannt":
                    if message.startswith("Vorgang fehlgeschlagen"):
                        status = "fehler"
                    elif message.startswith("Vorgang zurückgestellt"):
                        status = "offen"
                reason_code: str | None = fields.get("grundcode")
                if status == "nicht_erkannt" and not reason_code:
                    reason_code = LEGACY_REASON_CODE
                    parser["legacy_records"] += 1
                elif status != "erfolgreich" and not reason_code:
                    reason_code = "nicht_angegeben"
                elif status == "erfolgreich":
                    reason_code = None
                filename = Path(fields.get("datei", "")).name or None
                processing_events.append(
                    {
                        "timestamp": timestamp,
                        "status": status,
                        "level": level,
                        "logger": logger_name,
                        "operation_id": fields.get("id") or None,
                        "session_id": fields.get("sitzung") or None,
                        "version": fields.get("version") or active_version,
                        "reason_code": reason_code,
                        "stage": fields.get("stufe") or fields.get("phase") or None,
                        "page": _safe_int(fields.get("seite")),
                        "page_count": _safe_int(fields.get("seiten")),
                        "duration_seconds": _safe_float(fields.get("gesamt_s")),
                        "archive_seconds": _safe_float(fields.get("archiv_s")),
                        "recognition_seconds": _safe_float(fields.get("erkennung_s")),
                        "output_seconds": _safe_float(fields.get("ausgabe_s")),
                        "size_bytes": _safe_int(fields.get("groesse_bytes")),
                        "document_types": _document_types(fields.get("typen")),
                        "filename": filename,
                    }
                )

    parser["unknown_fields_ignored"] = sum(unknown_fields.values())
    parser["unknown_field_names"] = dict(sorted(unknown_fields.items()))
    parser["lines_by_level"] = dict(sorted(level_counts.items()))
    parser["unclassified_lines_by_level"] = dict(sorted(unclassified_by_level.items()))
    parser["unclassified_lines_by_logger"] = dict(sorted(unclassified_by_logger.items()))

    overall = _new_result_bucket()
    by_day: defaultdict[str, dict[str, int]] = defaultdict(_new_result_bucket)
    by_version: defaultdict[str, dict[str, int]] = defaultdict(_new_result_bucket)
    by_reason: Counter[str] = Counter()
    by_document_type: Counter[str] = Counter()
    durations: list[float] = []
    archive_durations: list[float] = []
    recognition_durations: list[float] = []
    output_durations: list[float] = []
    sizes: list[float] = []
    problem_cases: list[dict[str, Any]] = []

    def public_case(event: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "timestamp": event["timestamp"].isoformat(timespec="milliseconds"),
            "status": event["status"],
            "reason_code": event["reason_code"],
            "stage": event["stage"],
            "page": event["page"],
            "page_count": event["page_count"],
            "duration_seconds": event["duration_seconds"],
            "archive_seconds": event["archive_seconds"],
            "recognition_seconds": event["recognition_seconds"],
            "output_seconds": event["output_seconds"],
            "size_bytes": event["size_bytes"],
        }
        if include_filenames:
            result["filename"] = event["filename"]
        return result

    for event in processing_events:
        status = event["status"]
        _update_bucket(overall, status)
        _update_bucket(by_day[event["timestamp"].date().isoformat()], status)
        _update_bucket(by_version[event["version"]], status)
        if event["reason_code"]:
            by_reason[event["reason_code"]] += 1
        for document_type in event["document_types"]:
            by_document_type[document_type] += 1
        if event["duration_seconds"] is not None:
            durations.append(event["duration_seconds"])
        if event["archive_seconds"] is not None:
            archive_durations.append(event["archive_seconds"])
        if event["recognition_seconds"] is not None:
            recognition_durations.append(event["recognition_seconds"])
        if event["output_seconds"] is not None:
            output_durations.append(event["output_seconds"])
        if event["size_bytes"] is not None and event["size_bytes"] >= 0:
            sizes.append(float(event["size_bytes"]))
        if status != "erfolgreich":
            problem_cases.append(public_case(event))

    slowest_cases = [
        public_case(event)
        for event in sorted(
            (event for event in processing_events if event["duration_seconds"] is not None),
            key=lambda event: event["duration_seconds"],
            reverse=True,
        )[:10]
    ]

    all_days: dict[str, dict[str, int | float | None]] = {}
    current_day = report_start
    while current_day <= report_end:
        day_key = current_day.isoformat()
        all_days[day_key] = _finish_bucket(by_day[day_key])
        current_day += timedelta(days=1)

    size_statistics = _statistics(sizes)
    if size_statistics["average"] is not None:
        size_statistics["average"] = int(round(float(size_statistics["average"])))
        size_statistics["median"] = int(round(float(size_statistics["median"])))
        size_statistics["p95"] = int(round(float(size_statistics["p95"])))
        size_statistics["maximum"] = int(round(float(size_statistics["maximum"])))

    heartbeats_by_session: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for heartbeat in heartbeat_events:
        if heartbeat["session_id"]:
            heartbeats_by_session[heartbeat["session_id"]].append(heartbeat)
    heartbeat_gaps: list[float] = []
    interruption_count = 0
    for session_heartbeats in heartbeats_by_session.values():
        ordered = sorted(session_heartbeats, key=lambda item: item["timestamp"])
        for previous, current in zip(ordered, ordered[1:]):
            gap = (current["timestamp"] - previous["timestamp"]).total_seconds()
            heartbeat_gaps.append(gap)
            expected = max(previous["interval_seconds"], current["interval_seconds"])
            if gap > expected * 2.5:
                interruption_count += 1

    application_by_day: dict[str, dict[str, int]] = {}
    current_day = report_start
    while current_day <= report_end:
        day_key = current_day.isoformat()
        application_by_day[day_key] = {
            "starts": starts_by_day[day_key],
            "controlled_stops": stops_by_day[day_key],
        }
        current_day += timedelta(days=1)

    unclassified_warning_count = unclassified_by_level["WARNING"]
    unclassified_error_count = (
        unclassified_by_level["ERROR"] + unclassified_by_level["CRITICAL"]
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": _iso_datetime(created_at or _utc_now()),
        "application_version": __version__,
        "report_period": {
            "from": report_start.isoformat(),
            "to": report_end.isoformat(),
            "days": days,
        },
        "privacy": {
            "filenames_included": bool(include_filenames),
            "full_paths_included": False,
            "raw_logs_included": False,
            "pdfs_included": False,
            "ocr_full_text_included": False,
        },
        "parser_statistics": parser,
        "summary": {
            "processing_results": _finish_bucket(overall),
            "duration_seconds": _statistics(durations),
            "phase_duration_seconds": {
                "archive": _statistics(archive_durations),
                "recognition": _statistics(recognition_durations),
                "output": _statistics(output_durations),
            },
            "file_size_bytes": size_statistics,
            "queue": {
                "measurements": len(queue_values),
                "average_waiting_pdfs": (
                    round(sum(queue_values) / len(queue_values), 3)
                    if queue_values
                    else None
                ),
                "maximum_waiting_pdfs": max(queue_values) if queue_values else None,
            },
            "folder_monitoring": {
                "errors": folder_errors,
                "continuing_error_messages": folder_error_continues,
                "recoveries": folder_recoveries,
            },
            "application_starts": starts,
            "application_lifecycle": {
                "starts": starts,
                "controlled_stops": stops,
                "identified_sessions_started": len(started_sessions),
                "identified_sessions_stopped": len(stopped_sessions),
                "sessions_without_stop": len(started_sessions - stopped_sessions),
                "stops_without_start": len(stopped_sessions - started_sessions),
                "shutdown_reasons": dict(sorted(shutdown_reasons.items())),
            },
            "monitoring_lifecycle": {
                "starts": monitor_starts,
                "controlled_stops": monitor_stops,
            },
            "heartbeat_continuity": {
                "session_aware_measurements": sum(len(items) for items in heartbeats_by_session.values()),
                "identified_sessions": len(heartbeats_by_session),
                "gaps_evaluated": len(heartbeat_gaps),
                "suspected_interruptions": interruption_count,
                "maximum_gap_seconds": round(max(heartbeat_gaps), 3) if heartbeat_gaps else None,
            },
            "log_health": {
                "warning_lines": level_counts["WARNING"],
                "error_lines": level_counts["ERROR"],
                "critical_lines": level_counts["CRITICAL"],
                "unclassified_warning_lines": unclassified_warning_count,
                "unclassified_error_or_critical_lines": unclassified_error_count,
            },
        },
        "grouped_by_day": all_days,
        "application_lifecycle_by_day": application_by_day,
        "grouped_by_version": {
            key: _finish_bucket(value) for key, value in sorted(by_version.items())
        },
        "grouped_by_document_type": dict(sorted(by_document_type.items())),
        "grouped_by_reason_code": dict(sorted(by_reason.items())),
        "problem_cases": problem_cases,
        "slowest_cases": slowest_cases,
    }


def _format_number(value: Any, digits: int = 1) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}".replace(".", ",")
    return str(value)


def _format_percent(value: Any) -> str:
    if value is None:
        return "–"
    return f"{float(value) * 100:.1f} %".replace(".", ",")


def render_diagnostic_html(report: dict[str, Any]) -> str:
    """Erzeugt eine komplett lokale, skriptfreie HTML-Zusammenfassung."""

    def esc(value: Any) -> str:
        return html.escape(str(value), quote=True)

    summary = report["summary"]
    results = summary["processing_results"]
    durations = summary["duration_seconds"]
    phases = summary["phase_duration_seconds"]
    lifecycle = summary["application_lifecycle"]
    log_health = summary["log_health"]
    privacy = report["privacy"]

    day_rows = "".join(
        "<tr>"
        f"<td>{esc(day)}</td><td>{values['total']}</td>"
        f"<td>{values['successful']}</td><td>{values['not_recognized']}</td>"
        f"<td>{values['technical_errors']}</td>"
        f"<td>{esc(_format_percent(values['recognition_rate']))}</td>"
        "</tr>"
        for day, values in report["grouped_by_day"].items()
    )
    reason_rows = "".join(
        f"<tr><td>{esc(reason)}</td><td>{count}</td></tr>"
        for reason, count in report["grouped_by_reason_code"].items()
    ) or '<tr><td colspan="2">Keine Grundcodes vorhanden.</td></tr>'
    version_rows = "".join(
        "<tr>"
        f"<td>{esc(version)}</td><td>{values['total']}</td>"
        f"<td>{values['successful']}</td><td>{values['not_recognized']}</td>"
        f"<td>{esc(_format_percent(values['recognition_rate']))}</td>"
        "</tr>"
        for version, values in report["grouped_by_version"].items()
    ) or '<tr><td colspan="5">Keine Verarbeitungsvorgänge vorhanden.</td></tr>'
    type_rows = "".join(
        f"<tr><td>{esc(document_type)}</td><td>{count}</td></tr>"
        for document_type, count in report["grouped_by_document_type"].items()
    ) or '<tr><td colspan="2">Keine Dokumenttypen vorhanden.</td></tr>'
    phase_rows = "".join(
        "<tr>"
        f"<td>{esc(label)}</td><td>{values['count']}</td>"
        f"<td>{esc(_format_number(values['average'], 3))}</td>"
        f"<td>{esc(_format_number(values['median'], 3))}</td>"
        f"<td>{esc(_format_number(values['p95'], 3))}</td>"
        f"<td>{esc(_format_number(values['maximum'], 3))}</td>"
        "</tr>"
        for key, label in (
            ("archive", "Archivierung"),
            ("recognition", "Erkennung"),
            ("output", "Ausgabe"),
        )
        for values in (phases[key],)
    )
    unknown_field_rows = "".join(
        f"<tr><td>{esc(field)}</td><td>{count}</td></tr>"
        for field, count in report["parser_statistics"]["unknown_field_names"].items()
    ) or '<tr><td colspan="2">Keine unbekannten Felder.</td></tr>'
    shutdown_reason_rows = "".join(
        f"<tr><td>{esc(reason)}</td><td>{count}</td></tr>"
        for reason, count in lifecycle["shutdown_reasons"].items()
    ) or '<tr><td colspan="2">Keine Stop-Ereignisse vorhanden.</td></tr>'

    filename_header = "<th>Dateiname</th>" if privacy["filenames_included"] else ""
    problem_rows_parts: list[str] = []
    for case in report["problem_cases"]:
        filename_cell = (
            f"<td>{esc(case.get('filename') or '–')}</td>"
            if privacy["filenames_included"]
            else ""
        )
        problem_rows_parts.append(
            "<tr>"
            f"<td>{esc(case['timestamp'])}</td><td>{esc(case['status'])}</td>"
            f"<td>{esc(case.get('reason_code') or '–')}</td>"
            f"<td>{esc(case.get('stage') or '–')}</td>"
            f"<td>{esc(_format_number(case.get('page'), 0))}</td>"
            f"<td>{esc(_format_number(case.get('page_count'), 0))}</td>"
            f"<td>{esc(_format_number(case.get('duration_seconds'), 2))}</td>"
            f"<td>{esc(_format_number(case.get('size_bytes'), 0))}</td>"
            f"{filename_cell}</tr>"
        )
    problem_rows = "".join(problem_rows_parts) or (
        f'<tr><td colspan="{9 if privacy["filenames_included"] else 8}">'
        "Keine Problemfälle vorhanden.</td></tr>"
    )

    slowest_rows_parts: list[str] = []
    for case in report["slowest_cases"]:
        filename_cell = (
            f"<td>{esc(case.get('filename') or '–')}</td>"
            if privacy["filenames_included"]
            else ""
        )
        slowest_rows_parts.append(
            "<tr>"
            f"<td>{esc(case['timestamp'])}</td><td>{esc(case['status'])}</td>"
            f"<td>{esc(_format_number(case.get('duration_seconds'), 2))}</td>"
            f"<td>{esc(_format_number(case.get('archive_seconds'), 2))}</td>"
            f"<td>{esc(_format_number(case.get('recognition_seconds'), 2))}</td>"
            f"<td>{esc(_format_number(case.get('output_seconds'), 2))}</td>"
            f"{filename_cell}</tr>"
        )
    slowest_rows = "".join(slowest_rows_parts) or (
        f'<tr><td colspan="{7 if privacy["filenames_included"] else 6}">'
        "Keine Laufzeitdaten vorhanden.</td></tr>"
    )

    return f"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnosebericht Dokumentenscanner</title>
<style>
body {{ font-family: Segoe UI, Arial, sans-serif; margin: 2rem; color: #17202a; background: #f7f9fb; }}
h1, h2 {{ color: #17365d; }}
.meta, .cards, section {{ max-width: 1100px; margin: 0 auto 1.5rem; }}
.cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: .8rem; }}
.card, section {{ background: white; border: 1px solid #dce3ea; border-radius: 8px; padding: 1rem; }}
.value {{ font-size: 1.55rem; font-weight: 650; margin-top: .25rem; }}
table {{ border-collapse: collapse; width: 100%; font-size: .92rem; }}
th, td {{ border-bottom: 1px solid #e3e8ee; padding: .5rem; text-align: left; vertical-align: top; }}
th {{ background: #eef3f8; }}
.note {{ color: #566573; font-size: .9rem; }}
</style>
</head>
<body>
<div class="meta">
<h1>Lokaler Diagnosebericht</h1>
<p>Version {esc(report['application_version'])} · Zeitraum {esc(report['report_period']['from'])}
bis {esc(report['report_period']['to'])} · erstellt {esc(report['created_at'])}</p>
<p class="note">Dateinamen: {"enthalten" if privacy['filenames_included'] else "nicht enthalten"}.
Vollständige Pfade, Rohlogs, PDFs und OCR-Volltexte sind nicht Bestandteil des Berichts.</p>
</div>
<div class="cards">
<div class="card">Vorgänge<div class="value">{results['total']}</div></div>
<div class="card">Erfolgreich<div class="value">{results['successful']}</div></div>
<div class="card">Erkennungsquote<div class="value">{esc(_format_percent(results['recognition_rate']))}</div></div>
<div class="card">Ø Laufzeit<div class="value">{esc(_format_number(durations['average'], 2))} s</div></div>
<div class="card">95. Perzentil<div class="value">{esc(_format_number(durations['p95'], 2))} s</div></div>
<div class="card">Max. Warteschlange<div class="value">{esc(_format_number(summary['queue']['maximum_waiting_pdfs'], 0))}</div></div>
<div class="card">Unklassifizierte Fehler<div class="value">{log_health['unclassified_error_or_critical_lines']}</div></div>
</div>
<section><h2>Tagesverlauf</h2><table><thead><tr><th>Tag</th><th>Gesamt</th><th>Erfolgreich</th>
<th>Nicht erkannt</th><th>Technische Fehler</th><th>Erkennungsquote</th></tr></thead><tbody>{day_rows}</tbody></table></section>
<section><h2>Versionen</h2><table><thead><tr><th>Version</th><th>Gesamt</th><th>Erfolgreich</th>
<th>Nicht erkannt</th><th>Erkennungsquote</th></tr></thead><tbody>{version_rows}</tbody></table></section>
<section><h2>Grundcodes</h2><table><thead><tr><th>Grundcode</th><th>Anzahl</th></tr></thead><tbody>{reason_rows}</tbody></table></section>
<section><h2>Dokumenttypen</h2><table><thead><tr><th>Typ</th><th>Anzahl</th></tr></thead><tbody>{type_rows}</tbody></table></section>
<section><h2>Phasenlaufzeiten</h2><table><thead><tr><th>Phase</th><th>Messungen</th><th>Durchschnitt (s)</th>
<th>Median (s)</th><th>95. Perzentil (s)</th><th>Maximum (s)</th></tr></thead><tbody>{phase_rows}</tbody></table></section>
<section><h2>Problemfälle</h2><table><thead><tr><th>Zeitpunkt</th><th>Status</th><th>Grundcode</th>
<th>Stufe</th><th>Fehlerseite</th><th>Seiten gesamt</th><th>Dauer (s)</th><th>Größe (Bytes)</th>{filename_header}</tr></thead>
<tbody>{problem_rows}</tbody></table></section>
<section><h2>Langsamste Vorgänge</h2><table><thead><tr><th>Zeitpunkt</th><th>Status</th><th>Gesamt (s)</th>
<th>Archiv (s)</th><th>Erkennung (s)</th><th>Ausgabe (s)</th>{filename_header}</tr></thead><tbody>{slowest_rows}</tbody></table></section>
<section><h2>Anwendungslebenszyklus</h2>
<p>Starts: {lifecycle['starts']} · kontrollierte Stopps: {lifecycle['controlled_stops']} ·
Sitzungen ohne Stop-Ereignis: {lifecycle['sessions_without_stop']}.</p>
<p class="note">Eine Sitzung ohne Stop-Ereignis kann noch aktiv sein oder unerwartet beendet worden sein.</p>
<table><thead><tr><th>Stoppgrund</th><th>Anzahl</th></tr></thead><tbody>{shutdown_reason_rows}</tbody></table></section>
<section><h2>Logqualität</h2>
<p>Warnungen: {log_health['warning_lines']} · Fehler: {log_health['error_lines']} ·
kritische Fehler: {log_health['critical_lines']} · unklassifizierte Warnungen:
{log_health['unclassified_warning_lines']} · unklassifizierte Fehler:
{log_health['unclassified_error_or_critical_lines']}.</p>
<p>{report['parser_statistics']['log_files_read']} Protokolldateien,
{report['parser_statistics']['lines_read']} Zeilen, {report['parser_statistics']['recognized_events']} erkannte Ereignisse,
{report['parser_statistics']['ignored_lines']} sonstige Zeilen und
{report['parser_statistics']['malformed_lines']} unvollständige Zeilen.</p>
<table><thead><tr><th>Unbekanntes Feld</th><th>Vorkommen</th></tr></thead><tbody>{unknown_field_rows}</tbody></table></section>
</body>
</html>
"""


def export_diagnostic_report(
    log_directory: str | Path,
    destination: str | Path,
    *,
    days: int = DEFAULT_REPORT_DAYS,
    include_filenames: bool = False,
    end_date: date | None = None,
    created_at: datetime | None = None,
) -> Path:
    """Erstellt das Diagnose-ZIP temporaer und veroeffentlicht es atomar."""

    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    report = build_diagnostic_report(
        log_directory,
        days=days,
        include_filenames=include_filenames,
        end_date=end_date,
        created_at=created_at,
    )
    json_content = json.dumps(
        report, ensure_ascii=False, indent=2, sort_keys=False
    ).encode("utf-8")
    html_content = render_diagnostic_html(report).encode("utf-8")

    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b",
            prefix=f".{destination_path.name}.",
            suffix=".tmp",
            dir=destination_path.parent,
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            with zipfile.ZipFile(
                temporary, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.writestr("diagnosebericht.html", html_content)
                archive.writestr("diagnosebericht.json", json_content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination_path)
        temporary_path = None
        return destination_path
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
