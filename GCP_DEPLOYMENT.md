# TradeWiz — Google Cloud Platform Deployment Architecture
## Low-Cost, High-Performance Design with Vertex AI

---

## ARCHITECTURE REVIEW: Current vs GCP

### Current (Docker Compose on VPS)
```
User → nginx (SSL) → Flask/Gunicorn → PostgreSQL (local)
                                     → OpenRouter API (LLM)
                                     → yfinance (market data)
                                     → BloFin/Alpaca (trading)
```
**Problems:**
- Single point of failure (one server)
- No auto-scaling (fixed 2 workers)
- OpenRouter markup on LLM calls (~30-50% above direct API pricing)
- No CDN for static assets
- No Redis cache (in-memory Python dicts lost on restart)
- SSL renewal via certbot (manual)
- Bot threads run in same process as web (compete for CPU)

---

## GCP ARCHITECTURE (Recommended)

```
                    ┌─────────────────────────────┐
                    │     Cloud Load Balancer      │
                    │   (Global, managed SSL)      │
                    └──────────┬──────────────────┘
                               │
                    ┌──────────▼──────────────────┐
                    │      Cloud CDN               │
                    │  (static JS/CSS/images)      │
                    └──────────┬──────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼────┐  ┌───────▼──────┐  ┌──────▼───────┐
    │  Cloud Run   │  │  Cloud Run   │  │ Cloud Run    │
    │  (Web App)   │  │  (Bot Worker)│  │ (Skill Jobs) │
    │  Flask API   │  │  Crypto+Stock│  │ Long-running │
    │  0-10 inst.  │  │  1 inst min  │  │ 0-3 inst.    │
    └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
           │                 │                  │
    ┌──────▼─────────────────▼──────────────────▼──────┐
    │                  VPC Network                      │
    │  ┌─────────────┐  ┌──────────┐  ┌─────────────┐  │
    │  │ Cloud SQL   │  │Memorystore│  │ Vertex AI   │  │
    │  │ PostgreSQL  │  │  Redis    │  │ Model Garden│  │
    │  │ (db-f1-micro│  │  (1GB)   │  │ Gemini 2.5  │  │
    │  │  $7/mo)     │  │  ($6/mo) │  │ Claude 4    │  │
    │  └─────────────┘  └──────────┘  │ Llama 3.1   │  │
    │                                  └─────────────┘  │
    │  ┌─────────────┐  ┌──────────────────────────┐   │
    │  │Cloud Storage│  │   Cloud Tasks / Pub/Sub   │   │
    │  │(skill output│  │  (job queue, bot commands) │   │
    │  │ + backups)  │  │                            │   │
    │  └─────────────┘  └──────────────────────────┘   │
    └───────────────────────────────────────────────────┘
```

---

## COMPONENT BREAKDOWN & COST

### 1. Cloud Run — Web App (Flask API)
**What:** Serves all HTTP requests (API routes, templates, SSE streams)
**Config:**
- Min instances: 0 (scales to zero when idle)
- Max instances: 10
- CPU: 1 vCPU, Memory: 512MB
- Concurrency: 80 requests/container
- Timeout: 300s (for long LLM calls)

**Cost:** ~$5-15/mo at low traffic (pay per request)
- 100K requests/mo × $0.00000040/request = $0.04
- CPU: ~$10/mo for sustained usage
- Free tier: 2M requests/mo, 360K vCPU-seconds

### 2. Cloud Run — Bot Worker (Trading Bots)
**What:** Runs crypto + stock bot scan loops as always-on service
**Config:**
- Min instances: 1 (bots must run continuously)
- Max instances: 1
- CPU: 1 vCPU, Memory: 1GB
- Always-on CPU allocation

**Cost:** ~$25/mo (always-on)
- 1 vCPU × 730 hrs × $0.0000240/vCPU-sec = ~$25/mo

