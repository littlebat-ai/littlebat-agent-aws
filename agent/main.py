"""
AgentCore Runtime handler
Receives prompts (voice-transcribed or text), maintains conversation history per
session, replies via a configurable Bedrock model. Each invocation writes a usage
record to DynamoDB and appends the conversation turn to a per-session JSONL file
in S3.

Memory access:
  - On cold start the agent loads index.md from S3 and builds the base system prompt.
  - User memories are loaded on first request per user (keyed by Cognito user_sub).
    Device requests (no user_sub) use the MEMORIES_PREFIX/user-memories.md key.
    Authenticated iOS/web requests use users/{user_sub}/user-memories.md.
    Device memories are merged in for authenticated users so facts are shared
    across surfaces.
  - The model uses two tools:
      read_memory  : lazily load a knowledge-base file (architecture.md, etc.)
      save_memory  : append a persistent fact to the user's S3 memory file

Environment variables:
  AWS_REGION        — AWS region (default: us-east-1)
  MODEL_ID          — Bedrock model ID (default: us.amazon.nova-micro-v1:0)
  USAGE_TABLE       — DynamoDB table name for usage records
  MEMORIES_BUCKET   — S3 bucket for memory files and session logs
  MEMORIES_PREFIX   — S3 key prefix for the knowledge base (default: agent)
  AGENT_NAME        — Name the agent uses for itself (default: Assistant)
"""

import json
import os
import time
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from bedrock_agentcore import BedrockAgentCoreApp

app = BedrockAgentCoreApp()

AWS_REGION       = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID         = os.environ.get("MODEL_ID", "us.amazon.nova-micro-v1:0")
USAGE_TABLE      = os.environ.get("USAGE_TABLE", "")
MEMORIES_BUCKET  = os.environ.get("MEMORIES_BUCKET", "")
MEMORIES_PREFIX  = os.environ.get("MEMORIES_PREFIX", "agent")
AGENT_NAME       = os.environ.get("AGENT_NAME", "Assistant")

BASE_SYSTEM = (
    f"You are {AGENT_NAME}, a helpful AI assistant. "
    "Keep responses concise — they will be spoken aloud via text-to-speech, "
    "so avoid markdown, bullet points, and long lists. "
    "Answer in plain conversational sentences. "
    "When the user tells you their name, a preference, or any fact worth remembering "
    "long-term, call save_memory immediately so you never forget it in future sessions. "
    "When you know the user's name from memory, use it naturally in conversation."
)

# Nova Micro on-demand pricing (us-east-1) per 1K tokens
INPUT_TOKEN_PRICE  = Decimal("0.000035")
OUTPUT_TOKEN_PRICE = Decimal("0.000140")

bedrock = boto3.client("bedrock-runtime",  region_name=AWS_REGION)
s3      = boto3.client("s3",               region_name=AWS_REGION)
ddb     = boto3.resource("dynamodb",       region_name=AWS_REGION)

# In-memory conversation history keyed by session_id.
# Persists while the runtime instance is alive.
_history: dict[str, list] = {}

# Per-user memory cache: { user_sub_or_pi_sentinel -> (s3_key, memory_text) }
# Loaded lazily on first request per user; updated in place by save_memory.
_PI_SENTINEL = "__pi__"
_memory_cache: dict[str, tuple[str, str]] = {}

# Tool spec exposed to the model
_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "read_memory",
                "description": (
                    "Load a knowledge-base file. "
                    "Use this to get details about hardware, AWS resources, "
                    "the Pi setup, the codebase, usage data, or known issues. "
                    "The index.md (already in your system prompt) lists all "
                    "available files and when to use each one."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "filename": {
                                "type": "string",
                                "description": (
                                    "Name of the file to load, e.g. 'architecture.md'. "
                                    "Must be one of the files listed in the index."
                                ),
                            }
                        },
                        "required": ["filename"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "save_memory",
                "description": (
                    "Permanently save a fact about the user or context so it is "
                    "remembered in all future sessions. Use this when the user "
                    "tells you their name, a preference, a habit, or anything "
                    "else worth remembering long-term."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "fact": {
                                "type": "string",
                                "description": (
                                    "The fact to remember, as a brief statement. "
                                    "E.g. 'User's name is Brian.' or "
                                    "'User prefers temperatures in Celsius.'"
                                ),
                            }
                        },
                        "required": ["fact"],
                    }
                },
            }
        },
    ]
}


