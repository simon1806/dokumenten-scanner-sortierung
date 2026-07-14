"""Tell Tcl/Tk where PyInstaller extracted the bundled libraries."""

from __future__ import annotations

import os
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    bundle = Path(sys._MEIPASS)
    os.environ.setdefault("TCL_LIBRARY", str(bundle / "_tcl_data"))
    os.environ.setdefault("TK_LIBRARY", str(bundle / "_tk_data"))
