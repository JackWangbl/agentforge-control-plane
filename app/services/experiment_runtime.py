"""Sticky A/B assignment and experiment metrics."""
from __future__ import annotations

import hashlib
import json
import random
from datetime import datetime
from typing import Any, Optional
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.access.auth import CurrentUser
from app.access.kinds import ResourceKind
from app.access.scope import stamp_owner
from app.access.service import access_service
from app.models import Agent, Dataset, DatasetCase, Experiment, ExperimentAssignment, ExperimentEvent, ExperimentVariant, ModelConfig

VARIANT_KEYS = ("A", "B", "C", "D", "E", "F")
STRATEGIES = {
    "user_hash": {"unit": "user", "algo": "hash", "label": "按用户 ID 哈希", "hint": "同一用户始终分到同一 Agent"},
    "session_hash": {"unit": "session", "algo": "hash", "label": "按 Session ID 哈希", "hint": "同一会话始终同一 Agent，换会话可能换 Agent"},
    "user_first": {"unit": "user", "algo": "first", "label": "按用户首次进组", "hint": "用户第一次按权重随机进组，之后一直粘滞"},
    "random": {"unit": "request", "algo": "random", "label": "每次随机", "hint": "每次请求独立抽取，不保证同一用户同一 Agent"},
}


def normalize_strategy(value: Optional[str] = None, assignment_unit: Optional[str] = None) -> str:
    raw = (value or "").strip().lower()
    if raw in STRATEGIES:
        return raw
    if raw == "user" or (assignment_unit or "").strip().lower() == "user":
        return "user_hash"
    return "session_hash"


def apply_strategy(row: Experiment, strategy: Optional[str] = None) -> str:
    resolved = normalize_strategy(strategy or getattr(row, "assignment_strategy", None), getattr(row, "assignment_unit", None))
    row.assignment_strategy = resolved
    row.assignment_unit = "user" if STRATEGIES[resolved]["unit"] == "user" else "session"
    return resolved


def _unit_key(experiment: Experiment, *, session_id: str, user_key: str) -> str:
    strategy = normalize_strategy(getattr(experiment, "assignment_strategy", None), experiment.assignment_unit)
    unit = STRATEGIES[strategy]["unit"]
    if unit == "request":
        return f"req:{uuid4().hex}"
    if unit == "user":
        return (user_key or session_id or "anonymous").strip() or "anonymous"
    return (session_id or user_key or "anonymous").strip() or "anonymous"


def _bucket(seed: str, modulo: int) -> int:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % max(1, modulo)


def list_variants(db: Session, experiment_id: int) -> list[ExperimentVariant]:
    return list(
        db.scalars(
            select(ExperimentVariant).where(ExperimentVariant.experiment_id == experiment_id).order_by(ExperimentVariant.id.asc())
        ).all()
    )


def validate_variants(items: list[Any], db: Session, user: CurrentUser) -> list[dict[str, Any]]:
    if len(items) < 2:
        raise HTTPException(400, "至少需要两个分流变体")
    if len(items) > 6:
        raise HTTPException(400, "一次实验最多 6 个变体")
    seen: set[str] = set()
    cleaned: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        key = (getattr(item, "key", None) or VARIANT_KEYS[index]).strip().upper()[:16]
        if key in seen:
            raise HTTPException(400, f"变体标识 {key} 重复")
        seen.add(key)
        agent = access_service.get_row(user, ResourceKind.AGENT, int(item.agent_id), db)
        weight = max(1, int(getattr(item, "weight", 50) or 50))
        cleaned.append({
            "key": key,
            "name": (getattr(item, "name", None) or f"变体 {key}").strip()[:80],
            "agent_id": agent.id,
            "weight": weight,
        })
    return cleaned


def replace_variants(db: Session, experiment: Experiment, items: list[dict[str, Any]], user: CurrentUser) -> None:
    for row in list_variants(db, experiment.id):
        db.delete(row)
    db.flush()
    for item in items:
        db.add(stamp_owner(ExperimentVariant(experiment_id=experiment.id, **item), user))
    db.flush()


