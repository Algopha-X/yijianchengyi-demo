"""Server-owned template rendering; no user HTML or remote resources."""

from app.rendering.renderer import render_template

__all__ = ["render_template"]
