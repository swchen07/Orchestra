"""Legacy v1 tables, retained for migration compatibility and existing local records.

Active chat execution lives in open_webui.execution; no new tasks use these tables.
"""

from sqlalchemy import JSON, Column, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import declarative_base

WorkbenchBase = declarative_base()


class WorkbenchRun(WorkbenchBase):
    __tablename__ = 'workbench_run'
    id = Column(String(36), primary_key=True)
    owner_id = Column(Text, nullable=False, index=True)
    harness_id = Column(Text, nullable=False)
    prompt = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    timeout_seconds = Column(Integer, nullable=False)
    created_at = Column(Float, nullable=False)
    started_at = Column(Float)
    ended_at = Column(Float)
    error_code = Column(Text)
    sequence = Column(Integer, nullable=False, default=0)


class WorkbenchEvent(WorkbenchBase):
    __tablename__ = 'workbench_event'
    run_id = Column(String(36), ForeignKey('workbench_run.id', ondelete='CASCADE'), primary_key=True)
    sequence = Column(Integer, primary_key=True)
    created_at = Column(Float, nullable=False)
    type = Column(Text, nullable=False)
    payload = Column(JSON, nullable=False)
