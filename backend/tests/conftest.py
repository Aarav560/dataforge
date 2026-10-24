"""Tests run against a throwaway data directory so they never touch real projects."""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("DATAFORGE_HOME", tempfile.mkdtemp(prefix="dataforge_test_"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
