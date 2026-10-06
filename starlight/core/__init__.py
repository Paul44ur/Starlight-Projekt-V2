"""Public API of the Starlight core. Modules import from here."""

from starlight.core.agent import Agent, AgentResult
from starlight.core.config import ConfigError, StarlightConfig, load_config
from starlight.core.dispatcher import Dispatcher, ToolResult
from starlight.core.events import Event, EventBus
from starlight.core.kernel import Kernel
from starlight.core.killswitch import KillSwitch, KillSwitchEngaged
from starlight.core.model import (
    EchoProvider,
    Message,
    ModelProvider,
    ModelResponse,
    ToolCall,
    register_provider,
)
from starlight.core.modules import Module, ModuleContext, ModuleManager, ModuleManifest
from starlight.core.paths import Paths
from starlight.core.permissions import Permission, PermissionPolicy
from starlight.core.tools import Tool, ToolError, tool

__all__ = [
    "Agent",
    "AgentResult",
    "ConfigError",
    "Dispatcher",
    "EchoProvider",
    "Event",
    "EventBus",
    "Kernel",
    "KillSwitch",
    "KillSwitchEngaged",
    "Message",
    "ModelProvider",
    "ModelResponse",
    "Module",
    "ModuleContext",
    "ModuleManager",
    "ModuleManifest",
    "Paths",
    "Permission",
    "PermissionPolicy",
    "StarlightConfig",
    "Tool",
    "ToolCall",
    "ToolError",
    "ToolResult",
    "load_config",
    "register_provider",
    "tool",
]
