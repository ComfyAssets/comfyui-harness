"""ComfyUI Harness: a drop-down terminal for your own agent CLI."""

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}
__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

try:  # absent under pytest, where tests build their own aiohttp app
    from server import PromptServer
except ImportError:
    PromptServer = None

if PromptServer is not None:
    from .harness.routes import install

    install(PromptServer.instance)
