from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
PERF_INLINE_LIMIT = 16
PERF_BUCKETS = (
    (300, "<300ms"),
    (800, "300-800ms"),
    (1500, "800ms-1.5s"),
    (3000, "1.5-3s"),
    (None, ">3s"),
)
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
            .where(EvaluationRun.status == "queued", EvaluationRun.mode.in_(("offline", "performance")))
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
    with SessionLocal() as db:
        run = db.get(EvaluationRun, run_id)
        if run and run.mode == "performance":
            return execute_perf_run(run_id)
    return _execute_quality_run(run_id)


def _execute_quality_run(run_id: int) -> EvaluationRun:
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
        judge = None
        if run.judge_model_id:
            judge = db.get(ModelConfig, run.judge_model_id)
        if not judge:
            from app.services.eval_catalog import resolve_judge_model
            judge = resolve_judge_model(db) or model
        cases = resolve_cases(db, run.dataset_id or 0, run.case_ids or [])
        run.total = len(cases)
        run.cases = len(cases)
        run.status = "running"
        run.started_at = run.started_at or datetime.utcnow()
        run.error_message = ""
        db.commit()
        existing = list(db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)).all())
        done_ids = {row.case_id for row in existing}
        passed = sum(1 for row in existing if row.status == "passed")
        failed = sum(1 for row in existing if row.status not in {"passed", "skipped"})
        skipped = sum(1 for row in existing if row.status == "skipped")
        tokens = sum(int(row.tokens or 0) for row in existing)
        latency = sum(int(row.latency_ms or 0) for row in existing)
        for case in cases:
            db.refresh(run)
            if run.status == "cancelled":
                break
            if case.id in done_ids:
                continue
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
    reply, mode, tool_spans, usage = generate_chat_reply(agent, model, history, db, session_id=session_id)
    latency_ms = max(1, int((datetime.utcnow() - started).total_seconds() * 1000))
    spans = build_debug_spans(agent, model, mode, tool_spans, latency_ms, db)
    trace_id = new_trace_id()
    trace = Trace(
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
    )
    if hasattr(agent, "tenant_id"):
        trace.tenant_id = agent.tenant_id
    if hasattr(agent, "owner_id"):
        trace.owner_id = agent.owner_id
    db.add(trace)
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


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return int(ordered[index])


def _histogram(latencies: list[int]) -> list[dict[str, Any]]:
    counts = [0] * len(PERF_BUCKETS)
    for latency in latencies:
        placed = False
        for index, (limit, _label) in enumerate(PERF_BUCKETS):
            if limit is None or latency < limit:
                counts[index] += 1
                placed = True
                break
        if not placed:
            counts[-1] += 1
    total = max(1, len(latencies))
    return [
        {"label": label, "count": count, "percent": round(count / total * 100, 1)}
        for (_limit, label), count in zip(PERF_BUCKETS, counts)
    ]


def _perf_one(agent_id: int, model_id: int, prompt: str, session_id: str) -> dict[str, Any]:
    from app.main import generate_chat_reply

    started = time.perf_counter()
    try:
        with SessionLocal() as db:
            agent = db.get(Agent, agent_id)
            model = db.get(ModelConfig, model_id)
            if not agent or not model:
                raise RuntimeError("Agent 或模型不存在")
            reply, mode, _spans, usage = generate_chat_reply(
                agent, model, [{"role": "user", "content": prompt}], db, session_id=session_id
            )
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        ok = mode != "error"
        return {
            "status": "passed" if ok else "error",
            "actual": reply,
            "reason": f"请求成功 {latency_ms} ms" if ok else (reply or mode)[:160],
            "latency_ms": latency_ms,
            "tokens": int((usage or {}).get("total_tokens") or (len(prompt) + len(reply or ""))),
            "error": "" if ok else (reply or mode)[:160],
        }
    except Exception as exc:
        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        return {
            "status": "error",
            "actual": str(exc)[:500],
            "reason": f"请求失败：{exc}"[:160],
            "latency_ms": latency_ms,
            "tokens": 0,
            "error": str(exc)[:160],
        }


