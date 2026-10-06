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
| Montageinfo/-bericht | Überschrift und Auftragsnummer; ohne Nummer mit Scannerdatum | `MI_<Auftragsnummer>.pdf` bzw. `MI_<JJJJ-MM-TT>.pdf` |
| Abtretungserklärung | `Abtretungserklärung bei Versicherungsschäden`, Nummer im Feld `Auftrag/Angebot` (Präfix `32` oder `52`) | `ABTRET_<Auftrag>.pdf` |
| Unterschriebenes Angebot | Glas-Hagen-Angebotsnummer und Bestätigungsbereich `Datum / Unterschrift` | `AG_<Angebotsnummer>_UNTERS.pdf` |
| Nowak-Lieferschein | Nowak-Kopf und vollständige Lieferscheinnummer ohne festen Nummernpräfix | `LS-Nowak-<Lieferscheinnummer>.pdf` |
| Heitzer-Lieferschein | `Heitzer AG` und Lieferscheinnummer | `LS-Heitzer-<Lieferscheinnummer>.pdf` |
| Pauli-Lieferschein | `Pauli + Sohn` und Nummer/Datum | `LS-Pauli-<Lieferscheinnummer>.pdf` |
| Bohle-Lieferschein | `Bohle AG` und Lieferscheinnummer im Kopfbereich | `LS-Bohle-<Lieferscheinnummer>.pdf` |

Vorhandener PDF-Text und Barcodes werden vor der langsameren OCR ausgewertet. Bei Nowak wird gezielt der kleine Bereich oben rechts neben dem Barcode gelesen; dadurch entfällt normalerweise die Ganzseiten-OCR. Bei Bohle werden Lieferantenkopf und Nummernfeld getrennt in zwei kleinen Ausschnitten gelesen. Bei einem Glas-Hagen-Angebot wird nach dem Kopf nur der Bestätigungs- und Unterschriftsbereich gelesen. Sobald eine unterschriebene Angebotsseite erkannt ist, bleiben alle Seiten des Angebots zusammen; dies gilt auch bei umgekehrter Scanreihenfolge. Falls weitere OCR nötig ist, wird zuerst nur der allgemeine Kopfbereich geprüft. Bei mehrseitigen Scans arbeiten höchstens zwei OCR-Prozesse gleichzeitig.

Aufmaß- und Glasbestellblätter von Pauli + Sohn mit einer `Set-Nr.` besitzen keine eigene Glas-Hagen-Dokumentnummer. Folgen sie auf einen erkannten Aufmaßschein, werden sie deshalb als Fortsetzungsseiten in dessen `AM_...pdf` übernommen. Einzeln eingescannte Pauli-Aufmaßblätter werden unverändert als unbekanntes Dokument weitergeleitet. Pauli-Lieferscheine bleiben davon unberührt.

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

### Archiv manuell leeren

Über **Archiv manuell leeren** in der Steuerung kann ein berechtigter Benutzer das vom Tool verwaltete Archiv zurücksetzen. Die Überwachung muss dazu beendet sein; anschließend sind eine Warnung und die Eingabe von `ARCHIV LEEREN` erforderlich. Entfernt werden alle markierten Tagesarchive sowie der interne Ordner für offene Wiederherstellungsvorgänge. Eingang, Ziel, Prüfordner, Einstellungen und Protokolle bleiben erhalten. Unbekannte Dateien oder nicht von der Anwendung markierte Ordner im Archiv werden aus Sicherheitsgründen nicht gelöscht und im Ergebnis angezeigt.

## Einstellungen

Die Oberfläche verwaltet:

- Eingangsordner für neue Scanner-PDFs
- Zielordner für erkannte oder unverändert weitergeleitete Dokumente
- Archivordner für Originalscans und offene Vorgänge
- Prüfordner für nicht erkannte oder beschädigte Scans
- Archiv-Aufbewahrung in Tagen, Standard 30
- Dateistabilität nach der letzten Änderung, Standard 1 Sekunde
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

