"""Pytest configuration: put the project root on sys.path so `import pipeline...`
resolves when tests run from any working directory (locally and in CI)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
