"""Initial persistent sensor, alert and audit schema."""
from alembic import op
from database import AuditEvent, SafetyAlert, SensorReading

revision="0001_initial"
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    bind=op.get_bind()
    SensorReading.metadata.create_all(bind=bind,tables=[SensorReading.__table__,SafetyAlert.__table__,AuditEvent.__table__])

def downgrade():
    bind=op.get_bind()
    for table in (AuditEvent.__table__,SafetyAlert.__table__,SensorReading.__table__):table.drop(bind,checkfirst=True)
