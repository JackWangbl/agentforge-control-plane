from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.auth import CurrentUser, require_permission
from app.access.kinds import ResourceKind
from app.access.scope import stamp_owner
from app.access.service import access_service
from app.database import ROOT, SessionLocal, get_db
from app.models import Agent, Dataset, DatasetCase, EvaluationResult, EvaluationRun
from app.schemas import DatasetCaseCreate, DatasetCreate, EvaluationLaunch
from app.services.dataset_import import ImportErrorDetail, parse_dataset_bytes
from app.services.eval_runner import ONLINE_CASE_LIMIT, dump_run, execute_run, resolve_cases
from app.services.eval_scorer import SCORERS

router = APIRouter()
DATASET_FILES = ROOT / "workspaces" / "_datasets"


def _refresh_count(db: Session, dataset: Dataset) -> None:
    dataset.case_count = len(list(db.scalars(select(DatasetCase.id).where(DatasetCase.dataset_id == dataset.id)).all()))


def _dump_dataset(row: Dataset) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "source_name": row.source_name or "",
        "case_count": row.case_count,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _dump_case(row: DatasetCase) -> dict[str, Any]:
    return {
        "id": row.id,
        "dataset_id": row.dataset_id,
        "case_key": row.case_key,
        "input": row.input,
        "expected": row.expected or "",
        "tags": row.tags or [],
        "extra": row.extra or {},
        "enabled": row.enabled,
        "created_at": row.created_at,
    }


def _load_run(run_id: int) -> dict[str, Any]:
    with SessionLocal() as db:
        run = db.get(EvaluationRun, run_id)
        data = dump_run(run)
        data["results"] = [
            _dump_result(item)
            for item in db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id).order_by(EvaluationResult.id.asc())).all()
        ]
        return data


def _dump_result(row: EvaluationResult) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_id": row.run_id,
        "case_id": row.case_id,
        "case_key": row.case_key,
        "status": row.status,
        "score": row.score,
        "input": row.input,
        "expected": row.expected,
        "actual": row.actual,
        "reason": row.reason,
        "latency_ms": row.latency_ms,
        "tokens": row.tokens,
        "trace_id": row.trace_id,
        "session_id": row.session_id,
        "error": row.error,
        "created_at": row.created_at,
    }


def _insert_cases(db: Session, dataset: Dataset, cases: list[dict[str, Any]], on_duplicate: str) -> dict[str, int]:
    existing = {
        row.case_key: row
        for row in db.scalars(select(DatasetCase).where(DatasetCase.dataset_id == dataset.id)).all()
        if row.case_key
    }
    added = updated = skipped = 0
    for item in cases:
        key = item.get("case_key") or ""
        found = existing.get(key) if key else None
        if found and on_duplicate == "skip":
            skipped += 1
            continue
        if found and on_duplicate == "replace":
            found.input = item["input"]
            found.expected = item.get("expected") or ""
            found.tags = item.get("tags") or []
            found.extra = item.get("extra") or {}
            found.enabled = True
            updated += 1
            continue
        row = DatasetCase(
            dataset_id=dataset.id,
            case_key=key,
            input=item["input"],
            expected=item.get("expected") or "",
            tags=item.get("tags") or [],
            extra=item.get("extra") or {},
            tenant_id=dataset.tenant_id,
            owner_id=dataset.owner_id,
        )
        db.add(row)
        if key:
            existing[key] = row
        added += 1
    db.flush()
    _refresh_count(db, dataset)
    return {"added": added, "updated": updated, "skipped": skipped}


@router.get("/api/datasets")
def list_datasets(user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [_dump_dataset(row) for row in access_service.list_rows(user, ResourceKind.DATASET, db)]


@router.get("/api/datasets/template.csv")
def dataset_template() -> StreamingResponse:
    content = "id,input,expected,tags\n1,用户问退款要几天到账,三个工作日内原路退回,退款\n"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=dataset-template.csv"},
    )


