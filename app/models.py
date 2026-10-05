"""Request/response schemas (pydantic v2).

Field *types* are enforced here; domain constraints (positive volumes,
concentration ranges, known reagents, duplicate steps, stale references...)
are validated item-by-item in :mod:`app.engine` so every offending item is
reported with its step number, field and a stable machine-readable code.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


class ContainerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material: str
    volume_ml: float
    temperature_c: float = 25.0


class InitialLiquid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reagent: str
    volume_ml: float
    concentration: float  # mass fraction of active component, (0, 1]


class AllowedContact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    max_seconds: float


class Step(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int
    action: Literal["add", "dilute", "pour_out", "wash", "transfer"]
    # add
    reagent: Optional[str] = None
    concentration: Optional[float] = None
    # add / dilute / pour_out / transfer
    volume_ml: Optional[float] = None
    # dilute (defaults to water)
    solvent: Optional[str] = None
    # transfer
    from_label: Optional[str] = None
    # wash
    cycles: int = 1
    # any step: contact-time contribution and optional state snapshot label
    duration_s: float = 0.0
    label: Optional[str] = None


class PrecheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    container: ContainerSpec
    initial: list[InitialLiquid] = []
    allowed_contact: list[AllowedContact] = []
    steps: list[Step]
