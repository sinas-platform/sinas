"""Helpers for parsing system_tools configuration on agents.

system_tools is a mixed list of strings and dicts:
  ["codeExecution", {"name": "databaseIntrospection", "connections": ["built-in"]}]

These helpers normalize and query the list.
"""
from typing import Any, Optional


def has_system_tool(system_tools: list, tool_name: str) -> bool:
    """Check if a system tool is enabled (by name)."""
    for entry in system_tools or []:
        if isinstance(entry, str) and entry == tool_name:
            return True
        if isinstance(entry, dict) and entry.get("name") == tool_name:
            return True
    return False


def get_system_tool_config(system_tools: list, tool_name: str) -> Optional[dict[str, Any]]:
    """Get the config dict for a system tool, or None if not enabled.

    For string entries, returns an empty dict (enabled, no config).
    For dict entries, returns the full dict.
    """
    for entry in system_tools or []:
        if isinstance(entry, str) and entry == tool_name:
            return {}
        if isinstance(entry, dict) and entry.get("name") == tool_name:
            return entry
    return None


def list_enabled_tools(system_tools: list) -> list[str]:
    """Return a list of enabled tool names."""
    names = []
    for entry in system_tools or []:
        if isinstance(entry, str):
            names.append(entry)
        elif isinstance(entry, dict) and entry.get("name"):
            names.append(entry["name"])
    return names
