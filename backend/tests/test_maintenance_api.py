"""封锁检修全链路集成测试（SQLite + FastAPI TestClient，标准 unittest）。

运行：cd backend && python3 tests/test_maintenance_api.py
Postgres 专属的咨询锁在 SQLite 下退化为无操作，冲突与版本裁决逻辑仍完整验证。
"""
import os
import sys
import time
import tempfile
import unittest

_DB = os.path.join(tempfile.gettempdir(), 'maint_test.db')
if os.path.exists(_DB):
    os.remove(_DB)
os.environ.setdefault('DATABASE_URL', 'sqlite:///%s' % _DB)
os.environ.setdefault('REDIS_URL', 'redis://127.0.0.1:6399/0')
os.environ.setdefault('JWT_SECRET', 'test-secret')
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fastapi.testclient import TestClient  # noqa: E402
from src.main import app  # noqa: E402
from src.config.database import Session, Base, engine  # noqa: E402
from src.models import User, Job  # noqa: E402
from src.utils.security import hash_password, token_for  # noqa: E402

H = 3600


class MaintenanceApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with Session() as db:
            admin = User(email='admin@demo.local', name='负责人', role='admin',
                         password=hash_password('Demo123!'))
            analyst = User(email='analyst@demo.local', name='分析员', role='analyst',
                           password=hash_password('Demo123!'))
            viewer = User(email='viewer@demo.local', name='观察员', role='viewer',
                          password=hash_password('Demo123!'))
            db.add_all([admin, analyst, viewer])
            db.flush()
            job = Job(name='基线试验', dataset_id=1, params={}, status='completed', approved=True,
                      created_by=admin.id, created_at=time.time(), result={'table': {'rows': [
                          {'band': '缺陷带1', 'from_km': 12.1, 'to_km': 12.6},
                          {'band': '缺陷带2', 'from_km': 48.0, 'to_km': 48.5},
                          {'band': '缺陷带3', 'from_km': 93.6, 'to_km': 93.9},
                          {'band': '孤立/稀疏点', 'from_km': 1, 'to_km': 119},
                      ]}})
            db.add(job)
            db.commit()
            cls.admin_id, cls.analyst_id, cls.viewer_id, cls.job_id = admin.id, analyst.id, viewer.id, job.id
        cls.client = TestClient(app)
        cls.tokens = {}
        for role, email in [('admin', 'admin@demo.local'), ('analyst', 'analyst@demo.local'),
                            ('viewer', 'viewer@demo.local')]:
            r = cls.client.post('/auth/login', json={'email': email, 'password': 'Demo123!'})
            cls.tokens[role] = r.json()['access_token']

    def auth(self, role):
        return {'Authorization': 'Bearer ' + self.tokens[role]}

    def ensure_tasks(self):
        tasks = self.client.get('/maintenance/tasks', headers=self.auth('admin')).json()
        if not tasks:
            self.client.post('/maintenance/tasks/generate',
                             json={'job_id': self.job_id, 'default_hours': 1.5},
                             headers=self.auth('analyst'))
            tasks = self.client.get('/maintenance/tasks', headers=self.auth('admin')).json()
        return tasks

    def ensure_two_crews(self):
        crews = self.client.get('/maintenance/crews', headers=self.auth('admin')).json()
        active = [c for c in crews if c['active']]
        for n in ('隔离甲班', '隔离乙班'):
            if not any(c['name'] == n for c in active):
                r = self.client.post('/maintenance/crews',
                                      json={'name': n}, headers=self.auth('admin'))
                if r.status_code == 201:
                    active.append(r.json())
        return active[:2]

    # ---------- 资源维护与权限 ----------
    def test_01_resource_permissions(self):
        r = self.client.post('/maintenance/crews', json={'name': '甲班'}, headers=self.auth('analyst'))
        self.assertEqual(r.status_code, 403)
        r = self.client.post('/maintenance/crews', json={'name': '甲班'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 201)
        r = self.client.post('/maintenance/crews', json={'name': '乙班'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 201)
        r = self.client.post('/maintenance/crews', json={'name': '甲班'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 409)
        now = time.time()
        r = self.client.post('/maintenance/windows', json={
            'name': '周三夜间窗', 'start_at': now + H, 'end_at': now + 7 * H},
            headers=self.auth('admin'))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['version'], 1)
        r = self.client.post('/maintenance/windows', json={
            'name': '周四夜间窗', 'start_at': now + 25 * H, 'end_at': now + 31 * H},
            headers=self.auth('admin'))
        self.assertEqual(r.status_code, 201)
        r = self.client.post('/maintenance/windows', json={
            'name': '坏窗', 'start_at': now + 10 * H, 'end_at': now + 9 * H},
            headers=self.auth('admin'))
        self.assertEqual(r.status_code, 422)
        # 观察员只读
        r = self.client.get('/maintenance/crews', headers=self.auth('viewer'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()), 2)

    # ---------- 从已批准试验登记任务 ----------
    def test_02_generate_tasks_idempotent(self):
        r = self.client.post('/maintenance/tasks/generate',
                             json={'job_id': self.job_id, 'default_hours': 2},
                             headers=self.auth('analyst'))
        self.assertEqual(r.status_code, 201)
        self.assertEqual(len(r.json()['created']), 3)  # 孤立点不登记
        tasks = self.client.get('/maintenance/tasks', headers=self.auth('admin')).json()
        self.assertEqual(len(tasks), 3)
        # 重复登记幂等跳过
        r = self.client.post('/maintenance/tasks/generate',
                             json={'job_id': self.job_id}, headers=self.auth('analyst'))
        self.assertEqual(r.json()['created'], [])
        self.assertEqual(len(r.json()['skipped_band_index']), 3)

    # ---------- 试排 ----------
    def test_03_trial_with_unscheduled_reasons(self):
        r = self.client.post('/maintenance/plans/trial',
                             json={'name': '首轮试排'}, headers=self.auth('analyst'))
        self.assertEqual(r.status_code, 201)
        plan = r.json()
        self.assertEqual(plan['status'], 'draft')
        self.assertEqual(len(plan['items']), 3)
        for item in plan['items']:
            self.assertAlmostEqual(item['end_at'] - item['start_at'], 2 * H)
        # 不存在的天窗 → 404
        bad = self.client.post('/maintenance/plans/trial',
                               json={'name': '坏试排', 'window_ids': [999]},
                               headers=self.auth('analyst'))
        self.assertEqual(bad.status_code, 404)

    # ---------- 确认：整组占用、幂等重试不重复占用 ----------
    def test_04_confirm_idempotent_and_no_overbook(self):
        plan = self.client.post('/maintenance/plans/trial',
                                json={'name': '待确认A'}, headers=self.auth('analyst')).json()
        key = 'key-plan-%d-0001' % plan['id']
        r = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                             json={'confirm_key': key}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'confirmed')
        # 回执丢失后用同一 key 重试：回放首次结果，不产生第二组确认安排
        r2 = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                              json={'confirm_key': key}, headers=self.auth('admin'))
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()['id'], plan['id'])
        confirmed = [p for p in self.client.get('/maintenance/plans',
                     headers=self.auth('admin')).json() if p['status'] == 'confirmed']
        self.assertEqual(len(confirmed), 1)
        # 分析员无权确认
        r3 = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                              json={'confirm_key': 'other-key'}, headers=self.auth('analyst'))
        self.assertEqual(r3.status_code, 403)
        self.plan_a = plan['id']

    def test_05_conflicting_second_plan_whole_group_rejected(self):
        # 限定单班组重排全部任务：该班组在首个时段已被确认方案占用，确认时整组拦截
        tasks = self.client.get('/maintenance/tasks', headers=self.auth('admin')).json()
        crews = self.client.get('/maintenance/crews', headers=self.auth('admin')).json()
        plan = self.client.post('/maintenance/plans/trial', json={
            'name': '冲突试排', 'task_ids': [t['id'] for t in tasks],
            'crew_ids': [crews[0]['id']]}, headers=self.auth('analyst')).json()
        r = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                             json={'confirm_key': 'conflict-key-0001'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 409)
        # 失败也有回执；同 key 重试回放同一 409，方案仍是 draft
        r2 = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                              json={'confirm_key': 'conflict-key-0001'}, headers=self.auth('admin'))
        self.assertEqual(r2.status_code, 409)
        fresh = next(p for p in self.client.get('/maintenance/plans',
                     headers=self.auth('admin')).json() if p['id'] == plan['id'])
        self.assertEqual(fresh['status'], 'draft')

    # ---------- 资源变更与确认并发：旧试排拒绝确认 ----------
    def test_06_stale_trial_rejected_after_window_change(self):
        # 独立天窗，避免与前序已确认方案争用，保证裁决可重复
        now = time.time()
        w = self.client.post('/maintenance/windows', json={
            'name': '专项窗', 'start_at': now + 48 * H, 'end_at': now + 54 * H},
            headers=self.auth('admin')).json()
        plan = self.client.post('/maintenance/plans/trial', json={
            'name': '旧试排', 'window_ids': [w['id']]}, headers=self.auth('analyst')).json()
        self.assertEqual(len(plan['items']), 3)
        # 天窗缩短 2 小时（先完成者裁决：资源变更先落库，版本号递增）
        r = self.client.patch('/maintenance/windows/%d' % w['id'],
                              json={'end_at': w['end_at'] - 2 * H}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['window']['version'], w['version'] + 1)
        r = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                             json={'confirm_key': 'stale-key-0001'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 409)
        self.assertIn('重新排程', r.json()['detail'])
        # 重新试排（仍只用专项窗：缩短后 4 小时、两班组并行可容纳三条 2 小时任务）后可以确认
        replan = self.client.post('/maintenance/plans/trial', json={
            'name': '重排方案', 'window_ids': [w['id']]}, headers=self.auth('analyst')).json()
        self.assertEqual(len(replan['items']), 3)
        r = self.client.post('/maintenance/plans/%d/confirm' % replan['id'],
                             json={'confirm_key': 'replan-key-0001'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 200)

    # ---------- 天窗撤销：受影响方案失效、拒绝开工，其他安排保留 ----------
    def test_07_window_withdraw_invalidates_affected_only(self):
        plans = self.client.get('/maintenance/plans', headers=self.auth('admin')).json()
        confirmed = [p for p in plans if p['status'] == 'confirmed']
        self.assertGreaterEqual(len(confirmed), 1)
        target = confirmed[0]
        window_id = target['items'][0]['window_id']
        r = self.client.patch('/maintenance/windows/%d' % window_id,
                              json={'active': False}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 200)
        affected_ids = {p['id'] for p in r.json()['affected_plans']}
        self.assertIn(target['id'], affected_ids)
        # 受影响方案失效且拒绝开工
        r = self.client.post('/maintenance/plans/%d/start' % target['id'],
                             headers=self.auth('admin'))
        self.assertEqual(r.status_code, 409)
        detail = next(p for p in self.client.get('/maintenance/plans',
                      headers=self.auth('admin')).json() if p['id'] == target['id'])
        self.assertEqual(detail['status'], 'invalidated')
        self.assertTrue(detail['invalidate_reason'])
        # 未受影响的已确认安排保留
        others = [p for p in confirmed if p['id'] not in affected_ids]
        for p in others:
            fresh = next(x for x in self.client.get('/maintenance/plans',
                         headers=self.auth('admin')).json() if x['id'] == p['id'])
            self.assertEqual(fresh['status'], 'confirmed')

    def test_08_confirmed_plan_can_start(self):
        plans = self.client.get('/maintenance/plans', headers=self.auth('admin')).json()
        ok = next((p for p in plans if p['status'] == 'confirmed'), None)
        if ok is None:
            self.skipTest('没有可开工的确认方案')
        r = self.client.post('/maintenance/plans/%d/start' % ok['id'],
                             headers=self.auth('admin'))
        self.assertEqual(r.status_code, 200)

    def test_09_crew_deactivation_makes_stale_trial(self):
        self.ensure_tasks()
        self.ensure_two_crews()
        now = time.time()
        w = self.client.post('/maintenance/windows', json={
            'name': '班组裁定窗', 'start_at': now + 72 * H, 'end_at': now + 78 * H},
            headers=self.auth('admin')).json()
        plan = self.client.post('/maintenance/plans/trial', json={
            'name': '班组试排', 'window_ids': [w['id']]}, headers=self.auth('analyst')).json()
        crew_id = plan['items'][0]['crew_id']
        r = self.client.patch('/maintenance/crews/%d' % crew_id,
                              json={'active': False}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 200)
        r = self.client.post('/maintenance/plans/%d/confirm' % plan['id'],
                             json={'confirm_key': 'crew-stale-0001'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 409)
        self.assertIn('班组', r.json()['detail'])

    def test_10_mileage_conflict_across_crews_rejected(self):
        # 两条里程重叠的任务，两个不同班组也不得同时段并行：第二个方案确认被拦截
        from src.models import MaintenanceTask
        now = time.time()
        w = self.client.post('/maintenance/windows', json={
            'name': '里程裁定窗', 'start_at': now + 96 * H, 'end_at': now + 102 * H},
            headers=self.auth('admin')).json()
        tasks = self.ensure_tasks()
        crews = self.ensure_two_crews()
        t1 = tasks[0]
        with Session() as db:
            overlap = MaintenanceTask(
                job_id=self.job_id, band_index=99, title='里程重叠邻接任务',
                from_km=t1['from_km'] + 0.05, to_km=t1['to_km'], work_hours=1.5,
                note='', active=True, created_by=self.admin_id, created_at=time.time())
            db.add(overlap)
            db.commit()
            overlap_id = overlap.id
        # 分别指定不同班组，排除班组因素，专门验证跨班组的里程重叠拦截
        p1 = self.client.post('/maintenance/plans/trial', json={
            'name': '里程方案甲', 'window_ids': [w['id']], 'task_ids': [t1['id']],
            'crew_ids': [crews[0]['id']]}, headers=self.auth('analyst')).json()
        self.assertEqual(self.client.post(
            '/maintenance/plans/%d/confirm' % p1['id'],
            json={'confirm_key': 'mile-a-0001'}, headers=self.auth('admin')).status_code, 200)
        p2 = self.client.post('/maintenance/plans/trial', json={
            'name': '里程方案乙', 'window_ids': [w['id']], 'task_ids': [overlap_id],
            'crew_ids': [crews[1]['id']]}, headers=self.auth('analyst')).json()
        r = self.client.post('/maintenance/plans/%d/confirm' % p2['id'],
                             json={'confirm_key': 'mile-b-0001'}, headers=self.auth('admin'))
        self.assertEqual(r.status_code, 409)
        self.assertIn('里程', r.json()['detail'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
