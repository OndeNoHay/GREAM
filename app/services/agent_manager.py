"""
Agent Manager service for GraphRagExec.

Handles CRUD operations for agent definitions and task management.
Persists agent configurations to the app data directory.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.config import get_app_data_dir
from app.models.agents import (
    AgentDefinition,
    AgentTask,
    PendingApproval,
    TaskStatus,
    AGENT_TEMPLATES,
)

logger = logging.getLogger(__name__)


class AgentManager:
    """
    Manager for agent definitions and tasks.

    Singleton pattern - one instance per application.
    Persists agent definitions to JSON file in app data directory.
    """

    _instance: Optional["AgentManager"] = None
    _agents: dict[str, AgentDefinition]
    _tasks: dict[str, AgentTask]
    _pending_approvals: dict[str, PendingApproval]

    AGENTS_FILENAME = "agents.json"

    def __new__(cls) -> "AgentManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._agents = {}
            cls._instance._tasks = {}
            cls._instance._pending_approvals = {}
            cls._instance._load_agents()
        return cls._instance

    def _get_agents_path(self) -> Path:
        """Get path to agents.json file."""
        config_dir = get_app_data_dir() / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / self.AGENTS_FILENAME

    def _load_agents(self) -> None:
        """Load agents from persistent storage."""
        agents_path = self._get_agents_path()

        # Add templates first (they can be overridden by user agents)
        for template in AGENT_TEMPLATES:
            self._agents[template.id] = template

        if agents_path.exists():
            try:
                with open(agents_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for agent_data in data.get("agents", []):
                    try:
                        agent = AgentDefinition(**agent_data)
                        self._agents[agent.id] = agent
                        logger.debug(f"Loaded agent: {agent.name}")
                    except Exception as e:
                        logger.warning(f"Failed to load agent: {e}")

                logger.info(f"Loaded {len(self._agents)} agents")
            except Exception as e:
                logger.error(f"Failed to load agents file: {e}")
        else:
            logger.info("No agents file found, using templates only")

    def _save_agents(self) -> None:
        """Save user-defined agents to persistent storage."""
        agents_path = self._get_agents_path()

        # Only save non-template agents
        user_agents = [
            agent.model_dump(mode="json")
            for agent in self._agents.values()
            if not agent.is_template
        ]

        try:
            with open(agents_path, "w", encoding="utf-8") as f:
                json.dump({"agents": user_agents}, f, indent=2, default=str)
            logger.debug(f"Saved {len(user_agents)} user agents")
        except Exception as e:
            logger.error(f"Failed to save agents: {e}")
            raise

    # Agent CRUD operations

    def list_agents(self, include_templates: bool = True) -> list[AgentDefinition]:
        """List all agents, optionally including templates."""
        agents = list(self._agents.values())
        if not include_templates:
            agents = [a for a in agents if not a.is_template]
        return sorted(agents, key=lambda a: (a.is_template, a.name))

    def get_agent(self, agent_id: str) -> Optional[AgentDefinition]:
        """Get an agent by ID."""
        return self._agents.get(agent_id)

    def create_agent(self, agent: AgentDefinition) -> AgentDefinition:
        """Create a new agent definition."""
        if agent.id in self._agents:
            raise ValueError(f"Agent with ID {agent.id} already exists")

        agent.is_template = False
        agent.created_at = datetime.utcnow()
        agent.updated_at = datetime.utcnow()

        self._agents[agent.id] = agent
        self._save_agents()

        logger.info(f"Created agent: {agent.name} ({agent.id})")
        return agent

    def update_agent(self, agent_id: str, updates: dict) -> AgentDefinition:
        """Update an existing agent.

        Template agents can be edited: editing promotes them to user-defined
        agents (is_template=False) that override the built-in template on reload.
        Deleting such an agent later restores the original template.
        """
        agent = self._agents.get(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        # Promote templates to user-defined so they are persisted on disk
        if agent.is_template:
            agent.is_template = False

        # Apply updates
        for key, value in updates.items():
            if hasattr(agent, key) and key not in ("id", "is_template", "created_at"):
                setattr(agent, key, value)

        agent.updated_at = datetime.utcnow()
        self._save_agents()

        logger.info(f"Updated agent: {agent.name} ({agent.id})")
        return agent

    def delete_agent(self, agent_id: str) -> bool:
        """Delete an agent."""
        agent = self._agents.get(agent_id)
        if not agent:
            return False

        if agent.is_template:
            raise ValueError("Cannot delete template agents")

        del self._agents[agent_id]
        self._save_agents()

        logger.info(f"Deleted agent: {agent.name} ({agent_id})")
        return True

    def clone_template(self, template_id: str, new_name: str) -> AgentDefinition:
        """Clone a template to create a new editable agent."""
        template = self._agents.get(template_id)
        if not template:
            raise ValueError(f"Template not found: {template_id}")

        # Create a copy with new ID and name
        new_agent = AgentDefinition(
            name=new_name,
            description=template.description,
            system_prompt=template.system_prompt,
            tools=template.tools.copy(),
            approval_mode=template.approval_mode,
            max_iterations=template.max_iterations,
            temperature=template.temperature,
            mcp_servers=[s.model_copy() for s in template.mcp_servers],
            is_template=False,
        )

        return self.create_agent(new_agent)

    # Task management

    def create_task(
        self,
        agent_id: str,
        library_id: str,
        prompt: str
    ) -> AgentTask:
        """Create a new task for an agent."""
        agent = self.get_agent(agent_id)
        if not agent:
            raise ValueError(f"Agent not found: {agent_id}")

        task = AgentTask(
            agent_id=agent_id,
            library_id=library_id,
            prompt=prompt,
            status=TaskStatus.PENDING,
        )

        self._tasks[task.id] = task
        logger.info(f"Created task {task.id} for agent {agent.name}")
        return task

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Get a task by ID."""
        return self._tasks.get(task_id)

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        result: Optional[str] = None,
        error: Optional[str] = None
    ) -> None:
        """Update task status."""
        task = self._tasks.get(task_id)
        if task:
            task.status = status
            if result:
                task.result = result
            if error:
                task.error = error
            if status == TaskStatus.RUNNING and not task.started_at:
                task.started_at = datetime.utcnow()
            if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                task.completed_at = datetime.utcnow()

    def add_task_log(self, task_id: str, entry: dict) -> None:
        """Add an entry to the task log."""
        task = self._tasks.get(task_id)
        if task:
            entry["timestamp"] = datetime.utcnow().isoformat()
            task.log.append(entry)

    def list_tasks(
        self,
        agent_id: Optional[str] = None,
        status: Optional[TaskStatus] = None,
        limit: int = 50
    ) -> list[AgentTask]:
        """List tasks with optional filters."""
        tasks = list(self._tasks.values())

        if agent_id:
            tasks = [t for t in tasks if t.agent_id == agent_id]
        if status:
            tasks = [t for t in tasks if t.status == status]

        # Sort by creation time (newest first)
        tasks = sorted(tasks, key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    # Approval management

    def create_approval(
        self,
        task_id: str,
        agent_id: str,
        tool_name: str,
        tool_args: dict,
        description: str
    ) -> PendingApproval:
        """Create a pending approval request."""
        approval = PendingApproval(
            task_id=task_id,
            agent_id=agent_id,
            tool_name=tool_name,
            tool_args=tool_args,
            description=description,
        )

        self._pending_approvals[approval.id] = approval

        # Update task status
        self.update_task_status(task_id, TaskStatus.AWAITING_APPROVAL)

        logger.info(f"Created approval {approval.id} for task {task_id}")
        return approval

    def get_approval(self, approval_id: str) -> Optional[PendingApproval]:
        """Get a pending approval by ID."""
        return self._pending_approvals.get(approval_id)

    def resolve_approval(self, approval_id: str) -> Optional[PendingApproval]:
        """Remove and return a pending approval."""
        return self._pending_approvals.pop(approval_id, None)

    def list_pending_approvals(
        self,
        task_id: Optional[str] = None
    ) -> list[PendingApproval]:
        """List pending approvals, optionally filtered by task."""
        approvals = list(self._pending_approvals.values())
        if task_id:
            approvals = [a for a in approvals if a.task_id == task_id]
        return approvals

    def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """Remove completed/failed tasks older than max_age_hours."""
        cutoff = datetime.utcnow()
        removed = 0

        task_ids_to_remove = []
        for task_id, task in self._tasks.items():
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                if task.completed_at:
                    age = (cutoff - task.completed_at).total_seconds() / 3600
                    if age > max_age_hours:
                        task_ids_to_remove.append(task_id)

        for task_id in task_ids_to_remove:
            del self._tasks[task_id]
            removed += 1

        if removed > 0:
            logger.info(f"Cleaned up {removed} old tasks")

        return removed


# Singleton accessor
_agent_manager: Optional[AgentManager] = None


def get_agent_manager() -> AgentManager:
    """Get the singleton AgentManager instance."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = AgentManager()
    return _agent_manager
