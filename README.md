# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

顶栏进入「逆变器条带」专页：左侧维护逆变器编号（扫描员可新增/改名），右侧整条按**该逆变器最近一次已办结扫描**着色——合格绿、衰减红、从未办结灰。颜色唯一来自接口 `GET /api/inverters` 的返回，前端不做本地乐观涂色；接口回报什么颜色，画面才是什么颜色。同一台逆变器多笔扫描抢办时，条带跟随扫描单编号（`id`）更大的那一张。

## 接口

| 方法 | 路径 | 权限 | 说明 |
|------|------|------|------|
| GET | `/api/inverters` | 登录 | 条带数据源，含 `latest_verdict`（未办结为 null） |
| POST | `/api/inverters` | 扫描员 | 新增逆变器编号 |
| PATCH | `/api/inverters/{id}` | 扫描员 | 改编号，历史扫描单级联改名 |
| GET | `/api/logs` | 登录 | 扫描记录 |
| POST | `/api/logs` | 扫描员 | 提交扫描并入队，编号自动登记 |

旁观账号（watcher）只有读权限：不能改条带、不能改编号、不能提交入队。

## 技术栈

- 后端：Litestar、Uvicorn、psycopg 同步写入
- 工人：`LISTEN/NOTIFY` 唤醒后认领
- 前端：Vue 3、Vite、nginx 反代 `/api`

## 端口

| 服务 | 地址 |
|------|------|
| 页面 | http://localhost:3202 |
| 接口 | http://localhost:8202 |
| PostgreSQL | localhost:54402（库名 `pvivscan`） |

## 账号

| 用户 | 密码 | 权限 |
|------|------|------|
| scanner | scan123456 | 可提交 |
| watcher | watch123456 | 只读 |

## 启动

```bash
cd projects/22-pv-string-iv-scan
docker compose up --build
```

健康检查：`GET http://localhost:8202/api/health`

## 种子

| 组串 | 填充因子 | 结论 |
|------|----------|------|
| 阵列A-串03 | 0.78 | 合格 |
| 阵列B-串11 | 0.61 | 衰减 |