def dump_variant(row: ExperimentVariant, db: Session) -> dict[str, Any]:
    agent = db.get(Agent, row.agent_id)
    return {
        "id": row.id,
        "experiment_id": row.experiment_id,
        "key": row.key,
        "name": row.name,
        "agent_id": row.agent_id,
        "agent_name": agent.name if agent else "",
        "weight": row.weight,
    }


def dump_experiment(row: Experiment, db: Session) -> dict[str, Any]:
    strategy = normalize_strategy(getattr(row, "assignment_strategy", None), row.assignment_unit)
    variants = [dump_variant(item, db) for item in list_variants(db, row.id)]
    weight_sum = sum(item["weight"] for item in variants) or 1
    for item in variants:
        item["share"] = round(item["weight"] / weight_sum * 100, 1)
    return {
        "id": row.id,
        "name": row.name,
        "description": row.description or "",
        "status": row.status,
        "assignment_unit": "user" if STRATEGIES[strategy]["unit"] == "user" else "session",
        "assignment_strategy": strategy,
        "assignment_strategy_label": STRATEGIES[strategy]["label"],
        "assignment_strategy_hint": STRATEGIES[strategy]["hint"],
        "traffic_percent": row.traffic_percent or 100,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "variants": variants,
        "variant_count": len(variants),
    }


def simulate_split(experiment: Experiment, variants: list[ExperimentVariant], samples: int = 200) -> dict[str, Any]:
    traffic = max(1, min(int(experiment.traffic_percent or 100), 100))
    strategy = normalize_strategy(getattr(experiment, "assignment_strategy", None), experiment.assignment_unit)
    algo = STRATEGIES[strategy]["algo"]
    counts = {item.id: 0 for item in variants}
    holdout = 0
    for index in range(max(1, samples)):
        unit_key = f"sim:{index}"
        if algo == "random":
            gated = random.randrange(100) >= traffic
        else:
            gated = _bucket(f"gate:{experiment.id}:{unit_key}", 100) >= traffic
        if gated:
            holdout += 1
            continue
        picked = pick_weighted(variants) if algo in {"random", "first"} else pick_variant(experiment.id, unit_key, variants)
        counts[picked.id] += 1
    enrolled = max(0, samples - holdout)
    return {"samples": samples, "holdout": holdout, "enrolled": enrolled, "counts": counts}


def pick_weighted(variants: list[ExperimentVariant]) -> ExperimentVariant:
    weights = [max(1, item.weight) for item in variants]
    return random.choices(variants, weights=weights, k=1)[0]


def pick_variant(experiment_id: int, unit_key: str, variants: list[ExperimentVariant]) -> ExperimentVariant:
    total = sum(max(1, item.weight) for item in variants)
    slot = _bucket(f"{experiment_id}:{unit_key}", total)
    cursor = 0
    for item in variants:
        cursor += max(1, item.weight)
        if slot < cursor:
            return item
    return variants[-1]


def assign_unit(
    db: Session,
    experiment: Experiment,
    *,
    session_id: str,
    user_key: str,
    user: CurrentUser,
) -> dict[str, Any]:
    variants = list_variants(db, experiment.id)
    if len(variants) < 2:
        raise HTTPException(400, "实验变体不完整")
    strategy = normalize_strategy(getattr(experiment, "assignment_strategy", None), experiment.assignment_unit)
    algo = STRATEGIES[strategy]["algo"]
    unit_key = _unit_key(experiment, session_id=session_id, user_key=user_key)
    if algo != "random":
        found = db.scalar(
            select(ExperimentAssignment).where(
                ExperimentAssignment.experiment_id == experiment.id,
                ExperimentAssignment.unit_key == unit_key,
            )
        )
        if found:
            variant = db.get(ExperimentVariant, found.variant_id) if found.variant_id else None
            return _assignment_payload(experiment, found, variant, unit_key, db)

    traffic = max(1, min(int(experiment.traffic_percent or 100), 100))
    if algo == "random":
        holdout = random.randrange(100) >= traffic
    else:
        holdout = _bucket(f"gate:{experiment.id}:{unit_key}", 100) >= traffic
    if holdout:
        variant = None
    elif algo in {"random", "first"}:
        variant = pick_weighted(variants)
    else:
        variant = pick_variant(experiment.id, unit_key, variants)
    row = stamp_owner(ExperimentAssignment(
        experiment_id=experiment.id,
        variant_id=variant.id if variant else (variants[0].id),
        unit_key=unit_key,
        holdout=holdout,
    ), user)
    if holdout:
        row.variant_id = variants[0].id
    db.add(row)
    db.flush()
    if not holdout:
        db.add(stamp_owner(ExperimentEvent(
            experiment_id=experiment.id,
            variant_id=variant.id if variant else None,
            session_id=session_id or "",
            unit_key=unit_key,
            kind="assign",
            status="ok",
        ), user))
        db.flush()
    return _assignment_payload(experiment, row, None if holdout else variant, unit_key, db)


