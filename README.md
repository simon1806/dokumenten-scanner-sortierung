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

Archivdateien werden nach der in den Einstellungen festgelegten Aufbewahrungsdauer gelöscht. Die Frist beginnt mit der Archivierung, unabhängig vom ursprünglichen Datei-Zeitstempel. Standard: **30 Tage**.

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
- Wartezeit für unvollständige oder beschädigte PDFs (Standard: 60 Sekunden)
- optionaler Pfad zu Tesseract OCR, falls Tesseract nicht mitgeliefert oder systemweit installiert ist

Die Oberfläche startet auf üblichen Full-HD-Bildschirmen in einer großzügigen, zentrierten Ansicht und passt sich auf kleineren Bildschirmen automatisch an. Sie verwendet eine übersichtliche Kartenansicht mit klar getrennten Haupt-, Neben- und Beenden-Aktionen. Wenn der Mauszeiger kurz über einem Button stehen bleibt, erklärt ein Hinweis dessen Funktion und Auswirkungen.

Die Schaltflächensymbole stammen aus dem freien, MIT-lizenzierten Paket [Tabler Icons](https://tabler.io/icons). Es werden nur die tatsächlich benötigten Symbole mitgeliefert; der vollständige Lizenztext steht in `THIRD_PARTY_NOTICES.md`.

Das eigene Dokumenten- und Scanner-Symbol wird durchgängig für Anwendung, Setup, Desktop-Verknüpfung, Fenstertitel, Kopfbereich und Windows-Infobereich verwendet. Das Setup installiert dafür zusätzlich eine eigene ICO-Datei und meldet Änderungen an die Windows-Oberfläche, damit veraltete Symbole nicht aus dem Explorer-Cache übernommen werden.

Die Stabilitätszeit verhindert, dass eine PDF verarbeitet wird, während der Scanner oder das Netzwerk sie noch schreibt. Zusätzlich prüft die Anwendung, ob die PDF-Struktur vollständig lesbar ist. Bleibt sie nach der konfigurierten Fehlerwartezeit unvollständig, wird das Original archiviert und unverändert in Ziel- und Prüfordner weitergeleitet. Das Eingangsverzeichnis wird jede Sekunde geprüft, sodass fertige Scans gewöhnlich nach wenigen Sekunden starten.

Für eine schnellere Erkennung werden vorhandener PDF-Text und Barcodes vor der OCR ausgewertet. Bei mehrseitigen Scans werden höchstens zwei Seiten gleichzeitig per OCR verarbeitet, damit die Laufzeit sinkt, ohne den Server unnötig auszulasten.

Eingangs-, Ziel-, Archiv- und Prüfordner müssen unterschiedlich sein. Bleibt der Prüfordner leer, wird `Nicht_erkannt` im Zielordner verwendet. Gleichnamige Dateien werden nicht überschrieben, sondern mit einer laufenden Nummer abgelegt. Das Ausführungskonto benötigt Löschrechte im Eingangsordner. Kann eine Eingangsdatei nicht entfernt werden, entstehen keine Ausgabedokumente und die vorläufige Archivkopie wird zurückgerollt.

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
- `DokumentenScannerSortierung-Setup.exe` fragt vor jeder Änderung nach. Bei einer Erstinstallation steht **Installation ausführen**, bei einer vorhandenen Version **Update ausführen** zur Auswahl; **Abbrechen** beendet das Setup ohne Änderungen. Anschließend installiert das Setup die Anwendung unter `%LOCALAPPDATA%\Programs\DokumentenScannerSortierung`, erstellt eine Desktop-Verknüpfung und startet sie.

Für ein Update wird die neue `DokumentenScannerSortierung-Setup.exe` gestartet, nachdem die Anwendung geschlossen wurde. Einstellungen und Archivdateien bleiben erhalten, weil sie getrennt von der installierten EXE gespeichert werden.

Die laufende Anwendung zeigt ein Symbol im Windows-Infobereich unten rechts. Das Schließen des Fensters blendet es nur aus; die Überwachung läuft weiter. Über das Symbol können das Fenster geöffnet, die Überwachung gestartet oder beendet und die Anwendung vollständig beendet werden. Windows kann das Symbol zunächst hinter dem Pfeil für ausgeblendete Symbole anzeigen.

Pro Einstellungsdatei kann nur eine Programminstanz laufen. Ein erneuter Start über die Desktop-Verknüpfung oder eine portable EXE erzeugt deshalb keine zusätzlichen Überwachungen oder Infobereich-Symbole. Oben rechts neben dem Betriebsstatus öffnet die Schaltfläche **Info** eine Übersicht mit den tatsächlich verwendeten Versionen der Anwendung, Tesseract OCR, Leptonica, Python/Tk und der eingebundenen Bibliotheken. Im Fenstertitel wird bewusst keine Versionsnummer mehr angezeigt.

Zum Erzeugen der Dateien im Entwicklungsordner:

```powershell
.\scripts\build-release.ps1 -Version 0.1.16
```

Die Dateien liegen danach im Ordner `release`.

Soll Tesseract direkt in die Anwendung eingebettet werden, wird der installierte Tesseract-Ordner angegeben. Der Ordner muss `tesseract.exe` und `tessdata` enthalten:

```powershell
.\scripts\build-release.ps1 -Version 0.1.16 -TesseractDir "C:\Program Files\Tesseract-OCR"
```

Alternativ kann der Ordner als `vendor\Tesseract-OCR` ins Projekt gelegt werden; dann wird er automatisch mitgenommen.

Zum Vorbereiten dieses Ordners kann das Hilfsskript verwendet werden:

```powershell
.\scripts\prepare-tesseract-vendor.ps1
.\scripts\build-release.ps1 -Version 0.1.16
```

Der Release 0.1.16 liefert Tesseract `5.5.0.20241111` mit Leptonica `1.85.0` sowie den Sprachmodellen `deu`, `eng` und `osd` direkt in der Anwendung mit. Grundlage ist der offizielle Windows-x64-Installer des Tesseract-Releases 5.5.0. Seine SHA-256-Pruefsumme lautet `F3FC4236425B690C8BE756F35793F77394EE004BE0A6460A440C754D892F68BC`.

Hinweis: Das offizielle Tesseract-Release 5.5.2 stellt auf GitHub nur Quellcodearchive bereit. Fuer die Windows-EXE wird deshalb der offizielle fertige Windows-Build 5.5.0 verwendet.

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
