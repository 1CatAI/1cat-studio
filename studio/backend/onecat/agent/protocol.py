# SPDX-License-Identifier: LicenseRef-1Cat-Community-1.0
"""The bounded Responses subset used by our pinned Codex, over vLLM tool calls.

No tool executes here. Codex owns the agent loop and executes in the project
sandbox. Unknown protocol features fail explicitly instead of losing context.
"""

from __future__ import annotations

import json
import time
import uuid


def tool_catalog(tools: list) -> tuple[list, dict]:
    definitions, mapping = [], {}

    def add(tool, namespace=None):
        kind = tool.get("type")
        if kind == "namespace":
            for child in tool.get("tools", []):
                add(child, tool["name"])
            return
        if kind not in {"function", "custom"}:
            raise ValueError(f"Unsupported Codex tool type: {kind}")
        name = tool["name"]
        flat = f"{namespace}__{name}" if namespace else name
        if flat in mapping or len(flat) > 64:
            raise ValueError(f"Ambiguous or oversized tool name: {flat}")
        mapping[flat] = {"name": name, "namespace": namespace, "custom": kind == "custom"}
        schema = tool.get("parameters", {"type": "object", "properties": {}})
        description = tool.get("description", "")
        if kind == "custom":
            schema = {
                "type": "object",
                "properties": {"input": {"type": "string"}},
                "required": ["input"],
                "additionalProperties": False,
            }
            description += "\nReturn the complete raw tool input in the input string."
        definitions.append(
            {
                "type": "function",
                "function": {"name": flat, "description": description, "parameters": schema},
            }
        )

    for tool in tools:
        add(tool)
    return definitions, mapping


