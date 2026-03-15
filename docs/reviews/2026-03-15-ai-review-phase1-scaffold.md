# Review: Phase 1 Scaffold (Tasks 1A–1D)

**Date:** 2026-03-15
**Tools:** pr-review-toolkit:code-simplifier, pr-review-toolkit:silent-failure-hunter, pr-review-toolkit:code-reviewer
**Branch:** feature/1a-scaffold
**Files reviewed:** app/models.py, app/database.py, app/main.py, app/routers/foods.py, app/routers/sources.py, app/seed/sources.json, tests/conftest.py, tests/test_models.py, tests/test_sources.py, tests/test_foods.py, tests/test_health.py, .gitea/workflows/ci.yml, docker-compose.yml, Dockerfile

## Findings

### Fixed in commit f2b7e21

- [x] **CRITICAL** — SQLite DB path (`./carbtrack.db`) outside Docker volume (`/app/data`) — data lost on container restart. Fixed: set `DATABASE_URL` in compose environment to `sqlite:////app/data/carbtrack.db`.
- [x] **CRITICAL** — `GET /foods/{id}` returned soft-deleted foods — inconsistent with list endpoint active filter contract. Fixed: returns 404 for inactive foods unless `include_inactive=true`.
- [x] **HIGH** — `create_food`/`update_food` gave raw 500 on duplicate barcode (IntegrityError unhandled). Fixed: catch IntegrityError, return 409.
- [x] **HIGH** — Gitea CI actions not SHA-pinned — supply chain risk. Fixed: pinned checkout and setup-python to full SHAs.
- [x] **HIGH** — CI missing `--cov` flag — SonarCloud would have zero coverage data. Fixed: added `--cov=app --cov-report=xml`.
- [x] **MEDIUM** — `test_tables_created` leaked a database connection (`engine.connect()` not closed). Fixed: wrapped in context manager.
- [x] **MEDIUM** — `database.py:36` variable named `count` held a `Source` object, not an int. Fixed: renamed to `existing`.
- [x] **LOW** — `foods.py` used `datetime.now(timezone.utc)` instead of `_utcnow()` from models. Fixed: reuse shared helper.

### Deferred (valid but not blocking Phase 1)

- [ ] **MEDIUM** — No `response_model` on food endpoints — future `raw_response_json` leakage risk when relationship loading is added. **Revisit in Phase 2** when FoodSourceRef joins are introduced.
- [ ] **MEDIUM** — `FoodCreate`/`FoodUpdate` repeat 14 identical field definitions — consider shared base model. **Revisit if a third schema appears.**
- [ ] **MEDIUM** — Seed data parsing/validation errors crash app with unhelpful traceback. **Acceptable for Phase 1** — fail-fast on bad seed data is correct behaviour for a file we control.
- [ ] **MEDIUM** — Health endpoint does not verify database connectivity. **Revisit when Postgres** — Docker healthcheck catches startup failures for now.
- [ ] **MEDIUM** — `DATABASE_URL` silently defaults to SQLite with no log message. **Revisit when Postgres** — add startup log of active backend.
- [ ] **LOW** — No-op PATCH (empty body) still stamps `updated_at`. Minor semantic issue, not worth guarding.
- [ ] **LOW** — `conftest.py` runs `drop_all` on in-memory SQLite (harmless no-op). Not worth changing.

## Resolution

All HIGH+ findings resolved in commit `f2b7e21`. Deferred items tracked here for Phase 2+ pickup. No security vulnerabilities identified (OWASP top 10 checked). Spec compliance verified: per-100g storage, energy_kj naming, soft deletes, active filter, repr exclusion, seed guard, portable types.
