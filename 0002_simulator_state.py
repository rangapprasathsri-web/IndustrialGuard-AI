"""Persist simulator scenario and meteorology state."""
from alembic import op
import sqlalchemy as sa

revision="0002_simulator_state"
down_revision="0001_initial"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("application_state",sa.Column("key",sa.String(length=80),primary_key=True),sa.Column("value",sa.Text(),nullable=False))

def downgrade():
    op.drop_table("application_state")
