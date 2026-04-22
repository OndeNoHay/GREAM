"""
Tests de Fase 3 — MCP s1000d_csdb.

Cubre:
  - Helpers: _extract_keywords, _find_entity_ids, _docs_for_entities
  - Herramientas: list_libraries, get_library_stats, search_technical_content,
                  get_entity_relationships, query_graph
  - Test de integración: arranque real del servidor vía MCPClientManager
"""

import json
import sys

import chromadb
import kuzu
import pytest


# ---------------------------------------------------------------------------
# Fixtures: Kùzu + ChromaDB en memoria con datos de prueba
# ---------------------------------------------------------------------------

@pytest.fixture
def kuzu_conn(tmp_path):
    """Fresh Kùzu DB con esquema GRAEM y datos de prueba."""
    db = kuzu.Database(str(tmp_path / "test_graph.db"))
    conn = kuzu.Connection(db)

    # Esquema mínimo igual al de GraphDBService
    conn.execute(
        "CREATE NODE TABLE Document("
        "id STRING, library_id STRING, source_file STRING, "
        "page STRING, chunk_index STRING, created_at STRING, "
        "PRIMARY KEY (id))"
    )
    conn.execute(
        "CREATE NODE TABLE Entity("
        "id STRING, library_id STRING, name STRING, entity_type STRING, "
        "PRIMARY KEY (id))"
    )
    conn.execute("CREATE REL TABLE HAS_ENTITY(FROM Document TO Entity)")
    conn.execute("CREATE REL TABLE PART_OF(FROM Entity TO Entity)")
    conn.execute("CREATE REL TABLE CONTROLS(FROM Entity TO Entity)")
    conn.execute("CREATE REL TABLE CO_OCCURS(FROM Entity TO Entity, strength STRING)")

    # Datos de prueba — escenario S1000D: Hydraulic Power System
    lib = "lib-test-001"
    conn.execute(
        f"CREATE (d:Document {{id:'doc-1', library_id:'{lib}', "
        f"source_file:'hydraulic_system.pdf', page:'1', "
        f"chunk_index:'0', created_at:'2024-01-01'}})"
    )
    conn.execute(
        f"CREATE (d:Document {{id:'doc-2', library_id:'{lib}', "
        f"source_file:'maintenance_bulletin.pdf', page:'3', "
        f"chunk_index:'1', created_at:'2024-01-01'}})"
    )

    # Entidades
    entities = [
        ("ent-1", lib, "Hydraulic Pump", "Component"),
        ("ent-2", lib, "Hydraulic Reservoir", "Component"),
        ("ent-3", lib, "Pressure Relief Valve", "Component"),
        ("ent-4", lib, "Engine", "System"),
    ]
    for eid, elid, ename, etype in entities:
        conn.execute(
            f"CREATE (e:Entity {{id:'{eid}', library_id:'{elid}', "
            f"name:'{ename}', entity_type:'{etype}'}})"
        )

    # Relaciones Document → Entity
    conn.execute(
        "MATCH (d:Document {id:'doc-1'}), (e:Entity {id:'ent-1'}) "
        "CREATE (d)-[:HAS_ENTITY]->(e)"
    )
    conn.execute(
        "MATCH (d:Document {id:'doc-1'}), (e:Entity {id:'ent-2'}) "
        "CREATE (d)-[:HAS_ENTITY]->(e)"
    )
    conn.execute(
        "MATCH (d:Document {id:'doc-2'}), (e:Entity {id:'ent-3'}) "
        "CREATE (d)-[:HAS_ENTITY]->(e)"
    )
    conn.execute(
        "MATCH (d:Document {id:'doc-2'}), (e:Entity {id:'ent-4'}) "
        "CREATE (d)-[:HAS_ENTITY]->(e)"
    )

    # Relaciones entidad → entidad
    conn.execute(
        "MATCH (a:Entity {id:'ent-1'}), (b:Entity {id:'ent-2'}) "
        "CREATE (a)-[:PART_OF]->(b)"
    )
    conn.execute(
        "MATCH (a:Entity {id:'ent-4'}), (b:Entity {id:'ent-1'}) "
        "CREATE (a)-[:CONTROLS]->(b)"
    )

    yield conn


