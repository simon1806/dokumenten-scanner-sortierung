# Dokumenten-Scanner-Sortierung

Windows-Anwendung zur automatischen Verarbeitung von Stapel-Scans. Sie überwacht einen konfigurierbaren Eingangsordner, trennt erkannte Dokumente, benennt sie und legt sie im Zielordner ab.

## Verarbeitungsablauf

```text
Scanner-PDF im Eingangsordner
        |
        v
Barcode- und OCR-Erkennung je Seite
        |
        +-- erkannt --> einzelne, benannte PDFs im Zielordner
        |                Originalscan im Archiv
        |
        +-- nicht erkannt --> Originalscan unverändert im Zielordner
                              zusätzliche Kopie im Prüfordner
                              Originalscan im Archiv
```

Archivdateien werden nach der in den Einstellungen festgelegten Aufbewahrungsdauer gelöscht. Standard: **30 Tage**.

## Unterstützte Dokumente

| Dokumenttyp | Erkennungsmerkmale | Dateiname |
| --- | --- | --- |
| Aufmaßblatt | Barcode oder `AUFMASSBLATT` | `AM_<Dokumentnummer>.pdf` |
| Empfangsschein | Barcode oder `Empfangsschein-Nr.` | `EM_<Empfangsschein-Nr.>.pdf` |
| Montagebericht | `Montagebericht` und Auftragsnummer | `MI_<Auftragsnummer>.pdf` |
| Nowak-Lieferschein | `NOWAK GLAS` und Lieferscheinnummer | `LS-Nowak-<Lieferscheinnummer>.pdf` |
| Heitzer-Lieferschein | `Heitzer AG` und Lieferscheinnummer | `LS-Heitzer-<Lieferscheinnummer>.pdf` |
| Pauli-Lieferschein | `Pauli + Sohn` und Nummer/Datum | `LS-Pauli-<Lieferscheinnummer>.pdf` |

Mehrseitige Dokumente bleiben zusammen. Beispiel: Die Seiten `1 von 2` und `2 von 2` des Heitzer-Lieferscheins `26060887` ergeben `LS-Heitzer-26060887.pdf`.

## Einstellungen

Die Oberfläche verwaltet diese Werte:

- Eingangsordner auf dem Server
- Zielordner für verarbeitete Dateien
- Archivordner
- Prüfordner für nicht erkannte Scans
- Archiv-Aufbewahrung in Tagen (Standard: 30)
- Dateistabilität nach einem Scan (Standard: 2 Sekunden)
- optionaler Pfad zu Tesseract OCR, falls Tesseract nicht mitgeliefert oder systemweit installiert ist

Die Stabilitätszeit verhindert, dass eine PDF verarbeitet wird, während der Scanner oder das Netzwerk sie noch schreibt. Zusätzlich prüft die Anwendung, ob die PDF-Struktur vollständig lesbar ist. Das Eingangsverzeichnis wird jede Sekunde geprüft, sodass fertige Scans gewöhnlich nach wenigen Sekunden starten.

Für eine schnellere Erkennung werden vorhandener PDF-Text und Barcodes vor der OCR ausgewertet. Bei mehrseitigen Scans werden höchstens zwei Seiten gleichzeitig per OCR verarbeitet, damit die Laufzeit sinkt, ohne den Server unnötig auszulasten.

Eingangs-, Ziel-, Archiv- und Prüfordner müssen unterschiedlich sein. Bleibt der Prüfordner leer, wird `Nicht_erkannt` im Zielordner verwendet. Gleichnamige Dateien werden nicht überschrieben, sondern mit einer laufenden Nummer abgelegt.

## Installation auf Windows Server 2025

Voraussetzungen fuer die Entwicklung: Python 3.11 oder neuer sowie Tesseract OCR mit deutschem Sprachpaket (`deu`). Bei der Setup-EXE kann Tesseract mitgeliefert werden.

```powershell
git clone https://github.com/simon1806/dokumenten-scanner-sortierung.git
cd dokumenten-scanner-sortierung
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\dokumentensortierer.exe
```

Beim ersten Start werden die Ordner und die Aufbewahrungsdauer in der Oberfläche eingetragen. Die Einstellungen liegen standardmäßig unter:

```text
%APPDATA%\DokumentenScannerSortierung\settings.json
```

Falls Tesseract nicht mit der EXE mitgeliefert und nicht im Windows-Pfad hinterlegt ist, wird seine `tesseract.exe` in den Einstellungen eingetragen.

## EXE für Test und Updates

Das Release wird als zwei EXE-Dateien erzeugt:

- `DokumentenScannerSortierung-<Version>.exe` ist die portable Anwendung.
- `DokumentenScannerSortierung-Setup.exe` installiert die Anwendung unter `%LOCALAPPDATA%\Programs\DokumentenScannerSortierung` und startet sie anschließend.

Für ein Update wird die neue `DokumentenScannerSortierung-Setup.exe` gestartet, nachdem die Anwendung geschlossen wurde. Einstellungen und Archivdateien bleiben erhalten, weil sie getrennt von der installierten EXE gespeichert werden.

Zum Erzeugen der Dateien im Entwicklungsordner:

```powershell
.\scripts\build-release.ps1 -Version 0.1.6
```

Die Dateien liegen danach im Ordner `release`.

Soll Tesseract direkt in die Anwendung eingebettet werden, wird der installierte Tesseract-Ordner angegeben. Der Ordner muss `tesseract.exe` und `tessdata` enthalten:

```powershell
.\scripts\build-release.ps1 -Version 0.1.6 -TesseractDir "C:\Program Files\Tesseract-OCR"
```

Alternativ kann der Ordner als `vendor\Tesseract-OCR` ins Projekt gelegt werden; dann wird er automatisch mitgenommen.

Zum Vorbereiten dieses Ordners kann das Hilfsskript verwendet werden:

```powershell
.\scripts\prepare-tesseract-vendor.ps1
.\scripts\build-release.ps1 -Version 0.1.6
```

Hinweis: Das offizielle Tesseract-Release auf GitHub enthaelt fuer Version 5.5.2 den Quellcode. Fuer eine Windows-EXE wird ein fertig gebauter Windows-Ordner mit `tesseract.exe`, DLLs und `tessdata` benoetigt.

## Automatischer Betrieb

Für den Serverbetrieb wird in der Windows-Aufgabenplanung eine Aufgabe mit dem Auslöser **Beim Starten des Computers** eingerichtet. Als Programm wird verwendet:

```text
C:\Pfad\zum\Projekt\.venv\Scripts\dokumentensortierer.exe
```

Argumente:

```text
--run --settings "C:\ProgramData\DokumentenScannerSortierung\settings.json"
```

Das verwendete Dienstkonto benötigt Änderungsrechte für Eingangs-, Ziel-, Archiv- und Prüfordner. Die Anwendung zeigt die letzten Aktivitäten direkt im Fenster und schreibt ein dauerhaftes Protokoll im Unterordner `logs` neben der verwendeten `settings.json`. Die Logdatei wird bei 5 MB rotiert; fünf ältere Dateien werden aufbewahrt.

## Entwicklung und Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