def _assignment_payload(
    experiment: Experiment,
    assignment: ExperimentAssignment,
    variant: Optional[ExperimentVariant],
    unit_key: str,
    db: Session,
) -> dict[str, Any]:
    holdout = bool(assignment.holdout)
    agent = db.get(Agent, variant.agent_id) if variant and not holdout else None
    return {
        "experiment_id": experiment.id,
        "experiment_name": experiment.name,
        "status": experiment.status,
        "assignment_strategy": normalize_strategy(getattr(experiment, "assignment_strategy", None), experiment.assignment_unit),
        "unit_key": unit_key,
        "holdout": holdout,
        "variant_id": None if holdout else (variant.id if variant else None),
        "variant_key": "" if holdout else ((variant.key if variant else "") or ""),
        "variant_name": "" if holdout else ((variant.name if variant else "") or ""),
        "agent_id": agent.id if agent else None,
        "agent_name": agent.name if agent else "",
    }


def record_run(
    db: Session,
    experiment: Experiment,
    assignment: dict[str, Any],
    *,
    session_id: str,
    status: str,
    latency_ms: int,
    tokens: int,
    user: CurrentUser,
) -> None:
    if assignment.get("holdout") or not assignment.get("variant_id"):
        return
    db.add(stamp_owner(ExperimentEvent(
        experiment_id=experiment.id,
        variant_id=int(assignment["variant_id"]),
        session_id=session_id or "",
        unit_key=str(assignment.get("unit_key") or ""),
        kind="run",
        status=status,
        latency_ms=max(0, int(latency_ms or 0)),
        tokens=max(0, int(tokens or 0)),
    ), user))


def experiment_results(db: Session, experiment: Experiment) -> dict[str, Any]:
    variants = dump_experiment(experiment, db)["variants"]
    rows = []
    for item in variants:
        assigned = db.scalar(
            select(func.count()).select_from(ExperimentAssignment).where(
                ExperimentAssignment.experiment_id == experiment.id,
                ExperimentAssignment.variant_id == item["id"],
                ExperimentAssignment.holdout == False,
            )
        ) or 0
        runs = list(
            db.scalars(
                select(ExperimentEvent).where(
                    ExperimentEvent.experiment_id == experiment.id,
                    ExperimentEvent.variant_id == item["id"],
                    ExperimentEvent.kind == "run",
                )
            ).all()
        )
        errors = sum(1 for event in runs if event.status == "error")
        previews = sum(1 for event in runs if event.status == "preview")
        ready = sum(1 for event in runs if event.status == "ready")
        latency = sum(event.latency_ms for event in runs)
        tokens = sum(event.tokens for event in runs)
        count = len(runs)
        compares = list(
            db.scalars(
                select(ExperimentEvent).where(
                    ExperimentEvent.experiment_id == experiment.id,
                    ExperimentEvent.variant_id == item["id"],
                    ExperimentEvent.kind == "compare",
                )
            ).all()
        )
        compare_passed = sum(1 for event in compares if event.status == "passed")
        compare_failed = sum(1 for event in compares if event.status == "failed")
        compare_judged = compare_passed + compare_failed
        compare_latency = sum(event.latency_ms for event in compares)
        rows.append({
            **item,
            "assignments": assigned,
            "runs": count,
            "ready": ready,
            "errors": errors,
            "previews": previews,
            "error_rate": round(errors / count * 100, 1) if count else 0,
            "avg_latency_ms": int(latency / count) if count else (int(compare_latency / len(compares)) if compares else 0),
            "avg_tokens": int(tokens / count) if count else 0,
            "total_tokens": tokens,
            "compare_runs": len(compares),
            "compare_passed": compare_passed,
            "compare_failed": compare_failed,
            "compare_pass_rate": round(compare_passed / compare_judged * 100, 1) if compare_judged else 0,
        })
    holdout = db.scalar(
        select(func.count()).select_from(ExperimentAssignment).where(
            ExperimentAssignment.experiment_id == experiment.id,
            ExperimentAssignment.holdout == True,
        )
    ) or 0
    total_assigned = sum(item["assignments"] for item in rows)
    variant_rows = list_variants(db, experiment.id)
    preview = simulate_split(experiment, variant_rows)
    for item in rows:
        item["actual_share"] = round(item["assignments"] / total_assigned * 100, 1) if total_assigned else 0
        preview_count = preview["counts"].get(item["id"], 0)
        item["preview_count"] = preview_count
        item["preview_share"] = round(preview_count / preview["enrolled"] * 100, 1) if preview["enrolled"] else 0
    ranked = [row for row in rows if row["runs"] >= 3]
    winner = ""
    compared = [row for row in rows if row["compare_runs"]]
    if compared:
        scored = [row for row in compared if (row["compare_passed"] + row["compare_failed"]) > 0]
        pool = scored or compared
        pool.sort(key=lambda row: (-row["compare_pass_rate"], row["avg_latency_ms"]))
        winner = pool[0]["key"]
    elif ranked:
        ranked.sort(key=lambda row: (row["error_rate"], row["avg_latency_ms"]))
        winner = ranked[0]["key"]
    return {
        **dump_experiment(experiment, db),
        "holdout_assignments": holdout,
        "total_assignments": total_assigned,
        "preview_samples": preview["samples"],
        "preview_holdout": preview["holdout"],
        "winner": winner,
        "variants": rows,
        "last_compare": _as_compare(experiment.last_compare),
    }


