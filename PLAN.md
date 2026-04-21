# Agent Framework Implementation Plan

## Overview

This plan describes the implementation of an agent framework for GraphRagExec using **PydanticAI-slim**. The framework will enable autonomous document review, compliance checking, and multi-step reasoning while maintaining the project's lightweight, distributable nature.

### Key Requirements

1. **Create/Edit Agents**: Users can define custom agents with specific instructions and tool access
2. **Human Approval**: All consequential actions require user confirmation before execution
3. **MCP Compatibility**: Architecture supports future Model Context Protocol integration (PydanticAI has native MCP support)
4. **Lightweight**: Minimal additional dependencies (~5-10 MB)
5. **Local-First**: No telemetry, works with Ollama

---

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Web UI                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐    │
│  │ Agent       │  │ Agent       │  │ Task        │  │ Approval    │    │
│  │ Editor      │  │ Runner      │  │ Monitor     │  │ Dialog      │    │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘    │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────┼────────────────────────────────────┐
│                           FastAPI Routes                                 │
│  ┌─────────────────────────────────┴─────────────────────────────────┐  │
│  │                    /api/agents                                     │  │
│  │  POST /           - Create agent                                   │  │
│  │  GET  /           - List agents                                    │  │
│  │  GET  /{id}       - Get agent details                              │  │
│  │  PUT  /{id}       - Update agent                                   │  │
│  │  DELETE /{id}     - Delete agent                                   │  │
│  │  POST /{id}/run   - Execute agent task (SSE stream)               │  │
│  │  POST /approve    - Approve/reject pending action                  │  │
│  │  GET  /tasks      - List running/completed tasks                   │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────┼────────────────────────────────────┐
│                         Agent Services                                   │
│  ┌─────────────────────────────────┴─────────────────────────────────┐  │
│  │                     AgentManager (Singleton)                       │  │
│  │  - load_agents()      - Load from persistent storage               │  │
│  │  - save_agent()       - Persist agent definition                   │  │
│  │  - delete_agent()     - Remove agent                               │  │
│  │  - run_agent()        - Execute agent with task                    │  │
│  │  - get_pending()      - Get actions awaiting approval              │  │
│  │  - approve_action()   - Process user approval/rejection            │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                     │                                    │
│  ┌─────────────────────────────────┴─────────────────────────────────┐  │
│  │                     AgentExecutor (PydanticAI)                     │  │
│  │  - PydanticAI Agent with custom system prompt                      │  │
│  │  - Tool registry (GraphRagExec tools + MCP tools)                  │  │
│  │  - Human-in-the-loop checkpoints                                   │  │
│  │  - Streaming output via SSE                                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                     │                                    │
│  ┌─────────────────────────────────┴─────────────────────────────────┐  │
│  │                        Tool Registry                               │  │
│  │  Built-in Tools:                    MCP Tools (Future):            │  │
│  │  - search_documents()               - Via MCPServerStdio           │  │
│  │  - search_graph()                   - Via MCPServerHTTP            │  │
│  │  - get_entities()                   - Dynamic discovery            │  │
│  │  - get_relationships()                                             │  │
│  │  - compare_documents()                                             │  │
│  │  - get_document_chunks()                                           │  │
│  │  - summarize_text()                                                │  │
│  └───────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
┌────────────────────────────────────┼────────────────────────────────────┐
│                     Existing GraphRagExec Services                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────┐  │
│  │ AIClient     │  │ VectorDB     │  │ GraphDB      │  │ Library     │  │
│  │              │  │ Service      │  │ Service      │  │ Manager     │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Data Models

### Agent Definition

