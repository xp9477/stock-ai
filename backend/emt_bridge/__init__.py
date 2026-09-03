"""Isolated, read-only EMT simulation bridge.

This package is intentionally outside ``app`` so the FastAPI worker never
loads the vendor native SDK or receives broker credentials.
"""

