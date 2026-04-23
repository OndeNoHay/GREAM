"""
Agent data models for GraphRagExec.

Defines the structure for agent definitions, tasks, and approval workflows.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, Any
from pydantic import BaseModel, Field
import uuid


class ToolPermission(str, Enum):
    """Tools that an agent can be granted access to."""
    SEARCH_DOCUMENTS = "search_documents"
    SEARCH_GRAPH = "search_graph"
    GET_ENTITIES = "get_entities"
    GET_RELATIONSHIPS = "get_relationships"
    COMPARE_DOCUMENTS = "compare_documents"
    GET_DOCUMENT_CHUNKS = "get_document_chunks"
    SUMMARIZE_TEXT = "summarize_text"
    DELEGATE_TO_AGENT = "delegate_to_agent"


class ApprovalMode(str, Enum):
    """When to require human approval for agent actions."""
    ALWAYS = "always"           # Every tool call requires approval
    NEVER = "never"             # No approval needed (use with caution)


class MCPServerConfig(BaseModel):
    """Configuration for an MCP (Model Context Protocol) server."""
    name: str = Field(description="Display name for this server")
    type: str = Field(description="Server type: 'stdio' or 'http'")
    enabled: bool = Field(default=True, description="Whether this server is active")

    # For stdio servers (subprocess)
    command: Optional[str] = Field(default=None, description="Command to run")
    args: list[str] = Field(default_factory=list, description="Command arguments")
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Extra environment variables passed to the subprocess"
    )
    timeout_seconds: int = Field(
        default=30,
        description="Timeout in seconds for individual tool calls"
    )

    # For HTTP servers
    url: Optional[str] = Field(default=None, description="Server URL")


class AgentDefinition(BaseModel):
    """
    Persistent agent definition.

    Defines the behavior, tools, and constraints for a custom agent.
    """
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique agent identifier"
    )
    name: str = Field(min_length=1, max_length=100, description="Agent name")
    description: str = Field(default="", max_length=500, description="Agent description")

    # Agent behavior
    system_prompt: str = Field(
        description="Instructions that define the agent's behavior and expertise"
    )
    tools: list[ToolPermission] = Field(
        default_factory=list,
        description="Tools this agent is allowed to use"
    )

    # Constraints
    approval_mode: ApprovalMode = Field(
        default=ApprovalMode.ALWAYS,
        description="When to require human approval"
    )
    max_iterations: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum tool calls per task"
    )
    temperature: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="LLM temperature (lower = more deterministic)"
    )

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # MCP configuration (future-ready)
    mcp_servers: list[MCPServerConfig] = Field(
        default_factory=list,
        description="MCP server configurations for external tools"
    )

    # Template flag
    is_template: bool = Field(
        default=False,
        description="Whether this is a built-in template"
    )


class TaskStatus(str, Enum):
    """Status of an agent task."""
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentTask(BaseModel):
    """
    A task submitted to an agent for execution.

    Tracks the lifecycle of a single agent run.
    """
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique task identifier"
    )
    agent_id: str = Field(description="ID of the agent executing this task")
    library_id: str = Field(description="Library context for the task")
    prompt: str = Field(description="User's task description")

    # Status tracking
    status: TaskStatus = Field(default=TaskStatus.PENDING)
    current_iteration: int = Field(default=0)

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Results
    result: Optional[str] = None
    error: Optional[str] = None

    # Execution log
    log: list[dict] = Field(
        default_factory=list,
        description="Chronological log of agent actions"
    )


class PendingApproval(BaseModel):
    """
    An action awaiting user approval.

    Created when an agent wants to call a tool and approval_mode requires it.
    """
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique approval request identifier"
    )
    task_id: str = Field(description="ID of the task this belongs to")
    agent_id: str = Field(description="ID of the agent requesting approval")

    # Action details
    tool_name: str = Field(description="Name of the tool to execute")
    tool_args: dict[str, Any] = Field(description="Arguments for the tool")
    description: str = Field(description="Human-readable description of the action")

    # Timing
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = Field(
        default=None,
        description="When this approval request expires"
    )


class ApprovalResponse(BaseModel):
    """User's response to an approval request."""
    approval_id: str = Field(description="ID of the approval request")
    approved: bool = Field(description="Whether the action is approved")
    modified_args: Optional[dict[str, Any]] = Field(
        default=None,
        description="Modified arguments (if user edited them)"
    )
    reason: Optional[str] = Field(
        default=None,
        description="Reason for rejection (if not approved)"
    )