```python
# app/models/agents.py

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


class ToolPermission(str, Enum):
    """Tools that an agent can be granted access to."""
    SEARCH_DOCUMENTS = "search_documents"
    SEARCH_GRAPH = "search_graph"
    GET_ENTITIES = "get_entities"
    GET_RELATIONSHIPS = "get_relationships"
    COMPARE_DOCUMENTS = "compare_documents"
    GET_DOCUMENT_CHUNKS = "get_document_chunks"
    SUMMARIZE_TEXT = "summarize_text"
    # Future MCP tools added dynamically


class ApprovalMode(str, Enum):
    """When to require human approval."""
    ALWAYS = "always"           # Every tool call requires approval
    DESTRUCTIVE = "destructive" # Only destructive actions (future)
    NEVER = "never"             # No approval needed (use with caution)


class AgentDefinition(BaseModel):
    """Persistent agent definition."""
    id: str = Field(description="Unique agent identifier")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)

    # Agent behavior
    system_prompt: str = Field(
        description="Instructions that define the agent's behavior"
    )
    tools: list[ToolPermission] = Field(
        default_factory=list,
        description="Tools this agent is allowed to use"
    )

    # Constraints
    approval_mode: ApprovalMode = Field(default=ApprovalMode.ALWAYS)
    max_iterations: int = Field(default=10, ge=1, le=50)
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # MCP configuration (future-ready)
    mcp_servers: list[dict] = Field(
        default_factory=list,
        description="MCP server configurations for external tools"
    )


class AgentTask(BaseModel):
    """A task submitted to an agent."""
    id: str
    agent_id: str
    library_id: str
    prompt: str
    status: str  # pending, running, awaiting_approval, completed, failed
    created_at: datetime
    completed_at: Optional[datetime] = None
    result: Optional[str] = None
    error: Optional[str] = None


class PendingApproval(BaseModel):
    """An action awaiting user approval."""
    id: str
    task_id: str
    agent_id: str
    tool_name: str
    tool_args: dict
    description: str  # Human-readable description
    created_at: datetime
```

### Persistent Storage

Agent definitions stored at: `%APPDATA%/GraphRagExec/config/agents.json`

```json
{
  "agents": [
    {
      "id": "doc-reviewer",
      "name": "Document Reviewer",
      "description": "Reviews documents for completeness and consistency",
      "system_prompt": "You are a technical document reviewer...",
      "tools": ["search_documents", "get_entities", "compare_documents"],
      "approval_mode": "always",
      "max_iterations": 10,
      "temperature": 0.3,
      "mcp_servers": []
    }
  ]
}
```

---

## Tool Registry

### Built-in Tools

Each tool wraps existing GraphRagExec services:

| Tool | Description | Wraps |
|------|-------------|-------|
| `search_documents` | Vector similarity search | VectorDBService.search() |
| `search_graph` | Knowledge graph traversal | GraphDBService.search_by_entity() |
| `get_entities` | Extract entities from document | GraphDBService.get_entities() |
| `get_relationships` | Get entity relationships | GraphDBService.get_relationships() |
| `compare_documents` | Compare two documents | Custom (vector + graph diff) |
| `get_document_chunks` | Get raw text chunks | VectorDBService.get_chunks() |
| `summarize_text` | Generate text summary | AIClient.chat_completion() |

### Tool Implementation Pattern

```python
# app/services/agent_tools.py

from pydantic import BaseModel, Field
from app.services.ai_client import get_ai_client
from app.services.vector_db import get_vector_db_service


class SearchDocumentsArgs(BaseModel):
    """Arguments for document search."""
    query: str = Field(description="Search query")
    library_id: str = Field(description="Library to search in")
    top_k: int = Field(default=5, description="Number of results")


class SearchDocumentsResult(BaseModel):
    """Results from document search."""
    results: list[dict]
    total: int


async def search_documents(args: SearchDocumentsArgs) -> SearchDocumentsResult:
    """
    Search documents using vector similarity.
    Returns document references with relevance scores.
    """
    ai_client = get_ai_client()
    vector_db = get_vector_db_service()

    query_embedding = ai_client.generate_embedding(args.query)
    results = vector_db.search(
        library_id=args.library_id,
        query_embedding=query_embedding,
        top_k=args.top_k
    )

    return SearchDocumentsResult(results=results, total=len(results))
```

---

## Human-in-the-Loop Approval

### Approval Flow

