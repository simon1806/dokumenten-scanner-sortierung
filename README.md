# Dokumenten-Scanner-Sortierung

Windows-Anwendung zur automatischen Verarbeitung von Stapel-Scans. Sie überwacht einen konfigurierbaren Eingangsordner, erkennt Dokumentgrenzen, benennt die getrennten PDFs und legt sie sicher im Zielordner ab.

## Verarbeitungsablauf

```text
Scanner-PDF im Eingangsordner
        |
        v
Original dauerhaft archivieren und Vorgang protokollieren
        |
        v
Barcode-, Text- und OCR-Erkennung je Seite
        |
        +-- erkannt ------> einzelne, benannte PDFs im Zielordner
        |
        +-- nicht erkannt -> Original unverändert im Zielordner
                             zusätzliche Kopie im Prüfordner
```

Mehrseitige Dokumente bleiben zusammen. Werden in einem Scan mehrere Dokumentanfänge erkannt, erzeugt die Anwendung entsprechend mehrere PDFs.

## Unterstützte Dokumente

| Dokumenttyp | Erkennungsmerkmale | Dateiname |
| --- | --- | --- |
| Aufmaßschein/-blatt | Barcode oder Dokumentüberschrift | `AM_<Dokumentnummer>.pdf` |
| Empfangsschein | Barcode oder `Empfangsschein-Nr.` | `EM_<Empfangsschein-Nr.>.pdf` |
| Neuma-Empfangsschein | `NEUMA` und Neuma-Auftragsnummer | `EM-NEUMA-I-<Jahr>-<Nummer>.pdf` |
| Montageinfo/-bericht | Überschrift und Auftragsnummer | `MI_<Auftragsnummer>.pdf` |
| Abtretungserklärung | `Abtretungserklärung bei Versicherungsschäden`, Nummer im Feld `Auftrag/Angebot` (Präfix `32` oder `52`) | `ABTRET_<Auftrag>.pdf` |
| Nowak-Lieferschein | Nowak-Kopf und vollständige Lieferscheinnummer ohne festen Nummernpräfix | `LS-Nowak-<Lieferscheinnummer>.pdf` |
| Heitzer-Lieferschein | `Heitzer AG` und Lieferscheinnummer | `LS-Heitzer-<Lieferscheinnummer>.pdf` |
| Pauli-Lieferschein | `Pauli + Sohn` und Nummer/Datum | `LS-Pauli-<Lieferscheinnummer>.pdf` |

Vorhandener PDF-Text und Barcodes werden vor der langsameren OCR ausgewertet. Bei Nowak wird gezielt der kleine Bereich oben rechts neben dem Barcode gelesen; dadurch entfällt normalerweise die Ganzseiten-OCR. Falls weitere OCR nötig ist, wird zuerst nur der allgemeine Kopfbereich geprüft. Bei mehrseitigen Scans arbeiten höchstens zwei OCR-Prozesse gleichzeitig.

Enthält die Kopferkennung keinen Hinweis auf einen unterstützten Dokumenttyp, wird die zeitaufwendige Ganzseiten-OCR übersprungen. Das Original wird dann unverändert in Ziel- und Prüfordner weitergeleitet. Zeigt der Kopf dagegen einen bekannten Dokumenttyp, aber noch keine lesbare Nummer, bleibt die Ganzseiten-OCR aktiv. So werden unbekannte Dokumente zügig weitergeleitet, ohne schwer lesbare bekannte Dokumente vorschnell auszuschließen.

## Datensicherheit und Wiederanlauf

Ab Version 0.1.24 wird jeder Scan als persistenter Vorgang verarbeitet:

1. Das unveränderte Original wird in einen datierten, von der Anwendung markierten Archivordner kopiert und mit SHA-256 geprüft.
2. Erst wenn Archivkopie und Vorgangsdatei dauerhaft geschrieben sind, wird die Eingangsdatei nach erfolgreicher Ausgabe in den privaten Vorgangsordner übernommen und entfernt. Während der Erkennung bleibt sie im Eingangsordner unverändert; es gibt keine Zwischen-Umbenennung im Eingangsordner.
3. Alle Teildokumente werden zunächst im eigenen Vorgangsordner erzeugt und geprüft.
4. Zielseitige Dateien werden ohne Überschreiben vorhandener Dateien veröffentlicht. Bei Namenskonflikten wird eine laufende Nummer ergänzt.
5. Unterbrochene Vorgänge unter `Archiv\.dokumentensortierer\pending` werden beim Start und anschließend regelmäßig automatisch fortgesetzt.

