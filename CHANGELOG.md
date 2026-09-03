# Änderungsprotokoll

## 0.3.4 – 2026-09-03

- Verarbeitungslogs messen PDF-Rendering, Barcode-Erkennung, OCR-Gesamtzeit, langsamsten OCR-Einzelaufruf, OCR-Aufrufzahl, OCR-Pixel, Erkennungspfade und die pfadfreie Tesseract-Laufzeitquelle. OCR-Inhalte und vollständige Installationspfade werden weiterhin nicht protokolliert oder exportiert.
- Diagnose-JSON Schema 3 und der lokale HTML-Bericht zeigen die Durchschnittslaufzeit je Anwendungsversion sowie detaillierte Erkennungsstatistiken und gruppieren Laufzeiten nach Tesseract-Laufzeitquelle. Ältere Logs bleiben kompatibel.
- NEUMA-Aufträge verwenden nach dem eindeutigen Lieferantensignal einen kleineren Kopfbereich. Ein Regressionstest mit allen 286 bereitgestellten PDFs bestätigt unveränderte Dokumenttypen, Nummern, Lieferanten und Seitenfolgen.
- Neuinstallationen warten standardmäßig eine statt zwei Sekunden auf Dateistabilität. Bereits gespeicherte Einstellungen bleiben bei Updates unverändert.
- Eine Tesseract-Laufzeit im stabilen Anwendungsverzeichnis wird vor der temporär entpackten Paketlaufzeit verwendet; technische Pfadüberschreibungen behalten Vorrang.

## 0.3.3 – 2026-08-27

- Empfangsscheine und andere unterstützte Dokumente mit tiefer liegender Belegzeile werden durch den erweiterten Kopfbereich zuverlässig erkannt.
- Mehrseitige Heitzer-Lieferscheine werden über Lieferant und Lieferscheinnummer erkannt. Eine vollständig lesbare, eindeutige Angabe `Seite X von Y` korrigiert eine falsche Scanreihenfolge; bei Lücken, Dubletten oder widersprüchlichen Angaben bleibt die Quellreihenfolge unverändert.
- Zeidler-Ausführungsbestätigungen werden anhand ihrer Dokumentüberschrift, eines Zeidler-Merkmals und der Auftragsnummer erkannt und als `Ausführung-Zeidler-<Auftragsnummer>.pdf` abgelegt. Generische Ausführungsbestätigungen ohne Zeidler-Nachweis bleiben unberücksichtigt.

## 0.3.2 – 2026-08-27

- Die PDF-, Barcode-, Paketierungs- und Qualitätswerkzeuge wurden auf pypdf 6.16.2, zxing-cpp 3.1.1, PyInstaller 6.22.2, pyinstaller-hooks-contrib 2026.7, setuptools 84.0.0, packaging 26.3 und ruff 0.16.4 aktualisiert. Die QR-Code-Erkennung profitiert dabei von den Korrekturen in zxing-cpp 3.1.1; der Release-Build verwendet die zugehörigen geprüften Windows-x64-Wheels.

## 0.3.1 – 2026-08-27

- Zentrale Betriebs-, Verarbeitungs- und Ordnerereignisse verwenden ein stabiles Ereignisschema mit Schema-, Ereignis- und Sitzungskennung. Dynamische Werte bleiben einzeilig und können keine zusätzlichen Felder einschleusen.
- Der Diagnosebericht zählt Grundcodes nur noch für Problemfälle und behandelt die regulären Heartbeat-Felder korrekt. Logstufen, unklassifizierte Warnungen und Fehler sowie tatsächlich unbekannte Feldnamen bleiben sichtbar.
- Bereits protokollierte Archivierungs-, Erkennungs- und Ausgabelaufzeiten werden mit Durchschnitt, Median, 95. Perzentil und Maximum ausgewertet. Die langsamsten Vorgänge und die Gesamtseitenzahl von Problemfällen werden gesondert ausgewiesen.
- Anwendungsstarts und kontrollierte Stopps werden über Sitzungskennungen verknüpft. Stoppgrund, Exit-Code, Laufzeit und Heartbeat-Unterbrechungen erleichtern die Unterscheidung von Wartung und unerwartetem Prozessende.
- Diagnose-JSON verwendet Schema 2. Alte Tageslogs bleiben weiterhin auswertbar; die Dokumenterkennung ist gegenüber 0.3.0 unverändert.

## 0.3.0 – 2026-08-19

