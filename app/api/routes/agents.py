"""
Agent API routes for GraphRagExec.

Provides endpoints for agent management and execution with human-in-the-loop approval.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import json

from app.models.agents import (
    AgentDefinition,
    AgentTask,
    ApprovalResponse,
    ToolPermission,
    ApprovalMode,
    TaskStatus,
)
from app.services.agent_manager import get_agent_manager
from app.services.agent_executor import get_agent_executor
from app.services.agent_tools import get_all_tool_descriptions
from app.services.library_manager import get_library_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agents", tags=["Agents"])


# Request/Response models

class CreateAgentRequest(BaseModel):
    """Request to create a new agent."""
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(min_length=10)
    tools: list[str] = Field(default_factory=list)
    approval_mode: str = Field(default="always")
    max_iterations: int = Field(default=10, ge=1, le=50)
    temperature: float = Field(default=0.3, ge=0.0, le=1.0)


class UpdateAgentRequest(BaseModel):
    """Request to update an agent."""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = Field(default=None, max_length=500)
    system_prompt: Optional[str] = Field(default=None, min_length=10)
    tools: Optional[list[str]] = None
    approval_mode: Optional[str] = None
    max_iterations: Optional[int] = Field(default=None, ge=1, le=50)
    temperature: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class RunAgentRequest(BaseModel):
    """Request to run an agent task."""
    library_id: str
    prompt: str = Field(min_length=1, max_length=10000)


class ApprovalRequest(BaseModel):
    """Request to approve/reject a pending action."""
    approval_id: str
    approved: bool
    modified_args: Optional[dict] = None
    reason: Optional[str] = None


class CloneTemplateRequest(BaseModel):
    """Request to clone a template agent."""
    template_id: str
    new_name: str = Field(min_length=1, max_length=100)


class AgentResponse(BaseModel):
    """Response with agent details."""
    id: str
    name: str
    description: str
    system_prompt: str
    tools: list[str]
    approval_mode: str
    max_iterations: int
    temperature: float
    is_template: bool
    created_at: str
    updated_at: str
    mcp_servers: list[dict] = []


class AgentListResponse(BaseModel):
    """Response with list of agents."""
    agents: list[AgentResponse]
    total: int


class TaskResponse(BaseModel):
    """Response with task details."""
    id: str
    agent_id: str
    library_id: str
    prompt: str
    status: str
    current_iteration: int
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    result: Optional[str]
    error: Optional[str]


class ToolsResponse(BaseModel):
    """Response with available tools."""
    tools: list[dict]


# Helper functions

def agent_to_response(agent: AgentDefinition) -> AgentResponse:
    """Convert AgentDefinition to API response."""
    return AgentResponse(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        system_prompt=agent.system_prompt,
        tools=[t.value for t in agent.tools],
        approval_mode=agent.approval_mode.value,
        max_iterations=agent.max_iterations,
        temperature=agent.temperature,
        is_template=agent.is_template,
        created_at=agent.created_at.isoformat(),
        updated_at=agent.updated_at.isoformat(),
        mcp_servers=[s.model_dump() for s in agent.mcp_servers],
    )


def task_to_response(task: AgentTask) -> TaskResponse:
    """Convert AgentTask to API response."""
    return TaskResponse(
        id=task.id,
        agent_id=task.agent_id,
        library_id=task.library_id,
        prompt=task.prompt,
        status=task.status.value,
        current_iteration=task.current_iteration,
        created_at=task.created_at.isoformat(),
        started_at=task.started_at.isoformat() if task.started_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
        result=task.result,
        error=task.error,
    )


# Agent CRUD endpoints

@router.get("/", response_model=AgentListResponse, summary="List all agents")
async def list_agents(include_templates: bool = True) -> AgentListResponse:
    """List all agent definitions, optionally including templates."""
    manager = get_agent_manager()
    agents = manager.list_agents(include_templates=include_templates)
    return AgentListResponse(
        agents=[agent_to_response(a) for a in agents],
        total=len(agents),
    )


@router.get("/tools", response_model=ToolsResponse, summary="List available tools")
async def list_tools() -> ToolsResponse:
    """List all tools that agents can use."""
    return ToolsResponse(tools=get_all_tool_descriptions())


@router.get("/{agent_id}", response_model=AgentResponse, summary="Get agent details")
async def get_agent(agent_id: str) -> AgentResponse:
    """Get details for a specific agent."""
    manager = get_agent_manager()
    agent = manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}"
        )
    return agent_to_response(agent)


@router.post("/", response_model=AgentResponse, summary="Create a new agent")
async def create_agent(request: CreateAgentRequest) -> AgentResponse:
    """Create a new agent definition."""
    manager = get_agent_manager()

    # Parse tools
    tools = []
    for tool_name in request.tools:
        try:
            tools.append(ToolPermission(tool_name))
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid tool: {tool_name}"
            )

    # Parse approval mode
    try:
        approval_mode = ApprovalMode(request.approval_mode)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid approval mode: {request.approval_mode}"
        )

    # Create agent
    agent = AgentDefinition(
        name=request.name,
        description=request.description,
        system_prompt=request.system_prompt,
        tools=tools,
        approval_mode=approval_mode,
        max_iterations=request.max_iterations,
        temperature=request.temperature,
    )

    try:
        created = manager.create_agent(agent)
        return agent_to_response(created)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put("/{agent_id}", response_model=AgentResponse, summary="Update an agent")
async def update_agent(agent_id: str, request: UpdateAgentRequest) -> AgentResponse:
    """Update an existing agent definition."""
    manager = get_agent_manager()

    # Build updates dict
    updates = {}

    if request.name is not None:
        updates["name"] = request.name
    if request.description is not None:
        updates["description"] = request.description
    if request.system_prompt is not None:
        updates["system_prompt"] = request.system_prompt
    if request.max_iterations is not None:
        updates["max_iterations"] = request.max_iterations
    if request.temperature is not None:
        updates["temperature"] = request.temperature

    if request.tools is not None:
        tools = []
        for tool_name in request.tools:
            try:
                tools.append(ToolPermission(tool_name))
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid tool: {tool_name}"
                )
        updates["tools"] = tools

    if request.approval_mode is not None:
        try:
            updates["approval_mode"] = ApprovalMode(request.approval_mode)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid approval mode: {request.approval_mode}"
            )

    try:
        updated = manager.update_agent(agent_id, updates)
        return agent_to_response(updated)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete("/{agent_id}", summary="Delete an agent")
async def delete_agent(agent_id: str) -> dict:
    """Delete an agent definition."""
    manager = get_agent_manager()
    try:
        success = manager.delete_agent(agent_id)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent not found: {agent_id}"
            )
        return {"success": True, "message": f"Agent {agent_id} deleted"}
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.post("/clone", response_model=AgentResponse, summary="Clone a template")
async def clone_template(request: CloneTemplateRequest) -> AgentResponse:
    """Clone a template agent to create a new editable agent."""
    manager = get_agent_manager()
    try:
        cloned = manager.clone_template(request.template_id, request.new_name)
        return agent_to_response(cloned)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


# Task execution endpoints

@router.post("/{agent_id}/run", summary="Run an agent task")
async def run_agent(agent_id: str, request: RunAgentRequest):
    """
    Run an agent task with streaming output.

    Returns SSE stream with progress updates and approval requests.
    """
    manager = get_agent_manager()
    executor = get_agent_executor()

    # Validate agent
    agent = manager.get_agent(agent_id)
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}"
        )

    # Validate library
    library_mgr = get_library_manager()
    library = library_mgr.get_library(request.library_id)
    if not library:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Library not found: {request.library_id}"
        )

    # Create task
    task = manager.create_task(
        agent_id=agent_id,
        library_id=request.library_id,
        prompt=request.prompt,
    )

    async def event_stream():
        """Generate SSE events from agent execution."""
        try:
            async for event in executor.run_agent(task, agent):
                # Format as SSE
                data = json.dumps(event)
                yield f"data: {data}\n\n"
        except Exception as e:
            logger.error(f"Agent execution error: {e}")
            error_event = json.dumps({"type": "error", "message": str(e)})
            yield f"data: {error_event}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Task-ID": task.id,
        },
    )


@router.post("/approve", summary="Approve or reject a pending action")
async def submit_approval(request: ApprovalRequest) -> dict:
    """Submit approval decision for a pending agent action."""
    manager = get_agent_manager()
    executor = get_agent_executor()

    # Find the approval
    approval = manager.get_approval(request.approval_id)
    if not approval:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval not found: {request.approval_id}"
        )

    # Create response object
    response = ApprovalResponse(
        approval_id=request.approval_id,
        approved=request.approved,
        modified_args=request.modified_args,
        reason=request.reason,
    )

    # Submit to executor
    success = executor.submit_approval(approval.task_id, response)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to submit approval - task may have ended"
        )

    return {
        "success": True,
        "approved": request.approved,
        "task_id": approval.task_id,
    }


@router.post("/tasks/{task_id}/cancel", summary="Cancel a running task")
async def cancel_task(task_id: str) -> dict:
    """Cancel a running agent task."""
    executor = get_agent_executor()

    success = executor.cancel_task(task_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found or not running: {task_id}"
        )

    return {"success": True, "message": f"Task {task_id} cancelled"}


@router.get("/tasks", summary="List tasks")
async def list_tasks(
    agent_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50
) -> dict:
    """List agent tasks with optional filters."""
    manager = get_agent_manager()

    task_status = None
    if status:
        try:
            task_status = TaskStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status}"
            )

    tasks = manager.list_tasks(
        agent_id=agent_id,
        status=task_status,
        limit=limit,
    )

    return {
        "tasks": [task_to_response(t) for t in tasks],
        "total": len(tasks),
    }


@router.get("/tasks/{task_id}", response_model=TaskResponse, summary="Get task details")
async def get_task(task_id: str) -> TaskResponse:
    """Get details for a specific task."""
    manager = get_agent_manager()
    task = manager.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}"
        )
    return task_to_response(task)


@router.get("/tasks/{task_id}/pending", summary="Get pending approvals for a task")
async def get_task_approvals(task_id: str) -> dict:
    """Get pending approval requests for a task."""
    manager = get_agent_manager()

    approvals = manager.list_pending_approvals(task_id=task_id)
    return {
        "approvals": [
            {
                "id": a.id,
                "tool_name": a.tool_name,
                "tool_args": a.tool_args,
                "description": a.description,
                "created_at": a.created_at.isoformat(),
            }
            for a in approvals
        ],
        "total": len(approvals),
    }
