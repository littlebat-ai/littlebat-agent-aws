#!/usr/bin/env python3
"""
Package, upload, and create/update the Littlebat AgentCore Runtime.

The build step runs ON the Pi over SSH (Linux ARM64) so that binary
dependencies are compiled for the correct platform. Upload to S3 also
happens from the Pi. The runtime create/update API call runs locally.

Usage:
    AWS_PROFILE=littlebat python3 rpi-agent/deploy_agent.py
"""

import boto3
import os
import subprocess
import time
from pathlib import Path

REGION        = os.environ.get("AWS_REGION", "us-east-1")
RUNTIME_NAME  = "littlebat_agent"
MODEL_ID      = "us.amazon.nova-micro-v1:0"

PI_HOST = os.environ.get("PI_HOST", "192.168.0.176")
PI_USER = os.environ.get("PI_USER", "brian")
CODE_DIR = Path(__file__).parent / "agent_code"
S3_KEY   = "littlebat_agent/deployment_package.zip"


def tf_output(key: str) -> str:
    result = subprocess.run(
        ["terraform", "output", "-raw", key],
        cwd=Path(__file__).parent.parent / "aws" / "terraform",
        capture_output=True, text=True, check=True,
        env={**os.environ, "AWS_PROFILE": os.environ.get("AWS_PROFILE", "littlebat")},
    )
    return result.stdout.strip()


def _pi_reachable() -> bool:
    result = subprocess.run(
        ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
         f"{PI_USER}@{PI_HOST}", "true"],
        capture_output=True,
    )
    return result.returncode == 0


def _build_on_pi(tmp: str) -> str:
    target = f"{PI_USER}@{PI_HOST}"
    local_zip = f"{tmp}/deployment_package.zip"

    print(f"Syncing agent_code/ to {target}:/tmp/agent_code/ ...")
    subprocess.run(
        ["rsync", "-a", "--delete", str(CODE_DIR) + "/", f"{target}:/tmp/agent_code/"],
        check=True,
    )
    print("Building ARM64 Linux package on Pi...")
    build_script = """
set -euo pipefail
BUILD=/tmp/agent_build
rm -rf "$BUILD" && mkdir -p "$BUILD"
pip3 install --break-system-packages \
    -r /tmp/agent_code/requirements.txt \
    --target "$BUILD" --quiet 2>/dev/null
cp /tmp/agent_code/*.py "$BUILD/"
cd "$BUILD"
zip -r /tmp/deployment_package.zip . -x "*.pyc" -x "*/__pycache__/*" >/dev/null
echo "Package: $(du -sh /tmp/deployment_package.zip | cut -f1)"
"""
    subprocess.run(["ssh", target, build_script], check=True)
    print("Pulling package from Pi...")
    subprocess.run(["scp", f"{target}:/tmp/deployment_package.zip", local_zip], check=True)
    return local_zip


def _build_with_docker(tmp: str) -> str:
    """Build ARM64 Linux package locally using Docker with --platform linux/arm64."""
    local_zip = f"{tmp}/deployment_package.zip"
    print("Building ARM64 Linux package with Docker (Pi unreachable)...")
    script = (
        "set -euo pipefail && "
        "apt-get update -qq && apt-get install -y -qq zip > /dev/null && "
        "BUILD=/tmp/agent_build && rm -rf $BUILD && mkdir -p $BUILD && "
        "pip3 install -r /src/requirements.txt --target $BUILD --quiet && "
        "cp /src/*.py $BUILD/ && "
        "cd $BUILD && "
        "zip -r /out/deployment_package.zip . -x '*.pyc' -x '*/__pycache__/*' > /dev/null && "
        "echo \"Package: $(du -sh /out/deployment_package.zip | cut -f1)\""
    )
    subprocess.run([
        "docker", "run", "--rm", "--platform", "linux/arm64",
        "-v", f"{CODE_DIR}:/src:ro",
        "-v", f"{tmp}:/out",
        "python:3.13-slim",
        "bash", "-c", script,
    ], check=True)
    return local_zip


def build_and_upload(s3, bucket: str) -> None:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        if _pi_reachable():
            local_zip = _build_on_pi(tmp)
        else:
            local_zip = _build_with_docker(tmp)
        print(f"Uploading to s3://{bucket}/{S3_KEY} ...")
        s3.upload_file(local_zip, bucket, S3_KEY)
        print("Upload complete.")


