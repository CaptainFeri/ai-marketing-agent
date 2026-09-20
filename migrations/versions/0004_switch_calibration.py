"""Record what a window switch really costs, so the estimate self-corrects.

``app.services.tuning`` estimates the switch time from the hardware probe at
install. Once the GPU worker has actually switched a few times, the measured
average is better than any estimate, and the scheduler should batch against
the real number.

Revision ID: 0004_switch_calibration
Revises: 0003_pgvector
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_switch_calibration"
down_revision: str | None = "0003_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("gpu_window_state", sa.Column("ewma_switch_seconds", sa.Float(), nullable=True))
    op.add_column(
        "gpu_window_state",
        sa.Column("switch_samples", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("gpu_window_state", "switch_samples")
    op.drop_column("gpu_window_state", "ewma_switch_seconds")
