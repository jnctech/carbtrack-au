# CarbTrack AU — Claude Code Context

## Project Purpose

**CarbTrack AU** is a self-hosted Docker service providing a validated Australian food carbohydrate database for managing dietary intake for a child with Type 1 Diabetes.

**Primary user:** A parent tracking carb intake for a diabetic child to inform insulin dosing decisions.

**Carbohydrates are the hero metric.** All nutrition values are stored, but `carbs_per_100g` drives schema priority, conflict detection thresholds, and UI hierarchy.

**Three design principles:**
1. **Data integrity** — every food entry attributed to a validated Australian source with confidence scoring
2. **Conflict detection** — sources disagreeing >5% on carb values are surfaced and held for manual resolution before committing
3. **Barcode-first schema** — barcode lookup supported from day one; scanning UI is a future phase

---

## Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.12 | |
| API framework | FastAPI | Auto-generates OpenAPI docs at `/docs` |
| ORM | SQLModel | Combines SQLAlchemy + Pydantic — single model definition |
| Database | SQLite (default) | Postgres-ready — swap driver only, no schema changes |
| Container | Docker + Compose | Deployed on Docker-VM 01 (${DEPLOY_HOST}) |
| Network | servers VLAN | servers VLAN subnet |
| Port | 8765 | Avoids conflicts with existing Docker services |
| Test framework | pytest | `coverage.xml` output for SonarCloud |
| Linter | ruff | |
| AI integration | `anthropic` SDK | Claude Sonnet only, query construction + field mapping only |

**Postgres migration path:** change `DATABASE_URL` to `postgresql+asyncpg://...` and add `asyncpg` to `requirements.txt`. No schema or model changes. This is why SQLModel was chosen.

**Do not use Postgres-specific SQL or SQLite-specific types anywhere.**

---

## AI Usage Policy

This is a deliberate constraint, not a limitation.

| Model | Role |
|---|---|
| Claude Opus | One-time source registry investigation only (already completed — baked into `app/seed/sources.json`) |
| Claude Sonnet | Query template construction and field mapping only — never in the runtime data path |
| No AI | Runtime data retrieval, validation, conflict detection, or storage |

**Sonnet is used in exactly two places:**
1. `POST /query-builder/construct` — constructs API call templates; server never executes them
2. `POST /staging/{id}/map` — maps raw JSON response to schema fields; user reviews before approval

**Model:** always `claude-sonnet-4-5`, set via `SONNET_MODEL` env var. Never hardcode. Never use Opus at runtime.

**Max tokens:** 400 (query construction), 600 (field mapping).

---

## Quality Constraints (non-negotiable)

- **SonarCloud Grade A** from first commit — do not merge if failing
- **SonarCloud project key:** `jnctech_carbtrack-au` / org: `jnctech`
- **≥ 80% coverage**, cognitive complexity ≤ 15, zero code smells on new code
- **Analysis target branch:** `develop`
- All GitHub Actions **SHA-pinned** to full commit SHAs — never tag references

---

## Branch Strategy

| Branch | Purpose |
|---|---|
| `main` | Stable, deployable. GitHub default branch. |
| `develop` | Active development — SonarCloud analysis target |
| `feature/*` | Short-lived, branch off `develop` |

PRs to `develop` and `main` blocked by the quality gate workflow.

---

## Commit Conventions

Conventional commits with scope:

```
feat(foods): add barcode lookup endpoint
fix(staging): correct conflict detection threshold comparison
chore(deps): update fastapi to 0.115.1
docs: update ROADMAP Phase 2 scope
ci: pin actions/checkout to SHA
```

**AI-assisted commits must include a `Co-Authored-By` trailer** — match the model used:

```
Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>
Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
```

---

## Repository Structure

