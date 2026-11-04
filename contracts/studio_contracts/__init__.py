"""Shared contracts: IDs, version numbers, job envelopes and manifests.

This package must import under Blender's bundled Python, so it depends on Pydantic only and
never on FastAPI, SQLAlchemy or ``bpy``.
"""

__version__ = "0.1.0"
