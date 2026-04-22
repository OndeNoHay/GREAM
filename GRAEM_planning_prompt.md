# Prompt de planificación — GRAEM (GraphRAG-Agent-MCP)

> Pega este prompt como mensaje inicial a Claude en VSCode (extensión Claude o Claude Code) una vez abierto el repositorio renombrado a `GRAEM`.

---

## Rol

Eres un arquitecto de software senior especializado en sistemas agénticos locales, Retrieval-Augmented Generation (RAG) y Model Context Protocol (MCP). Vas a trabajar con JJO, AI Ambassador assistant en ATEXIS Group — empresa de ~1.500 personas especializada en aerospace, defense, naval y rail, con servicios core en technical authoring S1000D/DITA, specialized training, maintenance engineering e ILS/LSA.

## Punto de partida

Este repositorio (`GRAEM`) es una copia renombrada de `GraphRagExec`, un PoC GraphRAG full-stack ya funcional con:

- Backend FastAPI con **sistema de agentes ligero ya integrado** (loop razonar → seleccionar herramienta → ejecutar → observar).
- Neo4j como knowledge graph.
- ChromaDB como vector store.
- Frontend React.
- Ejecución de código integrada.
- Dataset de prueba: Data Modules S1000D-compliant de Airbus A320 ATA 32 (landing gear).
- Estado: completado hasta fase 5 de 6 (Graph RAG query system).

**No vas a reescribir nada de lo anterior.** Lo extiendes.

## Objetivo de GRAEM

Añadir una **capa Model Context Protocol (MCP)** al sistema de agentes existente para que el agente pueda **actuar sobre herramientas externas reales**: editor Office (Word, Excel, PowerPoint), navegador web, sistema de archivos y servidores MCP customizados para el dominio S1000D. El entregable es un demostrador visible frente a cliente donde el agente opera herramientas reales en pantalla (no solo genera texto).

**Caso de uso prioritario — PoC 1 "S1000D Co-Author":**
A partir de un documento fuente (boletín de servicio OEM, sección CMM) depositado en una carpeta de input:
1. El agente lo parsea.
2. Consulta el knowledge graph y ChromaDB para detectar DMs relacionados.
3. Genera un Procedural Data Module S1000D válido (conforme a BREX del proyecto y ASD-STE100).
4. Lo abre en un viewer XML en el navegador (Playwright MCP).
5. Emite un changelog en Word sobre plantilla ATEXIS.
6. Emite un slide ejecutivo de resumen en PowerPoint.
7. Todo local, sin un solo byte saliendo de la red.

**Caso de uso secundario — PoC 2 "Field Maintenance Copilot":** copiloto de troubleshooting offline que navega el IETM solo. Se planificará tras PoC 1, fuera de scope de este documento.

## Restricciones críticas

1. **Confidencialidad absoluta.** Todo on-premise. Transporte MCP por `stdio` (sin red). Rechazar cualquier dependencia que requiera API externa para funcionar en runtime.
2. **LLM local.** `qwen3:14b` vía Ollama como modelo primario (RTX 3070). Alternativas válidas: `qwen2.5:14b`, `llama3.3:8b`, `phi-4:14b`.
3. **Reutilización máxima de dependencias.** Antes de añadir nada, inspecciona `requirements.txt`, `pyproject.toml` y `package.json` existentes y prefiere siempre lo que ya está instalado.
4. **Paquetes nuevos — criterios de aceptación obligatorios** (aplicar uno por uno y dejar constancia en el plan):
   - Fuente reputada: repos oficiales (Anthropic / `modelcontextprotocol` org, Microsoft, AWS Labs, Google, Meta) **o** proyectos comunitarios con ≥ 500 stars en GitHub, último commit < 90 días y ≥ 1 maintainer activo.
   - Sin CVEs críticos o altos abiertos (consultar GitHub Security Advisories y PyPI/npm audit).
   - Licencia compatible con uso comercial: MIT, Apache-2.0, BSD. Excluir GPL/AGPL salvo justificación explícita.
   - Descargas PyPI/npm razonables (> 1 k/mes) o respaldo institucional claro.
   - Sin riesgo de typosquatting: nombre del paquete verificado contra el repo oficial.
5. **No reemplazar el sistema agéntico existente.** Envolverlo: el dispatcher de tools debe entender el namespace `mcp:<server>.<tool>` y delegar al cliente MCP.
6. **Separación read/write** en todos los MCPs, con uso de `readOnlyHint` / `destructiveHint` en tool annotations para gobernanza visible al cliente.

## Tu tarea

Producir un documento `PLAN.md` en la raíz del repo, **en español** (términos técnicos en inglés), con las siguientes secciones **en este orden y sin escribir código fuente todavía**:

### 1. Inventario del estado actual
- Árbol de directorios principal (máx. 3 niveles).
- Dependencias Python y JS con versiones exactas, agrupadas por propósito (web, agent, rag, llm, data, tooling).
- Diagnóstico del sistema de agentes existente: punto de entrada, contrato de tools, cómo se registra hoy una herramienta nueva.
- Endpoints FastAPI relevantes.
- Estado de tests si los hay.

