# GraphRagExec

A lightweight, fully distributable **Graph-RAG** (Retrieval Augmented Generation) local AI server for Windows and Linux. Combines vector similarity search with knowledge graph traversal for intelligent document retrieval and analysis.

---

## Overview

GraphRagExec is a local AI server that merges two complementary retrieval paradigms:

- **Vector Database** (ChromaDB) -- semantic similarity search over document embeddings
- **Graph Database** (Kuzu) -- entity relationships and knowledge graph traversal via Cypher queries
- **External LLM APIs** (Ollama, OpenAI-compatible) -- embeddings generation and conversational chat

The result is a **hybrid retrieval system** where vector search finds semantically relevant chunks while graph traversal discovers structurally and relationally connected information that pure vector search would miss.

GraphRagExec can be packaged into a single `.exe` file using PyInstaller, making it distributable in corporate environments where Python installation may be restricted.

### Key Features

- **Hybrid Search**: Combines vector embeddings with graph relationships for superior retrieval
- **Knowledge Graph Extraction**: Automatically extracts entities and relationships from documents
  - **Regex-based extraction**: Fast, CPU-only processing with pattern matching
  - **LLM-based extraction**: Accurate, GPU-accelerated via Ollama or compatible API
- **Multiple Document Formats**: PDF, DOCX, TXT, Markdown, Excel
- **Google Drive Integration**: OAuth 2.0 authentication, folder browsing, file selection, and direct import into the RAG pipeline
- **Document Libraries**: Organize documents into separate searchable collections
- **Agentic AI Framework**: Autonomous agents with tool-calling, human-in-the-loop approval, and multi-agent orchestration
- **Multi-Agent Orchestration**: An Orchestrator agent delegates sub-tasks to specialized agents and synthesizes their results
- **Web Interface**: Built-in drag-and-drop UI for document management, chat, and agent interaction
- **Per-Message Copy & Clear Chat**: Copy any individual chat message or clear the entire conversation with one click
- **Persistent Storage**: Data stored in `%APPDATA%` (Windows) or `~/.local/share` (Linux) -- survives executable updates
- **Configurable Relationships**: Enable/disable 14+ relationship types across 6 categories
- **OpenAI-Compatible API**: Works with Ollama, OpenAI, Azure OpenAI, and any compatible endpoint

---

## Technology Stack

