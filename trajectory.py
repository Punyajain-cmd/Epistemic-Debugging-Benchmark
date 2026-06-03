"""
Trajectory logging and analysis for agent mode evaluations.

Records every tool call made by the AI agent during an epistemic debugging session,
enabling post-hoc analysis of reasoning patterns, efficiency, and anti-patterns.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCallRecord:
    """A single recorded tool call."""
    timestamp: float
    tool_name: str
    arguments: dict[str, Any]
    result: Any = None
    duration_ms: float = 0.0
    was_useful: bool | None = None


@dataclass
class TrajectoryLogger:
    """
    Records and analyzes tool call trajectories during agent evaluations.

    Usage:
        logger = TrajectoryLogger(case_id="RF-001")
        logger.log_call("ph_calculator", {"solution": "Tris-HCl"}, result={...})
        summary = logger.get_summary()
        patterns = logger.detect_anti_patterns()
    """

    case_id: str
    calls: list[ToolCallRecord] = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    def log_call(self, tool_name: str, arguments: dict[str, Any],
                 result: Any = None, duration_ms: float = 0.0) -> None:
        self.calls.append(ToolCallRecord(
            timestamp=time.time(), tool_name=tool_name,
            arguments=arguments, result=result, duration_ms=duration_ms,
        ))

    def get_summary(self) -> dict[str, Any]:
        tool_counts: dict[str, int] = {}
        for call in self.calls:
            tool_counts[call.tool_name] = tool_counts.get(call.tool_name, 0) + 1
        return {
            "case_id": self.case_id,
            "total_calls": len(self.calls),
            "unique_tools_used": len(tool_counts),
            "tool_usage": tool_counts,
            "total_tool_duration_ms": round(sum(c.duration_ms for c in self.calls), 1),
            "wall_clock_seconds": round(time.time() - self.start_time, 2),
        }

    def detect_anti_patterns(self) -> list[dict[str, Any]]:
        patterns: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        for call in self.calls:
            sig = f"{call.tool_name}:{sorted(call.arguments.items())}"
            seen[sig] = seen.get(sig, 0) + 1
        redundant = {k: v for k, v in seen.items() if v > 1}
        if redundant:
            patterns.append({"type": "redundant_calls", "severity": "medium",
                             "description": f"{sum(v-1 for v in redundant.values())} redundant calls"})
        tool_names = [c.tool_name for c in self.calls]
        if len(self.calls) > 3 and len(set(tool_names)) == 1:
            patterns.append({"type": "single_tool_fixation", "severity": "high",
                             "description": f"Used only '{tool_names[0]}' across {len(self.calls)} calls"})
        return patterns

    def to_dict(self) -> list[dict[str, Any]]:
        return [{"timestamp": c.timestamp, "tool": c.tool_name,
                 "args": c.arguments, "duration_ms": c.duration_ms} for c in self.calls]
