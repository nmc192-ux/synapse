import asyncio
import sqlite3

from synapse.runtime.tools import ToolRegistry


def register(registry: ToolRegistry) -> None:
    async def database_query(arguments: dict[str, object]) -> dict[str, object]:
        database = str(arguments.get("database", "")).strip()
        query = str(arguments.get("query", "")).strip()
        allow_write = bool(arguments.get("allow_write", False))

        if not database:
            raise ValueError("database.query requires a 'database' path.")
        if not query:
            raise ValueError("database.query requires a SQL 'query'.")
        if not allow_write and _is_write_query(query):
            raise ValueError("database.query only allows read-only queries unless allow_write=true.")

        return await asyncio.to_thread(_execute_query, database, query)

    registry.register(
        "database.query",
        database_query,
        description="Execute structured SQLite queries and return rows/columns.",
        plugin_name="database",
    )


def _execute_query(database: str, query: str) -> dict[str, object]:
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        cursor = connection.execute(query)
        rows = [dict(row) for row in cursor.fetchall()]
        return {
            "row_count": len(rows),
            "columns": list(rows[0].keys()) if rows else [],
            "rows": rows,
        }
    finally:
        connection.close()


def _is_write_query(query: str) -> bool:
    return query.lstrip().split(" ", 1)[0].upper() in {
        "INSERT",
        "UPDATE",
        "DELETE",
        "CREATE",
        "DROP",
        "ALTER",
        "REPLACE",
    }
