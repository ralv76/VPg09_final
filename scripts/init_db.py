#!/usr/bin/env python3
"""Initialize database schema. Run from project root: python -m scripts.init_db"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.database import init_db

if __name__ == "__main__":
    path = init_db()
    print(f"Database initialized at {path}")
