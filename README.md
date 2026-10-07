# 光伏组串IV扫描台

扫描员提交组串开路电压、短路电流与填充因子。写入后走 PostgreSQL 通知通道叫醒独立工人，工人不轮询空转。填充因子不低于 0.72 为合格，否则衰减。页面是 Vue 3。

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

## 逆变器条带

顶栏「逆变器条带」进入专页：左侧维护逆变器编号（扫描员可改名，旁观者只读），右侧整条条带可刷新。

- `GET /api/stripes`：每台逆变器一条，颜色只取**最近一次办结**扫描（`status='done'` 中 `id` 最大那笔，`DISTINCT ON ... ORDER BY string_code, id DESC`）。同一台两笔抢着办完时，条带跟随编号更大的那张。从未办结（仅待处理或无 done 记录）返回 `verdict=null`，前端保持灰色空白，不假装合格。
- `POST /api/inverters/rename`：仅扫描员（writer），把某编号名下所有扫描改名；旁观者返回 403。
- 条带颜色严格绑定接口返回，前端不做乐观涂色；提交后等工人办结、接口回报新颜色，画面才变色，避免「只把画面涂绿、接口仍报旧颜色」。

## 种子

| 组串 | 填充因子 | 结论 |
|------|----------|------|
| 阵列A-串03 | 0.78 | 合格 |
| 阵列B-串11 | 0.61 | 衰减 |