```
carbtrack-au/
├── .gitea/workflows/
│   └── ci.yml                  # Gitea Actions — inner loop (lint, test, pass/fail)
├── .github/
│   ├── workflows/
│   │   ├── quality-gate.yml    # SonarCloud, coverage, gitleaks — PR to main/develop
│   │   ├── scorecard.yml       # OpenSSF Scorecard — weekly + push to main
│   │   └── dependency-review.yml
│   └── dependabot.yml
├── app/
│   ├── main.py                 # FastAPI entry point
│   ├── database.py             # Engine setup, create_all(), seed on first run
│   ├── models.py               # All SQLModel table definitions
│   ├── routers/
│   │   ├── foods.py            # CRUD + barcode lookup
│   │   ├── sources.py          # Source registry read endpoints
│   │   ├── staging.py          # Submit, list, approve, reject
│   │   └── queries.py          # Query builder (Sonnet-assisted)
│   └── seed/
│       ├── sources.json        # Baked Opus source registry (Tier 1/2/3)
│       └── seed_foods.json     # Starter AU food entries (Phase 4+)
├── docs/
│   ├── internal/               # Non-shippable docs — NOT committed (gitignored)
│   ├── reviews/                # AI review audit trail
│   └── CHANGE-REGISTER.md
├── scripts/
│   └── import_csv.py           # AUSNUT bulk import helper
├── tests/
├── .env.example
├── .gitleaks.toml
├── .pre-commit-config.yaml
├── CHANGELOG.md
├── CLAUDE.md
├── CONTRIBUTING.md
├── Dockerfile
├── LICENSE                     # MIT
├── README.md
├── ROADMAP.md
├── SECURITY.md
├── docker-compose.yml
├── requirements.txt
└── sonar-project.properties
```

---

## Database Schema

Four tables defined in `app/models.py` using SQLModel. All Postgres-compatible — no SQLite-specific types.

### `sources`
Seeded from `app/seed/sources.json` on first run. Read-only at runtime except via admin endpoint.
- Tier 1: AUSNUT 2011-13 (FSANZ), Open Food Facts AU, GI Foundation AU
- Tier 2: Diabetes Australia, Eat For Health (NHMRC), FoodWorks Professional
- Tier 3: CalorieKing AU, NUTTAB 2010, Nutritionix API

### `foods`
Canonical food database. Written only after staging review and approval. Never hard-deleted (`active=false`).
- `carbs_per_100g` — critical field, drives conflict detection
- `source_confidence` — 0.0–1.0, lower if multi-source conflict
- `conflict_flag` — true if sources disagree >5% on carbs
- `barcode` — unique, nullable (Phase 5 scanning)

### `food_source_refs`
Every source's reported value for a food — full audit trail. Inserted on every staging approval.

### `staging`
Foods live here after API import, before user approval. Nothing in staging affects `foods`.
- `raw_data` — verbatim source API response
- `mapped_data` — Sonnet-mapped JSON matching schema (user reviews before approve)
- `status` — `pending` | `approved` | `rejected` | `conflict`

---

## Conflict Detection Rule

On `POST /staging/{id}/approve`:
1. Check `food_source_refs` for existing entries matching name+brand (or barcode)
2. If found: compare `reported_carbs` vs `mapped_data.carbs_per_100g`
3. If difference **> 5%**: set `staging.status = 'conflict'`, populate `conflict_notes`, do NOT promote to `foods`
4. If difference **≤ 5%** or no existing refs: promote to `foods`, insert `food_source_refs` row

---

## Data Conventions

- **All nutrition values per 100g** as the base unit — never store serving-adjusted values
- `raw_response_json` stored in `food_source_refs` — **never log this field**
- `ANTHROPIC_API_KEY` in `.env` only — never hardcode
- IPs and hostnames in env vars with sensible defaults — never hardcode `${DEPLOY_HOST}`
- Soft deletes only — no `DELETE FROM foods` ever

---

## Test Conventions