### Core Framework

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Web Framework | [FastAPI](https://fastapi.tiangolo.com/) | 0.109.2 | High-performance async REST API with automatic OpenAPI docs |
| ASGI Server | [Uvicorn](https://www.uvicorn.org/) | 0.27.1 | Production-grade ASGI server with multi-worker support |
| Data Validation | [Pydantic](https://docs.pydantic.dev/) | 2.6.1 | Type-safe request/response validation and settings management |

### Databases

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Vector Database | [ChromaDB](https://www.trychroma.com/) | 0.4.22 | Embedding storage, cosine similarity search, persistent collections |
| Graph Database | [Kuzu](https://kuzudb.com/) | 0.3.2 | Embedded graph database with Cypher query language, entity-relationship storage |

ChromaDB stores document chunk embeddings (384-768 dimensions depending on the model) with metadata, enabling semantic similarity search. Kuzu stores entities and their relationships as a property graph, enabling traversal queries that discover connections across documents.

### AI & LLM Integration

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| API Client | [OpenAI SDK](https://github.com/openai/openai-python) | ≥1.66.0 | Unified client for Ollama, OpenAI, Azure OpenAI, and any compatible endpoint |
| HTTP Client | [httpx](https://www.python-httpx.org/) | ≥0.27 | Async HTTP for API communication |

The AI client operates in two modes:
1. **Embeddings**: Converts text chunks into vector representations (batch processing, max 10 items per request)
2. **Chat Completions**: Powers RAG-based Q&A and LLM-based entity extraction

### Agentic AI Framework

The agent framework is implemented from scratch — no third-party agent library is used at runtime.

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Agent Models | [Pydantic](https://docs.pydantic.dev/) | ≥2.10 | `AgentDefinition`, `AgentTask`, `PendingApproval` data models and validation |
| Execution Loop | Python `asyncio` | stdlib | Iterative LLM→tool→result loop; `asyncio.Event` coordinates human-in-the-loop approval pauses |
| LLM Tool Calling | [OpenAI SDK](https://github.com/openai/openai-python) | ≥1.66.0 | Sends tool schemas to the LLM and parses `[TOOL_CALL]` responses |
| Streaming | Server-Sent Events (SSE) | — | Pushes thinking / tool-call / tool-result / final-answer events to the browser in real time |
| Tool Registry | Built-in (`agent_tools.py`) | — | 8 read-only document tools + `delegate_to_agent` for multi-agent orchestration |
| Agent Storage | JSON / `pathlib` | stdlib | Agent definitions and task state persisted to the app data directory |

Key design decisions:
- **No framework lock-in**: the `AgentExecutor` loop calls the OpenAI-compatible API directly, so any Ollama or OpenAI-compatible model works without adapters.
- **Human-in-the-loop**: each tool call can be paused for user approval via `asyncio.Event`; the auto-approve toggle bypasses this.
- **Multi-agent orchestration**: the `delegate_to_agent` tool lets an Orchestrator spin up sub-agents and collect their results, with delegation depth capped at 1 to prevent recursion.

### Document Processing

| Component | Technology | Version | Formats |
|-----------|------------|---------|---------|
| PDF | [PyPDF](https://pypdf.readthedocs.io/) | 4.0.1 | `.pdf` -- page-by-page text extraction |
| Word | [python-docx](https://python-docx.readthedocs.io/) | 1.1.0 | `.docx`, `.doc` -- paragraphs and tables |
| Excel | [openpyxl](https://openpyxl.readthedocs.io/) | 3.1.2 | `.xlsx`, `.xls` -- sheet-by-sheet cell content |
| Markdown | [markdown](https://python-markdown.github.io/) | 3.5.2 | `.md`, `.markdown` -- HTML conversion with tag stripping |
| Plain Text | Built-in | -- | `.txt` -- direct ingestion with encoding detection |

### Google Drive Integration

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Drive API | [google-api-python-client](https://github.com/googleapis/google-api-python-client) | 2.100.0+ | Google Drive v3 API for file listing, downloading, and exporting |
| Auth | [google-auth](https://google-auth.readthedocs.io/) | 2.23.0+ | OAuth 2.0 credential management and token refresh |
| OAuth Flow | [google-auth-oauthlib](https://google-auth-oauthlib.readthedocs.io/) | 1.1.0+ | Desktop app OAuth consent flow (code-copy method) |

Google Workspace files (Docs, Sheets, Slides) are automatically exported to a configurable format (PDF, DOCX, or TXT) before ingestion.

### Frontend

| Component | Technology | Purpose |
|-----------|------------|---------|
| Markup | HTML5 | Single-page application structure |
| Styling | CSS3 | CSS custom properties, flexbox layout, responsive design |
| Logic | Vanilla JavaScript (ES6+) | Zero-dependency frontend -- no build step required |
| Streaming | Server-Sent Events (SSE) | Real-time progress during document processing |

### Build & Distribution

| Component | Technology | Version | Purpose |
|-----------|------------|---------|---------|
| Packaging | [PyInstaller](https://pyinstaller.org/) | 6.4.0 | Single-file executable for Windows/Linux distribution |

The build script handles hidden imports (89+ modules), data file bundling, metadata copying, and collect-all configuration for packages that PyInstaller cannot auto-detect.

---

---

## Quick Start

### Prerequisites

- Python 3.10+ (for development)
- [Ollama](https://ollama.ai/) running locally (recommended) or another OpenAI-compatible API
- Required Ollama models:
  ```bash
  ollama pull nomic-embed-text    # For embeddings
  ollama pull llama3.2            # For chat/graph extraction
  ```

### Development Mode

```bash
# Clone the repository
git clone https://github.com/OndeNoHay/GraphRagExec.git
cd GraphRagExec

# Create virtual environment
python -m venv venv

# Activate virtual environment
# Windows:
venv\Scripts\activate
# Linux/Mac:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the server
python run.py
```

The server starts at `http://127.0.0.1:8000`

### Command Line Options

```bash
python run.py                    # Start with defaults
python run.py --port 8080        # Custom port
python run.py --host 0.0.0.0     # Bind to all interfaces
python run.py --debug            # Enable debug mode with auto-reload
python run.py --workers 4        # Multiple worker processes
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `GRAPHRAGEXEC_HOST` | `127.0.0.1` | Server host binding |
| `GRAPHRAGEXEC_PORT` | `8000` | Server port |
| `GRAPHRAGEXEC_DEBUG` | `false` | Enable debug mode |
| `GRAPHRAGEXEC_TOP_K_RESULTS` | `10` | Default search results limit |
| `GRAPHRAGEXEC_SIMILARITY_THRESHOLD` | `0.5` | Minimum similarity score |
| `GRAPHRAGEXEC_KUZU_BUFFER_POOL_SIZE` | `1073741824` | Kuzu buffer pool (1GB) |

---

## Building the Executable

### Standard Build

```bash
# Build single-file executable
python build.py

# Output: dist/GraphRagExec.exe (Windows) or dist/GraphRagExec (Linux)
```

### Build Options

```bash
python build.py --onefile    # Single file executable (default)
python build.py --onedir     # Directory with dependencies
python build.py --debug      # Include debug information
python build.py --no-clean   # Keep previous build artifacts
```

### Build Output

After a successful build:
- **Windows**: `dist/GraphRagExec.exe`
- **Linux**: `dist/GraphRagExec`

The executable is self-contained and can be distributed without Python. Expected size is 200-500MB as it bundles all dependencies including ChromaDB, Kuzu, ONNX Runtime, and Google API libraries.

### Post-Build Steps

1. Run the executable: `dist/GraphRagExec.exe`
2. Open browser: `http://127.0.0.1:8000`
3. Configure AI API in Settings (gear icon)
4. Import documents via drag-and-drop
5. API documentation: `http://127.0.0.1:8000/docs`

---

## Google Drive Integration

### Setup

1. **Create Google Cloud Project**: Go to [Google Cloud Console](https://console.cloud.google.com/), create a project, and enable the Google Drive API.
2. **Create OAuth Credentials**: In APIs & Services > Credentials, create an OAuth 2.0 Client ID for a "Desktop app".
3. **Download credentials.json**: Download the JSON file from the credentials page.

### Google Workspace (Company) Accounts

The integration works with both personal Gmail and Google Workspace (company) accounts:

| Account Type | Requirements |
|--------------|--------------|
| **Personal Gmail** | OAuth consent screen can be in "Testing" mode. Add your email as a test user. |
| **Google Workspace** | Ask your admin to either: (a) add the app's OAuth client ID to the allowed list, or (b) set the consent screen to "Internal" for your organization. |

For Workspace accounts where the admin has restricted third-party apps:
1. The Workspace admin can approve the app via Admin Console > Security > API Controls > App Access Control
2. Alternatively, the admin can add users as "Test users" in the OAuth consent screen configuration

The application only requests read-only access (`drive.readonly` scope) and cannot modify or delete files in your Drive.

### Usage

1. Open Settings (gear icon) in the web interface.
2. Under **Google Drive**, click **Upload Credentials** and select your `credentials.json`.
3. Click **Connect to Google Drive**. A URL will appear -- open it in your browser.
4. Complete the Google consent flow and copy the authorization code.
5. Paste the code back into the application and click **Submit**.
6. Once connected, go to the **Import** tab and click **Browse Google Drive**.
7. Navigate folders, select files, and click **Import Selected** to ingest them into the current library.

Google Workspace files (Docs, Sheets) are automatically exported to your configured format (PDF, DOCX, or TXT) before processing.

---

## Web Interface

Access the web UI at `http://127.0.0.1:8000`:

- **Libraries**: Create and manage document collections
- **Documents**: Drag-and-drop file upload with real-time progress tracking
- **Search**: Hybrid search with independent vector and graph toggles
- **Chat**: Conversational Q&A with RAG context and source citations; agents can be selected from the same tab for agentic interactions
  - **Per-message copy button**: Copy any individual message to the clipboard
  - **Clear chat button**: Reset the conversation in one click
  - **Auto-approve toggle**: Let agents run fully autonomously without per-step approval prompts
  - **Agent info popup**: Inspect an agent's full configuration (system prompt, tools, parameters) inline
- **Agents**: Create, configure, and manage autonomous agents with custom system prompts and tool permissions
- **Google Drive**: Browse, select, and import files from Google Drive
- **Settings**: Configure AI API, embedding models, graph extraction, and Google Drive

### Graph Extraction Methods

In Settings, you can choose between:
- **Regex** (default): Fast CPU-based extraction using pattern matching for proper nouns, emails, and URLs
- **LLM**: Accurate GPU-accelerated extraction using your configured chat model, with structured entity and relationship output

---

## Agent Framework

GraphRagExec ships a built-in agentic AI framework that runs autonomous multi-step reasoning over the document libraries. Agents are configured via the **Agents** tab in the web UI or via the REST API; they are invoked directly from the **Chat** tab.

> Full technical documentation: [docs/AGENTIC_FRAMEWORK.md](docs/AGENTIC_FRAMEWORK.md)

### How agents work

1. The user selects an agent from the Chat tab dropdown and sends a message.
2. The backend runs an **AgentExecutor** loop: the LLM decides which tool to call, the tool is executed, the result is fed back, and the loop repeats until the task is complete or the iteration limit is reached.
3. Every step (thinking, tool call, tool result, final response) is streamed to the browser via Server-Sent Events.

### Human-in-the-loop approval

Agents can be configured with `approval_mode: always`, which pauses execution before each tool call and asks the user to **Approve** or **Reject** inline in the chat. Enabling the **Auto-approve** toggle bypasses the prompt so the agent runs fully autonomously.

### Multi-agent orchestration

The built-in **Orchestrator** agent can delegate sub-tasks to specialized agents via the `delegate_to_agent` tool, collect their results, and synthesize a final answer — all from a single user message.

```
User: "Review all safety documents and summarize the findings"
  ↓
Orchestrator
  ├─ search_documents("safety")
  ├─ delegate_to_agent → Document Reviewer → findings
  ├─ delegate_to_agent → Summary Generator → summary
  └─ Synthesized response
```

Delegation depth is capped at 1 to prevent infinite recursion; sub-agents automatically run in auto-approve mode.

### Available tools

| Tool | Description |
|------|-------------|
| `search_documents` | Vector similarity search over document chunks |
| `search_graph` | Knowledge-graph traversal search |
| `get_entities` | List entities extracted from a specific document |
| `get_relationships` | Get relationships for a specific entity |
| `compare_documents` | Side-by-side comparison of two documents |
| `get_document_chunks` | Retrieve raw text chunks from a document |
| `summarize_text` | Generate an LLM summary of provided text |
| `delegate_to_agent` | Delegate a sub-task to another agent (orchestrators only) |

### Built-in agent templates

Five read-only templates are included. Clone any of them to create an editable copy.

| Template | Purpose | Key tools |
|----------|---------|-----------|
| **Document Reviewer** | Find inconsistencies, validate cross-references | search_documents, compare_documents, get_entities |
| **Compliance Checker** | Verify documents meet standards; produce compliance matrix | search_documents, search_graph, get_relationships |
| **Summary Generator** | Create executive summaries from document collections | search_documents, get_entities, summarize_text |
| **Change Impact Analyzer** | Trace how a document change affects related documents | search_graph, get_relationships, compare_documents |
| **Orchestrator** | Decompose complex questions and delegate to specialized agents | search_documents, search_graph, delegate_to_agent |

---

## API Endpoints

### Documents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/documents/upload` | Upload and process a document (SSE progress stream) |
| `POST` | `/api/documents/upload/text` | Ingest pasted text content |
| `GET` | `/api/documents/sources/{library_id}` | List documents in a library |
| `GET` | `/api/documents/source/{library_id}/{file}/details` | Get chunk and entity details |
| `DELETE` | `/api/documents/delete/{library_id}/{file}` | Delete a document and its data |

### Search & Chat

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/search` | Hybrid search (vector + graph) |
| `POST` | `/api/search/chat` | Chat with RAG context and citations |
| `POST` | `/api/search/chat/agent` | Run an agent and stream SSE progress events |

### Agents

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/agents` | List all agent definitions |
| `POST` | `/api/agents` | Create a new agent definition |
| `GET` | `/api/agents/{id}` | Get a specific agent |
| `PUT` | `/api/agents/{id}` | Update an agent definition |
| `DELETE` | `/api/agents/{id}` | Delete an agent |
| `POST` | `/api/agents/{id}/run` | Run an agent and stream SSE (agents-tab endpoint) |
| `POST` | `/api/agents/approve` | Approve or reject a pending tool call |
| `GET` | `/api/agents/tasks/{task_id}` | Get task status and result |

### Libraries

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/libraries` | List all libraries |
| `POST` | `/api/libraries` | Create a new library |
| `PUT` | `/api/libraries/{id}` | Update library metadata |
| `DELETE` | `/api/libraries/{id}` | Delete a library and all its data |

### Settings

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/settings` | Get current settings |
| `PUT` | `/api/settings` | Update settings |
| `POST` | `/api/settings/test-connection` | Test AI API connectivity |

### Google Drive

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/google-drive/status` | Check authentication status |
| `POST` | `/api/google-drive/auth/credentials` | Upload OAuth credentials |
| `POST` | `/api/google-drive/auth/start` | Start OAuth flow |
| `POST` | `/api/google-drive/auth/complete` | Complete auth with code |
| `POST` | `/api/google-drive/disconnect` | Disconnect Google Drive |
| `GET` | `/api/google-drive/files` | List folder contents |
| `POST` | `/api/google-drive/import/stream` | Import files with SSE progress |

### System

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/health` | Health check (database status) |
| `GET` | `/docs` | OpenAPI interactive documentation |
| `GET` | `/redoc` | ReDoc documentation |

---

## Project Structure

```
GraphRagExec/
├── app/
│   ├── __init__.py                # Package metadata (version, app name)
│   ├── config.py                  # Configuration, settings models, persistence
│   ├── main.py                    # FastAPI application, lifespan, routers
│   ├── api/
│   │   └── routes/
│   │       ├── documents.py       # Document upload & management
│   │       ├── search.py          # Hybrid search, RAG chat & agent SSE endpoint
│   │       ├── libraries.py       # Library CRUD
│   │       ├── settings.py        # Settings API
│   │       ├── agents.py          # Agent CRUD, task management & approval
│   │       └── google_drive.py    # Google Drive auth & file operations
│   ├── models/
│   │   └── agents.py              # AgentDefinition, AgentTask Pydantic models
│   ├── services/
│   │   ├── ai_client.py           # OpenAI-compatible API client (embeddings + chat)
│   │   ├── vector_db.py           # ChromaDB service (collections, search)
│   │   ├── graph_db.py            # Kuzu service (entities, relationships, traversal)
│   │   ├── document_processor.py  # Multi-format parsing & text chunking
│   │   ├── library_manager.py     # Library lifecycle management
│   │   ├── agent_manager.py       # Agent definitions store & task lifecycle
│   │   ├── agent_executor.py      # Iterative LLM loop, SSE event generator
│   │   ├── agent_tools.py         # Tool registry (8 tools + delegate_to_agent)
│   │   └── google_drive.py        # Google Drive OAuth & file operations
│   └── static/
│       ├── index.html             # Web interface (single-page application)
│       ├── css/style.css          # Styling (CSS variables, responsive layout)
│       └── js/app.js              # Frontend logic (vanilla ES6+, SSE handling)
├── build.py                       # PyInstaller build configuration
├── run.py                         # Application entry point (Uvicorn launcher)
├── requirements.txt               # Python dependencies
├── pyproject.toml                 # Project metadata & dev tool config
└── README.md
```

## Data Storage

Data is stored persistently outside the application directory:

**Windows:** `%APPDATA%\GraphRagExec\`
**Linux/Mac:** `~/.local/share/GraphRagExec/`

```
GraphRagExec/
├── vector_db/     # ChromaDB persistent storage (embeddings, metadata)
├── graph_db/      # Kuzu graph database (entities, relationships)
├── config/        # settings.json, libraries.json, google_drive_token.json
└── logs/          # Application logs
```

This ensures data persists when you update or replace the executable.

---

## Configuration

### AI Settings (via Web UI or API)

| Setting | Default | Description |
|---------|---------|-------------|
| `api_base_url` | `http://localhost:11434/v1` | OpenAI-compatible API URL |
| `api_key` | `ollama` | API key (use "ollama" for local Ollama) |
| `embedding_model` | `nomic-embed-text` | Model for generating embeddings |
| `chat_model` | `llama3.2` | Model for chat and LLM graph extraction |
| `chunk_size` | `512` | Text chunk size (characters) for processing |
| `chunk_overlap` | `50` | Overlap between consecutive chunks |

### Graph Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `enable_graph_extraction` | `true` | Enable/disable graph extraction during ingestion |
| `extraction_method` | `regex` | `regex` (fast, CPU) or `llm` (accurate, GPU) |
| `max_entities_per_chunk` | `15` | Maximum entities extracted per chunk |
| `extract_proper_nouns` | `true` | Extract capitalized names, organizations |
| `extract_emails` | `false` | Extract email addresses as entities |
| `extract_urls` | `false` | Extract URLs as entities |

### Relationship Types

Configure which relationship types to extract (all individually toggleable):

| Category | Types | Description |
|----------|-------|-------------|
| **Document Structure** | NEXT_CHUNK, SAME_PAGE | Sequential and co-located chunk relationships |
| **Component** | PART_OF, CONNECTS_TO, SUPPLIES_TO, CONTROLS | Physical/logical component relationships |
| **Process** | PRECEDES, TRIGGERS, REQUIRES | Temporal and causal process flows |
| **Semantic** | CO_OCCURS, RELATED_TO | Co-occurrence at sentence/chunk level |
| **Hierarchy** | IS_A, HAS_PROPERTY | Classification and attribute relationships (disabled by default) |
| **Reference** | REFERENCES, CITES | Cross-document references and citations (disabled by default) |

### Google Drive Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `enabled` | `false` | Enable Google Drive integration |
| `export_format` | `pdf` | Export format for Google Workspace files: `pdf`, `txt`, `docx` |

---

## Security

> A full white-box static security assessment is available in [SECURITY_REPORT.md](SECURITY_REPORT.md).

### Current Status

The application is a **Proof of Concept (PoC)** and is **not production-ready** from a security standpoint. It is designed for **single-user, localhost-only** use.

### Pending Security Issues

| ID | Title | Severity | Status |
|----|-------|----------|--------|
| SEC-001 | No authentication or authorisation on any endpoint | **Critical** | Open |
| SEC-002 | Wildcard CORS + `allow_credentials=True` | **High** | Open |
| SEC-003 | SSRF via configurable AI backend URL | **High** | Open |
| SEC-004 | Unrestricted file upload size (DoS vector) | **High** | Open |
| SEC-005 | Stored XSS via filename in JS event handlers | **Medium** | Open |
| SEC-006 | Cypher queries built via string interpolation | **Medium** | Open |
| SEC-007 | Indirect prompt injection via document content | **Medium** | Open |
| SEC-008 | AI API key stored in plaintext on disk | **Medium** | Open |
| SEC-009 | No TLS (HTTP only) | **Medium** | Open |
| SEC-010 | Swagger/OpenAPI docs publicly accessible | **Medium** | Open |
| SEC-011 | Internal exception details leaked in HTTP 500 responses | **Medium** | Open |
| SEC-012 | No rate limiting on any endpoint | **Medium** | Open |
| SEC-013 | Race condition in agent delegation depth counter | **Low** | Open |
| SEC-014 | Pinned dependencies include packages with known CVEs | **Low** | Open — `python-multipart 0.0.9` has CVE-2024-24762 (DoS) |

### Deployment Guidance

The default configuration binds to `127.0.0.1` only, which limits exposure to the local machine. **Do not expose to a network** until at minimum SEC-001 (authentication) and SEC-002 (CORS) are resolved. See [SECURITY_REPORT.md](SECURITY_REPORT.md) for fix guidance and a prioritised hardening checklist.

---

## Potential Future Features

The following features represent high-value extensions for technical authors, document validators, and knowledge management workflows.

### Image-to-Vector/Graph Processing

Process images embedded in documents or uploaded directly:

- **OCR extraction**: Extract text from scanned PDFs and images using Tesseract or cloud vision APIs, then feed into the existing chunking and embedding pipeline.
- **Image captioning**: Use multimodal models (e.g., LLaVA via Ollama) to generate text descriptions of diagrams, schematics, and figures, then embed those descriptions alongside document text.
- **Diagram entity extraction**: Parse technical diagrams (flowcharts, P&IDs, circuit diagrams) to extract entities and relationships directly into the knowledge graph.
- **Visual similarity search**: Store image embeddings (CLIP or similar) in a parallel ChromaDB collection, enabling retrieval of visually similar figures across documents.

This would be particularly valuable for engineering documentation where diagrams carry as much information as text.

### Prompt Library

A managed collection of reusable prompt templates:

- **Domain-specific extraction prompts**: Pre-built prompts for legal documents, technical manuals, medical records, financial reports -- each tuned to extract the right entity types and relationships.
- **Search prompt templates**: Saved search patterns for common queries (e.g., "find all safety requirements related to {component}", "list dependencies between {system A} and {system B}").
- **Chat system prompts**: Configurable system prompts that define the AI persona and response format for different use cases (technical review, summarization, compliance checking).
- **Prompt versioning**: Track changes to prompts over time, allowing rollback and A/B comparison of extraction quality.
- **Import/export**: Share prompt libraries across teams as JSON/YAML files.

### Agent Framework Enhancements

The agent framework (now implemented) has several planned improvements:

- **Conversation memory**: Agents currently start fresh on every message; adding per-session memory would enable multi-turn agentic conversations.
- **Parallel sub-agent execution**: The orchestrator currently calls sub-agents sequentially; parallel delegation would significantly reduce end-to-end latency for complex tasks.
- **Write tools**: Current tools are read-only; adding write tools (create document, update graph) would unlock automation workflows.
- **MCP server integration**: The `mcp_servers` field on AgentDefinition exists but is not yet wired up; connecting Model Context Protocol servers would make external tools available to agents.
- **Native function calling**: Replace the text-based `[TOOL_CALL]` parsing with native OpenAI-compatible function calling for more reliable tool invocation.
- **Tool-call retries**: Automatic retry with error feedback when a tool call fails.

### Additional Integration Points

- **SharePoint / OneDrive**: Similar OAuth-based integration for Microsoft 365 environments, using the Microsoft Graph API.
- **Confluence / Jira**: Import wiki pages and issue descriptions for teams using Atlassian tools.
- **S3 / Azure Blob Storage**: Bulk import from cloud storage buckets for large document sets.
- **Webhook-driven ingestion**: Watch a folder or endpoint for new documents and auto-ingest them.
- **Export to knowledge management tools**: Push extracted entities and relationships to Neo4j, Obsidian, or other graph-based tools.

### Document Comparison & Versioning

- **Diff analysis**: Compare two versions of a document, highlighting changes in entities and relationships in the knowledge graph.
- **Version tracking**: Maintain historical versions of document embeddings to see how content evolved.
- **Merge detection**: Identify when separate documents converge on the same entities or topics.

### Enhanced Graph Visualization

- **Interactive graph viewer**: Browser-based visualization of the knowledge graph using D3.js or Cytoscape.js, allowing users to explore entity relationships visually.
- **Cluster detection**: Identify and highlight document clusters, orphaned entities, and relationship patterns.
- **Path finding**: Visual display of the shortest path between two entities across the knowledge graph.

---

## System Requirements

### Development
- Python 3.10+
- 4GB RAM (8GB recommended)
- 2GB disk space
- Ollama or OpenAI-compatible API

### Production (.exe)
- Windows 10/11 (64-bit) or Linux
- 4GB RAM minimum (8GB recommended for LLM extraction)
- 500MB disk for executable
- 2GB+ disk for data storage
- GPU recommended for LLM-based graph extraction

---

## Troubleshooting

### "Connection refused" to API
Ensure Ollama is running: `ollama serve`

### "Model not found"
Pull required models:
```bash
ollama pull nomic-embed-text
ollama pull llama3.2
```

### "Buffer manager exception: Failed to claim a frame"
Increase buffer pool size via environment variable:
```bash
export GRAPHRAGEXEC_KUZU_BUFFER_POOL_SIZE=2147483648  # 2GB
```

### "Permission denied" errors
Ensure write access to data directory:
- Windows: `%APPDATA%\GraphRagExec\`
- Linux: `~/.local/share/GraphRagExec/`

### Slow graph extraction
Switch to regex-based extraction in Settings if LLM extraction is too slow, or ensure GPU acceleration is working with Ollama.

### Large executable size
The `.exe` includes all dependencies (ChromaDB, Kuzu, ONNX Runtime, Google APIs). Size of 200-500MB is expected.

### Google Drive authentication issues
- Ensure your Google Cloud project has the Drive API enabled.
- The OAuth consent screen must be configured (can be in "Testing" mode for personal use).
- If the token expires, click **Disconnect** and re-authenticate.

---

## License

MIT License - See LICENSE file for details.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes with proper typing
4. Run tests and linting
5. Submit a pull request