Ein Ziel- oder Netzwerkfehler führt deshalb nicht zu einem unvollständigen Dokumentstapel oder zum Verlust des Originals. Beim kontrollierten Beenden wird ein bereits gestarteter Vorgang fertiggestellt; danach beginnt kein weiterer Scan. Eine serverweite Sperre verhindert, dass derselbe Eingangsordner gleichzeitig von mehreren Sitzungen überwacht wird.

Die Archivbereinigung löscht ausschließlich direkt abgelegte PDFs mit gültigem Eigentums- und Prüfsummennachweis. Dateien offener Vorgänge sowie unbekannte, verschachtelte oder manuell hinzugefügte Dateien bleiben unangetastet. Archive aus Versionen vor 0.1.24 besitzen diesen Nachweis noch nicht und werden absichtlich **nicht automatisch gelöscht**. Diese Altbestände müssen nach einer manuellen Prüfung separat bereinigt werden.

## Einstellungen

Die Oberfläche verwaltet:

- Eingangsordner für neue Scanner-PDFs
- Zielordner für erkannte oder unverändert weitergeleitete Dokumente
- Archivordner für Originalscans und offene Vorgänge
- Prüfordner für nicht erkannte oder beschädigte Scans
- Archiv-Aufbewahrung in Tagen, Standard 30
- Dateistabilität nach der letzten Änderung, Standard 2 Sekunden
- Wartezeit für unvollständige PDFs, Standard 60 Sekunden
- Stapelgrenze und Stapelpause für einen kontrollierten Wiederanlauf nach Rückstau, Standard 3 PDFs und 10 Sekunden
- OCR-Gesamtlimit pro Scan, Standard 90 Sekunden
- optionaler eigener Pfad zu `tesseract.exe`

Eingangs-, Ziel-, Archiv- und Prüfordner müssen getrennt sein und dürfen nicht gefährlich ineinander liegen. Bleibt der Prüfordner leer, wird `Nicht_erkannt` im Zielordner verwendet. Das Ausführungskonto benötigt Änderungsrechte in allen vier Ordnern.

Die Stabilitätszeit verhindert, dass die Anwendung eine PDF öffnet, während Scanner oder Netzwerk sie noch schreiben. Zusätzlich wird die PDF-Struktur geprüft. Bleibt sie nach der Fehlerwartezeit unvollständig, wird sie unverändert in Ziel- und Prüfordner weitergeleitet.

Schutzgrenzen für den unbeaufsichtigten Betrieb:

- maximal 500 MiB pro PDF
- maximal 250 Seiten pro PDF
- maximal 50 Millionen gerenderte Pixel pro Seite
- maximal 60 Sekunden pro Tesseract-Aufruf
- maximal 90 Sekunden OCR-Gesamtzeit pro Scan

Liegt mehr als eine kleine Anzahl von Scans im Eingang, arbeitet die Überwachung zusätzlich bewusst gedrosselt: Ab vier wartenden PDFs wird zwischen zwei Vorgängen standardmäßig zehn Sekunden pausiert. So bleibt der Betrieb kontrolliert, auch wenn nach einem Ausfall viele Scans gleichzeitig eintreffen. Stapelgrenze, Stapelpause und OCR-Gesamtlimit lassen sich in der Oberfläche unter **Verarbeitung** einstellen.

Bei Überschreitung bleibt das Original erhalten und wird als nicht verarbeitet zur Prüfung weitergeleitet. Abgebrochene OCR-Aufträge einer langen PDF werden nicht unnötig weiter ausgeführt.

Die Einstellungen liegen standardmäßig unter:

```text
%APPDATA%\DokumentenScannerSortierung\settings.json
```

Die Datei wird atomar gespeichert. Eine beschädigte oder falsch typisierte Einstellungsdatei erzeugt eine verständliche Fehlermeldung und wird nicht stillschweigend überschrieben.

## Autostart

Das Setup legt eine Verknüpfung im Windows-Startordner des installierenden Benutzerkontos an. Nach dessen Windows-Anmeldung startet die Anwendung die Überwachung mit den gespeicherten Einstellungen und wird in den Windows-Infobereich ausgeblendet. Sind die Einstellungen unvollständig oder ungültig, bleibt das Fenster zur Korrektur geöffnet. Die Deinstallation entfernt auch diese Autostart-Verknüpfung.

## Protokolle

Für jeden Kalendertag entsteht unter `%APPDATA%\DokumentenScannerSortierung\logs` eine eigene UTF-8-Datei, zum Beispiel:

```text
dokumentensortierer-2026-07-15.log
```

Tagesprotokolle werden 90 Tage aufbewahrt. Andere Dateien im Protokollordner werden von der Bereinigung nicht gelöscht.

