import pathlib
import sys

# Ensure the repo root is importable as the `src` package during tests.
sys.path.insert(0, str(pathlib.Path(__file__).parent))
