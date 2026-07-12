# Instabot

AI-powered Instagram comment auto-reply SaaS. Instabot listens for new Instagram comments via the official Meta webhook, generates natural replies with Google Gemini, and posts them back through the Instagram Graph API — with spam filtering, duplicate protection, and human-like random delays.

## Features

- **Meta Webhook Integration** — GET verification and POST event handling
- **Comment Storage** — Every comment persisted in PostgreSQL
- **AI Replies** — Gemini-powered responses with a witty, human social media manager persona
- **Duplicate Protection** — Never replies to the same comment twice
- **Spam Detection** — Ignores emoji-only, single-character, repeated, and obvious spam comments
- **Random Delay** — 10–90 second wait before replying for natural timing
- **Structured Logging** — Full observability of prompts, responses, and API calls
- **Cloud Run Ready** — Environment-variable configuration, Docker support, health checks

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Runtime | Python 3.12 |
| Framework | FastAPI + Uvicorn |
| AI | Google Gemini (`google-genai`) |
| Database | PostgreSQL + SQLAlchemy (async) |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Deployment | Docker, Google Cloud Run |

## Project Structure

```
project/
├── app/
│   ├── main.py              # Application entry point
│   ├── config.py            # Environment-based settings
│   ├── dependencies.py      # Dependency injection
│   ├── routes/
│   │   ├── webhook.py       # Meta webhook endpoints
│   │   └── health.py        # Health check
│   ├── services/
│   │   ├── gemini_service.py
│   │   ├── instagram_service.py
│   │   ├── comment_processor.py
│   │   └── comment_repository.py
│   ├── models/
│   │   ├── comment.py
│   │   └── setting.py
│   ├── database/
│   ├── schemas/
│   ├── utils/
│   │   ├── logging.py
│   │   └── spam.py
│   └── middleware/
├── alembic/                 # Database migrations
├── Dockerfile
├── requirements.txt
└── .env.example
```

## Setup

### Prerequisites

- Python 3.12+
- PostgreSQL 14+
- A Meta Developer App with Instagram Graph API access
- A Google AI Studio API key (Gemini)

### 1. Clone and install

```bash
git clone <your-repo-url>
cd instabot
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Create the database

```bash
createdb instabot
```

### 4. Run migrations

```bash
export $(grep -v '^#' .env | xargs)
alembic upgrade head
```

### 5. Start the server

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080
```

Visit `http://localhost:8080/docs` for the interactive API documentation (development only).

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `VERIFY_TOKEN` | Yes | Random string for Meta webhook verification |
| `META_ACCESS_TOKEN` | Yes | Long-lived Page access token with `instagram_manage_comments` |
| `GEMINI_API_KEY` | Yes | Google AI Studio API key |
| `DATABASE_URL` | Yes | PostgreSQL connection string |
| `INSTAGRAM_ACCOUNT_ID` | No | Instagram Business Account ID |
| `META_API_VERSION` | No | Graph API version (default: `v21.0`) |
| `GEMINI_MODEL` | No | Gemini model alias (default: `gemini-flash-lite-latest`; use `gemini-flash-latest` for higher quality) |
| `REPLY_DELAY_MIN_SECONDS` | No | Minimum reply delay (default: `10`) |
| `REPLY_DELAY_MAX_SECONDS` | No | Maximum reply delay (default: `90`) |
| `PORT` | No | Server port (default: `8080`, set by Cloud Run) |
| `APP_ENV` | No | `development` or `production` |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

## Running with Docker

### Build

```bash
docker build -t instabot .
```

### Run

```bash
docker run -p 8080:8080 \
  -e VERIFY_TOKEN=your_token \
  -e META_ACCESS_TOKEN=your_token \
  -e GEMINI_API_KEY=your_key \
  -e DATABASE_URL=postgresql://user:pass@host:5432/instabot \
  instabot
```

For local development with a PostgreSQL container:

```bash
docker run -d --name instabot-db \
  -e POSTGRES_USER=instabot \
  -e POSTGRES_PASSWORD=instabot \
  -e POSTGRES_DB=instabot \
  -p 5432:5432 \
  postgres:16-alpine

docker run -p 8080:8080 \
  --link instabot-db:db \
  -e DATABASE_URL=postgresql://instabot:instabot@db:5432/instabot \
  -e VERIFY_TOKEN=your_token \
  -e META_ACCESS_TOKEN=your_token \
  -e GEMINI_API_KEY=your_key \
  instabot
```

## Google Cloud Run Deployment

### 1. Build and push to Artifact Registry

```bash
gcloud auth configure-docker
docker build -t gcr.io/PROJECT_ID/instabot .
docker push gcr.io/PROJECT_ID/instabot
```

### 2. Deploy

