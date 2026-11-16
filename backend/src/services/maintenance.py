"""封锁检修排程的持久化与事务规则。

关键并发语义：
- 确认、天窗变更都先取同一把 Postgres 咨询锁（单写者串行化裁决），再做版本/冲突校验；
- 试排单保存资源版本，确认时版本不一致即“旧试排拒绝确认”；
- confirm_key 幂等：回执丢失重试返回首次结果，绝不重复占用；
- 天窗缩短/撤销只失效受影响的已确认方案，其他已确认安排保留。
"""
import time
from fastapi import HTTPException
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from src.config.database import engine
from src.models import (
    Job, Crew, MaintenanceWindow, MaintenanceTask, MaintenancePlan,
    PlanItem, UnscheduledReason, ConfirmReceipt,
)
from src.services import scheduling
from src.services.scheduling import Window, Crew as CrewDTO, Task, build_trial

# 咨询锁固定常量，确认与资源变更共用，保证“先完成者裁决”。
_SCHED_LOCK_ID = 0x5A17_0001


def _acquire_scheduler_lock(db):
    """Postgres 用事务级咨询锁串行化；SQLite 等本地库退化为无操作（写事务本身串行）。"""
    if engine.dialect.name == 'postgresql':
        db.execute(func.pg_advisory_xact_lock(_SCHED_LOCK_ID))


# ---------------- 序列化 ----------------
def crew_dict(c: Crew):
    return {k: getattr(c, k) for k in ('id', 'name', 'skill', 'active', 'version', 'created_at')}


def window_dict(w: MaintenanceWindow):
    return {k: getattr(w, k) for k in
            ('id', 'name', 'start_at', 'end_at', 'line_direction', 'active', 'version', 'created_at')}


def task_dict(t: MaintenanceTask):
    return {k: getattr(t, k) for k in
            ('id', 'job_id', 'band_index', 'title', 'from_km', 'to_km', 'work_hours', 'note',
             'active', 'created_at')}


def plan_dict(p: MaintenancePlan, items=None, reasons=None):
    return {
        'id': p.id, 'name': p.name, 'status': p.status,
        'created_by': p.created_by, 'created_at': p.created_at,
        'confirmed_at': p.confirmed_at, 'invalidated_at': p.invalidated_at,
        'invalidate_reason': p.invalidate_reason,
        'snapshot': p.snapshot,
        'items': items if items is not None else [],
        'unscheduled': reasons if reasons is not None else [],
    }


def item_dict(i: PlanItem):
    return {k: getattr(i, k) for k in
            ('id', 'plan_id', 'task_id', 'crew_id', 'window_id', 'start_at', 'end_at',
             'work_hours', 'window_version', 'crew_version')}


def reason_dict(r: UnscheduledReason):
    return {'id': r.id, 'plan_id': r.plan_id, 'task_id': r.task_id,
            'code': r.code, 'detail': r.detail}


# ---------------- 资源：班组 ----------------
def create_crew(db, data, user):
    if db.scalar(select(Crew).where(Crew.name == data.name)):
        raise HTTPException(409, '班组名称已存在')
    c = Crew(name=data.name, skill=data.skill, active=data.active, version=1, created_at=time.time())
    db.add(c)
    db.flush()
    return c


def update_crew(db, crew_id, data):
    _acquire_scheduler_lock(db)
    c = db.get(Crew, crew_id)
    if not c:
        raise HTTPException(404, '班组不存在')
    payload = data.model_dump(exclude_none=True)
    if 'name' in payload and db.scalar(
            select(Crew).where(Crew.name == payload['name'], Crew.id != crew_id)):
        raise HTTPException(409, '班组名称已存在')
    changed = any(k in payload for k in ('name', 'skill', 'active'))
    for k, v in payload.items():
        setattr(c, k, v)
    if changed:
        c.version += 1
    db.flush()
    return c


# ---------------- 资源：天窗 ----------------
def create_window(db, data, user):
    if data.end_at <= data.start_at:
        raise HTTPException(422, '天窗结束时间必须晚于开始时间')
    w = MaintenanceWindow(name=data.name, start_at=data.start_at, end_at=data.end_at,
                          line_direction=data.line_direction, active=data.active,
                          version=1, created_at=time.time())
    db.add(w)
    db.flush()
    return w


