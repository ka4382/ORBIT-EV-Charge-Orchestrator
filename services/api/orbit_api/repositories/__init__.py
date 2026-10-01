"""Repository pattern: ports in `base.py`, asyncpg adapter in `postgres.py`. Tests supply in-memory fakes."""

from .base import Repos

__all__ = ["Repos"]