- Abschlussmeldungen enthalten nun die Anwendungsversion. Nicht erkannte Dokumente erhalten zusätzlich einen stabilen Grundcode, die Erkennungsstufe und gegebenenfalls die betroffene Seite; Freitextgründe werden einzeilig und delimiter-sicher protokolliert.
- Im Aktivitätsprotokoll kann ein lokaler Diagnosebericht für die letzten 7, 30 oder 90 Tage erstellt werden. Das atomar veröffentlichte ZIP enthält eine skriptfreie HTML-Zusammenfassung und strukturierte JSON-Daten, aber keine Rohlogs, PDFs oder OCR-Volltexte.
- Diagnoseberichte fassen Verarbeitungsergebnisse, Erkennungsquote, Laufzeiten, Dateigrößen, Warteschlangenstände, Ordnerfehler, Anwendungsstarts, Versionen, Dokumenttypen und Grundcodes zusammen. Ältere Einträge ohne Grundcode werden als `legacy_nicht_spezifiziert` ausgewiesen.
- Dateinamen sind im Diagnoseexport standardmäßig deaktiviert und müssen bewusst freigegeben werden; vollständige Pfade werden nie exportiert. Bei aktivem SYSTEM-Betrieb wird das zentrale Protokoll ausgewertet, ohne die Überwachung anzuhalten.

## 0.2.9 – 2026-08-10

- Aufmaß- und Glasbestellblätter von Pauli + Sohn werden als Fortsetzungsseiten eines unmittelbar vorausgehenden Glas-Hagen-Aufmaßscheins behandelt. Die stabilen Set-Merkmale verhindern dabei eine unnötige Ganzseiten-OCR; einzeln eingescannte Blätter bleiben weiterhin unbekannte Dokumente.
- Unterschriebene Glas-Hagen-Angebote werden anhand der Angebotsnummer und des Bestätigungsbereichs als `AG_<Angebotsnummer>_UNTERS.pdf` abgelegt. Alle Angebotsseiten bleiben auch bei umgekehrter Scanreihenfolge in einer Datei zusammen.
- Lieferscheine von Bohle werden über zwei kleine Kopfbereiche erkannt und als `LS-Bohle-<Lieferscheinnummer>.pdf` abgelegt.
- PyMuPDF und die zugrunde liegende MuPDF-Laufzeit wurden auf 1.28.2 aktualisiert. Die Anwendung verwendet nun den aktuellen Modulnamen `pymupdf` statt des abgekündigten Legacy-Imports `fitz`.
- pypdf wurde auf 6.15.0 aktualisiert. Die neue Version begrenzt problematische Schriftinformationen beim Einlesen und behebt weitere PDF-Decoderfehler.

## 0.2.8 – 2026-07-30

- Eigene Empfangsscheine werden auch bei numerischen Code-39-Prüfzeichen korrekt erkannt. Der verifizierte Mod-43-Prüfwert wird nicht mehr fälschlich als Teil der Auftragsnummer übernommen; ein gezielter Barcode-Ausschnitt im Kopfbereich verbessert schwache Scans ohne langsame Ganzseitenvergrößerung.
- Wiederholte identische Ordner- und Netzwerkfehler erzeugen nur beim ersten Auftreten einen vollständigen Traceback. Danach folgt höchstens alle zehn Minuten eine kompakte Zustandsmeldung; geänderte Fehler werden erneut vollständig und die Wiederherstellung mit Dauer und Versuchszahl protokolliert.
- Der zehnminütige Betriebsstatus enthält nun Anwendungsversion, Prozess-ID sowie die beim Start ermittelten Tesseract- und Leptonica-Versionen. Die zwischengespeicherten Angaben starten während späterer Statusmeldungen keine zusätzlichen OCR-Prozesse.
- Das Setup übergibt die sichtbare Bestätigungsmaske direkt an die Fortschrittsanzeige und schließt diese erst, nachdem die Abschlussmaske sichtbar ist. Dadurch bleibt während Installation, Update und Reparatur durchgehend ein Installer-Fenster sichtbar.

## 0.2.7 – 2026-07-28

- Die mitgelieferte OCR-Laufzeit wurde auf Tesseract OCR 5.5.3 aktualisiert. Leptonica 1.87.0 und die Sprachmodelle `deu`, `eng` und `osd` bleiben unverändert. Das Wartungsupdate verbessert insbesondere die Speichersicherheit beim Laden von Sprachmodellen und behebt weitere Stabilitätsprobleme.

## 0.2.6 – 2026-07-27