def update_window(db, window_id, data):
    """天窗缩短或撤销：受影响的已确认方案失效并拒绝开工，其他已确认安排保留。"""
    _acquire_scheduler_lock(db)
    w = db.get(MaintenanceWindow, window_id)
    if not w:
        raise HTTPException(404, '天窗不存在')
    payload = data.model_dump(exclude_none=True)
    new_start = payload.get('start_at', w.start_at)
    new_end = payload.get('end_at', w.end_at)
    if new_end <= new_start:
        raise HTTPException(422, '天窗结束时间必须晚于开始时间')
    becomes_inactive = payload.get('active') is False and w.active
    shortened = (new_start > w.start_at + 1e-6) or (new_end < w.end_at - 1e-6)

    old_start, old_end, old_active = w.start_at, w.end_at, w.active
    for k, v in payload.items():
        setattr(w, k, v)

    affected = []
    if old_active and (becomes_inactive or shortened):
        # 找出落在该天窗上的已确认方案，逐带判断是否仍能完整容纳。
        confirmed_plans = db.scalars(select(MaintenancePlan).where(
            MaintenancePlan.status == 'confirmed')).unique().all()
        for plan in confirmed_plans:
            items = db.scalars(select(PlanItem).where(
                PlanItem.plan_id == plan.id, PlanItem.window_id == window_id)).all()
            if not items:
                continue
            broken = []
            if becomes_inactive:
                broken = items
            else:
                for it in items:
                    if it.start_at < new_start - 1e-6 or it.end_at > new_end + 1e-6:
                        broken.append(it)
            if broken:
                plan.status = 'invalidated'
                plan.invalidated_at = time.time()
                plan.invalidate_reason = (
                    '天窗「%s」%s，%d 条安排无法继续执行，整组方案失效；请重新排程。'
                    % (w.name, '已撤销' if becomes_inactive else '时长缩短', len(broken)))
                affected.append(plan)
    if payload:
        w.version += 1
    db.flush()
    return w, affected, {'old': {'start_at': old_start, 'end_at': old_end, 'active': old_active}}


# ---------------- 检修任务（从已批准试验登记） ----------------
def generate_tasks(db, data, user):
    job = db.get(Job, data.job_id)
    if not job or job.status != 'completed' or not job.approved:
        raise HTTPException(409, '只能从已完成且已批准的试验登记检修任务')
    rows = (job.result or {}).get('table', {}).get('rows', [])
    bands = [r for r in rows if str(r.get('band', '')).startswith('缺陷带')]
    if not bands:
        raise HTTPException(409, '该试验结果中没有可登记的缺陷带')
    created, skipped = [], []
    for idx, row in enumerate(bands, 1):
        exists = db.scalar(select(MaintenanceTask).where(
            MaintenanceTask.job_id == job.id, MaintenanceTask.band_index == idx))
        if exists:
            skipped.append(idx)
            continue
        t = MaintenanceTask(
            job_id=job.id, band_index=idx,
            title='%s · %s' % (job.name[:60], row.get('band', '缺陷带%d' % idx)),
            from_km=float(row['from_km']), to_km=float(row['to_km']),
            work_hours=float(data.default_hours), note='', active=True,
            created_by=user.id, created_at=time.time())
        db.add(t)
        created.append(t)
    db.flush()
    return created, skipped, len(bands)


def update_task(db, task_id, data):
    t = db.get(MaintenanceTask, task_id)
    if not t:
        raise HTTPException(404, '检修任务不存在')
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(t, k, v)
    db.flush()
    return t


# ---------------- 试排 ----------------
def _load_trial_inputs(db, data):
    tasks = list(db.scalars(select(MaintenanceTask).where(MaintenanceTask.active == True)))
    if data.task_ids:
        wanted = set(data.task_ids)
        tasks = [t for t in tasks if t.id in wanted]
        if len(tasks) != len(wanted):
            raise HTTPException(404, '部分检修任务不存在或已停用')
    windows = list(db.scalars(select(MaintenanceWindow).where(MaintenanceWindow.active == True)))
    if data.window_ids:
        wanted = set(data.window_ids)
        windows = [w for w in windows if w.id in wanted]
        if len(windows) != len(wanted):
            raise HTTPException(404, '部分天窗不存在或已撤销')
    crews = list(db.scalars(select(Crew).where(Crew.active == True)))
    if data.crew_ids:
        wanted = set(data.crew_ids)
        crews = [c for c in crews if c.id in wanted]
        if len(crews) != len(wanted):
            raise HTTPException(404, '部分班组不存在或已停用')
    return tasks, crews, windows


