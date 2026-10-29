"""Tell Tcl/Tk where PyInstaller extracted the bundled libraries."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    bundle = Path(sys._MEIPASS)
    # Tcl/Tk 9 can load its scripts from DLL-embedded zipfs archives.
    # Only override paths when PyInstaller collected external script directories.
    for variable, directory in (("TCL_LIBRARY", "_tcl_data"), ("TK_LIBRARY", "_tk_data")):
        path = bundle / directory
        if path.is_dir():
            os.environ.setdefault(variable, str(path))
