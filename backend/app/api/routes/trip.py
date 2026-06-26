"""旅行规划 API 路由 (数据库版) - WebSocket 同步 + 轮询兼容模式"""

import asyncio
import traceback
import uuid
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from ...agents.trip_planner_agent import get_trip_planner_agent
from ...database import get_db
from ...database.crud import (
    create_task, get_task as db_get_task, update_task as db_update_task,
    list_recent_plans, create_plan,
)
from ...models.schemas import TripPlanResponse, TripRequest
from ...services.knowledge_graph_service import build_knowledge_graph

router = APIRouter(prefix="/trip", tags=["旅行规划"])

# 内存任务存储（用于 WebSocket 快速广播）+ 数据库持久化
_tasks: Dict[str, Dict[str, Any]] = {}
_FINAL_TASK_STATUS = {"completed", "failed"}


def _create_task_state(task_id: str) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "plan_id": task_id,
        "status": "processing",
        "stage": "submitted",
        "progress": 0,
        "message": "任务已提交，等待执行...",
        "result": None,
        "error": None,
        "request_payload": None,
        "subscribers": [],
    }


def _serialize_result(result: Any) -> Any:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    return result


def _build_task_event(task_id: str, task: Dict[str, Any], include_result: bool = True) -> Dict[str, Any]:
    event = {
        "task_id": task_id,
        "plan_id": task.get("plan_id", task_id),
        "status": task.get("status", "processing"),
        "stage": task.get("stage", ""),
        "progress": task.get("progress", 0),
        "message": task.get("message", ""),
    }
    if task.get("error"):
        event["error"] = task["error"]
    if task["status"] == "failed" and task.get("request_payload") is not None:
        event["request_payload"] = task["request_payload"]
    if include_result and task.get("result") is not None:
        event["result"] = _serialize_result(task["result"])
    return event


def _broadcast_task_event(task_id: str, event: Dict[str, Any]) -> None:
    task = _tasks.get(task_id)
    if not task:
        return
    dead_queues = []
    for queue in task.get("subscribers", []):
        try:
            queue.put_nowait(event)
        except Exception:
            dead_queues.append(queue)
    if dead_queues:
        task["subscribers"] = [q for q in task.get("subscribers", []) if q not in dead_queues]


async def _update_task_state(
    task_id: str,
    *,
    status: str | None = None,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    result: Any = None,
    error: str | None = None,
) -> None:
    """更新任务状态 → 内存 + 数据库 + 广播"""
    task = _tasks.get(task_id)
    if not task:
        return

    if status is not None:
        task["status"] = status
    if stage is not None:
        task["stage"] = stage
    if progress is not None:
        task["progress"] = progress
    if message is not None:
        task["message"] = message
    if result is not None:
        task["result"] = result
    if error is not None:
        task["error"] = error

    # 持久化到数据库
    try:
        from ...database import SessionLocal
        db = SessionLocal()
        try:
            db_update_task(
                db, task_id,
                status=task["status"], stage=task["stage"],
                progress=task["progress"], message=task["message"],
                result=_serialize_result(result) if result else None,
                error=task.get("error"),
            )
        finally:
            db.close()
    except Exception as e:
        print(f"⚠️  数据库持久化任务 {task_id} 失败: {e}")

    event = _build_task_event(task_id, task, include_result=True)
    _broadcast_task_event(task_id, event)


@router.post("/plan", summary="提交旅行规划任务")
async def plan_trip(request: TripRequest, db: Session = Depends(get_db)):
    """提交旅行规划任务（立即返回 task_id）。"""
    task_id = str(uuid.uuid4())[:8]

    # 创建内存状态
    state = _create_task_state(task_id)
    state["request_payload"] = request.model_dump(mode="json")
    _tasks[task_id] = state

    # 写入数据库
    create_task(db, task_id, request_payload=state["request_payload"])

    _city_display = ' → '.join(cs.city for cs in request.cities) if request.cities else request.city
    print(f"\n{'=' * 60}")
    print(f"📥 收到旅行规划请求 (task_id={task_id}):")
    print(f"   城市: {_city_display}")
    print(f"   日期: {request.start_date} - {request.end_date}")
    print(f"   天数: {request.travel_days}")
    print(f"{'=' * 60}\n")

    await _update_task_state(task_id, status="processing", stage="submitted", progress=5,
                              message="任务已提交，正在初始化流程...")

    asyncio.create_task(_run_trip_planning(task_id, request))

    return {
        "task_id": task_id,
        "plan_id": task_id,
        "status": "processing",
        "ws_url": f"/api/trip/ws/{task_id}",
        "message": f"任务已提交，可通过 WebSocket /api/trip/ws/{task_id} 实时订阅状态",
    }