- Die Oberfläche beendet die SYSTEM-Überwachung über ein administrativ geschriebenes Stoppsignal. Der Worker schließt einen bereits laufenden Scan sicher ab und beendet anschließend seinen vollständigen PyInstaller-Prozessbaum; ein verwaister Hintergrundprozess bleibt nicht mehr zurück.
- Bei eingerichtetem Serverautostart zeigt das Aktivitätsprotokoll der Benutzeroberfläche automatisch das zentrale Tagesprotokoll aus `C:\ProgramData\DokumentenScannerSortierung\logs` an. Benutzer- und SYSTEM-Prozess schreiben weiterhin getrennt, sodass keine konkurrierenden Schreibzugriffe entstehen.
- Das Abschlussfenster des Setups bietet mehr Platz für Installationspfad und Serverautostart-Status. Veraltete interne Hinweise auf eine frühere Build-Version wurden bereinigt.

## 0.2.5 – 2026-07-27

- Eine unter `SYSTEM` laufende Serverüberwachung wird in der Benutzeroberfläche als aktiv erkannt. Die geschützte globale Sperre führt nicht mehr zu einer irreführenden Fehlermeldung. Die vorhandenen Schaltflächen starten und stoppen die SYSTEM-Aufgabe nach administrativer Bestätigung; das manuelle Archivleeren wird erst nach dem bestätigten Stopp freigegeben.
- Die laufende Überwachung schreibt alle zehn Minuten einen kompakten Betriebsstatus mit Laufzeit, Betriebsart, Verarbeitung, Warteschlange, Erreichbarkeit aller Arbeitsordner, fortlaufenden Ordnerfehlern und dem letzten Verarbeitungsergebnis in das Tagesprotokoll.

## 0.2.4 – 2026-07-24

- Das Setup zeigt nach der Bestätigung direkt eine nicht schließbare Fortschrittsmaske mit animiertem Balken. Sie bleibt während Dateiprüfung und Austausch sichtbar und schließt vor dem Erfolgs- oder Fehlerdialog.
- Das Archiv kann nach gestoppter Überwachung manuell zurückgesetzt werden. Zwei Bestätigungen schützen die Aktion; entfernt werden nur markierte Tagesarchive und interne Wiederherstellungsvorgänge.
- Montageinfos ohne Auftragsnummer oder MI-Barcode werden als `MI_<JJJJ-MM-TT>.pdf` gespeichert. Das Datum wird aus dem Scanner-Dateinamen abgeleitet, ansonsten aus dem Dateidatum.

## 0.2.3 – 2026-07-23

- Die Desktop-Verknüpfung startet einen schlanken Öffnen-Starter statt unmittelbar die große OCR-Anwendung. Läuft diese bereits ausgeblendet im Windows-Infobereich, wird ihr Fenster ohne erneutes Entpacken der Tesseract-Laufzeit aktiviert.
- Der Starter startet die Hauptanwendung nur, wenn noch kein Anwendungsfenster vorhanden ist. Der Benutzer- und SYSTEM-Autostart bleiben unverändert auf der Hauptanwendung.

## 0.2.2 – 2026-07-23

- Das Setup bietet optional die Einrichtung eines Serverautostarts beim Systemstart an. Es erstellt eine SYSTEM-Aufgabe mit Startverzögerung, Wiederanlauf bei Fehlern und Schutz vor parallelen Instanzen.
- Bei Auswahl des Serverautostarts werden die bestehenden Einstellungen einmalig nach `C:\ProgramData\DokumentenScannerSortierung\settings.json` übernommen. Eine bereits vorhandene zentrale Konfiguration wird bei Updates nicht überschrieben.
- Die benutzerbezogene Autostart-Verknüpfung wird bei erfolgreicher Servereinrichtung entfernt. Die Deinstallation entfernt die optionale SYSTEM-Aufgabe, sofern sie mit ausreichenden Rechten ausgeführt wird.
- Das Hauptfenster nutzt die verfügbare Bildschirmhöhe besser; das Aktivitätsprotokoll erhält einen dauerhaft sichtbaren Mindestbereich.
- Der optionale Tesseract-Pfad wird nicht mehr in der normalen Oberfläche angezeigt. Die mitgelieferte OCR wird verwendet; vorhandene technische Überschreibungen bleiben für Kompatibilität erhalten.

## 0.2.1 – 2026-07-21

- Die Eingangsdatei wird während der OCR nicht mehr im Eingangsordner umbenannt. Erst nach vollständig geprüfter Ausgabe wird sie in den privaten Vorgangsordner übernommen und entfernt.
- Bei einem Rückstau werden Scanvorgänge kontrolliert nacheinander abgearbeitet; standardmäßig pausiert die Anwendung ab vier wartenden PDFs zehn Sekunden zwischen zwei Vorgängen.
- Eine OCR-Gesamtzeitgrenze von 90 Sekunden pro Scan verhindert, dass mehrere Einzel-Timeouts einen Vorgang unverhältnismäßig lange blockieren.
- Montageinfos akzeptieren die bekannte OCR-Abweichung `Montageber’cht`, wenn gleichzeitig `Auftrag:` und eine gültige Auftragsnummer vorliegen; dadurch entfällt bei diesen Scans ein zusätzlicher OCR-Kopfbereich.
- Die Betriebsdokumentation beschreibt die einmalige Einrichtung einer beim Serverboot startenden SYSTEM-Aufgabe mit zentraler Einstellungsdatei und UNC-Pfaden.
- Die lokalen manuellen Testläufe sind mit repräsentativen Dokumentklassen und getrennten Testordnern dokumentiert; die Test-PDFs bleiben wegen enthaltener personenbezogener Daten außerhalb von Git.

