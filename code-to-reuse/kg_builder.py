"""
Servicio de construcción del Knowledge Graph.
Extrae entidades y relaciones usando LLMs y las almacena en Neo4j.
"""

import logging
import asyncio
from typing import List, Dict, Optional
from neo4j import GraphDatabase
import hashlib
import re
from datetime import datetime

# Imports de LangChain
try:
    from langchain.schema import Document as LangchainDocument
except ImportError:
    try:
        from langchain_core.documents import Document as LangchainDocument
    except ImportError:
        LangchainDocument = None
        logging.error("No se pudo importar Document de LangChain")
# CORRECCIÓN: Import actualizado para LangChain 0.1.0+
try:
    # Intenta el import nuevo (LangChain 0.1.0+)
    from langchain_experimental.graph_transformers.llm import LLMGraphTransformer
except ImportError:
    try:
        # Fallback al import antiguo
        from langchain_experimental.graph_transformers import LLMGraphTransformer
    except ImportError:
        # Si ninguno funciona, usar None y dar error claro
        LLMGraphTransformer = None
        logging.error("No se pudo importar LLMGraphTransformer. Instala: pip install langchain-experimental")

from app.services.llm_service import LLMService
from app.config import settings

logger = logging.getLogger("app.kg_builder")


class KGBuilder:
    """
    Constructor del Knowledge Graph.
    Extrae entidades y relaciones desde texto y las almacena en Neo4j.
    """
    
    def __init__(self):
        """Inicializa conexión a Neo4j y servicios LLM"""
        self.neo4j_uri = settings.NEO4J_URI
        self.neo4j_user = settings.NEO4J_USER
        self.neo4j_password = settings.NEO4J_PASSWORD
        
        # Inicializar driver de Neo4j
        try:
            self.driver = GraphDatabase.driver(
                self.neo4j_uri,
                auth=(self.neo4j_user, self.neo4j_password)
            )
            logger.info("✅ Driver de Neo4j inicializado")
        except Exception as e:
            logger.error(f"❌ Error conectando a Neo4j: {e}")
            self.driver = None
        
        # Inicializar servicio LLM
        self.llm_service = LLMService()
        
        # Tipos de entidades y relaciones permitidas
        self.allowed_node_types = settings.ENTITY_TYPES
        self.allowed_relationships = settings.RELATION_TYPES
    
    async def verify_connection(self) -> bool:
        """Verifica que la conexión a Neo4j esté activa"""
        if not self.driver:
            return False
        
        try:
            with self.driver.session() as session:
                result = session.run("RETURN 1 as num")
                return result.single()["num"] == 1
        except Exception as e:
            logger.error(f"Error verificando conexión: {e}")
            return False
    
    async def build_graph_from_document(
        self,
        doc_id: str,
        chunks: List[Dict],
        metadata: Dict
    ) -> Dict:
        """
        Construye el Knowledge Graph desde los chunks de un documento
        procesando todos los chunks en paralelo.
        OPTIMIZADO: Paralelización con asyncio.gather + batching Neo4j.

        Args:
            doc_id: ID del documento
            chunks: Lista de chunks procesados
            metadata: Metadata del documento

        Returns:
            Dict con estadísticas de entidades y relaciones creadas
        """
        logger.info(f"[BUILD KG] Construyendo KG para documento {doc_id} ({len(chunks)} chunks)")

        total_entities = 0
        total_relationships = 0
        errors = []

        try:
            logger.info("[PASO 1] Obteniendo LLM para extracción...")
            llm = await self.llm_service.get_llm()
            logger.info(f"[OK] LLM obtenido: {type(llm).__name__}")

            if LangchainDocument is None:
                raise ImportError("Document de LangChain no disponible.")

            # PASO 2: Crear tareas de extracción para todos los chunks
            logger.info(f"[PASO 2] Creando {len(chunks)} tareas de extracción en paralelo...")
            extraction_tasks = []
            for i, chunk in enumerate(chunks):
                extraction_tasks.append(
                    self._extract_graph_from_text(
                        llm=llm,
                        text=chunk["text"],
                        doc_id=doc_id,
                        chunk_index=i
                    )
                )

            # Ejecutar todas las tareas de extracción concurrentemente
            logger.info("[PASO 3] Ejecutando extracción en paralelo...")
            results = await asyncio.gather(*extraction_tasks, return_exceptions=True)

            logger.info("[PASO 4] Recopilando resultados de extracción...")
            all_entities = []
            all_relationships = []

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"[ERROR CHUNK {i}] {result}")
                    errors.append(f"Chunk {i}: {str(result)}")
                    continue

                entities, relationships = result
                if entities:
                    all_entities.extend(entities)

                if relationships:
                    all_relationships.extend(relationships)

            logger.info(f"Total extraído: {len(all_entities)} entidades, {len(all_relationships)} relaciones")

            # PASO 5: Almacenar todo en Neo4j en un solo batch
            if all_entities or all_relationships:
                logger.info("[PASO 5] Almacenando en Neo4j con batch UNWIND...")
                stats = await self._store_graph_in_batch(
                    entities=all_entities,
                    relationships=all_relationships,
                    doc_id=doc_id
                )
                total_entities = stats["entities"]
                total_relationships = stats["relationships"]

            # Crear nodo del documento
            await self._create_document_node(doc_id, metadata, total_entities)

            logger.info(
                f"[OK] KG construido: {total_entities} entidades, "
                f"{total_relationships} relaciones (paralelo + batch)"
            )

            return {
                "doc_id": doc_id,
                "entities_created": total_entities,
                "relationships_created": total_relationships,
                "chunks_processed": len(chunks) - len(errors),
                "errors": errors,
                "timestamp": datetime.utcnow().isoformat(),
            }

        except Exception as e:
            logger.error(f"[ERROR] Error construyendo KG: {e}", exc_info=True)
            raise

    async def _extract_graph_from_text(
        self,
        llm,
        text: str,
        doc_id: str,
        chunk_index: int
    ) -> tuple[List[Dict], List[Dict]]:
        """
        Extrae entidades y relaciones de un texto usando UN SOLO prompt.
        OPTIMIZADO: Una sola llamada al LLM en lugar de dos.

        Returns:
            Tuple de (entidades, relaciones)
        """
        # Prompt combinado
        combined_prompt = f"""Extract key entities and their relationships from the text.

Allowed entity types: {', '.join(self.allowed_node_types)}
Allowed relationship types: {', '.join(self.allowed_relationships)}

Text:
{text}

Return the results in this exact format, with ## ENTITIES first, then ## RELATIONSHIPS:

## ENTITIES
ENTITY_NAME | ENTITY_TYPE | DESCRIPTION
ENTITY_NAME | ENTITY_TYPE | DESCRIPTION

## RELATIONSHIPS
SOURCE_ENTITY -> RELATIONSHIP_TYPE -> TARGET_ENTITY
SOURCE_ENTITY -> RELATIONSHIP_TYPE -> TARGET_ENTITY
"""

        try:
            # Una sola llamada al LLM
            response = await asyncio.to_thread(llm.invoke, combined_prompt)
            response_text = response.content if hasattr(response, 'content') else str(response)

            entities = []
            relationships = []

            # Parsear la respuesta combinada
            entities_part = ""
            relationships_part = ""

            if "## RELATIONSHIPS" in response_text:
                parts = response_text.split("## RELATIONSHIPS", 1)
                entities_part = parts[0]
                relationships_part = parts[1]
            elif "## ENTITIES" in response_text:
                entities_part = response_text

            if "## ENTITIES" in entities_part:
                entities_part = entities_part.split("## ENTITIES", 1)[-1]

            # Parsear entidades
            for line in entities_part.strip().split('\n'):
                line = line.strip()
                if '|' in line and not line.startswith('ENTITY'):
                    parts = [p.strip() for p in line.split('|')]
                    if len(parts) >= 3:
                        entity_type = parts[1] if parts[1] in self.allowed_node_types else "Concept"
                        entities.append({
                            "name": parts[0],
                            "type": entity_type,
                            "description": parts[2]
                        })

            # Parsear relaciones
            entity_names = {e["name"] for e in entities}
            for line in relationships_part.strip().split('\n'):
                line = line.strip()
                if '->' in line and not line.startswith('SOURCE'):
                    parts = [p.strip() for p in line.split('->')]
                    if len(parts) >= 3:
                        # Validar que las entidades existan en la lista de este chunk
                        if parts[0] in entity_names and parts[2] in entity_names:
                            relationships.append({
                                "source": parts[0],
                                "type": parts[1],
                                "target": parts[2]
                            })

            logger.debug(f"[CHUNK {chunk_index}] Extracted {len(entities)} entities, {len(relationships)} relationships (1 LLM call)")
            return (entities, relationships)

        except Exception as e:
            logger.error(f"Error extrayendo grafo (optimizado): {e}")
            return ([], [])

    async def _store_extracted_graph(
        self,
        entities: List[Dict],
        relationships: List[Dict],
        doc_id: str,
        metadata: Dict
    ) -> Dict:
        """
        Almacena entidades y relaciones extraídas en Neo4j.

        Returns:
            Dict con conteo de entidades y relaciones creadas
        """
        entities_count = 0
        relationships_count = 0

        if not self.driver:
            logger.warning("Driver de Neo4j no disponible")
            return {"entities": 0, "relationships": 0}

        with self.driver.session() as session:
            # Crear nodos (entidades)
            for entity in entities:
                try:
                    entity_id = self._generate_node_id(entity["name"], entity["type"])
                    session.run(
                        """
                        MERGE (n:Entity {id: $id})
                        SET n.name = $name,
                            n.type = $type,
                            n.description = $description,
                            n.source_document = $source_doc,
                            n.updated_at = datetime()
                        """,
                        id=entity_id,
                        name=entity["name"],
                        type=entity["type"],
                        description=entity.get("description", ""),
                        source_doc=doc_id
                    )
                    entities_count += 1
                except Exception as e:
                    logger.warning(f"Error creando entidad {entity['name']}: {e}")

            # Crear relaciones
            for rel in relationships:
                try:
                    # Generar IDs para source y target
                    # Buscar entidades correspondientes
                    source_entity = next((e for e in entities if e["name"] == rel["source"]), None)
                    target_entity = next((e for e in entities if e["name"] == rel["target"]), None)

                    if not source_entity or not target_entity:
                        continue

                    source_id = self._generate_node_id(source_entity["name"], source_entity["type"])
                    target_id = self._generate_node_id(target_entity["name"], target_entity["type"])

                    # Normalizar tipo de relación
                    rel_type = self._normalize_relationship_type(rel["type"])

                    # Crear relación
                    session.run(
                        f"""
                        MATCH (a:Entity {{id: $source_id}})
                        MATCH (b:Entity {{id: $target_id}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.source_document = $source_doc,
                            r.updated_at = datetime()
                        """,
                        source_id=source_id,
                        target_id=target_id,
                        source_doc=doc_id
                    )
                    relationships_count += 1
                except Exception as e:
                    logger.warning(f"Error creando relación {rel.get('type', 'unknown')}: {e}")

        return {
            "entities": entities_count,
            "relationships": relationships_count
        }

    async def _store_graph_document(
        self,
        graph_doc,
        doc_id: str,
        metadata: Dict
    ) -> Dict:
        """
        Almacena un GraphDocument en Neo4j.
        
        Returns:
            Dict con conteo de entidades y relaciones creadas
        """
        entities_count = 0
        relationships_count = 0
        
        if not self.driver:
            logger.warning("Driver de Neo4j no disponible")
            return {"entities": 0, "relationships": 0}
        
        with self.driver.session() as session:
            # Crear nodos (entidades)
            for node in graph_doc.nodes:
                try:
                    session.run(
                        """
                        MERGE (n:Entity {id: $id})
                        SET n.name = $name,
                            n.type = $type,
                            n.description = $description,
                            n.source_document = $source_doc,
                            n.updated_at = datetime()
                        """,
                        id=self._generate_node_id(node.id, node.type),
                        name=node.id,
                        type=node.type,
                        description=getattr(node, 'description', ''),
                        source_doc=doc_id
                    )
                    entities_count += 1
                except Exception as e:
                    logger.warning(f"Error creando nodo {node.id}: {e}")
            
            # Crear relaciones
            for rel in graph_doc.relationships:
                try:
                    # Generar IDs consistentes para source y target
                    source_id = self._generate_node_id(
                        rel.source.id,
                        rel.source.type
                    )
                    target_id = self._generate_node_id(
                        rel.target.id,
                        rel.target.type
                    )
                    
                    # Normalizar tipo de relación
                    rel_type = self._normalize_relationship_type(rel.type)
                    
                    # Crear relación (usar apoc si está disponible, sino cypher estándar)
                    session.run(
                        f"""
                        MATCH (a:Entity {{id: $source_id}})
                        MATCH (b:Entity {{id: $target_id}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.description = $description,
                            r.confidence = $confidence,
                            r.source_document = $source_doc,
                            r.updated_at = datetime()
                        """,
                        source_id=source_id,
                        target_id=target_id,
                        description=getattr(rel, 'description', ''),
                        confidence=getattr(rel, 'confidence', 1.0),
                        source_doc=doc_id
                    )
                    relationships_count += 1
                except Exception as e:
                    logger.warning(f"Error creando relación {rel.type}: {e}")
        
        return {
            "entities": entities_count,
            "relationships": relationships_count
        }

    async def _store_graph_in_batch(
        self,
        entities: List[Dict],
        relationships: List[Dict],
        doc_id: str
    ) -> Dict:
        """
        Almacena TODAS las entidades y relaciones de un documento
        en Neo4j usando UNWIND para batching.
        OPTIMIZADO: Una sola query UNWIND en lugar de N+1 MERGEs.

        Returns:
            Dict con conteo de entidades y relaciones creadas
        """
        if not self.driver:
            logger.warning("Driver de Neo4j no disponible")
            return {"entities": 0, "relationships": 0}

        # 1. Desduplicar entidades ANTES de enviar a Neo4j
        # Usamos (name, type) como clave única
        unique_entities = {}
        for e in entities:
            key = (e["name"], e["type"])
            if key not in unique_entities:
                unique_entities[key] = {
                    "id": self._generate_node_id(e["name"], e["type"]),
                    "name": e["name"],
                    "type": e["type"],
                    "description": e.get("description", ""),
                    "source_doc": doc_id
                }

        entities_props = list(unique_entities.values())

        # 2. Mapear entidades a sus IDs para las relaciones
        entity_id_map = {(e['name'], e['type']): e['id'] for e in entities_props}

        # 3. Preparar props de relaciones, asegurando que ambos nodos existan en nuestro batch
        relationships_props = []

        # Necesitamos un lookup rápido de tipo por nombre
        entity_type_map = {e['name']: e['type'] for e in entities_props}

        for r in relationships:
            # Buscar el tipo de la entidad fuente y destino
            source_type = entity_type_map.get(r["source"])
            target_type = entity_type_map.get(r["target"])

            if not source_type or not target_type:
                continue

            source_id = entity_id_map.get((r["source"], source_type))
            target_id = entity_id_map.get((r["target"], target_type))

            if source_id and target_id:
                relationships_props.append({
                    "source_id": source_id,
                    "target_id": target_id,
                    "type": self._normalize_relationship_type(r["type"]),
                    "source_doc": doc_id
                })

        # 4. Ejecutar queries en batch
        with self.driver.session() as session:
            try:
                # Batch de entidades
                if entities_props:
                    session.run(
                        """
                        UNWIND $props as e_props
                        MERGE (n:Entity {id: e_props.id})
                        SET n.name = e_props.name,
                            n.type = e_props.type,
                            n.description = e_props.description,
                            n.source_document = e_props.source_doc,
                            n.updated_at = datetime()
                        """,
                        props=entities_props
                    )

                # Batch de relaciones
                # Agrupamos por tipo de relación para una query por tipo
                from collections import defaultdict
                rels_by_type = defaultdict(list)
                for r in relationships_props:
                    rels_by_type[r["type"]].append(r)

                for rel_type, props in rels_by_type.items():
                    session.run(
                        f"""
                        UNWIND $props as r_props
                        MATCH (a:Entity {{id: r_props.source_id}})
                        MATCH (b:Entity {{id: r_props.target_id}})
                        MERGE (a)-[r:{rel_type}]->(b)
                        SET r.source_document = r_props.source_doc,
                            r.updated_at = datetime()
                        """,
                        props=props
                    )
            except Exception as e:
                logger.error(f"Error en batch UNWIND: {e}")
                raise

        logger.info(f"[BATCH] Stored {len(entities_props)} entities, {len(relationships_props)} relationships")
        return {
            "entities": len(entities_props),
            "relationships": len(relationships_props)
        }

    async def _create_document_node(
        self,
        doc_id: str,
        metadata: Dict,
        entity_count: int
    ):
        """Crea un nodo Document en Neo4j"""
        if not self.driver:
            return
        
        try:
            with self.driver.session() as session:
                session.run(
                    """
                    MERGE (d:Document {id: $doc_id})
                    SET d.filename = $filename,
                        d.size_bytes = $size_bytes,
                        d.language = $language,
                        d.entity_count = $entity_count,
                        d.created_at = datetime($created_at)
                    """,
                    doc_id=doc_id,
                    filename=metadata.get("filename", "unknown"),
                    size_bytes=metadata.get("size_bytes", 0),
                    language=metadata.get("language", "unknown"),
                    entity_count=entity_count,
                    created_at=metadata.get("processed_at", datetime.utcnow().isoformat())
                )
                
                # Relacionar documento con sus entidades
                session.run(
                    """
                    MATCH (d:Document {id: $doc_id})
                    MATCH (e:Entity {source_document: $doc_id})
                    MERGE (d)-[:CONTAINS]->(e)
                    """,
                    doc_id=doc_id
                )
        except Exception as e:
            logger.error(f"Error creando nodo Document: {e}")
    
    def _generate_node_id(self, name: str, node_type: str) -> str:
        """
        Genera un ID único y consistente para un nodo.
        Usa hash para evitar IDs muy largos.
        """
        import hashlib
        combined = f"{node_type}:{name}".lower().strip()
        # Usar hash corto para IDs manejables
        hash_short = hashlib.md5(combined.encode()).hexdigest()[:12]
        return f"{node_type}_{hash_short}"
    
    def _normalize_relationship_type(self, rel_type: str) -> str:
        """
        Normaliza el tipo de relación para que sea válido en Neo4j.
        Neo4j requiere tipos de relación sin espacios y en UPPER_CASE.
        """
        # Convertir a upper case y reemplazar espacios/caracteres especiales
        normalized = rel_type.upper().strip()
        normalized = normalized.replace(" ", "_")
        normalized = normalized.replace("-", "_")
        
        # Si no está en la lista de permitidos, usar el más cercano
        if normalized not in self.allowed_relationships:
            # Intentar mapeo inteligente
            mapping = {
                "REQUIRE": "REQUIRES",
                "AFFECT": "AFFECTS",
                "REFERENCE": "REFERENCES",
                "PARTOF": "PART_OF",
                "FOLLOW": "FOLLOWS",
                "CONFLICT": "CONFLICTS_WITH",
                "SUPERSEDE": "SUPERSEDES",
                "APPLY": "APPLIES_TO",
            }
            normalized = mapping.get(normalized, "RELATED_TO")
        
        return normalized
    
    async def get_graph_statistics(self, document_ids: Optional[List[str]] = None) -> Dict:
        """
        Obtiene estadísticas del Knowledge Graph.

        Args:
            document_ids: Filtrar por documentos específicos (library filtering)

        Returns:
            Dict con conteo de nodos, relaciones, etc.
        """
        if not self.driver:
            return {
                "error": "Neo4j no disponible",
                "total_nodes": 0,
                "total_edges": 0,
            }

        try:
            with self.driver.session() as session:
                # Build WHERE clause for document filtering
                where_clause = ""
                if document_ids:
                    where_clause = "WHERE n.source_document IN $document_ids"

                # Contar nodos por tipo
                node_stats_query = f"""
                    MATCH (n)
                    {where_clause}
                    RETURN labels(n)[0] as label, count(*) as count
                    ORDER BY count DESC
                """
                node_stats = session.run(
                    node_stats_query,
                    document_ids=document_ids if document_ids else []
                ).data()

                # Contar relaciones por tipo (only between filtered nodes)
                if document_ids:
                    rel_stats_query = """
                        MATCH (a)-[r]->(b)
                        WHERE a.source_document IN $document_ids
                        AND b.source_document IN $document_ids
                        RETURN type(r) as type, count(*) as count
                        ORDER BY count DESC
                    """
                    rel_stats = session.run(rel_stats_query, document_ids=document_ids).data()
                else:
                    rel_stats_query = """
                        MATCH ()-[r]->()
                        RETURN type(r) as type, count(*) as count
                        ORDER BY count DESC
                    """
                    rel_stats = session.run(rel_stats_query).data()

                # Totales
                if document_ids:
                    totals_query = """
                        MATCH (n)
                        WHERE n.source_document IN $document_ids
                        WITH count(DISTINCT n) as nodes
                        OPTIONAL MATCH (a)-[r]->(b)
                        WHERE a.source_document IN $document_ids
                        AND b.source_document IN $document_ids
                        RETURN nodes, count(DISTINCT r) as edges
                    """
                    totals = session.run(totals_query, document_ids=document_ids).single()
                else:
                    totals_query = """
                        MATCH (n)
                        WITH count(n) as nodes
                        OPTIONAL MATCH ()-[r]->()
                        RETURN nodes, count(DISTINCT r) as edges
                    """
                    totals = session.run(totals_query).single()

                return {
                    "total_nodes": totals["nodes"],
                    "total_edges": totals["edges"],
                    "node_types": {item["label"]: item["count"] for item in node_stats},
                    "edge_types": {item["type"]: item["count"] for item in rel_stats},
                    "timestamp": datetime.utcnow().isoformat(),
                }
        except Exception as e:
            logger.error(f"Error obteniendo estadísticas: {e}")
            return {"error": str(e)}
    
    async def get_graph_for_visualization(
        self,
        limit: int = 100,
        node_types: Optional[List[str]] = None,
        document_ids: Optional[List[str]] = None
    ) -> Dict:
        """
        Obtiene datos del grafo en formato para visualización con Cytoscape.

        Args:
            limit: Número máximo de nodos a retornar
            node_types: Filtrar por tipos de nodo específicos
            document_ids: Filtrar por documentos específicos (library filtering)

        Returns:
            Dict con arrays de nodes y edges en formato Cytoscape
        """
        if not self.driver:
            return {"nodes": [], "edges": []}

        try:
            with self.driver.session() as session:
                # Construir query con filtros
                where_clauses = []

                if node_types:
                    labels_filter = " OR ".join([f"n:{t}" for t in node_types])
                    where_clauses.append(f"({labels_filter})")

                if document_ids:
                    # Filter by source_document property
                    where_clauses.append("n.source_document IN $document_ids")

                where_clause = ""
                if where_clauses:
                    where_clause = "WHERE " + " AND ".join(where_clauses)

                # Obtener nodos
                nodes_query = f"""
                MATCH (n)
                {where_clause}
                RETURN n.id as id,
                       n.name as name,
                       n.type as type,
                       n.description as description,
                       labels(n)[0] as label
                LIMIT {limit}
                """

                logger.info(f"[VIZ] Ejecutando query de nodos con limit={limit}, node_types={node_types}, document_ids={document_ids}")
                nodes_data = session.run(nodes_query, document_ids=document_ids if document_ids else []).data()
                logger.info(f"[VIZ] Obtenidos {len(nodes_data)} nodos")

                # Obtener relaciones entre esos nodos
                node_ids = [n["id"] for n in nodes_data]

                if not node_ids:
                    logger.warning("[VIZ] No se encontraron nodos en Neo4j")
                    return {"nodes": [], "edges": [], "count": {"nodes": 0, "edges": 0}}

                edges_query = """
                MATCH (a)-[r]->(b)
                WHERE a.id IN $node_ids AND b.id IN $node_ids
                RETURN a.id as source,
                       b.id as target,
                       type(r) as type,
                       r.description as description,
                       id(r) as id
                """

                edges_data = session.run(edges_query, node_ids=node_ids).data()
                logger.info(f"[VIZ] Obtenidas {len(edges_data)} relaciones")
                
                # Formatear para Cytoscape
                nodes = [
                    {
                        "data": {
                            "id": node["id"],
                            "label": node["name"] or node["id"],
                            "type": node["type"] or node["label"],
                            "description": node.get("description", ""),
                        }
                    }
                    for node in nodes_data
                ]
                
                edges = [
                    {
                        "data": {
                            "id": f"e_{edge['id']}",
                            "source": edge["source"],
                            "target": edge["target"],
                            "label": edge["type"],
                            "description": edge.get("description", ""),
                        }
                    }
                    for edge in edges_data
                ]
                
                return {
                    "nodes": nodes,
                    "edges": edges,
                    "count": {
                        "nodes": len(nodes),
                        "edges": len(edges),
                    }
                }
                
        except Exception as e:
            logger.error(f"Error obteniendo datos de visualización: {e}")
            return {"nodes": [], "edges": [], "error": str(e)}
    
    async def clear_graph(self) -> Dict:
        """
        Limpia completamente el Knowledge Graph.
        ⚠️ Operación destructiva - elimina todos los nodos y relaciones.
        """
        if not self.driver:
            return {"success": False, "message": "Neo4j no disponible"}
        
        try:
            with self.driver.session() as session:
                # Obtener conteos antes de borrar
                before = session.run(
                    "MATCH (n) OPTIONAL MATCH ()-[r]->() "
                    "RETURN count(DISTINCT n) as nodes, count(r) as edges"
                ).single()
                
                # Borrar todo
                session.run("MATCH (n) DETACH DELETE n")
                
                logger.warning(
                    f"🗑️  Grafo limpiado: {before['nodes']} nodos, "
                    f"{before['edges']} relaciones eliminadas"
                )
                
                return {
                    "success": True,
                    "deleted": {
                        "nodes": before["nodes"],
                        "edges": before["edges"],
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                }
        except Exception as e:
            logger.error(f"Error limpiando grafo: {e}")
            return {"success": False, "error": str(e)}

    async def delete_graph_by_document(self, doc_id: str) -> Dict:
        """
        Elimina todos los nodos y relaciones asociados a un documento específico.

        Args:
            doc_id: ID del documento cuyo grafo se eliminará

        Returns:
            Dict con información sobre la operación y estadísticas de eliminación
        """
        if not self.driver:
            return {"success": False, "message": "Neo4j no disponible"}

        try:
            with self.driver.session() as session:
                # Contar nodos y relaciones antes de eliminar
                count_query = """
                    MATCH (e:Entity {source_document: $doc_id})
                    OPTIONAL MATCH (e)-[r]-()
                    WHERE r.source_document = $doc_id
                    RETURN count(DISTINCT e) as entities, count(DISTINCT r) as relationships
                """
                before = session.run(count_query, doc_id=doc_id).single()

                # Eliminar relaciones asociadas al documento
                session.run(
                    """
                    MATCH ()-[r]->()
                    WHERE r.source_document = $doc_id
                    DELETE r
                    """,
                    doc_id=doc_id
                )

                # Eliminar entidades asociadas al documento
                # DETACH DELETE elimina el nodo y todas sus relaciones
                session.run(
                    """
                    MATCH (e:Entity {source_document: $doc_id})
                    DETACH DELETE e
                    """,
                    doc_id=doc_id
                )

                # Eliminar nodo Document si existe
                session.run(
                    """
                    MATCH (d:Document {id: $doc_id})
                    DETACH DELETE d
                    """,
                    doc_id=doc_id
                )

                entities_deleted = before["entities"] if before else 0
                relationships_deleted = before["relationships"] if before else 0

                logger.info(
                    f"[DELETE] Grafo del documento {doc_id} eliminado: "
                    f"{entities_deleted} entidades, {relationships_deleted} relaciones"
                )

                return {
                    "success": True,
                    "doc_id": doc_id,
                    "deleted": {
                        "entities": entities_deleted,
                        "relationships": relationships_deleted,
                    },
                    "timestamp": datetime.utcnow().isoformat(),
                }
        except Exception as e:
            logger.error(f"Error eliminando grafo del documento {doc_id}: {e}")
            return {"success": False, "error": str(e)}

    async def rebuild_graph_from_documents(self, doc_ids: List[str]) -> Dict:
        """
        Reconstruye el grafo desde una lista de documentos.
        """
        # TODO: Implementar reconstrucción completa
        # Esto requeriría cargar los documentos procesados y volver a extraer
        logger.info(f"Reconstruyendo grafo desde {len(doc_ids)} documentos")
        return {
            "message": "Rebuild en progreso",
            "doc_ids": doc_ids,
        }

    async def merge_duplicate_entities(self, similarity_threshold: float = 0.85) -> Dict:
        """
        Encuentra y fusiona entidades duplicadas o muy similares entre documentos.

        Esto es crucial para documentación técnica donde el mismo componente,
        parte o concepto puede aparecer en múltiples documentos con nombres
        ligeramente diferentes.

        Args:
            similarity_threshold: Umbral de similitud (0-1) para considerar entidades duplicadas

        Returns:
            Dict con estadísticas de fusión
        """
        if not self.driver:
            return {"success": False, "message": "Neo4j no disponible"}

        try:
            with self.driver.session() as session:
                # Encontrar entidades con nombres exactamente iguales de diferentes documentos
                exact_matches_query = """
                    MATCH (e1:Entity)
                    MATCH (e2:Entity)
                    WHERE e1.name = e2.name
                      AND e1.type = e2.type
                      AND e1.id < e2.id
                      AND e1.source_document <> e2.source_document
                    RETURN e1.id as id1, e2.id as id2, e1.name as name, e1.type as type,
                           e1.source_document as doc1, e2.source_document as doc2
                """

                matches = session.run(exact_matches_query).data()

                merged_count = 0
                merge_details = []

                for match in matches:
                    # Fusionar e2 en e1 (mantener e1 como entidad principal)
                    merge_query = """
                        MATCH (e1:Entity {id: $id1})
                        MATCH (e2:Entity {id: $id2})

                        // Copiar todas las relaciones de e2 a e1
                        WITH e1, e2
                        OPTIONAL MATCH (e2)-[r]->(target)
                        WHERE NOT (e1)-[]->(target)
                        FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
                            MERGE (e1)-[new_r:RELATES_TO]->(target)
                            SET new_r = properties(r)
                        )

                        WITH e1, e2
                        OPTIONAL MATCH (source)-[r]->(e2)
                        WHERE NOT (source)-[]->(e1)
                        FOREACH (_ IN CASE WHEN r IS NOT NULL THEN [1] ELSE [] END |
                            MERGE (source)-[new_r:RELATES_TO]->(e1)
                            SET new_r = properties(r)
                        )

                        // Agregar metadata de fusión
                        WITH e1, e2
                        SET e1.merged_from = COALESCE(e1.merged_from, []) + e2.source_document,
                            e1.merged_count = COALESCE(e1.merged_count, 0) + 1,
                            e1.description = COALESCE(e1.description, '') + ' | ' + COALESCE(e2.description, ''),
                            e1.appears_in_documents = COALESCE(e1.appears_in_documents, [e1.source_document]) + [e2.source_document]

                        // Crear relación SAME_AS antes de eliminar
                        MERGE (e1)-[:SAME_AS {merged_at: datetime()}]->(e2)

                        // Eliminar la entidad duplicada
                        DETACH DELETE e2

                        RETURN e1.id as merged_id, e1.name as name
                    """

                    result = session.run(merge_query, id1=match['id1'], id2=match['id2']).single()

                    if result:
                        merged_count += 1
                        merge_details.append({
                            'name': match['name'],
                            'type': match['type'],
                            'documents': [match['doc1'], match['doc2']],
                            'kept_id': match['id1'],
                            'removed_id': match['id2']
                        })

                logger.info(f"[MERGE] Fusionadas {merged_count} entidades duplicadas")

                return {
                    "success": True,
                    "merged_count": merged_count,
                    "details": merge_details,
                    "timestamp": datetime.utcnow().isoformat(),
                }

        except Exception as e:
            logger.error(f"Error fusionando entidades: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def analyze_document_impact(self, doc_id: str, max_depth: int = 3) -> Dict:
        """
        Analiza el impacto de cambios o errores en un documento.

        Encuentra todos los documentos, entidades y relaciones que podrían
        verse afectados si este documento tiene errores o se modifica.

        Args:
            doc_id: ID del documento a analizar
            max_depth: Profundidad máxima de búsqueda en el grafo

        Returns:
            Dict con análisis de impacto
        """
        if not self.driver:
            return {"success": False, "message": "Neo4j no disponible"}

        try:
            with self.driver.session() as session:
                # Encontrar todas las entidades del documento
                entities_query = """
                    MATCH (e:Entity {source_document: $doc_id})
                    RETURN e.id as id, e.name as name, e.type as type
                """
                source_entities = session.run(entities_query, doc_id=doc_id).data()

                if not source_entities:
                    return {
                        "success": True,
                        "doc_id": doc_id,
                        "impact": "none",
                        "message": "No se encontraron entidades para este documento"
                    }

                # Encontrar entidades relacionadas (hasta max_depth saltos)
                impact_query = """
                    MATCH (e:Entity {source_document: $doc_id})
                    CALL apoc.path.subgraphNodes(e, {
                        maxLevel: $max_depth,
                        relationshipFilter: null
                    })
                    YIELD node
                    WHERE node.source_document <> $doc_id
                    WITH DISTINCT node
                    RETURN node.id as id,
                           node.name as name,
                           node.type as type,
                           node.source_document as source_doc
                """

                # Si APOC no está disponible, usar query alternativa
                try:
                    impacted = session.run(impact_query, doc_id=doc_id, max_depth=max_depth).data()
                except:
                    # Fallback sin APOC: solo 1 nivel de profundidad
                    fallback_query = """
                        MATCH (e:Entity {source_document: $doc_id})-[*1..2]-(related:Entity)
                        WHERE related.source_document <> $doc_id
                        RETURN DISTINCT related.id as id,
                               related.name as name,
                               related.type as type,
                               related.source_document as source_doc
                    """
                    impacted = session.run(fallback_query, doc_id=doc_id).data()

                # Agrupar por documento
                affected_docs = {}
                for entity in impacted:
                    doc = entity['source_doc']
                    if doc not in affected_docs:
                        affected_docs[doc] = []
                    affected_docs[doc].append({
                        'id': entity['id'],
                        'name': entity['name'],
                        'type': entity['type']
                    })

                # Encontrar documentos relacionados directamente
                doc_relations_query = """
                    MATCH (d1:Document {id: $doc_id})
                    MATCH (d2:Document)
                    WHERE d1 <> d2
                    MATCH (e1:Entity {source_document: $doc_id})-[r]-(e2:Entity {source_document: d2.id})
                    RETURN DISTINCT d2.id as doc_id,
                           d2.filename as filename,
                           count(r) as connection_count,
                           collect(DISTINCT type(r)) as relationship_types
                """
                related_docs = session.run(doc_relations_query, doc_id=doc_id).data()

                logger.info(
                    f"[IMPACT] Documento {doc_id} afecta a {len(affected_docs)} documentos, "
                    f"{len(impacted)} entidades"
                )

                return {
                    "success": True,
                    "doc_id": doc_id,
                    "source_entities_count": len(source_entities),
                    "impacted_entities_count": len(impacted),
                    "affected_documents_count": len(affected_docs),
                    "affected_documents": affected_docs,
                    "related_documents": related_docs,
                    "impact_level": "high" if len(affected_docs) > 5 else "medium" if len(affected_docs) > 2 else "low",
                    "timestamp": datetime.utcnow().isoformat(),
                }

        except Exception as e:
            logger.error(f"Error analizando impacto del documento {doc_id}: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def get_cross_document_stats(self) -> Dict:
        """
        Obtiene estadísticas sobre relaciones entre documentos.

        Returns:
            Dict con estadísticas de conexiones entre documentos
        """
        if not self.driver:
            return {"success": False, "message": "Neo4j no disponible"}

        try:
            with self.driver.session() as session:
                # Contar relaciones entre documentos
                query = """
                    MATCH (e1:Entity)-[r]-(e2:Entity)
                    WHERE e1.source_document <> e2.source_document
                    RETURN e1.source_document as doc1,
                           e2.source_document as doc2,
                           count(r) as connections,
                           collect(DISTINCT type(r))[..5] as relationship_types
                    ORDER BY connections DESC
                    LIMIT 50
                """

                connections = session.run(query).data()

                # Contar documentos totales
                doc_count_query = "MATCH (d:Document) RETURN count(d) as total"
                total_docs = session.run(doc_count_query).single()['total']

                # Documentos aislados (sin conexiones con otros)
                isolated_query = """
                    MATCH (d:Document)
                    WHERE NOT EXISTS {
                        MATCH (e1:Entity {source_document: d.id})-[]-(e2:Entity)
                        WHERE e2.source_document <> d.id
                    }
                    RETURN d.id as doc_id, d.filename as filename
                """
                isolated = session.run(isolated_query).data()

                return {
                    "success": True,
                    "total_documents": total_docs,
                    "cross_document_connections": len(connections),
                    "isolated_documents": len(isolated),
                    "top_connections": connections[:10],
                    "isolated_document_list": isolated,
                    "timestamp": datetime.utcnow().isoformat(),
                }

        except Exception as e:
            logger.error(f"Error obteniendo estadísticas cross-document: {e}")
            return {"success": False, "error": str(e)}

    def get_top_entities(self, document_ids: List[str] = None, limit: int = 10) -> List[Dict]:
        """
        Get top N most connected entities from specified documents.
        Used for generating contextual example questions.

        Args:
            document_ids: List of document IDs to filter by (optional)
            limit: Maximum number of entities to return

        Returns:
            List of dicts with entity name, type, and connection count
        """
        if not self.driver:
            logger.warning("Neo4j no disponible para obtener top entities")
            return []

        try:
            with self.driver.session() as session:
                if document_ids and len(document_ids) > 0:
                    # Filter by document IDs
                    query = """
                        MATCH (n:Entity)-[r]-(m)
                        WHERE n.source_document IN $document_ids
                        WITH n, count(DISTINCT r) as connections, labels(n)[0] as entity_type
                        ORDER BY connections DESC
                        LIMIT $limit
                        RETURN n.name as entity, entity_type, connections
                    """
                    result = session.run(query, document_ids=document_ids, limit=limit)
                else:
                    # All entities
                    query = """
                        MATCH (n:Entity)-[r]-(m)
                        WITH n, count(DISTINCT r) as connections, labels(n)[0] as entity_type
                        ORDER BY connections DESC
                        LIMIT $limit
                        RETURN n.name as entity, entity_type, connections
                    """
                    result = session.run(query, limit=limit)

                entities = []
                for record in result:
                    entities.append({
                        "name": record["entity"],
                        "type": record["entity_type"],
                        "connections": record["connections"]
                    })

                logger.info(f"[TOP_ENTITIES] Found {len(entities)} entities from {len(document_ids) if document_ids else 'all'} documents")
                return entities

        except Exception as e:
            logger.error(f"Error obteniendo top entities: {e}")
            return []

    def close(self):
        """Cierra la conexión a Neo4j"""
        if self.driver:
            self.driver.close()
            logger.info("Conexión a Neo4j cerrada")
