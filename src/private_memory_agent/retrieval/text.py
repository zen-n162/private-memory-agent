"""Local text indexing and retrieval without embeddings."""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from private_memory_agent.storage import initialize_database
from private_memory_agent.storage.repositories import utc_now


@dataclass(frozen=True)
class TextIndexResult:
    """Summary-only indexing result."""

    documents_indexed: int
    fts5_enabled: bool


@dataclass(frozen=True)
class TextSearchResult:
    """Structured text search result."""

    source_table: str
    source_id: int
    title: str | None
    snippet: str
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_table": self.source_table,
            "source_id": self.source_id,
            "title": self.title,
            "snippet": self.snippet,
            "score": self.score,
        }


@dataclass(frozen=True)
class TextSearchDiagnostics:
    """Aggregate text-search stage counts with no query or snippet payloads."""

    fts_candidate_count: int
    exact_like_candidate_count: int
    keyword_like_candidate_count: int
    final_candidate_count: int
    query_term_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "fts_candidate_count": self.fts_candidate_count,
            "exact_like_candidate_count": self.exact_like_candidate_count,
            "keyword_like_candidate_count": self.keyword_like_candidate_count,
            "final_candidate_count": self.final_candidate_count,
            "query_term_count": self.query_term_count,
        }


def index_text(db_path: Path | str) -> TextIndexResult:
    """Rebuild the local text index from LINE messages, notes, and photo annotations."""

    storage = initialize_database(db_path)
    try:
        connection = storage.connection
        fts5_enabled = ensure_fts5(connection)
        documents = _collect_documents(connection)
        now = utc_now()

        with storage.transaction():
            connection.execute("DELETE FROM text_search_documents")
            if fts5_enabled:
                connection.execute("DELETE FROM text_search_fts")

            for document in documents:
                cursor = connection.execute(
                    """
                    INSERT INTO text_search_documents(
                        source_table,
                        source_id,
                        title,
                        body,
                        normalized_text,
                        snippet_text,
                        is_excluded,
                        indexed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        document["source_table"],
                        document["source_id"],
                        document["title"],
                        document["body"],
                        document["normalized_text"],
                        document["snippet_text"],
                        now,
                    ),
                )
                if fts5_enabled:
                    connection.execute(
                        """
                        INSERT INTO text_search_fts(
                            rowid,
                            source_table,
                            source_id,
                            title,
                            body,
                            normalized_text
                        )
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            cursor.lastrowid,
                            document["source_table"],
                            document["source_id"],
                            document["title"],
                            document["body"],
                            document["normalized_text"],
                        ),
                    )

            _update_source_normalized_text(connection)

        return TextIndexResult(documents_indexed=len(documents), fts5_enabled=fts5_enabled)
    finally:
        storage.close()


def search_text(
    db_path: Path | str,
    query: str,
    *,
    limit: int = 10,
    ensure_fts: bool = True,
    source_tables: tuple[str, ...] = (),
) -> list[TextSearchResult]:
    """Search indexed local text documents."""

    results, _diagnostics = _search_text_internal(
        db_path,
        query,
        limit=limit,
        ensure_fts=ensure_fts,
        source_tables=source_tables,
    )
    return results


def diagnose_text_search(
    db_path: Path | str,
    query: str,
    *,
    limit: int = 10,
    ensure_fts: bool = True,
    source_tables: tuple[str, ...] = (),
) -> TextSearchDiagnostics:
    """Return aggregate text-search stage counts without exposing query text."""

    _results, diagnostics = _search_text_internal(
        db_path,
        query,
        limit=limit,
        ensure_fts=ensure_fts,
        source_tables=source_tables,
    )
    return diagnostics


