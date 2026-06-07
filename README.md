# littlebat-agent

Privacy-first AI agent on AWS Bedrock AgentCore. Self-host the full stack in
your own AWS account — your data never leaves your infrastructure.

Licensed under the [Apache License 2.0](LICENSE).

---

## What's in this repo

| Path | What it does |
|------|-------------|
| `agent/` | AgentCore Runtime handler — the AI brain. Runs on Bedrock AgentCore, calls Nova Micro, manages per-user memory in S3. |
| `lambda/chat/` | Thin HTTP proxy Lambda — sits behind API Gateway, forwards authenticated requests to AgentCore. |
| `rpi-client/` | Raspberry Pi voice client — button hold → Transcribe → AgentCore → Polly → speaker. |
| `terraform/` | Complete AWS infrastructure as code — API Gateway, Cognito, Lambda, S3, DynamoDB, IAM, budget controls. |

## Architecture

```
iOS app / Pi client
       │
       │  POST /chat  (Cognito JWT)
       ▼
  API Gateway (HTTP)
       │
       ▼
  Lambda (chat proxy)
       │
       ▼
  Bedrock AgentCore Runtime
       │  ├── Bedrock (Nova Micro)
       │  ├── S3 (memories + session logs)
       │  └── DynamoDB (usage records)
```

## Prerequisites

- AWS account with Bedrock model access enabled for `us.amazon.nova-micro-v1:0`
- [Terraform](https://developer.hashicorp.com/terraform/install) >= 1.6
- Python 3.12+ (for the Pi client and deploy script)
- AWS CLI configured with appropriate credentials

## Setup

### 1. Deploy the infrastructure

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars — set cognito_domain_prefix to something globally unique
terraform init
terraform apply
```

Note the outputs — you'll need them in the next steps. Leave
`agentcore_runtime_arn` unset for this first apply; you'll fill it in at step 3
once the runtime exists.

### 2. Deploy the agent code

`deploy_agent.py` reads the Terraform outputs, builds an ARM64 Linux package,
uploads it to S3, and creates (or updates) the AgentCore runtime for you. It
builds with Docker by default; set `PI_HOST` to build over SSH on a Pi instead.

```bash
cd rpi-client
pip install boto3
python deploy_agent.py
```

When it finishes it prints the runtime ARN. Copy it for the next step.

### 3. Wire the runtime ARN into Terraform

Add the runtime ARN to `terraform/terraform.tfvars`:

```hcl
agentcore_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:..."
```

Then re-apply to wire it into the chat Lambda:

```bash
terraform apply
```

### 4. (Optional) Set up the Raspberry Pi client

```bash
cd rpi-client
cp config.env.example config.env
# Edit config.env with your values from terraform output
./install.sh
```

## Memory / knowledge base

The agent loads a knowledge base from S3 at cold start. To populate it, upload
markdown files to your memories bucket:

```
s3://<memories-bucket>/agent/index.md        ← lists available files
s3://<memories-bucket>/agent/architecture.md ← example knowledge file
```

Per-user memories are automatically saved to:
```
s3://<memories-bucket>/users/<cognito-sub>/user-memories.md
```

## Configuration

All agent behaviour is controlled via environment variables on the AgentCore
runtime:

| Variable | Default | Description |
|----------|---------|-------------|
| `MODEL_ID` | `us.amazon.nova-micro-v1:0` | Bedrock model to use |
| `MEMORIES_BUCKET` | — | S3 bucket for memory and session files |
| `MEMORIES_PREFIX` | `agent` | S3 key prefix for the knowledge base |
| `USAGE_TABLE` | — | DynamoDB table for usage records |
| `AGENT_NAME` | `Assistant` | Name the agent uses for itself |

## Cost controls

If you set `alert_email` and `monthly_budget_usd` in `terraform.tfvars`, the
stack deploys AWS Budgets rules that:

- Send an email alert at 80% of forecast spend
- Automatically attach a deny-Bedrock IAM policy at 100% actual spend

## Contributing

PRs welcome. This project is licensed under the Apache License 2.0 — you're
free to use, modify, and redistribute it (including commercially or as part of
a larger product), provided you retain the copyright and `NOTICE` attribution.
The "Littlebat" name and logos are trademarks and are not covered by the
license.
