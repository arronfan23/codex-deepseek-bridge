#!/usr/bin/env python3
"""
Codex DeepSeek Bridge — 将 OpenAI Responses API 翻译为 DeepSeek Chat Completions API。
基于 codex-deepseek (yangfei4913438) 架构重写，零外部依赖。

Codex (Responses API) → 本代理 (127.0.0.1:11435) → DeepSeek Chat API
"""

import json, sys, os, time, uuid, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path

# Windows UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---- 零依赖 .env 加载 ----
def _load_dotenv():
    """加载 .env 文件到 os.environ（不覆盖已有环境变量）"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key not in os.environ:
                os.environ[key] = val

_load_dotenv()

# ========== 配置 ==========
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 11435
UPSTREAM_BASE = "https://api.deepseek.com/v1"
UPSTREAM_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEFAULT_MODEL = "deepseek-v4-pro"
VERBOSE = True

# Codex 内部模型名 → DeepSeek 模型名
MODEL_MAP = {
    "gpt-5.5": "deepseek-v4-pro",
    "gpt-5.5-low": "deepseek-v4-pro",
    "gpt-5.5-medium": "deepseek-v4-pro",
    "gpt-5.5-high": "deepseek-v4-pro",
    "gpt-5.5-xhigh": "deepseek-v4-pro",
    "gpt-5.5-minimal": "deepseek-v4-pro",
    "gpt-5.4": "deepseek-v4-pro",
    "gpt-5.4-mini": "deepseek-v4-flash",
    "gpt-5.4-nano": "deepseek-v4-flash",
    "gpt-5.3-codex": "deepseek-v4-pro",
    "gpt-5.2-codex": "deepseek-v4-pro",
    "gpt-5.1-codex": "deepseek-v4-pro",
    "gpt-5-codex": "deepseek-v4-pro",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
}
def map_model(m): return MODEL_MAP.get(m, DEFAULT_MODEL)

def log(msg):
    if VERBOSE:
        print(f"[bridge] {msg}", flush=True)


# ========== 请求翻译: Responses API → Chat Completions ==========

ROLE_MAP = {
    "developer": "system",
    "latest_reminder": "user",
}

def translate_input_to_messages(body: dict) -> list[dict]:
    """将 Responses API 的 input 转换为 Chat Completions 的 messages"""
    messages = []

    # instructions → system message
    instructions = body.get("instructions")
    if instructions:
        if isinstance(instructions, str):
            messages.append({"role": "system", "content": instructions})
        elif isinstance(instructions, list):
            for inst in instructions:
                inst_type = inst.get("type", "")
                if inst_type == "input_text":
                    messages.append({"role": "system", "content": inst.get("text", "")})

    # input items → messages
    raw_input = body.get("input", "")
    if isinstance(raw_input, str):
        messages.append({"role": "user", "content": raw_input})
    elif isinstance(raw_input, list):
        for item in raw_input:
            msg = _convert_input_item(item)
            if msg:
                messages.append(msg)

    if not messages:
        messages.append({"role": "user", "content": "Hello"})

    return messages


def _convert_input_item(item: dict) -> dict | None:
    """转换单个 Responses API input item 到 chat message"""
    item_type = item.get("type", "message")
    role = item.get("role", "user")
    role = ROLE_MAP.get(role, role)

    # 文本消息
    if item_type == "message":
        content = item.get("content", "")
        if isinstance(content, str):
            return {"role": role, "content": content}
        elif isinstance(content, list):
            text_parts = []
            for part in content:
                pt = part.get("type", "")
                if pt == "input_text":
                    text_parts.append(part.get("text", ""))
                elif pt == "output_text":
                    text_parts.append(part.get("text", ""))
            return {"role": role, "content": "\n".join(text_parts)} if text_parts else None

    # 函数调用结果 → tool 消息（DeepSeek 要求跟在 assistant tool_calls 之后）
    elif item_type == "function_call_output":
        call_id = item.get("call_id", "")
        output = item.get("output", "")
        if isinstance(output, dict):
            output = json.dumps(output)
        elif not isinstance(output, str):
            output = str(output)
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": output,
        }

    # 函数调用（在 input 中作为历史记录传递）
    elif item_type == "function_call":
        fc_name = item.get("name", "")
        fc_args = item.get("arguments", "")
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": item.get("call_id", f"fc_{uuid.uuid4().hex[:12]}"),
                "type": "function",
                "function": {
                    "name": fc_name,
                    "arguments": fc_args if isinstance(fc_args, str) else json.dumps(fc_args),
                }
            }]
        }

    return None


def translate_tools(tools: list) -> list:
    """翻译 Responses API tools → Chat Completions tools"""
    result = []
    for tool in tools:
        ttype = tool.get("type", "")
        if ttype == "function":
            result.append({
                "type": "function",
                "function": {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters", {}),
                }
            })
        elif ttype == "web_search" and "web_search_options" in tool:
            # DeepSeek 部分支持 web search
            result.append({
                "type": "web_search",
                "web_search": tool.get("web_search_options", {}),
            })
        # file_search, code_interpreter 等 DeepSeek 不支持，跳过
    return result


def responses_to_chat(body: dict) -> dict:
    """主翻译: Responses API request → Chat Completions request"""
    model = map_model(body.get("model", DEFAULT_MODEL))
    messages = translate_input_to_messages(body)

    chat_body = {
        "model": model,
        "messages": messages,
        "stream": body.get("stream", False),
    }

    # 温度等参数
    for resp_key, chat_key in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_output_tokens", "max_tokens"),
    ):
        if resp_key in body:
            chat_body[chat_key] = body[resp_key]

    # tools 翻译
    tools = body.get("tools", [])
    if tools:
        chat_tools = translate_tools(tools)
        if chat_tools:
            chat_body["tools"] = chat_tools

    # tool_choice
    if "tool_choice" in body:
        tc = body["tool_choice"]
        if isinstance(tc, str):
            chat_body["tool_choice"] = tc
        elif isinstance(tc, dict):
            chat_body["tool_choice"] = tc

    # DeepSeek V4 reasoning/thinking 配置
    reasoning = body.get("reasoning", {})
    if reasoning:
        effort = reasoning.get("effort")
        summary = reasoning.get("summary")
        if effort:
            chat_body["reasoning_effort"] = effort
        if summary:
            chat_body["reasoning_summary"] = summary

    # 禁用推理内容来避免兼容问题（DeepSeek V4 可选）
    # chat_body["reasoning_effort"] = "disabled"

    return chat_body


# ========== SSE 流式翻译: Chat Completions → Responses API ==========

class SSEState:
    """跟踪流式翻译状态，管理 item_id / output_index"""
    def __init__(self, request_id: str, model: str):
        self.request_id = request_id
        self.model = model
        self.msg_id = f"msg_{uuid.uuid4().hex[:12]}"
        self.output_index = 0
        self.content_index = 0
        self.in_tool_calls = False
        self.current_tool_call = None
        self.tool_call_ids = []
        self.started = False

    def start(self):
        """发送初始事件序列"""
        self.started = True
        events = [
            {"type": "response.created",
             "response": {"id": f"resp_{self.request_id}", "object": "response", "status": "in_progress",
                          "model": self.model, "output": []}},
            {"type": "response.in_progress",
             "response": {"id": f"resp_{self.request_id}", "object": "response", "status": "in_progress",
                          "model": self.model, "output": []}},
        ]
        return events

    def text_output_started(self):
        """文本输出的第一个 chunk — 发送 output_item.added + content_part.added"""
        return [
            {"type": "response.output_item.added",
             "output_index": self.output_index,
             "item": {"id": self.msg_id, "type": "message", "status": "in_progress",
                      "role": "assistant", "content": []}},
            {"type": "response.content_part.added",
             "item_id": self.msg_id,
             "output_index": self.output_index,
             "content_index": self.content_index,
             "part": {"type": "output_text", "text": "", "annotations": []}},
        ]

    def text_delta(self, text: str):
        """文本增量"""
        return {"type": "response.output_text.delta",
                "item_id": self.msg_id,
                "output_index": self.output_index,
                "content_index": self.content_index,
                "delta": text}

    def text_done(self):
        """文本完成"""
        return [
            {"type": "response.output_text.done",
             "item_id": self.msg_id,
             "output_index": self.output_index,
             "content_index": self.content_index,
             "text": ""},
            {"type": "response.content_part.done",
             "item_id": self.msg_id,
             "output_index": self.output_index,
             "content_index": self.content_index,
             "part": {"type": "output_text", "text": "", "annotations": []}},
        ]

    def tool_call_start(self, tc_id: str, func_name: str):
        """工具调用开始"""
        self.in_tool_calls = True
        self.current_tool_call = {"id": tc_id, "name": func_name}
        self.tool_call_ids.append(tc_id)
        return {"type": "response.output_item.added",
                "output_index": self.output_index,
                "item": {"id": tc_id, "type": "function_call", "status": "in_progress",
                         "name": func_name, "arguments": ""}}

    def tool_call_delta(self, delta: str):
        """工具调用参数增量"""
        return {"type": "response.function_call_arguments.delta",
                "item_id": self.current_tool_call["id"],
                "output_index": self.output_index,
                "delta": delta}

    def tool_call_done(self):
        """工具调用完成"""
        tc_id = self.current_tool_call["id"]
        return {"type": "response.function_call_arguments.done",
                "item_id": tc_id,
                "output_index": self.output_index,
                "arguments": "",
                "name": self.current_tool_call["name"]}

    def output_item_done(self):
        """当前 output item 完成"""
        idx = self.output_index
        self.output_index += 1
        self.content_index += 1
        self.in_tool_calls = False
        return {"type": "response.output_item.done",
                "output_index": idx,
                "item": {"id": self.msg_id if not self.current_tool_call else self.current_tool_call["id"],
                         "type": "message" if not self.current_tool_call else "function_call",
                         "status": "completed"}}

    def completed(self, usage: dict | None = None):
        """流完成"""
        evt = {
            "type": "response.completed",
            "response": {
                "id": f"resp_{self.request_id}",
                "object": "response",
                "status": "completed",
                "model": self.model,
            }
        }
        if usage:
            evt["response"]["usage"] = {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        return evt


def write_sse(wfile, event):
    """写入单个 SSE 事件"""
    if isinstance(event, list):
        for e in event:
            wfile.write(f"data: {json.dumps(e, ensure_ascii=False)}\n\n".encode("utf-8"))
    else:
        wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
    wfile.flush()


def translate_sse_stream(resp, wfile, request_id: str, model: str):
    """主 SSE 流翻译循环"""
    state = SSEState(request_id, model)
    text_started = False
    text_done_sent = False
    tool_started = {}  # index → bool
    reasoning_started = False

    buffer = b""
    for chunk in resp:
        buffer += chunk
        while b"\n" in buffer:
            line_bytes, buffer = buffer.split(b"\n", 1)
            line = line_bytes.decode("utf-8", errors="replace").strip()

            if not line:
                continue
            if not line.startswith("data: "):
                continue

            data_str = line[6:]
            if data_str == "[DONE]":
                # 发送完成事件
                if state.in_tool_calls:
                    write_sse(wfile, state.tool_call_done())
                    write_sse(wfile, state.output_item_done())
                elif not text_done_sent and text_started:
                    write_sse(wfile, state.text_done())
                    write_sse(wfile, state.output_item_done())
                    text_done_sent = True
                write_sse(wfile, state.completed())
                continue

            try:
                chunk_data = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            choices = chunk_data.get("choices", [])
            if not choices:
                continue

            choice = choices[0]
            delta = choice.get("delta", {})
            finish_reason = choice.get("finish_reason")

            # 开始事件（第一次有内容的 chunk）
            if not state.started:
                write_sse(wfile, state.start())

            # 处理 reasoning / thinking 内容
            reasoning = delta.get("reasoning_content")
            if reasoning and not reasoning_started:
                reasoning_started = True
                # 将 reasoning 作为 output_text 的一部分（简化处理）
                # codex-deepseek 通常禁用 reasoning_effort 来避免兼容问题
                pass

            # 处理文本增量
            content = delta.get("content")
            if content:
                if not text_started and not state.in_tool_calls:
                    write_sse(wfile, state.text_output_started())
                    text_started = True

                if state.in_tool_calls:
                    # 如果有未结束的 tool call，先结束它
                    write_sse(wfile, state.tool_call_done())
                    write_sse(wfile, state.output_item_done())
                    if not text_started:
                        write_sse(wfile, state.text_output_started())
                        text_started = True

                write_sse(wfile, state.text_delta(content))

            # 处理工具调用增量
            tool_calls = delta.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    idx = tc.get("index", 0)
                    tc_id = tc.get("id", f"fc_{uuid.uuid4().hex[:12]}")
                    func = tc.get("function", {})

                    if idx not in tool_started:
                        # 如果之前有文本输出，先结束它
                        if text_started and not text_done_sent:
                            write_sse(wfile, state.text_done())
                            text_done_sent = True
                        tool_started[idx] = True
                        name = func.get("name", "")
                        write_sse(wfile, state.tool_call_start(tc_id, name))

                    if "arguments" in func:
                        write_sse(wfile, state.tool_call_delta(func["arguments"]))

            # 处理 finish_reason
            if finish_reason:
                if finish_reason == "tool_calls":
                    if state.in_tool_calls:
                        write_sse(wfile, state.tool_call_done())
                elif finish_reason == "stop":
                    if text_started and not text_done_sent:
                        write_sse(wfile, state.text_done())
                        text_done_sent = True
                    elif not text_started and not state.in_tool_calls:
                        # 空响应：至少输出一个空的 output_item
                        write_sse(wfile, state.text_output_started())
                        write_sse(wfile, state.text_delta(""))
                        write_sse(wfile, state.text_done())

            # 如果有 usage，发送完成事件
            usage = chunk_data.get("usage")
            if usage or finish_reason:
                if finish_reason in ("stop", "tool_calls"):
                    if not state.in_tool_calls or finish_reason == "stop":
                        write_sse(wfile, state.output_item_done())
                    write_sse(wfile, state.completed(usage))


# ========== HTTP Handler ==========

class BridgeHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        rid = uuid.uuid4().hex[:8]
        log(f"[{rid}] GET {self.path}")

        if self.path in ("/v1/models", "/models"):
            self._json_response(200, {
                "object": "list",
                "data": [
                    {"id": "deepseek-v4-pro", "object": "model", "created": 1767225600, "owned_by": "deepseek"},
                    {"id": "deepseek-v4-flash", "object": "model", "created": 1767225600, "owned_by": "deepseek"},
                ]
            })
            return

        if self.path.startswith("/v1/models/") or self.path.startswith("/models/"):
            model_id = self.path.split("/")[-1]
            self._json_response(200, {
                "id": model_id, "object": "model", "created": 1767225600, "owned_by": "deepseek",
                "capabilities": {
                    "supports_tool_calls": True,
                    "supports_images": False,
                    "supports_streaming": True,
                    "max_context_window": 131072,
                    "max_output_tokens": 32768,
                }
            })
            return

        # 其他 GET → 代理到上游
        self._proxy_request("GET", None)

    def do_POST(self):
        rid = uuid.uuid4().hex[:8]
        cl = int(self.headers.get("Content-Length", 0))
        body_bytes = self.rfile.read(cl)

        try:
            body = json.loads(body_bytes)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        model = body.get("model", "?")
        stream = body.get("stream", False)
        log(f"[{rid}] POST {self.path} | model={model} | stream={stream}")

        # Debug 翻译预览
        if self.path == "/debug/translate":
            chat_body = responses_to_chat(body)
            self._json_response(200, {"chat_body": chat_body})
            return

        # /v1/responses 或 /responses → 翻译
        if self.path in ("/v1/responses", "/responses"):
            self._handle_responses(rid, body)
            return

        # 其他 POST → 代理到上游
        self._proxy_request("POST", body_bytes)

    def _handle_responses(self, rid, body):
        """处理 Responses API 请求"""
        mapped_model = map_model(body.get("model", DEFAULT_MODEL))
        chat_body = responses_to_chat(body)
        is_stream = chat_body.get("stream", False)
        tools_count = len(chat_body.get("tools", []))
        msgs_count = len(chat_body.get("messages", []))

        # Debug: 打印实际发送的 messages 角色
        msg_roles = [m["role"] for m in chat_body.get("messages", [])]
        log(f"[{rid}] → Upstream | model={mapped_model} | msgs={msgs_count} "
            f"| roles={msg_roles} | tools={tools_count} | stream={is_stream}")

        try:
            req = Request(
                f"{UPSTREAM_BASE}/chat/completions",
                data=json.dumps(chat_body).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {UPSTREAM_KEY}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream" if is_stream else "application/json",
                },
            )
            resp = urlopen(req, timeout=600)

            if is_stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.send_header("x-request-id", f"resp_{rid}")
                self.end_headers()
                translate_sse_stream(resp, self.wfile, rid, mapped_model)
                log(f"[{rid}] ✓ stream done")
            else:
                data = json.loads(resp.read().decode("utf-8"))
                translated = self._non_stream_response(data, rid, mapped_model)
                body_out = json.dumps(translated, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_out)))
                self.send_header("x-request-id", f"resp_{rid}")
                self.end_headers()
                self.wfile.write(body_out)
                log(f"[{rid}] ✓ non-stream OK | tokens={translated.get('usage', {}).get('total_tokens', '?')}")

        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            log(f"[{rid}] ✗ Upstream {e.code}: {err_body[:300]}")
            self.send_response(e.code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(err_body.encode("utf-8"))
        except Exception as e:
            log(f"[{rid}] ✗ Bridge error: {e}")
            self.send_error(502, str(e))

    def _non_stream_response(self, data, rid, model):
        """将 Chat Completions 非流式响应转换为 Responses API 格式"""
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content")
        tool_calls = msg.get("tool_calls") or []
        usage = data.get("usage", {})

        output = []
        if content:
            output.append({
                "id": f"msg_{uuid.uuid4().hex[:12]}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": content}],
            })

        for tc in tool_calls:
            func = tc.get("function", {})
            output.append({
                "id": tc.get("id", f"fc_{uuid.uuid4().hex[:12]}"),
                "type": "function_call",
                "call_id": tc.get("id", ""),
                "name": func.get("name", ""),
                "arguments": func.get("arguments", "{}"),
                "status": "completed",
            })

        return {
            "id": f"resp_{rid}",
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": model,
            "output": output,
            "usage": {
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
        }

    def _proxy_request(self, method, body_bytes):
        """透传请求到上游"""
        target = f"{UPSTREAM_BASE}{self.path}"
        try:
            headers = {"Authorization": f"Bearer {UPSTREAM_KEY}"}
            for h in ("Content-Type", "Accept"):
                if h in self.headers:
                    headers[h] = self.headers[h]
            req = Request(target, data=body_bytes, headers=headers, method=method)
            resp = urlopen(req, timeout=120)
            self.send_response(resp.status)
            for k, v in resp.headers.items():
                if k.lower() not in ("transfer-encoding",):
                    self.send_header(k, v)
            self.end_headers()
            self.wfile.write(resp.read())
        except Exception as e:
            log(f"proxy {method} error: {e}")
            self.send_error(502, str(e))

    def _json_response(self, code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


# ========== 启动 ==========
def main():
    print(f"""
╔══════════════════════════════════════════════════╗
║     Codex DeepSeek Bridge (codex-deepseek)       ║
║     Responses API <-> Chat Completions API       ║
╠══════════════════════════════════════════════════╣
║  监听: {LISTEN_HOST}:{LISTEN_PORT}     上游: {UPSTREAM_BASE} ║
║  模型: {DEFAULT_MODEL}                            ║
╚══════════════════════════════════════════════════╝
""")
    server = HTTPServer((LISTEN_HOST, LISTEN_PORT), BridgeHandler)
    print("[bridge] Proxy ready, waiting for Codex...\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[bridge] Stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
