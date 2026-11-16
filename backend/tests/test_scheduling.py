"""排程引擎纯逻辑单测，仅依赖标准库：python3 backend/tests/test_scheduling.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.services.scheduling import (  # noqa: E402
    Window, Crew, Task, build_trial, validate_placements, assign_task,
    NO_WINDOW, WINDOW_TOO_SHORT, MILEAGE_BUSY, CREW_BUSY, RESOURCE_MISSING,
)

H = 3600


class SchedulingTests(unittest.TestCase):
    def setUp(self):
        self.crews = [Crew(id=1, name='甲班'), Crew(id=2, name='乙班')]
        self.windows = [
            Window(id=10, start_at=0 * H, end_at=6 * H),
            Window(id=11, start_at=24 * H, end_at=30 * H),
        ]

    def test_basic_packing_and_no_split(self):
        tasks = [
            Task(1, '带1', 1.0, 1.2, 2),
            Task(2, '带2', 5.0, 5.2, 2),
            Task(3, '带3', 9.0, 9.2, 2),
        ]
        trial = build_trial(tasks, self.crews, self.windows)
        self.assertEqual(len(trial.placements), 3)
        self.assertEqual(trial.unscheduled, [])
        for p in trial.placements:
            w = next(x for x in self.windows if x.id == p.window_id)
            # 完整落在单个天窗内
            self.assertGreaterEqual(p.start_at, w.start_at)
            self.assertLessEqual(p.end_at, w.end_at)
            self.assertAlmostEqual(p.end_at - p.start_at, p.work_hours * H)

    def test_same_crew_no_overlap(self):
        # 一个班组、四条同里程任务；6 小时天窗只能放 3 条 2 小时任务（0-2,2-4,4-6），第四条排次日
        tasks = [Task(i, '带%d' % i, 1.0, 1.2, 2) for i in (1, 2, 3, 4)]
        trial = build_trial(tasks, [Crew(id=1, name='独苗')], self.windows)
        self.assertEqual(len(trial.placements), 4)
        first_window = [p for p in trial.placements if p.window_id == 10]
        self.assertEqual(len(first_window), 3)
        first_window.sort(key=lambda p: p.start_at)
        self.assertEqual(first_window[0].end_at, first_window[1].start_at)
        self.assertEqual(first_window[1].end_at, first_window[2].start_at)
        # 班组内任意两条不得时间重叠
        ps = sorted(trial.placements, key=lambda p: p.start_at)
        for a, b in zip(ps, ps[1:]):
            self.assertFalse(a.start_at < b.end_at and b.start_at < a.end_at)
        self.assertEqual([p.window_id for p in ps if p.window_id == 11], [11])

    def test_mileage_overlap_blocks_parallel_crews(self):
        # 里程重叠的两条任务即便班组不同也不能并行；错峰后可以
        tasks = [Task(1, '带1', 1.0, 2.0, 2), Task(2, '带2', 1.5, 2.5, 2)]
        trial = build_trial(tasks, self.crews, self.windows)
        self.assertEqual(len(trial.placements), 2)
        ps = sorted(trial.placements, key=lambda p: p.start_at)
        self.assertGreaterEqual(ps[1].start_at, ps[0].end_at - 1e-6)

    def test_non_overlapping_mileage_runs_parallel(self):
        tasks = [Task(1, '带1', 1.0, 1.2, 2), Task(2, '带2', 8.0, 8.2, 2)]
        trial = build_trial(tasks, self.crews, self.windows)
        first = [p for p in trial.placements if p.window_id == 10]
        self.assertEqual(len(first), 2)
        # 两个班组、同一时刻并行
        self.assertEqual(len({p.crew_id for p in first}), 2)
        self.assertEqual({p.start_at for p in first}, {0.0})

    def test_window_too_short_unscheduled_reason(self):
        tasks = [Task(1, '带1', 1.0, 1.2, 8)]  # 8 小时 > 所有 6 小时天窗
        trial = build_trial(tasks, self.crews, self.windows)
        self.assertEqual(trial.placements, [])
        self.assertEqual(trial.unscheduled[0]['code'], WINDOW_TOO_SHORT)

    def test_no_window_reason(self):
        tasks = [Task(1, '带1', 1.0, 1.2, 2)]
        trial = build_trial(tasks, self.crews, [])
        self.assertEqual(trial.unscheduled[0]['code'], NO_WINDOW)

    def test_no_crew_reason(self):
        tasks = [Task(1, '带1', 1.0, 1.2, 2)]
        trial = build_trial(tasks, [], self.windows)
        self.assertEqual(trial.unscheduled[0]['code'], RESOURCE_MISSING)

    def test_crew_busy_reason(self):
        # 单班组、单天窗 6 小时，四条 2 小时、里程互不重叠任务：三条排进，
        # 第四条只受班组产能限制（里程不冲突），原因应为班组占用
        tasks = [Task(i, '带%d' % i, float(i * 10), float(i * 10) + 0.2, 2) for i in (1, 2, 3, 4)]
        trial = build_trial(tasks, [Crew(1, '独苗')], [self.windows[0]])
        self.assertEqual(len(trial.placements), 3)
        self.assertEqual(trial.unscheduled[0]['code'], CREW_BUSY)

    def test_mileage_busy_reason(self):
        # 两班组、单天窗、里程全重叠：错峰只能放 3 条，第 4 条里程占用排不进
        tasks = [Task(i, '带%d' % i, 1.0, 1.5, 2) for i in range(1, 5)]
        trial = build_trial(tasks, self.crews, [self.windows[0]])
        self.assertEqual(len(trial.placements), 3)
        self.assertEqual(trial.unscheduled[0]['code'], MILEAGE_BUSY)

    def test_validate_detects_crew_and_mileage_conflict(self):
        t1 = Task(1, '带1', 1.0, 1.2, 2)
        t2 = Task(2, '带2', 5.0, 5.2, 2)
        t3 = Task(3, '带3', 1.1, 1.3, 2)
        base = dict(window_id=10, window_version=1, crew_version=1)
        good = validate_placements([], {1: t1})
        self.assertIsNone(good)
        crew_conflict = validate_placements([
            type('P', (), dict(task_id=1, crew_id=1, start_at=0, end_at=2 * H, **base))(),
            type('P', (), dict(task_id=2, crew_id=1, start_at=H, end_at=3 * H, **base))(),
        ], {1: t1, 2: t2})
        self.assertIn('班组', crew_conflict)
        mileage_conflict = validate_placements([
            type('P', (), dict(task_id=1, crew_id=1, start_at=0, end_at=2 * H, **base))(),
            type('P', (), dict(task_id=3, crew_id=2, start_at=H, end_at=3 * H, **base))(),
        ], {1: t1, 3: t3})
        self.assertIn('里程', mileage_conflict)

    def test_assign_prefers_early_window_and_frees_crew(self):
        tasks = [Task(1, '带1', 1.0, 1.2, 2), Task(2, '带2', 8.0, 8.2, 2)]
        trial = build_trial(tasks, self.crews, self.windows)
        self.assertTrue(all(p.window_id == 10 for p in trial.placements))


if __name__ == '__main__':
    unittest.main(verbosity=2)
