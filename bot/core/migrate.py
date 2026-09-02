import asyncio
import logging
import pathlib
import re

import asyncpg

from bot.config import DATABASE_URL

log = logging.getLogger(__name__)

MIGRATIONS_DIR = pathlib.Path(__file__).resolve().parents[2] / "migrations"

SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$")


def split_statements(sql: str) -> list[str]:
    statements = []
    buf = []
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            while i < n and sql[i] != "\n":
                buf.append(sql[i])
                i += 1
            continue

        if ch == "/" and i + 1 < n and sql[i + 1] == "*":
            end = sql.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue

        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        buf.append(quote * 2)
                        i += 2
                        continue
                    buf.append(quote)
                    i += 1
                    break
                buf.append(sql[i])
                i += 1
            continue

        if ch == "$":
            match = DOLLAR_QUOTE.match(sql[i:])
            if match:
                tag = match.group()
                end = sql.find(tag, i + len(tag))
                if end != -1:
                    buf.append(sql[i:end + len(tag)])
                    i = end + len(tag)
                    continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    stmt = "".join(buf).strip()
    if stmt:
        statements.append(stmt)
    return statements


async def run_migrations(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_MIGRATIONS)
        applied = {
            row["filename"]
            for row in await conn.fetch("SELECT filename FROM schema_migrations")
        }
        pending = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.name not in applied)

        for path in pending:
            sql = path.read_text(encoding="utf-8")
            try:
                async with conn.transaction():
                    for stmt in split_statements(sql):
                        await conn.execute(stmt)
                    await conn.execute(
                        "INSERT INTO schema_migrations (filename) VALUES ($1)",
                        path.name,
                    )
            except Exception as exc:
                log.error("Falha ao aplicar migration %s: %s", path.name, exc)
                raise
            log.info("Migration aplicada: %s", path.name)

    if pending:
        log.info("%d migrations pendentes aplicadas", len(pending))
    else:
        log.info("Nenhuma migration pendente, banco já atualizado")
    return len(pending)


async def _migrate_standalone() -> None:
    pool = await asyncpg.create_pool(DATABASE_URL)
    try:
        await run_migrations(pool)
    finally:
        await pool.close()


def main() -> None:
    from bot.config import ENVIRONMENT
    from bot.core.logging import setup_logging

    setup_logging(environment=ENVIRONMENT, level="INFO")
    asyncio.run(_migrate_standalone())


if __name__ == "__main__":
    main()