### 3. Cloud SQL — PostgreSQL
**What:** Managed PostgreSQL (same schema, zero code changes)
**Config:**
- Instance: db-f1-micro (shared core, 0.6GB RAM)
- Storage: 10GB SSD
- Region: us-central1
- Automated backups: daily

**Cost:** ~$7-10/mo
- db-f1-micro: $7.67/mo
- Storage: $0.17/GB/mo × 10GB = $1.70
- Backups: $0.08/GB/mo

### 4. Memorystore — Redis
**What:** Replaces in-memory Python caches (market pulse, prediction markets, market sensor)
**Config:**
- Basic tier, 1GB
- Region: us-central1

**Cost:** ~$6/mo (M1 basic 1GB)

**Alternative (FREE):** Use Cloud Run's built-in memory for caching. Current Python dict caches work fine at low scale. Add Redis later when you hit 1000+ users.

### 5. Vertex AI — Model Garden (LLM Calls)
**What:** Direct LLM calls without OpenRouter middleman

**Available Models:**
| Model | Vertex AI Name | Use Case | Cost per 1M tokens |
|-------|---------------|----------|-------------------|
| **Gemini 2.5 Flash** | gemini-2.5-flash | Screener, bot sentiment, fast validation | Input: $0.15, Output: $0.60 |
| **Gemini 2.5 Pro** | gemini-2.5-pro | Pattern recognition, deep analysis | Input: $1.25, Output: $5.00 |
| **Claude Sonnet 4** | claude-sonnet-4-6@001 | Research, fundamentals (via partner) | Input: $3.00, Output: $15.00 |
| **Claude Opus 4** | claude-opus-4-6@001 | Earnings, complex analysis (via partner) | Input: $15.00, Output: $75.00 |
| **Llama 3.1 405B** | meta/llama-3.1-405b | Self-hosted alternative | Input: $0.90, Output: $0.90 |

**Savings vs OpenRouter:**
- OpenRouter adds ~30-50% markup on API prices
- Vertex AI = direct Google pricing (Gemini) or partner pricing (Claude)
- **Estimated savings: 30-40% on LLM costs**

**Monthly LLM Cost Estimate (1000 users):**
| Call Type | Calls/day | Model | Tokens/call | Monthly Cost |
|-----------|-----------|-------|-------------|-------------|
| Bot validation (crypto) | 200 | Gemini Flash | 2K | $3.60 |
| Bot validation (stock) | 100 | Gemini Flash | 2K | $1.80 |
| Analyzer validation | 500 | Gemini Pro + Flash | 3K | $15.00 |
| 12-month prediction | 100 | Claude Sonnet | 4K | $7.20 |
| Screener vetting | 300 | Gemini Flash | 1.5K | $2.70 |
| Skills (research) | 50 | Gemini Pro | 5K | $4.50 |
| Market sensor | 48 | Gemini Flash | 500 | $0.05 |
| **Total** | **~1300/day** | | | **~$35/mo** |

vs OpenRouter: ~$50-60/mo for same volume

### 6. Cloud Storage — Static & Outputs
**What:** Serve static JS/CSS via CDN, store skill outputs (DOCX/XLSX)
**Config:**
- Standard storage class
- Lifecycle: delete skill outputs after 30 days

**Cost:** ~$1/mo (< 5GB)

### 7. Cloud Load Balancer + Managed SSL
**What:** HTTPS termination, global routing, free managed certs
**Config:**
- Serverless NEG backend (Cloud Run)
- Managed SSL certificate (auto-renew, no certbot needed)

**Cost:** ~$18/mo (minimum for external LB)
**Alternative (FREE):** Cloud Run's built-in HTTPS with custom domain mapping (no LB needed at low traffic)

### 8. Cloud Tasks / Pub/Sub
**What:** Job queue for skill execution, bot commands
**Config:**
- Cloud Tasks for skill jobs (async)
- Pub/Sub for bot start/stop commands between web and worker

**Cost:** ~$0-2/mo (first 1M operations free)

---

## TOTAL MONTHLY COST

