# Lokale manuelle PDF-Muster

Diese Ablage ist für echte Testscans vorgesehen. PDF-Dateien bleiben ausschließlich lokal und werden nicht in das Repository aufgenommen, da sie personen- oder projektspezifische Daten enthalten können.

Aktueller lokaler Bestand: Montageinfos `MI_3260455` bis `MI_3260477`, Neuma-Empfangsscheine sowie eine kuratierte Archivauswahl unter `archiv_auswahl`.

Unter `testlaeufe` liegen vorbereitete Eingangsordner für manuelle Regressionstests:

- `01_einzeldokumente`: Aufmaß, Montageinfo, Empfangsschein und Nowak-Lieferschein
- `02_kombinierter_scan`: ein Scan mit Montageinfo und Nowak-Lieferschein
- `03_nicht_erkannt`: zwei bewusst nicht unterstützte PDFs
- `04_rueckstau`: mehrere unterschiedliche PDFs zur Prüfung von Stapelmodus, Drosselung und Ausgaben

Die erwarteten Ausgabedateien stehen jeweils im Dateinamen der kuratierten Auswahl. Alle Muster bleiben lokal und sind per `.gitignore` vom Repository ausgeschlossen.
