"""封锁检修试排引擎（纯函数，不依赖数据库，便于独立验证）。

口径：
- 同一班组时间重叠的安排不能同时开工；
- 里程区间重叠的安排不能同时开工（不同班组也不行，同一封锁区段无法并行上道）；
- 作业必须完整落在单个天窗内，跨天窗不拆工；
- 每条缺陷带一个任务，登记工时（小时），按分钟对齐铺排。
"""
from __future__ import annotations
from dataclasses import dataclass, field

STEP_SECONDS = 300  # 开工时间按 5 分钟对齐

# 未排原因代码
NO_WINDOW = 'no_window'            # 没有任何可用天窗
WINDOW_TOO_SHORT = 'window_too_short'
CREW_BUSY = 'crew_busy'
MILEAGE_BUSY = 'mileage_busy'
NO_FIT = 'no_fit'                  # 有天窗但受班组/里程双重占用，找不到任何落点
RESOURCE_MISSING = 'resource_missing'


@dataclass
class Window:
    id: int
    start_at: float
    end_at: float
    version: int = 1
    active: bool = True


@dataclass
class Crew:
    id: int
    name: str
    version: int = 1
    active: bool = True


@dataclass
class Task:
    id: int
    title: str
    from_km: float
    to_km: float
    work_hours: float


@dataclass
class Placement:
    task_id: int
    crew_id: int
    window_id: int
    start_at: float
    end_at: float
    work_hours: float
    window_version: int
    crew_version: int


@dataclass
class Assignment:
    placement: Placement | None
    reason_code: str = ''
    reason_detail: str = ''


@dataclass
class TrialResult:
    placements: list[Placement] = field(default_factory=list)
    unscheduled: list[dict] = field(default_factory=list)


def _overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def _mileage_overlap(t: Task, other: Task) -> bool:
    return t.from_km < other.to_km and other.from_km < t.to_km


def _candidate_slots(window: Window, duration: float):
    """生成天窗内所有按 STEP 对齐、能完整容纳作业的开工时刻。"""
    start = window.start_at
    last_start = window.end_at - duration
    slot = start
    while slot <= last_start + 1e-6:
        yield slot
        slot += STEP_SECONDS


def _slot_blocked(slot_start: float, slot_end: float, task: Task, crew_id: int,
                  tasks_by_id: dict, placements: list[Placement]) -> str | None:
    """返回阻塞原因代码；None 表示该落点可用。"""
    crew_blocked = False
    mileage_blocked = False
    for p in placements:
        if not _overlap(slot_start, slot_end, p.start_at, p.end_at):
            continue
        if p.crew_id == crew_id:
            crew_blocked = True
        other = tasks_by_id.get(p.task_id)
        if other and _mileage_overlap(task, other):
            mileage_blocked = True
        if crew_blocked and mileage_blocked:
            break
    if mileage_blocked:
        return MILEAGE_BUSY
    if crew_blocked:
        return CREW_BUSY
    return None


def assign_task(task: Task, crews: list[Crew], windows: list[Window],
                tasks_by_id: dict, placements: list[Placement]) -> Assignment:
    """为单个任务寻找最早可行落点；同一班组及重叠里程不得时间重叠，不拆工。"""
    duration = task.work_hours * 3600
    usable = [w for w in windows if w.active and (w.end_at - w.start_at) >= duration - 1e-6]
    if not usable:
        any_window = [w for w in windows if w.active]
        if not any_window:
            return Assignment(None, NO_WINDOW, '当前没有可用天窗，请先维护封锁天窗。')
        return Assignment(None, WINDOW_TOO_SHORT,
                          '所有可用天窗都短于该带登记工时 %.2f 小时，作业不跨天窗不拆工。' % task.work_hours)
    active_crews = [c for c in crews if c.active]
    if not active_crews:
        return Assignment(None, RESOURCE_MISSING, '没有可用班组，请先维护检修班组。')
    seen_codes: set[str] = set()
    # 天窗按开始时间排序，保证“先早窗后晚窗”；班组按已承担安排数从少到多，避免单班组堆积。
    ordered_windows = sorted(usable, key=lambda w: w.start_at)
    crew_load = {c.id: 0 for c in active_crews}
    for p in placements:
        crew_load[p.crew_id] = crew_load.get(p.crew_id, 0) + 1
    for window in ordered_windows:
        for crew in sorted(active_crews, key=lambda c: (crew_load.get(c.id, 0), c.id)):
            for slot in _candidate_slots(window, duration):
                end = slot + duration
                code = _slot_blocked(slot, end, task, crew.id, tasks_by_id, placements)
                if code is None:
                    return Assignment(Placement(
                        task_id=task.id, crew_id=crew.id, window_id=window.id,
                        start_at=slot, end_at=end, work_hours=task.work_hours,
                        window_version=window.version, crew_version=crew.version,
                    ))
                seen_codes.add(code)
    # 未排原因取最具行动指导性的一个。
    if MILEAGE_BUSY in seen_codes:
        return Assignment(None, MILEAGE_BUSY, '重叠里程区段在所有可行天窗内均已被占用，需追加天窗或调整已确认安排。')
    if CREW_BUSY in seen_codes:
        return Assignment(None, CREW_BUSY, '班组在所有可行天窗内均已排满，需追加班组或天窗。')
    return Assignment(None, NO_FIT, '受班组与里程双重占用限制，找不到完整落在单天窗内的落点。')


def build_trial(tasks: list[Task], crews: list[Crew], windows: list[Window]) -> TrialResult:
    """整组试排：工时最长、里程最靠前的任务优先，保证难排的任务先占资源。"""
    result = TrialResult()
    tasks_by_id = {t.id: t for t in tasks}
    ordered = sorted(tasks, key=lambda t: (-t.work_hours, t.from_km, t.id))
    for task in ordered:
        assignment = assign_task(task, crews, windows, tasks_by_id, result.placements)
        if assignment.placement is not None:
            result.placements.append(assignment.placement)
        else:
            result.unscheduled.append({
                'task_id': task.id, 'code': assignment.reason_code, 'detail': assignment.reason_detail,
            })
    return result


def validate_placements(placements: list[Placement], tasks_by_id: dict) -> str | None:
    """确认前对一组安排做整组冲突校验，任一冲突即返回说明（整组不生效）。"""
    for i, p in enumerate(placements):
        task = tasks_by_id.get(p.task_id)
        if task is None:
            return '安排引用的检修任务已不存在。'
        if abs((p.end_at - p.start_at) - task.work_hours * 3600) > 1e-3:
            return '安排工时与任务登记工时不一致，可能已被改动，请重新试排。'
        for q in placements[:i]:
            if not _overlap(p.start_at, p.end_at, q.start_at, q.end_at):
                continue
            if p.crew_id == q.crew_id:
                return '班组 %s 存在时间重叠的安排。' % p.crew_id
            other = tasks_by_id.get(q.task_id)
            if other and _mileage_overlap(task, other):
                return '里程 %.3f~%.3f km 与其他安排在时间上重叠。' % (task.from_km, task.to_km)
    return None
