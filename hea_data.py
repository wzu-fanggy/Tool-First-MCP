"""
高熵合金常用元素数据库。

数据来源说明：
- 原子半径 r：金属/Goldschmidt 半径，单位 pm
- 价电子数 vec：常用经验值（过渡金属取 s+d 电子）
- 密度 density：单位 g/cm^3，室温
- 熔点 tm：单位 K
- 原子量 mass：g/mol
- 毒性等级 tox：safe / caution / toxic / radioactive
- 弹性模量 E_GPa：杨氏模量，单位 GPa，室温
- 价格 price_USD_per_kg：参考 2024 年 LME / USGS / Mineral Commodities Summary
- PBR：Pilling-Bedworth Ratio，金属最稳定氧化物的体积比

二元混合焓数据基于 Miedema 模型常见简化表，单位 kJ/mol。
仅包含高熵合金研究中最常出现的元素子集。

参考：
- 弹性模量：CRC Handbook (95th ed.)
- 价格：USGS Mineral Commodity Summaries 2024（数量级估算，月度波动 ±30%）
- PBR：Birks, Meier & Pettit, "Introduction to the High Temperature
       Oxidation of Metals" (2nd ed., 2006)
"""

from __future__ import annotations

ELEMENTS: dict[str, dict] = {
    # element: {r, vec, density, tm, mass, tox, E_GPa, price_USD_per_kg, PBR}
    "Al": {"r": 143.2, "vec": 3,  "density": 2.70, "tm": 933,  "mass": 26.98,  "tox": "safe",        "E_GPa": 70,  "price_USD_per_kg": 2.3,  "PBR": 1.28},
    "Ti": {"r": 146.2, "vec": 4,  "density": 4.51, "tm": 1941, "mass": 47.87,  "tox": "safe",        "E_GPa": 116, "price_USD_per_kg": 8.0,  "PBR": 1.73},
    "V":  {"r": 134.6, "vec": 5,  "density": 6.11, "tm": 2183, "mass": 50.94,  "tox": "caution",     "E_GPa": 128, "price_USD_per_kg": 28.0, "PBR": 3.18},
    "Cr": {"r": 128.0, "vec": 6,  "density": 7.19, "tm": 2180, "mass": 52.00,  "tox": "caution",     "E_GPa": 279, "price_USD_per_kg": 9.0,  "PBR": 2.07},
    "Mn": {"r": 135.0, "vec": 7,  "density": 7.21, "tm": 1519, "mass": 54.94,  "tox": "safe",        "E_GPa": 198, "price_USD_per_kg": 2.0,  "PBR": 1.79},
    "Fe": {"r": 127.4, "vec": 8,  "density": 7.87, "tm": 1811, "mass": 55.85,  "tox": "safe",        "E_GPa": 211, "price_USD_per_kg": 0.5,  "PBR": 2.14},
    "Co": {"r": 125.1, "vec": 9,  "density": 8.90, "tm": 1768, "mass": 58.93,  "tox": "caution",     "E_GPa": 209, "price_USD_per_kg": 35.0, "PBR": 1.99},
    "Ni": {"r": 124.6, "vec": 10, "density": 8.91, "tm": 1728, "mass": 58.69,  "tox": "safe",        "E_GPa": 200, "price_USD_per_kg": 18.0, "PBR": 1.65},
    "Cu": {"r": 127.8, "vec": 11, "density": 8.96, "tm": 1358, "mass": 63.55,  "tox": "safe",        "E_GPa": 130, "price_USD_per_kg": 9.0,  "PBR": 1.68},
    "Zn": {"r": 139.4, "vec": 12, "density": 7.13, "tm": 693,  "mass": 65.38,  "tox": "safe",        "E_GPa": 108, "price_USD_per_kg": 2.5,  "PBR": 1.55},
    "Zr": {"r": 160.3, "vec": 4,  "density": 6.51, "tm": 2128, "mass": 91.22,  "tox": "safe",        "E_GPa": 88,  "price_USD_per_kg": 35.0, "PBR": 1.56},
    "Nb": {"r": 146.8, "vec": 5,  "density": 8.57, "tm": 2750, "mass": 92.91,  "tox": "safe",        "E_GPa": 105, "price_USD_per_kg": 45.0, "PBR": 2.68},
    "Mo": {"r": 139.0, "vec": 6,  "density": 10.22,"tm": 2896, "mass": 95.95,  "tox": "safe",        "E_GPa": 329, "price_USD_per_kg": 25.0, "PBR": 3.40},
    "Hf": {"r": 158.0, "vec": 4,  "density": 13.31,"tm": 2506, "mass": 178.49, "tox": "safe",        "E_GPa": 78,  "price_USD_per_kg": 1500.0,"PBR": 1.62},
    "Ta": {"r": 146.7, "vec": 5,  "density": 16.65,"tm": 3290, "mass": 180.95, "tox": "safe",        "E_GPa": 186, "price_USD_per_kg": 280.0,"PBR": 2.50},
    "W":  {"r": 139.4, "vec": 6,  "density": 19.25,"tm": 3695, "mass": 183.84, "tox": "safe",        "E_GPa": 411, "price_USD_per_kg": 30.0, "PBR": 3.40},
    "Re": {"r": 137.5, "vec": 7,  "density": 21.02,"tm": 3459, "mass": 186.21, "tox": "safe",        "E_GPa": 463, "price_USD_per_kg": 3000.0,"PBR": 3.92},
    "Si": {"r": 117.6, "vec": 4,  "density": 2.33, "tm": 1687, "mass": 28.09,  "tox": "safe",        "E_GPa": 130, "price_USD_per_kg": 3.0,  "PBR": 2.27},

    "Be": {"r": 112.8, "vec": 2,  "density": 1.85, "tm": 1560, "mass": 9.01,   "tox": "toxic",       "E_GPa": 287, "price_USD_per_kg": 850.0,"PBR": 1.68},
    "Pb": {"r": 175.0, "vec": 4,  "density": 11.34,"tm": 600,  "mass": 207.2,  "tox": "toxic",       "E_GPa": 16,  "price_USD_per_kg": 2.0,  "PBR": 1.40},
    "Cd": {"r": 151.0, "vec": 12, "density": 8.65, "tm": 594,  "mass": 112.41, "tox": "toxic",       "E_GPa": 50,  "price_USD_per_kg": 3.0,  "PBR": 1.27},
    "U":  {"r": 138.5, "vec": 6,  "density": 19.05,"tm": 1408, "mass": 238.03, "tox": "radioactive", "E_GPa": 208, "price_USD_per_kg": None, "PBR": 3.05},
}