def get_existing_runtime(client) -> dict | None:
    paginator = client.get_paginator("list_agent_runtimes")
    for page in paginator.paginate():
        for rt in page.get("agentRuntimes", []):
            if rt["agentRuntimeName"] == RUNTIME_NAME:
                return rt
    return None


def delete_runtime(client, runtime_id: str) -> None:
    print(f"Deleting failed runtime {runtime_id}...")
    client.delete_agent_runtime(agentRuntimeId=runtime_id)
    print("Waiting for deletion", end="", flush=True)
    for _ in range(60):
        try:
            resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
            if "DELETE" in resp.get("status", ""):
                print(".", end="", flush=True)
                time.sleep(3)
        except client.exceptions.ResourceNotFoundException:
            print(" ✓")
            return
    raise TimeoutError("Timed out waiting for runtime deletion")


def wait_for_ready(client, runtime_id: str, timeout: int = 300) -> dict:
    print("Waiting for runtime to become READY", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.get_agent_runtime(agentRuntimeId=runtime_id)
        status = resp.get("status", "")
        if status == "READY":
            print(" ✓")
            return resp
        if "FAILED" in status:
            reason = resp.get("failureReason", "unknown")
            print(f"\nRuntime failed: {reason}")
            raise RuntimeError(f"Runtime failed: {reason}")
        print(".", end="", flush=True)
        time.sleep(5)
    raise TimeoutError("Timed out waiting for runtime to become READY")


def main():
    print("Reading Terraform outputs...")
    bucket           = tf_output("agentcore_code_bucket")
    role_arn         = tf_output("agentcore_runtime_role_arn")
    usage_table      = tf_output("agent_usage_table")
    memories_bucket  = tf_output("mobile_memories_bucket")
    print(f"  Bucket          : {bucket}")
    print(f"  Role ARN        : {role_arn}")
    print(f"  Usage table     : {usage_table}")
    print(f"  Memories bucket : {memories_bucket}")

    s3     = boto3.client("s3", region_name=REGION)
    client = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # Build ARM64 Linux package (Pi if reachable, Docker otherwise) and upload
    build_and_upload(s3, bucket)

    artifact = {
        "codeConfiguration": {
            "code": {"s3": {"bucket": bucket, "prefix": S3_KEY}},
            "runtime":    "PYTHON_3_13",
            "entryPoint": ["main.py"],
        }
    }
    network  = {"networkMode": "PUBLIC"}
    env_vars = {
        "AWS_REGION":       REGION,
        "MODEL_ID":         MODEL_ID,
        "USAGE_TABLE":      usage_table,
        "MEMORIES_BUCKET":  memories_bucket,
        "MEMORIES_PREFIX":  "rpi-agent/",
    }
    lifecycle = {
        "idleRuntimeSessionTimeout": 600,   # 10 min keep-warm
        "maxLifetime":               3600,  # 1 hr hard cap
    }

    existing = get_existing_runtime(client)

    if existing:
        runtime_id = existing["agentRuntimeId"]
        # If the previous attempt failed, delete and recreate
        if "FAILED" in existing.get("status", ""):
            delete_runtime(client, runtime_id)
            existing = None

    if existing:
        runtime_id = existing["agentRuntimeId"]
        print(f"Updating runtime {runtime_id}...")
        client.update_agent_runtime(
            agentRuntimeId=runtime_id,
            agentRuntimeArtifact=artifact,
            networkConfiguration=network,
            roleArn=role_arn,
            environmentVariables=env_vars,
            lifecycleConfiguration=lifecycle,
        )
    else:
        print(f"Creating runtime '{RUNTIME_NAME}'...")
        resp = client.create_agent_runtime(
            agentRuntimeName=RUNTIME_NAME,
            agentRuntimeArtifact=artifact,
            networkConfiguration=network,
            roleArn=role_arn,
            environmentVariables=env_vars,
            lifecycleConfiguration=lifecycle,
        )
        runtime_id = resp["agentRuntimeId"]

    details = wait_for_ready(client, runtime_id)
    arn = details["agentRuntimeArn"]

    print(f"\nRuntime ARN:\n  {arn}")
    print(f"\nAdd this to rpi-agent/config.env on the Pi:")
    print(f"  AGENTCORE_RUNTIME_ARN={arn}")


if __name__ == "__main__":
    main()
