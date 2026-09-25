"""Put the repository root on sys.path so `import tennis` works from a bare checkout."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
