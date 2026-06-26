"""数据库 CRUD 操作封装"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session
from .models import TripTask, TripPlan, RuntimeSetting


# ========== TripTask CRUD ==========

def create_task(db: Session, task_id: str, request_payload: Optional[dict] = None) -> TripTask:
    """创建新任务"""
    task = TripTask(
        id=task_id,
        plan_id=task_id,
        status="processing",
        stage="submitted",
        progress=0,
        message="任务已提交",
        request_payload=request_payload,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def get_task(db: Session, task_id: str) -> Optional[TripTask]:
    """获取任务"""
    return db.query(TripTask).filter(TripTask.id == task_id).first()


def update_task(
    db: Session,
    task_id: str,
    **kwargs,
) -> Optional[TripTask]:
    """更新任务状态"""
    task = get_task(db, task_id)
    if not task:
        return None
    for key, value in kwargs.items():
        if hasattr(task, key) and value is not None:
            setattr(task, key, value)
    task.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(task)
    return task


def list_tasks(db: Session, status: Optional[str] = None, limit: int = 20) -> List[TripTask]:
    """列出任务"""
    query = db.query(TripTask)
    if status:
        query = query.filter(TripTask.status == status)
    return query.order_by(TripTask.updated_at.desc()).limit(limit).all()


# ========== TripPlan CRUD ==========

def create_plan(
    db: Session,
    task_id: str,
    plan_data: dict,
    city: str = "",
    cities: Optional[list] = None,
    start_date: str = "",
    end_date: str = "",
    travel_days: int = 0,
    overall_suggestions: str = "",
) -> TripPlan:
    """创建旅行计划（任务完成时调用）"""
    plan = TripPlan(
        task_id=task_id,
        city=city,
        cities=cities or [],
        start_date=start_date,
        end_date=end_date,
        travel_days=travel_days,
        plan_data=plan_data,
        overall_suggestions=overall_suggestions,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


def get_plan(db: Session, plan_id: str) -> Optional[TripPlan]:
    """获取计划"""
    return db.query(TripPlan).filter(TripPlan.id == plan_id).first()


def get_plan_by_task(db: Session, task_id: str) -> Optional[TripPlan]:
    """通过 task_id 获取计划"""
    return db.query(TripPlan).filter(TripPlan.task_id == task_id).first()


def list_recent_plans(db: Session, limit: int = 10) -> List[Dict[str, Any]]:
    """获取最近成功的历史计划摘要"""
    plans = (
        db.query(TripPlan)
        .order_by(TripPlan.created_at.desc())
        .limit(limit)
        .all()
    )
    return [p.to_summary() for p in plans if p.plan_data]


# ========== RuntimeSetting CRUD ==========

def get_setting(db: Session, key: str) -> Optional[str]:
    """获取运行时配置"""
    setting = db.query(RuntimeSetting).filter(RuntimeSetting.key == key).first()
    return setting.value if setting else None


def get_all_settings(db: Session) -> Dict[str, str]:
    """获取所有运行时配置"""
    settings = db.query(RuntimeSetting).all()
    return {s.key: s.value for s in settings}


def set_setting(db: Session, key: str, value: str) -> RuntimeSetting:
    """设置运行时配置（存在则更新，不存在则创建）"""
    setting = db.query(RuntimeSetting).filter(RuntimeSetting.key == key).first()
    if setting:
        setting.value = value
        setting.updated_at = datetime.utcnow()
    else:
        setting = RuntimeSetting(key=key, value=value)
        db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting


def set_settings_bulk(db: Session, settings: Dict[str, str]) -> Dict[str, str]:
    """批量设置运行时配置"""
    for key, value in settings.items():
        set_setting(db, key, value)
    return get_all_settings(db)
