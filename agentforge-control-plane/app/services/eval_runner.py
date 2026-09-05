from __future__ import annotations

import os
import threading
import time
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Agent, Dataset, DatasetCase, EvaluationResult, EvaluationRun, ModelConfig, Trace
from app.services.agent_workspace import new_trace_id
from app.services.eval_scorer import score_case, score_with_llm
from app.services.studio_tracer import export_playground_to_studio

ONLINE_CASE_LIMIT = 10
_worker_started = False
_lock = threading.Lock()


def start_eval_worker() -> None:
    global _worker_started
    if os.getenv("EVAL_WORKER") == "0":
        return
    with _lock:
        if _worker_started:
            return
        _worker_started = True
    thread = threading.Thread(target=_worker_loop, name="eval-worker", daemon=True)
    thread.start()


def _worker_loop() -> None:
    while True:
        time.sleep(1.2)
        try:
            run_id = _claim_run()
            if run_id:
                execute_run(run_id)
        except Exception:
            continue


def _claim_run() -> Optional[int]:
    with SessionLocal() as db:
        row = db.scalar(
            select(EvaluationRun)
            .where(EvaluationRun.status == "queued", EvaluationRun.mode == "offline")
            .order_by(EvaluationRun.id.asc())
            .limit(1)
        )
        if not row:
            return None
        row.status = "running"
        row.started_at = datetime.utcnow()
        db.commit()
        return row.id


def resolve_cases(db: Session, dataset_id: int, case_ids: list[int] | None) -> list[DatasetCase]:
    stmt = select(DatasetCase).where(DatasetCase.dataset_id == dataset_id, DatasetCase.enabled.is_(True))
    if case_ids:
        stmt = stmt.where(DatasetCase.id.in_([int(item) for item in case_ids]))
    return list(db.scalars(stmt.order_by(DatasetCase.id.asc())).all())


def execute_run(run_id: int) -> EvaluationRun:
    from app.main import build_debug_spans, generate_chat_reply, model_endpoint, resolve_model_credential

    with SessionLocal() as db:
        run = db.get(EvaluationRun, run_id)
        if not run:
            raise ValueError("Evaluation not found")
        if run.status == "cancelled":
            return run
        agent = db.get(Agent, run.agent_id) if run.agent_id else None
        if not agent:
            run.status = "failed"
            run.error_message = "Agent 不存在"
            run.finished_at = datetime.utcnow()
            db.commit()
            return run
        model = db.scalar(select(ModelConfig).where(ModelConfig.name == agent.model_name))
        if not model:
            models = list(db.scalars(select(ModelConfig).where(ModelConfig.enabled.is_(True))).all())
            model = models[0] if models else None
        if not model:
            run.status = "failed"
            run.error_message = "没有可用的模型配置"
            run.finished_at = datetime.utcnow()
            db.commit()
            return run
        judge = db.get(ModelConfig, run.judge_model_id) if run.judge_model_id else model
        cases = resolve_cases(db, run.dataset_id or 0, run.case_ids or [])
        run.total = len(cases)
        run.cases = len(cases)
        run.status = "running"
        run.started_at = run.started_at or datetime.utcnow()
        run.error_message = ""
        db.commit()
        passed = failed = skipped = tokens = latency = 0
        for case in cases:
            db.refresh(run)
            if run.status == "cancelled":
                break
            result = _run_one_case(
                db,
                run=run,
                agent=agent,
                model=model,
                judge=judge,
                case=case,
                generate_chat_reply=generate_chat_reply,
                build_debug_spans=build_debug_spans,
                model_endpoint=model_endpoint,
                resolve_model_credential=resolve_model_credential,
            )
            if result.status == "passed":
                passed += 1
            elif result.status == "skipped":
                skipped += 1
            else:
                failed += 1
            tokens += result.tokens
            latency += result.latency_ms
            judged = passed + failed
            run.passed = passed
            run.failed = failed
            run.skipped = skipped
            run.total_tokens = tokens
            run.avg_latency_ms = int(latency / max(1, passed + failed + skipped))
            run.score = round(passed / judged * 100, 1) if judged else 0
            db.commit()
        db.refresh(run)
        if run.status != "cancelled":
            run.status = "completed"
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return run


