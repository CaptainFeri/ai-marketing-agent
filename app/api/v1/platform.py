"""What the server is, and what configuration follows from it.

Restricted to platform operators: this reports host hardware, which is no
customer's business and would help anyone probing the deployment.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import Principal, get_principal
from app.core.errors import PermissionDeniedError
from app.core.platform import probe_platform
from app.db.tenancy import system_session
from app.services.tuning import recommend
from app.worker.dispatcher import measured_switch_seconds

router = APIRouter(prefix="/platform", tags=["platform"])


@router.get("/analysis")
def analyse(
    disk: str = Query(default="/", description="Filesystem holding weights and MinIO"),
    benchmark: bool = Query(default=False, description="Time a 256 MB write to gauge the disk"),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Probe the host and return the configuration it implies.

    The switch cost is the measured one once the GPU worker has recorded a few
    switches, and an estimate from the hardware before that — the response
    says which, because the two deserve different amounts of trust.
    """
    if not principal.is_superuser:
        raise PermissionDeniedError("platform analysis is restricted to operators")

    probe = probe_platform(disk, benchmark_mb=256 if benchmark else 0)

    try:
        with system_session() as session:
            measured = measured_switch_seconds(session)
    except Exception:  # noqa: BLE001 - the analysis is still worth returning
        measured = None

    result = recommend(probe, measured_switch_seconds=measured)
    return {
        "probe": probe.as_dict(),
        "llm": result.llm.name if result.llm else None,
        "models": [vars(choice) for choice in result.models],
        "switch_strategy": result.switch_strategy.value,
        "switch_seconds": round(result.switch_seconds, 1),
        "switch_basis": result.switch_basis,
        "settings": result.settings,
        "findings": [vars(finding) for finding in result.findings],
        "is_viable": result.is_viable,
        "env_fragment": result.env_fragment(),
    }
