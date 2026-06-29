import os
import sys

# Make the `src` package importable as top-level (data.*, config, ui.*).
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)