- **Framework:** pytest
- **Coverage output:** `coverage.xml` (SonarCloud) — run with `pytest --cov=app --cov-report=xml`
- **Required coverage:** barcode lookup, conflict detection trigger, full staging approve/reject flow
- **Mock Sonnet** in tests — never make live API calls in CI
- Tests live in `tests/` mirroring `app/` structure

---

## CI/CD Overview

**Gitea Actions** (${GITEA_HOST}:3000) — every push to `develop`, PR to `main`:
- Checkout → pip install → ruff lint → pytest → pass/fail only
- No deployment step — deploy is manual `docker compose pull && docker compose up -d`

**GitHub Actions** — PR to `main`/`develop`:
- Quality gate: ruff, pytest + coverage, SonarCloud scan
- Scorecard: weekly + push to main/develop (SARIF → Security tab)
- Dependency review: blocks moderate+ CVEs and copyleft licenses on PRs

**All GitHub Actions SHA-pinned.** `github-actions` ecosystem in Dependabot keeps SHAs current.

---

## Internal Documentation

Non-shippable documentation (specifications, design docs, test plans, AI prompts, decision records) lives in `docs/internal/`. This directory is excluded from version control via `.gitignore` and must not be committed to any public or private repo. Files in `docs/internal/` are shared between development environments (e.g. Claude Code, devcontainers, local workstations) via out-of-band sync — not git. When creating or referencing internal docs, always use `docs/internal/` as the base path. Never move internal docs to a tracked directory or include their contents in commits.

---

## Session Start Protocol

At the start of every session, before beginning work:

1. Read `docs/ISSUES.md` — check open issues, priorities, and deploy status
2. Read `docs/CHANGE-REGISTER.md` — check for items in Staged state awaiting confirmation
3. Confirm with user: "Current priority is [X]. Continue with that or work on something else?"
4. **Deploy gate:** Do not start new work if merged PRs have configs in Staged state that haven't been confirmed as running on Docker-VM 01

This prevents drift and scope creep.

---

## Session Handoff Protocol

At the end of every session, create a **new** handoff file at `~/Code/handoffs/YYYY-MM-DD-<short-topic>.md`. One file per session — do not append to an existing file.

The handoff file must contain:

1. **What was done** — branches, PRs, commits (with SHAs)
2. **Tracking updates** — which docs were updated (`docs/ISSUES.md`, `docs/CHANGE-REGISTER.md`)
3. **Gaps / remaining work** — numbered list with priority
4. **Infrastructure state** — any container configs in Staged status awaiting confirmation on Docker-VM 01
5. **Next session prompt** — exact prompt to resume work

This ensures the next session (same or different model) can pick up without re-exploring.

---

## Change Tracking

- `docs/CHANGE-REGISTER.md` — CR-nnn entries for every merged change
- `docs/reviews/` — AI review audit trail with findings and resolutions
- Update both before raising a PR

---

## Pre-PR Checklist

1. `ruff check .` — zero errors
2. `pytest --cov=app --cov-report=xml` — 100% pass, ≥ 80% coverage
3. `/simplify` on changed code
4. `pr-review-toolkit:silent-failure-hunter` on changed files
5. `pr-review-toolkit:code-reviewer` on the diff
6. `docs/CHANGE-REGISTER.md` updated with new CR entry
7. `docs/reviews/` updated with review findings
8. README and docs updated to match changes
9. Branch up to date with `develop`
10. Working tree clean
11. PR target: `jnctech/carbtrack-au`, branch `develop`

---

## Deployment

```bash
# On Docker-VM 01 (${DEPLOY_HOST})
docker compose pull
docker compose up -d
```

Service available at `http://${DEPLOY_HOST}:8765`. API docs at `/docs`.

**Security:** No auth by default — network-segmented to servers VLAN. If exposing beyond servers VLAN, add Bearer token auth before doing so.

---

## Editor

Development machine: Windows 11, editor Zed. Target environment: Docker-VM 01 on servers VLAN (servers VLAN subnet). Gitea at `http://${GITEA_HOST}:3000/jnctech/carbtrack-au`.
