from __future__ import annotations
from typing import Any
from fastapi import APIRouter
from app.services import settings_service as svc

router = APIRouter(tags=["settings"])


@router.get("/settings/day")
def get_day():
    return svc.get_day()


@router.put("/settings/day")
def put_day(body: dict[str, Any]):
    return svc.set_day(body)


@router.get("/settings/visibility")
def get_visibility():
    return svc.get_visibility()


@router.put("/settings/visibility")
def put_visibility(body: dict[str, Any]):
    return svc.set_visibility(body)


@router.get("/debug/faults")
def get_faults():
    return svc.list_faults()


@router.put("/debug/faults")
def put_fault(body: dict[str, Any]):
    return svc.set_fault(body)