def _load_s3_text(key: str) -> str | None:
    """Fetch a text object from S3. Returns None on any error."""
    if not MEMORIES_BUCKET:
        return None
    try:
        obj = s3.get_object(Bucket=MEMORIES_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except Exception as e:
        print(f"[memory] failed to load s3://{MEMORIES_BUCKET}/{key}: {e}", flush=True)
        return None


def _build_index_prompt() -> str:
    """Load index.md and build the base system prompt. Called once at cold start."""
    if not MEMORIES_BUCKET:
        return BASE_SYSTEM
    index_key  = MEMORIES_PREFIX.rstrip("/") + "/index.md"
    index_text = _load_s3_text(index_key)
    if not index_text:
        return BASE_SYSTEM
    return (
        f"{BASE_SYSTEM}\n\n"
        "---\n"
        "KNOWLEDGE BASE INDEX (use the read_memory tool to load files as needed):\n\n"
        f"{index_text}"
    )


def _user_memories_key(user_sub: str) -> str:
    """Return the S3 key for a user's memory file."""
    if user_sub:
        return f"users/{user_sub}/user-memories.md"
    return MEMORIES_PREFIX.rstrip("/") + "/user-memories.md"


def _load_user_memories(user_sub: str) -> tuple[str, str]:
    """
    Load memories for a user from S3 and cache them.
    For iOS users (user_sub set), also merges in the legacy rpi-agent memories
    so facts saved from the Pi are visible across both surfaces.
    Returns (s3_key, memory_text).
    """
    cache_key = user_sub or _PI_SENTINEL
    if cache_key in _memory_cache:
        return _memory_cache[cache_key]

    key  = _user_memories_key(user_sub)
    text = _load_s3_text(key) or ""

    if user_sub:
        # Merge legacy Pi memories so the user sees them on iOS too
        legacy_key  = MEMORIES_PREFIX.rstrip("/") + "/user-memories.md"
        legacy_text = _load_s3_text(legacy_key) or ""
        if legacy_text and legacy_text not in text:
            text = (legacy_text + "\n" + text).strip()

    _memory_cache[cache_key] = (key, text)
    return key, text


def _current_system_prompt(user_memories: str, user_email: str = "") -> str:
    """
    Build the full system prompt for a single Converse call.
    Injects the user's saved memories and, if known, their email for personalization.
    """
    parts = [_INDEX_PROMPT]
    if user_email:
        parts.append(f"---\nThe authenticated user's email is: {user_email}")
    if user_memories.strip():
        parts.append(
            "---\n"
            "REMEMBERED USER FACTS (always take these into account):\n\n"
            + user_memories.strip()
        )
    return "\n\n".join(parts)


# Build the static index portion once at cold start
_INDEX_PROMPT = _build_index_prompt()

print(
    f"[init] index prompt: {len(_INDEX_PROMPT)} chars | "
    f"bucket: {MEMORIES_BUCKET or 'none'}",
    flush=True,
)


def _read_memory_tool(filename: str) -> str:
    """Fetch a knowledge-base file from S3 and return its contents."""
    safe = os.path.basename(filename.strip().lstrip("/"))
    if not safe:
        return "Error: empty filename."
    key = MEMORIES_PREFIX.rstrip("/") + "/" + safe
    content = _load_s3_text(key)
    if content is None:
        return f"Error: could not load '{safe}' from the knowledge base."
    return content


def _save_memory_tool(fact: str, user_sub: str) -> str:
    """Append a fact to the user's memory file in S3 and update the in-memory cache."""
    fact = fact.strip()
    if not fact:
        return "Error: empty fact — nothing saved."
    if not MEMORIES_BUCKET:
        return "Error: memory bucket not configured."

    cache_key = user_sub or _PI_SENTINEL
    s3_key, current_text = _memory_cache.get(cache_key, (_user_memories_key(user_sub), ""))

    date_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    new_line     = f"- [{date_str}] {fact}\n"
    updated_text = (current_text or "") + new_line
    _memory_cache[cache_key] = (s3_key, updated_text)

    try:
        s3.put_object(
            Bucket=MEMORIES_BUCKET,
            Key=s3_key,
            Body=updated_text.encode("utf-8"),
            ContentType="text/markdown",
        )
        print(f"[memory] saved for user={cache_key!r}: {fact}", flush=True)
        return f"Got it — I'll remember: {fact}"
    except Exception as e:
        print(f"[memory] save error: {e}", flush=True)
        return f"I tried to remember that but there was an error saving: {e}"


def _converse_with_tools(msgs: list, user_sub: str, user_email: str) -> tuple[str, int, int]:
    """
    Run a Bedrock Converse loop that handles tool_use stop reasons.

    Returns (reply_text, total_input_tokens, total_output_tokens).
    """
    total_input  = 0
    total_output = 0
    _, user_memories = _load_user_memories(user_sub)

    while True:
        response = bedrock.converse(
            modelId=MODEL_ID,
            system=[{"text": _current_system_prompt(user_memories, user_email)}],
            messages=msgs,
            toolConfig=_TOOL_CONFIG,
        )

        usage        = response.get("usage", {})
        total_input  += usage.get("inputTokens",  0)
        total_output += usage.get("outputTokens", 0)

        out_msg     = response["output"]["message"]
        stop_reason = response.get("stopReason", "end_turn")

        # Append the assistant turn (may contain toolUse blocks)
        msgs.append(out_msg)

        if stop_reason != "tool_use":
            # Done — extract the text reply
            reply = ""
            for block in out_msg.get("content", []):
                if "text" in block:
                    reply = block["text"]
                    break
            return reply, total_input, total_output

        # Process tool calls and build the toolResult message
        tool_results = []
        for block in out_msg.get("content", []):
            if "toolUse" not in block:
                continue
            tool_use = block["toolUse"]
            tool_id  = tool_use["toolUseId"]
            name     = tool_use["name"]
            inp      = tool_use.get("input", {})

            if name == "read_memory":
                filename = inp.get("filename", "")
                print(f"[memory] read '{filename}'", flush=True)
                result_text = _read_memory_tool(filename)
            elif name == "save_memory":
                fact = inp.get("fact", "")
                result_text = _save_memory_tool(fact, user_sub)
            else:
                result_text = f"Unknown tool: {name}"

            tool_results.append({
                "toolUseId": tool_id,
                "content":   [{"text": result_text}],
            })

        msgs.append({
            "role":    "user",
            "content": [{"toolResult": tr} for tr in tool_results],
        })
        # Loop back to call Converse again with the tool results


def _write_usage(
    session_id: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int,
    prompt_len: int,
    reply_len: int,
) -> None:
    """Write one invocation record to DynamoDB. Errors are logged but never raised."""
    if not USAGE_TABLE:
        return
    try:
        total_tokens = input_tokens + output_tokens
        cost_usd = (
            Decimal(input_tokens)  * INPUT_TOKEN_PRICE +
            Decimal(output_tokens) * OUTPUT_TOKEN_PRICE
        ) / Decimal("1000")

        now = datetime.now(timezone.utc)
        ddb.Table(USAGE_TABLE).put_item(Item={
            "date":          now.strftime("%Y-%m-%d"),
            "ts":            now.isoformat(),
            "request_id":    str(uuid.uuid4()),
            "session_id":    session_id,
            "model_id":      MODEL_ID,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
            "total_tokens":  total_tokens,
            "cost_usd":      cost_usd.quantize(Decimal("0.00000001")),
            "latency_ms":    latency_ms,
            "prompt_length": prompt_len,
            "reply_length":  reply_len,
            # Auto-expire after 90 days
            "expire_at":     int((now + timedelta(days=90)).timestamp()),
        })
    except Exception as e:
        print(f"[usage logging error] {e}", flush=True)


def _log_turn(
    session_id: str,
    prompt: str,
    reply: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    """Append one conversation turn to a per-session JSONL file in S3."""
    if not MEMORIES_BUCKET:
        return
    try:
        now      = datetime.now(timezone.utc)
        date_str = now.strftime("%Y-%m-%d")
        key      = f"sessions/{date_str}/{session_id}.jsonl"
        record   = json.dumps({
            "ts":            now.isoformat(),
            "prompt":        prompt,
            "reply":         reply,
            "input_tokens":  input_tokens,
            "output_tokens": output_tokens,
        }, ensure_ascii=False) + "\n"

        try:
            existing = s3.get_object(Bucket=MEMORIES_BUCKET, Key=key)["Body"].read().decode("utf-8")
        except Exception:
            existing = ""

        s3.put_object(
            Bucket=MEMORIES_BUCKET,
            Key=key,
            Body=(existing + record).encode("utf-8"),
            ContentType="application/x-ndjson",
        )
    except Exception as e:
        print(f"[session log error] {e}", flush=True)


@app.entrypoint
def handler(payload: dict) -> dict:
    prompt     = payload.get("prompt", "").strip()
    session_id = payload.get("session_id", "default")
    user_sub   = payload.get("user_sub", "")    # Cognito sub from JWT (empty on Pi)
    user_email = payload.get("user_email", "")  # Cognito email from JWT (empty on Pi)

    if not prompt:
        return {"output": "I didn't receive any input. Please try again."}

    # Log who is talking (sub truncated for brevity)
    sub_display = user_sub[:8] + "..." if len(user_sub) > 8 else (user_sub or "pi")
    print(f"[handler] session={session_id[:8]}... user={sub_display}", flush=True)

    msgs = _history.setdefault(session_id, [])
    msgs.append({"role": "user", "content": [{"text": prompt}]})

    t0 = time.time()
    reply, input_tokens, output_tokens = _converse_with_tools(msgs, user_sub, user_email)
    latency_ms = int((time.time() - t0) * 1000)

    _write_usage(
        session_id    = session_id,
        input_tokens  = input_tokens,
        output_tokens = output_tokens,
        latency_ms    = latency_ms,
        prompt_len    = len(prompt),
        reply_len     = len(reply),
    )
    _log_turn(
        session_id    = session_id,
        prompt        = prompt,
        reply         = reply,
        input_tokens  = input_tokens,
        output_tokens = output_tokens,
    )

    return {"output": reply}


if __name__ == "__main__":
    app.run()
