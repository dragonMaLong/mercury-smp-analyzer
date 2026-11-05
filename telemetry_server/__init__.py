"""Optional, separately deployed MIP usage-statistics service."""

from .app import create_app

__all__ = ["create_app"]