@pytest.fixture
def chroma_client(tmp_path):
    """ChromaDB en directorio temporal con algunos chunks de prueba."""
    from chromadb.config import Settings as ChromaSettings
    client = chromadb.PersistentClient(
        path=str(tmp_path / "chroma"),
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    col = client.create_collection("lib_lib_test_001")
    col.add(
        ids=["doc-1", "doc-2"],
        embeddings=[[0.1] * 384, [0.2] * 384],
        documents=[
            "The hydraulic pump pressurizes fluid from the reservoir.",
            "The pressure relief valve protects the system from overpressure.",
        ],
        metadatas=[
            {"source_file": "hydraulic_system.pdf", "page": "1"},
            {"source_file": "maintenance_bulletin.pdf", "page": "3"},
        ],
    )
    return client


@pytest.fixture(autouse=True)
def inject_test_dbs(kuzu_conn, chroma_client):
    """Inyecta las conexiones de prueba en el módulo MCP antes de cada test."""
    from mcp_servers.s1000d_csdb.server import _init_for_testing
    _init_for_testing(kuzu_conn, chroma_client)
    yield
    # Cleanup: resetear el estado del módulo
    from mcp_servers.s1000d_csdb import server as s
    s._kuzu_conn = None
    s._kuzu_db = None
    s._chroma = None


# ---------------------------------------------------------------------------
# _extract_keywords
# ---------------------------------------------------------------------------

class TestExtractKeywords:
    def test_filters_stop_words(self):
        from mcp_servers.s1000d_csdb.server import _extract_keywords
        result = _extract_keywords("What is the hydraulic pump for")
        assert "hydraulic" in result
        assert "pump" in result
        assert "the" not in result
        assert "what" not in result
        assert "is" not in result

    def test_extracts_technical_ids(self):
        from mcp_servers.s1000d_csdb.server import _extract_keywords
        result = _extract_keywords("Check bulletin D-260 for engine S3A")
        assert "d-260" in result
        assert "s3a" in result

    def test_empty_query(self):
        from mcp_servers.s1000d_csdb.server import _extract_keywords
        result = _extract_keywords("")
        assert result == []

    def test_deduplicates_keywords(self):
        from mcp_servers.s1000d_csdb.server import _extract_keywords
        result = _extract_keywords("pump pump pump")
        assert result.count("pump") == 1


# ---------------------------------------------------------------------------
# list_libraries
# ---------------------------------------------------------------------------

class TestListLibraries:
    def test_returns_injected_library(self):
        from mcp_servers.s1000d_csdb.server import list_libraries
        data = json.loads(list_libraries())
        assert data["count"] == 1
        assert "lib-test-001" in data["libraries"]

    def test_returns_json(self):
        from mcp_servers.s1000d_csdb.server import list_libraries
        result = list_libraries()
        assert isinstance(json.loads(result), dict)


# ---------------------------------------------------------------------------
# get_library_stats
# ---------------------------------------------------------------------------

class TestGetLibraryStats:
    def test_counts_correct(self):
        from mcp_servers.s1000d_csdb.server import get_library_stats
        data = json.loads(get_library_stats("lib-test-001"))
        assert data["document_chunks"] == 2
        assert data["entities"] == 4

    def test_vector_chunks_from_chroma(self):
        from mcp_servers.s1000d_csdb.server import get_library_stats
        data = json.loads(get_library_stats("lib-test-001"))
        assert data.get("vector_chunks") == 2

    def test_nonexistent_library_returns_zeros(self):
        from mcp_servers.s1000d_csdb.server import get_library_stats
        data = json.loads(get_library_stats("nonexistent"))
        assert data["document_chunks"] == 0
        assert data["entities"] == 0


# ---------------------------------------------------------------------------
# search_technical_content — modo graph
# ---------------------------------------------------------------------------

class TestSearchTechnicalContent:
    def test_graph_search_finds_entity_match(self):
        from mcp_servers.s1000d_csdb.server import search_technical_content
        data = json.loads(search_technical_content(
            "hydraulic pump", "lib-test-001", top_k=5, mode="graph"
        ))
        assert data["total_results"] >= 1
        assert any(r["matched_entity"] == "Hydraulic Pump" for r in data["results"])

    def test_graph_search_unknown_query_returns_empty(self):
        from mcp_servers.s1000d_csdb.server import search_technical_content
        data = json.loads(search_technical_content(
            "completely unrelated xyz99", "lib-test-001", mode="graph"
        ))
        assert data["total_results"] == 0

    def test_graph_mode_returns_source_file(self):
        from mcp_servers.s1000d_csdb.server import search_technical_content
        data = json.loads(search_technical_content(
            "pressure relief valve", "lib-test-001", mode="graph"
        ))
        assert data["total_results"] >= 1
        r = data["results"][0]
        assert r["source_file"] == "maintenance_bulletin.pdf"

    def test_top_k_respected(self):
        from mcp_servers.s1000d_csdb.server import search_technical_content
        data = json.loads(search_technical_content(
            "hydraulic", "lib-test-001", top_k=1, mode="graph"
        ))
        assert len(data["results"]) <= 1

    def test_graph_results_enriched_with_chroma_text(self):
        from mcp_servers.s1000d_csdb.server import search_technical_content
        data = json.loads(search_technical_content(
            "hydraulic pump", "lib-test-001", top_k=5, mode="graph"
        ))
        # chunk doc-1 está en ChromaDB con texto real
        match = next((r for r in data["results"] if r["chunk_id"] == "doc-1"), None)
        if match:
            assert "hydraulic" in match.get("text_snippet", "").lower()

    def test_nonexistent_library_returns_empty(self):
        from mcp_servers.s1000d_csdb.server import search_technical_content
        data = json.loads(search_technical_content(
            "hydraulic", "no-lib", mode="graph"
        ))
        assert data["total_results"] == 0


# ---------------------------------------------------------------------------
# get_entity_relationships
# ---------------------------------------------------------------------------

class TestGetEntityRelationships:
    def test_found_entity(self):
        from mcp_servers.s1000d_csdb.server import get_entity_relationships
        data = json.loads(get_entity_relationships("Hydraulic Pump", "lib-test-001"))
        assert data["found"] is True
        assert data["entity"] == "Hydraulic Pump"

    def test_part_of_relationship_detected(self):
        from mcp_servers.s1000d_csdb.server import get_entity_relationships
        data = json.loads(get_entity_relationships("Hydraulic Pump", "lib-test-001"))
        assert any(
            r["type"] == "PART_OF" and r["direction"] == "outgoing"
            for r in data["relationships"]
        )

    def test_controls_incoming_relationship(self):
        from mcp_servers.s1000d_csdb.server import get_entity_relationships
        # Engine CONTROLS Pump  →  Pump receives incoming CONTROLS
        data = json.loads(get_entity_relationships("Hydraulic Pump", "lib-test-001"))
        assert any(
            r["type"] == "CONTROLS" and r["direction"] == "incoming"
            for r in data["relationships"]
        )

    def test_entity_not_found(self):
        from mcp_servers.s1000d_csdb.server import get_entity_relationships
        data = json.loads(get_entity_relationships("Nonexistent Part XYZ", "lib-test-001"))
        assert data["found"] is False

    def test_case_insensitive_match(self):
        from mcp_servers.s1000d_csdb.server import get_entity_relationships
        data = json.loads(get_entity_relationships("hydraulic pump", "lib-test-001"))
        assert data["found"] is True


# ---------------------------------------------------------------------------
# query_graph
# ---------------------------------------------------------------------------

class TestQueryGraph:
    def test_basic_match(self):
        from mcp_servers.s1000d_csdb.server import query_graph
        data = json.loads(query_graph(
            "MATCH (e:Entity) WHERE e.library_id = 'lib-test-001' RETURN e.name LIMIT 10",
            "lib-test-001",
        ))
        assert data["count"] == 4

    def test_count_query(self):
        from mcp_servers.s1000d_csdb.server import query_graph
        data = json.loads(query_graph(
            "MATCH (d:Document) WHERE d.library_id = 'lib-test-001' RETURN count(d)",
            "lib-test-001",
        ))
        assert data["rows"][0][0] == 2

    def test_forbidden_create(self):
        from mcp_servers.s1000d_csdb.server import query_graph
        data = json.loads(query_graph("CREATE (n:Entity)", "lib-test-001"))
        assert "error" in data
        assert "CREATE" in data["error"]

    def test_forbidden_delete(self):
        from mcp_servers.s1000d_csdb.server import query_graph
        data = json.loads(query_graph(
            "MATCH (n) DELETE n", "lib-test-001"
        ))
        assert "error" in data

    def test_invalid_cypher(self):
        from mcp_servers.s1000d_csdb.server import query_graph
        data = json.loads(query_graph("THIS IS NOT CYPHER", "lib-test-001"))
        assert "error" in data


# ---------------------------------------------------------------------------
# Integración: arranque real del servidor
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.asyncio
async def test_s1000d_csdb_server_starts_and_lists_tools():
    """
    Arranca s1000d_csdb como subproceso stdio y verifica que
    MCPClientManager descubre las 5 herramientas esperadas.
    """
    from app.models.agents import MCPServerConfig
    from app.services.mcp_client_manager import MCPClientManager

    manager = MCPClientManager.__new__(MCPClientManager)
    manager._sessions = {}
    manager._contexts = {}
    manager._configs = {}

    config = MCPServerConfig(
        name="s1000d_test",
        type="stdio",
        command=sys.executable,
        args=["-m", "mcp_servers.s1000d_csdb.server"],
        enabled=True,
        timeout_seconds=20,
    )

    try:
        ok = await manager.start_server(config)
        assert ok, "s1000d_csdb server failed to start"

        tools = await manager.list_tools("s1000d_test")
        tool_names = {t.name for t in tools}
        assert "list_libraries" in tool_names
        assert "get_library_stats" in tool_names
        assert "search_technical_content" in tool_names
        assert "get_entity_relationships" in tool_names
        assert "query_graph" in tool_names
    finally:
        await manager.stop_all()
