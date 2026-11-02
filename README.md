# IndustrialShield AI

IndustrialShield AI extends the existing command centre with a trained synthetic-data ML pipeline, time-series screening forecasts, time-weighted exposure indicators, geospatial plume estimates, durable sensor/alert/audit storage, JWT sessions, and WebSocket event streaming. The current UI and navigation remain intact, with model and forecast panels added to the dashboard.

## Run locally

    python -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    uvicorn app:app --reload

Open http://127.0.0.1:8000. Demo login defaults to admin@industrialshield.demo / ChangeMe-For-Deployment. Set INDUSTRIALSHIELD_DEMO_EMAIL, INDUSTRIALSHIELD_DEMO_PASSWORD, and a long random INDUSTRIALSHIELD_SECRET before sharing the service. Local mode persists to industrialshield.db (SQLite). Simulator data, ML training samples, worker roster, and meteorology are synthetic.

## Run PostgreSQL + Redis

Configure secret values in .env (start from .env.example), then:

    docker compose up --build

Compose runs PostgreSQL 16 for persistent readings, alerts, and audit events; Alembic applies schema migrations before FastAPI starts. Redis 7 is an optional event bus used to fan out WebSocket notifications across app instances. Local mode uses SQLite and in-process WebSocket fanout. Docker is not required for local development.

## Deploy on Vercel

The project keeps `app.py` as the FastAPI entrypoint and stores the unchanged UI under `public/`, which Vercel serves as static CDN assets. `vercel.json` selects Python 3.12, excludes development artifacts from the function bundle, and sets the function lifetime to 300 seconds. Vercel's filesystem is not used for durable state.

1. Create a managed PostgreSQL database and a TLS Redis service (for example, from Vercel Marketplace integrations). Use the PostgreSQL provider's pooled connection URL.
2. Configure these Vercel environment variables for Preview and Production: `DATABASE_URL`, `REDIS_URL`, `INDUSTRIALSHIELD_SECRET`, `INDUSTRIALSHIELD_DEMO_EMAIL`, and `INDUSTRIALSHIELD_DEMO_PASSWORD`. Use a unique random secret of at least 32 characters and a unique password of at least 16 characters. Vercel startup rejects SQLite, missing Redis, and the local default secrets.
3. Apply the schema once before the first deployment, from an environment that has the production `DATABASE_URL`:

       alembic upgrade head

4. Link the repository to a Vercel project or run `vercel link`. Use `vercel dev` with the Vercel environment variables to check the Vercel runtime locally, then run `vercel` for a Preview deployment. Use `vercel --prod` only when you intend a Production deployment.

Vercel serves `/` and `/static/*` from `public/`, while API requests go through the existing FastAPI application. The WebSocket route uses Redis pub/sub so separate function instances can share events; connections are temporary and the dashboard reconnects when a function instance reaches its configured lifetime. The per-request refresh reads persisted readings, alerts, audit events, and simulator settings so a warm function sees writes made by other instances. Continue to treat all seeded sensor and worker records as synthetic demo data.

## Reproduce the hazard demo

1. Sign in and select **Normal operations**, then **Run scenario**.
2. Select **Gas leak simulation · chemical** and run it.
3. Confirm the chemical facility reports a current deterministic **CRITICAL** assessment, Isolation Forest anomaly, model explanation, and an open critical alert.
4. Ask IndustrialShield Agent why the chemical facility is at risk. The response includes underlying timestamps, readings, model output, and recommendation workflow results.
5. Inspect Community intelligence. Wind direction/speed and downwind zones are labelled simulated meteorology and modelled dispersion.
6. Acknowledge the alert and record the intervention. Fetch the incident report with GET /api/alerts/{id}/report or export telemetry at /api/reports/daily.csv.

The simulator writes each scenario sample to persistent reading history. Exposure duration is estimated as five minutes per sample; this assumption and the rolling window are exposed by GET /api/exposures?window_minutes=480.

