"""Local web GUI over the pipeline: start runs, answer approval gates, browse outputs,
the production package, the build queue and the asset library. ``studio gui`` serves it."""

from studio.gui.server import make_server, serve
from studio.gui.service import GuiError, StudioService

__all__ = ["GuiError", "StudioService", "make_server", "serve"]
