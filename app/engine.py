"""Simulation engine and itemized validation.

The engine replays the ordered steps against a virtual container:

* ``add``       mix a reagent in; concentrations are recomputed.
* ``dilute``    add a pure solvent (default water); concentrations drop.
* ``pour_out``  remove a homogeneous fraction of the mixture.
* ``wash``      reset the container to empty; invalidates every state label
                saved so far (referencing them afterwards is rejected).
* ``transfer``  pour a portion of a previously labelled state back in.

After the initial load (reported as step 0) and after every step, all pair /
material rules are evaluated against the estimated component concentrations.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .catalog import MATERIALS, REAGENTS
from .models import PrecheckRequest, Step
from .rules import (
    BLOCKING_SEVERITIES,
    MATERIAL_RULES,
    PAIR_RULES,
    PAIR_RULE_IDS,
    MaterialRule,
    PairRule,
)

EPS = 1e-9
VOLUME_TOL = 1e-6


# --------------------------------------------------------------------------- #
# state
# --------------------------------------------------------------------------- #
@dataclass
class Snapshot:
    moles: dict[str, float]
    volume_ml: float
    epoch: int  # wash epoch the snapshot belongs to


@dataclass
class State:
    moles: dict[str, float] = field(default_factory=dict)
    volume_ml: float = 0.0
    epoch: int = 0  # incremented by every wash
    labels: dict[str, Snapshot] = field(default_factory=dict)
    fired: set[str] = field(default_factory=set)  # rule ids already reported
    contact: dict[str, float] = field(default_factory=dict)  # rule id -> seconds

    def concentrations(self) -> dict[str, float]:
        """Estimated molar concentration (mol/L) of every component."""
        if self.volume_ml <= EPS:
            return {}
        liters = self.volume_ml / 1000.0
        return {rid: m / liters for rid, m in self.moles.items() if m > EPS}

    def add_reagent(self, rid: str, volume_ml: float, fraction: float) -> None:
        r = REAGENTS[rid]
        self.moles[rid] = self.moles.get(rid, 0.0) + volume_ml * r.density * fraction / r.molar_mass
        self.volume_ml += volume_ml

    def snapshot(self) -> Snapshot:
        return Snapshot(dict(self.moles), self.volume_ml, self.epoch)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _err(step, field_, code, message):
    return {"step": step, "field": field_, "code": code, "message": message}


def _components(rids, concs):
    return [
        {"reagent": rid, "name": REAGENTS[rid].name,
         "concentration_mol_l": round(concs[rid], 6)}
        for rid in rids
    ]


def _finding(step, category, rule: str, severity, components, message):
    return {
        "step": step,
        "category": category,
        "rule_id": rule,
        "severity": severity,
        "blocking": severity in BLOCKING_SEVERITIES,
        "components": components,
        "message": message,
    }


def _side_match(rid: str, reagents: frozenset, categories: frozenset) -> bool:
    return rid in reagents or bool(REAGENTS[rid].categories & categories)


def _match_pairs(rule: PairRule, concs: dict[str, float], temp_c: float):
    """Return the list of (a_side_rid, b_side_rid) pairs currently matching."""
    if rule.temp_min_c is not None and temp_c < rule.temp_min_c:
        return []
    rids = sorted(concs)
    out = []
    for i in range(len(rids)):
        for j in range(i + 1, len(rids)):
            x, y = rids[i], rids[j]
            if frozenset((x, y)) in rule.except_pairs:
                continue
            if (_side_match(x, rule.reagents_a, rule.categories_a)
                    and _side_match(y, rule.reagents_b, rule.categories_b)
                    and concs[x] >= rule.min_conc_a and concs[y] >= rule.min_conc_b):
                out.append((x, y))
            elif (_side_match(y, rule.reagents_a, rule.categories_a)
                    and _side_match(x, rule.reagents_b, rule.categories_b)
                    and concs[y] >= rule.min_conc_a and concs[x] >= rule.min_conc_b):
                out.append((y, x))
    return out


def _match_material(rule: MaterialRule, concs: dict[str, float], material_id: str):
    if material_id not in rule.materials:
        return []
    return sorted(
        rid for rid, c in concs.items()
        if c >= rule.min_conc and _side_match(rid, rule.reagents, rule.categories)
    )


# --------------------------------------------------------------------------- #
# static (request-level) validation - every problem reported item by item
# --------------------------------------------------------------------------- #
def static_validate(req: PrecheckRequest) -> list[dict]:
    errors: list[dict] = []

    # container
    if req.container.material not in MATERIALS:
        errors.append(_err(None, "container.material", "UNKNOWN_MATERIAL",
                           f"未知容器材质 '{req.container.material}'，可选: {sorted(MATERIALS)}"))
    if req.container.volume_ml <= 0:
        errors.append(_err(None, "container.volume_ml", "NON_POSITIVE_VOLUME",
                           "容器容量必须为正数"))

    # initial liquids
    total_initial = 0.0
    for i, liq in enumerate(req.initial):
        loc = f"initial[{i}]"
        if liq.reagent not in REAGENTS:
            errors.append(_err(None, f"{loc}.reagent", "UNKNOWN_REAGENT",
                               f"未知试剂 '{liq.reagent}'"))
        if liq.volume_ml <= 0:
            errors.append(_err(None, f"{loc}.volume_ml", "NON_POSITIVE_VOLUME",
                               "体积必须为正数"))
        else:
            total_initial += liq.volume_ml
        errors.extend(_check_concentration(None, f"{loc}.concentration",
                                           liq.reagent, liq.concentration))
    if req.container.volume_ml > 0 and total_initial > req.container.volume_ml + VOLUME_TOL:
        errors.append(_err(None, "initial", "CONTAINER_OVERFLOW",
                           f"初始液体总体积 {total_initial:g} mL 超过容器容量 "
                           f"{req.container.volume_ml:g} mL"))

    # allowed contact declarations
    seen_rules: set[str] = set()
    for i, ac in enumerate(req.allowed_contact):
        loc = f"allowed_contact[{i}]"
        if ac.rule_id not in PAIR_RULE_IDS:
            errors.append(_err(None, f"{loc}.rule_id", "UNKNOWN_RULE_ID",
                               f"未知反应规则 '{ac.rule_id}'，可选: {sorted(PAIR_RULE_IDS)}"))
        elif ac.rule_id in seen_rules:
            errors.append(_err(None, f"{loc}.rule_id", "DUPLICATE_RULE_ID",
                               f"规则 '{ac.rule_id}' 的允许接触时长被重复声明"))
        seen_rules.add(ac.rule_id)
        if ac.max_seconds <= 0:
            errors.append(_err(None, f"{loc}.max_seconds", "NON_POSITIVE_DURATION",
                               "允许接触时长必须为正数"))

    # steps
    seen_steps: set[int] = set()
    for idx, s in enumerate(req.steps):
        loc = f"steps[{idx}]"
        if s.step < 1:
            errors.append(_err(s.step, f"{loc}.step", "INVALID_STEP_NUMBER",
                               "步骤序号必须为 >= 1 的整数"))
        if s.step in seen_steps:
            errors.append(_err(s.step, f"{loc}.step", "DUPLICATE_STEP",
                               f"步骤序号 {s.step} 重复"))
        seen_steps.add(s.step)
        if s.duration_s < 0:
            errors.append(_err(s.step, f"{loc}.duration_s", "NON_POSITIVE_DURATION",
                               "步骤时长不能为负数"))

        if s.action == "add":
            if not s.reagent:
                errors.append(_err(s.step, f"{loc}.reagent", "MISSING_FIELD",
                                   "add 动作必须提供 reagent"))
            elif s.reagent not in REAGENTS:
                errors.append(_err(s.step, f"{loc}.reagent", "UNKNOWN_REAGENT",
                                   f"未知试剂 '{s.reagent}'"))
            errors.extend(_check_volume(s.step, f"{loc}.volume_ml", s.volume_ml))
            if s.concentration is None:
                errors.append(_err(s.step, f"{loc}.concentration", "MISSING_FIELD",
                                   "add 动作必须提供 concentration"))
            else:
                errors.extend(_check_concentration(s.step, f"{loc}.concentration",
                                                   s.reagent, s.concentration))
        elif s.action == "dilute":
            solvent = s.solvent or "water"
            if solvent not in REAGENTS:
                errors.append(_err(s.step, f"{loc}.solvent", "UNKNOWN_REAGENT",
                                   f"未知溶剂 '{solvent}'"))
            errors.extend(_check_volume(s.step, f"{loc}.volume_ml", s.volume_ml))
        elif s.action == "pour_out":
            errors.extend(_check_volume(s.step, f"{loc}.volume_ml", s.volume_ml))
        elif s.action == "transfer":
            if not s.from_label:
                errors.append(_err(s.step, f"{loc}.from_label", "MISSING_FIELD",
                                   "transfer 动作必须提供 from_label"))
            errors.extend(_check_volume(s.step, f"{loc}.volume_ml", s.volume_ml))
        elif s.action == "wash":
            if s.cycles < 1:
                errors.append(_err(s.step, f"{loc}.cycles", "INVALID_CYCLES",
                                   "清洗次数必须为 >= 1 的整数"))

    return _sort_errors(errors)


def _check_volume(step, field_, volume):
    if volume is None:
        return [_err(step, field_, "MISSING_FIELD", "该动作必须提供 volume_ml")]
    if volume <= 0:
        return [_err(step, field_, "NON_POSITIVE_VOLUME", "体积必须为正数")]
    return []


def _check_concentration(step, field_, rid, conc):
    if not (0 < conc <= 1):
        return [_err(step, field_, "CONCENTRATION_OUT_OF_RANGE",
                     "浓度(质量分数)必须在 (0, 1] 区间内")]
    if rid in REAGENTS and conc > REAGENTS[rid].max_fraction + 1e-12:
        return [_err(step, field_, "CONCENTRATION_OUT_OF_RANGE",
                     f"浓度 {conc:g} 超过试剂 '{rid}' 常见市售上限 "
                     f"{REAGENTS[rid].max_fraction:g}")]
    return []


def _sort_errors(errors):
    return sorted(errors, key=lambda e: (e["step"] is None, e["step"] or 0,
                                         e["field"], e["code"]))


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #
def simulate(req: PrecheckRequest):
    """Replay the plan. Returns (runtime_errors, findings, trace)."""
    errors: list[dict] = []
    findings: list[dict] = []
    trace: list[dict] = []

    material = MATERIALS[req.container.material]
    allowed = {ac.rule_id: ac.max_seconds for ac in req.allowed_contact}
    st = State()

    for liq in req.initial:
        st.add_reagent(liq.reagent, liq.volume_ml, liq.concentration)
    trace.append(_trace_entry(st, 0, "initial"))
    _run_checks(req, st, material, allowed, 0, findings)

    for s in sorted(req.steps, key=lambda x: x.step):
        _apply_step(req, st, s, errors)
        if s.label:
            st.labels[s.label] = st.snapshot()
        _run_checks(req, st, material, allowed, s.step, findings,
                    duration_s=s.duration_s)
        trace.append(_trace_entry(st, s.step, s.action))

    return _sort_errors(errors), findings, trace


def _apply_step(req: PrecheckRequest, st: State, s: Step, errors: list[dict]) -> None:
    if s.action == "add":
        st.add_reagent(s.reagent, s.volume_ml, s.concentration)
    elif s.action == "dilute":
        st.add_reagent(s.solvent or "water", s.volume_ml, 1.0)
    elif s.action == "pour_out":
        if s.volume_ml > st.volume_ml + VOLUME_TOL:
            errors.append(_err(s.step, "volume_ml", "INSUFFICIENT_VOLUME",
                               f"倒出体积 {s.volume_ml:g} mL 超过当前容器内体积 "
                               f"{st.volume_ml:g} mL"))
            return
        frac = s.volume_ml / st.volume_ml if st.volume_ml > EPS else 0.0
        for rid in list(st.moles):
            st.moles[rid] *= (1.0 - frac)
            if st.moles[rid] <= EPS:
                del st.moles[rid]
        st.volume_ml = max(0.0, st.volume_ml - s.volume_ml)
    elif s.action == "wash":
        st.moles.clear()
        st.volume_ml = 0.0
        st.epoch += 1
        st.fired.clear()
        st.contact.clear()
        # labels keep existing but their epoch no longer matches -> stale
    elif s.action == "transfer":
        snap = st.labels.get(s.from_label)
        if snap is None:
            errors.append(_err(s.step, "from_label", "UNKNOWN_LABEL",
                               f"状态标签 '{s.from_label}' 未定义"))
            return
        if snap.epoch != st.epoch:
            errors.append(_err(s.step, "from_label", "STALE_STATE_REFERENCE",
                               f"状态标签 '{s.from_label}' 保存于清洗之前，"
                               "清洗后不得再引用旧状态"))
            return
        if s.volume_ml > snap.volume_ml + VOLUME_TOL:
            errors.append(_err(s.step, "volume_ml", "INSUFFICIENT_VOLUME",
                               f"转移体积 {s.volume_ml:g} mL 超过标签 "
                               f"'{s.from_label}' 对应混合物体积 {snap.volume_ml:g} mL"))
            return
        frac = s.volume_ml / snap.volume_ml if snap.volume_ml > EPS else 0.0
        for rid, m in snap.moles.items():
            st.moles[rid] = st.moles.get(rid, 0.0) + m * frac
        st.volume_ml += s.volume_ml

    if st.volume_ml > req.container.volume_ml + VOLUME_TOL:
        errors.append(_err(s.step, "volume_ml", "CONTAINER_OVERFLOW",
                           f"累计体积 {st.volume_ml:g} mL 超过容器容量 "
                           f"{req.container.volume_ml:g} mL"))


def _run_checks(req, st, material, allowed, step_no, findings, duration_s=0.0):
    concs = st.concentrations()
    temp = req.container.temperature_c

    # container temperature vs material limit (reported once)
    if "material_temperature_limit" not in st.fired and temp > material.max_temp_c:
        st.fired.add("material_temperature_limit")
        findings.append(_finding(
            step_no, "material_incompatibility", "material_temperature_limit", "high",
            [], f"容器温度 {temp:g}°C 超过材质 {material.name} 的耐受上限 "
               f"{material.max_temp_c:g}°C"))

    for rule in MATERIAL_RULES:
        if rule.rule_id in st.fired:
            continue
        matched = _match_material(rule, concs, material.mid)
        if matched:
            st.fired.add(rule.rule_id)
            findings.append(_finding(step_no, "material_incompatibility",
                                     rule.rule_id, rule.severity,
                                     _components(matched, concs), rule.message))

    for rule in PAIR_RULES:
        pairs = _match_pairs(rule, concs, temp)
        if not pairs:
            continue
        involved = sorted({rid for pair in pairs for rid in pair})
        if rule.rule_id in allowed:
            # declared pair: accumulate contact time instead of instant block
            st.contact[rule.rule_id] = st.contact.get(rule.rule_id, 0.0) + duration_s
            limit = allowed[rule.rule_id]
            if st.contact[rule.rule_id] > limit + EPS and rule.rule_id not in st.fired:
                st.fired.add(rule.rule_id)
                # exceeding a declared contact limit always blocks, even when
                # the underlying rule is only a warning
                severity = (rule.severity if rule.severity in BLOCKING_SEVERITIES
                            else "high")
                findings.append(_finding(
                    step_no, "contact_timeout", rule.rule_id, severity,
                    _components(involved, concs),
                    f"{rule.message}；累计接触 {st.contact[rule.rule_id]:g}s "
                    f"已超过声明的允许上限 {limit:g}s"))
        elif rule.rule_id not in st.fired:
            st.fired.add(rule.rule_id)
            findings.append(_finding(step_no, "incompatible_pair", rule.rule_id,
                                     rule.severity, _components(involved, concs),
                                     rule.message))


def _trace_entry(st: State, step_no: int, action: str) -> dict:
    concs = {k: round(v, 6) for k, v in sorted(st.concentrations().items())}
    fingerprint = json.dumps(
        {"v": round(st.volume_ml, 3), "c": concs, "e": st.epoch},
        sort_keys=True)
    return {
        "step": step_no,
        "action": action,
        "total_volume_ml": round(st.volume_ml, 3),
        "components": concs,
        "state_id": hashlib.sha256(fingerprint.encode()).hexdigest()[:12],
    }


# --------------------------------------------------------------------------- #
# response assembly
# --------------------------------------------------------------------------- #
def build_response(findings: list[dict], trace: list[dict]) -> dict:
    ordered = sorted(findings, key=lambda f: (f["step"], f["rule_id"]))
    blocking = any(f["blocking"] for f in ordered)
    status = "blocked" if blocking else ("warning" if ordered else "ok")
    return {
        "status": status,
        "first_trigger_step": ordered[0]["step"] if ordered else None,
        "findings": ordered,
        "trace": trace,
        "final_state": trace[-1] if trace else None,
    }
