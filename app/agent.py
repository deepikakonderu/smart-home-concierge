import os
import re
import json
import logging
import sys
from typing import Any, AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.apps import App, ResumabilityConfig
from google.adk.models import Gemini
from google.adk.tools import AgentTool
from google.adk.workflow import Workflow, START, node
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents.context import Context
from google.genai import types
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StdioConnectionParams
from mcp import StdioServerParameters

from app.config import config

# Set up logging for security audits
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("smart_home_security")

# Initialize local MCP Server Toolset
mcp_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "app.mcp_server"],
        )
    )
)

# 1. Specialized Sub-Agent for Groceries/Inventory
inventory_manager = LlmAgent(
    name="inventory_manager",
    model=Gemini(model=config.model),
    instruction="""You are the Home Inventory Specialist.
You have access to MCP tools to retrieve and update home inventory and grocery stock.
Analyze the current stock levels. If any items are below their low_threshold, notify the orchestrator/user and suggest restocking them.
Always use the tools provided to get or update inventory.""",
    tools=[mcp_toolset],
)

# 2. Specialized Sub-Agent for Scheduling Maintenance
maintenance_scheduler = LlmAgent(
    name="maintenance_scheduler",
    model=Gemini(model=config.model),
    instruction="""You are the Home Maintenance Scheduler.
You have access to MCP tools to retrieve and schedule home maintenance tasks (e.g. cleaning, repairs).
Always use the tools provided to list or schedule tasks. Make sure to check due dates and estimated costs.
""",
    tools=[mcp_toolset],
)

# 3. Smart Home Orchestrator (Single-turn mode for graph node compatibility)
orchestrator = LlmAgent(
    name="orchestrator",
    model=Gemini(model=config.model),
    mode="single_turn",
    instruction="""You are the Smart Home Orchestrator.
Your job is to route the user's request to the correct specialist sub-agent using their tools:
- For questions/commands about food, groceries, shopping lists, or household consumables, delegate to inventory_manager.
- For questions/commands about home cleaning, scheduling maintenance, repairs, HVAC, or filters, delegate to maintenance_scheduler.

If a task is successfully executed, state the results clearly.
""",
    tools=[AgentTool(inventory_manager), AgentTool(maintenance_scheduler)],
)

# 4. Security Checkpoint Function Node (Phase 4 / Security)
@node
def security_checkpoint(ctx: Context, node_input: types.Content) -> Event:
    user_message = ""
    if node_input and node_input.parts:
        user_message = node_input.parts[0].text or ""

    # PII Scrubbing
    scrubbed_message = re.sub(r"\b(?:\d[ -]*?){13,16}\b", "[REDACTED_CARD]", user_message)
    scrubbed_message = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[REDACTED_PHONE]", scrubbed_message)

    # Prompt Injection Detection
    injection_keywords = ["ignore instructions", "system prompt", "override", "bypass", "delete all"]
    detected_injection = False
    for kw in injection_keywords:
        if kw in scrubbed_message.lower():
            detected_injection = True
            break

    if detected_injection:
        audit_log = {
            "event": "security_checkpoint_violation",
            "severity": "CRITICAL",
            "reason": "Prompt injection detected",
            "input": scrubbed_message
        }
        logger.warning(json.dumps(audit_log))
        return Event(
            output="Security check failed: Request blocked due to potential policy violation.",
            route="blocked"
        )

    # PIN Verification Command Check
    if "verify pin" in scrubbed_message.lower():
        match = re.search(r"verify pin\s+(\d+)", scrubbed_message, re.IGNORECASE)
        if match:
            pin = match.group(1)
            if pin == "1234":
                ctx.state["owner_pin_verified"] = True
                audit_log = {
                    "event": "pin_verification_success",
                    "severity": "INFO"
                }
                logger.info(json.dumps(audit_log))
                return Event(
                    output="Owner PIN verified successfully. You may now perform sensitive operations.",
                    route="blocked"
                )
            else:
                audit_log = {
                    "event": "pin_verification_failed",
                    "severity": "WARNING",
                    "reason": "Incorrect PIN entry"
                }
                logger.warning(json.dumps(audit_log))
                return Event(
                    output="Verification failed: Incorrect PIN.",
                    route="blocked"
                )

    # Sensitive Action Check (Domain-specific Rule)
    sensitive_keywords = ["unlock", "disarm", "disable alarm", "disable security", "open front door"]
    is_sensitive = False
    for kw in sensitive_keywords:
        if kw in scrubbed_message.lower():
            is_sensitive = True
            break

    if is_sensitive:
        if not ctx.state.get("owner_pin_verified"):
            audit_log = {
                "event": "sensitive_operation_blocked",
                "severity": "WARNING",
                "reason": "Sensitive operation requested without verified PIN",
                "input": scrubbed_message
            }
            logger.warning(json.dumps(audit_log))
            return Event(
                output="Sensitive operation requested. For security, please verify your owner PIN first using 'verify PIN 1234'.",
                route="blocked"
            )

    # Standard check pass log
    audit_log = {
        "event": "security_checkpoint_passed",
        "severity": "INFO",
        "input": scrubbed_message
    }
    logger.info(json.dumps(audit_log))
    
    ctx.state["scrubbed_input"] = scrubbed_message
    return Event(output=scrubbed_message, route="approved")

# 5. Human-in-the-Loop Approval Node
@node(rerun_on_resume=True)
async def human_approval(ctx: Context, node_input: Any) -> AsyncGenerator[Event, None]:
    text = ""
    if isinstance(node_input, types.Content):
        if node_input.parts:
            text = node_input.parts[0].text or ""
    elif isinstance(node_input, str):
        text = node_input
    else:
        text = str(node_input)

    # Detect high-cost schedules or inventory updates
    needs_confirm = False
    lower_text = text.lower()
    
    if "scheduled" in lower_text or "schedule" in lower_text or "updating" in lower_text or "updated" in lower_text:
        needs_confirm = True

    if needs_confirm:
        if not ctx.resume_inputs or "approval" not in ctx.resume_inputs:
            yield RequestInput(
                interrupt_id="approval",
                message=f"System requires approval for this action. Do you approve? (yes/no)"
            )
            return
        
        user_response = ctx.resume_inputs.get("approval", "").strip().lower()
        if user_response in ["yes", "y", "approve"]:
            yield Event(output=f"Action approved by user. Proceeding...\n\n{text}")
        else:
            yield Event(output="Action cancelled by user.")
    else:
        yield Event(output=text)

# 6. Final Formatting Node for web UI
@node
async def final_output(node_input: str) -> AsyncGenerator[Event, None]:
    text = str(node_input)
    yield Event(content=types.Content(role='model', parts=[types.Part.from_text(text=text)]))
    yield Event(output=text)

# Define workflow graph (ADK 2.0 style)
root_agent = Workflow(
    name="smart_home_concierge",
    edges=[
        (START, security_checkpoint),
        (security_checkpoint, {"approved": orchestrator, "blocked": final_output}),
        (orchestrator, human_approval),
        (human_approval, final_output),
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
