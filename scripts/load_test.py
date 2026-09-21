#!/usr/bin/env python3
"""Load test: N tenants driving packages through the pipeline concurrently.

Handoff section 7, phase 1 week 9-10: "آزمون بار: ۳ مشتری هم‌زمان" (load
test: 3 concurrent tenants). Real, against a live stack — HTTP calls against
a running API, real Postgres, real Celery task dispatch — not a synthetic
in-process simulation.

Also exercises the handoff's isolation acceptance criterion under actual
concurrent load ("داده هر مشتری برای مشتری دیگر قابل مشاهده نباشد"):
``tests/test_tenant_isolation.py`` proves the RLS policies are correct in
isolation; this proves one tenant's live token still can't reach another's
data while both are actively creating packages at the same time.

Prerequisites — a live stack, matching docker-compose.yml's services:

    # 1. Postgres, migrated (scripts/dev_postgres.sh start && make migrate,
    #    or docker compose up -d postgres && alembic upgrade head)
    # 2. Redis
    redis-server &

    # 3. The API
    GPU_RUNTIME=simulated LLM_CLIENT=simulated STORAGE_BACKEND=memory \\
      .venv/bin/uvicorn app.main:app --port 8000 &

    # 4. One combined worker across every queue, and beat — gpu-dispatch
    #    (every 15s) is what actually advances a package after each step.
    GPU_RUNTIME=simulated LLM_CLIENT=simulated STORAGE_BACKEND=memory \\
      .venv/bin/celery -A app.worker.celery_app worker \\
      -Q gpu,pipeline,publish,media_cpu,maintenance -c 1 --loglevel INFO &
    GPU_RUNTIME=simulated LLM_CLIENT=simulated STORAGE_BACKEND=memory \\
      .venv/bin/celery -A app.worker.celery_app beat --loglevel INFO &

Then:

    .venv/bin/python scripts/load_test.py --tenants 3 --packages-per-tenant 3

Tenant provisioning itself (``create_tenant_with_owner``) is a direct call
into the same service function the operator-only ``POST /tenants`` endpoint
uses — there is no self-service signup yet (phase 2) to drive over HTTP, and
provisioning is a one-time setup step, not the load being tested. Everything
from login onward — the part actually under concurrent load — is real HTTP.

``pipeline.start_text`` is queued directly (a real Celery ``.delay()`` call,
the same as any caller would make) because nothing in the app currently
calls it automatically for a freshly created package; every step after that
runs on its own once queued, via the live worker and beat.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
import uuid
from dataclasses import dataclass, field

os.environ.setdefault("ENVIRONMENT", "local")

import httpx  # noqa: E402


@dataclass
class PackageResult:
    tenant_slug: str
    package_id: str
    ok: bool
    seconds: float
    final_status: str
    error: str | None = None


@dataclass
class TenantResult:
    slug: str
    workspace_id: str | None = None
    packages: list[PackageResult] = field(default_factory=list)
    error: str | None = None


def _provision_tenants(count: int) -> list[dict]:
    """Direct service call, not HTTP — see the module docstring."""
    from app.schemas.tenant import TenantCreate
    from app.services.auth import create_tenant_with_owner

    tenants = []
    for _ in range(count):
        suffix = uuid.uuid4().hex[:8]
        payload = TenantCreate(
            slug=f"loadtest-{suffix}",
            name=f"Load Test {suffix}",
            owner_email=f"owner-{suffix}@loadtest.example.com",
            owner_password="loadtest-password-123",
        )
        tenant = create_tenant_with_owner(payload)
        tenants.append(
            {
                "slug": payload.slug,
                "email": payload.owner_email,
                "password": payload.owner_password,
                "tenant_id": str(tenant.id),
            }
        )
    return tenants


async def _login(client: httpx.AsyncClient, email: str, password: str) -> dict:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    response.raise_for_status()
    return response.json()


async def _wait_for_status(
    client: httpx.AsyncClient,
    headers: dict,
    package_id: str,
    targets: set[str],
    *,
    timeout: float,
    poll_interval: float = 1.0,
) -> str:
    deadline = time.monotonic() + timeout
    status = "unknown"
    while time.monotonic() < deadline:
        response = await client.get(f"/packages/{package_id}", headers=headers)
        response.raise_for_status()
        status = response.json()["status"]
        if status in targets or status in {"rejected"}:
            return status
        await asyncio.sleep(poll_interval)
    return status


async def _drive_package(
    client: httpx.AsyncClient,
    headers: dict,
    tenant_id: str,
    workspace_id: str,
    locale: str,
    title: str,
    *,
    timeout: float,
) -> PackageResult:
    from app.worker.tasks.pipeline import start_text

    started = time.monotonic()
    try:
        response = await client.post(
            "/packages",
            headers=headers,
            json={
                "workspace_id": workspace_id,
                "title": title,
                "locale": locale,
                "video_mode": "none",
            },
        )
        response.raise_for_status()
        package_id = response.json()["id"]

        start_text.delay(tenant_id, package_id)

        status = await _wait_for_status(
            client, headers, package_id, {"text_review"}, timeout=timeout
        )
        if status != "text_review":
            return PackageResult(
                tenant_slug=title,
                package_id=package_id,
                ok=False,
                seconds=time.monotonic() - started,
                final_status=status,
                error="did not reach gate 1 in time",
            )

        approve = await client.post(
            f"/packages/{package_id}/gates/text",
            headers=headers,
            json={"decision": "approved"},
        )
        approve.raise_for_status()

        status = await _wait_for_status(
            client, headers, package_id, {"selection", "media_generating"}, timeout=timeout
        )
        ok = status in {"selection", "media_generating"}
        return PackageResult(
            tenant_slug=title,
            package_id=package_id,
            ok=ok,
            seconds=time.monotonic() - started,
            final_status=status,
            error=None if ok else "did not clear gate 1 approval in time",
        )
    except httpx.HTTPStatusError as exc:
        return PackageResult(
            tenant_slug=title,
            package_id="",
            ok=False,
            seconds=time.monotonic() - started,
            final_status="error",
            error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
        )


async def _drive_tenant(
    base_url: str, tenant: dict, packages_per_tenant: int, timeout: float
) -> TenantResult:
    result = TenantResult(slug=tenant["slug"])
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        try:
            pair = await _login(client, tenant["email"], tenant["password"])
            headers = {"Authorization": f"Bearer {pair['access_token']}"}

            workspaces = await client.get("/workspaces", headers=headers)
            workspaces.raise_for_status()
            workspace = workspaces.json()[0]
            result.workspace_id = workspace["id"]
            locale = workspace["default_locale"]

            brief = await client.post(
                f"/workspaces/{workspace['id']}/briefs",
                headers=headers,
                json={"data": {"brand": tenant["slug"]}, "activate": True},
            )
            brief.raise_for_status()

            tasks = [
                _drive_package(
                    client,
                    headers,
                    tenant["tenant_id"],
                    workspace["id"],
                    locale,
                    f"{tenant['slug']} package {i + 1}",
                    timeout=timeout,
                )
                for i in range(packages_per_tenant)
            ]
            result.packages = await asyncio.gather(*tasks)
        except httpx.HTTPStatusError as exc:
            result.error = f"HTTP {exc.response.status_code}: {exc.response.text[:200]}"
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            result.error = f"{type(exc).__name__}: {exc}"
    return result


async def _check_isolation(base_url: str, tenants: list[dict], results: list[TenantResult]) -> bool:
    """Tenant A's token must never reach tenant B's workspace or packages."""
    if len(results) < 2:
        return True
    a, b = results[0], results[1]
    if a.workspace_id is None or not b.packages or not b.packages[0].package_id:
        return True  # nothing to check if either tenant never got that far

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        pair = await _login(client, tenants[0]["email"], tenants[0]["password"])
        headers = {"Authorization": f"Bearer {pair['access_token']}"}

        workspace_leak = await client.get(f"/workspaces/{b.workspace_id}", headers=headers)
        package_leak = await client.get(f"/packages/{b.packages[0].package_id}", headers=headers)
    return workspace_leak.status_code == 404 and package_leak.status_code == 404


def _report(
    tenants: list[dict], results: list[TenantResult], isolated: bool, wall_seconds: float
) -> bool:
    all_packages = [p for r in results if not r.error for p in r.packages]
    ok_packages = [p for p in all_packages if p.ok]
    timings = [p.seconds for p in ok_packages]

    print()
    print("=" * 70)
    print(f"{len(tenants)} tenants, {len(all_packages)} packages, {wall_seconds:.1f}s wall clock")
    print("=" * 70)
    for result in results:
        if result.error:
            print(f"  {result.slug}: FAILED to drive at all — {result.error}")
            continue
        failed = [p for p in result.packages if not p.ok]
        succeeded = len(result.packages) - len(failed)
        total = len(result.packages)
        print(f"  {result.slug}: {succeeded}/{total} packages reached gate 1 + approval")
        for p in failed:
            print(f"    - {p.package_id or '(none)'}: {p.final_status} — {p.error}")

    print()
    print(f"Succeeded: {len(ok_packages)}/{len(all_packages)}")
    if timings:
        lo, mid, hi = min(timings), statistics.mean(timings), max(timings)
        print(f"Per-package time: min={lo:.1f}s mean={mid:.1f}s max={hi:.1f}s")
        if len(timings) >= 2:
            print(f"p95={statistics.quantiles(timings, n=20)[18]:.1f}s")
        print(f"Throughput: {len(ok_packages) / wall_seconds:.2f} packages/sec")
    print(f"Tenant isolation held: {isolated}")
    print("=" * 70)

    return len(ok_packages) == len(all_packages) and bool(all_packages) and isolated


async def _main(args: argparse.Namespace) -> int:
    print(f"Provisioning {args.tenants} tenants...")
    tenants = _provision_tenants(args.tenants)
    for tenant in tenants:
        print(f"  {tenant['slug']} ({tenant['tenant_id']})")

    print(
        f"Driving {args.tenants} tenants x {args.packages_per_tenant} packages "
        f"concurrently against {args.base_url}..."
    )
    started = time.monotonic()
    results = await asyncio.gather(
        *[
            _drive_tenant(args.base_url, tenant, args.packages_per_tenant, args.timeout)
            for tenant in tenants
        ]
    )
    wall_seconds = time.monotonic() - started

    print("Checking tenant isolation...")
    isolated = await _check_isolation(args.base_url, tenants, results)

    passed = _report(tenants, results, isolated, wall_seconds)
    return 0 if passed else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/api/v1")
    parser.add_argument("--tenants", type=int, default=3)
    parser.add_argument("--packages-per-tenant", type=int, default=3)
    parser.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        help=(
            "seconds to wait for each pipeline stage (gate 1, then approval). "
            "gpu.dispatch batches up to 8 jobs every 15s, so scale this up for "
            "more than a handful of tenants/packages: roughly "
            "(tenants * packages_per_tenant * 6 text steps / 8) * 15s"
        ),
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_main(args)))


if __name__ == "__main__":
    main()
