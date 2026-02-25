# uptime_net (Phase 0 alpha skeleton)

This repo is the walkable skeleton for the crowdsourced uptime/latency monitoring network.

## Quickstart (local)

### 1) Start Postgres
```bash
cd docker
docker compose up -d
```

### 2) API: install + migrate + run
```bash
cd apps/api
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/uptime_net'
alembic -c alembic.ini upgrade head

uvicorn app.main:app --reload --port 8000
```

Health:
```bash
curl http://localhost:8000/healthz
```

### 3) Seed jobs (creates demo target if none)
```bash
cd apps/worker
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/uptime_net'
python seed_jobs.py
```

### 4) Run a probe node
```bash
cd apps/probe
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export API_BASE='http://localhost:8000'
python probe.py
```

> Note: server_sig verification is skipped unless you set `SERVER_ED25519_PK_B64` in the probe environment.
> In production, you would distribute the server public key and require verification.
