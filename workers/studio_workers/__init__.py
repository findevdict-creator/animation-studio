"""Workers. Start one per queue::

    celery -A studio_workers.tasks worker -Q blender --concurrency 2 --hostname blender@%h
"""