```bash
gcloud run deploy instabot \
  --image gcr.io/PROJECT_ID/instabot \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "APP_ENV=production" \
  --set-env-vars "VERIFY_TOKEN=your_token" \
  --set-env-vars "META_ACCESS_TOKEN=your_token" \
  --set-env-vars "GEMINI_API_KEY=your_key" \
  --set-env-vars "DATABASE_URL=postgresql://user:pass@/instabot?host=/cloudsql/PROJECT:REGION:INSTANCE" \
  --add-cloudsql-instances PROJECT:REGION:INSTANCE \
  --memory 512Mi \
  --timeout 300 \
  --min-instances 1
```

> **Note:** Cloud Run has a 300-second request timeout. Reply delays (up to 90 s) run in background tasks, so webhook responses return immediately.

### 3. Use Secret Manager (recommended)

```bash
echo -n "your_token" | gcloud secrets create verify-token --data-file=-
echo -n "your_token" | gcloud secrets create meta-access-token --data-file=-
echo -n "your_key"   | gcloud secrets create gemini-api-key --data-file=-

gcloud run deploy instabot \
  --image gcr.io/PROJECT_ID/instabot \
  --set-secrets "VERIFY_TOKEN=verify-token:latest,META_ACCESS_TOKEN=meta-access-token:latest,GEMINI_API_KEY=gemini-api-key:latest"
```

## Meta App Configuration

### 1. Create a Meta App

1. Go to [Meta for Developers](https://developers.facebook.com/)
2. Create a new app → type **Business**
3. Add the **Instagram** product

### 2. Configure permissions

Your access token needs:

- `instagram_basic`
- `instagram_manage_comments`
- `pages_show_list`
- `pages_read_engagement`

Generate a long-lived Page access token via the Graph API Explorer or Business Manager.

### 3. Configure the webhook

1. In your Meta App → **Instagram** → **Webhooks**
2. Callback URL: `https://your-domain.com/webhook`
3. Verify Token: same value as your `VERIFY_TOKEN` env var
4. Subscribe to: **comments** (and **live_comments** if needed)

### 4. Connect Instagram Business Account

1. Link a Facebook Page to your Instagram Business/Creator account
2. Note the Instagram Business Account ID (`INSTAGRAM_ACCOUNT_ID`)

### 5. Test webhook verification

```bash
curl "https://your-domain.com/webhook?hub.mode=subscribe&hub.verify_token=YOUR_TOKEN&hub.challenge=test123"
# Expected: test123
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Service info |
| `GET` | `/health` | Health check (Cloud Run probe) |
| `GET` | `/webhook` | Meta webhook verification |
| `POST` | `/webhook` | Receive Instagram comment events |
| `GET` | `/docs` | Swagger UI (development only) |

## How It Works

```
Instagram Comment
       │
       ▼
Meta Webhook (POST /webhook)
       │
       ▼
Store in PostgreSQL
       │
       ▼ (background task)
Spam Check ──► Skip if spam
       │
       ▼
Random Delay (10–90 s)
       │
       ▼
Gemini AI Reply
       │
       ▼
Instagram Graph API Reply
       │
       ▼
Mark comment as replied
```

## Troubleshooting

### Webhook verification fails

- Confirm `VERIFY_TOKEN` in `.env` matches the token in Meta App settings
- Ensure your server is publicly reachable (use ngrok for local testing)
- Check logs for `webhook_verification_failed`

### Comments not triggering replies

- Verify webhook subscription includes **comments**
- Confirm `META_ACCESS_TOKEN` has `instagram_manage_comments` permission
- Check that the Instagram account is a Business/Creator account linked to a Facebook Page
- Review logs for `unsupported_webhook_field` or `spam_comment_ignored`

### Instagram API errors

| Error | Fix |
|-------|-----|
| `(#10) Application does not have permission` | Add `instagram_manage_comments` to your token |
| `(#100) Invalid parameter` | Verify comment ID format and token scope |
| `429 Too Many Requests` | Rate limited — retries are automatic with backoff |

### Database connection errors

- Verify `DATABASE_URL` format: `postgresql://user:pass@host:5432/dbname`
- For Cloud Run + Cloud SQL, use the Unix socket format shown above
- Run `alembic upgrade head` to ensure tables exist

### Gemini errors

- Verify `GEMINI_API_KEY` is valid at [Google AI Studio](https://aistudio.google.com/)
- Check quota limits on your Google Cloud project
- Review logs for `gemini_response` entries

### Local testing with ngrok

```bash
ngrok http 8080
# Use the ngrok HTTPS URL as your Meta webhook callback URL
```

## Future Roadmap

The architecture supports upcoming SaaS features:

- Multiple Instagram accounts (`account_id` columns already in schema)
- Configurable AI personalities (`settings` table + personality override in `GeminiService`)
- AI conversation memory (`memory_context` parameter ready)
- Human approval mode (queue replies before posting)
- Dashboard and analytics API
- Scheduled replies
- Multilingual support
- Rate limiting middleware
- User authentication

## License

MIT
