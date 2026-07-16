# Änderungsprotokoll

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
