# AGENTS.md — Coding Rules & Conventions

Guidance for humans and AI agents working in this repository. Follow the existing code
first; these rules describe it, they do not replace reading the file you are editing.

**Docs map:** [PRD.md](PRD.md) · [architecture.md](architecture.md) · [database.md](database.md) · [api.md](api.md) · [README.md](README.md)

---

## 1. Ground rules

1. **Make the smallest change that solves the problem.** Do not reformat untouched code,
   rename things opportunistically, or mix refactors with fixes.
2. **Preserve the "why" comments.** Many comments record measured calibration data,
   provider quirks, and failure modes. Deleting them makes the next change dangerous.
3. **Do not weaken behaviour to make a check pass.** No skipped tests, broad `except`,
   ignored lint/type errors, or assertions relaxed to match a bug.
4. **Never introduce a secret.** Credentials belong in `backend/.env` (git-ignored) or the
   Email setup UI, never in source, tests, logs, or docs.
5. **Do not commit, push, or open PRs unless asked.** When asked, stage only your own files.
6. **Keep the document set in sync.** If you change a table, endpoint, env var, or pipeline
   stage, update [database.md](database.md), [api.md](api.md), [architecture.md](architecture.md),
   or this file in the same change.

---

## 2. Repository map

```
backend/app/api/v1/endpoints/   HTTP layer (thin)
backend/app/services/           domain logic (analysis, LLM, Slack, email)
backend/app/models/             SQLAlchemy models
backend/app/schemas/            Pydantic request/response models
backend/app/core/               config, DB session, JWT/bcrypt, auth deps
backend/migrations/versions/    Alembic revisions (append-only)
backend/tests/                  pytest
frontend/src/api/               typed HTTP client + error helpers
frontend/src/hooks/             shared state/polling hooks
frontend/src/components/        layout + presentational components
frontend/src/pages/             route-level screens
ml/                             ShelfAnalyzer + training/inference scripts
```

Where new code goes:

| You are adding… | Put it in… |
|-----------------|------------|
| A route | `backend/app/api/v1/endpoints/`, register it in `api.py`, add a schema |
| Reusable business logic | `backend/app/services/` |
| A table/column | model in `backend/app/models/`, export in `models/__init__.py`, new migration |
| A request/response shape | `backend/app/schemas/` |
| A reusable data call | `frontend/src/api/client.ts` (+ types) |
| A screen | `frontend/src/pages/` + route in `App.tsx` |
| Cross-page state | `frontend/src/hooks/` |

---

## 3. Commands

### Backend (from `backend/`)

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate    POSIX: source .venv/bin/activate
pip install -r requirements.txt