## ML and forecasting

The intelligence module creates a reproducible synthetic dataset with normal operations and labelled heat, particulate, gas, ventilation/oxygen, and mixed events. Isolation Forest is trained only on a training split of synthetic normal observations. A separate random forest predicts rule-derived severity classes. Both models are evaluated on held-out synthetic records; measured precision/recall, accuracy, macro F1, sample sizes, and the synthetic-label caveat are returned by GET /api/model/metrics and displayed in the UI. The metrics are not field performance claims.

Feature analysis includes rolling mean/min/max, range, delta, and bounded per-hour slope. A short-horizon linear extrapolation with conservative per-feature rate caps checks for crossings of demonstration screening lines. It is labelled predicted, LOW confidence, and uncalibrated. Deterministic threshold rules remain authoritative and independent of ML outputs. ML cannot suppress alerts or change thresholds.

## AgentFoundry

The workflows module defines grounded hazard-analysis, recommendation, and incident-summary workflows with explicit data scope and stages. They retrieve current readings, model results, findings, and persistent audit entries; response actions are deterministic playbook mappings. This runtime has no AgentFoundry connector, invitation, API credentials, or deployment target, so these local workflows are **not** represented as workflows deployed in the external AgentFoundry service. See ARCHITECTURE.md for the status and integration boundary.

## Persistence, security, and limitations

- SQLAlchemy persists sensor readings, safety alerts, and audit events. SQLite is the local default; Compose uses PostgreSQL. Schema migration is versioned in migrations/ and invoked by the container entry point.
- JWT bearer sessions are signed with HMAC-SHA256 and expire after 30 minutes. Demo password verification uses PBKDF2-HMAC-SHA256. Facility scopes are checked on facility data and alert actions.
- WebSocket /ws/stream authenticates with a short-lived bearer token on connection and emits new reading, alert, scenario, and audit events. Redis pub/sub is selected when REDIS_URL is configured.
- Community plume polygons use a simple synthetic wind advection geometry. They are not a validated atmospheric dispersion model and must not be used for public emergency decisions.
- Worker exposure is an estimated time-weighted count of samples over a selectable time window. It is neither a personal dosimeter reading nor a medical assessment.
- Model thresholds are illustrative screening values and require validation against site controls and applicable regulations. This app is advisory and must not replace certified instrumentation, qualified safety staff, or emergency procedures.
- Historical API data is bounded in process memory to the latest 10,000 samples while the database retains readings; restart reconstruction loads up to 10,000 newest rows. No retention/archival policy is configured yet.
- Users, facility metadata, and worker roster remain configured synthetic in-memory records. Simulator mode and meteorology, readings, alert state, and immutable audit events are durable. The in-process history cache loads up to 10,000 recent rows.

## Verification

    python -m py_compile app.py database.py intelligence.py exposure.py community.py workflows.py test_app.py
    node --check public/static/app.js
    python -m unittest -v test_app

Test coverage includes model training/evaluation and normal/anomaly predictions, trend threshold crossing, exposure aggregation, plume geometry, and the complete leak → critical alert → grounded agent → acknowledgement/intervention → incident report workflow. The suite uses a temporary SQLite database so test writes cannot change the local demo database or interfere with a running server. The verification uses service-level integration calls; run the app locally with Uvicorn or Docker Compose for browser/API verification.

## API reference

Interactive OpenAPI docs are at /docs. Main endpoints:

- POST /api/auth/login, GET /api/me, GET /api/overview
- POST /api/ingest, GET /api/sensors, POST /api/simulator/{scenario}
- GET /api/model/metrics, GET /api/workflows, GET /api/alerts
- POST /api/alerts/{id}/acknowledge, /interventions, /resolve
- GET /api/alerts/{id}/report, GET /api/exposures
- GET /api/community, POST /api/assistant, GET /api/reports/daily.csv
- WS /ws/stream