Für Windows Server bietet das Setup zusätzlich die auswählbare Option **Serverautostart beim Systemstart einrichten**. Sie muss mit Administratorrechten ausgeführt werden und richtet eine geplante SYSTEM-Aufgabe mit 30 Sekunden Startverzögerung, drei Wiederanlaufversuchen und Schutz vor parallelen Instanzen ein. Die Aufgabe startet die Anwendung ohne Benutzeroberfläche; sie bleibt deshalb auch nach einem Serverneustart ohne Anmeldung aktiv. Bei erfolgreicher Einrichtung entfernt das Setup die benutzerbezogene Autostart-Verknüpfung des installierenden Kontos.

## Protokolle

Für jeden Kalendertag entsteht unter `%APPDATA%\DokumentenScannerSortierung\logs` eine eigene UTF-8-Datei, zum Beispiel:

```text
dokumentensortierer-2026-07-15.log
```

Tagesprotokolle werden 90 Tage aufbewahrt. Andere Dateien im Protokollordner werden von der Bereinigung nicht gelöscht.

Jeder Vorgang erhält eine ID. Protokolliert werden unter anderem Ergebnisstatus, Anwendungsversion, Dateiname und Größe, Seiten- und Dokumentanzahl, erkannte Typen, Ausgabedateien sowie Zeiten für Archivierung, Erkennung, Ausgabe und Gesamtverarbeitung. Die Erkennungsphase weist zusätzlich PDF-Rendering, Barcode-Erkennung, OCR-Gesamtzeit, langsamsten OCR-Einzelaufruf, OCR-Aufrufzahl, verarbeitete OCR-Pixel, verwendete Erkennungspfade und die pfadfreie Tesseract-Laufzeitquelle aus. Nicht erkannte Dokumente erhalten einen stabilen `grundcode`, die betroffene `stufe` und gegebenenfalls eine `seite`; der ergänzende Freitextgrund bleibt einzeilig. Startmeldungen enthalten Anwendung, Python, Tesseract, Leptonica, System, Prozess-ID und die wesentlichen Betriebseinstellungen. Vollständiger OCR-Text wird bewusst nicht gespeichert.

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

Es erstellt eine Desktop- und Autostart-Verknüpfung und registriert die Anwendung unter **Windows-Einstellungen > Apps > Installierte Apps** mit dem Herausgeber `Simon Hagen – Glas Hagen` und dem Kontakt `simon.hagen@glashagen.de`. Die Desktop-Verknüpfung verwendet einen schlanken Öffnen-Starter: Ist die Anwendung bereits im Infobereich aktiv, erscheint ihr Fenster ohne erneutes Laden der OCR-Laufzeit. Auf einem Server kann im Bestätigungsfenster stattdessen der SYSTEM-Autostart ausgewählt werden.

Vorhandene Versionen werden als Update oder Reparatur erkannt. Ein unbeabsichtigtes Downgrade und eine Installation über eine unbekannte/defekte Versionslage werden standardmäßig blockiert. Programmdateien, Registry-Eintrag und Desktop-Verknüpfung werden transaktional ausgetauscht; bei Fehlern wird die alte Installation wiederhergestellt. Einstellungen, Protokolle und Dokumentordner werden weder bei Updates noch bei der Deinstallation gelöscht.

Die administrativen Schalter `--allow-downgrade` und `--allow-unknown-version` heben die jeweilige Sperre bewusst auf und sollten nur nach Sicherung und Prüfung der vorhandenen Installation verwendet werden. Mit `--self-test` prüft das Setup ausschließlich Version, Manifest und eingebettete Nutzdaten; es verändert dabei weder Installation noch Einstellungen.

Die Anwendung muss vor einem Update vollständig beendet sein. Die Abschlussmaske bietet die standardmäßig aktivierte Option **Anwendung starten**.

