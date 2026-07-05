# JengaSec Competition Platform

The official platform for **JengaSec 2026** — Kenya's first full-spectrum enterprise cybersecurity simulation, organized by the Strathmore Cybersecurity Club (Nairobi, October 2026).

This single Django application serves:

- **The public event website** (`/`) — event info, registration CTA, and login.
- **The evaluation platform** (`/dashboard/…`) — submissions, AI-assisted judging, scoring, analytics, and reports, with role-based dashboards for every participant type.

Serving everything from one app is deliberate: one deployment, one origin, no public cross-app API — a smaller attack surface for an event full of people actively looking for one.

---

## How the platform works

```
Team registers (email for now → online registration later)
        │
        ▼
Login at /login/  ──►  routed by role from /dashboard/
        │
        ├─ Admin        → /dashboard/command/    (competition management)
        ├─ Blue Team    → /dashboard/blue/       (submit proposal → ship system as Docker image → watch live performance)
        ├─ Red Team     → /dashboard/red/        (access dockerized replica target → submit findings report)
        └─ Judge/Partner→ /dashboard/insights/   (review AI-suggested scores, override, approve)
```

Evaluation pipeline: **document upload → parsing → AI evaluation (Ollama) → rubric-based scoring → human judge review → final approval → reports & feedback release**. AI is decision support only — judges always have the final word, and every override is logged.

## Roles

Roles are Django auth **Groups**, created automatically by migration (`accounts/migrations/0001_create_roles.py`):

| Role | Group name | Dashboard |
|---|---|---|
| Administrator | *(is_staff / is_superuser)* | `/dashboard/command/` |
| Blue Team | `blue_team` | `/dashboard/blue/` |
| Red Team | `red_team` | `/dashboard/red/` |
| Judge | `judge` | `/dashboard/insights/` |
| Partner | `partner` | `/dashboard/insights/` |

Assign roles in Django admin (`/admin/` → Users → Groups). Users without a role see a "role not assigned" page after login.

## Directory structure

```
jengasec-competition-platform/
├── manage.py
├── requirements.txt
├── config/                  # Project settings, root URLs, WSGI/ASGI
├── accounts/                # Auth, user management, roles (Pair 1)
├── competitions/            # Competition lifecycle management (Pair 1)
├── submissions/             # Uploads, versioning, file storage (Pair 1)
├── judging/                 # Human judge review workspace (Pair 2)
├── rubrics/                 # Rubric builder: criteria + weights (Pair 2)
├── ai_engine/               # Ollama evaluation engine (Pair 2)
├── reports/                 # PDF/CSV report generation (Dev 5)
├── analytics/               # Dashboards, charts, leaderboards (Ahmed)
├── notifications/           # Email + in-app notifications (everyone)
├── dashboard/               # Role dispatch + role dashboards
├── services/                # Business logic layer — keep it OUT of views
│   ├── ai_service.py        #   prompt building + LLM evaluation
│   ├── scoring_service.py   #   weighted score aggregation
│   ├── report_service.py    #   ReportLab report generation
│   └── parser_service.py    #   PDF/DOCX text extraction
├── templates/
│   ├── website_landing_page.html   # Public event site (canonical copy)
│   ├── base.html                   # App shell: navbar + sidebar + footer
│   ├── components/                 # Reusable UI: button, card, table, modal,
│   │                               #   badge, alert, input, textarea, dropdown,
│   │                               #   pagination, navbar, sidebar
│   ├── accounts/  dashboard/  judging/  reports/  submissions/
├── static/
│   ├── css/                 # variables.css = design tokens (single source of truth)
│   ├── js/main.js           # Shared vanilla-JS behaviour + Chart.js defaults
│   ├── images/  icons/  logo/
└── media/                   # Uploaded submission documents (gitignored)
```

**Architecture rule:** `Browser → Templates → Views → Services → Models → PostgreSQL`. Views stay thin; anything that looks like business logic belongs in `services/`.

## Tech stack

