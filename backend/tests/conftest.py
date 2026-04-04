"""Pytest loads this first; ensure env is set before app.main imports Settings."""

from __future__ import annotations

import os

# In-memory DB for tests (avoid vecna.db); ok if .env has no DATABASE_URL.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
