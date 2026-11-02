# IndustrialShield AI architecture

## Runtime

The existing FastAPI + browser UI remains the application shell. Backend modules separate the intelligence concerns:

- app.py composes authenticated APIs, simulator ingestion, deterministic rule evaluation, persistent state, and WebSocket transport.
- intelligence.py fits a synthetic-only Isolation Forest and random forest at process start, computes explanations against a generated normal baseline, derives rolling time-series features, and produces a low-confidence trend extrapolation.
- database.py maps telemetry, alert state, and audit events through SQLAlchemy. Local development defaults to SQLite; the container uses PostgreSQL.
- exposure.py calculates a rolling worker/department exposure sample estimate using a configurable time window.
- community.py builds approximate downwind polygon coordinates from configured facility coordinates and synthetic wind parameters.
- workflows.py exposes grounded hazard-analysis, recommendation, and incident-summary workflow definitions. The hazard flow consumes the rule and ML outputs; recommendations map rule findings to fixed safety guidance; incident summaries query alert and audit state.

## Vercel deployment

Vercel detects the root `app.py:app` FastAPI entrypoint and runs it on the Python 3.12 Functions runtime. Browser assets live in `public/` for CDN delivery; the FastAPI static mount remains for local Uvicorn and Docker use. `vercel.json` excludes tests, migrations, output artifacts, local SQLite data, and Python caches from the function bundle. Deployment requires externally managed PostgreSQL and Redis, an applied Alembic schema, a unique HMAC secret, and non-default demo login credentials. SQLite and local fallback secrets are rejected in Vercel.

Function processes are ephemeral and can scale independently. HTTP requests refresh readings, alerts, audit events, and simulator state from PostgreSQL before route handling; SQLAlchemy uses `NullPool` with a provider-pooled database URL. Redis pub/sub carries WebSocket events across function instances. WebSocket connections are temporary, and clients reconnect. This is suitable for a Vercel-hosted demonstration; high-contention writes and larger production history still need transaction-oriented repository queries and pagination.

## Hazard decision boundaries

Sensor ingestion stores the source record, calculates deterministic threshold findings, computes ML output independently, builds temporal features and a 30-minute trend projection, and creates a durable alert when the deterministic rule level is HIGH or CRITICAL. ML output is appended as evidence and never changes that rule decision. Critical alerts remain persisted after acknowledgement. Every lifecycle change adds a separate audit row.

Current readings are presented as observations. Forecast crossings are separately labelled PREDICTED and include the configured screening line, current value, projected value, slope, and calibration limitation. The UI and CSV expose model outputs and the evaluated synthetic dataset note.

## Workflow catalog

| Workflow | Inputs | Operation | Output |
|---|---|---|---|
| Hazard analysis | Authorized facility readings, rule assessment, Isolation Forest, classifier, temporal features | Join measured values and timestamped model evidence | Structured facts, anomaly score, classifier label/confidence caveat |
| Safety recommendations | Deterministic rule findings and severity | Map each hazard type to a fixed advisory playbook | Supervisor actions; cannot alter the rule engine |
| Incident summary | Persistent alert payload and durable audit events | Order creation/acknowledgement/intervention/resolution evidence | Timeline report containing the assessment and synthetic-data flag |

These workflow implementations execute inside this application. The public AgentFoundry site describes a closed beta/invitation-only service and this runtime has no AgentFoundry app connector, account invitation, API endpoint, or credential. No remote AgentFoundry project or workflow deployment is claimed. The external integration remains a dependency requiring authorized access and the target service's deployment API. The local workflows are independently testable and available at GET /api/workflows.

## Persistence and event delivery

PostgreSQL tables currently cover sensor readings, safety alerts, and audit events with facility/time and lifecycle indexes. Migration 0001 creates the initial schema. SQLite is used for quick local runs. Alembic is invoked by the Docker entrypoint; the API bootstraps missing tables only in local development. Vercel deployments must run migrations against the managed database before the first request.

WebSocket clients send a short-lived signed bearer token in the first connection message, avoiding putting credentials in URL logs. In local mode, events fan out through in-process queues. When REDIS_URL is set, events publish and subscribe on industrialshield.events so WebSocket clients can receive events across app instances.

## Synthetic-data boundaries

- The initial site measurements and all selectable scenarios are synthetic.
- The train/test dataset is generated locally with seeded distributions and synthetic hazard perturbations. The Isolation Forest is fitted only to a normal-data training partition. The random forest labels come from demonstration rules.
- Model metrics are computed from held-out synthetic samples at startup; they are not field-validated accuracy claims and cannot establish safety effectiveness.
- Worker identities, departments, zones, and sensor associations are synthetic. Exposure time is estimated from the simulator's five-minute sample assumption.
- Wind direction and speed are fixed synthetic scenario inputs. Community plume geometry uses a simplified advection estimate, not a regulatory dispersion solver or observed pollution map.

## Security and operational limits

HMAC-signed, 30-minute bearer JWTs and PBKDF2-HMAC password verification protect the demo login. Claims contain role and facility scopes; facility routes and alert operations apply those scopes. The seeded administrator owns all three facilities. There is no user-administration UI, password reset, token revocation, refresh flow, rate limiter, or TLS setup in this MVP.

Sensor and audit data, plus the selected simulator mode and synthetic meteorology, are persistent. Access tokens are ephemeral; worker and facility configuration remain in memory. Startup reconstructs up to 10,000 recent readings and 2,000 alerts / 5,000 audit events into memory. The database retains historical reading rows without a configured retention policy. High-volume production needs paging, retention, partitioning/hypertables, and transaction-backed services rather than full in-memory history reads.

All thresholds are demonstrations and require validation for the facility, work task, and applicable standard. Neither the synthetic ML metrics, linear forecasting, nor worker exposure estimates are medical or certified safety judgments. Community model output does not establish source attribution.
