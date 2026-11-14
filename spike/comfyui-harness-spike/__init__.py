"""THROWAWAY SPIKE. Unauthenticated shell. Does nothing unless HARNESS_SPIKE=1."""
import os

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
__all__ = ["WEB_DIRECTORY", "NODE_CLASS_MAPPINGS"]

if os.environ.get("HARNESS_SPIKE") == "1":
    from . import spike_server  # noqa: F401
