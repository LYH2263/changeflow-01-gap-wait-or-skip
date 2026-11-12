from fastapi import APIRouter, HTTPException

from app.db import connect
from app.modules.gap_wait_or_skip import POLICIES
from app.repositories import settings as settings_repo
from app.schemas.entities import SettingPut

router = APIRouter(tags=["settings"])


@router.get("/settings")
def list_settings():
    conn = connect()
    try:
        return settings_repo.as_list(conn)
    finally:
        conn.close()


@router.put("/settings/{key}")
def put_setting(key: str, body: SettingPut):
    value = body.value
    if key == "gap_policy":
        if value not in POLICIES:
            raise HTTPException(400, f"gap_policy 必须是 {list(POLICIES)} 之一")
    elif key == "gap_wait_window":
        try:
            window = int(value)
        except ValueError:
            raise HTTPException(400, "gap_wait_window 必须是不小于 0 的整数") from None
        if window < 0:
            raise HTTPException(400, "gap_wait_window 必须是不小于 0 的整数")
        value = str(window)
    conn = connect()
    try:
        settings_repo.put(conn, key, value)
        conn.commit()
        return {"key": key, "value": value}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    finally:
        conn.close()
