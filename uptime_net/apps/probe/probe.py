from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from shared.canonical_json import canonical_dumps, strip_keys_deep
from shared.ed25519 import generate_keypair, sign_bytes, verify_bytes

STATE_PATH = Path(os.environ.get("PROBE_STATE", str(Path.home() / ".uptime_probe_state.json")))


def iso_z(dt: datetime) -> str:
    return dt.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def headers_hash(headers: httpx.Headers) -> str:
    items = []
    for k, v in headers.items():
        items.append((k.lower().strip(), " ".join(v.split())))
    items.sort()
    blob = "\n".join([f"{k}:{v}" for k, v in items]).encode("utf-8")
    return sha256_hex(blob)


@dataclass
class State:
    api_base: str
    node_id: str
    token: str
    sk_b64: str
    pk_b64: str


def load_state() -> Optional[State]:
    if not STATE_PATH.exists():
        return None
    data = json.loads(STATE_PATH.read_text("utf-8"))
    return State(**data)


def save_state(st: State) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(st.__dict__, indent=2), "utf-8")


def register(api_base: str) -> State:
    kp = generate_keypair()
    payload = {
        "node_pubkey": kp.pk_b64,
        "node_version": "0.1.0",
        "platform": os.name,
        "capabilities": {"http": True, "tls": True, "dns": False},
    }
    r = httpx.post(f"{api_base}/v1/node/register", json=payload, timeout=10)
    r.raise_for_status()
    resp = r.json()
    st = State(api_base=api_base, node_id=resp["node_id"], token=resp["token"], sk_b64=kp.sk_b64, pk_b64=kp.pk_b64)
    save_state(st)
    print(f"Registered node_id={st.node_id} state={STATE_PATH}")
    return st


def verify_server_sig(job: Dict[str, Any]) -> bool:
    pk = os.environ.get("SERVER_ED25519_PK_B64")
    if not pk:
        # MVP dev convenience
        return True
    unsigned = {k: job[k] for k in job.keys() if k != "server_sig"}
    msg = canonical_dumps(unsigned)
    return verify_bytes(pk, msg, job["server_sig"])


def run_http_check(url: str, method: str, timeout_ms: int) -> Dict[str, Any]:
    started = datetime.utcnow()
    t0 = time.perf_counter()
    ok = False
    reason = "OK"
    status = None
    hdr_hash = None

    try:
        with httpx.Client(follow_redirects=False, timeout=timeout_ms / 1000.0) as client:
            resp = client.request(method=method, url=url)
            status = int(resp.status_code)
            hdr_hash = headers_hash(resp.headers)
            if status is not None and 200 <= status < 400:
                ok = True
            else:
                ok = False
                reason = "HTTP_BAD_STATUS"
    except httpx.TimeoutException:
        reason = "TIMEOUT"
    except Exception:
        reason = "CONNECTION_FAIL"

    t1 = time.perf_counter()
    finished = datetime.utcnow()
    total_ms = int((t1 - t0) * 1000)

    return {
        "started_at": started,
        "finished_at": finished,
        "result": {
            "ok": ok,
            "reason_code": reason,
            "http_status": status,
            "ip": None,
        },
        "timings_ms": {
            "dns": 0,
            "tcp": 0,
            "tls": 0,
            "ttfb": 0,
            "total": total_ms,
        },
        "fingerprints": {
            "headers_hash": hdr_hash or "",
            "body_hash_first4kb": None,
            "tls_cert_sha256": None,
        },
    }


def loop(st: State):
    headers = {"Authorization": f"Bearer {st.token}"}

    sleep_s = 2

    while True:
        r = httpx.get(f"{st.api_base}/v1/node/jobs", params={"limit": 1}, headers=headers, timeout=10)
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
        if not jobs:
            # Backoff when no jobs are available, up to 30s.
            print(f"No jobs available, sleeping {sleep_s}s")
            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 2, 30)
            continue

        # Reset backoff when we successfully get a job.
        sleep_s = 2

        job = jobs[0]
        if not verify_server_sig(job):
            print("Server signature invalid; skipping job", job.get("job_id"))
            continue

        params = job.get("params", {})
        url = params.get("url")
        method = params.get("method", "HEAD")
        timeout_ms = int(params.get("timeout_ms", 8000))

        check = run_http_check(url, method, timeout_ms)

        receipt = {
            "job_id": job["job_id"],
            "node_id": st.node_id,
            "nonce": job["nonce"],
            "started_at": iso_z(check["started_at"]),
            "finished_at": iso_z(check["finished_at"]),
            "result": check["result"],
            "timings_ms": check["timings_ms"],
            "fingerprints": check["fingerprints"],
        }
        msg = canonical_dumps(receipt)
        receipt["node_sig"] = sign_bytes(st.sk_b64, msg)

        rr = httpx.post(f"{st.api_base}/v1/node/receipts", json=receipt, headers=headers, timeout=10)
        if rr.status_code >= 400:
            try:
                err = rr.json()
            except Exception:
                err = rr.text
            print("Receipt rejected:", rr.status_code, err)
        else:
            print("Receipt accepted for job", job["job_id"], "ok=", receipt["result"]["ok"], "total_ms=", receipt["timings_ms"]["total"])

        time.sleep(0.2)


def main():
    api_base = os.environ.get("API_BASE", "http://localhost:8000")
    st = load_state()
    if not st or st.api_base != api_base:
        st = register(api_base)
    loop(st)


if __name__ == "__main__":
    main()