### Minimal Setup (< 100 users, getting started)
| Component | Config | Cost |
|-----------|--------|------|
| Cloud Run (web) | Scale to zero | $5 |
| Cloud Run (bot worker) | 1 always-on | $25 |
| Cloud SQL | db-f1-micro | $8 |
| Vertex AI (LLM) | Pay per call | $10 |
| Cloud Storage | < 1GB | $0.02 |
| Domain + SSL | Cloud Run built-in | $0 |
| **Total** | | **~$48/mo** |

vs Current VPS: ~$20-40/mo + $30-50 OpenRouter = $50-90/mo

### Growth Setup (100-1000 users)
| Component | Config | Cost |
|-----------|--------|------|
| Cloud Run (web) | 1-5 instances | $15 |
| Cloud Run (bot worker) | 1 always-on | $25 |
| Cloud SQL | db-g1-small | $25 |
| Memorystore Redis | 1GB basic | $6 |
| Vertex AI (LLM) | Higher volume | $35 |
| Cloud CDN + Storage | Static assets | $5 |
| Load Balancer | External | $18 |
| **Total** | | **~$129/mo** |

### Scale Setup (1000-10000 users)
| Component | Config | Cost |
|-----------|--------|------|
| Cloud Run (web) | 2-10 instances | $50 |
| Cloud Run (bot workers) | 1 per 50 bot users | $75 |
| Cloud SQL | db-custom-2-4096 | $60 |
| Memorystore Redis | 3GB basic | $18 |
| Vertex AI (LLM) | High volume | $150 |
| Cloud CDN + Storage | Full CDN | $10 |
| Load Balancer | External | $18 |
| Cloud Monitoring | Basic | $0 |
| **Total** | | **~$381/mo** |

---

## CODE CHANGES NEEDED

### 1. Vertex AI LLM Adapter (Replace OpenRouter)

Create `vertex_adapter.py`:
```python
"""
Vertex AI LLM adapter — replaces OpenRouter with direct Google Cloud calls.
Supports Gemini (native) and Claude (partner models via Vertex).
"""
import os
import json
import vertexai
from vertexai.generative_models import GenerativeModel, Part

PROJECT_ID = os.getenv("GCP_PROJECT_ID")
REGION = os.getenv("GCP_REGION", "us-central1")

# Initialize once
vertexai.init(project=PROJECT_ID, location=REGION)

# Model mapping: old OpenRouter names → Vertex AI names
MODEL_MAP = {
    # Gemini (native, cheapest)
    "google/gemini-2.5-flash": "gemini-2.5-flash",
    "google/gemini-2.5-pro-preview": "gemini-2.5-pro",
    # Claude (partner models via Vertex)
    "anthropic/claude-sonnet-4-6": "claude-sonnet-4-6@001",
    "anthropic/claude-opus-4-6": "claude-opus-4-6@001",
    # DeepSeek (not on Vertex — fallback to Gemini)
    "deepseek/deepseek-chat-v3-0324": "gemini-2.5-flash",
    # Nvidia (not on Vertex — fallback to Gemini Pro)
    "nvidia/nemotron-3-super-120b-a12b": "gemini-2.5-pro",
}

def call_vertex_ai(model_name, messages, max_tokens=4096,
                   temperature=0.2, timeout=60):
    """Call Vertex AI model, compatible with OpenRouter message format."""
    vertex_model = MODEL_MAP.get(model_name, "gemini-2.5-flash")

    # Separate system prompt from user messages
    system = ""
    user_parts = []
    for msg in messages:
        if msg["role"] == "system":
            system = msg["content"]
        else:
            user_parts.append(msg["content"])

    prompt = "\n\n".join(user_parts)

    if vertex_model.startswith("claude"):
        # Use Anthropic partner model via Vertex
        from anthropic import AnthropicVertex
        client = AnthropicVertex(region=REGION, project_id=PROJECT_ID)
        response = client.messages.create(
            model=vertex_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text
    else:
        # Use Gemini natively
        model = GenerativeModel(
            vertex_model,
            system_instruction=system if system else None,
        )
        response = model.generate_content(
            prompt,
            generation_config={
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            },
        )
        return response.text
```