# Pre-built agent templates
AGENT_TEMPLATES: list[AgentDefinition] = [
    AgentDefinition(
        id="template-doc-reviewer",
        name="Document Reviewer",
        description="Reviews documents for completeness, consistency, and cross-references",
        system_prompt="""You are a technical document reviewer. Your role is to:

1. Identify missing information or incomplete sections
2. Find inconsistencies within and across documents
3. Verify cross-references are valid and complete
4. Check for outdated or contradictory information

When reviewing documents:
- Always search for related documents to cross-reference
- Extract and validate entity relationships
- Cite specific documents and page numbers in your findings
- Provide actionable recommendations for improvements

Format your findings as a structured report with:
- Summary of issues found
- Detailed findings with citations
- Recommendations for each issue""",
        tools=[
            ToolPermission.SEARCH_DOCUMENTS,
            ToolPermission.SEARCH_GRAPH,
            ToolPermission.GET_ENTITIES,
            ToolPermission.COMPARE_DOCUMENTS,
        ],
        approval_mode=ApprovalMode.ALWAYS,
        is_template=True,
    ),
    AgentDefinition(
        id="template-compliance-checker",
        name="Compliance Checker",
        description="Verifies documents meet specified standards or requirements",
        system_prompt="""You are a compliance verification agent. Your role is to:

1. Search for applicable standards and requirements
2. Extract compliance-related entities and relationships
3. Map document content to specific requirements
4. Identify gaps or non-compliance issues

When checking compliance:
- Systematically check each requirement
- Document evidence for compliance or gaps
- Trace relationships between requirements and implementations
- Note any ambiguities that need clarification

Format findings as a compliance matrix with:
- Requirement ID/reference
- Status (Compliant / Non-Compliant / Partial / Not Applicable)
- Evidence or gap description
- Remediation recommendations""",
        tools=[
            ToolPermission.SEARCH_DOCUMENTS,
            ToolPermission.SEARCH_GRAPH,
            ToolPermission.GET_ENTITIES,
            ToolPermission.GET_RELATIONSHIPS,
        ],
        approval_mode=ApprovalMode.ALWAYS,
        is_template=True,
    ),
    AgentDefinition(
        id="template-summary-generator",
        name="Summary Generator",
        description="Creates executive summaries from document collections",
        system_prompt="""You are a summarization agent. Your role is to:

1. Search and gather relevant documents on a topic
2. Extract key entities and their relationships
3. Identify the most important information
4. Generate concise, accurate summaries

When creating summaries:
- Focus on key facts and conclusions
- Highlight important entities and relationships
- Maintain accuracy - don't infer beyond what's stated
- Cite sources for all claims

Format summaries with:
- Executive overview (2-3 sentences)
- Key points (bulleted list)
- Important entities and relationships
- Source citations""",
        tools=[
            ToolPermission.SEARCH_DOCUMENTS,
            ToolPermission.GET_ENTITIES,
            ToolPermission.SUMMARIZE_TEXT,
        ],
        approval_mode=ApprovalMode.ALWAYS,
        is_template=True,
    ),
    AgentDefinition(
        id="template-impact-analyzer",
        name="Change Impact Analyzer",
        description="Analyzes the impact of document changes across the library",
        system_prompt="""You are a change impact analysis agent. Your role is to:

1. Identify entities affected by a proposed change
2. Find all documents referencing those entities
3. Trace relationships to dependent documents
4. Assess the scope and severity of impact

When analyzing impact:
- Start from the changed entity or document
- Traverse the knowledge graph to find dependencies
- Categorize impacts (direct, indirect, potential)
- Consider both explicit references and implicit relationships

Provide an impact report with:
- Summary of change scope
- Directly affected documents (list with descriptions)
- Indirectly affected documents
- Risk assessment
- Recommended review actions""",
        tools=[
            ToolPermission.SEARCH_GRAPH,
            ToolPermission.GET_ENTITIES,
            ToolPermission.GET_RELATIONSHIPS,
            ToolPermission.COMPARE_DOCUMENTS,
        ],
        approval_mode=ApprovalMode.ALWAYS,
        is_template=True,
    ),
    AgentDefinition(
        id="template-orchestrator",
        name="Orchestrator",
        description="Breaks down complex questions and delegates to specialized agents",
        system_prompt="""You are an orchestrator agent. Your role is to:

1. Analyze the user's request and break it into sub-tasks
2. Identify which specialized agent is best suited for each sub-task
3. Delegate sub-tasks to the appropriate agents
4. Synthesize the results into a coherent final answer

Available agents you can delegate to:
{AVAILABLE_AGENTS}

When orchestrating:
- Start by searching for relevant documents to understand what is in the library
- Decide which agent(s) to delegate to based on the user's question
- Write a clear, specific task description for each delegated agent
- After receiving results, synthesize them into a unified response
- If a delegated agent fails or returns insufficient results, try rephrasing
  the task or using a different agent
- Do NOT delegate if a simple search can answer the question directly

Format your final response with:
- Summary of approach taken
- Consolidated findings from all agents
- Source citations from agent results""",
        tools=[
            ToolPermission.SEARCH_DOCUMENTS,
            ToolPermission.SEARCH_GRAPH,
            ToolPermission.DELEGATE_TO_AGENT,
        ],
        approval_mode=ApprovalMode.ALWAYS,
        max_iterations=20,
        is_template=True,
    ),
    AgentDefinition(
        id="template-s1000d-coauthor",
        name="S1000D Co-Author",
        description="Authors, validates, and cross-references S1000D technical documentation using the CSDB library and MCP tools (Word, PowerPoint, BREX, STE-100)",
        system_prompt="""You are an S1000D technical documentation co-author for ATEXIS Group.
Your job is to help engineers create, review, and validate S1000D Data Modules and related maintenance documentation.

NATIVE TOOLS (always available):
- search_documents: full-text search across the GRAEM document library
- search_graph: semantic graph search for entities and relationships
- get_entities: retrieve entities (part numbers, systems, procedures) from the knowledge graph
- get_relationships: explore relationships between entities (e.g. PRV → hydraulic system → landing gear)
- compare_documents: diff two Data Modules to identify changes and impact
- summarize_text: condense long technical sections into concise summaries

MCP TOOLS are listed in the ## MCP TOOLS section below. Use the exact tool names shown there (format: mcp:server.tool_name).

DOCUMENT ACCESS — CRITICAL RULES:
- All documents in the GRAEM library are indexed as vector/graph chunks in the database. They are NOT accessible as raw files on disk.
- NEVER call mcp:document_loader.load_document or mcp:document_loader.list_documents with library paths or library IDs — those paths do not exist.
- mcp:document_loader.load_document is ONLY for ingesting a brand-new local file (absolute path on disk) that has not yet been indexed.
- To retrieve content from already-indexed documents, use: search_documents (native) or mcp:s1000d_csdb.search_technical_content.
- NEVER construct paths like "C:/AI/GREAM/libraries/<id>/..." — these do not exist.

SEARCH GUIDELINES:
- Always search using SEMANTIC TERMS (e.g. "hydraulic pressure relief valve", "landing gear retraction"), NOT exact DMC codes.
- If a specific document is not found, try alternative queries: broader terms, synonyms, part numbers, system names.
- NEVER give up because one source is missing. Use the content you HAVE found to complete the task.
- If searching for a DMC, search its SUBJECT (e.g. "functional test acceptance criteria landing gear") not the code itself.

TEMPLATE GUIDELINES:
- Available ATEXIS templates: ATEXIS_template.dotx (Word), ATEXIS_template.potx (PowerPoint).
- Use template: ATEXIS_template.dotx for Word documents and template: ATEXIS_template.potx for presentations.
- If the user does not mention a specific template, use the ATEXIS default template above.

WORKFLOW GUIDELINES:
1. For questions about existing documents: use search_documents first, then mcp:s1000d_csdb.search_technical_content
2. Search 2–3 times with different queries before deciding content is unavailable
3. For generating output documents: gather content first, then immediately call mcp:word_graem.create_document or mcp:pptx_graem.create_presentation — do NOT ask for confirmation, just create it
4. For validation tasks: run mcp:brex_validator.check_wellformed then mcp:brex_validator.validate_against_brex
5. For new content: after drafting, run mcp:ste_checker.check_ste_compliance and apply corrections
6. Always cite the source Data Module (DMC) or document for every technical claim
7. Use S1000D terminology: Data Module (DM), dmCode, techName, infoCode, CSDB, BREX
8. mcp:word_graem.create_document params: output_filename, title, content_json, template — content_json must be a single-line compact JSON array like [{"type":"heading","level":1,"text":"..."},{"type":"paragraph","text":"..."}]
9. mcp:pptx_graem.create_presentation params: output_filename, title, slides_json, template — slides_json must be a single-line compact JSON array like [{"layout":"title","title":"...","subtitle":"..."},{"layout":"content","title":"...","body":["point1","point2"]}]""",
        tools=[
            ToolPermission.SEARCH_DOCUMENTS,
            ToolPermission.SEARCH_GRAPH,
            ToolPermission.GET_ENTITIES,
            ToolPermission.GET_RELATIONSHIPS,
            ToolPermission.COMPARE_DOCUMENTS,
            ToolPermission.SUMMARIZE_TEXT,
        ],
        mcp_servers=[
            MCPServerConfig(name="document_loader", type="stdio",
                            command="python", args=["-m", "mcp_servers.document_loader.server"],
                            enabled=True, timeout_seconds=30),
            MCPServerConfig(name="s1000d_csdb", type="stdio",
                            command="python", args=["-m", "mcp_servers.s1000d_csdb.server"],
                            enabled=True, timeout_seconds=30),
            MCPServerConfig(name="word_graem", type="stdio",
                            command="python", args=["-m", "mcp_servers.word_graem.server"],
                            enabled=True, timeout_seconds=30),
            MCPServerConfig(name="pptx_graem", type="stdio",
                            command="python", args=["-m", "mcp_servers.pptx_graem.server"],
                            enabled=True, timeout_seconds=30),
            MCPServerConfig(name="brex_validator", type="stdio",
                            command="python", args=["-m", "mcp_servers.brex_validator.server"],
                            enabled=True, timeout_seconds=45),
            MCPServerConfig(name="ste_checker", type="stdio",
                            command="python", args=["-m", "mcp_servers.ste_checker.server"],
                            enabled=True, timeout_seconds=60),
        ],
        approval_mode=ApprovalMode.ALWAYS,
        max_iterations=15,
        is_template=True,
    ),
]