MIXING_ENTHALPY: dict[tuple[str, str], float] = {
    ("Al", "Ti"): -30, ("Al", "V"): -16, ("Al", "Cr"): -10, ("Al", "Mn"): -19,
    ("Al", "Fe"): -11, ("Al", "Co"): -19, ("Al", "Ni"): -22, ("Al", "Cu"): -1,
    ("Al", "Zr"): -44, ("Al", "Nb"): -18, ("Al", "Mo"): -5,  ("Al", "Hf"): -39,
    ("Al", "Ta"): -19, ("Al", "W"):  -2,
    ("Ti", "V"): -2,   ("Ti", "Cr"): -7, ("Ti", "Mn"): -8,  ("Ti", "Fe"): -17,
    ("Ti", "Co"): -28, ("Ti", "Ni"): -35, ("Ti", "Cu"): -9, ("Ti", "Zr"): 0,
    ("Ti", "Nb"): 2,   ("Ti", "Mo"): -4, ("Ti", "Hf"): 0,   ("Ti", "Ta"): 1,
    ("Ti", "W"):  -6,
    ("V",  "Cr"): -2,  ("V",  "Mn"): -1, ("V",  "Fe"): -7,  ("V",  "Co"): -14,
    ("V",  "Ni"): -18, ("V",  "Cu"): 5,  ("V",  "Zr"): -4,  ("V",  "Nb"): -1,
    ("V",  "Mo"): 0,   ("V",  "Hf"): -2, ("V",  "Ta"): -1,  ("V",  "W"):  -1,
    ("Cr", "Mn"): 2,   ("Cr", "Fe"): -1, ("Cr", "Co"): -4,  ("Cr", "Ni"): -7,
    ("Cr", "Cu"): 12,  ("Cr", "Zr"): -12,("Cr", "Nb"): -7,  ("Cr", "Mo"): 0,
    ("Cr", "Hf"): -9,  ("Cr", "Ta"): -7, ("Cr", "W"):  1,
    ("Mn", "Fe"): 0,   ("Mn", "Co"): -5, ("Mn", "Ni"): -8,  ("Mn", "Cu"): 4,
    ("Mn", "Zr"): -15, ("Mn", "Nb"): -4, ("Mn", "Mo"): 5,   ("Mn", "Hf"): -12,
    ("Mn", "Ta"): -4,  ("Mn", "W"):  6,
    ("Fe", "Co"): -1,  ("Fe", "Ni"): -2, ("Fe", "Cu"): 13,  ("Fe", "Zr"): -25,
    ("Fe", "Nb"): -16, ("Fe", "Mo"): -2, ("Fe", "Hf"): -21, ("Fe", "Ta"): -15,
    ("Fe", "W"):  0,
    ("Co", "Ni"): 0,   ("Co", "Cu"): 6,  ("Co", "Zr"): -41, ("Co", "Nb"): -25,
    ("Co", "Mo"): -5,  ("Co", "Hf"): -35,("Co", "Ta"): -24, ("Co", "W"):  -1,
    ("Ni", "Cu"): 4,   ("Ni", "V"): -18,  ("Ni", "Zr"): -49,("Ni", "Nb"): -30, ("Ni", "Mo"): -7,
    ("Ni", "Hf"): -42, ("Ni", "Ta"): -29,("Ni", "W"):  -3,
    ("Cu", "Zr"): -23, ("Cu", "Nb"): 3,  ("Cu", "Mo"): 19,  ("Cu", "Hf"): -17,
    ("Cu", "Ta"): 2,   ("Cu", "W"):  22,
    ("Zr", "Nb"): 4,   ("Zr", "Mo"): -6, ("Zr", "Hf"): 0,   ("Zr", "Ta"): 3,
    ("Zr", "W"):  -9,
    ("Nb", "Mo"): -6,  ("Nb", "Hf"): 4,  ("Nb", "Ta"): 0,   ("Nb", "W"):  -8,
    ("Mo", "Hf"): -4,  ("Mo", "Ta"): -5, ("Mo", "W"):  0,
    ("Hf", "Ta"): 3,   ("Hf", "W"):  -6,
    ("Ta", "W"):  -7,
}