### 2. Swap in ai_validator.py

Change `_call_openrouter()` to check for Vertex AI:
```python
# At top of ai_validator.py
USE_VERTEX = os.getenv("USE_VERTEX_AI", "0") == "1"

def _call_llm(model, messages, **kwargs):
    if USE_VERTEX:
        from vertex_adapter import call_vertex_ai
        return call_vertex_ai(model, messages, **kwargs)
    else:
        return _call_openrouter(model, messages, **kwargs)
```

### 3. Dockerfile for Cloud Run

```dockerfile
FROM python:3.11-slim

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev libjpeg62-turbo-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install google-cloud-aiplatform anthropic[vertex]

COPY . .

# Cloud Run uses PORT env var
ENV PORT=8080
EXPOSE 8080

# Web service
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8080", \
     "--workers", "2", "--threads", "4", "--timeout", "300"]
```

### 4. Bot Worker Dockerfile

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install google-cloud-aiplatform anthropic[vertex]

COPY . .

# Bot worker entry point
CMD ["python", "bot_worker.py"]
```

### 5. Cloud SQL Connection

```python
# In db.py — add Cloud SQL connector
import os

if os.getenv("CLOUD_SQL_CONNECTION"):
    # Use Cloud SQL Python Connector (no IP allowlisting needed)
    from google.cloud.sql.connector import Connector
    connector = Connector()

    def get_cloud_sql_conn():
        return connector.connect(
            os.getenv("CLOUD_SQL_CONNECTION"),  # project:region:instance
            "pg8000",
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASS"),
            db=os.getenv("DB_NAME"),
        )
```

---

## DEPLOYMENT STEPS

### Step 1: GCP Project Setup
```bash
# Create project
gcloud projects create tradewiz-prod --name="TradeWiz"
gcloud config set project tradewiz-prod

# Enable APIs
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  aiplatform.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  redis.googleapis.com

# Set region
gcloud config set run/region us-central1
```

### Step 2: Cloud SQL
```bash
# Create PostgreSQL instance
gcloud sql instances create tradewiz-db \
  --database-version=POSTGRES_16 \
  --tier=db-f1-micro \
  --region=us-central1 \
  --storage-size=10GB \
  --storage-auto-increase

# Create database and user
gcloud sql databases create tradewiz --instance=tradewiz-db
gcloud sql users create tradewiz \
  --instance=tradewiz-db \
  --password=YOUR_STRONG_PASSWORD
```

### Step 3: Secrets
```bash
# Store secrets in Secret Manager
echo -n "YOUR_STRONG_PASSWORD" | gcloud secrets create db-password --data-file=-
echo -n "your-secret-key" | gcloud secrets create flask-secret-key --data-file=-
echo -n "your-openrouter-key" | gcloud secrets create openrouter-api-key --data-file=-
echo -n "your-stripe-key" | gcloud secrets create stripe-secret-key --data-file=-
echo -n "your-encryption-key" | gcloud secrets create encryption-key --data-file=-
```

### Step 4: Deploy Web App
```bash
# Build and deploy
gcloud run deploy tradewiz-web \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "DATABASE_URL=postgresql://tradewiz:PASSWORD@/tradewiz?host=/cloudsql/PROJECT:us-central1:tradewiz-db" \
  --set-env-vars "USE_VERTEX_AI=1,GCP_PROJECT_ID=tradewiz-prod,GCP_REGION=us-central1" \
  --set-secrets "SECRET_KEY=flask-secret-key:latest,STRIPE_SECRET_KEY=stripe-secret-key:latest,ENCRYPTION_KEY=encryption-key:latest" \
  --add-cloudsql-instances tradewiz-prod:us-central1:tradewiz-db \
  --min-instances 0 \
  --max-instances 10 \
  --memory 512Mi \
  --cpu 1 \
  --timeout 300 \
  --concurrency 80
