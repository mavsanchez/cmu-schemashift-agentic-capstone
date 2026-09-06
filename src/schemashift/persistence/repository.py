"""Single persistence facade used by services and UI handlers."""

from __future__ import annotations

from schemashift.config import Settings
from schemashift.persistence.artifacts import ArtifactRepository
from schemashift.persistence.conversations import ConversationRepository
from schemashift.persistence.events import EventRepository
from schemashift.persistence.finalization import FinalizationRepository
from schemashift.persistence.messages import MessageRepository
from schemashift.persistence.postgres import PostgresDatabase
from schemashift.persistence.reviews import ReviewRepository
from schemashift.persistence.sources import SourceRepository


class PostgresRepository(
    ConversationRepository,
    MessageRepository,
    EventRepository,
    SourceRepository,
    ReviewRepository,
    ArtifactRepository,
    FinalizationRepository,
):
    """Aggregate repository over the canonical SchemaShift history database."""

    def __init__(
        self,
        database: PostgresDatabase | Settings | str | None = None,
    ) -> None:
        if database is None:
            self.db = PostgresDatabase.from_settings()
        elif isinstance(database, PostgresDatabase):
            self.db = database
        elif isinstance(database, Settings):
            self.db = PostgresDatabase.from_settings(database)
        elif isinstance(database, str):
            self.db = PostgresDatabase(database)
        else:  # pragma: no cover - static typing normally prevents this
            raise TypeError("database must be PostgresDatabase, Settings, DSN string, or None")

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> PostgresRepository:
        return cls(PostgresDatabase.from_settings(settings))

    def initialize(self) -> None:
        self.db.initialize()

    def init_schema(self) -> None:
        """Compatibility alias for explicit setup scripts."""

        self.initialize()

    def health_check(self) -> bool:
        return self.db.health_check()

    def close(self) -> None:
        """No-op: this repository deliberately owns no persistent connection pool."""


def create_repository(settings: Settings | None = None) -> PostgresRepository:
    """Construct the standard application repository without initializing schema."""

    return PostgresRepository.from_settings(settings)
