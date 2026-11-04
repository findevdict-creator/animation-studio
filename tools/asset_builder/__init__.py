"""Asset build phase 4: the sandbox that runs Asset Builder scripts headless.

The agent side lives in studio/agents (asset_builder.py, render_qa.py) and the QA loop in
studio/assets/builder.py; this package holds what needs Blender: the static check, the
subprocess runner and the child script."""
