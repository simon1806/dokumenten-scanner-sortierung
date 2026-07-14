# Dokumenten-Scanner-Sortierung

Dieses Tool verarbeitet Stapel-Scans aus einem Serverordner automatisch. Es trennt die enthaltenen Dokumente, liest Barcodes aus und benennt die erzeugten PDF-Dateien nach festgelegten Regeln.

## Ablauf

1. Ein Scanner legt eine PDF-Datei im Eingangsordner ab.
2. Das Tool wartet, bis die Datei vollständig geschrieben wurde.
3. Jede Seite wird auf Barcodes und bei Bedarf auf charakteristische Formularfelder geprüft.
4. Zusammengehörige Seiten werden zu einzelnen PDF-Dateien getrennt.
5. Die Dateien werden benannt und im Ausgabeordner abgelegt.
6. Nicht eindeutig erkennbare Dokumente werden in einen Prüf-Ordner verschoben.

## Benennungsregeln

| Dokumenttyp | Erkennung | Dateiname |
| --- | --- | --- |
| Aufmaßblatt | Barcode oder „AUFMASSBLATT“ | `AM_<Dokumentnummer>.pdf` |
| Empfangsschein | Barcode oder „Empfangsschein-Nr.“ | `EM_<Empfangsschein-Nr.>.pdf` |
| Montagebericht | „Montagebericht“ und Auftragsnummer | `MI_<Auftragsnummer>.pdf` |
| Nowak-Lieferschein | „NOWAK GLAS“ und Lieferscheinnummer | `LS-Nowak-<Lieferscheinnummer>.pdf` |

Beispiel: Ein Nowak-Lieferschein mit der Nummer `4783804` wird als `LS-Nowak-4783804.pdf` abgelegt. Mehrseitige Lieferscheine bleiben zusammen.

## Ordnerstruktur

```text
Eingang/       Neue Scanner-PDFs
Verarbeitet/   Erfolgreich getrennte und benannte Dokumente
Pruefen/       Nicht eindeutig erkennbare oder fehlerhafte Dateien
```

## Grundsätze

- Das Original wird nicht überschrieben.
- Barcodes haben Vorrang vor OCR-Erkennung.
- OCR dient als Rückfallebene für Dokumente ohne lesbaren Barcode.
- Gleichnamige Dateien werden nicht überschrieben, sondern zur Prüfung abgelegt.