def _search_text_internal(
    db_path: Path | str,
    query: str,
    *,
    limit: int,
    ensure_fts: bool,
    source_tables: tuple[str, ...],
) -> tuple[list[TextSearchResult], TextSearchDiagnostics]:
    """Search indexed documents and keep privacy-safe stage counts."""

    normalized_query = normalize_text(query)
    empty_diagnostics = TextSearchDiagnostics(
        fts_candidate_count=0,
        exact_like_candidate_count=0,
        keyword_like_candidate_count=0,
        final_candidate_count=0,
        query_term_count=0,
    )
    if not normalized_query:
        return [], empty_diagnostics

    storage = initialize_database(db_path)
    try:
        connection = storage.connection
        if not _table_exists(connection, "text_search_documents"):
            return [], empty_diagnostics
        fts5_enabled = ensure_fts5(connection) if ensure_fts else _table_exists(connection, "text_search_fts")
        seen: set[tuple[str, int]] = set()
        results: list[TextSearchResult] = []
        fts_candidate_count = 0
        exact_like_candidate_count = 0
        keyword_like_candidate_count = 0
        source_tables = _clean_source_tables(source_tables)

        if fts5_enabled:
            rows = _search_fts(
                connection,
                normalized_query,
                limit=limit,
                source_tables=source_tables,
            )
            fts_candidate_count = len(rows)
            for row in rows:
                result = _row_to_result(row, normalized_query, score=1.0)
                key = (result.source_table, result.source_id)
                if key not in seen:
                    seen.add(key)
                    results.append(result)

        rows = _search_like(
            connection,
            normalized_query,
            limit=limit,
            source_tables=source_tables,
        )
        exact_like_candidate_count = len(rows)
        for row in rows:
            result = _row_to_result(row, normalized_query, score=0.5)
            key = (result.source_table, result.source_id)
            if key not in seen:
                seen.add(key)
                results.append(result)
            if len(results) >= limit:
                break

        terms = extract_query_terms(normalized_query)
        if len(results) < limit and terms:
            rows = _search_keyword_like(
                connection,
                terms,
                limit=limit,
                source_tables=source_tables,
            )
            keyword_like_candidate_count = len(rows)
            for row in rows:
                result = _row_to_result(row, _best_snippet_query(normalized_query, terms, row), score=0.35)
                key = (result.source_table, result.source_id)
                if key not in seen:
                    seen.add(key)
                    results.append(result)
                if len(results) >= limit:
                    break

        clipped = results[:limit]
        diagnostics = TextSearchDiagnostics(
            fts_candidate_count=fts_candidate_count,
            exact_like_candidate_count=exact_like_candidate_count,
            keyword_like_candidate_count=keyword_like_candidate_count,
            final_candidate_count=len(clipped),
            query_term_count=len(terms),
        )
        return clipped, diagnostics
    finally:
        storage.close()


