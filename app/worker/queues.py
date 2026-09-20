"""Queue layout (handoff section 4).

One ``gpu`` queue served by a single worker with ``concurrency=1`` — the card
is the scarce resource and nothing else may touch it.  Everything that does
not need CUDA goes to a CPU queue so a twelve-minute Wan clip never blocks a
Telegram post.
"""

from __future__ import annotations

from enum import StrEnum


class Queue(StrEnum):
    #: Served by exactly one worker process, concurrency 1.
    GPU = "gpu"
    #: Orchestration: moving packages between states, fan-out, gate handling.
    PIPELINE = "pipeline"
    #: Channel connectors and the scheduler that fires them.
    PUBLISH = "publish"
    #: Metrics collection, backups, quota allocation, lease reclamation.
    MAINTENANCE = "maintenance"
    #: Persian TTS (Piper) and FFmpeg muxing — CPU-bound but slow.
    MEDIA_CPU = "media_cpu"


#: ``celery -A app.worker.celery_app worker -Q gpu -c 1`` and one worker per
#: other queue; see ``docker-compose.yml``.
CPU_QUEUES = (Queue.PIPELINE, Queue.PUBLISH, Queue.MAINTENANCE, Queue.MEDIA_CPU)
