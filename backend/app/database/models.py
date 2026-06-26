"""SQLAlchemy 数据模型"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, DateTime, JSON, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from . import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _uuid():
    return str(uuid.uuid4())


class TripTask(Base):
    """旅行规划任务表（替代 JSON 文件存储）"""
    __tablename__ = "trip_tasks"

    id = Column(String(36), primary_key=True, default=_uuid)
    plan_id = Column(String(36), default=_uuid)
    status = Column(String(20), default="processing", index=True)  # processing / completed / failed
    stage = Column(String(50), default="")
    progress = Column(Integer, default=0)
    message = Column(Text, default="")
    request_payload = Column(JSON, nullable=True)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # 关联
    plan = relationship("TripPlan", back_populates="task", uselist=False)

    def to_dict(self):
        return {
            "task_id": self.id,
            "plan_id": self.plan_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "message": self.message,
            "request_payload": self.request_payload,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class TripPlan(Base):
    """旅行计划表（历史记录）"""
    __tablename__ = "trip_plans"

    id = Column(String(36), primary_key=True, default=_uuid)
    task_id = Column(String(36), ForeignKey("trip_tasks.id"), unique=True)
    city = Column(String(100), default="")
    cities = Column(JSON, default=list)  # ["北京", "西安"]
    start_date = Column(String(20), default="")
    end_date = Column(String(20), default="")
    travel_days = Column(Integer, default=0)
    plan_data = Column(JSON, nullable=True)  # 完整行程 JSON
    overall_suggestions = Column(Text, default="")
    created_at = Column(DateTime, default=_utcnow)

    task = relationship("TripTask", back_populates="plan")

    def to_summary(self):
        return {
            "plan_id": self.id,
            "task_id": self.task_id,
            "city": self.city,
            "cities": self.cities or [],
            "start_date": self.start_date,
            "end_date": self.end_date,
            "travel_days": self.travel_days,
            "overall_suggestions": self.overall_suggestions[:100] if self.overall_suggestions else "",
            "updated_at": self.created_at.isoformat() if self.created_at else None,
        }


class RuntimeSetting(Base):
    """运行时配置表（替代 JSON 文件）"""
    __tablename__ = "runtime_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, default="")
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
