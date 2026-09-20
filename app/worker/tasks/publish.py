"""The ``publish`` queue: firing channel connectors on schedule.

``publish.dispatch`` runs on a beat tick, finds due publications and hands
each to ``publish.run`` on the same queue. Splitting them means one slow or
stuck connector call cannot block the sweep that finds the others.
"""

from __future__ import annotations

import logging
import uuid

from app.db.models import Publication
from app.db.tenancy import system_session
from app.services import publishing
from app.worker.celery_app import celery_app
from app.worker.queues import Queue

logger = logging.getLogger(__name__)


@celery_app.task(name="publish.dispatch", queue=Queue.PUBLISH.value)
def dispatch() -> dict:
    """Find publications that are due and queue one attempt for each."""
    with system_session() as session:
        due = publishing.due_publications(session)
        publication_ids = [str(publication.id) for publication in due]

    for publication_id in publication_ids:
        run.delay(publication_id)

    return {"queued": len(publication_ids)}


@celery_app.task(name="publish.run", queue=Queue.PUBLISH.value)
def run(publication_id: str) -> dict:
    """Make one publish attempt.

    The retry decision lives in ``app.services.publishing.attempt`` — three
    tries against the row itself, then the operator is alerted — not in
    Celery's own retry machinery, so it stays true whether this ran because
    beat fired ``dispatch`` or because an operator retried it by hand
    through the API.
    """
    with system_session() as session:
        publication = session.get(Publication, uuid.UUID(publication_id))
        if publication is None:
            logger.warning(
                "publication vanished before it could be run",
                extra={"publication_id": publication_id},
            )
            return {"publication_id": publication_id, "skipped": True}

        publishing.attempt(session, publication)
        return {
            "publication_id": publication_id,
            "status": publication.status.value,
            "attempt_count": publication.attempt_count,
        }