Jeder Vorgang erhält eine ID. Protokolliert werden unter anderem Ergebnisstatus, Dateiname und Größe, Seiten- und Dokumentanzahl, erkannte Typen, Ausgabedateien sowie Zeiten für Archivierung, Erkennung, Ausgabe und Gesamtverarbeitung. Startmeldungen enthalten Anwendung, Python, Tesseract, Leptonica, System, Prozess-ID und die wesentlichen Betriebseinstellungen. Vollständiger OCR-Text wird bewusst nicht gespeichert.

## Installation und Update

Der freigegebene Build liegt versionsbezogen unter `release\<Version>`:

- `DokumentenScannerSortierung-<Version>.exe`: portable Anwendung
- `DokumentenScannerSortierung-Setup.exe`: Installation, Update und Reparatur
- `SHA256SUMS.txt`: SHA-256-Prüfsummen
- `RELEASE-MANIFEST.json`: Build-, Versions- und Komponenteninformationen
- `THIRD_PARTY_NOTICES.md`: Hinweise und Lizenzen externer Komponenten
- `README.md`: Betriebs-, Installations- und Wiederanlaufanleitung
- `CHANGELOG.md`: Änderungen und Migrationshinweise der Version

Für die Installation auf dem Server wird nur `DokumentenScannerSortierung-Setup.exe` benötigt. Das Setup prüft seine eingebetteten Dateien vor jeder Änderung und installiert pro Windows-Benutzer nach:

```text
%LOCALAPPDATA%\Programs\DokumentenScannerSortierung
```

Es erstellt eine Desktop- und Autostart-Verknüpfung und registriert die Anwendung unter **Windows-Einstellungen > Apps > Installierte Apps** mit dem Herausgeber `Simon Hagen – Glas Hagen` und dem Kontakt `simon.hagen@glashagen.de`.

Vorhandene Versionen werden als Update oder Reparatur erkannt. Ein unbeabsichtigtes Downgrade und eine Installation über eine unbekannte/defekte Versionslage werden standardmäßig blockiert. Programmdateien, Registry-Eintrag und Desktop-Verknüpfung werden transaktional ausgetauscht; bei Fehlern wird die alte Installation wiederhergestellt. Einstellungen, Protokolle und Dokumentordner werden weder bei Updates noch bei der Deinstallation gelöscht.

Die administrativen Schalter `--allow-downgrade` und `--allow-unknown-version` heben die jeweilige Sperre bewusst auf und sollten nur nach Sicherung und Prüfung der vorhandenen Installation verwendet werden. Mit `--self-test` prüft das Setup ausschließlich Version, Manifest und eingebettete Nutzdaten; es verändert dabei weder Installation noch Einstellungen.

Die Anwendung muss vor einem Update vollständig beendet sein. Die Abschlussmaske bietet die standardmäßig aktivierte Option **Anwendung starten**.

## Server-Pilot und Freigaben

Version 0.2.0 bündelt die im Server-Pilot geprüften Dokumentregeln und den Autostart für die produktive Inbetriebnahme. Vor dem Update werden mit den tatsächlichen Serverpfaden nochmals mindestens je ein Aufmaßschein, eigener Empfangsschein, Neuma-Empfangsschein, Montageinfo, Nowak-Lieferschein, Abtretungserklärung und nicht erkennbarer Scan verarbeitet. Dabei werden Ziel-, Archiv-, Prüf- und Protokollordner sowie ein Wiederanlauf geprüft.

## Mitgelieferte OCR-Komponenten

Release 0.2.0 enthält:

- Tesseract OCR 5.5.2
- Leptonica 1.87.0
- Sprachmodelle `deu`, `eng` und `osd`

Die Versionen werden beim Build geprüft und im Infofenster sowie im Release-Manifest ausgewiesen. Weitere verwendete Bibliotheken und ihre Lizenzhinweise stehen in `THIRD_PARTY_NOTICES.md`. Insbesondere PyMuPDF ist dual unter AGPL und kommerzieller Lizenz verfügbar; der Betreiber muss vor einer Weitergabe oder Bereitstellung die passende Lizenzgrundlage festlegen.

## Automatischer Betrieb auf Windows Server 2025

Zuerst wird das Setup mit genau dem Windows-Konto ausgeführt, unter dem die spätere Aufgabe laufen soll. Anschließend kann die Oberfläche mit einer zentralen Einstellungsdatei geöffnet werden:

```powershell
& "$env:LOCALAPPDATA\Programs\DokumentenScannerSortierung\DokumentenScannerSortierung.exe" `
  --settings "C:\ProgramData\DokumentenScannerSortierung\settings.json"