```
User submits task
        │
        ▼
┌───────────────────┐
│ Agent processes   │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐     ┌───────────────────┐
│ Agent wants to    │────▶│ Create            │
│ call a tool       │     │ PendingApproval   │
└───────────────────┘     └─────────┬─────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ SSE: approval_needed │
                         │ sent to UI          │
                         └──────────┬──────────┘
                                    │
                         ┌──────────▼──────────┐
                         │ Agent pauses        │
                         │ (awaiting_approval) │
                         └──────────┬──────────┘
                                    │
      ┌─────────────────────────────┼─────────────────────────────┐
      │                             │                             │
      ▼                             ▼                             ▼
┌─────────────┐           ┌─────────────┐           ┌─────────────┐
│ APPROVE     │           │ REJECT      │           │ MODIFY      │
└──────┬──────┘           └──────┬──────┘           └──────┬──────┘
       │                         │                         │
       ▼                         ▼                         ▼
┌─────────────┐           ┌─────────────┐           ┌─────────────┐
│ Execute     │           │ Skip tool,  │           │ Execute     │
│ tool as-is  │           │ inform agent│           │ with mods   │
└──────┬──────┘           └──────┬──────┘           └──────┬──────┘
       │                         │                         │
       └─────────────────────────┼─────────────────────────┘
                                 │
                                 ▼
                      ┌───────────────────┐
                      │ Agent continues   │
                      │ with result       │
                      └─────────┬─────────┘
                                │
                                ▼
                      ┌───────────────────┐
                      │ Repeat until done │
                      │ or max iterations │
                      └───────────────────┘
```

### SSE Event Types

```typescript
// Sent from server to client
{ type: "thinking", content: "Reasoning about the task..." }
{ type: "tool_call", tool: "search_documents", args: {...} }
{ type: "approval_needed", approval: { id, tool, args, description } }
{ type: "tool_result", tool: "search_documents", result: {...} }
{ type: "response", content: "Based on my analysis..." }
{ type: "complete", result: "Final summary..." }
{ type: "error", message: "Something went wrong" }
```

---

## MCP Integration (Future-Ready)

PydanticAI has **native MCP support** via `pydantic_ai.mcp`:

- `MCPServerStdio` - Connect to MCP servers via subprocess
- `MCPServerHTTP` - Connect to MCP servers via HTTP/SSE
- Automatic tool discovery and registration

### Architecture

```python
# Future: app/services/mcp_manager.py

from pydantic import BaseModel
from pydantic_ai.mcp import MCPServerStdio, MCPServerHTTP


class MCPServerConfig(BaseModel):
    """Configuration for an MCP server."""
    name: str
    type: str  # "stdio" or "http"
    enabled: bool = True
    command: str | None = None   # For stdio
    args: list[str] = []         # For stdio
    url: str | None = None       # For http


class MCPManager:
    """Manages MCP server connections."""

    def get_toolsets(self, configs: list[MCPServerConfig]) -> list:
        """Get PydanticAI-compatible toolsets."""
        toolsets = []
        for config in configs:
            if not config.enabled:
                continue
            if config.type == "stdio" and config.command:
                toolsets.append(MCPServerStdio(
                    command=config.command,
                    args=config.args
                ))
            elif config.type == "http" and config.url:
                toolsets.append(MCPServerHTTP(url=config.url))
        return toolsets
```

### Agent Integration

```python
# In agent_executor.py

from pydantic_ai import Agent

def create_agent(definition: AgentDefinition, library_id: str) -> Agent:
    # Get built-in tools
    tools = [TOOL_REGISTRY[t] for t in definition.tools]

    # Get MCP toolsets (future)
    mcp_toolsets = mcp_manager.get_toolsets(definition.mcp_servers)

    agent = Agent(
        model="openai:gpt-4o",  # Configured to use Ollama
        system_prompt=definition.system_prompt,
        tools=tools,
        toolsets=mcp_toolsets if mcp_toolsets else None,
    )
    return agent
```

---

## Web UI Components

### Agent Editor

