# 轨检缺陷密度追踪

密度聚类的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery。

### 封锁检修排程（扩展模块）
已批准试验只在简报里给建议，本模块把复核后的缺陷带落成可执行的封锁检修安排，实体为 Crew 班组、MaintenanceWindow 封锁天窗、MaintenanceTask 每带检修任务（登记工时/班组/可用天窗）、MaintenancePlan 方案（含 PlanItem 安排与 UnscheduledReason 未排原因）、ConfirmReceipt 确认回执。
- 试排不占用资源：同一班组时间重叠、里程区间时间重叠均不能同时开工；作业必须完整落在单个天窗内，跨天窗不拆工；排不进的任务返回未排原因代码（无天窗/天窗过短/班组占用/里程占用等）。
- 确认整组生效：任一冲突全组不生效；确认与天窗/班组变更共用同一把 Postgres 事务级咨询锁，按“先完成者裁决”，资源版本变化后旧试排拒绝确认。
- 幂等确认：前端为每张试排单持久化 confirm_key，回执丢失后用同一 key 重试，服务端回放首次结论，绝不重复占用资源；并发确认也不会超订。
- 天窗缩短/撤销：落在该窗上的已确认方案整组失效并拒绝开工，未受影响的已确认安排保留，可重新排程。
- 权限：admin 维护资源、确认与开工；analyst 登记任务、试排、改工时（不能确认）；viewer 只读。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18210；后端文档 http://localhost:19210/docs；数据库宿主端口 17710。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；PostgreSQL 16；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py；backend/tests/{test_scheduling,test_maintenance_api}.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed；检修方案状态：draft/confirmed/invalidated。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。
排程引擎 backend/src/services/scheduling.py 是不依赖数据库的纯函数（按 5 分钟对齐铺排），可用标准库直接跑 `python3 tests/test_scheduling.py`；服务层与路由的全链路（幂等确认、整组冲突、旧试排拒绝、天窗失效重排）见 `python3 tests/test_maintenance_api.py`（SQLite 内存验证，Postgres 咨询锁在 SQLite 退化为无操作）。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=rail-density；FRONTEND_PORT=18210；BACKEND_PORT=19210；DB_PORT=17710；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1011/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
