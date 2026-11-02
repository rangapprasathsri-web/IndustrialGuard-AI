"""SQLAlchemy persistence layer. SQLite is the local default; Compose uses PostgreSQL."""
from __future__ import annotations
import json, os
from datetime import datetime
from sqlalchemy import create_engine, DateTime, Integer, String, Text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool

IS_VERCEL=os.getenv("VERCEL")=="1"
_database_url=os.getenv("DATABASE_URL")
if IS_VERCEL and not _database_url:
    raise RuntimeError("Set DATABASE_URL to a managed PostgreSQL connection before deploying to Vercel.")
DATABASE_URL=_database_url or "sqlite:///./industrialshield.db"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL="postgresql://"+DATABASE_URL[len("postgres://"):]
if IS_VERCEL and not DATABASE_URL.startswith("postgresql"):
    raise RuntimeError("Vercel deployments require persistent PostgreSQL; SQLite storage is ephemeral.")
connect_args={"check_same_thread":False} if DATABASE_URL.startswith("sqlite") else {}
engine_options={"connect_args":connect_args,"pool_pre_ping":True}
if IS_VERCEL:
    # Use the database provider's pooled URL; serverless instances should not
    # retain private per-process pools as instances scale and scale to zero.
    engine_options["poolclass"]=NullPool
engine=create_engine(DATABASE_URL,**engine_options)
Session=sessionmaker(bind=engine,expire_on_commit=False)

class Base(DeclarativeBase): pass

class SensorReading(Base):
    __tablename__="sensor_readings"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    facility_id:Mapped[str]=mapped_column(String(80),index=True)
    sensor_id:Mapped[str]=mapped_column(String(120),index=True)
    observed_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    payload:Mapped[str]=mapped_column(Text)
    synthetic:Mapped[int]=mapped_column(Integer,default=1)

class SafetyAlert(Base):
    __tablename__="safety_alerts"
    id:Mapped[str]=mapped_column(String(80),primary_key=True)
    facility_id:Mapped[str]=mapped_column(String(80),index=True)
    status:Mapped[str]=mapped_column(String(32),index=True)
    created_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    payload:Mapped[str]=mapped_column(Text)

class AuditEvent(Base):
    __tablename__="audit_events"
    id:Mapped[int]=mapped_column(Integer,primary_key=True)
    event:Mapped[str]=mapped_column(String(100),index=True)
    alert_id:Mapped[str]=mapped_column(String(80),index=True)
    actor:Mapped[str]=mapped_column(String(255))
    occurred_at:Mapped[datetime]=mapped_column(DateTime(timezone=True),index=True)
    payload:Mapped[str]=mapped_column(Text)

class ApplicationState(Base):
    __tablename__="application_state"
    key:Mapped[str]=mapped_column(String(80),primary_key=True)
    value:Mapped[str]=mapped_column(Text)

Index("ix_readings_facility_time",SensorReading.facility_id,SensorReading.observed_at)

def initialize():
    Base.metadata.create_all(engine)

def _dt(value): return datetime.fromisoformat(value.replace("Z","+00:00"))
def _json(value): return json.dumps(value,separators=(",",":"),allow_nan=False)
def save_reading(payload:dict):
    with Session.begin() as s:
        s.add(SensorReading(facility_id=payload["facility_id"],sensor_id=payload["sensor_id"],observed_at=_dt(payload["timestamp"]),payload=_json(payload),synthetic=int(bool(payload.get("synthetic",False)))))
def load_readings(limit:int=10000):
    with Session() as s:
        rows=s.query(SensorReading).order_by(SensorReading.observed_at.desc()).limit(limit).all()
        return [json.loads(x.payload) for x in reversed(rows)]
def save_alert(payload:dict):
    with Session.begin() as s:
        row=s.get(SafetyAlert,payload["id"])
        if row is None:
            row=SafetyAlert(id=payload["id"],facility_id=payload["facility_id"],status=payload["status"],created_at=_dt(payload["created_at"]),payload=_json(payload));s.add(row)
        else:
            row.facility_id=payload["facility_id"];row.status=payload["status"];row.payload=_json(payload)
def load_alerts(limit:int=2000):
    with Session() as s:
        rows=s.query(SafetyAlert).order_by(SafetyAlert.created_at.desc()).limit(limit).all()
        return [json.loads(x.payload) for x in rows]
def save_audit(payload:dict):
    with Session.begin() as s:
        s.add(AuditEvent(event=payload["event"],alert_id=payload["alert_id"],actor=payload["actor"],occurred_at=_dt(payload["timestamp"]),payload=_json(payload)))
def load_audit(limit:int=5000):
    with Session() as s:
        rows=s.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit).all()
        return [json.loads(x.payload) for x in reversed(rows)]
def save_state(key:str,value:dict):
    with Session.begin() as s:
        row=s.get(ApplicationState,key)
        if row is None:s.add(ApplicationState(key=key,value=_json(value)))
        else:row.value=_json(value)
def load_state(key:str):
    with Session() as s:
        row=s.get(ApplicationState,key)
        return json.loads(row.value) if row else {}
