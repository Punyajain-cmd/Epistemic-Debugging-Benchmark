"""Mock scientific instruments, databases, and analysis tools."""

from epidebug.tools.analysis_tools import ANALYSIS_TOOL_REGISTRY
from epidebug.tools.database_tools import DATABASE_TOOL_REGISTRY
from epidebug.tools.lab_tools import LAB_TOOL_REGISTRY

TOOL_REGISTRY: dict[str, callable] = {
    **LAB_TOOL_REGISTRY,
    **DATABASE_TOOL_REGISTRY,
    **ANALYSIS_TOOL_REGISTRY,
}


def get_tool(name: str):
    """Return a registered tool function by name."""
    if name not in TOOL_REGISTRY:
        raise KeyError(f"Unknown tool: {name}")
    return TOOL_REGISTRY[name]


def list_tools() -> list[dict[str, str]]:
    """List available tools with short descriptions."""
    tools = []
    for name, fn in TOOL_REGISTRY.items():
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        tools.append({"name": name, "description": doc})
    return tools


def invoke_tool(name: str, **kwargs):
    """Invoke a registered tool with keyword arguments."""
    return get_tool(name)(**kwargs)
