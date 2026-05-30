# Codex DeepSeek Bridge

将 **OpenAI Codex CLI** 接入 **DeepSeek V4** 的本地协议桥接代理。

Codex 使用 OpenAI 的 **Responses API**，而 DeepSeek 提供的是 **Chat Completions API**——协议不兼容。本代理在本地将二者实时互译，让 Codex 无缝使用 DeepSeek。

```
Codex CLI ──Responses API──▶ 本代理 (:11435) ──Chat API──▶ DeepSeek
```

## 前置条件

| 依赖 | 说明 |
|------|------|
| **Python 3.10+** | 零外部依赖，仅用标准库 |
| **Codex CLI** | `npm install -g @openai/codex` |
| **DeepSeek API Key** | [platform.deepseek.com](https://platform.deepseek.com) 注册获取 |
| **CC Switch**（可选） | [GitHub Releases](https://github.com/farion1231/cc-switch/releases) 下载 GUI，一键切换供应商 |

## 快速开始（5 分钟）

### 1. 克隆项目

```bash
git clone https://github.com/YOUR_USERNAME/codex-deepseek-bridge.git
cd codex-deepseek-bridge
```

### 2. 配置 API Key

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek API Key：
# DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxx
```

### 3. 启动桥接代理

**Windows:**
```cmd
start-bridge.bat
```

**macOS / Linux:**
```bash
chmod +x start-bridge.sh
./start-bridge.sh
```

### 4. 配置 Codex

编辑 `~/.codex/config.toml`（或通过 CC Switch GUI 配置）：

```toml
model_provider = "custom"
model = "deepseek-v4-pro"
model_reasoning_effort = "high"
disable_response_storage = true

[model_providers.custom]
name = "custom"
wire_api = "responses"
requires_openai_auth = true
base_url = "http://127.0.0.1:11435"
```

或者**一行命令**直接写入：

```bash
mkdir -p ~/.codex
cat > ~/.codex/config.toml << 'EOF'
model_provider = "custom"
model = "deepseek-v4-pro"
model_reasoning_effort = "high"
disable_response_storage = true

[model_providers.custom]
name = "custom"
wire_api = "responses"
requires_openai_auth = true
base_url = "http://127.0.0.1:11435"
EOF
```

再设置认证（会提示输入 API Key）：

```bash
export OPENAI_API_KEY=your-deepseek-key
codex
```

或写入 `~/.codex/auth.json`：

```json
{"OPENAI_API_KEY": "your-deepseek-key"}
```

### 5. 启动 Codex

```bash
codex
```

看到正常回复即配置成功。

---

## 通过 CC Switch 使用（推荐）

如果你使用 **CC Switch** 桌面版管理多供应商：

1. 打开 CC Switch → 右上角 **+** → 选择 **DeepSeek**
2. 填入 API Key → 选择模型 `deepseek-v4-pro`
3. **关键**：将请求地址改为 `http://127.0.0.1:11435`
4. 勾选「写入通用配置」→ 添加 → 启用

CC Switch 会自动管理 Codex 和 Claude Code 的配置切换。

---

## 文件说明

```
codex-deepseek-bridge/
├── deepseek-bridge.py    # 🔧 核心：协议翻译代理（689 行）
├── start-bridge.bat      # 🪟 Windows 启动脚本
├── start-bridge.sh       # 🍎 macOS/Linux 启动脚本
├── .env.example          # 📋 环境变量模板
├── .gitignore            # 🚫 Git 忽略规则
└── README.md             # 📖 本文件
```

### `deepseek-bridge.py` —— 唯一核心文件

| 模块 | 函数 / 类 | 作用 |
|------|-----------|------|
| 请求翻译 | `responses_to_chat()` | Responses API → Chat Completions |
| 角色映射 | `ROLE_MAP`, `_convert_input_item()` | `developer`→`system`, `latest_reminder`→`user`, `function_call_output`→`tool` |
| 模型映射 | `MODEL_MAP`, `map_model()` | Codex 内部模型名（gpt-5.x）→ DeepSeek 模型名 |
| 工具翻译 | `translate_tools()` | Responses tools → Chat tools（跳过不兼容的内置工具） |
| SSE 流翻译 | `SSEState`, `translate_sse_stream()` | 完整 SSE 事件链互译 |
| HTTP 服务 | `BridgeHandler` | 处理 GET（模型列表）和 POST（对话）请求 |
| 调试端点 | `POST /debug/translate` | 查看翻译后的 Chat 请求体 |

### 为什么只有一个文件？

协议翻译逻辑紧密耦合，拆成多文件反而增加复杂度。689 行在一个文件中，用注释清晰分隔模块。

---

## API 翻译对照表

### Request 翻译

| Responses API（Codex 发出） | Chat Completions API（发给 DeepSeek） |
|---|---|
| `input: "Hello"` | `messages: [{"role":"user","content":"Hello"}]` |
| `instructions: "You are..."` | `messages: [{"role":"system","content":"You are..."}]` |
| `input[].role: "developer"` | `messages[].role: "system"` |
| `input[].type: "function_call_output"` | `messages[].role: "tool"` |
| `model: "gpt-5.5"` | `model: "deepseek-v4-pro"` |
| `model: "gpt-5.4-mini"` | `model: "deepseek-v4-flash"` |
| `tools: [{type:"function",...}]` | `tools: [{type:"function",...}]` 透传 |
| `tools: [{type:"file_search"}]` | 跳过（DeepSeek 不支持） |
| `reasoning: {effort:"high"}` | `reasoning_effort: "high"` |

### Response 翻译

| Chat Completions（DeepSeek 返回） | Responses API（返回给 Codex） |
|---|---|
| `choices[0].message.content` | `output[0].content[0].text` |
| `choices[0].message.tool_calls[]` | `output[].type: "function_call"` |
| SSE: `delta.content` | SSE: `response.output_text.delta` |
| SSE: `delta.tool_calls[].function.arguments` | SSE: `response.function_call_arguments.delta` |
| SSE: `[DONE]` | SSE: `response.completed` |

### SSE 事件链

```
response.created → response.in_progress → response.output_item.added
→ response.content_part.added → response.output_text.delta (×N)
→ response.output_text.done → response.content_part.done
→ response.output_item.done → response.completed
```

---

## 故障排查

### `Model metadata not found`

Codex 警告但**不影响使用**。代理已返回模型列表（`GET /v1/models`）。

### 端口被占用

```bash
# Windows
netstat -ano | findstr 11435
taskkill /F /PID <PID>

# macOS/Linux
lsof -ti:11435 | xargs kill -9
```

### `unknown variant 'developer'`

说明代理未生效或运行的是旧版本。确保：
1. 先杀掉所有旧 Python 进程
2. 用 `start-bridge.bat` / `start-bridge.sh` 启动
3. 检查 `bridge.log` 确认是新版本

### CC Switch 代理冲突

CC Switch 内置代理和本桥接代理**二选一**：
- **本代理**（推荐）：完整的 Responses ↔ Chat 协议翻译
- **CC Switch 代理**：透明转发，不做协议翻译，Codex 直连会 404

---

## 技术原理

DeepSeek V4 支持 Anthropic 和 Chat Completions 两种 API 格式，但**不支持** OpenAI 的 Responses API。

OpenAI Codex CLI（`@openai/codex`）在 `config.toml` 中配置 `wire_api = "responses"`，强制使用 Responses API 格式。所以即使 DeepSeek 有 OpenAI 兼容的 Chat Completions 端点，Codex 也无法直连。

本代理做的事情：
1. 在 `127.0.0.1:11435` 起一个 HTTP 服务
2. Codex 把请求发到本代理的 `/v1/responses`
3. 代理将 Responses API 的 `input` + `instructions` 重组为 Chat 的 `messages`
4. 把 `developer` 等 Responses 专有 role 映射为 `system`
5. 转发到 `api.deepseek.com/v1/chat/completions`
6. 将 DeepSeek 的 Chat/SSE 响应翻译回 Responses 格式
7. 返回给 Codex

Codex 完全感知不到背后的 API 被替换了。

---

## License

MIT