def _as_compare(value: Any) -> Optional[dict[str, Any]]:
    if not value:
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _resolve_agent_model(db: Session, agent: Agent) -> ModelConfig:
    model = db.scalar(select(ModelConfig).where(ModelConfig.name == agent.model_name))
    if not model:
        model = db.scalar(select(ModelConfig).where(ModelConfig.enabled.is_(True)).order_by(ModelConfig.id.asc()))
    if not model:
        raise HTTPException(409, f"「{agent.name}」没有可用的模型配置")
    return model


def _compare_cases(db: Session, user: CurrentUser, dataset_id: Optional[int], prompts: list[str], case_limit: int) -> list[dict[str, str]]:
    cases: list[dict[str, str]] = []
    if dataset_id:
        dataset = access_service.get_row(user, ResourceKind.DATASET, int(dataset_id), db)
        rows = list(
            db.scalars(
                select(DatasetCase)
                .where(DatasetCase.dataset_id == dataset.id, DatasetCase.enabled.is_(True))
                .order_by(DatasetCase.id.asc())
                .limit(case_limit)
            ).all()
        )
        cases.extend({
            "input": item.input,
            "expected": item.expected or "",
            "case_key": item.case_key or str(item.id),
        } for item in rows)
    for text in prompts or []:
        prompt, expected = _split_prompt(text)
        if prompt:
            cases.append({"input": prompt[:2000], "expected": expected[:2000], "case_key": f"prompt_{len(cases) + 1}"})
    cases = cases[:case_limit]
    if not cases:
        raise HTTPException(400, "请选择带用例的数据集，或填写至少一条测试问题")
    return cases


def _split_prompt(text: str) -> tuple[str, str]:
    raw = (text or "").strip()
    for sep in ("|||", "||", "｜｜"):
        if sep in raw:
            left, right = raw.split(sep, 1)
            return left.strip(), right.strip()
    return raw, ""


