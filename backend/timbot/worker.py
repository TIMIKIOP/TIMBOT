"""Celery tasks for durable, retryable knowledge indexing."""
from celery import Celery
from sqlalchemy import select
from timbot.config import settings
from timbot.db import SessionLocal
from timbot.models import KnowledgeDocument
from timbot.services.ai import index_document
from timbot.services.providers.base import AIProviderUnavailableError

celery_app = Celery("timbot", broker=settings.redis_url or "redis://localhost:6379/0")
celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
)

@celery_app.task(bind=True, autoretry_for=(AIProviderUnavailableError,), retry_backoff=True, retry_jitter=True, max_retries=4)
def index_knowledge_document(self, workspace_id: int, document_id: int) -> int:
    """Index only a document that belongs to the task's recorded workspace."""
    with SessionLocal() as db:
        document = db.scalar(select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.workspace_id == workspace_id,
        ))
        if document is None:
            return 0
        return index_document(db, document)