async def _run_trip_planning(task_id: str, request: TripRequest):
    """后台执行旅行规划并推送进度。"""
    try:
        await _update_task_state(task_id, stage="initializing", progress=10,
                                  message="正在获取多智能体系统实例...")
        agent = get_trip_planner_agent()

        async def progress_callback(stage: str, message: str, progress: int) -> None:
            await _update_task_state(task_id, stage=stage, progress=progress, message=message)

        trip_plan = await agent.plan_trip(request, progress_callback=progress_callback)

        await _update_task_state(task_id, stage="graph_building", progress=95,
                                  message="正在构建知识图谱...")
        graph_data = build_knowledge_graph(trip_plan, language=getattr(request, 'language', 'zh') or 'zh')

        trip_result = TripPlanResponse(
            success=True, message="旅行计划生成成功",
            plan_id=task_id, data=trip_plan, graph_data=graph_data,
        )

        print(f"✅ 任务 {task_id} 完成")
        await _update_task_state(task_id, status="completed", stage="completed", progress=100,
                                  message="旅行计划生成成功", result=trip_result)

        # 保存到历史计划表
        try:
            from ...database import SessionLocal
            db = SessionLocal()
            try:
                create_plan(
                    db, task_id=task_id,
                    plan_data=_serialize_result(trip_result),
                    city=getattr(trip_plan, 'city', ''),
                    cities=getattr(trip_plan, 'cities', None) or [],
                    start_date=getattr(trip_plan, 'start_date', ''),
                    end_date=getattr(trip_plan, 'end_date', ''),
                    travel_days=len(getattr(trip_plan, 'days', [])),
                    overall_suggestions=getattr(trip_plan, 'overall_suggestions', ''),
                )
            finally:
                db.close()
        except Exception as e:
            print(f"⚠️  保存历史计划失败: {e}")

    except Exception as e:
        print(f"❌ 任务 {task_id} 失败: {e}")
        traceback.print_exc()
        try:
            from ...services.xhs_service import XHSCookieExpiredError
            error_msg = f"【认证失败】{str(e)}" if isinstance(e, XHSCookieExpiredError) else str(e)
        except ImportError:
            error_msg = str(e)

        await _update_task_state(task_id, status="failed", stage="failed", progress=100,
                                  message=error_msg, error=error_msg)


@router.websocket("/ws/{task_id}")
async def trip_task_ws(websocket: WebSocket, task_id: str):
    """WebSocket 订阅任务状态。"""
    await websocket.accept()
    task = _tasks.get(task_id)
    if not task:
        await websocket.send_json({
            "task_id": task_id, "plan_id": task_id,
            "status": "failed", "stage": "failed", "progress": 100,
            "message": "任务不存在", "error": "任务不存在",
        })
        await websocket.close(code=1008)
        return

    queue: asyncio.Queue = asyncio.Queue()
    task["subscribers"].append(queue)

    snapshot = _build_task_event(task_id, task, include_result=True)
    await websocket.send_json(snapshot)
    if snapshot["status"] in _FINAL_TASK_STATUS:
        try:
            await websocket.close()
        except Exception:
            pass
        task["subscribers"] = [q for q in task.get("subscribers", []) if q is not queue]
        return

    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event.get("status") in _FINAL_TASK_STATUS:
                break
    except WebSocketDisconnect:
        pass
    finally:
        task = _tasks.get(task_id)
        if task:
            task["subscribers"] = [q for q in task.get("subscribers", []) if q is not queue]
        try:
            await websocket.close()
        except Exception:
            pass


@router.get("/history", summary="最近历史计划")
async def get_trip_history(limit: int = 10, db: Session = Depends(get_db)):
    """查询最近的历史计划摘要。"""
    safe_limit = max(1, min(int(limit or 10), 50))
    return {"items": list_recent_plans(db, safe_limit)}


@router.get("/status/{task_id}", summary="查询任务状态")
async def get_task_status(task_id: str, db: Session = Depends(get_db)):
    """轮询旅行规划任务状态。"""
    # 优先从内存读取最新状态
    task = _tasks.get(task_id)
    if task:
        if task["status"] == "completed":
            return {"task_id": task_id, "plan_id": task.get("plan_id", task_id),
                    "status": "completed", "result": _serialize_result(task.get("result"))}
        if task["status"] == "failed":
            return {"task_id": task_id, "plan_id": task.get("plan_id", task_id),
                    "status": "failed", "error": task.get("error", ""),
                    "request_payload": task.get("request_payload")}
        return {"task_id": task_id, "plan_id": task.get("plan_id", task_id),
                "status": "processing", "stage": task.get("stage", ""),
                "progress": task.get("progress", 0), "progress_text": task.get("message", "处理中...")}

    # 回退到数据库
    db_task = db_get_task(db, task_id)
    if db_task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if db_task.status == "completed":
        return {"task_id": task_id, "plan_id": db_task.plan_id,
                "status": "completed", "result": db_task.result}
    if db_task.status == "failed":
        return {"task_id": task_id, "plan_id": db_task.plan_id,
                "status": "failed", "error": db_task.error or "",
                "request_payload": db_task.request_payload}
    return {"task_id": task_id, "plan_id": db_task.plan_id,
            "status": "processing", "stage": db_task.stage or "",
            "progress": db_task.progress or 0, "progress_text": db_task.message or "处理中..."}


@router.get("/health", summary="健康检查")
async def health_check():
    try:
        agent = get_trip_planner_agent()
        return {
            "status": "healthy", "service": "trip-planner",
            "agent_name": agent.planner_agent.name,
            "tools_count": len(agent.weather_agent.list_tools()) + len(agent.hotel_agent.list_tools()),
        }
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"服务不可用: {str(e)}")