def ensure_fts5(connection: sqlite3.Connection) -> bool:
    """Create the optional FTS5 table if SQLite supports it."""

    try:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS text_search_fts USING fts5(
                source_table UNINDEXED,
                source_id UNINDEXED,
                title,
                body,
                normalized_text,
                tokenize='unicode61'
            )
            """,
        )
    except sqlite3.OperationalError:
        return False
    connection.commit()
    return True


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type IN ('table', 'virtual table')
          AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def make_snippet(text: str | None, query: str, *, max_chars: int = 96) -> str:
    """Return a clipped, whitespace-normalized snippet for explicit search output."""

    cleaned = re.sub(r"\s+", " ", text or "").strip()
    if not cleaned:
        return ""
    normalized_cleaned = normalize_text(cleaned)
    index = normalized_cleaned.find(query)
    if index == -1:
        snippet = cleaned[:max_chars]
        return snippet + ("..." if len(cleaned) > max_chars else "")

    start = max(0, index - max_chars // 3)
    end = min(len(cleaned), index + len(query) + max_chars // 2)
    snippet = cleaned[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(cleaned):
        snippet += "..."
    return snippet


_QUERY_SPLIT_PATTERN = re.compile(
    r"[\s,.;:!?！？。、，．・/\\|()\[\]{}「」『』【】<>＜＞\"'`]+",
)
_JAPANESE_BREAK_PATTERN = re.compile(
    r"(?:について|に関係しそう|関係しそう|してください|して下さい|して|した|する|"
    r"ありますか|ですか|でした|です|ます|ました|だった|から|まで|として|による|"
    r"場合|最近|記録|探|答|説明|写真|画像|メモ|ノート|ライン|line|LINE|"
    r"の|を|に|が|は|へ|で|と|や|も|か|な|ね|よ)"
)
_QUERY_STOPWORDS = {
    "line",
    "LINE",
    "ライン",
    "メモ",
    "ノート",
    "写真",
    "画像",
    "説明",
    "記録",
    "最近",
    "場合",
    "探",
    "答",
    "してください",
    "して下さい",
}


def extract_query_terms(query: str | None, *, max_terms: int = 8) -> tuple[str, ...]:
    """Extract coarse search terms for Japanese-friendly LIKE fallback.

    This is intentionally simple and deterministic. It is not a morphological
    analyzer; it only recovers useful smoke-test keywords from full Japanese
    questions such as "研究に関係しそうな記録を探してください".
    """

    normalized = normalize_text(query)
    if not normalized:
        return ()
    raw_parts: list[str] = []
    for chunk in _QUERY_SPLIT_PATTERN.split(normalized):
        chunk = chunk.strip()
        if not chunk:
            continue
        raw_parts.append(chunk)
        raw_parts.extend(part for part in _JAPANESE_BREAK_PATTERN.split(chunk) if part)

    terms: list[str] = []
    for part in raw_parts:
        clean = part.strip()
        if not clean or clean in _QUERY_STOPWORDS:
            continue
        if len(clean) < 2 and not re.fullmatch(r"[a-z0-9]+", clean):
            continue
        if clean not in terms:
            terms.append(clean)
        if len(terms) >= max_terms:
            break
    return tuple(terms)


def media_annotation_search_text(value_text: str | None, data_json: str | None) -> str:
    """Return local-only annotation text used for indexing and retrieval."""

    parts = [value_text or ""]
    try:
        data = json_loads_object(data_json)
    except ValueError:
        data = {}
    if isinstance(data, dict):
        for key in (
            "caption",
            "summary",
            "ocr_text",
            "text",
            "event_hint",
            "event_hints",
            "objects",
            "object_tags",
            "topics",
            "dates",
            "action_items",
            "place_candidates",
            "people_candidates",
        ):
            _append_json_text(parts, data.get(key))
    return " ".join(part for part in parts if part)


def json_loads_object(value: str | None) -> dict[str, Any]:
    import json

    data = json.loads(value or "{}")
    if not isinstance(data, dict):
        raise ValueError("annotation JSON is not an object")
    return data


def _append_json_text(parts: list[str], value: object) -> None:
    if isinstance(value, str):
        parts.append(value)
    elif isinstance(value, (int, float)):
        parts.append(str(value))
    elif isinstance(value, list):
        for item in value:
            _append_json_text(parts, item)
    elif isinstance(value, dict):
        for item in value.values():
            _append_json_text(parts, item)


def _collect_documents(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for row in connection.execute(
        """
        SELECT id, sender_id, body_text
        FROM line_messages
        WHERE is_excluded = 0 AND COALESCE(body_text, '') != ''
        ORDER BY id
        """,
    ).fetchall():
        title = row["sender_id"]
        body = row["body_text"] or ""
        documents.append(
            {
                "source_table": "line_messages",
                "source_id": int(row["id"]),
                "title": title,
                "body": body,
                "normalized_text": normalize_text(f"{title or ''} {body}"),
                "snippet_text": body,
            },
        )

    for row in connection.execute(
        """
        SELECT id, title, body_text
        FROM notes
        WHERE is_excluded = 0
          AND (COALESCE(title, '') != '' OR COALESCE(body_text, '') != '')
        ORDER BY id
        """,
    ).fetchall():
        title = row["title"]
        body = row["body_text"] or ""
        documents.append(
            {
                "source_table": "notes",
                "source_id": int(row["id"]),
                "title": title,
                "body": body,
                "normalized_text": normalize_text(f"{title or ''} {body}"),
                "snippet_text": f"{title or ''} {body}".strip(),
            },
        )

    annotation_parts: dict[int, list[str]] = {}
    for row in connection.execute(
        """
        SELECT m.id AS media_item_id,
               a.value_text,
               a.data_json
        FROM media_items m
        JOIN media_annotations a ON a.media_item_id = m.id
        WHERE m.is_excluded = 0
          AND a.is_excluded = 0
          AND a.annotation_type = 'vision'
        ORDER BY m.id, a.id
        """,
    ).fetchall():
        text = media_annotation_search_text(row["value_text"], row["data_json"])
        if text:
            annotation_parts.setdefault(int(row["media_item_id"]), []).append(text)
    for media_item_id, parts in annotation_parts.items():
        body = " ".join(parts)
        documents.append(
            {
                "source_table": "media_items",
                "source_id": media_item_id,
                "title": None,
                "body": body,
                "normalized_text": normalize_text(body),
                "snippet_text": body,
            },
        )
    return documents


def _update_source_normalized_text(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE line_messages
        SET normalized_text = (
            SELECT normalized_text
            FROM text_search_documents
            WHERE source_table = 'line_messages'
              AND source_id = line_messages.id
        )
        WHERE is_excluded = 0
        """,
    )
    connection.execute(
        """
        UPDATE notes
        SET normalized_text = (
            SELECT normalized_text
            FROM text_search_documents
            WHERE source_table = 'notes'
              AND source_id = notes.id
        )
        WHERE is_excluded = 0
        """,
    )