def convert_input(body: dict) -> tuple[dict, dict]:
    if body.get("previous_response_id"):
        raise ValueError("This provider requires the full input history, not previous_response_id")
    tools, mapping = tool_catalog(body.get("tools", []))
    messages = []
    instructions = []
    if body.get("instructions"):
        instructions.append(body["instructions"])
    items = body.get("input", [])
    if isinstance(items, str):
        items = [{"role": "user", "content": items}]

    def assistant():
        if not messages or messages[-1]["role"] != "assistant":
            messages.append({"role": "assistant", "content": ""})
        return messages[-1]

    for item in items:
        kind = item.get("type", "message")
        if kind == "message":
            role = item.get("role", "user")
            role = "system" if role == "developer" else role
            if role not in {"user", "assistant", "system"}:
                raise ValueError(f"Unsupported message role: {role}")
            content = item.get("content", "")
            if isinstance(content, list):
                parts = []
                for part in content:
                    if part.get("type") not in {"input_text", "output_text", "text"}:
                        raise ValueError(
                            "Agent currently accepts text and project files; this content type is unsupported"
                        )
                    parts.append(part.get("text", ""))
                content = "\n".join(parts)
            if role == "system":
                # Codex can append developer/environment updates between turns.
                # Qwen's chat template accepts system instructions only at the
                # beginning. Preserve their order in one leading system message;
                # user text and tool output never enter this instruction channel.
                instructions.append(content)
            elif role == "assistant":
                target = assistant()
                target["content"] += ("\n" if target["content"] else "") + content
            else:
                messages.append({"role": role, "content": content})
        elif kind in {"function_call", "custom_tool_call"}:
            namespace = item.get("namespace")
            name = f"{namespace}__{item['name']}" if namespace else item["name"]
            arguments = (
                item.get("arguments", "{}")
                if kind == "function_call"
                else json.dumps({"input": item["input"]})
            )
            assistant().setdefault("tool_calls", []).append(
                {
                    "id": item["call_id"],
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
        elif kind in {"function_call_output", "custom_tool_call_output"}:
            output = item.get("output", "")
            if isinstance(output, list):
                if any(p.get("type") not in {"input_text", "text"} for p in output):
                    raise ValueError("Unsupported non-text tool output")
                output = "\n".join(p.get("text", "") for p in output)
            messages.append({"role": "tool", "tool_call_id": item["call_id"], "content": output})
        elif kind == "reasoning":
            # Internal reasoning is not re-injected into the chat template.
            continue
        else:
            raise ValueError(f"Unsupported Responses input item: {kind}")
    if instructions:
        messages.insert(0, {"role": "system", "content": "\n\n".join(instructions)})
    result = {
        "model": body["model"],
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        choice = body.get("tool_choice", "auto")
        if isinstance(choice, dict):
            name = choice.get("name")
            if choice.get("namespace"):
                name = f"{choice['namespace']}__{name}"
            if choice.get("type") not in {"function", "custom"} or name not in mapping:
                raise ValueError("Unsupported or unknown tool_choice")
            choice = {"type": "function", "function": {"name": name}}
        elif choice not in {"auto", "none", "required"}:
            raise ValueError("Unsupported tool_choice")
        result.update(tools=tools, tool_choice=choice, parallel_tool_calls=False)
    for key in ("temperature", "top_p"):
        if body.get(key) is not None:
            result[key] = body[key]
    if body.get("max_output_tokens") is not None:
        result["max_tokens"] = body["max_output_tokens"]
    return result, mapping


class ResponseStream:
    def __init__(self, model, mapping):
        self.response = {
            "id": "resp_" + uuid.uuid4().hex,
            "object": "response",
            "created_at": int(time.time()),
            "status": "in_progress",
            "model": model,
            "output": [],
            "error": None,
            "incomplete_details": None,
        }
        self.mapping = mapping
        self.sequence = 0
        self.text_item = None
        self.text_index = 0
        self.reasoning_item = None
        self.reasoning_index = 0
        self.calls = {}
        self.usage = {}
        self.finish_reason = None
        self.ended = False

    def event(self, kind, **fields):
        data = {"type": kind, "sequence_number": self.sequence, **fields}
        self.sequence += 1
        return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()

    def start(self):
        yield self.event("response.created", response=self.response)
        yield self.event("response.in_progress", response=self.response)

    def ingest(self, chunk):
        if chunk.get("error"):
            raise ValueError(str(chunk["error"]))
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if thought:
                if self.reasoning_item is None:
                    self.reasoning_index = len(self.response["output"])
                    self.reasoning_item = {
                        "type": "reasoning",
                        "id": "rs_" + uuid.uuid4().hex,
                        "summary": [],
                    }
                    self.response["output"].append(self.reasoning_item)
                    yield self.event(
                        "response.output_item.added",
                        output_index=self.reasoning_index,
                        item=self.reasoning_item,
                    )
                    self.reasoning_item["summary"] = [{"type": "summary_text", "text": ""}]
                    yield self.event(
                        "response.reasoning_summary_part.added",
                        item_id=self.reasoning_item["id"],
                        output_index=self.reasoning_index,
                        summary_index=0,
                        part=self.reasoning_item["summary"][0],
                    )
                self.reasoning_item["summary"][0]["text"] += thought
                yield self.event(
                    "response.reasoning_summary_text.delta",
                    item_id=self.reasoning_item["id"],
                    output_index=self.reasoning_index,
                    summary_index=0,
                    delta=thought,
                )
            if text := delta.get("content"):
                if self.text_item is None:
                    self.text_item = {
                        "type": "message",
                        "id": "msg_" + uuid.uuid4().hex,
                        "role": "assistant",
                        "status": "in_progress",
                        "content": [],
                    }
                    self.text_index = len(self.response["output"])
                    self.response["output"].append(self.text_item)
                    yield self.event(
                        "response.output_item.added",
                        output_index=self.text_index,
                        item=self.text_item,
                    )
                    self.text_item["content"] = [
                        {"type": "output_text", "text": "", "annotations": []}
                    ]
                    yield self.event(
                        "response.content_part.added",
                        item_id=self.text_item["id"],
                        output_index=self.text_index,
                        content_index=0,
                        part=self.text_item["content"][0],
                    )
                self.text_item["content"][0]["text"] += text
                yield self.event(
                    "response.output_text.delta",
                    item_id=self.text_item["id"],
                    output_index=self.text_index,
                    content_index=0,
                    delta=text,
                )
            for call in delta.get("tool_calls", []):
                target = self.calls.setdefault(
                    call["index"], {"id": None, "name": "", "arguments": ""}
                )
                if call.get("id"):
                    target["id"] = call["id"]
                function = call.get("function", {})
                target["name"] += function.get("name") or ""
                target["arguments"] += function.get("arguments") or ""
                if len(target["arguments"]) > 2 * 1024 * 1024:
                    raise ValueError("Tool arguments exceed the 2 MiB limit")
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]

    def complete(self):
        if not self.finish_reason:
            raise ValueError("Model stream disconnected before a finish reason")
        if self.reasoning_item:
            yield self.event(
                "response.reasoning_summary_text.done",
                item_id=self.reasoning_item["id"],
                output_index=self.reasoning_index,
                summary_index=0,
                text=self.reasoning_item["summary"][0]["text"],
            )
            yield self.event(
                "response.reasoning_summary_part.done",
                item_id=self.reasoning_item["id"],
                output_index=self.reasoning_index,
                summary_index=0,
                part=self.reasoning_item["summary"][0],
            )
            yield self.event(
                "response.output_item.done",
                output_index=self.reasoning_index,
                item=self.reasoning_item,
            )
        if self.text_item:
            self.text_item["status"] = "completed"
            yield self.event(
                "response.output_text.done",
                item_id=self.text_item["id"],
                output_index=self.text_index,
                content_index=0,
                text=self.text_item["content"][0]["text"],
            )
            yield self.event(
                "response.content_part.done",
                item_id=self.text_item["id"],
                output_index=self.text_index,
                content_index=0,
                part=self.text_item["content"][0],
            )
            yield self.event(
                "response.output_item.done", output_index=self.text_index, item=self.text_item
            )
        # Truncated arguments must never execute, even if their prefix parses.
        if self.finish_reason not in {"stop", "tool_calls", "length"}:
            raise ValueError(f"Model did not complete successfully: {self.finish_reason}")
        if self.finish_reason != "length":
            for call in self.calls.values():
                spec = self.mapping.get(call["name"])
                if not spec:
                    raise ValueError(f"Model returned an unknown tool: {call['name']}")
                args = json.loads(call["arguments"])
                if not isinstance(args, dict):
                    raise TypeError("Tool arguments must be a JSON object")
                item = {
                    "type": "custom_tool_call" if spec["custom"] else "function_call",
                    "id": "fc_" + uuid.uuid4().hex,
                    "call_id": call["id"] or "call_" + uuid.uuid4().hex,
                    "name": spec["name"],
                    "status": "completed",
                }
                if spec["namespace"]:
                    item["namespace"] = spec["namespace"]
                if spec["custom"]:
                    if not isinstance(args.get("input"), str):
                        raise ValueError("Custom tool requires an input string")
                    item["input"] = args["input"]
                else:
                    item["arguments"] = call["arguments"]
                index = len(self.response["output"])
                self.response["output"].append(item)
                yield self.event(
                    "response.output_item.added",
                    output_index=index,
                    item={**item, "status": "in_progress"},
                )
                yield self.event("response.output_item.done", output_index=index, item=item)
        if self.usage:
            prompt = self.usage.get("prompt_tokens", 0)
            completion = self.usage.get("completion_tokens", 0)
            cached = (self.usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
            self.response["usage"] = {
                "input_tokens": prompt,
                "output_tokens": completion,
                "total_tokens": prompt + completion,
                "input_tokens_details": {"cached_tokens": cached},
                "output_tokens_details": {
                    "reasoning_tokens": (self.usage.get("completion_tokens_details") or {}).get(
                        "reasoning_tokens", 0
                    )
                },
            }
        self.response["status"] = "incomplete" if self.finish_reason == "length" else "completed"
        if self.finish_reason == "length":
            self.response["incomplete_details"] = {"reason": "max_output_tokens"}
        self.ended = True
        yield self.event("response." + self.response["status"], response=self.response)

    def failure(self, error):
        self.response.update(
            status="failed", error={"code": "local_model_error", "message": str(error)[:2000]}
        )
        return self.event("response.failed", response=self.response)
