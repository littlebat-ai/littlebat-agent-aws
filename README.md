# littlebat-agent

Privacy-first AI agent on AWS Bedrock AgentCore. Self-host the full stack in
your own AWS account — your data never leaves your infrastructure.

Licensed under the [GNU Affero General Public License v3.0](LICENSE).

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

Note the outputs — you'll need them in the next steps.

### 2. Create the AgentCore runtime

The AgentCore runtime must currently be created via the AWS console or CLI (Terraform
support is in preview). In the AWS console:

1. Go to **Amazon Bedrock → AgentCore → Agent Runtimes → Create**
2. Set the execution role to the value of `terraform output agentcore_runtime_role_arn`
3. Upload the agent code package (see step 3)
4. Copy the runtime ARN

### 3. Deploy the agent code

```bash
cd rpi-client
pip install boto3 bedrock-agentcore
python deploy_agent.py \
  --bucket $(cd ../terraform && terraform output -raw agentcore_code_bucket) \
  --runtime-arn <runtime-arn-from-step-2>
```

### 4. Set the runtime ARN in Terraform

Add the runtime ARN to `terraform/terraform.tfvars`:

```hcl
agentcore_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:..."
```

Then re-apply to wire it into the chat Lambda:

```bash
terraform apply
```

### 5. (Optional) Set up the Raspberry Pi client

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

PRs welcome. This project is AGPL-3.0 licensed — any modifications you deploy
as a network service must be made available under the same license.