def _run_one_case(
    db: Session,
    *,
    run: EvaluationRun,
    agent: Agent,
    model: ModelConfig,
    judge: ModelConfig,
    case: DatasetCase,
    generate_chat_reply,
    build_debug_spans,
    model_endpoint,
    resolve_model_credential,
) -> EvaluationResult:
    session_id = f"eval_{run.id}_{case.id}"
    started = datetime.utcnow()
    history = [{"role": "user", "content": case.input}]
    reply, mode, tool_spans, usage = generate_chat_reply(agent, model, history, db)
    latency_ms = max(1, int((datetime.utcnow() - started).total_seconds() * 1000))
    spans = build_debug_spans(agent, model, mode, tool_spans, latency_ms, db)
    trace_id = new_trace_id()
    db.add(Trace(
        trace_id=trace_id,
        session_id=session_id,
        agent_id=agent.id,
        agent_name=agent.name,
        operation="POST /api/evaluations",
        status="ok" if mode != "error" else "error",
        duration_ms=latency_ms,
        input_tokens=int((usage or {}).get("prompt_tokens") or len(case.input)),
        output_tokens=int((usage or {}).get("completion_tokens") or len(reply)),
        spans=spans,
        langfuse_url="",
        started_at=started,
    ))
    try:
        export_playground_to_studio(
            agent_name=agent.name,
            session_id=session_id,
            trace_id=trace_id,
            message=case.input,
            reply=reply,
            mode=mode,
            model_name=model.name,
            model_id=model.model_id,
            spans=spans,
            usage=usage or {},
            latency_ms=latency_ms,
            title=f"评测 {run.name}",
        )
    except Exception:
        pass
    if run.scorer == "llm":
        credential = resolve_model_credential(judge)
        if not credential:
            judged = {"status": "error", "score": 0, "reason": "LLM 判分需要裁判模型的 API 密钥"}
        else:
            try:
                judged = score_with_llm(
                    expected=case.expected or "",
                    actual=reply,
                    input_text=case.input,
                    model_id=judge.model_id,
                    base_url=model_endpoint(judge),
                    api_key=credential,
                )
            except Exception as exc:
                judged = {"status": "error", "score": 0, "reason": f"裁判调用失败：{exc}"}
    else:
        judged = score_case(run.scorer, case.expected or "", reply)
    if mode == "error" and judged["status"] == "passed":
        judged = {"status": "error", "score": 0, "reason": reply[:160]}
    row = EvaluationResult(
        run_id=run.id,
        case_id=case.id,
        case_key=case.case_key or str(case.id),
        status=judged["status"],
        score=float(judged.get("score") or 0),
        input=case.input,
        expected=case.expected or "",
        actual=reply,
        reason=judged.get("reason") or "",
        latency_ms=latency_ms,
        tokens=int((usage or {}).get("total_tokens") or (len(case.input) + len(reply))),
        trace_id=trace_id,
        session_id=session_id,
        error="" if judged["status"] != "error" else (judged.get("reason") or ""),
    )
    db.add(row)
    db.flush()
    return row


def dump_run(run: EvaluationRun) -> dict[str, Any]:
    judged = (run.passed or 0) + (run.failed or 0)
    progress = 0
    if run.total:
        progress = round(((run.passed or 0) + (run.failed or 0) + (run.skipped or 0)) / run.total * 100)
    return {
        "id": run.id,
        "name": run.name,
        "dataset": run.dataset,
        "dataset_id": run.dataset_id,
        "agent_name": run.agent_name,
        "agent_id": run.agent_id,
        "judge_model_id": run.judge_model_id,
        "mode": run.mode,
        "scorer": run.scorer,
        "status": run.status,
        "score": run.score,
        "cases": run.cases or run.total,
        "case_ids": run.case_ids or [],
        "total": run.total,
        "passed": run.passed,
        "failed": run.failed,
        "skipped": run.skipped,
        "avg_latency_ms": run.avg_latency_ms,
        "total_tokens": run.total_tokens,
        "error_message": run.error_message or "",
        "progress": progress,
        "judged": judged,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
