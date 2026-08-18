"""Vercel Python entry point.

Vercel's Python builder only auto-detects functions under api/, and this
file's own directory (not src/) is what ends up on sys.path -- so main.py's
absolute `setuhaul.*` imports need src/ added explicitly before importing it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from setuhaul.main import app  # noqa: E402
