"""document metadata columns — document_date, originator, entities, metadata_json

Revision ID: 0002_document_metadata
Revises: 0001_initial
Create Date: 2026-07-04

Adds v1.1 rich-metadata fields to document. Uses IF NOT EXISTS so fresh DBs
where 0001_initial create_all already created these columns remain idempotent.
"""

from __future__ import annotations

from alembic import op

revision = "0002_document_metadata"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document ADD COLUMN IF NOT EXISTS document_date DATE")
    op.execute("ALTER TABLE document ADD COLUMN IF NOT EXISTS originator VARCHAR(256)")
    op.execute(
        "ALTER TABLE document ADD COLUMN IF NOT EXISTS entities VARCHAR[] "
        "NOT NULL DEFAULT '{}'"
    )
    op.execute("ALTER TABLE document ADD COLUMN IF NOT EXISTS metadata_json JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE document DROP COLUMN IF EXISTS metadata_json")
    op.execute("ALTER TABLE document DROP COLUMN IF EXISTS entities")
    op.execute("ALTER TABLE document DROP COLUMN IF EXISTS originator")
    op.execute("ALTER TABLE document DROP COLUMN IF EXISTS document_date")
