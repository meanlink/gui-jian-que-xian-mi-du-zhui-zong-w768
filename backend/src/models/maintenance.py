from sqlalchemy import String, Double, ForeignKey, Boolean, Integer, UniqueConstraint, JSON
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base


class Crew(Base):
    """封锁检修班组（资源）。"""
    __tablename__ = 'crews'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    skill: Mapped[str] = mapped_column(String(160), default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Double)


class MaintenanceWindow(Base):
    """封锁天窗（资源）：一段可安排上道作业的连续时间窗，作业不跨天窗。"""
    __tablename__ = 'maintenance_windows'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    start_at: Mapped[float] = mapped_column(Double, index=True)
    end_at: Mapped[float] = mapped_column(Double, index=True)
    line_direction: Mapped[str] = mapped_column(String(80), default='南环上行')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # 每次缩短/撤销都会递增；试排单保存当时的版本，确认时版本不一致即拒绝（旧试排拒绝确认）。
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[float] = mapped_column(Double)


class MaintenanceTask(Base):
    """由已批准试验的缺陷带登记生成的封锁检修任务，一带一条。"""
    __tablename__ = 'maintenance_tasks'
    __table_args__ = (UniqueConstraint('job_id', 'band_index', name='uq_task_job_band'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey('jobs.id'))
    band_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(160))
    from_km: Mapped[float] = mapped_column(Double)
    to_km: Mapped[float] = mapped_column(Double)
    work_hours: Mapped[float] = mapped_column(Double)
    note: Mapped[str] = mapped_column(String(300), default='')
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)


class MaintenancePlan(Base):
    """检修方案：一次试排生成一组整组生效的安排，确认时整组占用资源。"""
    __tablename__ = 'maintenance_plans'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default='draft')  # draft/confirmed/invalidated
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    confirmed_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    invalidated_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    invalidate_reason: Mapped[str] = mapped_column(String(300), default='')
    # 试排时的资源/任务快照摘要，用于解释旧试排为何失效。
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)


class PlanItem(Base):
    """方案内单带安排：任务 + 班组 + 天窗 + 具体开工时间，作业不拆工。"""
    __tablename__ = 'plan_items'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('maintenance_plans.id'), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('maintenance_tasks.id'))
    crew_id: Mapped[int] = mapped_column(ForeignKey('crews.id'))
    window_id: Mapped[int] = mapped_column(ForeignKey('maintenance_windows.id'))
    start_at: Mapped[float] = mapped_column(Double)
    end_at: Mapped[float] = mapped_column(Double)
    work_hours: Mapped[float] = mapped_column(Double)
    # 试排时记录资源版本，确认时比对。
    window_version: Mapped[int] = mapped_column(Integer, default=1)
    crew_version: Mapped[int] = mapped_column(Integer, default=1)


class UnscheduledReason(Base):
    """试排时未能排入的任务及原因，供页面查看“未排原因”。"""
    __tablename__ = 'unscheduled_reasons'
    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('maintenance_plans.id'), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey('maintenance_tasks.id'))
    code: Mapped[str] = mapped_column(String(40))
    detail: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[float] = mapped_column(Double)


class ConfirmReceipt(Base):
    """确认回执：以 confirm_key 幂等，回执丢失重试仍走原安排，不重复占用资源。"""
    __tablename__ = 'confirm_receipts'
    confirm_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey('maintenance_plans.id'))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Double)