@router.post("/api/datasets", status_code=201)
def create_dataset(payload: DatasetCreate, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = stamp_owner(Dataset(name=payload.name.strip(), description=payload.description or ""), user)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _dump_dataset(row)


@router.post("/api/datasets/import")
async def import_dataset(
    file: UploadFile = File(...),
    name: str = Form(""),
    dataset_id: Optional[int] = Form(None),
    on_duplicate: str = Form("skip"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_permission("eval:run")),
) -> dict[str, Any]:
    raw = await file.read()
    try:
        parsed = parse_dataset_bytes(raw, file.filename or "upload.csv")
    except ImportErrorDetail as exc:
        raise HTTPException(400, {"message": str(exc), "errors": exc.errors}) from exc
    dataset = access_service.get_row(user, ResourceKind.DATASET, dataset_id, db) if dataset_id else None
    if not dataset:
        dataset = stamp_owner(Dataset(name=(name or Path(file.filename or "数据集").stem).strip() or "未命名数据集"), user)
        db.add(dataset)
        db.flush()
    dataset.source_name = file.filename or dataset.source_name
    stats = _insert_cases(db, dataset, parsed["cases"], on_duplicate)
    folder = DATASET_FILES / str(dataset.id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (file.filename or "upload.csv")).write_bytes(raw)
    db.commit()
    db.refresh(dataset)
    return {**_dump_dataset(dataset), **stats, "errors": parsed["errors"], "imported": parsed["count"]}


@router.get("/api/datasets/{dataset_id}")
def get_dataset(dataset_id: int, user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.get_row(user, ResourceKind.DATASET, dataset_id, db)
    data = _dump_dataset(row)
    data["cases"] = [_dump_case(item) for item in db.scalars(select(DatasetCase).where(DatasetCase.dataset_id == dataset_id).order_by(DatasetCase.id.asc())).all()]
    return data


@router.get("/api/datasets/{dataset_id}/cases")
def list_cases(dataset_id: int, q: str = "", user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    access_service.get_row(user, ResourceKind.DATASET, dataset_id, db)
    stmt = select(DatasetCase).where(DatasetCase.dataset_id == dataset_id).order_by(DatasetCase.id.asc())
    rows = list(db.scalars(stmt).all())
    if q.strip():
        needle = q.strip().lower()
        rows = [row for row in rows if needle in (row.input or "").lower() or needle in (row.expected or "").lower() or needle in (row.case_key or "").lower()]
    return [_dump_case(row) for row in rows]


@router.post("/api/datasets/{dataset_id}/cases", status_code=201)
def add_case(dataset_id: int, payload: DatasetCaseCreate, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    dataset = access_service.resolve_for_edit(user, ResourceKind.DATASET, dataset_id, db)
    row = DatasetCase(
        dataset_id=dataset_id,
        case_key=payload.case_key or "",
        input=payload.input,
        expected=payload.expected or "",
        tags=payload.tags or [],
        tenant_id=user.tenant_id,
        owner_id=user.id,
    )
    db.add(row)
    db.flush()
    if not row.case_key:
        row.case_key = str(row.id)
    _refresh_count(db, dataset)
    db.commit()
    db.refresh(row)
    return _dump_case(row)


@router.delete("/api/datasets/{dataset_id}/cases/{case_id}")
def delete_case(dataset_id: int, case_id: int, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    dataset = access_service.resolve_for_edit(user, ResourceKind.DATASET, dataset_id, db)
    row = db.get(DatasetCase, case_id)
    if not dataset or not row or row.dataset_id != dataset_id:
        raise HTTPException(404, "Case not found")
    db.delete(row)
    db.flush()
    _refresh_count(db, dataset)
    db.commit()
    return {"ok": True}


@router.delete("/api/datasets/{dataset_id}")
def delete_dataset(dataset_id: int, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    row = access_service.resolve_for_edit(user, ResourceKind.DATASET, dataset_id, db)
    for case in db.scalars(select(DatasetCase).where(DatasetCase.dataset_id == dataset_id)).all():
        db.delete(case)
    db.delete(row)
    db.commit()
    return {"ok": True}


def _prepare_run(payload: EvaluationLaunch, mode: str, db: Session, user: CurrentUser) -> EvaluationRun:
    agent = access_service.get_row(user, ResourceKind.AGENT, payload.agent_id, db)
    dataset = access_service.get_row(user, ResourceKind.DATASET, payload.dataset_id, db)
    scorer = (payload.scorer or "contains").lower()
    if scorer not in SCORERS:
        raise HTTPException(400, "不支持的打分方式")
    cases = resolve_cases(db, dataset.id, payload.case_ids)
    if not cases:
        raise HTTPException(400, "没有可测试的用例")
    if mode == "online" and len(cases) > ONLINE_CASE_LIMIT:
        raise HTTPException(400, f"在线测试最多 {ONLINE_CASE_LIMIT} 条，请改走离线或减少勾选")
    name = payload.name.strip() or f"{agent.name} · {dataset.name} · {datetime.utcnow().strftime('%m-%d %H:%M')}"
    run = EvaluationRun(
        name=name,
        dataset=dataset.name,
        agent_name=agent.name,
        dataset_id=dataset.id,
        agent_id=agent.id,
        judge_model_id=payload.judge_model_id,
        mode=mode,
        scorer=scorer,
        status="queued",
        case_ids=[item.id for item in cases],
        cases=len(cases),
        total=len(cases),
        tenant_id=user.tenant_id,
        owner_id=user.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


@router.get("/api/evaluations")
def list_evaluations(user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return [dump_run(row) for row in access_service.list_rows(user, ResourceKind.EVALUATION, db)]


@router.post("/api/evaluations", status_code=201)
def create_evaluation(payload: EvaluationLaunch, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = _prepare_run(payload, "offline", db, user)
    return dump_run(run)


@router.post("/api/evaluations/online")
def run_online(payload: EvaluationLaunch, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = _prepare_run(payload, "online", db, user)
    execute_run(run.id)
    return _load_run(run.id)


@router.get("/api/evaluations/{run_id}")
def get_evaluation(run_id: int, user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = access_service.get_row(user, ResourceKind.EVALUATION, run_id, db)
    data = dump_run(run)
    data["results"] = [_dump_result(item) for item in db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id).order_by(EvaluationResult.id.asc())).all()]
    return data


@router.get("/api/evaluations/{run_id}/results")
def list_results(run_id: int, status: str = "", user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    access_service.get_row(user, ResourceKind.EVALUATION, run_id, db)
    stmt = select(EvaluationResult).where(EvaluationResult.run_id == run_id).order_by(EvaluationResult.id.asc())
    if status:
        stmt = stmt.where(EvaluationResult.status == status)
    return [_dump_result(row) for row in db.scalars(stmt).all()]


@router.post("/api/evaluations/{run_id}/resume")
def resume_evaluation(run_id: int, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = access_service.resolve_for_edit(user, ResourceKind.EVALUATION, run_id, db)
    if run.status in {"queued", "running"}:
        raise HTTPException(409, "任务还在执行，请等待结束或先取消")
    rows = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)).all())
    failed_rows = [item for item in rows if item.status != "passed"]
    if not failed_rows:
        raise HTTPException(409, "没有失败用例可以续跑")
    for item in failed_rows:
        db.delete(item)
    kept = [item for item in rows if item.status == "passed"]
    run.status = "queued"
    run.passed = len(kept)
    run.failed = 0
    run.skipped = 0
    run.score = round(len(kept) / max(1, run.total or run.cases or len(rows)) * 100, 1) if kept else 0
    run.error_message = ""
    run.started_at = None
    run.finished_at = None
    db.commit()
    if run.mode == "online":
        execute_run(run.id)
        return _load_run(run_id)
    return dump_run(run)


@router.post("/api/evaluations/{run_id}/run")
def rerun_evaluation(run_id: int, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = access_service.resolve_for_edit(user, ResourceKind.EVALUATION, run_id, db)
    for item in db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)).all():
        db.delete(item)
    run.status = "queued"
    run.passed = run.failed = run.skipped = 0
    run.score = 0
    run.error_message = ""
    run.started_at = None
    run.finished_at = None
    db.commit()
    if run.mode == "online":
        execute_run(run.id)
        return _load_run(run_id)
    return dump_run(run)


@router.post("/api/evaluations/{run_id}/cancel")
def cancel_evaluation(run_id: int, user: CurrentUser = Depends(require_permission("eval:run")), db: Session = Depends(get_db)) -> dict[str, Any]:
    run = access_service.resolve_for_edit(user, ResourceKind.EVALUATION, run_id, db)
    if run.status in {"completed", "failed"}:
        raise HTTPException(409, "任务已经结束")
    run.status = "cancelled"
    run.finished_at = datetime.utcnow()
    db.commit()
    return dump_run(run)


@router.get("/api/evaluations/{run_id}/export.csv")
def export_evaluation(run_id: int, user: CurrentUser = Depends(require_permission("eval:read")), db: Session = Depends(get_db)) -> StreamingResponse:
    run = access_service.get_row(user, ResourceKind.EVALUATION, run_id, db)
    rows = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id).order_by(EvaluationResult.id.asc())).all())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["case_key", "status", "score", "input", "expected", "actual", "reason", "latency_ms", "tokens", "trace_id"])
    for item in rows:
        writer.writerow([item.case_key, item.status, item.score, item.input, item.expected, item.actual, item.reason, item.latency_ms, item.tokens, item.trace_id])
    buf.seek(0)
    filename = f"eval-{run_id}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