```
┌─────────────────────────────────────────────────────────────────┐
│ Create/Edit Agent                                        [Save] │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ Name: [Document Reviewer_________________________]              │
│                                                                  │
│ Description:                                                     │
│ [Reviews documents for completeness and consistency___________] │
│                                                                  │
│ System Prompt:                                                   │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ You are a technical document reviewer specialized in      │   │
│ │ identifying gaps, inconsistencies, and compliance issues. │   │
│ │                                                            │   │
│ │ When reviewing a document:                                 │   │
│ │ 1. Search for related documents to cross-reference        │   │
│ │ 2. Extract and validate entity relationships              │   │
│ │ 3. Check for missing sections or incomplete information   │   │
│ │ 4. Identify contradictions with other documents           │   │
│ └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│ Allowed Tools:                                                   │
│ [x] search_documents    [x] get_entities                        │
│ [x] search_graph        [x] get_relationships                   │
│ [x] compare_documents   [ ] get_document_chunks                 │
│ [ ] summarize_text                                               │
│                                                                  │
│ Settings:                                                        │
│ Approval Mode: [Always ▼]   Max Iterations: [10]                │
│ Temperature:   [0.3___]                                          │
│                                                                  │
│ MCP Servers: (Optional - for future expansion)                   │
│ [+ Add MCP Server]                                               │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

### Agent Runner with Approval

```
┌─────────────────────────────────────────────────────────────────┐
│ Run Agent: Document Reviewer                                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│ Library: [Technical Manuals ▼]                                   │
│                                                                  │
│ Task:                                                            │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ Review the safety procedures document and identify any    │   │
│ │ missing cross-references to the equipment manual.         │   │
│ └───────────────────────────────────────────────────────────┘   │
│                                                                  │
│ [Run Agent]                                                      │
│                                                                  │
├─────────────────────────────────────────────────────────────────┤
│ Execution Log:                                                   │
│ ┌───────────────────────────────────────────────────────────┐   │
│ │ [12:34:01] Agent started                                  │   │
│ │ [12:34:02] Thinking: I need to find the safety doc...     │   │
│ │ [12:34:03] Tool call: search_documents                    │   │
│ │                                                            │   │
│ │ ┌───────────────────────────────────────────────────────┐ │   │
│ │ │ ⚠️  APPROVAL REQUIRED                                  │ │   │
│ │ │                                                        │ │   │
│ │ │ Agent wants to call: search_documents                  │ │   │
│ │ │ Arguments:                                             │ │   │
│ │ │   query: "safety procedures"                           │ │   │
│ │ │   library_id: "tech-manuals"                           │ │   │
│ │ │   top_k: 5                                             │ │   │
│ │ │                                                        │ │   │
│ │ │ [Approve] [Reject] [Modify Args]                       │ │   │
│ │ └───────────────────────────────────────────────────────┘ │   │
│ │                                                            │   │
│ │ [12:34:15] User approved tool call                        │   │
│ │ [12:34:16] Tool result: Found 3 documents                 │   │
│ │ [12:34:17] Thinking: Now analyzing the results...         │   │
│ └───────────────────────────────────────────────────────────┘   │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Pre-Built Agent Templates

### 1. Document Reviewer

```yaml
name: Document Reviewer
description: Reviews documents for completeness and consistency
system_prompt: |
  You are a technical document reviewer. Your role is to:
  1. Identify missing information or incomplete sections
  2. Find inconsistencies within and across documents
  3. Verify cross-references are valid and complete
  4. Check for outdated information
  Always cite specific documents and page numbers.
tools: [search_documents, search_graph, get_entities, compare_documents]
approval_mode: always
```

### 2. Compliance Checker

```yaml
name: Compliance Checker
description: Verifies documents meet standards or requirements
system_prompt: |
  You are a compliance verification agent. Your role is to:
  1. Search for applicable standards and requirements
  2. Extract compliance-related entities and relationships
  3. Map document content to requirements
  4. Identify gaps or non-compliance issues
  Format findings as a compliance matrix.
tools: [search_documents, search_graph, get_entities, get_relationships]
approval_mode: always
```

### 3. Summary Generator

```yaml
name: Summary Generator
description: Creates executive summaries from document collections
system_prompt: |
  You are a summarization agent. Your role is to:
  1. Search and gather relevant documents on a topic
  2. Extract key entities and relationships
  3. Identify the most important information
  4. Generate concise, accurate summaries with citations
tools: [search_documents, get_entities, summarize_text]
approval_mode: always
```

### 4. Change Impact Analyzer

```yaml
name: Change Impact Analyzer
description: Analyzes impact of document changes across the library
system_prompt: |
  You are a change impact analysis agent. Your role is to:
  1. Identify entities affected by a change
  2. Find all documents referencing those entities
  3. Trace relationships to dependent documents
  4. Assess scope and severity of impact
  Provide a clear impact report with affected documents.
tools: [search_graph, get_entities, get_relationships, compare_documents]
approval_mode: always
```

---

## Implementation Phases

### Phase 1: Core Agent Framework (3-4 days)

**Goal**: Basic agent execution with human approval