| Component | Technology |
|---|---|
| Backend | Django 5 |
| Frontend | Django Templates + vanilla JavaScript |
| Charts | Chart.js |
| Database | SQLite (dev default) / PostgreSQL (Neon test DB, production) |
| AI evaluation | Ollama (Llama 3 / Mistral / Qwen) via HTTP |
| Document parsing | PyMuPDF, python-docx, pdfplumber |
| Reports | ReportLab, pandas |
| File storage | Django Media |
| Deployment | Docker + Gunicorn + Nginx (planned) |

## Getting started

Prerequisites: **Python 3.12+** and pip. (PostgreSQL and Ollama are optional until you work on those modules.)

```bash
git clone https://github.com/JengaSec-Org/jengasec-competition-platform.git
cd jengasec-competition-platform

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Create the database (SQLite by default) and role groups
python manage.py migrate

# Create your admin account
python manage.py createsuperuser

# Run it
python manage.py runserver
```

Then open <http://127.0.0.1:8000/> — the public site is at `/`, login at `/login/`, Django admin at `/admin/`.

To try the role dashboards: create users in `/admin/` and add them to the `blue_team`, `red_team`, `judge`, or `partner` group, then log in as them.

## Configuration (environment variables)

All optional in development — sensible defaults are built in.

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Session/CSRF signing key. **Set a real one in production.** | insecure dev key |
| `DJANGO_DEBUG` | Debug mode. **Must be `False` in production.** | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames | `localhost,127.0.0.1` |
| `JENGASEC_DB_NAME` | Setting this switches from SQLite to PostgreSQL | — |
| `JENGASEC_DB_USER` / `JENGASEC_DB_PASSWORD` / `JENGASEC_DB_HOST` / `JENGASEC_DB_PORT` / `JENGASEC_DB_SSLMODE` | PostgreSQL connection (Neon: set `JENGASEC_DB_SSLMODE=require`) | — |
| `OLLAMA_BASE_URL` | Ollama API endpoint | `http://localhost:11434` |
| `OLLAMA_MODEL` | Model used for evaluation | `llama3` |

## Design system

Defined once in [`static/css/variables.css`](static/css/variables.css) — use the CSS variables, never hard-code colours.

| Token | Value | | Token | Value |
|---|---|---|---|---|
| Primary Green | `#007A3D` | | Background | `#080C08` |
| Accent Green | `#00C853` | | Surface | `#FFFFFF` |
| Gold | `#C9960A` | | Border | `#CDD8CD` |
| Danger | `#B3000C` | | | |

Fonts: **Bebas Neue** (headings) · **DM Sans** (body) · **Space Mono** (technical labels).
Radii: cards 12px · buttons/inputs 8px.

Reusable UI lives in `templates/components/` — every component documents its own parameters in a comment at the top. Example:

```django
{% include "components/button.html" with label="Submit" variant="primary" %}
{% include "components/badge.html" with label="Pending" color="gold" %}
{% include "components/table.html" with headers=headers rows=rows empty="No data yet" %}
```

## Team workflow

- `main` is the integration base — **do not commit to it directly**. Branch from `main`, then open a pull request.
- Feature branches: `feature/authentication`, `feature/submissions`, `feature/ai-evaluation`, `feature/dashboard`, `feature/rubrics`, …
- Module ownership: **Pair 1** — core platform (auth, teams, competitions, submissions, admin) · **Pair 2** — evaluation (rubrics, AI engine, judging, scoring, reports) · **Ahmed** — UI/UX, analytics, integration. Everyone builds the UI for their own module using the shared components.

## Before production (security checklist)

- [ ] `DJANGO_DEBUG=False`, real `DJANGO_SECRET_KEY`, correct `DJANGO_ALLOWED_HOSTS`
- [ ] PostgreSQL with TLS (`JENGASEC_DB_SSLMODE=require`)
- [ ] Restrict or relocate `/admin/`
- [ ] HTTPS only; enable `SECURE_*`/HSTS settings
- [ ] Review upload validation before opening submissions
