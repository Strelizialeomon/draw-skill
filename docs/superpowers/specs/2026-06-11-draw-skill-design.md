# draw-skill 设计文档

- 日期：2026-06-11
- 状态：已确认（接口契约按真机实测修正）
- 作者：用户 + Claude（brainstorming）

## 目标

把生图接口封装成一个能在 Claude Code 里调用的技能。
用户说"帮我画一张 X"，技能负责：发起生图 → 等待（带进度）→ 把成图下载到本地 → 返回本地路径。

## 不做（YAGNI）

- 本地图片垫图（接口只接受公网 URL）
- webHook 回调（技能跑在本地，没有公网地址接收回调）
- 并发批量生图
- `/v1/draw/result` 轮询：实测流式接口已经一并给出进度与结果，单独轮询接口本流程用不到

## 接口契约（来自真机实测，非文档）

域名记为 `{BASE}`，由环境变量提供。鉴权统一带：

```
Content-Type: application/json
Authorization: Bearer <IMAGE_API_KEY>
```

### 生图：流式接口

`POST {BASE}/v1/draw/completions`

请求体：

```json
{
  "model": "gpt-image-2",
  "prompt": "提示词",
  "aspectRatio": "1024x1024",
  "urls": ["https://example.com/ref.png"],
  "shutProgress": false
}
```

- `urls`：可选，参考图（垫图）的公网 URL 数组；不传即纯文生图。
- `shutProgress`：
  - `false`（本项目采用）：**流式**，连接挂住，按 SSE 一条条推进度事件直到完成。
  - `true`：阻塞到完成，只回最后一条事件（无中途进度）。

**响应是 SSE 流**（`Content-Type` 可能标 `text/event-stream` 或 `application/json`，但 body 一律是 `data:` 帧）。每条事件形如：

```
data: {"id":"1-a111...","progress":42,"status":"running","failure_reason":"","error":"","results":null,"start_time":...,"end_time":0}

data: {"id":"1-a111...","progress":100,"status":"succeeded","results":[{"url":"https://.../x.png","width":0,"height":0}],...}
```

- 每条事件是**扁平 JSON**（字段直接在顶层，没有 `{code,data:{...}}` 包装）。
- `status`：`running` 进行中 / `succeeded` 完成（`results` 有 url）/ `failed` 失败。
- `progress`：0~100。
- `results`：`[{"url": "..."}]`，成图是 **URL 链接**（可能多张）。
- `id`：任务 id，每条事件都带，用于给下载文件命名。

## 架构

单个零依赖 Python 脚本 + 一个 SKILL.md。`draw.py` 只用标准库（`urllib`、`json`、`argparse`、`os`、`sys`）。

按职责拆成小函数，HTTP/流式作为参数注入（`open_stream=` / `fetch=`），便于用桩做单元测试、不打真网络：

- `class DrawError(Exception)`
- `_parse_sse_event(line) -> dict | None`：把一行 `data: {...}` 解析成事件 dict；非 data 行/空行返回 None。
- `generate(prompt, *, base, api_key, model, aspect, refs, open_stream, log) -> (task_id, urls)`：发起生图、消费事件流、显示进度、返回任务 id 与图片 URL 列表。
- `_http_post_stream(url, api_key, body, *, timeout) -> 迭代器[str]`：真实流式 POST，逐行 yield 解码后的文本。
- `download(url, out_dir, task_id, index, *, fetch) -> str`：把图片 URL 下载到本地，返回绝对路径。
- `_http_get_bytes(url) -> bytes`：真实下载。
- `main(argv) -> int`：读环境变量、解析参数、串起流程。

### 调用形态

```
python3 draw.py "<prompt>" \
  [--model gpt-image-2] [--aspect 1024x1024] \
  [--ref <公网图URL>]...   # 对应接口 urls[]
  [--out <目录>]           # 默认当前目录
  [--url-only]             # 只打印图片 URL，不下载
  [--timeout 300]          # 流读取超时秒
```

文件名规则：`draw-<任务id>-<序号><扩展名>`（扩展名取自 URL，缺省 `.png`）。

## 数据流

1. 读取 `IMAGE_API_KEY`、`IMAGE_API_BASE`；缺任一报错退出码 2。
2. `generate`：`POST /v1/draw/completions`（`shutProgress=false`），逐行读事件：
   - `running` → 打印 `进度 xx%` 到 stderr。
   - `succeeded` → 取 `id` 与 `results[].url`，返回。
   - `failed` → 打印 `failure_reason`/`error`，抛错。
   - 流结束仍无终态 → 抛错。
3. 逐个下载 `results[].url` 到 `--out`，打印本地绝对路径（stdout）。
4. `--url-only` 时跳过下载，直接打印 URL 列表。

## 配置

| 环境变量 | 必填 | 说明 |
|---|---|---|
| `IMAGE_API_KEY` | 是 | Bearer token |
| `IMAGE_API_BASE` | 是 | 接口域名，如 `https://api.example.com`（不含路径） |
| `IMAGE_MODEL` | 否 | 默认模型名；不设则回落 `gpt-image-2` |

- **绝不在脚本里硬编码 key**。
- 必填项缺失时给清楚的中文报错。

**模型选择优先级**（高 → 低）：`--model` 参数 > `IMAGE_MODEL` 环境变量 > 兜底 `gpt-image-2`。

其它默认值：`aspect = 1024x1024`，流读取超时 `300` 秒。

## 错误处理

| 情况 | 行为 |
|---|---|
| 缺 `IMAGE_API_KEY` / `IMAGE_API_BASE` | 提示，退出码 2 |
| HTTP 非 2xx / 网络异常 | 打印状态码/原因，退出码 1 |
| 事件 `status == failed` | 打印 `failure_reason`/`error`，退出码 1 |
| 流结束但无 `succeeded`/`failed` | 打印"未生成完成 + 最后状态"，退出码 1 |
| 图片下载失败 | 打印 URL 与原因，退出码 1 |

报错走 stderr；正常的本地路径走 stdout，便于解析。

## 测试

- `_parse_sse_event`：纯函数，覆盖正常 data 行、非 data 行、空 payload。
- `generate`：注入 `open_stream` 桩，覆盖 running→succeeded（返回 id+urls 且打印进度）、failed、succeeded 但无 results、流提前结束、请求体含 `shutProgress:false` 与 refs。
- `download`：注入 `fetch` 桩，覆盖落盘+绝对路径、自动建目录、扩展名。
- `_http_post_stream`：monkeypatch `urlopen`，覆盖逐行 yield 与 HTTPError→DrawError。
- `main`：缺环境变量→2；happy path（桩掉 `generate`）打印路径并返回 0；`--url-only`；模型优先级三态。
- 一个真机冒烟：配好环境变量后实跑一次"发起→进度→下载"。

## 技能落地

- 项目放 `~/code/draw-skill/`，含 `draw.py`、`SKILL.md`、`README.md`。
- 软链到 `~/.claude/skills/draw`。
- 命令名/技能名：`draw`（`/draw 帮我画只猫`）。
- SKILL.md 的 `description` 覆盖触发词：生图、画图、生成图片、文生图、draw image 等。