### 2. Gap analysis
- Qué se reutiliza tal cual.
- Qué necesita extensión (p. ej. el dispatcher).
- Qué falta completamente (cliente MCP, registry, servidores custom, plantillas ATEXIS).

### 3. Arquitectura propuesta
- Diagrama Mermaid mostrando: React UI → FastAPI orchestrator → Agent loop → MCP Client → [MCP Servers off-the-shelf | MCP Servers custom] → herramientas externas / Neo4j / ChromaDB / filesystem.
- Flujo paso a paso de una request típica del caso PoC 1.
- Decisión razonada entre **tool-calling directo sobre MCP** vs patrón **"code execution with MCP"** (Anthropic engineering blog, 2025). Recomendar una y justificar dado que el LLM es local y tiene contexto limitado.

### 4. Selección y auditoría de MCPs
Aplicar los criterios de aceptación del punto 4 de restricciones a cada candidato y marcar **APROBADO / REVISAR / RECHAZADO** con justificación y URL del repo con fecha del último commit.

**Off-the-shelf a evaluar:**
- `@modelcontextprotocol/server-filesystem` (oficial Anthropic).
- `@playwright/mcp` (oficial Microsoft).
- `office-powerpoint-mcp-server` de GongRzhe (python-pptx).
- Servidor MCP para `.docx` basado en python-docx — evaluar `meterlong/mcp-doc` y alternativas.
- `awslabs.document-loader-mcp-server` (AWS Labs) para parseo de fuentes.
- SDK/cliente MCP: comparar `mcp` (SDK oficial Python) vs `mcp-use` y recomendar uno.

**Custom a desarrollar (FastMCP, Python):**
- `s1000d-csdb`: queries sobre el CSDB (reutilizando el Neo4j y/o PostgreSQL existentes).
- `brex-validator`: validación XSD + Schematron sobre los DMs.
- `ste-checker`: verificación ASD-STE100 (word-list + heurísticas + LLM-as-judge local).

### 5. Selección y auditoría de paquetes Python y JS nuevos
Todos los paquetes adicionales necesarios más allá de los MCP servers (p. ej. `lxml`, `python-docx`, `python-pptx`, `saxonche` si hace falta Schematron, driver MCP). Para cada uno: nombre exacto, versión pinneada sugerida, propósito, criterios de aceptación verificados. Si alguno falla un criterio, propón alternativa.

### 6. Plan por fases
- **Fase 0** — Setup del repo renombrado, `.gitignore`, CI mínimo (lint + test).
- **Fase 1** — Cliente MCP + registry YAML + integración con el dispatcher del agente existente.
- **Fase 2** — Integración de 2 MCPs off-the-shelf simples (filesystem, document-loader) con smoke tests.
- **Fase 3** — Primer MCP custom: `s1000d-csdb`, consumiendo el Neo4j existente.
- **Fase 4** — Integración Playwright MCP + viewer XML en navegador.
- **Fase 5** — Office MCPs (pptx, docx) + plantillas ATEXIS.
- **Fase 6** — MCPs custom restantes: `brex-validator`, `ste-checker`.
- **Fase 7** — Hardening: sandboxing, tool annotations, auditoría, logging estructurado.
- **Fase 8** — Guion de demo a cliente con tiempos y plan de fallback.

Para cada fase: objetivo, entregables concretos, criterios de aceptación, estimación en jornadas-hombre.

### 7. Riesgos y mitigaciones
Al menos: capacidad de tool-calling del LLM local, overhead de contexto por tool definitions (considerar code execution pattern), prompt injection vía documento fuente, compatibilidad Windows/Linux de MCPs que usan COM, licencias de dependencias, concurrencia de escrituras Office, fragilidad de Playwright frente a cambios de UI del viewer IETM.

### 8. Checklist de confidencialidad
Verificaciones explícitas con casilla:
- [ ] Ningún MCP llama a internet en runtime.
- [ ] Firewall saliente bloqueado en entorno de demo.
- [ ] Logs exclusivamente locales.
- [ ] Plantillas ATEXIS versionadas en repo privado.
- [ ] Carpetas de input/output sandboxeadas en filesystem MCP.
- [ ] Playwright MCP con allowlist de URLs.
- [ ] Separación read/write explícita en todos los MCPs.
- [ ] Ollama escuchando solo en `127.0.0.1`.

## Reglas de ejecución

- **Lee antes de proponer.** Inspecciona el repo con las herramientas disponibles antes de afirmar qué hay o no hay. No asumir versiones de memoria.
- **Si un criterio falla** para un paquete, propón al menos una alternativa evaluada bajo los mismos criterios.
- **Cita las fuentes**: URL del repo, fecha del último commit o release, número de stars, licencia.
- **No escribas código fuente** todavía. Entregables de esta fase: `PLAN.md` en la raíz y, opcionalmente, diagramas Mermaid en `docs/`.
- **Preguntas a JJO**: si encuentras decisiones que requieran su input (p. ej. elección entre dos MCPs equivalentes, ruta exacta del CSDB, plantillas Office disponibles), lístalas al final en una sección **"Decisiones pendientes"** con opciones y recomendación.

Cuando termines, resume en **máximo 10 bullets** las decisiones clave tomadas y los siguientes pasos concretos.
