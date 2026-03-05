"""
agent.tools 包
对外导出 ToolRegistry 和 ToolDispatcher
"""
from .registry import ToolRegistry, ToolDispatcher, ALL_TOOLS

# 向后兼容别名（旧代码 import ToolExecutor 仍可工作，但构造函数已改变）
ToolExecutor = ToolDispatcher

__all__ = ["ToolRegistry", "ToolDispatcher", "ToolExecutor", "ALL_TOOLS"]
