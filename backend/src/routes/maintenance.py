from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from src.config.database import get_db
from src.models import (
    Crew, MaintenanceWindow, MaintenanceTask, MaintenancePlan, PlanItem, UnscheduledReason,
)
from src.schemas.maintenance import (
    CrewInput, CrewPatch, WindowInput, WindowPatch, TaskGenerateInput, TaskPatch,
    TrialInput, ConfirmInput,
)
from src.middlewares.auth import current_user, can_write, admin_only
from src.services import maintenance as svc
from src.services.audit import record
router = APIRouter(prefix='/maintenance')


# ---------------- 资源：班组 ----------------
@router.get('/crews')
def list_crews(db=Depends(get_db), u=Depends(current_user)):
    return [svc.crew_dict(c) for c in db.scalars(select(Crew).order_by(Crew.id))]


@router.post('/crews', status_code=201)
def create_crew(data: CrewInput, db=Depends(get_db), u=Depends(admin_only)):
    c = svc.create_crew(db, data, u)
    record(db, u.email, 'crew.create', {'id': c.id, 'name': c.name})
    db.commit()
    return svc.crew_dict(c)


@router.patch('/crews/{crew_id}')
def patch_crew(crew_id: int, data: CrewPatch, db=Depends(get_db), u=Depends(admin_only)):
    c = svc.update_crew(db, crew_id, data)
    record(db, u.email, 'crew.update', {'id': crew_id, 'version': c.version})
    db.commit()
    return svc.crew_dict(c)


# ---------------- 资源：天窗 ----------------
@router.get('/windows')
def list_windows(db=Depends(get_db), u=Depends(current_user)):
    return [svc.window_dict(w) for w in
            db.scalars(select(MaintenanceWindow).order_by(MaintenanceWindow.start_at))]


@router.post('/windows', status_code=201)
def create_window(data: WindowInput, db=Depends(get_db), u=Depends(admin_only)):
    w = svc.create_window(db, data, u)
    record(db, u.email, 'window.create', {'id': w.id})
    db.commit()
    return svc.window_dict(w)


@router.patch('/windows/{window_id}')
def patch_window(window_id: int, data: WindowPatch, db=Depends(get_db), u=Depends(admin_only)):
    w, affected, change = svc.update_window(db, window_id, data)
    record(db, u.email, 'window.update', {
        'id': window_id, 'version': w.version, 'affected_plans': [p.id for p in affected], **change})
    db.commit()
    return {'window': svc.window_dict(w),
            'affected_plans': [svc.plan_dict(p) for p in affected]}


# ---------------- 检修任务 ----------------
@router.get('/tasks')
def list_tasks(db=Depends(get_db), u=Depends(current_user)):
    return [svc.task_dict(t) for t in
            db.scalars(select(MaintenanceTask).order_by(MaintenanceTask.id.desc()))]


@router.post('/tasks/generate', status_code=201)
def generate_tasks(data: TaskGenerateInput, db=Depends(get_db), u=Depends(can_write)):
    created, skipped, total = svc.generate_tasks(db, data, u)
    record(db, u.email, 'task.generate',
           {'job_id': data.job_id, 'created': len(created), 'skipped': skipped, 'bands': total})
    db.commit()
    return {'created': [svc.task_dict(t) for t in created],
            'skipped_band_index': skipped, 'bands': total}


@router.patch('/tasks/{task_id}')
def patch_task(task_id: int, data: TaskPatch, db=Depends(get_db), u=Depends(can_write)):
    t = svc.update_task(db, task_id, data)
    record(db, u.email, 'task.update', {'id': task_id})
    db.commit()
    return svc.task_dict(t)


# ---------------- 试排 / 确认 / 开工 ----------------
@router.get('/plans')
def list_plans(db=Depends(get_db), u=Depends(current_user)):
    plans = db.scalars(select(MaintenancePlan).order_by(MaintenancePlan.id.desc())).unique().all()
    return [svc.plan_dict(p,
            items=[svc.item_dict(i) for i in db.scalars(
                select(PlanItem).where(PlanItem.plan_id == p.id).order_by(PlanItem.id))],
            reasons=[svc.reason_dict(r) for r in db.scalars(
                select(UnscheduledReason).where(UnscheduledReason.plan_id == p.id))])
            for p in plans]


@router.get('/plans/{plan_id}')
def plan_detail(plan_id: int, db=Depends(get_db), u=Depends(current_user)):
    p = db.get(MaintenancePlan, plan_id)
    if not p:
        raise HTTPException(404, '检修方案不存在')
    return svc._plan_detail(db, p)


@router.post('/plans/trial', status_code=201)
def create_trial(data: TrialInput, db=Depends(get_db), u=Depends(can_write)):
    plan = svc.create_trial(db, data, u)
    record(db, u.email, 'plan.trial', {'id': plan.id})
    db.commit()
    return svc._plan_detail(db, plan)


@router.post('/plans/{plan_id}/confirm')
def confirm_plan(plan_id: int, data: ConfirmInput, db=Depends(get_db), u=Depends(admin_only)):
    status_code, body, replayed = svc.confirm_plan(db, plan_id, data.confirm_key)
    record(db, u.email, 'plan.confirm', {
        'id': plan_id, 'key': data.confirm_key, 'status': status_code, 'replayed': replayed})
    if status_code != 200:
        db.commit()  # 持久化幂等回执，保证后续重试仍拿到同一结论
        raise HTTPException(409, body.get('detail', '确认冲突，方案未生效'))
    db.commit()
    return body


@router.post('/plans/{plan_id}/start')
def start_plan(plan_id: int, db=Depends(get_db), u=Depends(admin_only)):
    p = svc.start_plan(db, plan_id)
    record(db, u.email, 'plan.start', {'id': plan_id})
    db.commit()
    return svc._plan_detail(db, p)