def create_trial(db, data, user):
    tasks, crews, windows = _load_trial_inputs(db, data)
    if not tasks:
        raise HTTPException(422, '没有可排程的检修任务，请先从已批准试验登记缺陷带')
    task_dto = [Task(id=t.id, title=t.title, from_km=t.from_km, to_km=t.to_km,
                     work_hours=t.work_hours) for t in tasks]
    crew_dto = [CrewDTO(id=c.id, name=c.name, version=c.version) for c in crews]
    window_dto = [Window(id=w.id, start_at=w.start_at, end_at=w.end_at, version=w.version)
                  for w in windows]
    trial = build_trial(task_dto, crew_dto, window_dto)

    plan = MaintenancePlan(name=data.name, status='draft', created_by=user.id,
                           created_at=time.time(), snapshot={
        'task_ids': [t.id for t in tasks], 'crew_ids': [c.id for c in crews],
        'window_ids': [w.id for w in windows],
        'window_versions': {str(w.id): w.version for w in windows},
        'crew_versions': {str(c.id): c.version for c in crews},
        'placed': len(trial.placements), 'unscheduled': len(trial.unscheduled),
    })
    db.add(plan)
    db.flush()
    for p in trial.placements:
        db.add(PlanItem(plan_id=plan.id, task_id=p.task_id, crew_id=p.crew_id,
                        window_id=p.window_id, start_at=p.start_at, end_at=p.end_at,
                        work_hours=p.work_hours, window_version=p.window_version,
                        crew_version=p.crew_version))
    for u in trial.unscheduled:
        db.add(UnscheduledReason(plan_id=plan.id, task_id=u['task_id'],
                                 code=u['code'], detail=u['detail'], created_at=time.time()))
    db.flush()
    return plan


def _plan_detail(db, plan):
    items = list(db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id).order_by(PlanItem.id)))
    reasons = list(db.scalars(select(UnscheduledReason).where(
        UnscheduledReason.plan_id == plan.id).order_by(UnscheduledReason.id)))
    return plan_dict(plan, [item_dict(i) for i in items], [reason_dict(r) for r in reasons])


# ---------------- 确认（整组占用，任一冲突全组不生效） ----------------
def confirm_plan(db, plan_id, confirm_key):
    _acquire_scheduler_lock(db)
    # 取锁后权威复查：同一 confirm_key 的重试直接回放首次回执，不重新占用资源。
    existing = db.get(ConfirmReceipt, confirm_key)
    if existing is not None:
        return existing.status_code, existing.response, True

    plan = db.get(MaintenancePlan, plan_id)
    if plan is None:
        raise HTTPException(404, '检修方案不存在')
    if plan.status == 'confirmed':
        # 客户端换了 key 重试同一方案：方案已确认，回放当前安排，不重复占用。
        return 200, _plan_detail(db, plan), False
    if plan.status == 'invalidated':
        return _store_receipt(db, confirm_key, plan_id, 409,
                              {'detail': '该方案已因资源变更失效，请重新排程。'})
    if plan.status != 'draft':
        raise HTTPException(409, '当前方案状态不允许确认')

    items = list(db.scalars(select(PlanItem).where(PlanItem.plan_id == plan.id)))
    if not items:
        return _store_receipt(db, confirm_key, plan_id, 409,
                              {'detail': '试排没有任何可确认的安排，请先查看未排原因并调整资源。'})

    # 2) 旧试排裁决：资源版本变化即拒绝确认。
    version_error = _check_versions(db, items)
    if version_error:
        return _store_receipt(db, confirm_key, plan_id, 409, {'detail': version_error})

    # 3) 与其他已确认安排做整组冲突校验（班组时间重叠 / 里程时间重叠）。
    conflict = _check_confirmed_conflicts(db, items)
    if conflict:
        return _store_receipt(db, confirm_key, plan_id, 409, {'detail': conflict})

    # 4) 全部通过：整组占用，一次提交。
    plan.status = 'confirmed'
    plan.confirmed_at = time.time()
    db.flush()
    return _store_receipt(db, confirm_key, plan_id, 200, _plan_detail(db, plan))


def _store_receipt(db, confirm_key, plan_id, status_code, response):
    db.add(ConfirmReceipt(confirm_key=confirm_key, plan_id=plan_id,
                          status_code=status_code, response=response, created_at=time.time()))
    try:
        db.flush()
    except IntegrityError:
        # 并发下另一请求已先写入同一 confirm_key：回放其回执，绝不重复占用。
        db.rollback()
        raced = db.get(ConfirmReceipt, confirm_key)
        if raced is not None:
            return raced.status_code, raced.response, True
        raise HTTPException(409, '确认请求并发冲突，请用原确认键重试')
    return status_code, response, False