def _search_fts(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    source_tables: tuple[str, ...],
):
    phrase = '"' + query.replace('"', '""') + '"'
    source_clause, source_params = _source_table_filter("d", source_tables)
    try:
        return connection.execute(
            f"""
            SELECT d.*
            FROM text_search_fts f
            JOIN text_search_documents d ON d.id = f.rowid
            WHERE text_search_fts MATCH ?
              AND d.is_excluded = 0
              {source_clause}
            ORDER BY d.id
            LIMIT ?
            """,
            (phrase, *source_params, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _search_like(
    connection: sqlite3.Connection,
    query: str,
    *,
    limit: int,
    source_tables: tuple[str, ...],
):
    pattern = f"%{_escape_like(query)}%"
    source_clause, source_params = _source_table_filter(None, source_tables)
    return connection.execute(
        f"""
        SELECT *
        FROM text_search_documents
        WHERE is_excluded = 0
          AND normalized_text LIKE ? ESCAPE '\\'
          {source_clause}
        ORDER BY id
        LIMIT ?
        """,
        (pattern, *source_params, limit),
    ).fetchall()


def _search_keyword_like(
    connection: sqlite3.Connection,
    terms: tuple[str, ...],
    *,
    limit: int,
    source_tables: tuple[str, ...],
):
    if not terms:
        return []
    clauses = ["normalized_text LIKE ? ESCAPE '\\'" for _term in terms]
    params = [f"%{_escape_like(term)}%" for term in terms]
    source_clause, source_params = _source_table_filter(None, source_tables)
    return connection.execute(
        f"""
        SELECT *
        FROM text_search_documents
        WHERE is_excluded = 0
          {source_clause}
          AND ({" OR ".join(clauses)})
        ORDER BY id
        LIMIT ?
        """,
        (*source_params, *params, limit),
    ).fetchall()


def _row_to_result(row: sqlite3.Row, query: str, *, score: float) -> TextSearchResult:
    snippet_source = row["snippet_text"] or row["body"] or row["title"] or ""
    return TextSearchResult(
        source_table=str(row["source_table"]),
        source_id=int(row["source_id"]),
        title=row["title"],
        snippet=make_snippet(snippet_source, query),
        score=score,
    )


def _best_snippet_query(
    normalized_query: str,
    terms: tuple[str, ...],
    row: sqlite3.Row,
) -> str:
    normalized_text = str(row["normalized_text"] or "")
    if normalized_query in normalized_text:
        return normalized_query
    for term in terms:
        if term in normalized_text:
            return term
    return terms[0] if terms else normalized_query


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _clean_source_tables(source_tables: tuple[str, ...]) -> tuple[str, ...]:
    allowed = {"line_messages", "notes", "media_items"}
    cleaned: list[str] = []
    for table in source_tables:
        if table not in allowed or table in cleaned:
            continue
        cleaned.append(table)
    return tuple(cleaned)


def _source_table_filter(
    table_alias: str | None,
    source_tables: tuple[str, ...],
) -> tuple[str, tuple[str, ...]]:
    if not source_tables:
        return "", ()
    column = f"{table_alias}.source_table" if table_alias else "source_table"
    placeholders = ",".join("?" for _table in source_tables)
    return f"AND {column} IN ({placeholders})", source_tables
