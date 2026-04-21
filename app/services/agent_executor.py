"""
Agent executor for GraphRagExec.

Executes agent tasks with human-in-the-loop approval using PydanticAI.
Provides streaming output via async generators for SSE.
"""

import asyncio
import logging
from datetime import datetime
from typing import AsyncIterator, Any, Optional
from dataclasses import dataclass, field

from app.models.agents import (
    AgentDefinition,
    AgentTask,
    ApprovalMode,
    ApprovalResponse,
    TaskStatus,
    ToolPermission,
)
from app.services.agent_manager import get_agent_manager
from app.services.agent_tools import TOOL_REGISTRY
from app.services.ai_client import get_ai_client
from app.config import get_settings_manager

logger = logging.getLogger(__name__)


@dataclass
class ExecutionContext:
    """Context for an agent execution."""
    task: AgentTask
    agent: AgentDefinition
    library_id: str
    current_iteration: int = 0
    pending_approval_id: Optional[str] = None
    approval_event: asyncio.Event = field(default_factory=asyncio.Event)
    approval_response: Optional[ApprovalResponse] = None
    should_stop: bool = False
    accumulated_sources: list = field(default_factory=list)
    accumulated_entities: list = field(default_factory=list)


class AgentExecutor:
    """
    Executes agents with human-in-the-loop approval.

    Manages the execution loop, tool calling, and approval workflow.
    """

    _instance: Optional["AgentExecutor"] = None
    _active_contexts: dict[str, ExecutionContext]

    def __new__(cls) -> "AgentExecutor":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._active_contexts = {}
        return cls._instance

    async def run_agent(
        self,
        task: AgentTask,
        agent: AgentDefinition,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Run an agent task with streaming output.

        Yields SSE events:
        - {"type": "started", "task_id": "..."}
        - {"type": "thinking", "content": "..."}
        - {"type": "tool_call", "tool": "...", "args": {...}}
        - {"type": "approval_needed", "approval": {...}}
        - {"type": "tool_result", "tool": "...", "result": {...}}
        - {"type": "response", "content": "..."}
        - {"type": "complete", "result": "..."}
        - {"type": "error", "message": "..."}
        """
        # Create execution context
        context = ExecutionContext(
            task=task,
            agent=agent,
            library_id=task.library_id,
        )
        self._active_contexts[task.id] = context

        manager = get_agent_manager()

        try:
            # Update task status
            manager.update_task_status(task.id, TaskStatus.RUNNING)
            yield {"type": "started", "task_id": task.id, "agent_name": agent.name}

            # Build system prompt with context
            system_prompt = self._build_system_prompt(agent, task.library_id)

            # Get AI client
            ai_client = get_ai_client()

            # Initialize conversation
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task.prompt},
            ]

            # Execution loop
            while context.current_iteration < agent.max_iterations and not context.should_stop:
                context.current_iteration += 1
                task.current_iteration = context.current_iteration

                manager.add_task_log(task.id, {
                    "type": "iteration",
                    "iteration": context.current_iteration,
                })

                yield {
                    "type": "thinking",
                    "content": f"Iteration {context.current_iteration}/{agent.max_iterations}",
                    "iteration": context.current_iteration,
                }

                # Call LLM for next action
                try:
                    response = ai_client.chat_completion(
                        messages=messages,
                        temperature=agent.temperature,
                        max_tokens=2048,
                    )
                except Exception as e:
                    logger.error(f"LLM call failed: {e}")
                    yield {"type": "error", "message": f"LLM error: {e}"}
                    break

                # Guard against empty responses (OpenAI rejects assistant
                # messages with no content on the next call)
                if not response or not response.strip():
                    logger.warning("LLM returned empty response, prompting to continue")
                    messages.append({
                        "role": "user",
                        "content": "[SYSTEM] Your previous response was empty. "
                                   "Please use one of your available tools or provide your analysis.",
                    })
                    continue

                # Parse response for tool calls
                tool_call = self._parse_tool_call(response, agent.tools)

                if tool_call:
                    tool_name = tool_call["tool"]
                    tool_args = tool_call["args"]

                    yield {
                        "type": "tool_call",
                        "tool": tool_name,
                        "args": tool_args,
                        "iteration": context.current_iteration,
                    }

                    # Check if approval needed
                    if agent.approval_mode == ApprovalMode.ALWAYS:
                        # Create approval request
                        approval = manager.create_approval(
                            task_id=task.id,
                            agent_id=agent.id,
                            tool_name=tool_name,
                            tool_args=tool_args,
                            description=f"Agent wants to call {tool_name}",
                        )

                        context.pending_approval_id = approval.id
                        context.approval_event.clear()

                        yield {
                            "type": "approval_needed",
                            "approval": {
                                "id": approval.id,
                                "tool": tool_name,
                                "args": tool_args,
                                "description": approval.description,
                            },
                        }

                        # Wait for approval
                        manager.add_task_log(task.id, {
                            "type": "awaiting_approval",
                            "approval_id": approval.id,
                        })

                        try:
                            # Wait with timeout (5 minutes)
                            await asyncio.wait_for(
                                context.approval_event.wait(),
                                timeout=300.0
                            )
                        except asyncio.TimeoutError:
                            yield {
                                "type": "error",
                                "message": "Approval timeout - task cancelled",
                            }
                            context.should_stop = True
                            manager.update_task_status(
                                task.id,
                                TaskStatus.CANCELLED,
                                error="Approval timeout"
                            )
                            break

                        # Process approval response
                        if context.approval_response:
                            if not context.approval_response.approved:
                                # User rejected - inform agent and continue
                                rejection_msg = context.approval_response.reason or "User rejected this action"
                                messages.append({"role": "assistant", "content": response})
                                messages.append({
                                    "role": "user",
                                    "content": f"[SYSTEM] The user rejected your request to call {tool_name}. "
                                               f"Reason: {rejection_msg}. Please try a different approach."
                                })
                                yield {
                                    "type": "tool_rejected",
                                    "tool": tool_name,
                                    "reason": rejection_msg,
                                }
                                context.approval_response = None
                                continue

                            # Use potentially modified args
                            if context.approval_response.modified_args:
                                tool_args = context.approval_response.modified_args

                            yield {
                                "type": "tool_approved",
                                "tool": tool_name,
                                "args": tool_args,
                            }

                        context.approval_response = None

                    # Execute the tool
                    tool_raw = None
                    try:
                        tool_str, tool_raw = await self._execute_tool(
                            tool_name,
                            tool_args,
                            task.library_id
                        )

                        yield {
                            "type": "tool_result",
                            "tool": tool_name,
                            "result": tool_str,
                        }

                        # Add to conversation
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": f"[TOOL RESULT: {tool_name}]\n{tool_str}"
                        })

                        manager.add_task_log(task.id, {
                            "type": "tool_executed",
                            "tool": tool_name,
                            "result_preview": str(tool_str)[:200],
                        })

                    except Exception as e:
                        logger.error(f"Tool execution failed: {e}")
                        yield {"type": "error", "message": f"Tool error: {e}"}
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": f"[TOOL ERROR: {tool_name}] {e}"
                        })

                    # Accumulate sources/entities separately — never let this
                    # break the execution loop or corrupt tool result handling
                    if tool_raw is not None:
                        try:
                            self._accumulate_results(context, tool_name, tool_raw)
                        except Exception as acc_err:
                            logger.warning(
                                f"Could not accumulate results from {tool_name}: {acc_err}"
                            )

                else:
                    # No tool call - this is a final response
                    yield {
                        "type": "response",
                        "content": response,
                        "iteration": context.current_iteration,
                    }

                    # Check if agent is done
                    if self._is_final_response(response):
                        manager.update_task_status(
                            task.id,
                            TaskStatus.COMPLETED,
                            result=response
                        )
                        yield {
                            "type": "complete",
                            "result": response,
                            "iterations": context.current_iteration,
                            "sources": context.accumulated_sources,
                            "entities": context.accumulated_entities,
                        }
                        break
                    else:
                        # Continue conversation
                        messages.append({"role": "assistant", "content": response})
                        messages.append({
                            "role": "user",
                            "content": "[SYSTEM] Please continue your analysis or provide your final answer."
                        })

            else:
                # Max iterations reached
                if not context.should_stop:
                    yield {
                        "type": "error",
                        "message": f"Max iterations ({agent.max_iterations}) reached",
                    }
                    manager.update_task_status(
                        task.id,
                        TaskStatus.FAILED,
                        error="Max iterations reached"
                    )

        except Exception as e:
            logger.error(f"Agent execution failed: {e}")
            yield {"type": "error", "message": str(e)}
            manager.update_task_status(task.id, TaskStatus.FAILED, error=str(e))

        finally:
            # Cleanup
            if task.id in self._active_contexts:
                del self._active_contexts[task.id]

    def submit_approval(self, task_id: str, response: ApprovalResponse) -> bool:
        """Submit an approval response for a pending action."""
        context = self._active_contexts.get(task_id)
        if not context:
            logger.warning(f"No active context for task {task_id}")
            return False

        if context.pending_approval_id != response.approval_id:
            logger.warning(f"Approval ID mismatch for task {task_id}")
            return False

        # Resolve the approval in manager
        manager = get_agent_manager()
        manager.resolve_approval(response.approval_id)

        # Update task status
        manager.update_task_status(task_id, TaskStatus.RUNNING)

        # Store response and signal continuation
        context.approval_response = response
        context.pending_approval_id = None
        context.approval_event.set()

        manager.add_task_log(task_id, {
            "type": "approval_resolved",
            "approved": response.approved,
            "reason": response.reason,
        })

        return True

    def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        context = self._active_contexts.get(task_id)
        if not context:
            return False

        context.should_stop = True

        # If waiting for approval, signal to continue (will exit loop)
        if context.pending_approval_id:
            context.approval_response = ApprovalResponse(
                approval_id=context.pending_approval_id,
                approved=False,
                reason="Task cancelled by user"
            )
            context.approval_event.set()

        manager = get_agent_manager()
        manager.update_task_status(task_id, TaskStatus.CANCELLED)

        return True

    def _build_system_prompt(self, agent: AgentDefinition, library_id: str) -> str:
        """Build the system prompt with tool descriptions."""
        tool_descriptions = []
        for tool_perm in agent.tools:
            tool_info = TOOL_REGISTRY.get(tool_perm)
            if tool_info:
                tool_descriptions.append(
                    f"- {tool_perm.value}: {tool_info['description']}"
                )

        tools_section = "\n".join(tool_descriptions) if tool_descriptions else "No tools available."

        system_prompt = agent.system_prompt

        # If this agent can delegate, inject the current list of available agents
        if ToolPermission.DELEGATE_TO_AGENT in agent.tools:
            manager = get_agent_manager()
            all_agents = manager.list_agents(include_templates=True)
            agent_lines = []
            for a in all_agents:
                # Skip the orchestrator itself to avoid self-delegation
                if a.id == agent.id:
                    continue
                # Skip agents that have no useful tools (besides delegation)
                useful_tools = [t for t in a.tools if t != ToolPermission.DELEGATE_TO_AGENT]
                if not useful_tools:
                    continue
                agent_lines.append(f"- {a.id}: {a.description}")

            agents_list = "\n".join(agent_lines) if agent_lines else "No agents available."

            # Replace placeholder if present, otherwise append
            if "{AVAILABLE_AGENTS}" in system_prompt:
                system_prompt = system_prompt.replace("{AVAILABLE_AGENTS}", agents_list)
            else:
                system_prompt += f"\n\nAvailable agents you can delegate to:\n{agents_list}"

        return f"""{system_prompt}

## CONTEXT
- Library ID: {library_id}
- Always use the provided tools to search and analyze documents
- Cite your sources when providing information
- If you cannot find relevant information, say so clearly

## AVAILABLE TOOLS
{tools_section}

## TOOL CALLING FORMAT
When you need to use a tool, respond with EXACTLY this format:
[TOOL_CALL]
tool: <tool_name>
args:
  <arg_name>: <arg_value>
  <arg_name>: <arg_value>
[/TOOL_CALL]

Example:
[TOOL_CALL]
tool: search_documents
args:
  query: safety procedures
  top_k: 5
[/TOOL_CALL]

After using tools and gathering information, provide your final answer.
When you have completed the task, end with "TASK COMPLETE" on its own line.
"""

    def _parse_tool_call(
        self,
        response: str,
        allowed_tools: list[ToolPermission]
    ) -> Optional[dict[str, Any]]:
        """Parse a tool call from the LLM response."""
        if "[TOOL_CALL]" not in response:
            return None

        try:
            # Extract tool call block
            start = response.index("[TOOL_CALL]") + len("[TOOL_CALL]")
            end = response.index("[/TOOL_CALL]")
            block = response[start:end].strip()

            # Parse YAML-like format
            lines = block.split("\n")
            tool_name = None
            args = {}
            in_args = False

            for line in lines:
                line = line.strip()
                if line.startswith("tool:"):
                    tool_name = line.split(":", 1)[1].strip()
                elif line == "args:":
                    in_args = True
                elif in_args and ":" in line:
                    key, value = line.split(":", 1)
                    key = key.strip()
                    value = value.strip()
                    # Try to parse as int or keep as string
                    try:
                        args[key] = int(value)
                    except ValueError:
                        args[key] = value

            if tool_name:
                # Verify tool is allowed
                try:
                    tool_perm = ToolPermission(tool_name)
                    if tool_perm in allowed_tools:
                        return {"tool": tool_name, "args": args}
                    else:
                        logger.warning(f"Tool {tool_name} not allowed for this agent")
                except ValueError:
                    logger.warning(f"Unknown tool: {tool_name}")

        except Exception as e:
            logger.warning(f"Failed to parse tool call: {e}")

        return None

    def _is_final_response(self, response: str) -> bool:
        """Check if the response indicates task completion."""
        indicators = [
            "TASK COMPLETE",
            "task complete",
            "Task Complete",
            "I have completed",
            "analysis is complete",
            "Here is my final",
            "In conclusion",
            "To summarize my findings",
        ]
        return any(indicator in response for indicator in indicators)

    async def _execute_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        library_id: str
    ) -> tuple[str, Any]:
        """Execute a tool and return (string result for LLM, raw result object)."""
        try:
            tool_perm = ToolPermission(tool_name)
            tool_info = TOOL_REGISTRY.get(tool_perm)

            if not tool_info:
                return f"Error: Unknown tool {tool_name}", None

            # Validate and parse arguments
            args_model = tool_info["args_model"]
            try:
                validated_args = args_model(**tool_args)
            except Exception as e:
                return f"Error: Invalid arguments for {tool_name}: {e}", None

            # Execute the tool
            func = tool_info["function"]
            result = await func(validated_args, library_id)

            # Convert result to string for LLM
            if hasattr(result, "model_dump"):
                return str(result.model_dump()), result
            else:
                return str(result), result

        except Exception as e:
            logger.error(f"Tool execution error: {e}")
            return f"Error executing {tool_name}: {e}", None

    def _accumulate_results(
        self,
        context: ExecutionContext,
        tool_name: str,
        result: Any,
    ) -> None:
        """Extract sources and entities from a tool result and add to context accumulators."""
        if result is None:
            return

        if tool_name in ("search_documents", "search_graph"):
            seen_sources = {
                (s["source_file"], s.get("page"))
                for s in context.accumulated_sources
            }
            for r in getattr(result, "results", []):
                if not isinstance(r, dict):
                    continue
                sf = r.get("source_file", "")
                page = r.get("page")
                metadata = r.get("metadata", {})
                if sf and (sf, page) not in seen_sources:
                    context.accumulated_sources.append({
                        "source_file": sf,
                        "page": page,
                        "metadata": metadata,
                    })
                    seen_sources.add((sf, page))
            if tool_name == "search_graph":
                seen_ents = {e["name"] for e in context.accumulated_entities}
                for r in getattr(result, "results", []):
                    for ent in (r.get("related_entities") or [] if isinstance(r, dict) else []):
                        if isinstance(ent, str) and ent and ent not in seen_ents:
                            context.accumulated_entities.append(
                                {"name": ent, "entity_type": "ENTITY"}
                            )
                            seen_ents.add(ent)

        elif tool_name == "get_entities":
            seen_ents = {e["name"] for e in context.accumulated_entities}
            for ent in getattr(result, "entities", []):
                if not isinstance(ent, dict):
                    continue
                name = ent.get("name", "")
                if name and name not in seen_ents:
                    context.accumulated_entities.append({
                        "name": name,
                        "entity_type": ent.get("type", "ENTITY"),
                    })
                    seen_ents.add(name)


# Singleton accessor
_agent_executor: Optional[AgentExecutor] = None


def get_agent_executor() -> AgentExecutor:
    """Get the singleton AgentExecutor instance."""
    global _agent_executor
    if _agent_executor is None:
        _agent_executor = AgentExecutor()
    return _agent_executor