alembic upgrade head
uvicorn app.main:app --reload --port 8000
python scripts/seed.py            # idempotent sample data
python -m pytest                  # full suite
python -m pytest tests/test_captures_endpoint.py -q
```

### Frontend (from `frontend/`)

```bash
npm ci                # use the lockfile; do not switch package managers
npm run dev           # Vite on :5173
npm test              # vitest run
npm run test:watch
npx tsc --noEmit      # type check
npm run build         # tsc && vite build
```

### ML (from `ml/`)

```bash
pip install -r requirements.txt
python -m pytest tests/test_shelf_analyzer.py -q
python scripts/detect.py --image path/to/shelf.jpg
```

### Docker Compose (repo root)

```bash
docker compose up --build
docker compose logs -f backend
docker compose exec backend alembic upgrade head
docker compose down -v        # destroys the Postgres volume — local only
```

Run the backend suite and `npx tsc --noEmit` before calling work done. If a check cannot
run (missing weights, no DB, no network), say so explicitly rather than implying success.

---

## 4. Python conventions (backend & ML)

- **Version:** Python 3.10. Use `from __future__ import annotations` in modules that use
  modern annotations.
- **Typing:** annotate function signatures, including return types (`-> bool`,
  `-> list[str]`, `-> Page[T]`). Use `Optional[...]`/`| None` consistently with the file.
- **Formatting:** 4-space indentation, roughly 100-column lines, blank line between
  top-level definitions, imports grouped stdlib → third-party → `app.*`.
- **Naming:** modules/functions `snake_case`, classes `PascalCase`, constants `UPPER_CASE`.
  Private helpers start with `_`.
- **Docstrings:** explain intent and the failure mode being prevented, not the syntax.
  One-line docstrings are fine for obvious endpoints; non-obvious decisions need the
  reasoning (the codebase already documents calibration measurements and provider quirks).
- **Comments:** only where they add information the code cannot convey. Do not narrate
  obvious statements.
- **Logging:** `logger = logging.getLogger(__name__)`, lazy `%s` formatting. No `print` in
  application code (`ml/shelf_analyzer.py`'s CLI and arbitration diagnostics are the
  documented exception).
- **Config:** read `settings` from `app.core.config`. Never call `os.environ` directly in
  application code.
- **Services must not raise for external failures.** Slack/email/LLM helpers return
  `False`/`[]` and log; callers record the outcome. Only validation errors raise
  `ValueError` (endpoints translate those to `400`).
- **Datetimes:** always `datetime.now(timezone.utc)`; new timestamp columns get a
  Python-side `default` (not only `server_default`), because SQLite cannot evaluate
  Postgres `now()`.
- **Pydantic v2:** use `ConfigDict(from_attributes=True)`, `field_validator(..., mode="before")`
  for normalisation, and `PlainSerializer` for output formatting (see `schemas/common.py`).

### SQLAlchemy models

- Inherit `Base` from `app.core.database`.
- Export every new model in `backend/app/models/__init__.py` so Alembic autogenerate sees it.
- Use `ondelete="CASCADE"` on foreign keys and `cascade="all, delete-orphan"` on the
  relationship that owns the child rows.
- Avoid reserved column names; `Detection.class_ = Column("class", …)` shows the pattern.
- Add `index=True` for columns used in filters/joins.

---

## 5. API conventions

- Routers are composed in `backend/app/api/v1/api.py`; endpoint modules declare routes
  relative to their prefix.
- Every endpoint declares `response_model`, a `summary`, and an explicit success
  `status_code` where it is not 200.
- Authenticate with `current_user: User = Depends(get_current_user)` on every private
  route. There is no anonymous access to tenant data.
- **Never trust client scope.** `store_id` comes from `current_user`; a body/query
  `store_id` is ignored. Verify ownership of nested resources through
  `_assert_camera_belongs_to_user`.
- Hide resources outside the caller's store. Reports return `404`; camera ownership
  failures use `403` via `_assert_camera_belongs_to_user`. Follow the existing helper
  rather than inventing a third convention.
- Use `Page[T]` for paginated lists; cap `limit` at 200 with `Query(..., ge=1, le=200)`.
- Error messages must tell the caller what to do next, not just what failed.
- Keep endpoints thin: validation + scoping + serialisation. Business logic belongs in
  `services/`.
- Attach joined context with `joinedload` (see `_CONTEXT_LOAD`) rather than per-row queries.

---

## 6. Database and migrations

- **Never edit an applied migration.** Add a new revision.
- Generate revisions from `backend/` so `script_location = migrations` resolves:
  ```bash
  alembic revision --autogenerate -m "add x to y"
  alembic upgrade head
  ```
- Import `app.models` before autogenerate or the tables will be missing.
- Use `op.batch_alter_table` for `ALTER` operations so SQLite works.
- New `nullable=False` columns need a `server_default` so existing rows stay valid.
- Add a downgrade path even if it is rarely used.
- Keep `schema` semantics documented in [database.md](database.md): fraction vs percentage,
  `low_confidence`, status vocabulary, cascade behaviour.
- Do not introduce dialect-specific SQL in ORM defaults; prefer Python-side values.

---

## 7. Testing rules

### Backend (`pytest`)

- `backend/tests/conftest.py` blanks **all** email credentials for every test. Never send a
  real email or Slack message from a test.
- Mock at the narrowest boundary:
  - Slack: `httpx.post` / `slack_service._post`.
  - Email: `smtplib` or `httpx.post`, or `email_service.notify_high_urgency`.
  - Pipeline: monkeypatch `capture_pipeline.SessionLocal`, `analyzer_service.analyze`,
    and `capture_pipeline.generate_shelf_report` (they are module-scope on purpose).
- Use FastAPI's `TestClient` and real SQLite test databases; assert status **and** body.
- Cover the negative path: unauthenticated (`401`), cross-store access (`403`/`404`),
  invalid upload (`400`), duplicate signup (`409`).
- Every bug fix gets a regression test that fails before the fix and names the user-visible
  behaviour in its docstring.

### Frontend (`vitest` + Testing Library)

- Tests live in `__tests__/` next to the code.
- Prefer accessible queries (`getByRole`, `getByLabelText`); use `userEvent` for interaction.
- Mock `../api/client` or `fetch`; never depend on a running backend.
- Assert what the user sees (labels, messages, disabled states), not implementation details.
- Clean up timers/intervals; `jsdom` will not fail loudly on leaks.

### ML

- `ml/tests/test_shelf_analyzer.py` covers geometry, NMS, fill math, and quality gating.
- Do not weaken thresholds to make a fixture pass; recalibrate deliberately and record the
  measurement in the comment.

---

## 8. Frontend conventions

- **TypeScript strict** (`strict`, `noUnusedLocals`, `noUnusedParameters`). No `any` unless
  unavoidable and explained; no non-null assertions masking a real absence.
- **Components:** function components, one default export per page, named exports for
  reusable pieces. Hooks start with `use`.
- **HTTP:** all calls go through `src/api/client.ts`. Do not call `fetch` directly from a
  page. Use the existing `ApiError`/`describeError`/`isAbortError` helpers.
- **Types mirror the API.** If a response shape changes, update `client.ts` types and
  [api.md](api.md) together.
- **Effects:** create an `AbortController`, pass its signal, and ignore `AbortError` on
  unmount. Cancel timers/intervals in cleanup.
- **State:** local `useState` + `useEffect` is the default. Use the existing `useAuth`
  module-level listener pattern only for genuinely global state; do not add a state library.
- **Styling:** Tailwind utility classes, existing spacing/colour vocabulary. Use
  `lucide-react` icons rather than new icon libraries.
- **Accessibility:** label inputs, give buttons explicit text or `aria-label`, keep keyboard
  focus visible, do not encode meaning in colour alone.
- **UX honesty:** show loading, empty, and error states. A `low_confidence` report must read
  "Unable to assess", never "100 %".
- **Dependencies:** do not add one without a clear need; check the project already uses an
  equivalent library first. Recharts is already the chart library and is lazy-loaded.
- **Build:** `VITE_API_URL` is inlined at build time; changing it at runtime has no effect.

---

## 9. Security rules

- No secrets in tracked files. `.env` is ignored; `backend/.env.example` documents keys with
  empty values only.
- Never log, return, or echo a password, API key, SMTP password, or raw token. Mask keys
  (`re_abc…wxyz`) as `email_setup.mask` does.
- `SECRET_KEY` is mandatory in production/staging; never add a hardcoded fallback.
- Passwords are bcrypt-hashed; truncate to 72 bytes consistently on hash and verify.
- Every new tenant-scoped query must be filtered by the authenticated user's `store_id`.
- New mutations should reject cross-origin browser requests in a real deployment; the
  current `CORS` policy (`*`) is development-only and must be narrowed before going public.
- Treat uploaded files as untrusted: validate content type, generate the stored filename,
  and never execute or serve them from a path derived from user input.

### Email/alert credential rules

- One installation-wide transport, not per-user credentials.
- Verify with a real test send **before** persisting to `.env`; restore the previous
  settings on failure.
- Installing SMTP clears any API key and vice versa.
- Recipients come from `users` rows of the store that owns the camera; `ALERT_EMAIL_TO`
  overrides them when set.
- Never alert on `low_confidence` reports.

---

## 10. Git workflow

- Work on the current branch; check `git status` before any consequential operation.
- Stage only the files you changed (`git add <paths>`), never `git add -A` in a shared tree.
- Do not discard, stash, or commit changes you did not make.
- Commit messages explain **why** the change was made, in one or two sentences.
- Do not add AI attribution unless the user asks for it in this repository.

---

## 11. Definition of done

- [ ] Behaviour matches the request, including error and empty states.
- [ ] Tenant scoping verified for every new read/write.
- [ ] Backend suite passes (`python -m pytest`).
- [ ] Frontend typecheck passes (`npx tsc --noEmit`) and tests pass (`npm test`).
- [ ] New/changed tables have a migration; `alembic upgrade head` is idempotent.
- [ ] New/changed endpoints appear in [api.md](api.md) with request/response examples.
- [ ] Schema changes appear in [database.md](database.md).
- [ ] Architecture or env-var changes appear in [architecture.md](architecture.md).
- [ ] No secret, token, or real credential appears in the diff.
- [ ] No unrelated formatting churn.
