"""
Littlebat AI — /chat endpoint
Secure proxy: iOS app → Bedrock AgentCore runtime.

POST /chat
  Authorization: Bearer <cognito_access_token>   (validated by API Gateway JWT authorizer)
  Content-Type: application/json

  { "prompt": "...", "session_id": "uuid" }

Responses
  200  { "reply": "...", "session_id": "..." }
  400  { "error": "prompt_required" | "prompt_too_long" | "invalid_json" }
  401  Returned by API Gateway before Lambda is invoked (missing/invalid token)
  502  { "error": "agent_error", "detail": "..." }
  500  { "error": "internal_error" }
"""

import base64
import json
import os
import re
import traceback

import boto3
from botocore.exceptions import ClientError

REGION            = os.environ.get("AWS_REGION", "us-east-1")
AGENT_RUNTIME_ARN = os.environ.get("AGENTCORE_RUNTIME_ARN", "")

CORS_HEADERS = {
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
}


def _response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", **CORS_HEADERS},
        "body": json.dumps(body),
    }


def _parse_runtime_arn(arn: str) -> tuple:
    """Return (runtime_id, account_id) from a full AgentCore runtime ARN."""
    parts      = arn.split(":")
    account_id = parts[4]
    runtime_id = parts[5].split("/", 1)[1]
    return runtime_id, account_id


def _clean_reply(text: str) -> str:
    """Strip <thinking> blocks before returning to the client."""
    return re.sub(r"<thinking>.*?</thinking>", "", text,
                  flags=re.DOTALL | re.IGNORECASE).strip()


def handler(event: dict, _context) -> dict:
    method = (
        (event.get("requestContext") or {})
        .get("http", {})
        .get("method", "POST")
    )
    if method == "OPTIONS":
        return {"statusCode": 204, "headers": CORS_HEADERS, "body": ""}

    # Auth is handled by API Gateway's Cognito JWT authorizer before Lambda is invoked.

    # ── Parse body ────────────────────────────────────────────────────────────
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except Exception:
            return _response(400, {"error": "invalid_body"})

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return _response(400, {"error": "invalid_json"})

    prompt     = (payload.get("prompt") or "").strip()
    session_id = (payload.get("session_id") or "default").strip()[:64]

    if not prompt:
        return _response(400, {"error": "prompt_required"})
    if len(prompt) > 4000:
        return _response(400, {"error": "prompt_too_long"})
    if not AGENT_RUNTIME_ARN:
        return _response(500, {"error": "agent_not_configured"})

    # Extract Cognito user identity from the JWT claims injected by API Gateway.
    # Used by the agent to scope memories per user.
    jwt_claims = (
        (event.get("requestContext") or {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    user_sub   = jwt_claims.get("sub", "")
    user_email = jwt_claims.get("email", "")

    # ── Invoke AgentCore ──────────────────────────────────────────────────────
    try:
        client = boto3.client("bedrock-agentcore", region_name=REGION)
        runtime_id, account_id = _parse_runtime_arn(AGENT_RUNTIME_ARN)

        resp = client.invoke_agent_runtime(
            agentRuntimeArn=runtime_id,
            accountId=account_id,
            runtimeSessionId=session_id,
            contentType="application/json",
            accept="application/json",
            payload=json.dumps({
                "prompt":     prompt,
                "session_id": session_id,
                "user_sub":   user_sub,
                "user_email": user_email,
            }).encode("utf-8"),
        )

        raw_out = b"".join(resp["response"].iter_chunks())
        data    = json.loads(raw_out)
        reply   = (
            data.get("output")
            or data.get("response")
            or data.get("message")
            or str(data)
        )
        reply = _clean_reply(reply)

        return _response(200, {"reply": reply, "session_id": session_id})

    except ClientError as e:
        error = e.response.get("Error", {})
        code  = error.get("Code", "Unknown")
        msg   = error.get("Message", str(e))
        print(f"[agent error] {code}: {msg}", flush=True)
        return _response(502, {"error": "agent_error", "detail": msg})

    except Exception as e:
        print(f"[unexpected error] {e}", flush=True)
        traceback.print_exc()
        return _response(500, {"error": "internal_error"})
