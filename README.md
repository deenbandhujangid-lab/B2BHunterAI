# B2B Hunter AI

100% free, zero-cost local B2B Lead Scraper & Email Verifier running on a single PC.

## Architecture

```
├── backend/          Python 3.11 + FastAPI (Async REST API)
│   ├── SQLite       via Async SQLAlchemy 2.0
│   ├── Playwright   Web scraper (headless/headful)
│   ├── dnspython    MX record lookup
│   └── aiosmtplib   SMTP handshake verification
└── frontend/         Next.js 14 Admin Dashboard
```

## Quick Start

### 1. Backend Setup

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

pip install -r requirements.txt
playwright install chromium
```

Start the API server:

```bash
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 8002
```

API docs: http://localhost:8002/docs

### 2. Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Dashboard: http://localhost:3002

## Usage

1. Open the dashboard at http://localhost:3002
2. Fill in the scrape form:
   - **Location**: e.g. `Bangalore`
   - **Category**: e.g. `corporate`
   - **Role**: e.g. `founder`
   - **Target Count**: 1000–2000 (daily recommended)
3. Click **Start Scraping** — job runs in background
4. View results in the Leads table, filter by verification status
5. Export as CSV or JSON

### Example

> Location: Bangalore | Category: corporate | Role: founder

Scrapes all Bangalore corporate company founders' email IDs.

## Daily Automation

Enable **Run daily automatically** when creating a job. The scheduler runs at 6 AM and re-enqueues recurring jobs to hit the 1k–2k daily target.

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/v1/jobs` | Create scrape job |
| GET | `/api/v1/jobs` | List all jobs |
| GET | `/api/v1/leads` | List leads (filterable) |
| POST | `/api/v1/verify` | Verify single email |
| POST | `/api/v1/verify/bulk` | Bulk verify leads |
| POST | `/api/v1/export` | Export CSV/JSON |
| GET | `/api/v1/stats` | Dashboard statistics |

## Email Verification Pipeline

1. **Syntax check** — email-validator
2. **MX lookup** — dnspython DNS query
3. **SMTP handshake** — aiosmtplib RCPT TO (no email sent)

## Scraper Sources (Free)

- Google search results for company/role queries
- Company website contact/about/team pages
- Public LinkedIn profile URLs via Google
- Pattern-based email generation (firstname@domain.com)

## Cost: $0

No paid APIs, no cloud DB, no Redis. Everything runs locally on your PC.
