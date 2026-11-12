"""Opt-in PostgreSQL/pgvector integration checks against a dedicated test DB."""
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from timbot.db import Base
from timbot.models import KnowledgeChunk, KnowledgeDocument, Workspace
from timbot.services import ai

POSTGRES_URL = os.getenv("TIMBOT_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not POSTGRES_URL, reason="Set TIMBOT_TEST_POSTGRES_URL to a dedicated PostgreSQL/pgvector test database")

def test_pgvector_retrieval_cannot_cross_workspace(monkeypatch):
    admin = create_engine(POSTGRES_URL, pool_pre_ping=True)
    schema = "timbot_test_" + uuid.uuid4().hex
    isolated = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        isolated = create_engine(POSTGRES_URL, connect_args={"options": f"-csearch_path={schema},public"})
        Base.metadata.create_all(isolated)
        with Session(isolated) as db:
            alpha = Workspace(name="Alpha")
            beta = Workspace(name="Beta")
            db.add_all([alpha, beta])
            db.flush()
            alpha_doc = KnowledgeDocument(workspace_id=alpha.id, filename="a.txt", media_type="text/plain", content="alpha reference")
            beta_doc = KnowledgeDocument(workspace_id=beta.id, filename="b.txt", media_type="text/plain", content="beta reference")
            db.add_all([alpha_doc, beta_doc])
            db.flush()
            vector = [1.0] + [0.0] * 767
            embedding_profile = "ollama:nomic-embed-text:768"
            db.add_all([
                KnowledgeChunk(workspace_id=alpha.id, document_id=alpha_doc.id, ordinal=0, content="alpha reference", embedding_model=embedding_profile, embedding=vector),
                KnowledgeChunk(workspace_id=beta.id, document_id=beta_doc.id, ordinal=0, content="beta reference", embedding_model=embedding_profile, embedding=vector),
            ])
            db.commit()

            from timbot.services.providers.base import EmbeddingBatch
            fake = SimpleNamespace(embed=lambda texts: EmbeddingBatch([vector], None, 1), embedding_profile=embedding_profile)
            monkeypatch.setattr(ai, "provider", lambda: fake)
            matches = ai.retrieve(db, alpha.id, "query")
            assert [row.content for row in matches] == ["alpha reference"]
    finally:
        if isolated is not None:
            isolated.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        admin.dispose()
