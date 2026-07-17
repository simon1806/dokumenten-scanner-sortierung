# Änderungsprotokoll

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
