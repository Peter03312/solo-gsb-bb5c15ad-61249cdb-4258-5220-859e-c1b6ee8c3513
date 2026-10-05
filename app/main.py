"""HTTP API entrypoint."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .catalog import MATERIALS, REAGENTS
from .engine import build_response, simulate, static_validate
from .models import PrecheckRequest
from .rules import MATERIAL_RULES, PAIR_RULES

app = FastAPI(
    title="ChemMix Precheck API",
    version=__version__,
    description="化学试剂混配风险预检：逐步模拟加液/稀释/倒出/清洗，"
                "识别禁配组合、超时接触与材质不相容。",
)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(_: Request, exc: RequestValidationError):
    """Reformat schema errors into the same itemized shape as domain errors."""
    detail = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e["loc"] if x != "body") or "body"
        detail.append({"step": None, "field": loc,
                       "code": "SCHEMA_VALIDATION", "message": e["msg"]})
    return JSONResponse(status_code=422, content={"detail": detail})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return {"service": "chem-mix-precheck", "version": __version__,
            "endpoints": ["/v1/precheck", "/v1/catalog", "/health", "/docs"]}


@app.get("/v1/catalog")
def catalog():
    """Expose the reagent/material/rule catalog used by the engine."""
    return {
        "reagents": [
            {"id": r.rid, "name": r.name, "molar_mass": r.molar_mass,
             "density": r.density, "max_fraction": r.max_fraction,
             "categories": sorted(r.categories)}
            for r in REAGENTS.values()
        ],
        "materials": [
            {"id": m.mid, "name": m.name, "max_temp_c": m.max_temp_c}
            for m in MATERIALS.values()
        ],
        "pair_rules": [
            {"rule_id": r.rule_id, "severity": r.severity, "message": r.message,
             "min_conc_a": r.min_conc_a, "min_conc_b": r.min_conc_b,
             "temp_min_c": r.temp_min_c, "contact_time_declarable": True}
            for r in PAIR_RULES
        ],
        "material_rules": [
            {"rule_id": r.rule_id, "severity": r.severity, "message": r.message,
             "materials": sorted(r.materials), "min_conc": r.min_conc}
            for r in MATERIAL_RULES
        ],
    }


@app.post("/v1/precheck")
def precheck(req: PrecheckRequest):
    errors = static_validate(req)
    if errors:
        raise HTTPException(status_code=422, detail=errors)
    runtime_errors, findings, trace = simulate(req)
    if runtime_errors:
        raise HTTPException(status_code=422, detail=runtime_errors)
    return build_response(findings, trace)
