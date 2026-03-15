# CLAUDE.md — CarbTrack AU

## Project Purpose
CarbTrack AU is a self-hosted API providing a validated Australian food carbohydrate database for Type 1 Diabetes management. `carbs_per_100g` is the hero metric driving schema priority, conflict detection, and UI.

## Tech Stack
**Python 3.12** | **FastAPI** | **SQLModel** | **SQLite** (Postgres-ready) | **Docker** | **ruff** | **pytest**

## Hard Constraints (Non-Negotiable)
1. **Data Rules**: All nutrition values stored strictly `per 100g`. Never use serving-adjusted values. Soft deletes only (`active=false`) — never `DELETE FROM foods`.
2. **Database Portability**: No Postgres-specific SQL or SQLite-specific types. Migration is `DATABASE_URL` swap only.
3. **AI Policy**: Claude Sonnet (`claude-sonnet-4-5` via `SONNET_MODEL` env var) restricted to query construction and field mapping only. Zero AI in runtime data retrieval, conflict detection, or storage. Max tokens: 400 (query), 600 (mapping).
4. **Quality Gates**: Zero `ruff` errors, ≥80% test coverage, cognitive complexity ≤15, SonarCloud Grade A (project: `jnctech_carbtrack-au`, org: `jnctech`, target branch: `develop`). All GitHub Actions SHA-pinned.
5. **Secrets**: `ANTHROPIC_API_KEY` in `.env` only — never hardcode, never log. `raw_response_json` in `food_source_refs` — never log. IPs as env vars — never hardcode.
6. **Commits**: Conventional commits with scope. AI-assisted commits must include `Co-Authored-By: Claude <Model> <Version> <noreply@anthropic.com>` — match the model used (Opus or Sonnet).

## Branch Strategy
| Branch | Purpose |
|---|---|
| `main` | Stable, deployable. GitHub default. |
| `develop` | Active development — SonarCloud analysis target |
| `feature/*` | Short-lived, branch off `develop` |

PRs target `jnctech/carbtrack-au` branch `develop` — never upstream unless explicitly told otherwise.

## Common Commands
* **Lint**: `ruff check .`
* **Test**: `pytest --cov=app --cov-report=xml` (always mock Sonnet — no live API calls in CI)
* **Run**: `docker compose up -d` (deploys to `${DEPLOY_HOST}:8765`, API docs at `/docs`)

## Internal Documentation
Non-shippable docs live in `docs/internal/` — gitignored, never committed. Shared between environments via out-of-band sync.

## Reference Docs (read when relevant)
* `docs/internal/architecture.md` — DB schema (foods, staging, sources, food_source_refs), conflict detection rules (>5% carb variance), staging pipeline flow
* `docs/internal/cicd-pipeline.md` — Gitea/GitHub workflows, SonarCloud config, OpenSSF Scorecard, Docker-VM 01 deployment
* `docs/internal/ai-review-workflow.md` — Pre-PR checklist, review audit trail, change register process
* `docs/internal/data-conventions.md` — Source tier confidence mapping, barcode schema, nutrition storage rules, seed data format
