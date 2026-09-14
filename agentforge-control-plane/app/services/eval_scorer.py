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

SCORER_GUIDES = {
    "contains": {
        "label": "包含匹配",
        "rule": "去掉空白并忽略大小写后，只要实际输出里出现期望答案就算通过。",
        "pass_input": "退款多久到账？",
        "pass_expected": "三个工作日",
        "pass_actual": "预计三个工作日内到账。",
        "pass_why": "实际输出包含「三个工作日」",
        "fail_input": "退款多久到账？",
        "fail_expected": "三个工作日",
        "fail_actual": "明天就到。",
        "fail_why": "实际输出没有「三个工作日」",
        "use_when": "标准答案是关键词或短句，允许前后有客套话。",
    },
    "exact": {
        "label": "完全匹配",
        "rule": "先去掉全部空白，再忽略大小写，然后要求实际输出和期望答案完全一致。多一个字、少一个标点都会失败。",
        "pass_input": "请只回复状态码",
        "pass_expected": "OK",
        "pass_actual": "ok",
        "pass_why": "去空白并忽略大小写后，两边都是「ok」",
        "fail_input": "请只回复状态码",
        "fail_expected": "OK",
        "fail_actual": "OK，已处理完成",
        "fail_why": "多了「已处理完成」，不是完全相等",
        "use_when": "只要固定短输出，例如状态码、枚举值、是/否。",
    },
    "regex": {
        "label": "正则",
        "rule": "把期望栏当作正则表达式，在实际输出中搜索（忽略大小写，可跨行）。搜到就算通过，不必整段一模一样。",
        "pass_input": "订单 AC9001 到哪了？",
        "pass_expected": r"配送中|运输中|已发货",
        "pass_actual": "订单 AC9001 正在配送中，预计明天到达。",
        "pass_why": "正则搜到了「配送中」",
        "fail_input": "订单 AC9001 到哪了？",
        "fail_expected": r"配送中|运输中|已发货",
        "fail_actual": "已为您申请退款。",
        "fail_why": "三种合法状态都没出现，说明调错了能力",
        "use_when": "正确答案有几种说法，或要同时约束单号、状态等格式。",
    },
    "llm": {
        "label": "LLM 判分",
        "rule": "默认用「评测裁判 · Qwen-Max」（模型 qwen-max，温度 0）阅读用户问题、期望标准和实际输出，只返回 {passed, reason}。意思对齐即可。裁判必须和被测 Agent 不是同一个模型，也不要用 Agent 自己给自己打分。",
        "recommended_model": "评测裁判 · Qwen-Max",
        "recommended_model_id": "qwen-max",
        "pass_input": "这单质量太差，我要退款。",
        "pass_expected": "先确认用户要退款，再说明下一步，不要直接查物流。",
        "pass_actual": "抱歉给您添麻烦了。请确认是否为订单 AC9001 申请退款？确认后我帮您提交。",
        "pass_why": "Qwen-Max 裁判认为意思对齐：确认退款而不是查物流",
        "fail_input": "这单质量太差，我要退款。",
        "fail_expected": "先确认用户要退款，再说明下一步，不要直接查物流。",
        "fail_actual": "您的订单正在配送中，预计明天送达。",
        "fail_why": "裁判判定未达标：当成查物流，没有处理退款意图",
        "use_when": "开放式回答、客服话术，无法用关键词或正则写死。",
    },
}


def scoring_guide() -> dict[str, Any]:
    return {
        "scorers": list(SCORERS),
        "guides": [ {"id": key, **value} for key, value in SCORER_GUIDES.items() ],
    }


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
            reason = "完全匹配：去空白且忽略大小写后一致" if passed else "完全匹配失败：去空白且忽略大小写后仍不一致"
        elif method == "regex":
            passed = bool(re.search(expect, output, re.S | re.I))
            reason = "正则命中" if passed else f"正则未命中：/{expect}/"
        elif method == "contains":
            passed = normalize_text(expect) in normalize_text(output)
            reason = "包含匹配：实际输出含有期望内容" if passed else "包含匹配失败：实际输出不含期望内容"
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