def get_mixing_enthalpy(a: str, b: str) -> float | None:
    """返回 a-b 二元混合焓（kJ/mol）。同元素返回 0，未知返回 None。"""
    if a == b:
        return 0.0
    if (a, b) in MIXING_ENTHALPY:
        return float(MIXING_ENTHALPY[(a, b)])
    if (b, a) in MIXING_ENTHALPY:
        return float(MIXING_ENTHALPY[(b, a)])
    return None


def known_elements() -> list[str]:
    return sorted(ELEMENTS.keys())


# 元素化学相似度分组：用于 suggest_substitutions
# 来源：周期表族 + HEA 文献中常用的替代关系
SIMILARITY_GROUPS: dict[str, list[str]] = {
    "refractory_4d_5d": ["Nb", "Mo", "Ta", "W", "Re"],
    "refractory_3d_4d": ["V", "Cr", "Nb", "Mo"],
    "group_IVB":        ["Ti", "Zr", "Hf"],
    "group_VB":         ["V", "Nb", "Ta"],
    "group_VIB":        ["Cr", "Mo", "W"],
    "fcc_stabilizers":  ["Ni", "Cu", "Co", "Fe"],
    "bcc_stabilizers":  ["V", "Cr", "Nb", "Mo", "Ta", "W", "Al"],
    "light_metals":     ["Al", "Ti", "V", "Si"],
    "magnetic_3d":      ["Fe", "Co", "Ni", "Mn"],
}


def find_similar_elements(element: str) -> list[str]:
    """返回与给定元素属于同一相似度分组的所有元素（不含自身）。"""
    if element not in ELEMENTS:
        return []
    similar: set[str] = set()
    for group in SIMILARITY_GROUPS.values():
        if element in group:
            similar.update(group)
    similar.discard(element)
    return sorted(similar)