def _check_versions(db, items):
    window_ids = {i.window_id for i in items}
    crew_ids = {i.crew_id for i in items}
    windows = {w.id: w for w in db.scalars(select(MaintenanceWindow).where(
        MaintenanceWindow.id.in_(window_ids)))}
    crews = {c.id: c for c in db.scalars(select(Crew).where(Crew.id.in_(crew_ids)))}
    for i in items:
        w = windows.get(i.window_id)
        if not w or not w.active:
            return '试排使用的天窗已撤销，旧试排不能确认，请重新排程。'
        if w.version != i.window_version:
            return '天窗「%s」在试排后发生变更，旧试排拒绝确认，请重新排程。' % w.name
        if i.start_at < w.start_at - 1e-6 or i.end_at > w.end_at + 1e-6:
            return '安排已不能完整落在天窗「%s」内，请重新排程。' % w.name
        c = crews.get(i.crew_id)
        if not c or not c.active:
            return '试排使用的班组已停用，旧试排不能确认，请重新排程。'
        if c.version != i.crew_version:
            return '班组「%s」在试排后发生变更，旧试排拒绝确认，请重新排程。' % c.name
    return None


def _check_confirmed_conflicts(db, items):
    task_ids = {i.task_id for i in items}
    tasks = {t.id: t for t in db.scalars(select(MaintenanceTask).where(
        MaintenanceTask.id.in_(task_ids)))}
    # 方案内一致性（工时、班组、里程）。
    task_dto = {tid: Task(id=t.id, title=t.title, from_km=t.from_km, to_km=t.to_km,
                          work_hours=t.work_hours) for tid, t in tasks.items()}
    placeholders = [scheduling.Placement(
        task_id=i.task_id, crew_id=i.crew_id, window_id=i.window_id,
        start_at=i.start_at, end_at=i.end_at, work_hours=i.work_hours,
        window_version=i.window_version, crew_version=i.crew_version) for i in items]
    internal = scheduling.validate_placements(placeholders, task_dto)
    if internal:
        return internal
    # 与其他已确认方案的安排比对。
    other = list(db.scalars(select(PlanItem).join(
        MaintenancePlan, MaintenancePlan.id == PlanItem.plan_id).where(
        MaintenancePlan.status == 'confirmed', PlanItem.plan_id != items[0].plan_id)))
    other_tasks = {t.id: t for t in db.scalars(select(MaintenanceTask).where(
        MaintenanceTask.id.in_({o.task_id for o in other})))} if other else {}
    for mine in items:
        mine_task = tasks.get(mine.task_id)
        for o in other:
            if not (mine.start_at < o.end_at and o.start_at < mine.end_at):
                continue
            if mine.crew_id == o.crew_id:
                crew = db.get(Crew, mine.crew_id)
                return '班组「%s」在该时段已有确认安排，并发确认不能超订。' % (crew.name if crew else mine.crew_id)
            other_task = other_tasks.get(o.task_id)
            if mine_task and other_task and (
                    mine_task.from_km < other_task.to_km and other_task.from_km < mine_task.to_km):
                return '里程 %.3f~%.3f km 与已确认安排的封锁区段时间重叠。' % (
                    mine_task.from_km, mine_task.to_km)
    return None


# ---------------- 开工（失效方案拒绝开工） ----------------
def start_plan(db, plan_id):
    plan = db.get(MaintenancePlan, plan_id)
    if not plan:
        raise HTTPException(404, '检修方案不存在')
    if plan.status != 'confirmed':
        raise HTTPException(409, '仅已确认且未失效的方案允许开工；当前状态：%s' % plan.status)
    # 开工前再次校验天窗仍然覆盖安排（天窗缩短后确认方案应已失效，这里做兜底）。
    items = db.scalars(select(PlanItem).where(PlanItem.plan_id == plan_id)).all()
    for i in items:
        w = db.get(MaintenanceWindow, i.window_id)
        if not w or not w.active or i.start_at < w.start_at - 1e-6 or i.end_at > w.end_at + 1e-6:
            plan.status = 'invalidated'
            plan.invalidated_at = time.time()
            plan.invalidate_reason = '开工前复核发现天窗已不能覆盖安排，拒绝开工。'
            db.flush()
            raise HTTPException(409, '天窗已变更，该方案失效并拒绝开工，请重新排程。')
    return plan