Die Änderung in Version 0.3.6 an **Anwendung beenden** beendet auch die eingerichtete SYSTEM-Überwachung. Windows fordert dafür bei Bedarf eine administrative Bestätigung an. Ein bereits laufender Vorgang wird sicher fertiggestellt; erst nachdem die Serveraufgabe einschließlich ihres Startprozesses beendet ist, schließt sich die Oberfläche. Wird die Bestätigung abgebrochen oder der Stopp nicht innerhalb von 120 Sekunden bestätigt, bleibt die Oberfläche mit einer Fehlermeldung geöffnet. **In Infobereich ausblenden** und das Schließen des Fensters über das Windows-X lassen die SYSTEM-Überwachung weiterhin laufen.

Ist die SYSTEM-Aufgabe bereits gestoppt, schließt **Anwendung beenden** nach einer Statusprüfung ohne erneute Administratorabfrage. Updates entfernen unveränderte, veraltete Laufzeitdateien anhand der geprüften bisherigen Runtime-Dateiliste mit Rückrollmöglichkeit. Geänderte und unbekannte Dateien bleiben erhalten; bei einem Konflikt mit der neuen Ordnerstruktur wird das Update zurückgerollt.

### Beschleunigung des ersten Fensterstarts

Version 0.3.6 installiert die Laufzeitdateien dauerhaft im Unterordner `_internal` des Programmordners. Damit muss die installierte Anwendung beim ersten Öffnen nach einem Serverneustart ihr großes OCR-Paket nicht erneut in einen temporären Ordner entpacken. Die Benutzeroberfläche fragt beim Start außerdem keine OCR-Versionen ab; diese bleiben im Infofenster und in den Protokollen der aktiven Überwachung verfügbar. Die tatsächliche Startzeit ist mit Version 0.3.6 auf dem Server zu prüfen.

EXE und `_internal` gehören bei dieser Installation zusammen. Zum Installieren, Aktualisieren oder Übertragen einer Installation wird weiterhin das Setup verwendet. Die portable Einzel-EXE bleibt eigenständig und entpackt ihre Komponenten weiterhin beim Start. Die Serveraufgabe, zentrale Einstellungen und Desktop-Verknüpfung verwenden unverändert ihre bisherigen Einstiegspunkte.

## Server-Pilot und Freigaben

Die freigegebene Version 0.3.6 verwendet Tesseract OCR 5.5.3, PyMuPDF 1.28.2, pypdf 6.19.0 und zxing-cpp 3.1.1. Vor dem Update werden mit den tatsächlichen Serverpfaden nochmals mindestens je ein Aufmaßschein, eigener Empfangsschein, Neuma-Empfangsschein, Montageinfo mit und ohne Auftragsnummer, Nowak-, Bohle-, Pauli- und Heitzer-Lieferschein, unterschriebenes Angebot, Pauli-Aufmaßanlage, Abtretungserklärung, Zeidler-Ausführungsbestätigung und nicht erkennbarer Scan verarbeitet. Dabei werden Ziel-, Archiv-, Prüf- und Protokollordner, Start und kontrollierter Stopp der SYSTEM-Aufgabe sowie ein Wiederanlauf geprüft. Zusätzlich wird ein Diagnose-ZIP aus dem zentralen Protokoll erzeugt, während die Überwachung weiterläuft.

## Mitgelieferte OCR-Komponenten

Die aktuelle Freigabe 0.3.6 enthält:

- Tesseract OCR 5.5.3
- Leptonica 1.87.0
- Sprachmodelle `deu`, `eng` und `osd`

Die Versionen werden beim Build geprüft und im Infofenster sowie im Release-Manifest ausgewiesen. Weitere verwendete Bibliotheken und ihre Lizenzhinweise stehen in `THIRD_PARTY_NOTICES.md`. Insbesondere PyMuPDF ist dual unter AGPL und kommerzieller Lizenz verfügbar; der Betreiber muss vor einer Weitergabe oder Bereitstellung die passende Lizenzgrundlage festlegen.

Ein Tesseract-Pfad muss im normalen Betrieb nicht eingestellt werden: Die geprüfte OCR-Laufzeit ist im Paket enthalten. Eine geprüfte Laufzeit im Anwendungsverzeichnis wird vor der temporär entpackten Paketlaufzeit verwendet; eine technische Pfadüberschreibung in der Einstellungsdatei hat weiterhin Vorrang. Der Diagnosebericht gruppiert Laufzeiten nach dieser pfadfreien Quelle, damit ein Serververgleich ohne Export vollständiger Installationspfade möglich ist. Technische Pfadüberschreibungen bleiben bewusst außerhalb der normalen Bedienoberfläche.

