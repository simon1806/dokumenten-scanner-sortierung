# Drittanbieter-Hinweise

## Python, Tcl/Tk und PyInstaller

Die Windows-Anwendung enthaelt die Python-Laufzeit, Tcl/Tk sowie den
PyInstaller-Bootloader.

- Python: Python Software Foundation License 2.0
- Tcl/Tk: Tcl/Tk License
- PyInstaller-Bootloader: GPL-2.0-or-later mit der PyInstaller-Bootloader-Ausnahme

## Python-Bibliotheken

Die Anwendung verwendet und verteilt folgende direkte Laufzeitabhaengigkeiten:

- PyMuPDF: GNU Affero General Public License 3.0 oder kommerzielle Artifex-Lizenz
- pypdf: BSD 3-Clause License
- Pillow: MIT-CMU License
- pytesseract: Apache License 2.0
- pystray: GNU Lesser General Public License v3.0
- zxing-cpp: Apache License 2.0

PyMuPDF und MuPDF werden dual unter AGPL und kommerziellen Lizenzvereinbarungen
angeboten. Der Betreiber beziehungsweise Verteiler der Anwendung muss
sicherstellen, dass die gewaehlte Lizenzgrundlage fuer den konkreten Einsatz
eingehalten wird. Weitere Informationen: https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright

## Tesseract OCR

Die Anwendung liefert Tesseract OCR `5.5.2` mit Leptonica `1.87.0`, OpenMP-Unterstuetzung und den Sprachmodellen `deu`, `eng` und `osd` mit.

- Tesseract OCR: Apache License 2.0
- Leptonica: BSD 2-Clause License
- Projekt: https://github.com/tesseract-ocr/tesseract
- Quellrelease: https://github.com/tesseract-ocr/tesseract/releases/tag/5.5.2
- Windows-Paket: https://packages.msys2.org/packages/mingw-w64-x86_64-tesseract-ocr
- Leptonica-Release: https://github.com/DanBloomberg/leptonica/releases/tag/1.87.0
- Leptonica-Windows-Paket: https://packages.msys2.org/packages/mingw-w64-x86_64-leptonica

## GCC- und Winpthreads-Laufzeit

Fuer Tesseract 5.5.2 werden die MSYS2-Pakete `mingw-w64-x86_64-gcc-libs` und `mingw-w64-x86_64-libwinpthread` mitgeliefert.

- GCC-Laufzeitbibliotheken: GPL-3.0-or-later mit GCC Runtime Library Exception 3.1 sowie LGPL-2.1-or-later
- Winpthreads: MIT und BSD-3-Clause-Clear
- Paketquelle: https://packages.msys2.org/

## Weitere native Tesseract-Abhaengigkeiten

Das Windows-Laufzeitpaket von Tesseract enthaelt weitere Bild-, Schrift-,
Kompressions-, Archiv- und Netzwerkbibliotheken, unter anderem libarchive,
libcurl, cairo, fontconfig, freetype, glib, harfbuzz, ICU, libjpeg-turbo,
libpng, libtiff, libwebp, OpenJPEG und zlib. Die konkreten Dateien und Versionen
werden im Release-Manifest beziehungsweise in der Tesseract-Versionsausgabe
dokumentiert. Fuer diese Komponenten gelten die jeweiligen Upstream-Lizenzen.

## Tabler Icons

Für die Schaltflächen der Anwendung werden ausgewählte Symbole aus [Tabler Icons](https://tabler.io/icons) verwendet.

MIT License

Copyright (c) 2020-2026 Paweł Kuna

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
