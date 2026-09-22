from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any


LOG = logging.getLogger(__name__)
MAX_MODEL_BYTES = 32_000
MIN_COMPRESS_BYTES = 4_000
MAX_HEADROOM_INPUT_BYTES = 2_000_000
LOG_TOOLS = {"terminal_exec", "terminal_read", "task_run", "agent_delegate"}
LOG_FIELDS = {"stdout", "stderr", "text"}


def encoded_size(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8"))


def estimated_tokens(value: Any) -> int:
    return (encoded_size(value) + 3) // 4


def _clip(value: str, limit: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= limit:
        return value
    marker = f"\n[... at least {len(raw) - limit} bytes omitted from model context ...]\n"
    room = max(0, limit - len(marker.encode("utf-8")))
    head = room * 2 // 3
    tail = room - head
    return raw[:head].decode("utf-8", errors="ignore") + marker + (raw[-tail:].decode("utf-8", errors="ignore") if tail else "")


def _bound(value: Any, *, string_limit: int, list_limit: int, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _clip(value, string_limit)
    if depth >= 12:
        return "[nested content omitted from model context]"
    if isinstance(value, list):
        if len(value) <= list_limit:
            return [_bound(item, string_limit=string_limit, list_limit=list_limit, depth=depth + 1) for item in value]
        front = max(1, list_limit * 3 // 4)
        back = max(0, list_limit - front)
        selected = value[:front]
        if back:
            selected += [{"_omitted_items": len(value) - list_limit}] + value[-back:]
        else:
            selected += [{"_omitted_items": len(value) - list_limit}]
        return [_bound(item, string_limit=string_limit, list_limit=list_limit, depth=depth + 1) for item in selected]
    if isinstance(value, dict):
        if value.get("type") == "image" and isinstance(value.get("data"), str):
            return {**{k: _bound(v, string_limit=string_limit, list_limit=list_limit, depth=depth + 1)
                       for k, v in value.items() if k != "data"},
                    "data": f"[base64 image omitted from model context: {len(value['data'])} characters]"}
        return {k: _bound(v, string_limit=string_limit, list_limit=list_limit, depth=depth + 1)
                for k, v in value.items()}
    return value


@dataclass(frozen=True)
class OptimizedOutput:
    result: dict[str, Any]
    text: str
    raw_tokens: int
    optimized: bool


class OutputOptimizer:
    """Reduce only MCP results sent to the model; desktop tool results stay raw."""

    def __init__(self, max_model_bytes: int = MAX_MODEL_BYTES):
        self.max_model_bytes = max_model_bytes
        self._log_compressor = None
        self._json_compressor = None
        try:
            from headroom.config import CCRConfig
            from headroom.transforms.log_compressor import LogCompressor, LogCompressorConfig
            from headroom.transforms.smart_crusher import SmartCrusher, SmartCrusherConfig

            self._log_compressor = LogCompressor(LogCompressorConfig(enable_ccr=False, max_total_lines=100))
            self._json_compressor = SmartCrusher(
                config=SmartCrusherConfig(
                    min_tokens_to_crush=200,
                    max_items_after_crush=30,
                    audit_safe=True,
                    protected_patterns=["error", "failed", "failure", "warning"],
                ),
                ccr_config=CCRConfig(enabled=False, inject_retrieval_marker=False, inject_tool=False),
                with_compaction=False,
            )
        except (ImportError, OSError, RuntimeError) as exc:
            LOG.warning("Headroom unavailable; using DevMesh output bounds: %s", exc)

    def close(self) -> None:
        self._log_compressor = None
        self._json_compressor = None

    def _headroom_logs(self, tool_name: str, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if tool_name not in LOG_TOOLS or self._log_compressor is None:
            return data, False
        changed = False
        result = dict(data)
        for key in LOG_FIELDS:
            value = result.get(key)
            value_bytes = len(value.encode("utf-8")) if isinstance(value, str) else 0
            if not isinstance(value, str) or not MIN_COMPRESS_BYTES <= value_bytes <= MAX_HEADROOM_INPUT_BYTES:
                continue
            try:
                compressed = self._log_compressor.compress(value).compressed
            except Exception as exc:
                LOG.warning("Headroom log compression failed for %s.%s: %s", tool_name, key, exc)
                continue
            if "<<ccr:" not in compressed and "<headroom:" not in compressed and len(compressed.encode("utf-8")) < value_bytes:
                result[key] = compressed
                changed = True
        return result, changed

    def _headroom_json(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        data_size = encoded_size(data)
        if self._json_compressor is None or not MIN_COMPRESS_BYTES <= data_size <= MAX_HEADROOM_INPUT_BYTES:
            return data, False
        if not any(isinstance(value, list) for value in data.values()):
            return data, False
        try:
            compressed = self._json_compressor.crush(
                json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
            ).compressed
            if "<<ccr:" in compressed or "<headroom:" in compressed:
                return data, False
            parsed = json.loads(compressed)
            if isinstance(parsed, dict) and encoded_size(parsed) < encoded_size(data):
                return parsed, True
        except Exception as exc:
            LOG.warning("Headroom JSON compression failed: %s", exc)
        return data, False

    def optimize(self, tool_name: str, data: dict[str, Any], call_id: int | None = None) -> OptimizedOutput:
        raw_tokens = estimated_tokens(data)
        current: dict[str, Any] = data
        methods: list[str] = []
        if encoded_size(data) >= MIN_COMPRESS_BYTES:
            current, used = self._headroom_logs(tool_name, current)
            if used:
                methods.append("headroom_logs")
            if encoded_size(current) > self.max_model_bytes:
                current, used = self._headroom_json(current)
                if used:
                    methods.append("headroom_json")

        # The complete MCP envelope also contains metadata and a text block.
        # Reserve space for those fields, then tighten recursively if needed.
        payload_budget = max(1024, self.max_model_bytes - 2500)
        bounded_source = current
        for string_limit, list_limit in ((12_000, 80), (6_000, 40), (3_000, 20), (1_000, 8), (400, 4)):
            if encoded_size(current) <= payload_budget:
                break
            current = _bound(bounded_source, string_limit=string_limit, list_limit=list_limit)
            if "bounded" not in methods:
                methods.append("bounded")

        if encoded_size(current) > payload_budget:
            # A huge flat object or an exceptionally long key can still exceed
            # the budget. Keep operational status and a small preview.
            current = {
                "status": data.get("status"),
                "returncode": data.get("returncode"),
                "isError": data.get("isError"),
                "preview": _clip(json.dumps(current, ensure_ascii=False, default=str), min(8_000, payload_budget // 2)),
            }
            if "bounded" not in methods:
                methods.append("bounded")

        optimized = bool(methods) or current != data
        if optimized:
            current = dict(current)
            current["_output_optimization"] = {
                "raw_estimated_tokens": raw_tokens,
                "delivered_estimated_tokens": estimated_tokens(current),
                "method": methods,
                "recorded_result_in_live_calls": call_id is not None,
                **({"call_id": call_id} if call_id is not None else {}),
            }
        if not optimized and encoded_size(current) <= 1000:
            text = json.dumps(current, ensure_ascii=False, separators=(",", ":"), default=str)
        else:
            status = "error" if data.get("isError") or data.get("error") else "ok"
            text = f"{tool_name}: {status}. Structured result follows."
            if optimized:
                text += " Large output was reduced for context."
                if call_id is not None:
                    text += " Its recorded result can be inspected in DevMesh Live Calls."
        return OptimizedOutput(current, text, raw_tokens, optimized)
