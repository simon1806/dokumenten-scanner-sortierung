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


SCHEMA_VERSION = 1
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


def _parse_log_line(line: str) -> tuple[datetime, str] | None:
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
    return timestamp, match.group("message")


def _event_kind(message: str) -> str | None:
    for prefix, kind in (
        ("Vorgang abgeschlossen", "processing"),
        ("Vorgang fehlgeschlagen", "processing"),
        ("Vorgang zurückgestellt", "processing"),
        ("Betriebsstatus", "heartbeat"),
        ("Anwendung gestartet", "start"),
        ("Fehler bei der Ordnerüberwachung", "folder_error"),
        ("Ordnerfehler besteht fort", "folder_error"),
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

    parser = {
        "log_files_found": len(files),
        "log_files_read": 0,
        "log_files_unreadable": 0,
        "lines_read": 0,
        "parsed_log_lines": 0,
        "recognized_events": 0,
        "ignored_lines": 0,
        "malformed_lines": 0,
        "legacy_records": 0,
        "unknown_fields_ignored": 0,
    }
    processing_events: list[dict[str, Any]] = []
    queue_values: list[int] = []
    starts = 0
    folder_errors = 0
    folder_recoveries = 0
    active_version = "unbekannt"

    known_fields = {
        "status",
        "version",
        "grundcode",
        "stufe",
        "seite",
        "gesamt_s",
        "groesse_bytes",
        "typen",
        "datei",
        "wartende_pdfs",
    }

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
                timestamp, message = parsed
                kind = _event_kind(message)
                if kind is None:
                    parser["ignored_lines"] += 1
                    continue
                parser["recognized_events"] += 1
                fields = _parse_fields(message)
                parser["unknown_fields_ignored"] += sum(
                    1 for key in fields if key not in known_fields
                )

                if fields.get("version"):
                    active_version = fields["version"]
                if kind == "start":
                    starts += 1
                    continue
                if kind == "heartbeat":
                    queued = _safe_int(fields.get("wartende_pdfs"))
                    if queued is not None and queued >= 0:
                        queue_values.append(queued)
                    continue
                if kind == "folder_error":
                    folder_errors += 1
                    continue
                if kind == "folder_recovery":
                    folder_recoveries += 1
                    continue

                status = fields.get("status", "unbekannt")
                reason_code = fields.get("grundcode")
                if status == "nicht_erkannt" and not reason_code:
                    reason_code = LEGACY_REASON_CODE
                    parser["legacy_records"] += 1
                elif not reason_code:
                    reason_code = "nicht_angegeben"
                filename = Path(fields.get("datei", "")).name or None
                processing_events.append(
                    {
                        "timestamp": timestamp,
                        "status": status,
                        "version": fields.get("version") or active_version,
                        "reason_code": reason_code,
                        "stage": fields.get("stufe") or None,
                        "page": _safe_int(fields.get("seite")),
                        "duration_seconds": _safe_float(fields.get("gesamt_s")),
                        "size_bytes": _safe_int(fields.get("groesse_bytes")),
                        "document_types": _document_types(fields.get("typen")),
                        "filename": filename,
                    }
                )

    overall = _new_result_bucket()
    by_day: defaultdict[str, dict[str, int]] = defaultdict(_new_result_bucket)
    by_version: defaultdict[str, dict[str, int]] = defaultdict(_new_result_bucket)
    by_reason: Counter[str] = Counter()
    by_document_type: Counter[str] = Counter()
    durations: list[float] = []
    sizes: list[float] = []
    problem_cases: list[dict[str, Any]] = []

    for event in processing_events:
        status = event["status"]
        _update_bucket(overall, status)
        _update_bucket(by_day[event["timestamp"].date().isoformat()], status)
        _update_bucket(by_version[event["version"]], status)
        by_reason[event["reason_code"]] += 1
        for document_type in event["document_types"]:
            by_document_type[document_type] += 1
        if event["duration_seconds"] is not None:
            durations.append(event["duration_seconds"])
        if event["size_bytes"] is not None and event["size_bytes"] >= 0:
            sizes.append(float(event["size_bytes"]))
        if status != "erfolgreich":
            problem: dict[str, Any] = {
                "timestamp": event["timestamp"].isoformat(timespec="milliseconds"),
                "status": status,
                "reason_code": event["reason_code"],
                "stage": event["stage"],
                "page": event["page"],
                "duration_seconds": event["duration_seconds"],
                "size_bytes": event["size_bytes"],
            }
            if include_filenames:
                problem["filename"] = event["filename"]
            problem_cases.append(problem)

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
                "recoveries": folder_recoveries,
            },
            "application_starts": starts,
        },
        "grouped_by_day": all_days,
        "grouped_by_version": {
            key: _finish_bucket(value) for key, value in sorted(by_version.items())
        },
        "grouped_by_document_type": dict(sorted(by_document_type.items())),
        "grouped_by_reason_code": dict(sorted(by_reason.items())),
        "problem_cases": problem_cases,
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
            f"<td>{esc(case['reason_code'])}</td><td>{esc(case.get('stage') or '–')}</td>"
            f"<td>{esc(_format_number(case.get('page'), 0))}</td>"
            f"<td>{esc(_format_number(case.get('duration_seconds'), 2))}</td>"
            f"<td>{esc(_format_number(case.get('size_bytes'), 0))}</td>"
            f"{filename_cell}</tr>"
        )
    problem_rows = "".join(problem_rows_parts) or (
        f'<tr><td colspan="{8 if privacy["filenames_included"] else 7}">'
        "Keine Problemfälle vorhanden.</td></tr>"
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
</div>
<section><h2>Tagesverlauf</h2><table><thead><tr><th>Tag</th><th>Gesamt</th><th>Erfolgreich</th>
<th>Nicht erkannt</th><th>Technische Fehler</th><th>Erkennungsquote</th></tr></thead><tbody>{day_rows}</tbody></table></section>
<section><h2>Versionen</h2><table><thead><tr><th>Version</th><th>Gesamt</th><th>Erfolgreich</th>
<th>Nicht erkannt</th><th>Erkennungsquote</th></tr></thead><tbody>{version_rows}</tbody></table></section>
<section><h2>Grundcodes</h2><table><thead><tr><th>Grundcode</th><th>Anzahl</th></tr></thead><tbody>{reason_rows}</tbody></table></section>
<section><h2>Dokumenttypen</h2><table><thead><tr><th>Typ</th><th>Anzahl</th></tr></thead><tbody>{type_rows}</tbody></table></section>
<section><h2>Problemfälle</h2><table><thead><tr><th>Zeitpunkt</th><th>Status</th><th>Grundcode</th>
<th>Stufe</th><th>Seite</th><th>Dauer (s)</th><th>Größe (Bytes)</th>{filename_header}</tr></thead>
<tbody>{problem_rows}</tbody></table></section>
<section><h2>Parser</h2><p>{report['parser_statistics']['log_files_read']} Protokolldateien,
{report['parser_statistics']['lines_read']} Zeilen, {report['parser_statistics']['recognized_events']} erkannte Ereignisse,
{report['parser_statistics']['malformed_lines']} unvollständige oder unbekannte Zeilen.</p></section>
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
