import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
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


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
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
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


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
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可提交IV扫描")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
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
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/stripes")
async def list_stripes(request: Request) -> list:
    """每台逆变器一条：颜色只认最近一次“已办结”扫描（取编号最大的一笔）。

    从未办结（仅待处理或没有任何 done 记录）的逆变器 verdict 为 None，
    前端必须保持灰/空，不得凭空给绿。
    """
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT c.string_code,
                      d.id AS last_scan_id,
                      d.verdict,
                      d.fill_factor,
                      d.processed_at
               FROM (SELECT DISTINCT string_code FROM iv_scans) c
               LEFT JOIN (
                   SELECT DISTINCT ON (string_code)
                          string_code, id, verdict, fill_factor, processed_at
                   FROM iv_scans
                   WHERE status = 'done'
                   ORDER BY string_code, id DESC
               ) d USING (string_code)
               ORDER BY c.string_code"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/inverters/rename", status_code=200)
async def rename_inverter(request: Request) -> dict:
    """扫描员改逆变器编号，名下所有扫描（含待处理）一起改名。旁观身份 403。"""
    user = need_writer(request)
    data = await request.json()
    old_code = (data.get("string_code") or "").strip()
    new_code = (data.get("new_code") or data.get("new_string_code") or "").strip()
    if not old_code:
        raise HTTPException(status_code=400, detail="原编号不能为空")
    if not new_code:
        raise HTTPException(status_code=400, detail="新编号不能为空")
    if old_code == new_code:
        return {"ok": True, "string_code": old_code}
    with connect() as conn:
        with conn.transaction():
            exists = conn.execute(
                "SELECT 1 FROM iv_scans WHERE string_code = %s LIMIT 1",
                (old_code,),
            ).fetchone()
            if exists is None:
                raise HTTPException(status_code=404, detail="逆变器不存在")
            clash = conn.execute(
                "SELECT 1 FROM iv_scans WHERE string_code = %s LIMIT 1",
                (new_code,),
            ).fetchone()
            if clash is not None:
                raise HTTPException(status_code=400, detail="该编号已被其他逆变器占用")
            conn.execute(
                "UPDATE iv_scans SET string_code = %s WHERE string_code = %s",
                (new_code, old_code),
            )
        conn.commit()
    return {"ok": True, "string_code": new_code, "renamed_by": user["username"]}


app = Litestar(
    route_handlers=[health, login, list_logs, create_log, list_stripes, rename_inverter]
)