## 0.2.0 – 2026-07-17

- Unbekannte Dokumente werden nach einer erfolglosen Kopferkennung ohne langsame Ganzseiten-OCR unverändert in Ziel- und Prüfordner weitergeleitet.
- Abtretungserklärungen werden über das Feld `Auftrag/Angebot` als `ABTRET_<Auftrag>.pdf` erkannt.
- Montageinfos werden über einen gezielten Kopfbereich schneller erkannt und stets als einzelne Seite ausgegeben, auch bei gleicher Auftragsnummer.
- Neuma-Empfangsscheine werden anhand der Neuma-Auftragsnummer als `EM-NEUMA-I-<Jahr>-<Nummer>.pdf` erkannt.
- Das Setup richtet den Autostart nach Windows-Anmeldung ein; die Überwachung startet bei gültigen gespeicherten Einstellungen im Windows-Infobereich.
- Reale manuelle Testscans für Montageinfos und Neuma werden lokal dokumentiert, aber wegen der enthaltenen Daten nicht in das Repository übertragen.

## 0.1.25 – 2026-07-16

- Nowak-Lieferscheine werden über einen kleinen, gezielten Kopfbereich oben rechts deutlich schneller erkannt.
- Die Lieferscheinnummer ist nicht mehr auf den bisherigen Präfix `47` beschränkt; auch vorherige, künftige und andere vollständige Nummern werden unterstützt.
- Unvollständig gelesene Nowak-Logos werden mit dem stabilen Lieferantenkopf abgesichert, ohne beliebige numerische Barcodes fälschlich Nowak zuzuordnen.
- Der vorhandene 5-seitige Nowak-Testscan wird weiterhin in vier Dokumente getrennt; die Erkennungszeit sank im lokalen Vergleich von 17,54 auf etwa 3,6 Sekunden.

## 0.1.24 – 2026-07-15

- Transaktionale Verarbeitung mit persistenten Pending-Vorgängen und automatischem Wiederanlauf ergänzt.
- Eingangsdateien werden erst nach dauerhafter, prüfsummengesicherter Archivierung atomar übernommen.
- Mehrteilige Ausgaben werden vollständig vorbereitet und ohne Überschreiben vorhandener Dateien veröffentlicht.
- Nicht erkannte, beschädigte oder dauerhaft nicht trennbare PDFs werden unverändert in Ziel- und Prüfordner weitergeleitet.
- Archivbereinigung auf eindeutig eigene Dateien begrenzt; offene und unbekannte Pending-Zustände blockieren die Löschung sicher.
- Kontrolliertes Beenden, stop-bewusste Wiederherstellung, serverweite Eingangsordner-Sperre und Netzwerk-Backoff ergänzt.
- Tagesprotokolle mit 90 Tagen Aufbewahrung und erweiterten Laufzeit-/Verarbeitungsdaten ergänzt.
- Tesseract OCR 5.5.2 und Leptonica 1.87.0 als geprüfte Build-Vorgaben festgelegt.
- Installer um Payload-Prüfung, transaktionalen Rollback, Reparatur-/Downgrade-Schutz und parallele Setup-Sperre gehärtet.
- Reproduzierbare Build-Abhängigkeiten, Windows-CI, PE-Versionsinformationen, Release-Manifeste und SHA-256-Dateien ergänzt.
- Ressourcenlimits und gepackte Laufzeit-Selbsttests ergänzt.
- Dokumentierte Admin-Freigaben für Downgrade, unbekannte Altversion, Entwicklungs-Build und erzwungenen Neuaufbau ergänzt.
- GitHub-Actions auf unveränderliche Commit-IDs und Python-Buildpakete auf geprüfte Windows-Wheel-Hashes festgelegt.

### Migrationshinweis

Archivdateien aus Versionen vor 0.1.24 besitzen keinen Eigentums- und Prüfsummennachweis. Sie werden daher absichtlich nicht automatisch durch die neue Archivbereinigung gelöscht und müssen nach manueller Prüfung separat bereinigt werden.
