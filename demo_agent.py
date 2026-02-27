"""
Demo Agent — monitored by Norn.

Usage:
    python demo_agent.py

Dashboard: http://localhost:3000
"""

import logging
import os
from pathlib import Path

# Suppress all logs — only conversation output in terminal
logging.disable(logging.CRITICAL)

# ── Workspace isolation ─────────────────────────────────────
# Agent'ın yazdığı dosyalar proje kökünü kirletmesin.
# Tüm output norn_logs/workspace/demo/ altında toplanır.
_NORN_ROOT = Path(__file__).parent
_WORKSPACE = _NORN_ROOT / "norn_logs" / "workspace" / "demo"
_WORKSPACE.mkdir(parents=True, exist_ok=True)
os.chdir(_WORKSPACE)
os.environ["NORN_WORKSPACE"] = str(_WORKSPACE)

from norn import NornHook
from strands import Agent
from strands.handlers import null_callback_handler
from strands.models import BedrockModel
from strands.tools import tool


# ── Tools ──────────────────────────────────────────────────

@tool
def write_file(path: str, content: str) -> str:
    """Write content to a file."""
    with open(path, "w") as f:
        f.write(content)
    return f"File written to {path}"


@tool
def read_file(path: str) -> str:
    """Read content from a file."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"


@tool
def list_files(directory: str = ".") -> str:
    """List files in a directory."""
    try:
        files = os.listdir(directory)
        return "\n".join(files) if files else "No files found."
    except Exception as e:
        return f"Error: {e}"


@tool
def delete_file(path: str) -> str:
    """Delete a file."""
    try:
        os.remove(path)
        return f"Deleted: {path}"
    except FileNotFoundError:
        return f"File not found: {path}"


# ── Agent setup ────────────────────────────────────────────

guard = NornHook(
    norn_url="http://localhost:8000",
    agent_name="Demo Agent",
)

model = BedrockModel(model_id="us.amazon.nova-2-lite-v1:0")

agent = Agent(
    model=model,
    tools=[write_file, read_file, list_files, delete_file],
    hooks=[guard],
    callback_handler=null_callback_handler,
)

# ── Chat loop ──────────────────────────────────────────────

print("Demo Agent ready. Type 'quit' to exit.")
print("Dashboard: http://localhost:3000")
print("-" * 40)

while True:
    try:
        user_input = input("\nYou: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nExiting...")
        break

    if not user_input:
        continue

    if user_input.lower() in ("quit", "exit", "q"):
        print("Goodbye!")
        break

    response = agent(user_input)
    print(f"\nAgent: {response}")