def execute_perf_run(run_id: int) -> EvaluationRun:
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
        cases = resolve_cases(db, run.dataset_id or 0, run.case_ids or [])
        if not cases:
            run.status = "failed"
            run.error_message = "没有可压测的用例"
            run.finished_at = datetime.utcnow()
            db.commit()
            return run
        config = dict(run.metrics or {})
        total = int(config.get("requests") or run.total or len(cases))
        concurrency = max(1, min(8, int(config.get("concurrency") or 1)))
        total = max(1, min(80, total))
        run.status = "running"
        run.started_at = run.started_at or datetime.utcnow()
        run.total = total
        run.cases = total
        run.error_message = ""
        db.commit()
        agent_id = agent.id
        model_id = model.id
        prompts = [
            {"case_id": cases[index % len(cases)].id, "case_key": cases[index % len(cases)].case_key or str(cases[index % len(cases)].id), "input": cases[index % len(cases)].input}
            for index in range(total)
        ]

    wall_started = time.perf_counter()
    samples: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(_perf_one, agent_id, model_id, item["input"], f"perf_{run_id}_{index}"): index
            for index, item in enumerate(prompts)
        }
        for future in as_completed(futures):
            index = futures[future]
            sample = future.result()
            sample["index"] = index
            sample["case_id"] = prompts[index]["case_id"]
            sample["case_key"] = f"{prompts[index]['case_key']}#{index + 1}"
            sample["input"] = prompts[index]["input"]
            samples.append(sample)

    samples.sort(key=lambda item: int(item.get("index") or 0))
    duration_ms = max(1, int((time.perf_counter() - wall_started) * 1000))
    latencies = [int(item["latency_ms"]) for item in samples]
    passed = sum(1 for item in samples if item["status"] == "passed")
    failed = len(samples) - passed
    tokens = sum(int(item["tokens"] or 0) for item in samples)
    metrics = {
        "concurrency": concurrency,
        "requests": len(samples),
        "success": passed,
        "errors": failed,
        "qps": round(len(samples) / (duration_ms / 1000), 2),
        "latency_min_ms": min(latencies) if latencies else 0,
        "latency_avg_ms": int(sum(latencies) / max(1, len(latencies))),
        "latency_p50_ms": _percentile(latencies, 50),
        "latency_p95_ms": _percentile(latencies, 95),
        "latency_p99_ms": _percentile(latencies, 99),
        "latency_max_ms": max(latencies) if latencies else 0,
        "total_tokens": tokens,
        "tokens_per_sec": round(tokens / (duration_ms / 1000), 1),
        "duration_ms": duration_ms,
        "histogram": _histogram(latencies),
    }

    with SessionLocal() as db:
        run = db.get(EvaluationRun, run_id)
        if not run:
            raise ValueError("Evaluation not found")
        for item in db.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)).all():
            db.delete(item)
        db.flush()
        for item in samples:
            db.add(EvaluationResult(
                run_id=run.id,
                case_id=item["case_id"],
                case_key=item["case_key"],
                status=item["status"],
                score=1 if item["status"] == "passed" else 0,
                input=item["input"],
                expected="",
                actual=item["actual"],
                reason=item["reason"],
                latency_ms=item["latency_ms"],
                tokens=item["tokens"],
                trace_id="",
                session_id=f"perf_{run.id}_{item['index']}",
                error=item["error"],
                tenant_id=run.tenant_id,
                owner_id=run.owner_id,
            ))
        run.passed = passed
        run.failed = failed
        run.skipped = 0
        run.total = len(samples)
        run.cases = len(samples)
        run.total_tokens = tokens
        run.avg_latency_ms = metrics["latency_avg_ms"]
        run.score = round(passed / max(1, len(samples)) * 100, 1)
        run.metrics = metrics
        if run.status != "cancelled":
            run.status = "completed"
        run.finished_at = datetime.utcnow()
        db.commit()
        db.refresh(run)
        return run


def dump_run(run: EvaluationRun, *, judge_name: str = "") -> dict[str, Any]:
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
        "judge_model_name": judge_name,
        "mode": run.mode,
        "scorer": run.scorer,
        "scorer_label": {
            "contains": "包含匹配",
            "exact": "完全匹配",
            "regex": "正则",
            "llm": "LLM 判分",
            "perf": "性能指标",
        }.get(run.scorer, run.scorer),
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
        "metrics": run.metrics or {},
        "error_message": run.error_message or "",
        "progress": progress,
        "judged": judged,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
