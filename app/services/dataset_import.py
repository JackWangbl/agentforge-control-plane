from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

INPUT_ALIASES = {"input", "问题", "query", "question", "prompt", "user", "用户"}
EXPECTED_ALIASES = {"expected", "期望", "answer", "output", "label", "reference", "标准答案"}
ID_ALIASES = {"id", "case_id", "key", "case_key", "编号"}
TAG_ALIASES = {"tags", "tag", "标签"}

MAX_CASES = 2000
MAX_BYTES = 5 * 1024 * 1024


class ImportErrorDetail(ValueError):
    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.errors = errors or []


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _norm_key(key: str) -> str:
    return re.sub(r"\s+", "", (key or "").strip().lower())


def _pick(row: dict[str, Any], aliases: set[str], default: str = "") -> str:
    mapped = {_norm_key(k): v for k, v in row.items()}
    for alias in aliases:
        if alias in mapped and mapped[alias] not in (None, ""):
            return str(mapped[alias]).strip()
    return default


def _tags(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [part.strip() for part in str(value).replace("，", ",").split(",") if part.strip()]


def _row_to_case(row: dict[str, Any], index: int) -> dict[str, Any]:
    text = _pick(row, INPUT_ALIASES)
    if not text:
        raise ImportErrorDetail(f"第 {index} 行缺少 input / 问题")
    extra = {}
    known = INPUT_ALIASES | EXPECTED_ALIASES | ID_ALIASES | TAG_ALIASES
    for key, value in row.items():
        if _norm_key(key) not in known and value not in (None, ""):
            extra[str(key)] = value
    return {
        "case_key": _pick(row, ID_ALIASES, str(index)),
        "input": text,
        "expected": _pick(row, EXPECTED_ALIASES),
        "tags": _tags(_pick(row, TAG_ALIASES)),
        "extra": extra,
    }


def parse_dataset_text(text: str, filename: str = "") -> dict[str, Any]:
    name = (filename or "").lower()
    stripped = text.lstrip()
    errors: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    if name.endswith(".jsonl") or (stripped.startswith("{") and "\n{" in stripped):
        for index, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("JSONL 每行必须是对象")
                cases.append(_row_to_case(row, index))
            except Exception as exc:
                errors.append({"line": index, "reason": str(exc)})
    elif stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ImportErrorDetail(f"JSON 无法解析：{exc}") from exc
        rows = payload.get("cases") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ImportErrorDetail("JSON 需要是数组，或带 cases 数组的对象")
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                errors.append({"line": index, "reason": "条目必须是对象"})
                continue
            try:
                cases.append(_row_to_case(row, index))
            except Exception as exc:
                errors.append({"line": index, "reason": str(exc)})
    else:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise ImportErrorDetail("CSV 缺少表头")
        for index, row in enumerate(reader, start=2):
            if not any(str(value or "").strip() for value in row.values()):
                continue
            try:
                cases.append(_row_to_case(row, index))
            except Exception as exc:
                errors.append({"line": index, "reason": str(exc)})
    if not cases and errors:
        raise ImportErrorDetail("没有解析到有效用例", errors)
    if not cases:
        raise ImportErrorDetail("文件是空的")
    if len(cases) > MAX_CASES:
        raise ImportErrorDetail(f"单次最多导入 {MAX_CASES} 条，当前 {len(cases)} 条")
    return {"cases": cases, "errors": errors, "count": len(cases)}


def parse_dataset_bytes(raw: bytes, filename: str = "") -> dict[str, Any]:
    if len(raw) > MAX_BYTES:
        raise ImportErrorDetail(f"文件不能超过 {MAX_BYTES // 1024 // 1024}MB")
    return parse_dataset_text(_decode(raw), filename)