| Task | File | Description |
|------|------|-------------|
| Add dependency | requirements.txt | Add `pydantic-ai-slim>=0.0.30` |
| Data models | app/models/agents.py | AgentDefinition, AgentTask, PendingApproval |
| Agent manager | app/services/agent_manager.py | CRUD, persistence to JSON |
| Basic executor | app/services/agent_executor.py | Single-tool execution with approval |
| API routes | app/api/routes/agents.py | Create, list, run, approve endpoints |
| Register router | app/main.py | Include agents router |
| PyInstaller | build.py | Add pydantic_ai hidden imports |

### Phase 2: Tool Registry (2-3 days)

**Goal**: Expose GraphRagExec services as agent tools

| Task | File | Description |
|------|------|-------------|
| Tool implementations | app/services/agent_tools.py | 7 tools wrapping existing services |
| Tool registration | app/services/agent_executor.py | Dynamic tool loading per agent config |
| Tests | tests/test_agent_tools.py | Unit tests for each tool |

### Phase 3: Web UI - Agent Management (2-3 days)

**Goal**: UI for creating and editing agents

| Task | File | Description |
|------|------|-------------|
| Agents tab | app/static/index.html | New tab in main navigation |
| Agent list | app/static/js/app.js | Display agents with edit/delete |
| Agent editor modal | app/static/index.html | Form for all agent properties |
| Agent templates | app/static/js/app.js | Pre-built template selection |
| Styles | app/static/css/style.css | Agent editor styling |

### Phase 4: Web UI - Agent Runner (2-3 days)

**Goal**: UI for executing agents and handling approvals

| Task | File | Description |
|------|------|-------------|
| Runner modal | app/static/index.html | Task input and execution log |
| SSE handling | app/static/js/app.js | Real-time progress display |
| Approval dialog | app/static/index.html | Approve/Reject/Modify UI |
| Task history | app/static/js/app.js | List past tasks and results |

### Phase 5: MCP Foundation (1-2 days)

**Goal**: Architecture ready for future MCP integration

| Task | File | Description |
|------|------|-------------|
| MCP manager | app/services/mcp_manager.py | Stub implementation |
| Config model | app/models/agents.py | MCPServerConfig |
| UI placeholder | app/static/index.html | MCP server config section |
| Documentation | README.md | MCP integration guide |

### Phase 6: Testing & Polish (2-3 days)

| Task | Description |
|------|-------------|
| End-to-end tests | Full workflow tests |
| PyInstaller build | Verify executable works |
| Error handling | Edge cases, timeouts |
| Documentation | Update README, add examples |

**Total Estimated Time**: 2-3 weeks

---

## File Structure

```
app/
├── models/
│   └── agents.py              # Agent data models (NEW)
├── services/
│   ├── agent_manager.py       # Agent CRUD, persistence (NEW)
│   ├── agent_executor.py      # PydanticAI execution (NEW)
│   ├── agent_tools.py         # Tool implementations (NEW)
│   └── mcp_manager.py         # MCP server management (NEW)
├── api/
│   └── routes/
│       └── agents.py          # Agent API endpoints (NEW)
└── static/
    ├── index.html             # Add agents tab (MODIFY)
    ├── js/app.js              # Add agent UI logic (MODIFY)
    └── css/style.css          # Add agent styles (MODIFY)
```

---

## Dependencies

### New Dependencies

```
# requirements.txt addition
pydantic-ai-slim>=0.0.30
```

### PyInstaller Updates

```python
# build.py additions to find_hidden_imports()
"pydantic_ai",
"pydantic_ai.agent",
"pydantic_ai.tools",
"pydantic_ai.mcp",  # For future MCP support
```

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| PydanticAI API changes | Pin version, wrap in abstraction layer |
| Ollama compatibility | Test with multiple models, fallback prompts |
| SSE connection drops | Reconnection logic, task state persistence |
| Approval timeout | Configurable timeout, auto-cancel with notification |
| Infinite loops | Max iterations limit enforced |
| Large tool results | Truncation, pagination support |

---

## Success Criteria

1. **Functional**: Users can create, edit, and run agents
2. **Safe**: All tool calls require explicit approval (default)
3. **Lightweight**: Adds <10 MB to executable size
4. **Responsive**: SSE streaming provides real-time feedback
5. **Extensible**: MCP architecture ready for future integration
6. **Documented**: Clear API docs and user guide
