import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, patch, post
from litestar.exceptions import HTTPException
from passlib.context import CryptContext
from psycopg.errors import UniqueViolation

from db import MIGRATIONS, SCHEMA, connect
from rules import judge

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def init_db():
    with connect() as conn:
        conn.execute(SCHEMA)
        for stmt in MIGRATIONS:
            conn.execute(stmt)
        # 旧数据回填：历史 iv_scans 的编号登记进 inverters，并补上 inverter_id
        conn.execute(
            """INSERT INTO inverters (code, created_by, created_at)
               SELECT DISTINCT s.string_code,
                       COALESCE(MIN(s.created_by), 'scanner'),
                       MIN(s.created_at)
               FROM iv_scans s
               WHERE s.inverter_id IS NULL
               GROUP BY s.string_code
               ON CONFLICT (code) DO NOTHING"""
        )
        conn.execute(
            """UPDATE iv_scans s SET inverter_id = i.id
               FROM inverters i
               WHERE s.inverter_id IS NULL AND s.string_code = i.code"""
        )
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                inv = conn.execute(
                    """INSERT INTO inverters (code, created_by, created_at)
                       VALUES (%s,'scanner',%s)
                       ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code
                       RETURNING id""",
                    (code, now),
                ).fetchone()
                conn.execute(
                    """INSERT INTO iv_scans
                       (inverter_id, string_code, voc_v, isc_a, fill_factor, status,
                        verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (inv["id"], code, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


init_db()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=403, detail="旁观身份只读，不能改动条带或提交入队")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login", status_code=200)
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


# 条带颜色的唯一权威来源：每台逆变器“最近一次已办结”的扫描，
# 并列时取 id 更大的一张；从未办结返回 null（前端保持灰/空）。
INVERTER_SELECT = """
SELECT i.id, i.code,
       l.verdict AS latest_verdict,
       l.reason  AS latest_reason,
       l.fill_factor AS latest_fill_factor,
       l.id AS latest_scan_id,
       l.processed_at AS latest_processed_at
FROM inverters i
LEFT JOIN LATERAL (
    SELECT s.verdict, s.reason, s.fill_factor, s.id, s.processed_at
    FROM iv_scans s
    WHERE s.inverter_id = i.id AND s.status = 'done'
    ORDER BY s.id DESC
    LIMIT 1
) l ON true
ORDER BY i.id
"""


@get("/api/inverters")
async def list_inverters(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(INVERTER_SELECT).fetchall()
        return [dump(r) for r in rows]


@post("/api/inverters", status_code=201)
async def create_inverter(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="逆变器编号不能为空")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        try:
            row = conn.execute(
                """INSERT INTO inverters (code, created_by, created_at)
                   VALUES (%s,%s,%s)
                   RETURNING id, code""",
                (code, user["username"], now),
            ).fetchone()
            conn.commit()
        except UniqueViolation:
            raise HTTPException(status_code=409, detail="该逆变器编号已存在")
        return dump(row)


@patch("/api/inverters/{inv_id:int}")
async def rename_inverter(request: Request, inv_id: int) -> dict:
    need_writer(request)
    data = await request.json()
    code = (data.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="逆变器编号不能为空")
    with connect() as conn:
        row = conn.execute("SELECT id FROM inverters WHERE id=%s", (inv_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="逆变器不存在")
        try:
            conn.execute("UPDATE inverters SET code=%s WHERE id=%s", (code, inv_id))
            # 编号改名级联到历史扫描，条带归属跟着编号走
            conn.execute(
                "UPDATE iv_scans SET string_code=%s WHERE inverter_id=%s",
                (code, inv_id),
            )
            conn.commit()
        except UniqueViolation:
            raise HTTPException(status_code=409, detail="该逆变器编号已存在")
        return {"id": inv_id, "code": code}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, inverter_id, string_code, voc_v, isc_a, fill_factor, status,
                      verdict, reason, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 提交即把编号登记进逆变器表；两笔同编号并发也只会有一台逆变器
        inv = conn.execute(
            """INSERT INTO inverters (code, created_by, created_at)
               VALUES (%s,%s,%s)
               ON CONFLICT (code) DO UPDATE SET code = EXCLUDED.code
               RETURNING id""",
            (code, user["username"], now),
        ).fetchone()
        row = conn.execute(
            """INSERT INTO iv_scans
               (inverter_id, string_code, voc_v, isc_a, fill_factor, status,
                created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, inverter_id, string_code, voc_v, isc_a, fill_factor, status,
                         verdict, reason, created_by, created_at, processed_at""",
            (inv["id"], code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


app = Litestar(
    route_handlers=[
        health,
        login,
        list_inverters,
        create_inverter,
        rename_inverter,
        list_logs,
        create_log,
    ]
)
