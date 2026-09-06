"""Persistence-layer exceptions with actionable semantics."""


class PersistenceError(RuntimeError):
    """Base error for durable history operations."""


class RecordNotFoundError(PersistenceError):
    """A requested durable entity does not exist."""


class PersistenceConflictError(PersistenceError):
    """An idempotent write conflicts with already persisted data."""


class ActiveRunExistsError(PersistenceConflictError):
    """A conversation already has an active or human-waiting run."""
