"""Pytest fixtures."""
import sys
from pathlib import Path

import pytest

# Project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def client():
    from backend.app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
