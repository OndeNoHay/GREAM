"""
s1000d_csdb — MCP server para consultar la base de conocimiento GRAEM.

Expone las bases Kùzu (grafo) y ChromaDB (vectores) como herramientas MCP
orientadas a S1000D: búsqueda técnica híbrida, travesía de grafo de entidades,
estadísticas de librería y consultas Cypher de sólo lectura.

Acceso: READ-ONLY en ambas bases para coexistir con el proceso FastAPI.
Transporte: stdio (FastMCP).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

import chromadb
import kuzu
from chromadb.config import Settings as ChromaSettings
from fastmcp import FastMCP
from mcp.types import ToolAnnotations

logger = logging.getLogger(__name__)
mcp = FastMCP("s1000d_csdb")

# ---------------------------------------------------------------------------
# Lazy DB singletons — inicializados en primera llamada a _dbs()
# ---------------------------------------------------------------------------

_kuzu_db: Optional[kuzu.Database] = None
_kuzu_conn: Optional[kuzu.Connection] = None
_chroma: Optional[chromadb.ClientAPI] = None


def _dbs() -> tuple[kuzu.Connection, Optional[chromadb.ClientAPI]]:
    """Devuelve (kuzu_conn, chroma_client), inicializando en la primera llamada."""
    global _kuzu_db, _kuzu_conn, _chroma

    if _kuzu_conn is not None:
        return _kuzu_conn, _chroma

    from app.config import ensure_data_directories, get_app_settings

    dirs = ensure_data_directories()

    try:
        buf_size = get_app_settings().kuzu_buffer_pool_size
    except Exception:
        buf_size = 0

    # Intentar READ-ONLY para no interferir con el proceso FastAPI.
    # Si falla (e.g. Kùzu < 0.4 con locking estricto), abrir sin flag.
    try:
        _kuzu_db = kuzu.Database(
            str(dirs["graph_db"]),
            buffer_pool_size=buf_size,
            read_only=True,
        )
        _kuzu_conn = kuzu.Connection(_kuzu_db)
        logger.info("s1000d_csdb: Kùzu abierto en modo read-only")
    except Exception as exc:
        logger.warning("s1000d_csdb: read-only falló (%s), reintentando sin flag", exc)
        _kuzu_db = kuzu.Database(str(dirs["graph_db"]), buffer_pool_size=buf_size)
        _kuzu_conn = kuzu.Connection(_kuzu_db)

    try:
        _chroma = chromadb.PersistentClient(
            path=str(dirs["vector_db"]),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        logger.info("s1000d_csdb: ChromaDB abierto")
    except Exception as exc:
        logger.warning("s1000d_csdb: ChromaDB no disponible: %s", exc)

    return _kuzu_conn, _chroma


def _init_for_testing(
    kuzu_conn: kuzu.Connection,
    chroma_client: Optional[chromadb.ClientAPI] = None,
) -> None:
    """Inyecta conexiones pre-configuradas para unit tests."""
    global _kuzu_conn, _chroma
    _kuzu_conn = kuzu_conn
    _chroma = chroma_client


# ---------------------------------------------------------------------------
# Helpers internos (importables en tests)
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "be", "been", "have", "has",
    "had", "do", "does", "did", "will", "would", "could", "should", "to",
    "of", "in", "for", "on", "with", "at", "by", "from", "as", "and",
    "or", "but", "not", "so", "if", "this", "that", "how", "what",
    "which", "who", "when", "where", "why", "about", "after", "before",
})


def _extract_keywords(query: str) -> list[str]:
    """Extrae keywords significativas del query (sin stop-words)."""
    # Primero: identificadores técnicos (D-260, S3A, 719-73-08…)
    technical = re.findall(
        r"\b[A-Za-z]{1,4}[-–][0-9A-Za-z][-0-9A-Za-z]*\b"
        r"|\b[0-9]+(?:[-–][0-9]+)+\b"
        r"|\b[A-Za-z]+[0-9]+[A-Za-z0-9]*\b"
        r"|\b[0-9]+[A-Za-z][A-Za-z0-9]*\b",
        query,
    )
    words = [w for w in re.findall(r"\b[a-zA-Z]{3,}\b", query.lower())
             if w not in _STOP_WORDS]
    seen: set[str] = set()
    result: list[str] = []
    for k in [t.lower() for t in technical] + words:
        if k not in seen:
            seen.add(k)
            result.append(k)
    return result


def _find_entity_ids(
    conn: kuzu.Connection,
    safe_lib: str,
    keywords: list[str],
) -> list[tuple[str, str]]:
    """Devuelve [(entity_id, entity_name)] que contienen algún keyword."""
    entities: list[tuple[str, str]] = []
    seen: set[str] = set()
    for kw in keywords[:8]:
        safe_kw = kw.replace("'", "\\'")
        try:
            r = conn.execute(
                f"MATCH (e:Entity) "
                f"WHERE e.library_id = '{safe_lib}' "
                f"AND lower(e.name) CONTAINS '{safe_kw}' "
                f"RETURN e.id, e.name LIMIT 5"
            )
            while r.has_next():
                eid, ename = r.get_next()
                if eid not in seen:
                    seen.add(eid)
                    entities.append((eid, ename))
        except Exception:
            pass
    return entities


def _docs_for_entities(
    conn: kuzu.Connection,
    safe_lib: str,
    entity_tuples: list[tuple[str, str]],
    top_k: int,
) -> list[dict]:
    """Devuelve chunks de documento conectados a las entidades dadas."""
    results: list[dict] = []
    seen_chunks: set[str] = set()
    for entity_id, entity_name in entity_tuples[:20]:
        try:
            r = conn.execute(
                f"MATCH (d:Document)-[:HAS_ENTITY]->(e:Entity) "
                f"WHERE d.library_id = '{safe_lib}' "
                f"AND e.id = '{entity_id}' "
                f"RETURN DISTINCT d.id, d.source_file, d.page, d.chunk_index "
                f"LIMIT {top_k}"
            )
            while r.has_next():
                row = r.get_next()
                chunk_id = row[0]
                if chunk_id not in seen_chunks:
                    seen_chunks.add(chunk_id)
                    results.append({
                        "chunk_id": chunk_id,
                        "source": "graph",
                        "score": 0.9,
                        "source_file": row[1],
                        "page": row[2],
                        "matched_entity": entity_name,
                        "text_snippet": "",
                    })
        except Exception:
            pass
        if len(results) >= top_k:
            break
    return results


def _safe_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


# ---------------------------------------------------------------------------
# Herramientas MCP
# ---------------------------------------------------------------------------

@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def list_libraries() -> str:
    """
    List available libraries (datasets) in the GRAEM knowledge base.

    Returns JSON with library IDs and document/entity counts.
    """
    conn, _ = _dbs()
    safe_lib_query = "MATCH (d:Document) RETURN DISTINCT d.library_id ORDER BY d.library_id"
    try:
        r = conn.execute(safe_lib_query)
        libraries = []
        while r.has_next():
            lib_id = r.get_next()[0]
            if lib_id:
                libraries.append(lib_id)
        return json.dumps({"libraries": libraries, "count": len(libraries)})
    except Exception as exc:
        return json.dumps({"libraries": [], "error": str(exc)})


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_library_stats(library_id: str) -> str:
    """
    Get document chunk, entity, and vector counts for a GRAEM library.

    Use list_libraries() first to discover available library IDs.
    """
    conn, chroma = _dbs()
    safe_lib = _safe_escape(library_id)
    stats: dict = {"library_id": library_id}

    try:
        r = conn.execute(
            f"MATCH (d:Document) WHERE d.library_id = '{safe_lib}' RETURN count(d)"
        )
        stats["document_chunks"] = r.get_next()[0] if r.has_next() else 0
    except Exception as exc:
        stats["document_chunks_error"] = str(exc)

    try:
        r = conn.execute(
            f"MATCH (e:Entity) WHERE e.library_id = '{safe_lib}' RETURN count(e)"
        )
        stats["entities"] = r.get_next()[0] if r.has_next() else 0
    except Exception as exc:
        stats["entities_error"] = str(exc)

    if chroma is not None:
        try:
            col_name = f"lib_{library_id.replace('-', '_')}"
            col = chroma.get_collection(col_name)
            stats["vector_chunks"] = col.count()
        except Exception:
            stats["vector_chunks"] = 0

    return json.dumps(stats)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def search_technical_content(
    query: str,
    library_id: str,
    top_k: int = 5,
    mode: str = "hybrid",
) -> str:
    """
    Search the GRAEM knowledge base for S1000D technical content.

    mode options:
      "graph"  — entity keyword matching on the Kùzu graph
      "vector" — semantic similarity using ChromaDB + Ollama embeddings
      "hybrid" — graph + vector combined (default, recommended)

    Returns JSON with ranked results including text snippets and source files.
    """
    conn, chroma = _dbs()
    safe_lib = _safe_escape(library_id)
    results: list[dict] = []
    errors: list[str] = []

    # --- Graph search ---
    if mode in ("graph", "hybrid"):
        keywords = _extract_keywords(query)
        entity_tuples = _find_entity_ids(conn, safe_lib, keywords)
        results.extend(_docs_for_entities(conn, safe_lib, entity_tuples, top_k))

    # --- Vector search ---
    if mode in ("vector", "hybrid") and chroma is not None:
        try:
            from app.services.ai_client import get_ai_client

            embedding = get_ai_client().generate_embedding(query)
            col_name = f"lib_{library_id.replace('-', '_')}"
            col = chroma.get_collection(col_name)
            raw = col.query(
                query_embeddings=[embedding],
                n_results=min(top_k, 100),
                include=["metadatas", "distances", "documents"],
            )
            for i, chunk_id in enumerate(raw["ids"][0]):
                dist = raw["distances"][0][i] if raw.get("distances") else 1.0
                score = max(0.0, 1.0 - dist / 2.0)
                meta = raw["metadatas"][0][i] if raw.get("metadatas") else {}
                text = raw["documents"][0][i] if raw.get("documents") else ""
                results.append({
                    "chunk_id": chunk_id,
                    "source": "vector",
                    "score": round(score, 4),
                    "source_file": meta.get("source_file", ""),
                    "page": meta.get("page"),
                    "text_snippet": text[:500] if text else "",
                })
        except Exception as exc:
            errors.append(f"vector_search_error: {exc}")

    # --- Deduplicación: mantener el score más alto por chunk ---
    seen: dict[str, dict] = {}
    for r in results:
        cid = r["chunk_id"]
        if cid not in seen or r["score"] > seen[cid]["score"]:
            seen[cid] = r

    final = sorted(seen.values(), key=lambda x: x["score"], reverse=True)[:top_k]

    # Enriquecer resultados de grafo con texto de ChromaDB
    if chroma is not None:
        graph_ids = [r["chunk_id"] for r in final if r.get("source") == "graph" and not r.get("text_snippet")]
        if graph_ids:
            try:
                col_name = f"lib_{library_id.replace('-', '_')}"
                col = chroma.get_collection(col_name)
                chunks = col.get(ids=graph_ids, include=["documents"])
                id_to_text = {}
                if chunks and chunks.get("ids") and chunks.get("documents"):
                    for cid, doc in zip(chunks["ids"], chunks["documents"]):
                        id_to_text[cid] = (doc or "")[:500]
                for r in final:
                    if r["chunk_id"] in id_to_text:
                        r["text_snippet"] = id_to_text[r["chunk_id"]]
            except Exception:
                pass

    return json.dumps({
        "query": query,
        "library_id": library_id,
        "mode": mode,
        "total_results": len(final),
        "results": final,
        "errors": errors if errors else None,
    }, ensure_ascii=False)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_entity_relationships(entity_name: str, library_id: str) -> str:
    """
    Get typed relationships for an entity from the Kùzu knowledge graph.

    Searches for entities whose name contains entity_name (case-insensitive).
    Returns both outgoing and incoming relationships plus chunk co-occurrences.
    """
    conn, _ = _dbs()
    safe_lib = _safe_escape(library_id)
    safe_name = _safe_escape(entity_name.lower())

    try:
        r = conn.execute(
            f"MATCH (e:Entity) "
            f"WHERE e.library_id = '{safe_lib}' "
            f"AND lower(e.name) CONTAINS '{safe_name}' "
            f"RETURN e.id, e.name, e.entity_type LIMIT 1"
        )
        if not r.has_next():
            return json.dumps({"entity": entity_name, "found": False, "relationships": []})

        entity_id, found_name, entity_type = r.get_next()
    except Exception as exc:
        return json.dumps({"entity": entity_name, "found": False, "error": str(exc)})

    rel_types = [
        "PART_OF", "CONTROLS", "SUPPLIES_TO", "CONNECTS_TO",
        "REQUIRES", "TRIGGERS", "PRECEDES", "CO_OCCURS", "IS_A", "HAS_PROPERTY",
    ]
    relationships: list[dict] = []

    for rel_type in rel_types:
        # Salientes (entity → other)
        try:
            r = conn.execute(
                f"MATCH (a:Entity {{id: '{entity_id}'}})-[:{rel_type}]->(b:Entity) "
                f"WHERE b.library_id = '{safe_lib}' "
                f"RETURN b.name, b.entity_type LIMIT 10"
            )
            while r.has_next():
                row = r.get_next()
                relationships.append({
                    "direction": "outgoing",
                    "type": rel_type,
                    "target_name": row[0],
                    "target_type": row[1],
                })
        except Exception:
            pass

        # Entrantes (other → entity)
        try:
            r = conn.execute(
                f"MATCH (a:Entity)-[:{rel_type}]->(b:Entity {{id: '{entity_id}'}}) "
                f"WHERE a.library_id = '{safe_lib}' "
                f"RETURN a.name, a.entity_type LIMIT 10"
            )
            while r.has_next():
                row = r.get_next()
                relationships.append({
                    "direction": "incoming",
                    "type": rel_type,
                    "source_name": row[0],
                    "source_type": row[1],
                })
        except Exception:
            pass

    # Fallback: co-ocurrencia por chunk (siempre útil cuando hay pocos arcos tipados)
    if len(relationships) < 5:
        try:
            r = conn.execute(
                f"MATCH (d:Document)-[:HAS_ENTITY]->(focal:Entity) "
                f"WHERE focal.id = '{entity_id}' "
                f"MATCH (d)-[:HAS_ENTITY]->(other:Entity) "
                f"WHERE other.id <> '{entity_id}' "
                f"AND other.library_id = '{safe_lib}' "
                f"RETURN DISTINCT other.name, other.entity_type LIMIT 20"
            )
            while r.has_next():
                row = r.get_next()
                relationships.append({
                    "direction": "co-occurrence",
                    "type": "CO_OCCURS_CHUNK",
                    "target_name": row[0],
                    "target_type": row[1],
                })
        except Exception:
            pass

    return json.dumps({
        "entity": found_name,
        "entity_type": entity_type,
        "found": True,
        "relationship_count": len(relationships),
        "relationships": relationships[:50],
    }, ensure_ascii=False)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def query_graph(cypher: str, library_id: str) -> str:
    """
    Execute a read-only Cypher MATCH query against the Kùzu knowledge graph.

    Only MATCH/RETURN queries are allowed. Forbidden: CREATE, DELETE, SET, MERGE.
    Filter by library with: WHERE d.library_id = '<library_id>'

    Example:
      MATCH (e:Entity) WHERE e.library_id = 'default' RETURN e.name LIMIT 10
    """
    upper = cypher.strip().upper()
    forbidden = ("CREATE", "DELETE", "SET", "MERGE", "REMOVE", "DETACH", "DROP")
    for kw in forbidden:
        if re.search(r"\b" + kw + r"\b", upper):
            return json.dumps({"error": f"Keyword '{kw}' is not allowed in read-only mode"})

    conn, _ = _dbs()
    try:
        result = conn.execute(cypher)
        rows: list[list] = []
        while result.has_next() and len(rows) < 100:
            rows.append(list(result.get_next()))
        return json.dumps({"rows": rows, "count": len(rows)}, ensure_ascii=False)
    except Exception as exc:
        return json.dumps({"error": str(exc), "rows": []})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run()
