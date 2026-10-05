"""Reagent and container-material catalog.

Concentrations throughout the service are expressed as *mass fraction* of the
active component in the stock liquid (0, 1].  The engine converts to moles via
``moles = volume_ml * density * mass_fraction / molar_mass`` and reports
estimated molar concentrations (mol/L) of each component in the mixture.

Densities are typical values near the common commercial concentration; they
are approximations and documented as such in the README.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reagent:
    rid: str
    name: str
    molar_mass: float  # g/mol of the active component
    density: float  # g/mL of the typical stock solution
    max_fraction: float  # max commercially common mass fraction
    categories: frozenset[str]


def _r(rid, name, mw, density, max_fraction, categories):
    return Reagent(rid, name, mw, density, max_fraction, frozenset(categories))


REAGENTS: dict[str, Reagent] = {r.rid: r for r in [
    _r("water", "水", 18.015, 1.000, 1.00, {"solvent", "aqueous"}),
    _r("hcl", "盐酸", 36.461, 1.18, 0.38, {"acid", "strong_acid"}),
    _r("sulfuric_acid", "硫酸", 98.079, 1.84, 0.98, {"acid", "strong_acid", "dehydrating"}),
    _r("nitric_acid", "硝酸", 63.012, 1.51, 0.70, {"acid", "strong_acid", "strong_oxidizer"}),
    _r("hydrofluoric_acid", "氢氟酸", 20.006, 1.15, 0.49, {"acid", "weak_acid", "hydrofluoric"}),
    _r("acetic_acid", "乙酸(醋酸)", 60.052, 1.049, 1.00, {"acid", "weak_acid", "organic_acid"}),
    _r("sodium_hydroxide", "氢氧化钠溶液", 40.000, 1.52, 0.50, {"base", "strong_base"}),
    _r("potassium_hydroxide", "氢氧化钾溶液", 56.106, 1.45, 0.45, {"base", "strong_base"}),
    _r("ammonia", "氨水", 17.031, 0.91, 0.30, {"base", "weak_base", "amine"}),
    _r("sodium_hypochlorite", "次氯酸钠溶液(漂白剂)", 74.442, 1.21, 0.15,
       {"base", "oxidizer", "hypochlorite"}),
    _r("hydrogen_peroxide", "过氧化氢溶液", 34.014, 1.45, 0.50,
       {"oxidizer", "strong_oxidizer", "peroxide"}),
    _r("potassium_permanganate", "高锰酸钾溶液", 158.034, 1.03, 0.06, {"strong_oxidizer"}),
    _r("ethanol", "乙醇", 46.069, 0.789, 1.00, {"organic_solvent", "alcohol", "flammable"}),
    _r("methanol", "甲醇", 32.042, 0.792, 1.00, {"organic_solvent", "alcohol", "flammable"}),
    _r("isopropanol", "异丙醇", 60.096, 0.786, 1.00, {"organic_solvent", "alcohol", "flammable"}),
    _r("acetone", "丙酮", 58.080, 0.784, 1.00, {"organic_solvent", "ketone", "flammable"}),
    _r("sodium_cyanide", "氰化钠溶液", 49.008, 1.10, 0.30, {"base", "cyanide"}),
    _r("sodium_sulfide", "硫化钠溶液", 78.045, 1.15, 0.30, {"base", "sulfide"}),
]}


@dataclass(frozen=True)
class Material:
    mid: str
    name: str
    max_temp_c: float


MATERIALS: dict[str, Material] = {m.mid: m for m in [
    Material("glass", "钠钙玻璃", 300.0),
    Material("borosilicate_glass", "硼硅玻璃", 500.0),
    Material("ceramic", "陶瓷", 1000.0),
    Material("hdpe", "高密度聚乙烯(HDPE)", 80.0),
    Material("pp", "聚丙烯(PP)", 110.0),
    Material("ptfe", "聚四氟乙烯(PTFE)", 260.0),
    Material("polystyrene", "聚苯乙烯(PS)", 70.0),
    Material("stainless_steel", "不锈钢", 600.0),
    Material("aluminum", "铝", 300.0),
    Material("copper", "铜", 400.0),
]}
