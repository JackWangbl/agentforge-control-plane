from __future__ import annotations

import json
import re
from typing import Any

from app.services.agentscope_adapter import complete_chat

SCORERS = ("contains", "exact", "regex", "llm")

JUDGE_PROMPT = """你是评测裁判。根据「期望答案」判断「实际输出」是否达标。
只返回 JSON，不要 markdown，不要解释：
{"passed": true或false, "reason": "不超过40字的原因"}
宽松原则：意思对齐即可，不必逐字相同；若期望为空，只要实际输出完整、切题就通过。"""


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", (value or "")).casefold()


def score_case(scorer: str, expected: str, actual: str) -> dict[str, Any]:
    method = (scorer or "contains").lower()
    if method not in SCORERS:
        method = "contains"
    expect = expected or ""
    output = actual or ""
    if method != "llm" and not expect.strip():
        return {"status": "skipped", "score": 0, "reason": "没有期望答案，仅记录输出"}
    try:
        if method == "exact":
            passed = normalize_text(output) == normalize_text(expect)
            reason = "完全匹配" if passed else "输出与期望不完全一致"
        elif method == "regex":
            passed = bool(re.search(expect, output, re.S | re.I))
            reason = "正则命中" if passed else "正则未命中"
        elif method == "contains":
            passed = normalize_text(expect) in normalize_text(output)
            reason = "包含期望内容" if passed else "未包含期望内容"
        else:
            return {"status": "failed", "score": 0, "reason": "LLM 判分需要调用 score_with_llm"}
    except re.error as exc:
        return {"status": "error", "score": 0, "reason": f"正则无效：{exc}"}
    return {"status": "passed" if passed else "failed", "score": 1 if passed else 0, "reason": reason}


def score_with_llm(
    *,
    expected: str,
    actual: str,
    input_text: str,
    model_id: str,
    base_url: str,
    api_key: str,
    temperature: float = 0,
) -> dict[str, Any]:
    user = f"用户问题：{input_text}\n期望答案：{expected or '（未提供，请按是否切题判断）'}\n实际输出：{actual}"
    result = complete_chat(
        model_id=model_id,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        system_prompt=JUDGE_PROMPT,
        messages=[{"role": "user", "content": user}],
    )
    raw = (result.get("content") or "").strip()
    parsed = _parse_judge(raw)
    if parsed is None:
        return {"status": "error", "score": 0, "reason": f"裁判返回无法解析：{raw[:160]}"}
    passed = bool(parsed.get("passed"))
    return {
        "status": "passed" if passed else "failed",
        "score": 1 if passed else 0,
        "reason": str(parsed.get("reason") or ("裁判判定通过" if passed else "裁判判定未通过")),
    }


def _parse_judge(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S)
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "passed" in data:
            return data
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                data = json.loads(match.group(0))
                if isinstance(data, dict) and "passed" in data:
                    return data
            except json.JSONDecodeError:
                return None
    return None