```

Für den produktiven Serverbetrieb muss die Startaufgabe nicht erst auf eine Benutzeranmeldung warten. Sie wird einmalig in der Windows-Aufgabenplanung durch einen Serveradministrator angelegt. Die Einstellungsdatei muss dazu an einem zentralen Ort liegen, beispielsweise:

```powershell
New-Item -ItemType Directory -Force "C:\ProgramData\DokumentenScannerSortierung"
Copy-Item "$env:APPDATA\DokumentenScannerSortierung\settings.json" `
  "C:\ProgramData\DokumentenScannerSortierung\settings.json"
notepad "C:\ProgramData\DokumentenScannerSortierung\settings.json"
```

Vor der Einrichtung müssen alle Netzlaufwerke in dieser Datei durch UNC-Pfade ersetzt sein, zum Beispiel `\\srv-gh-app\pool\Dateiarchiv` statt `G:\Dateiarchiv`. Benutzerabhängige Laufwerksbuchstaben stehen einem Systemkonto nach einem Serverneustart nicht zur Verfügung.

Bei einer manuellen Einrichtung in der Windows-Aufgabenplanung gelten dieselben Werte:

- Auslöser: **Beim Starten des Computers**
- Ausführen unabhängig von der Benutzeranmeldung
- Programm: installierte `DokumentenScannerSortierung.exe`
- Argumente: `--run --settings "C:\ProgramData\DokumentenScannerSortierung\settings.json"`
- Bei bereits laufender Aufgabe: **Keine neue Instanz starten**
- Bei Fehlern: Neustart nach einer kurzen Wartezeit aktivieren

Für Netzwerkfreigaben sind UNC-Pfade wie `\\server\freigabe\scanner\eingang` robuster als benutzerabhängige Laufwerksbuchstaben. Das Dienstkonto benötigt Lesen/Ändern/Löschen im Eingang sowie Lesen/Schreiben/Ändern in Ziel, Archiv, Prüfordner und am Ordner der zentralen `settings.json`.

Bei einem vorübergehenden Ausfall eines Serverpfads wartet die Anwendung mit exponentiellem Backoff zwischen 1 und 60 Sekunden und setzt die Überwachung nach der Wiederkehr automatisch fort.

## Sicherheitsgrenzen

PDFs werden als nicht vertrauenswürdige Eingaben behandelt. Dateigröße, Seitenzahl, gerenderte Pixelzahl und OCR-Laufzeit sind begrenzt; unerwartete Dateien werden nicht überschrieben. Tesseract erhält ausschließlich lokal gerenderte Bilddateien und keine URLs. Die mit dem Windows-OCR-Paket transitiv gelieferten Netzwerkbibliotheken werden vom Sortierer nicht für Netzwerkzugriffe verwendet; auf dem Server sollte ausgehender Netzwerkverkehr der Anwendung dennoch nach dem Prinzip der geringsten Rechte gesperrt werden.

SHA-256-Prüfsummen erkennen beschädigte Release-Dateien, ersetzen aber keine Herausgebersignatur. Ohne ein bereitgestelltes Authenticode-Zertifikat bleiben Anwendung und Setup als `signed: false` gekennzeichnet. Vor dem produktiven Einsatz ist außerdem die in `THIRD_PARTY_NOTICES.md` beschriebene AGPL- oder kommerzielle Lizenzgrundlage für PyMuPDF verbindlich festzulegen.

## Entwicklung, Tests und Release-Build

Voraussetzung ist Python 3.12 auf Windows x64. Die Build-Abhängigkeiten sind in `constraints-build.txt` exakt fixiert; `requirements-build.lock` bindet zusätzlich die geprüften Windows-Wheels an SHA-256-Hashes.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --require-hashes --only-binary=:all: --requirement requirements-build.lock
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation --editable .
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\ruff.exe check src installer tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

OCR-Paket vorbereiten und Release 0.2.0 bauen:

```powershell
.\scripts\prepare-tesseract-vendor.ps1
.\scripts\build-release.ps1 -Version 0.2.0
```

Der Build bricht bei Tests, Versionsabweichungen, fehlenden Sprachmodellen, falscher Tesseract-/Leptonica-Version, inkonsistenten Python-Paketen oder fehlenden Artefakten ab. Alte Release-Ordner bleiben erhalten. Optional können Anwendung und Setup mit einem vorhandenen Authenticode-Zertifikat signiert werden; ohne Zertifikat weist das Release-Manifest `signed: false` aus.

Ein bewusst nicht reproduzierbarer Entwicklungs-Build aus einem geänderten Arbeitsverzeichnis ist mit `-AllowDirtySource` möglich. Ein bereits vorhandener versionsbezogener Release-Ordner wird nur mit `-ForceRebuild` ersetzt. Anwendung und Setup unterstützen `--self-test`; der Release-Build führt beide Selbsttests zeitlich begrenzt aus, bevor er die Artefakte veröffentlicht.