def run_compare(
    db: Session,
    experiment: Experiment,
    user: CurrentUser,
    *,
    dataset_id: Optional[int] = None,
    prompts: Optional[list[str]] = None,
    scorer: str = "contains",
    case_limit: int = 6,
) -> dict[str, Any]:
    from app.main import generate_chat_reply
    from app.services.eval_scorer import SCORERS, score_case

    variants = list_variants(db, experiment.id)
    if len(variants) < 2:
        raise HTTPException(400, "至少需要两个分流变体")
    method = (scorer or "contains").lower()
    if method not in SCORERS or method == "llm":
        method = "contains"
    cases = _compare_cases(db, user, dataset_id, prompts or [], case_limit)
    dataset = db.get(Dataset, int(dataset_id)) if dataset_id else None
    snapshot_cases: list[dict[str, Any]] = []
    totals: dict[int, dict[str, Any]] = {
        item.id: {"passed": 0, "failed": 0, "ok": 0, "errors": 0, "skipped": 0, "latency": 0, "runs": 0}
        for item in variants
    }

    for case in cases:
        item = {"input": case["input"], "expected": case["expected"], "case_key": case["case_key"], "variants": []}
        for variant in variants:
            agent = db.get(Agent, variant.agent_id)
            if not agent:
                raise HTTPException(409, f"变体 {variant.key} 绑定的 Agent 已不存在")
            model = _resolve_agent_model(db, agent)
            started = datetime.utcnow()
            reply, mode, _spans, usage = generate_chat_reply(agent, model, [{"role": "user", "content": case["input"]}], db)
            latency_ms = max(1, int((datetime.utcnow() - started).total_seconds() * 1000))
            tokens = int((usage or {}).get("total_tokens") or (len(case["input"]) + len(reply or "")))
            if mode == "error":
                judged = {"status": "error", "score": 0, "reason": (reply or "调用失败")[:160]}
            else:
                judged = score_case(method, case["expected"], reply or "")
            db.add(stamp_owner(ExperimentEvent(
                experiment_id=experiment.id,
                variant_id=variant.id,
                session_id=f"compare_{experiment.id}_{variant.key}_{case['case_key']}"[:80],
                unit_key=f"compare:{experiment.id}",
                kind="compare",
                status=judged["status"],
                latency_ms=latency_ms,
                tokens=tokens,
            ), user))
            totals[variant.id]["runs"] += 1
            totals[variant.id]["latency"] += latency_ms
            if judged["status"] != "error":
                totals[variant.id]["ok"] += 1
            if judged["status"] == "passed":
                totals[variant.id]["passed"] += 1
            elif judged["status"] == "failed":
                totals[variant.id]["failed"] += 1
            elif judged["status"] == "error":
                totals[variant.id]["errors"] += 1
            elif judged["status"] == "skipped":
                totals[variant.id]["skipped"] += 1
            item["variants"].append({
                "key": variant.key,
                "name": variant.name,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "reply": (reply or "")[:4000],
                "status": judged["status"],
                "score": judged.get("score") or 0,
                "reason": judged.get("reason") or "",
                "latency_ms": latency_ms,
                "tokens": tokens,
            })
        snapshot_cases.append(item)

    ranked = []
    for variant in variants:
        stat = totals[variant.id]
        judged = stat["passed"] + stat["failed"]
        ranked.append({
            "key": variant.key,
            "name": variant.name,
            "agent_name": (db.get(Agent, variant.agent_id).name if db.get(Agent, variant.agent_id) else ""),
            "passed": stat["passed"],
            "failed": stat["failed"],
            "ok": stat["ok"],
            "errors": stat["errors"],
            "skipped": stat["skipped"],
            "pass_rate": round(stat["passed"] / judged * 100, 1) if judged else None,
            "ok_rate": round(stat["ok"] / stat["runs"] * 100, 1) if stat["runs"] else 0,
            "avg_latency_ms": int(stat["latency"] / stat["runs"]) if stat["runs"] else 0,
        })
    has_expected = any((case.get("expected") or "").strip() for case in cases)
    scored = [row for row in ranked if (row["passed"] + row["failed"]) > 0]
    pool = scored or ranked
    if scored:
        pool.sort(key=lambda row: (-(row["pass_rate"] or 0), row["avg_latency_ms"]))
    else:
        pool.sort(key=lambda row: (-row["ok_rate"], row["avg_latency_ms"]))
    winner = pool[0]["key"] if pool else ""
    snapshot = {
        "ran_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "scorer": method,
        "dataset_id": dataset.id if dataset else None,
        "dataset_name": dataset.name if dataset else "",
        "has_expected": has_expected,
        "winner": winner,
        "summary": ranked,
        "cases": snapshot_cases,
    }
    experiment.last_compare = snapshot
    experiment.updated_at = datetime.utcnow()
    db.flush()
    return snapshot
