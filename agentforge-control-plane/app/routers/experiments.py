from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.auth import CurrentUser, require_permission
from app.access.kinds import ResourceKind
from app.access.scope import stamp_owner
from app.access.service import access_service
from app.database import get_db
from app.models import Experiment, ExperimentAssignment, ExperimentEvent, ExperimentVariant
from app.schemas import ExperimentAssign, ExperimentCompare, ExperimentCreate, ExperimentUpdate
from app.services.experiment_runtime import (
    STRATEGIES,
    apply_strategy,
    assign_unit,
    dump_experiment,
    experiment_results,
    list_variants,
    normalize_strategy,
    replace_variants,
    run_compare,
    validate_variants,
)

router = APIRouter()


def _get_experiment(user: CurrentUser, experiment_id: int, db: Session) -> Experiment:
    return access_service.get_row(user, ResourceKind.EXPERIMENT, experiment_id, db)


@router.get("/api/experiments")
def list_experiments(user: CurrentUser = Depends(require_permission("experiment:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [dump_experiment(row, db) for row in access_service.list_rows(user, ResourceKind.EXPERIMENT, db)]


@router.post("/api/experiments", status_code=201)
def create_experiment(payload: ExperimentCreate, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    strategy = normalize_strategy(payload.assignment_strategy, payload.assignment_unit)
    if strategy not in STRATEGIES:
        raise HTTPException(400, "不支持的分流策略")
    variants = validate_variants(payload.variants, db, user)
    row = stamp_owner(Experiment(
        name=payload.name.strip(),
        description=payload.description or "",
        status="draft",
        assignment_unit="user" if STRATEGIES[strategy]["unit"] == "user" else "session",
        assignment_strategy=strategy,
        traffic_percent=payload.traffic_percent,
    ), user)
    db.add(row)
    db.flush()
    replace_variants(db, row, variants, user)
    db.commit()
    db.refresh(row)
    return dump_experiment(row, db)


@router.get("/api/experiments/{experiment_id}")
def get_experiment(experiment_id: int, user: CurrentUser = Depends(require_permission("experiment:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    return experiment_results(db, _get_experiment(user, experiment_id, db))


@router.put("/api/experiments/{experiment_id}")
def update_experiment(experiment_id: int, payload: ExperimentUpdate, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.EXPERIMENT, experiment_id, db)
    if row.status == "completed":
        raise HTTPException(409, "已结束的实验不能再改")
    if payload.name is not None:
        row.name = payload.name.strip()
    if payload.description is not None:
        row.description = payload.description
    if payload.assignment_strategy is not None or payload.assignment_unit is not None:
        if row.status == "running":
            raise HTTPException(409, "进行中的实验不能改分流策略，请先暂停")
        apply_strategy(row, payload.assignment_strategy or payload.assignment_unit)
    if payload.traffic_percent is not None:
        row.traffic_percent = payload.traffic_percent
    if payload.variants is not None:
        if row.status == "running":
            raise HTTPException(409, "进行中的实验不能改变体，请先暂停")
        replace_variants(db, row, validate_variants(payload.variants, db, user), user)
    db.commit()
    db.refresh(row)
    return dump_experiment(row, db)


@router.delete("/api/experiments/{experiment_id}")
def delete_experiment(experiment_id: int, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.EXPERIMENT, experiment_id, db)
    for model in (ExperimentEvent, ExperimentAssignment, ExperimentVariant):
        for item in db.scalars(select(model).where(model.experiment_id == experiment_id)).all():
            db.delete(item)
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.post("/api/experiments/{experiment_id}/start")
def start_experiment(experiment_id: int, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.EXPERIMENT, experiment_id, db)
    if row.status == "completed":
        raise HTTPException(409, "已结束的实验不能重新开启")
    if len(list_variants(db, row.id)) < 2:
        raise HTTPException(400, "至少需要两个分流变体")
    row.status = "running"
    row.started_at = row.started_at or datetime.utcnow()
    row.finished_at = None
    db.commit()
    db.refresh(row)
    return dump_experiment(row, db)


@router.post("/api/experiments/{experiment_id}/pause")
def pause_experiment(experiment_id: int, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.EXPERIMENT, experiment_id, db)
    if row.status != "running":
        raise HTTPException(409, "只有进行中的实验可以暂停")
    row.status = "paused"
    db.commit()
    db.refresh(row)
    return dump_experiment(row, db)


@router.post("/api/experiments/{experiment_id}/complete")
def complete_experiment(experiment_id: int, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.EXPERIMENT, experiment_id, db)
    if row.status == "completed":
        return dump_experiment(row, db)
    row.status = "completed"
    row.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return dump_experiment(row, db)


@router.post("/api/experiments/{experiment_id}/assign")
def assign_experiment(experiment_id: int, payload: ExperimentAssign, user: CurrentUser = Depends(require_permission("experiment:read", "session:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = _get_experiment(user, experiment_id, db)
    if row.status != "running":
        raise HTTPException(409, "只有进行中的实验才会分流")
    session_id = (payload.session_id or payload.unit_key or "").strip()
    user_key = (payload.user_key or "").strip() or user.username
    if not session_id and not user_key:
        raise HTTPException(400, "请提供 session_id 或 user_key")
    data = assign_unit(db, row, session_id=session_id or f"sess_{user_key}", user_key=user_key, user=user)
    db.commit()
    return data


@router.post("/api/experiments/{experiment_id}/compare")
def compare_experiment(experiment_id: int, payload: ExperimentCompare, user: CurrentUser = Depends(require_permission("experiment:write")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.EXPERIMENT, experiment_id, db)
    snapshot = run_compare(
        db,
        row,
        user,
        dataset_id=payload.dataset_id,
        prompts=payload.prompts,
        scorer=payload.scorer,
        case_limit=payload.case_limit,
    )
    db.commit()
    db.refresh(row)
    data = experiment_results(db, row)
    data["last_compare"] = snapshot
    return data
