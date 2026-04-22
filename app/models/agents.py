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
]