## Automatischer Betrieb auf Windows Server 2025

Das Setup kann die Serveraufgabe direkt einrichten: Als Administrator starten, im Bestätigungsfenster **Serverautostart beim Systemstart einrichten** aktivieren und die Installation abschließen. Bereits gespeicherte Einstellungen werden einmalig in die zentrale Datei übernommen:

```powershell
C:\ProgramData\DokumentenScannerSortierung\settings.json
```

Eine bereits vorhandene zentrale Einstellungsdatei bleibt bei Updates unverändert. Muss sie manuell neu angelegt werden, kann sie wie folgt erstellt werden:

```powershell
New-Item -ItemType Directory -Force "C:\ProgramData\DokumentenScannerSortierung"
Copy-Item "$env:APPDATA\DokumentenScannerSortierung\settings.json" `
  "C:\ProgramData\DokumentenScannerSortierung\settings.json"
notepad "C:\ProgramData\DokumentenScannerSortierung\settings.json"
```

Pfade auf demselben Server sollten als lokale Pfade wie `D:\Freigaben\pool\Dateiarchiv` eingetragen werden. Externe Netzwerkfreigaben müssen als UNC-Pfade wie `\\server\freigabe\ordner` hinterlegt sein. Benutzerabhängige Laufwerksbuchstaben wie `G:` stehen einem Systemkonto nach einem Serverneustart nicht zur Verfügung.

Falls die Aufgabe ausnahmsweise manuell in der Windows-Aufgabenplanung eingerichtet werden soll, gelten dieselben Werte:

- Auslöser: **Beim Starten des Computers**
- Ausführen unabhängig von der Benutzeranmeldung
- Programm: installierte `DokumentenScannerSortierung.exe`
- Argumente: `--run --settings "C:\ProgramData\DokumentenScannerSortierung\settings.json"`
- Bei bereits laufender Aufgabe: **Keine neue Instanz starten**
- Bei Fehlern: Neustart nach einer kurzen Wartezeit aktivieren

Die automatisch erstellte Aufgabe heißt `GlasHagen Dokumenten-Scanner-Sortierung`, läuft als `SYSTEM`, startet nach 30 Sekunden und verwendet die zentrale Einstellungsdatei. Bei der Deinstallation wird sie entfernt, sofern die Deinstallation mit Administratorrechten ausgeführt wird.

Wird die normale Benutzeroberfläche geöffnet, während diese SYSTEM-Aufgabe den Eingangsordner bereits überwacht, zeigt sie **Serverüberwachung aktiv** an und startet keine zweite Verarbeitung. **Überwachung starten** und **Überwachung beenden** steuern in diesem Betriebsmodus die SYSTEM-Aufgabe; Windows fordert dafür bei Bedarf eine administrative Bestätigung an. Beim Beenden schreibt die Oberfläche ein privilegiertes Stoppsignal. Der SYSTEM-Worker startet danach keinen neuen Scan, schließt einen bereits laufenden Vorgang sicher ab und beendet sich selbst. Das manuelle Archivleeren wird erst nach dem bestätigten Stopp wieder freigegeben.

Das Aktivitätsprotokoll in der Oberfläche zeigt bei eingerichtetem Serverautostart automatisch das zentrale Tagesprotokoll aus `C:\ProgramData\DokumentenScannerSortierung\logs`. Die Benutzeroberfläche schreibt weiterhin ihr eigenes Diagnoseprotokoll unter `%APPDATA%`; dadurch lesen beide Betriebsarten gemeinsam sichtbar aus einer zentralen Quelle, ohne gleichzeitig dieselbe Datei zu verändern.

Über **Diagnosebericht erstellen** kann dieses lokale Protokoll für die letzten 7, 30 oder 90 Tage ausgewertet werden; voreingestellt sind 30 Tage und die aktuelle Anwendungsversion. Wahlweise lassen sich alle Versionen gemeinsam auswerten und vergleichen. Das erzeugte ZIP enthält ausschließlich `diagnosebericht.html` und `diagnosebericht.json`. Schema 5 des JSON-Berichts enthält Durchschnitt, Median und 95. Perzentil der Laufzeit je Anwendungsversion, die Verarbeitungsversion und verständliche Prüfgründe je Problemfall, Render-, Barcode- und OCR-Statistiken, OCR-Aufruf- und Pixelzahlen, Erkennungspfade sowie Vergleiche nach pfadfreier Tesseract-Laufzeitquelle und wirksamem OCR-Threadlimit. Ältere Logs bleiben auswertbar und erscheinen bei den neuen Messwerten als nicht verfügbar beziehungsweise `legacy_nicht_spezifiziert` oder `nicht_erfasst`. Die Protokollqualität wird unabhängig vom Versionsfilter für alle ausgewerteten Logzeilen angegeben. Dateinamen sind standardmäßig deaktiviert und müssen bewusst freigegeben werden, vollständige Pfade werden nie aufgenommen. Rohlogs, PDFs und OCR-Volltexte verlassen den Rechner nicht und sind nicht Teil des Berichts. Die laufende Überwachung muss für den Export nicht beendet werden.

Für Netzwerkfreigaben sind UNC-Pfade wie `\\server\freigabe\scanner\eingang` robuster als benutzerabhängige Laufwerksbuchstaben. Das Dienstkonto benötigt Lesen/Ändern/Löschen im Eingang sowie Lesen/Schreiben/Ändern in Ziel, Archiv, Prüfordner und am Ordner der zentralen `settings.json`.

Bei einem vorübergehenden Ausfall eines Serverpfads wartet die Anwendung mit exponentiellem Backoff zwischen 1 und 60 Sekunden und setzt die Überwachung nach der Wiederkehr automatisch fort. Der erste Fehler und jede geänderte Fehlerursache werden vollständig protokolliert; identische Folgefehler erscheinen höchstens alle zehn Minuten kompakt. Nach der Wiederherstellung nennt das Protokoll Fehlerdauer und Anzahl der Versuche.

Alle zehn Minuten schreibt die aktive Überwachung ein Lebenszeichen mit Anwendungsversion, Prozess-ID, Tesseract-/Leptonica-Version, Laufzeit, Betriebsart, laufender Verarbeitung, Anzahl wartender PDFs, Erreichbarkeit der Arbeitsordner, fortlaufenden Ordnerfehlern und dem letzten Verarbeitungsergebnis in das jeweilige Tagesprotokoll. Die Laufzeitversionen werden beim Start einmalig ermittelt und für spätere Einträge wiederverwendet. Fehlt dieses Lebenszeichen länger als erwartet, sollte der Status der geplanten Aufgabe geprüft werden.

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
.\.venv\Scripts\ruff.exe check src installer scripts tests
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Nach Abschluss der gesammelten Änderungen das OCR-Paket vorbereiten und den nächsten Release-Build starten:

```powershell
.\scripts\prepare-tesseract-vendor.ps1
.\scripts\build-release.ps1 -Version 0.3.6
```

Der Build bricht bei Tests, Versionsabweichungen, fehlenden Sprachmodellen, falscher Tesseract-/Leptonica-Version, inkonsistenten Python-Paketen oder fehlenden Artefakten ab. Alte Release-Ordner bleiben erhalten. Optional können Anwendung und Setup mit einem vorhandenen Authenticode-Zertifikat signiert werden; ohne Zertifikat weist das Release-Manifest `signed: false` aus.

Ein bewusst nicht reproduzierbarer Entwicklungs-Build aus einem geänderten Arbeitsverzeichnis ist mit `-AllowDirtySource` möglich. Ein bereits vorhandener versionsbezogener Release-Ordner wird nur mit `-ForceRebuild` ersetzt. Anwendung und Setup unterstützen `--self-test`; der Release-Build führt beide Selbsttests zeitlich begrenzt aus, bevor er die Artefakte veröffentlicht.
