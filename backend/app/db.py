from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Dict, List, Optional, Sequence

import httpx

try:
    import psycopg
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except Exception:
    psycopg = None
    AsyncConnection = Any
    dict_row = None
    Jsonb = None


def _database_url() -> Optional[str]:
    return os.getenv("DATABASE_URL") or os.getenv("APP_DATABASE_URL")


class WorkerDb:
    def __init__(self, *, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _headers(self) -> Dict[str, str]:
        return {"x-internal-token": self.token}


@asynccontextmanager
async def open_db() -> AsyncIterator[Optional[AsyncConnection]]:
    url = _database_url()
    if not url or psycopg is None:
        worker_base_url = (os.getenv("WORKER_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "").strip()
        internal_token = (os.getenv("INTERNAL_API_TOKEN") or "").strip()
        if worker_base_url and internal_token:
            yield WorkerDb(base_url=worker_base_url, token=internal_token)
            return

        yield None
        return

    conn: Optional[AsyncConnection] = None
    try:
        conn = await psycopg.AsyncConnection.connect(url, row_factory=dict_row)
        await _ensure_schema(conn)
    except Exception:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass
        yield None
        return

    try:
        yield conn
    finally:
        try:
            await conn.close()
        except Exception:
            pass


async def _ensure_schema(conn: AsyncConnection) -> None:
    async with conn.cursor() as cur:
        await cur.execute(
            """
            CREATE TABLE IF NOT EXISTS dish_knowledge (
                dish_key TEXT NOT NULL,
                language TEXT NOT NULL,
                translated_name TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                romanji TEXT NOT NULL DEFAULT '',
                seen_count INTEGER NOT NULL DEFAULT 0,
                last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                source_scan_id TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (dish_key, language)
            );
            """
        )
        await cur.execute(
            """
            CREATE TABLE IF NOT EXISTS scan_records (
                scan_id TEXT PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                image_hash_sha256 TEXT NOT NULL,
                language TEXT NOT NULL,
                items JSONB NOT NULL DEFAULT '[]'::jsonb
            );
            """
        )
        await cur.execute("CREATE INDEX IF NOT EXISTS idx_scan_records_hash ON scan_records(image_hash_sha256);")

    await conn.commit()


async def fetch_dish_knowledge(
    conn: AsyncConnection,
    *,
    dish_keys: Sequence[str],
    language: str,
) -> Dict[str, Dict[str, Any]]:
    if isinstance(conn, WorkerDb):
        keys = [k for k in dish_keys if k]
        if not keys or not language:
            return {}
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{conn.base_url}/internal/dish_knowledge/fetch",
                headers=conn._headers(),
                json={"dish_keys": list(keys), "language": language},
            )
            resp.raise_for_status()
            data = resp.json() if resp.content else {}
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for dish_key, v in items.items():
            if not isinstance(dish_key, str) or not dish_key:
                continue
            if not isinstance(v, dict):
                continue
            out[dish_key] = {
                "dish_key": dish_key,
                "translated_name": str(v.get("translated_name") or ""),
                "description": str(v.get("description") or ""),
                "tags": list(v.get("tags") or []),
                "romanji": str(v.get("romanji") or ""),
                "seen_count": int(v.get("seen_count") or 0),
            }
        return out

    keys = [k for k in dish_keys if k]
    if not keys:
        return {}

    async with conn.cursor() as cur:
        await cur.execute(
            """
            SELECT dish_key, translated_name, description, tags, romanji, seen_count
            FROM dish_knowledge
            WHERE language = %s AND dish_key = ANY(%s)
            """,
            (language, keys),
        )
        rows = await cur.fetchall()

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        dish_key = str(r.get("dish_key") or "")
        if not dish_key:
            continue
        out[dish_key] = {
            "dish_key": dish_key,
            "translated_name": str(r.get("translated_name") or ""),
            "description": str(r.get("description") or ""),
            "tags": list(r.get("tags") or []),
            "romanji": str(r.get("romanji") or ""),
            "seen_count": int(r.get("seen_count") or 0),
        }

    return out


async def upsert_dish_knowledge_many(
    conn: AsyncConnection,
    *,
    rows: Sequence[Dict[str, Any]],
    language: str,
    source_scan_id: str,
) -> None:
    if isinstance(conn, WorkerDb):
        if not rows or not language:
            return
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{conn.base_url}/internal/dish_knowledge/upsert_many",
                headers=conn._headers(),
                json={"rows": list(rows), "language": language, "source_scan_id": source_scan_id},
            )
            resp.raise_for_status()
        return

    values: List[tuple[Any, ...]] = []
    for r in rows:
        dish_key = str(r.get("dish_key") or "")
        if not dish_key:
            continue
        translated_name = str(r.get("translated_name") or "")
        description = str(r.get("description") or "")
        tags_raw = r.get("tags") or []
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
        romanji = str(r.get("romanji") or "")
        values.append(
            (
                dish_key,
                language,
                translated_name,
                description,
                Jsonb(tags),
                romanji,
                source_scan_id,
            )
        )

    if not values:
        return

    async with conn.cursor() as cur:
        await cur.executemany(
            """
            INSERT INTO dish_knowledge (
                dish_key,
                language,
                translated_name,
                description,
                tags,
                romanji,
                seen_count,
                last_seen_at,
                source_scan_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, 1, NOW(), %s)
            ON CONFLICT (dish_key, language)
            DO UPDATE SET
                translated_name = CASE
                    WHEN dish_knowledge.translated_name = '' THEN EXCLUDED.translated_name
                    ELSE dish_knowledge.translated_name
                END,
                description = CASE
                    WHEN dish_knowledge.description = '' THEN EXCLUDED.description
                    ELSE dish_knowledge.description
                END,
                tags = CASE
                    WHEN dish_knowledge.tags = '[]'::jsonb THEN EXCLUDED.tags
                    ELSE dish_knowledge.tags
                END,
                romanji = CASE
                    WHEN dish_knowledge.romanji = '' THEN EXCLUDED.romanji
                    ELSE dish_knowledge.romanji
                END,
                seen_count = dish_knowledge.seen_count + 1,
                last_seen_at = NOW(),
                source_scan_id = CASE
                    WHEN EXCLUDED.source_scan_id = '' THEN dish_knowledge.source_scan_id
                    ELSE EXCLUDED.source_scan_id
                END
            """,
            values,
        )

    await conn.commit()


async def insert_scan_record(
    conn: AsyncConnection,
    *,
    scan_id: str,
    image_hash_sha256: str,
    language: str,
    items: Sequence[Dict[str, Any]],
) -> None:
    if isinstance(conn, WorkerDb):
        if not scan_id or not language:
            return
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{conn.base_url}/internal/scan_records/insert",
                headers=conn._headers(),
                json={
                    "scan_id": scan_id,
                    "image_hash_sha256": image_hash_sha256,
                    "language": language,
                    "items": list(items),
                },
            )
            resp.raise_for_status()
        return

    async with conn.cursor() as cur:
        await cur.execute(
            """
            INSERT INTO scan_records (scan_id, image_hash_sha256, language, items)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (scan_id) DO NOTHING
            """,
            (
                scan_id,
                image_hash_sha256,
                language,
                Jsonb(list(items)),
            ),
        )

    await conn.commit()
