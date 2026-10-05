"""Public-domain chemical incompatibility rules.

Two kinds of rules are evaluated against the simulated mixture state:

* ``PairRule``     - fires when two components of the mixture match the two
                     sides of the rule (by reagent id or by category) and both
                     estimated concentrations exceed the rule thresholds.
* ``MaterialRule`` - fires when a mixture component is incompatible with the
                     container material.

A ``PairRule`` whose ``rule_id`` is declared in the request's
``allowed_contact`` list does NOT fire immediately; instead the engine
accumulates contact time (sum of ``duration_s`` of the steps during which the
pair is co-present) and raises a ``contact_timeout`` finding once the declared
``max_seconds`` is exceeded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: severities that block the mixing plan
BLOCKING_SEVERITIES = frozenset({"critical", "high"})


@dataclass(frozen=True)
class PairRule:
    rule_id: str
    severity: str  # critical | high | warning
    message: str
    categories_a: frozenset[str] = frozenset()
    categories_b: frozenset[str] = frozenset()
    reagents_a: frozenset[str] = frozenset()
    reagents_b: frozenset[str] = frozenset()
    min_conc_a: float = 1e-3  # mol/L in the mixture
    min_conc_b: float = 1e-3
    temp_min_c: float | None = None  # rule only applies at/above this temperature
    except_pairs: frozenset[frozenset[str]] = frozenset()


@dataclass(frozen=True)
class MaterialRule:
    rule_id: str
    severity: str
    message: str
    materials: frozenset[str]
    categories: frozenset[str] = frozenset()
    reagents: frozenset[str] = frozenset()
    min_conc: float = 1e-3  # mol/L in the mixture


_fs = frozenset

PAIR_RULES: list[PairRule] = [
    PairRule(
        "acid_hypochlorite", "critical",
        "酸与次氯酸盐混合会释放剧毒氯气(Cl2)",
        categories_a=_fs({"acid"}), reagents_b=_fs({"sodium_hypochlorite"}),
    ),
    PairRule(
        "ammonia_hypochlorite", "critical",
        "氨与次氯酸盐混合会生成有毒氯胺气体",
        reagents_a=_fs({"ammonia"}), reagents_b=_fs({"sodium_hypochlorite"}),
    ),
    PairRule(
        "acid_cyanide", "critical",
        "酸与氰化物混合会释放剧毒氰化氢(HCN)气体",
        categories_a=_fs({"acid"}), reagents_b=_fs({"sodium_cyanide"}),
    ),
    PairRule(
        "acid_sulfide", "critical",
        "酸与硫化物混合会释放剧毒硫化氢(H2S)气体",
        categories_a=_fs({"acid"}), reagents_b=_fs({"sodium_sulfide"}),
    ),
    PairRule(
        "peroxide_acetone", "critical",
        "过氧化氢与丙酮可生成高爆炸性的丙酮过氧化物",
        reagents_a=_fs({"hydrogen_peroxide"}), reagents_b=_fs({"acetone"}),
        min_conc_a=0.1, min_conc_b=0.1,
    ),
    PairRule(
        "oxidizer_organic", "high",
        "强氧化剂与有机溶剂混合有着火/爆炸风险",
        categories_a=_fs({"strong_oxidizer"}), categories_b=_fs({"organic_solvent"}),
        min_conc_a=0.5, min_conc_b=0.5,
        except_pairs=_fs({_fs({"hydrogen_peroxide", "acetone"})}),  # covered by peroxide_acetone
    ),
    PairRule(
        "permanganate_sulfuric", "critical",
        "高锰酸盐与浓硫酸混合可生成易爆的七氧化二锰(Mn2O7)",
        reagents_a=_fs({"potassium_permanganate"}), reagents_b=_fs({"sulfuric_acid"}),
        min_conc_a=0.01, min_conc_b=1.0,
    ),
    PairRule(
        "strong_neutralization", "warning",
        "强酸与强碱混合剧烈放热，可能导致沸腾飞溅",
        categories_a=_fs({"strong_acid"}), categories_b=_fs({"strong_base"}),
        min_conc_a=0.5, min_conc_b=0.5,
    ),
    PairRule(
        "neutralization_overtemp", "high",
        "容器温度较高，强酸强碱中和放热有热失控风险",
        categories_a=_fs({"strong_acid"}), categories_b=_fs({"strong_base"}),
        min_conc_a=0.5, min_conc_b=0.5, temp_min_c=40.0,
    ),
]

MATERIAL_RULES: list[MaterialRule] = [
    MaterialRule(
        "hf_etches_glass", "critical",
        "氢氟酸会腐蚀玻璃/陶瓷容器，应改用 PTFE 或 HDPE 容器",
        reagents=_fs({"hydrofluoric_acid"}),
        materials=_fs({"glass", "borosilicate_glass", "ceramic"}),
        min_conc=1e-4,
    ),
    MaterialRule(
        "strong_base_aluminum", "high",
        "强碱会腐蚀铝制容器并释放氢气",
        categories=_fs({"strong_base"}), materials=_fs({"aluminum"}),
        min_conc=0.5,
    ),
    MaterialRule(
        "organic_solvent_polystyrene", "high",
        "有机溶剂会溶解聚苯乙烯(PS)容器",
        categories=_fs({"organic_solvent"}), materials=_fs({"polystyrene"}),
        min_conc=0.1,
    ),
    MaterialRule(
        "hypochlorite_stainless", "warning",
        "次氯酸盐对不锈钢容器有腐蚀性",
        reagents=_fs({"sodium_hypochlorite"}), materials=_fs({"stainless_steel"}),
        min_conc=0.05,
    ),
]

PAIR_RULE_IDS: set[str] = {r.rule_id for r in PAIR_RULES}
