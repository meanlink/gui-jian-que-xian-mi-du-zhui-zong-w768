from pydantic import BaseModel, Field, field_validator


class CrewInput(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    skill: str = Field(default='', max_length=160)
    active: bool = True


class CrewPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    skill: str | None = Field(default=None, max_length=160)
    active: bool | None = None


class WindowInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    start_at: float = Field(gt=0)
    end_at: float = Field(gt=0)
    line_direction: str = Field(default='南环上行', max_length=80)
    active: bool = True

    @field_validator('end_at')
    @classmethod
    def _order(cls, value, info):
        start = info.data.get('start_at')
        if start is not None and value <= start:
            raise ValueError('天窗结束时间必须晚于开始时间')
        return value


class WindowPatch(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=160)
    start_at: float | None = Field(default=None, gt=0)
    end_at: float | None = Field(default=None, gt=0)
    active: bool | None = None
    line_direction: str | None = Field(default=None, max_length=80)


class TaskGenerateInput(BaseModel):
    job_id: int
    default_hours: float = Field(default=2.0, ge=0.5, le=24)


class TaskPatch(BaseModel):
    work_hours: float | None = Field(default=None, ge=0.5, le=24)
    title: str | None = Field(default=None, min_length=2, max_length=160)
    note: str | None = Field(default=None, max_length=300)
    active: bool | None = None


class TrialInput(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    task_ids: list[int] = Field(default_factory=list)
    window_ids: list[int] = Field(default_factory=list)
    crew_ids: list[int] = Field(default_factory=list)


class ConfirmInput(BaseModel):
    # 前端在试排后生成并持久保存；回执丢失重试沿用同一 key，保证幂等不重复占用。
    confirm_key: str = Field(min_length=8, max_length=80)
