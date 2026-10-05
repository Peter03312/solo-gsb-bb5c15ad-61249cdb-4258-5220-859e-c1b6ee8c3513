"""End-to-end API tests for the ChemMix Precheck service."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def base_request(**overrides):
    req = {
        "container": {"material": "ptfe", "volume_ml": 2000, "temperature_c": 25},
        "initial": [{"reagent": "water", "volume_ml": 500, "concentration": 1.0}],
        "steps": [],
    }
    req.update(overrides)
    return req


def post(req):
    return client.post("/v1/precheck", json=req)


# --------------------------------------------------------------------------- #
# basics
# --------------------------------------------------------------------------- #
def test_health():
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_catalog_lists_reagents_materials_rules():
    r = client.get("/v1/catalog")
    assert r.status_code == 200
    body = r.json()
    ids = {x["id"] for x in body["reagents"]}
    assert {"water", "hcl", "sodium_hypochlorite"} <= ids
    assert "ptfe" in {m["id"] for m in body["materials"]}
    assert "acid_hypochlorite" in {p["rule_id"] for p in body["pair_rules"]}


def test_ok_scenario_no_findings():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "ethanol",
         "volume_ml": 100, "concentration": 0.95},
        {"step": 2, "action": "dilute", "volume_ml": 200},
    ])
    r = post(req)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["findings"] == []
    assert body["first_trigger_step"] is None
    assert body["final_state"]["total_volume_ml"] == pytest.approx(800)


# --------------------------------------------------------------------------- #
# incompatible pairs
# --------------------------------------------------------------------------- #
def test_acid_then_bleach_blocked_with_concentrations():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 50, "concentration": 0.37},
        {"step": 2, "action": "add", "reagent": "sodium_hypochlorite",
         "volume_ml": 20, "concentration": 0.12},
    ])
    r = post(req)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert body["first_trigger_step"] == 2
    f = body["findings"][0]
    assert f["step"] == 2
    assert f["category"] == "incompatible_pair"
    assert f["rule_id"] == "acid_hypochlorite"
    assert f["blocking"] is True
    involved = {c["reagent"]: c["concentration_mol_l"] for c in f["components"]}
    assert set(involved) == {"hcl", "sodium_hypochlorite"}
    assert involved["hcl"] == pytest.approx(1.05, abs=0.05)
    assert involved["sodium_hypochlorite"] == pytest.approx(0.068, abs=0.01)


def test_initial_mixture_checked_at_step_zero():
    req = base_request(initial=[
        {"reagent": "hcl", "volume_ml": 100, "concentration": 0.37},
        {"reagent": "sodium_cyanide", "volume_ml": 100, "concentration": 0.2},
    ])
    body = post(req).json()
    assert body["status"] == "blocked"
    assert body["first_trigger_step"] == 0
    assert body["findings"][0]["rule_id"] == "acid_cyanide"


def test_dilution_below_threshold_avoids_finding():
    # tiny amounts of strong acid + strong base stay below the 0.5 mol/L
    # neutralization threshold -> no finding
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 1, "concentration": 0.1},
        {"step": 2, "action": "add", "reagent": "sodium_hydroxide",
         "volume_ml": 1, "concentration": 0.1},
    ])
    body = post(req).json()
    assert body["status"] == "ok"
    assert body["findings"] == []


def test_pour_out_removes_components():
    req = base_request(initial=[{"reagent": "water", "volume_ml": 100,
                                 "concentration": 1.0}], steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 50, "concentration": 0.37},
        {"step": 2, "action": "pour_out", "volume_ml": 150},
        {"step": 3, "action": "add", "reagent": "sodium_hypochlorite",
         "volume_ml": 10, "concentration": 0.12},
    ])
    body = post(req).json()
    # acid fully poured out before bleach arrives -> no pair finding
    assert body["status"] == "ok"
    assert body["final_state"]["total_volume_ml"] == pytest.approx(10)


def test_wash_resets_state_and_allows_remix():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 50, "concentration": 0.37},
        {"step": 2, "action": "wash", "cycles": 2},
        {"step": 3, "action": "add", "reagent": "sodium_hypochlorite",
         "volume_ml": 10, "concentration": 0.12},
    ])
    body = post(req).json()
    assert body["status"] == "ok"
    washed = [t for t in body["trace"] if t["step"] == 2][0]
    assert washed["total_volume_ml"] == 0
    assert washed["components"] == {}


# --------------------------------------------------------------------------- #
# labels / stale state after wash
# --------------------------------------------------------------------------- #
def test_transfer_from_label_ok():
    req = base_request(initial=[{"reagent": "water", "volume_ml": 100,
                                 "concentration": 1.0}], steps=[
        {"step": 1, "action": "add", "reagent": "ethanol",
         "volume_ml": 50, "concentration": 1.0, "label": "mixA"},
        {"step": 2, "action": "pour_out", "volume_ml": 50},
        {"step": 3, "action": "transfer", "from_label": "mixA", "volume_ml": 20},
    ])
    body = post(req).json()
    assert body["status"] == "ok"
    assert body["final_state"]["total_volume_ml"] == pytest.approx(120)


def test_stale_state_reference_after_wash_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 50, "concentration": 0.37, "label": "mixA"},
        {"step": 2, "action": "wash"},
        {"step": 3, "action": "transfer", "from_label": "mixA", "volume_ml": 10},
    ])
    r = post(req)
    assert r.status_code == 422
    errors = r.json()["detail"]
    assert len(errors) == 1
    assert errors[0]["step"] == 3
    assert errors[0]["code"] == "STALE_STATE_REFERENCE"


def test_unknown_label_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "transfer", "from_label": "ghost", "volume_ml": 10},
    ])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "UNKNOWN_LABEL"


# --------------------------------------------------------------------------- #
# itemized rejection
# --------------------------------------------------------------------------- #
def test_duplicate_step_numbers_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 10, "concentration": 0.37},
        {"step": 1, "action": "add", "reagent": "ethanol",
         "volume_ml": 10, "concentration": 0.95},
    ])
    r = post(req)
    assert r.status_code == 422
    codes = [e["code"] for e in r.json()["detail"]]
    assert "DUPLICATE_STEP" in codes


def test_non_positive_volume_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": -5, "concentration": 0.37},
    ])
    r = post(req)
    assert r.status_code == 422
    e = r.json()["detail"][0]
    assert e["code"] == "NON_POSITIVE_VOLUME" and e["step"] == 1


@pytest.mark.parametrize("conc", [0, -0.2, 1.5])
def test_concentration_out_of_range_rejected(conc):
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "ethanol",
         "volume_ml": 10, "concentration": conc},
    ])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CONCENTRATION_OUT_OF_RANGE"


def test_concentration_above_catalog_max_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 10, "concentration": 0.5},  # hcl max 0.38
    ])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CONCENTRATION_OUT_OF_RANGE"


def test_unknown_reagent_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "unobtainium",
         "volume_ml": 10, "concentration": 0.5},
    ])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "UNKNOWN_REAGENT"


def test_multiple_invalid_steps_rejected_item_by_item():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 10, "concentration": 0.37},
        {"step": 1, "action": "add", "reagent": "unobtainium",
         "volume_ml": -3, "concentration": 2.0},
        {"step": 2, "action": "add", "reagent": "ethanol",
         "volume_ml": 0, "concentration": 0.95},
        {"step": 3, "action": "pour_out", "volume_ml": 99999},
    ])
    r = post(req)
    assert r.status_code == 422
    errors = r.json()["detail"]
    codes = {e["code"] for e in errors}
    # static phase: duplicate step, unknown reagent, non-positive volumes,
    # concentration out of range -- each tied to its own step
    assert {"DUPLICATE_STEP", "UNKNOWN_REAGENT", "NON_POSITIVE_VOLUME",
            "CONCENTRATION_OUT_OF_RANGE"} <= codes
    steps = {e["step"] for e in errors}
    assert {1, 2} <= steps
    # every error carries step/field/code/message
    for e in errors:
        assert {"step", "field", "code", "message"} <= set(e)


def test_pour_out_more_than_present_rejected():
    req = base_request(steps=[
        {"step": 1, "action": "pour_out", "volume_ml": 600},
    ])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "INSUFFICIENT_VOLUME"


def test_container_overflow_rejected():
    req = base_request(container={"material": "ptfe", "volume_ml": 550,
                                  "temperature_c": 25}, steps=[
        {"step": 1, "action": "add", "reagent": "water",
         "volume_ml": 100, "concentration": 1.0},
    ])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "CONTAINER_OVERFLOW"


def test_unknown_material_rejected():
    req = base_request(container={"material": "unobtainium", "volume_ml": 1000,
                                  "temperature_c": 25})
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "UNKNOWN_MATERIAL"


def test_unknown_allowed_contact_rule_rejected():
    req = base_request(allowed_contact=[{"rule_id": "nope", "max_seconds": 10}])
    r = post(req)
    assert r.status_code == 422
    assert r.json()["detail"][0]["code"] == "UNKNOWN_RULE_ID"


def test_schema_error_reformatted():
    r = client.post("/v1/precheck", json={"container": {"material": "ptfe"}})
    assert r.status_code == 422
    e = r.json()["detail"][0]
    assert e["code"] == "SCHEMA_VALIDATION"
    assert {"step", "field", "code", "message"} <= set(e)


# --------------------------------------------------------------------------- #
# contact-time declarations
# --------------------------------------------------------------------------- #
def _neutralization_request(durations, max_seconds):
    return base_request(
        container={"material": "ptfe", "volume_ml": 2000, "temperature_c": 25},
        initial=[{"reagent": "water", "volume_ml": 200, "concentration": 1.0}],
        allowed_contact=[{"rule_id": "strong_neutralization",
                          "max_seconds": max_seconds}],
        steps=[
            {"step": 1, "action": "add", "reagent": "hcl",
             "volume_ml": 100, "concentration": 0.37},
            {"step": 2, "action": "add", "reagent": "sodium_hydroxide",
             "volume_ml": 100, "concentration": 0.5, "duration_s": durations[0]},
            {"step": 3, "action": "dilute", "volume_ml": 100,
             "duration_s": durations[1]},
        ],
    )


def test_contact_timeout_fires_when_limit_exceeded():
    body = post(_neutralization_request([30, 45], 60)).json()
    assert body["status"] == "blocked"
    f = body["findings"][0]
    assert f["category"] == "contact_timeout"
    assert f["rule_id"] == "strong_neutralization"
    assert f["step"] == 3  # 30s + 45s = 75s > 60s
    assert body["first_trigger_step"] == 3
    # declared pair must NOT also fire an instant incompatible_pair finding
    assert all(x["category"] != "incompatible_pair" for x in body["findings"])


def test_contact_within_limit_no_finding():
    body = post(_neutralization_request([20, 30], 60)).json()
    assert body["status"] == "ok"
    assert body["findings"] == []


def test_undeclared_pair_blocks_immediately():
    req = _neutralization_request([0, 0], 60)
    del req["allowed_contact"]
    body = post(req).json()
    f = body["findings"][0]
    assert f["category"] == "incompatible_pair"
    assert f["step"] == 2  # fires at first co-presence


# --------------------------------------------------------------------------- #
# material incompatibility
# --------------------------------------------------------------------------- #
def test_hf_in_glass_blocked():
    req = base_request(container={"material": "glass", "volume_ml": 1000,
                                  "temperature_c": 25}, steps=[
        {"step": 1, "action": "add", "reagent": "hydrofluoric_acid",
         "volume_ml": 10, "concentration": 0.49},
    ])
    body = post(req).json()
    assert body["status"] == "blocked"
    f = body["findings"][0]
    assert f["category"] == "material_incompatibility"
    assert f["rule_id"] == "hf_etches_glass"
    assert f["step"] == 1


def test_hf_in_ptfe_ok():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hydrofluoric_acid",
         "volume_ml": 10, "concentration": 0.49},
    ])
    assert post(req).json()["status"] == "ok"


def test_material_temperature_limit():
    req = base_request(container={"material": "polystyrene", "volume_ml": 1000,
                                  "temperature_c": 90}, steps=[
        {"step": 1, "action": "add", "reagent": "water",
         "volume_ml": 10, "concentration": 1.0},
    ])
    body = post(req).json()
    f = body["findings"][0]
    assert f["rule_id"] == "material_temperature_limit"
    assert f["step"] == 0


# --------------------------------------------------------------------------- #
# ordering / trace
# --------------------------------------------------------------------------- #
def test_findings_sorted_by_step_stably():
    req = base_request(container={"material": "glass", "volume_ml": 2000,
                                  "temperature_c": 25}, steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 50, "concentration": 0.37},
        {"step": 2, "action": "add", "reagent": "sodium_hypochlorite",
         "volume_ml": 20, "concentration": 0.12},
        {"step": 3, "action": "add", "reagent": "hydrofluoric_acid",
         "volume_ml": 10, "concentration": 0.49},
    ])
    body = post(req).json()
    steps = [f["step"] for f in body["findings"]]
    assert steps == sorted(steps)
    assert body["first_trigger_step"] == 2
    rule_by_step = {f["step"]: f["rule_id"] for f in body["findings"]}
    assert rule_by_step[2] == "acid_hypochlorite"
    assert rule_by_step[3] == "hf_etches_glass"


def test_steps_executed_in_step_order_not_request_order():
    req = base_request(steps=[
        {"step": 2, "action": "add", "reagent": "ethanol",
         "volume_ml": 100, "concentration": 1.0},
        {"step": 1, "action": "add", "reagent": "water",
         "volume_ml": 100, "concentration": 1.0},
    ])
    body = post(req).json()
    assert [t["step"] for t in body["trace"]] == [0, 1, 2]
    assert body["final_state"]["total_volume_ml"] == pytest.approx(700)


def test_trace_contains_state_ids_and_concentrations():
    req = base_request(steps=[
        {"step": 1, "action": "add", "reagent": "hcl",
         "volume_ml": 50, "concentration": 0.37},
        {"step": 2, "action": "dilute", "volume_ml": 450},
    ])
    body = post(req).json()
    t1, t2 = body["trace"][1], body["trace"][2]
    assert t1["state_id"] != t2["state_id"]
    # dilution lowers the estimated hcl concentration
    assert t2["components"]["hcl"] < t1["components"]["hcl"]
    assert t2["total_volume_ml"] == pytest.approx(1000)