```

### Step 5: Deploy Bot Worker
```bash
gcloud run deploy tradewiz-bots \
  --source . \
  --dockerfile Dockerfile.worker \
  --region us-central1 \
  --no-allow-unauthenticated \
  --set-env-vars "DATABASE_URL=postgresql://..." \
  --set-env-vars "USE_VERTEX_AI=1,GCP_PROJECT_ID=tradewiz-prod" \
  --set-secrets "SECRET_KEY=flask-secret-key:latest,ENCRYPTION_KEY=encryption-key:latest" \
  --add-cloudsql-instances tradewiz-prod:us-central1:tradewiz-db \
  --min-instances 1 \
  --max-instances 1 \
  --memory 1Gi \
  --cpu 1 \
  --timeout 3600 \
  --no-cpu-throttling
```

### Step 6: Custom Domain + SSL
```bash
# Map custom domain (free managed SSL)
gcloud run domain-mappings create \
  --service tradewiz-web \
  --domain tradewiz.market \
  --region us-central1

# Add DNS records as instructed by the output
```

### Step 7: Vertex AI Access
```bash
# Grant Cloud Run service account access to Vertex AI
gcloud projects add-iam-policy-binding tradewiz-prod \
  --member="serviceAccount:PROJECT_NUMBER-compute@developer.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Enable partner models (Claude)
# Go to: console.cloud.google.com/vertex-ai/model-garden
# Search for "Claude" → Enable → Accept terms
```

---

## ENV VARS COMPARISON

| Variable | Current (VPS) | GCP |
|----------|--------------|-----|
| DATABASE_URL | postgresql://... | postgresql://...?host=/cloudsql/... |
| SECRET_KEY | .env file | Secret Manager |
| OPENROUTER_API_KEY | Required | Optional (fallback) |
| USE_VERTEX_AI | N/A | "1" |
| GCP_PROJECT_ID | N/A | "tradewiz-prod" |
| GCP_REGION | N/A | "us-central1" |
| STRIPE_SECRET_KEY | .env file | Secret Manager |
| ENCRYPTION_KEY | .env file | Secret Manager |
| PORT | 5000 | 8080 (Cloud Run default) |

---

## MIGRATION CHECKLIST

- [ ] Create GCP project and enable APIs
- [ ] Create Cloud SQL instance and migrate data
- [ ] Store secrets in Secret Manager
- [ ] Create `vertex_adapter.py`
- [ ] Add `USE_VERTEX_AI` flag to `ai_validator.py` and `skills/llm_adapter.py`
- [ ] Create `Dockerfile.worker` for bot worker
- [ ] Create `bot_worker.py` entry point
- [ ] Deploy web app to Cloud Run
- [ ] Deploy bot worker to Cloud Run
- [ ] Map custom domain
- [ ] Enable Vertex AI partner models (Claude)
- [ ] Test all LLM calls via Vertex AI
- [ ] Update Stripe webhook URL
- [ ] Verify bot scan cycles running
- [ ] Monitor costs in Cloud Billing

---

## COST OPTIMIZATION TIPS

1. **Use Gemini Flash for everything possible** — 10x cheaper than Pro, 50x cheaper than Claude
2. **Vertex AI free tier**: First $300 in credits for new accounts
3. **Cloud Run free tier**: 2M requests/mo, 360K vCPU-sec free
4. **Cloud SQL**: Use db-f1-micro ($7/mo) until you have 500+ users
5. **Skip Redis initially** — Python in-memory caches work fine at low scale
6. **Skip Load Balancer** — Cloud Run has built-in HTTPS + custom domain
7. **Use committed use discounts** (CUDs) for Cloud SQL after 1 year
8. **Set budget alerts** at $50, $100, $200/mo

**First month with $300 free credits: effectively $0**

---

*Infinity Ventures Group LLC — Technical Architecture Document*
