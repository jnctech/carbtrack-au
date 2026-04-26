# CarbTrack AU — Change Register

| CR ID | Branch | Summary | Status | Date |
|---|---|---|---|---|
| CR-260315-1a-scaffold | feature/1a-scaffold | Phase 1 (Tasks 1A–1D): repo scaffold, 4 SQLModel tables, sources + foods routers, Docker, Gitea CI. PR #1. | Deployed | 2026-03-15 |
| CR-260315-hardening | fix/hardening-gaps | SQLite path hardening, *.db gitignore, GitHub Actions (quality gate, scorecard, dependency review, dependabot), SonarCloud project setup. PR #2. | Deployed | 2026-03-15 |
| CR-260315-2-staging | feature/2-staging-pipeline | Phase 2: Staging pipeline with conflict detection. 4 endpoints (list, submit, approve, reject), >5% carb variance hold, food_source_refs audit trail. 27 tests. | Deployed | 2026-03-15 |
| CR-260315-3-query-builder | feature/3-query-builder | Phase 3: Query builder + field mapping (Sonnet integration). 6 new endpoints: construct, sources, map-fields, query-template, staging/map. 30 new tests (84 total), 90% coverage. | Deployed | 2026-03-15 |
| CR-260315-gitleaks-hardening | fix/gitleaks-ci | Gitleaks secret scanning CI, centralized call_sonnet() with Anthropic exception handling, raw_json 1MB limit, mapped_data name validation, query_type Literal. PR #3. | Deployed | 2026-03-15 |
| CR-260315-4-ausnut-import | feature/4-ausnut-import | Phase 4: AUSNUT CSV bulk import script, batch approve script, seed_foods.json (52 common AU products), shared approve service, foods seeding on first run. 42 new tests (127 total), 85% coverage. PR #4. | Deployed | 2026-03-15 |
| CR-260315-5a-barcode-ui | feature/5a-barcode-ui | Phase 5A: Mobile-friendly barcode scanning UI. Camera-based EAN-13/UPC-A/EAN-8 via vendored html5-qrcode, manual entry fallback, serving-adjusted carb calculator. Static files served from FastAPI. Route ordering fix for /barcode/{barcode}. 8 new tests (135 total), 91% coverage. PR #5. | Deployed | 2026-03-15 |
| CR-260315-5a-off-import | feature/5a-off-import | Scanner OFF integration: auto-search Open Food Facts on CarbTrack 404, OFF→CarbTrack field mapping, dual import (staging with conflict detection, quick add direct to foods). New POST /staging/{id}/set-mapped endpoint. 9 new tests (144 total), 91% coverage. PR #6. | Deployed | 2026-04-26 |
