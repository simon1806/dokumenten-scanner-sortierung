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

Mehrseitige Dokumente bleiben zusammen. Beispiel: Die Seiten `1 von 2` und `2 von 2` des Heitzer-Lieferscheins `26060887` ergeben `LS-Heitzer-26060887.pdf`.

## Einstellungen

Die Oberfläche verwaltet diese Werte:

- Eingangsordner auf dem Server
- Zielordner für verarbeitete Dateien
- Archivordner
- Archiv-Aufbewahrung in Tagen (Standard: 30)
- Wartezeit nach einem Scan, bevor die Datei verarbeitet wird
- optionaler Pfad zu Tesseract OCR, falls Tesseract nicht mitgeliefert oder systemweit installiert ist

Eingangs-, Ziel- und Archivordner müssen unterschiedlich sein. Gleichnamige Dateien werden nicht überschrieben, sondern mit einer laufenden Nummer abgelegt.

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
.\scripts\build-release.ps1 -Version 0.1.4
```

Die Dateien liegen danach im Ordner `release`.

Soll Tesseract direkt in die Anwendung eingebettet werden, wird der installierte Tesseract-Ordner angegeben. Der Ordner muss `tesseract.exe` und `tessdata` enthalten:

```powershell
.\scripts\build-release.ps1 -Version 0.1.4 -TesseractDir "C:\Program Files\Tesseract-OCR"
```

Alternativ kann der Ordner als `vendor\Tesseract-OCR` ins Projekt gelegt werden; dann wird er automatisch mitgenommen.

## Automatischer Betrieb

Für den Serverbetrieb wird in der Windows-Aufgabenplanung eine Aufgabe mit dem Auslöser **Beim Starten des Computers** eingerichtet. Als Programm wird verwendet:

```text
C:\Pfad\zum\Projekt\.venv\Scripts\dokumentensortierer.exe
```

Argumente:

```text
--run --settings "C:\ProgramData\DokumentenScannerSortierung\settings.json"
```

Das verwendete Dienstkonto benötigt Änderungsrechte für Eingangs-, Ziel- und Archivordner. Die Anwendung schreibt ein Protokoll im Unterordner `logs` neben der verwendeten `settings.json`.

## Entwicklung und Tests

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -v
```
