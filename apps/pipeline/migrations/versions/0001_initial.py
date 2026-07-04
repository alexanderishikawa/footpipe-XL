"""initial schema — batch, page, document, artifact, job

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-04

Creates the full schema from the ORM metadata so the migration stays in lock
step with `pipeline.models`.
"""

from __future__ import annotations

from alembic import op

from pipeline.models import Base

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
