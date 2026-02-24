from __future__ import annotations

import hashlib
import json
import os
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import certifi
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
    ssl_ctx, _, _ = get_ssl_context()
    r = httpx.post(
        f"{api_base}/v1/node/register",
        json=payload,
        timeout=10,
        verify=ssl_ctx,
    )
    r.raise_for_status()
    resp = r.json()
    st = State(api_base=api_base, node_id=resp["node_id"], token=resp["token"], sk_b64=kp.sk_b64, pk_b64=kp.pk_b64)
    save_state(st)
    print(f"Registered node_id={st.node_id} state={STATE_PATH}")
    return st


def _default_tls_verify_mode() -> str:
    """Default: truststore on Windows (OS store), certifi elsewhere."""
    return "truststore" if os.name == "nt" else "certifi"


def get_ssl_context() -> Tuple[ssl.SSLContext, str, str]:
    """
    Build an explicit SSLContext for deterministic verification on Windows.
    Returns (context, verify_mode_name, ca_file_or_empty).
    Modes: truststore (OS store), certifi, custom (TLS_CA_FILE), system.
    """
    mode = (os.environ.get("TLS_VERIFY_MODE") or _default_tls_verify_mode()).strip().lower()
    ca_file = os.environ.get("TLS_CA_FILE", "").strip()

    if mode == "custom" and ca_file:
        ctx = ssl.create_default_context(cafile=ca_file)
        return ctx, "custom", ca_file
    if mode == "system":
        ctx = ssl.create_default_context()
        return ctx, "system", ""
    if mode == "truststore":
        import truststore
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        return ctx, "truststore", ""
    # certifi
    ca_path = certifi.where()
    ctx = ssl.create_default_context(cafile=ca_path)
    return ctx, "certifi", ca_path


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
    tls_error_detail: Optional[str] = None
    tls_verify_mode: Optional[str] = None
    tls_ca_file: Optional[str] = None

    ssl_ctx, verify_mode_name, ca_file = get_ssl_context()
    timeout_sec = timeout_ms / 1000.0

    try:
        with httpx.Client(
            verify=ssl_ctx,
            follow_redirects=False,
            timeout=timeout_sec,
        ) as client:
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
    except (ssl.SSLError, httpx.ConnectError, OSError) as exc:
        reason = "TLS_VERIFY_FAILED"
        tls_error_detail = str(exc)
        tls_verify_mode = verify_mode_name
        tls_ca_file = ca_file or None
    except Exception as exc:
        if "ssl" in type(exc).__name__.lower() or "certificate" in str(exc).lower():
            reason = "TLS_VERIFY_FAILED"
            tls_error_detail = str(exc)
            tls_verify_mode = verify_mode_name
            tls_ca_file = ca_file or None
        else:
            reason = "CONNECTION_FAIL"

    t1 = time.perf_counter()
    finished = datetime.utcnow()
    total_ms = int((t1 - t0) * 1000)

    result: Dict[str, Any] = {
        "ok": ok,
        "reason_code": reason,
        "http_status": status,
        "ip": None,
    }
    if tls_error_detail is not None:
        result["tls_error_detail"] = tls_error_detail
    if tls_verify_mode is not None:
        result["tls_verify_mode"] = tls_verify_mode
    if tls_ca_file is not None:
        result["tls_ca_file"] = tls_ca_file

    return {
        "started_at": started,
        "finished_at": finished,
        "result": result,
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

    ssl_ctx, _, _ = get_ssl_context()
    while True:
        r = httpx.get(
            f"{st.api_base}/v1/node/jobs",
            params={"limit": 1},
            headers=headers,
            timeout=10,
            verify=ssl_ctx,
        )
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

        rr = httpx.post(
            f"{st.api_base}/v1/node/receipts",
            json=receipt,
            headers=headers,
            timeout=10,
            verify=ssl_ctx,
        )
        if rr.status_code >= 400:
            try:
                err = rr.json()
            except Exception:
                err = rr.text
            print("Receipt rejected:", rr.status_code, err)
        else:
            res = receipt["result"]
            timings = receipt["timings_ms"]
            parts = [
                "Receipt accepted:",
                "job_id=", job["job_id"],
                "ok=", res.get("ok"),
                "reason_code=", res.get("reason_code"),
                "http_status=", res.get("http_status"),
                "total_ms=", timings.get("total"),
            ]
            if res.get("reason_code") == "TLS_VERIFY_FAILED":
                parts.extend([
                    "tls_verify_mode=", res.get("tls_verify_mode"),
                    "tls_ca_file=", res.get("tls_ca_file"),
                    "tls_error_detail=", res.get("tls_error_detail"),
                ])
            print(*parts)

        time.sleep(0.2)


def main():
    api_base = os.environ.get("API_BASE", "http://localhost:8000")
    st = load_state()
    if not st or st.api_base != api_base:
        st = register(api_base)
    loop(st)


if __name__ == "__main__":
    main()
