from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from psycopg import AsyncConnection

from creditops.infrastructure.postgres.repositories import DatabaseConnection


class PsycopgConnectionFactory:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[DatabaseConnection]:
        connection = await AsyncConnection.connect(self._database_url, autocommit=False)
        try:
            yield cast(DatabaseConnection, connection)
        finally:
            await connection.close()
