"""
高熵合金常用经验规则计算。

约定：
- 成分使用 dict[str, float]，键为元素符号，值为原子百分比（at.%）或摩尔分数均可。
  内部会自动归一化到摩尔分数（和为 1）。
- 所有函数返回结构化结果（含数值 + 单位 + 解释），便于 Agent 使用。
"""

from __future__ import annotations

import math
from typing import Any

from hea_data import (
    ELEMENTS,
    SIMILARITY_GROUPS,
    find_similar_elements,
    get_mixing_enthalpy,
)

R_GAS = 8.314  # J/(mol*K)


def _normalize(composition: dict[str, float]) -> dict[str, float]:
    total = sum(composition.values())
    if total <= 0:
        raise ValueError("成分总和必须大于 0")
    return {el: amt / total for el, amt in composition.items()}


def _validate_elements(composition: dict[str, float]) -> list[str]:
    unknown = [el for el in composition if el not in ELEMENTS]
    return unknown


def compute_vec(composition: dict[str, float]) -> dict[str, Any]:
    """Valence Electron Concentration."""
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "vec": None}
    c = _normalize(composition)
    vec = sum(c[el] * ELEMENTS[el]["vec"] for el in c)
    if vec < 6.87:
        phase_hint = "倾向 BCC"
    elif vec < 8.0:
        phase_hint = "BCC + FCC 共存可能"
    else:
        phase_hint = "倾向 FCC"
    return {
        "vec": round(vec, 3),
        "unit": "electrons/atom",
        "phase_hint": phase_hint,
        "rule": "VEC < 6.87 BCC; 6.87 <= VEC < 8.0 mixed; VEC >= 8.0 FCC",
    }


def compute_delta(composition: dict[str, float]) -> dict[str, Any]:
    """原子尺寸差 δ (%)."""
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "delta": None}
    c = _normalize(composition)
    r_avg = sum(c[el] * ELEMENTS[el]["r"] for el in c)
    delta_sq = sum(c[el] * (1 - ELEMENTS[el]["r"] / r_avg) ** 2 for el in c)
    delta = 100 * math.sqrt(delta_sq)
    if delta < 4.0:
        risk = "low"
    elif delta < 6.6:
        risk = "moderate"
    else:
        risk = "high (倾向形成金属间化合物)"
    return {
        "delta": round(delta, 3),
        "unit": "%",
        "r_avg_pm": round(r_avg, 2),
        "lattice_distortion_risk": risk,
        "rule": "δ < 6.6% 倾向形成固溶体",
    }


def compute_mixing_enthalpy(composition: dict[str, float]) -> dict[str, Any]:
    """混合焓 ΔHmix (kJ/mol)，基于 Miedema 二元拟合 + 正则溶液近似。"""
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "delta_h_mix": None}
    c = _normalize(composition)
    elems = list(c.keys())
    h_mix = 0.0
    missing_pairs: list[tuple[str, str]] = []
    for i in range(len(elems)):
        for j in range(i + 1, len(elems)):
            a, b = elems[i], elems[j]
            ab = get_mixing_enthalpy(a, b)
            if ab is None:
                missing_pairs.append((a, b))
                continue
            h_mix += 4 * ab * c[a] * c[b]

    if -22 <= h_mix <= 7:
        ss_hint = "在固溶体形成窗口内"
    elif h_mix < -22:
        ss_hint = "焓过负，倾向形成金属间化合物"
    else:
        ss_hint = "焓过正，倾向相分离"

    result = {
        "delta_h_mix": round(h_mix, 3),
        "unit": "kJ/mol",
        "solid_solution_hint": ss_hint,
        "rule": "-22 <= ΔHmix <= 7 kJ/mol 倾向固溶体",
    }
    if missing_pairs:
        result["warning"] = f"以下二元对缺数据，已按 0 估算: {missing_pairs}"
    return result


def compute_entropy(composition: dict[str, float]) -> dict[str, Any]:
    """配置熵 ΔSmix = -R Σ ci ln(ci), 单位 J/(mol*K)."""
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "delta_s_mix": None}
    c = _normalize(composition)
    s_mix = -R_GAS * sum(ci * math.log(ci) for ci in c.values() if ci > 0)
    if s_mix < 1.0 * R_GAS:
        category = "low entropy"
    elif s_mix < 1.5 * R_GAS:
        category = "medium entropy"
    else:
        category = "high entropy"
    return {
        "delta_s_mix": round(s_mix, 3),
        "unit": "J/(mol*K)",
        "category": category,
        "rule": "ΔSmix >= 1.5R 通常定义为高熵合金",
    }


def compute_omega(composition: dict[str, float]) -> dict[str, Any]:
    """Ω = Tm * ΔSmix / |ΔHmix|, 衡量固溶体稳定性的相对强度。"""
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "omega": None}
    c = _normalize(composition)
    tm = sum(c[el] * ELEMENTS[el]["tm"] for el in c)
    s_mix = compute_entropy(c)["delta_s_mix"]
    h_mix_kj = compute_mixing_enthalpy(c)["delta_h_mix"]
    h_mix_j = h_mix_kj * 1000
    if abs(h_mix_j) < 1e-6:
        omega = float("inf")
        ss_hint = "ΔHmix 近 0，Ω 不适用"
    else:
        omega = (tm * s_mix) / abs(h_mix_j)
        ss_hint = "倾向固溶体" if omega >= 1.1 else "固溶体形成不利"
    return {
        "omega": round(omega, 3) if math.isfinite(omega) else None,
        "tm_avg_K": round(tm, 2),
        "delta_s_mix": round(s_mix, 3),
        "delta_h_mix_kJmol": round(h_mix_kj, 3),
        "solid_solution_hint": ss_hint,
        "rule": "Ω >= 1.1 倾向形成固溶体",
    }


def estimate_density(composition: dict[str, float]) -> dict[str, Any]:
    """密度的混合规则估算 (g/cm^3)，基于摩尔分数加权。
    注意：这是一阶近似，未考虑晶格畸变和点阵参数变化。
    """
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "density": None}
    c = _normalize(composition)
    m_avg = sum(c[el] * ELEMENTS[el]["mass"] for el in c)
    v_avg = sum(c[el] * ELEMENTS[el]["mass"] / ELEMENTS[el]["density"] for el in c)
    rho = m_avg / v_avg if v_avg > 0 else None
    return {
        "density": round(rho, 3) if rho else None,
        "unit": "g/cm^3",
        "method": "rule of mixtures (volume-weighted)",
        "note": "未考虑晶格畸变，仅作筛选用",
    }


def check_toxicity(composition: dict[str, float]) -> dict[str, Any]:
    """毒性 / 放射性元素检查。"""
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}"}
    flags: list[dict[str, str]] = []
    for el in composition:
        tox = ELEMENTS[el]["tox"]
        if tox != "safe":
            flags.append({"element": el, "level": tox})
    if not flags:
        return {"status": "safe", "flags": []}
    levels = {f["level"] for f in flags}
    overall = "radioactive" if "radioactive" in levels else (
        "toxic" if "toxic" in levels else "caution"
    )
    return {"status": overall, "flags": flags}


def hume_rothery_check(composition: dict[str, float]) -> dict[str, Any]:
    """Hume-Rothery + 高熵经验规则综合检查。"""
    vec = compute_vec(composition)
    delta = compute_delta(composition)
    h_mix = compute_mixing_enthalpy(composition)
    s_mix = compute_entropy(composition)
    omega = compute_omega(composition)
    tox = check_toxicity(composition)

    passes: list[str] = []
    warnings: list[str] = []

    if delta.get("delta") is not None:
        if delta["delta"] < 6.6:
            passes.append(f"δ = {delta['delta']}% < 6.6%")
        else:
            warnings.append(f"δ = {delta['delta']}% 超过 6.6%，可能形成金属间化合物")

    if h_mix.get("delta_h_mix") is not None:
        h = h_mix["delta_h_mix"]
        if -22 <= h <= 7:
            passes.append(f"ΔHmix = {h} kJ/mol 在 [-22, 7] 区间")
        else:
            warnings.append(f"ΔHmix = {h} kJ/mol 超出固溶体窗口")

    if omega.get("omega") is not None and omega["omega"] >= 1.1:
        passes.append(f"Ω = {omega['omega']} >= 1.1")
    elif omega.get("omega") is not None:
        warnings.append(f"Ω = {omega['omega']} < 1.1，固溶体形成不利")

    if tox["status"] != "safe":
        warnings.append(f"毒性/放射性元素警告: {tox['flags']}")

    verdict = "likely solid solution" if not warnings else "needs further verification"

    return {
        "verdict": verdict,
        "passes": passes,
        "warnings": warnings,
        "details": {
            "vec": vec,
            "delta": delta,
            "delta_h_mix": h_mix,
            "delta_s_mix": s_mix,
            "omega": omega,
            "toxicity": tox,
        },
    }


def estimate_modulus(composition: dict[str, float]) -> dict[str, Any]:
    """用混合规则估算杨氏模量 E (GPa)。

    E_avg = sum(c_i * E_i)，一阶近似。
    实际 HEA 由于固溶强化、晶格畸变，常比 ROM 高 5-15%。
    """
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "modulus_E": None}
    c = _normalize(composition)
    missing: list[str] = []
    e_avg = 0.0
    for el, ci in c.items():
        e_i = ELEMENTS[el].get("E_GPa")
        if e_i is None:
            missing.append(el)
            continue
        e_avg += ci * e_i
    if missing:
        return {
            "modulus_E": None,
            "unit": "GPa",
            "warning": f"缺少弹性模量数据: {missing}",
        }
    # 经验比强度（specific stiffness）：E / 密度
    rho_result = estimate_density(c)
    rho = rho_result.get("density")
    specific_stiffness = round(e_avg / rho, 3) if rho else None
    return {
        "modulus_E": round(e_avg, 2),
        "unit": "GPa",
        "method": "rule of mixtures (atomic-fraction weighted)",
        "specific_stiffness_GPa_cm3_per_g": specific_stiffness,
        "note": (
            "一阶 ROM 估算，未考虑固溶强化与晶格畸变；"
            "实际 HEA 模量常比 ROM 高 5-15%。"
        ),
    }


def compute_pilling_bedworth(composition: dict[str, float]) -> dict[str, Any]:
    """估算合金的 Pilling-Bedworth Ratio (PBR)。

    PBR = V_oxide / (n * V_metal)。
    - PBR < 1: 氧化膜不连续，保护性差
    - 1 <= PBR <= 2: 通常形成致密保护性氧化膜（最佳）
    - PBR > 2: 氧化膜压应力大，易剥落
    - PBR > 3: 氧化膜失效风险高

    对多组分合金，取摩尔分数加权的 PBR 作为粗略近似。
    实际抗氧化性还取决于氧化物的热力学稳定性、扩散动力学等。
    """
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "pbr": None}
    c = _normalize(composition)
    missing: list[str] = []
    pbr = 0.0
    contributions: dict[str, float] = {}
    for el, ci in c.items():
        pbr_i = ELEMENTS[el].get("PBR")
        if pbr_i is None:
            missing.append(el)
            continue
        pbr += ci * pbr_i
        contributions[el] = round(pbr_i, 2)
    if missing:
        return {"pbr": None, "warning": f"缺少 PBR 数据: {missing}"}

    if pbr < 1.0:
        oxidation_hint = "氧化膜不连续，抗氧化性差"
    elif pbr <= 2.0:
        oxidation_hint = "PBR 在保护性窗口内，潜在抗氧化性好"
    elif pbr <= 3.0:
        oxidation_hint = "PBR 偏高，氧化膜可能因压应力剥落"
    else:
        oxidation_hint = "PBR 过高，氧化膜失效风险大"

    return {
        "pbr": round(pbr, 3),
        "unit": "dimensionless",
        "rule": "1 <= PBR <= 2 倾向形成致密保护性氧化膜",
        "oxidation_hint": oxidation_hint,
        "per_element_pbr": contributions,
        "note": (
            "线性加权是粗略近似；准确抗氧化性需结合氧化物热力学稳定性、"
            "扩散动力学（如 Al/Cr/Si 形成连续 Al2O3/Cr2O3/SiO2 膜的能力）。"
        ),
    }


def estimate_cost(composition: dict[str, float]) -> dict[str, Any]:
    """估算合金的原料成本 (USD/kg)。

    基于元素原料价格的质量分数加权。
    价格数据为 2024 年量级估算，月度波动 ±30%，仅用于相对比较。
    """
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "cost_per_kg": None}
    c = _normalize(composition)

    # 先把原子分数换算为质量分数
    m_total = sum(c[el] * ELEMENTS[el]["mass"] for el in c)
    wt_fraction = {el: c[el] * ELEMENTS[el]["mass"] / m_total for el in c}

    missing: list[str] = []
    cost = 0.0
    contributions: dict[str, dict] = {}
    for el, wi in wt_fraction.items():
        price = ELEMENTS[el].get("price_USD_per_kg")
        if price is None:
            missing.append(el)
            continue
        contrib = wi * price
        cost += contrib
        contributions[el] = {
            "wt_fraction": round(wi, 4),
            "price_USD_per_kg": price,
            "contribution_USD_per_kg": round(contrib, 2),
        }

    if missing:
        return {
            "cost_per_kg": None,
            "warning": f"缺少价格数据或不可商用元素: {missing}",
        }

    # 成本分级
    if cost < 5:
        tier = "low cost (commodity-grade)"
    elif cost < 30:
        tier = "moderate cost"
    elif cost < 100:
        tier = "high cost (specialty alloy)"
    else:
        tier = "very high cost (research / aerospace grade)"

    # 找最贵的贡献元素
    top_drivers = sorted(
        contributions.items(),
        key=lambda kv: kv[1]["contribution_USD_per_kg"],
        reverse=True,
    )[:3]

    return {
        "cost_per_kg": round(cost, 2),
        "unit": "USD/kg",
        "cost_tier": tier,
        "top_cost_drivers": [
            {"element": el, **info} for el, info in top_drivers
        ],
        "method": "weight-fraction-weighted raw material cost",
        "note": (
            "价格为 2024 年量级估算，未含加工、熔炼、热处理与损耗成本；"
            "实际工业 HEA 制造成本通常为原料成本的 3-10 倍。"
        ),
    }


def compare_compositions(
    compositions: list[dict[str, float]],
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """对多个候选成分做横向对比。

    返回一张汇总表，便于 Agent 做 Pareto 分析与多目标决策。
    """
    if not compositions:
        return {"error": "compositions 不能为空"}
    if labels is None:
        labels = [f"C{i+1}" for i in range(len(compositions))]
    if len(labels) != len(compositions):
        return {"error": "labels 长度必须与 compositions 一致"}

    rows: list[dict[str, Any]] = []
    for label, comp in zip(labels, compositions):
        unknown = _validate_elements(comp)
        if unknown:
            rows.append({
                "label": label,
                "composition": comp,
                "error": f"未知元素: {unknown}",
            })
            continue
        c = _normalize(comp)
        vec = compute_vec(c)
        delta = compute_delta(c)
        h_mix = compute_mixing_enthalpy(c)
        s_mix = compute_entropy(c)
        omega = compute_omega(c)
        density = estimate_density(c)
        modulus = estimate_modulus(c)
        cost = estimate_cost(c)
        pbr = compute_pilling_bedworth(c)
        tox = check_toxicity(c)
        rows.append({
            "label": label,
            "composition_at_fraction": {k: round(v, 4) for k, v in c.items()},
            "VEC": vec.get("vec"),
            "delta_pct": delta.get("delta"),
            "delta_h_mix_kJmol": h_mix.get("delta_h_mix"),
            "delta_s_mix_J_molK": s_mix.get("delta_s_mix"),
            "omega": omega.get("omega"),
            "density_g_cm3": density.get("density"),
            "modulus_E_GPa": modulus.get("modulus_E"),
            "specific_stiffness": modulus.get(
                "specific_stiffness_GPa_cm3_per_g"
            ),
            "pbr": pbr.get("pbr"),
            "cost_USD_per_kg": cost.get("cost_per_kg"),
            "cost_tier": cost.get("cost_tier"),
            "toxicity": tox.get("status"),
        })

    # Pareto 提示：用户可以基于哪些字段做多目标筛选
    return {
        "n_candidates": len(rows),
        "rows": rows,
        "pareto_axes_hint": [
            "density_g_cm3 (lower better)",
            "cost_USD_per_kg (lower better)",
            "modulus_E_GPa (higher better)",
            "omega (higher better, solid-solution stability)",
            "pbr (closer to [1, 2] better)",
        ],
        "note": "未做归一化或多目标加权，留给 Agent / 用户决策。",
    }


def suggest_substitutions(
    composition: dict[str, float],
    goal: str = "reduce_density",
    max_suggestions: int = 5,
) -> dict[str, Any]:
    """基于目标推荐元素替代方案。

    支持的 goal:
      - reduce_density:  降密度
      - reduce_cost:     降成本
      - increase_omega:  提升 Ω（固溶体稳定性）
      - promote_bcc:     促进 BCC 相
      - promote_fcc:     促进 FCC 相
      - remove_toxicity: 去除毒性元素
      - improve_oxidation: 改善抗氧化（引入 Al/Cr/Si）

    返回若干"用 X 替代 Y"的建议，并解释理由。
    """
    unknown = _validate_elements(composition)
    if unknown:
        return {"error": f"未知元素: {unknown}", "suggestions": []}
    c = _normalize(composition)

    suggestions: list[dict[str, Any]] = []

    if goal == "reduce_density":
        heavy = sorted(
            c.keys(),
            key=lambda el: ELEMENTS[el]["density"],
            reverse=True,
        )
        for el in heavy[:2]:
            for cand in find_similar_elements(el):
                if cand in c:
                    continue
                if ELEMENTS[cand]["density"] >= ELEMENTS[el]["density"]:
                    continue
                suggestions.append({
                    "replace": el,
                    "with": cand,
                    "rationale": (
                        f"{cand} 密度 {ELEMENTS[cand]['density']} 低于 "
                        f"{el} 的 {ELEMENTS[el]['density']} g/cm^3，"
                        f"且与 {el} 同族/电子结构相近，预期相结构变化小"
                    ),
                })

    elif goal == "reduce_cost":
        expensive = sorted(
            c.keys(),
            key=lambda el: ELEMENTS[el].get("price_USD_per_kg") or 0,
            reverse=True,
        )
        for el in expensive[:2]:
            for cand in find_similar_elements(el):
                if cand in c:
                    continue
                p_cand = ELEMENTS[cand].get("price_USD_per_kg")
                p_el = ELEMENTS[el].get("price_USD_per_kg")
                if p_cand is None or p_el is None:
                    continue
                if p_cand >= p_el:
                    continue
                suggestions.append({
                    "replace": el,
                    "with": cand,
                    "rationale": (
                        f"{cand} 单价 ${p_cand}/kg 远低于 {el} 的 "
                        f"${p_el}/kg，同族替换风险较低"
                    ),
                })

    elif goal == "increase_omega":
        # Ω 提升的常见策略：提升 ΔSmix（加更多组元）或降低 |ΔHmix|
        suggestions.append({
            "strategy": "add_element",
            "candidates": [
                el for el in ["Cr", "Ti", "V", "Nb", "Ni", "Co"]
                if el not in c
            ][:max_suggestions],
            "rationale": (
                "引入新元素提升 ΔSmix；优先选与现有元素 ΔHmix 接近 0 的，"
                "避免大幅推高 |ΔHmix|"
            ),
        })

    elif goal == "promote_bcc":
        bcc_set = set(SIMILARITY_GROUPS["bcc_stabilizers"]) - set(c.keys())
        suggestions.append({
            "strategy": "add_bcc_stabilizer",
            "candidates": sorted(bcc_set)[:max_suggestions],
            "rationale": (
                "VEC < 6.87 倾向 BCC；加入这些元素可降低整体 VEC 并稳定 BCC"
            ),
        })

    elif goal == "promote_fcc":
        fcc_set = set(SIMILARITY_GROUPS["fcc_stabilizers"]) - set(c.keys())
        suggestions.append({
            "strategy": "add_fcc_stabilizer",
            "candidates": sorted(fcc_set)[:max_suggestions],
            "rationale": (
                "VEC >= 8.0 倾向 FCC；加入这些元素可提升整体 VEC"
            ),
        })

    elif goal == "remove_toxicity":
        for el in c:
            tox = ELEMENTS[el]["tox"]
            if tox in ("toxic", "radioactive"):
                # 推荐：同族 + safe 的替代
                alts = [
                    cand for cand in find_similar_elements(el)
                    if ELEMENTS[cand]["tox"] == "safe"
                ]
                suggestions.append({
                    "replace": el,
                    "tox_level": tox,
                    "with_candidates": alts[:max_suggestions],
                    "rationale": (
                        f"{el} 毒性等级 {tox}，建议用同族安全元素替代"
                    ),
                })

    elif goal == "improve_oxidation":
        oxidation_formers = ["Al", "Cr", "Si"]
        missing_formers = [el for el in oxidation_formers if el not in c]
        suggestions.append({
            "strategy": "add_protective_oxide_former",
            "candidates": missing_formers,
            "rationale": (
                "Al/Cr/Si 在高温下形成连续致密氧化膜 "
                "(Al2O3 / Cr2O3 / SiO2)，显著改善抗氧化性。"
                "工程经验：合金中至少含 5-10 at.% Al 或 Cr 才能形成稳定膜。"
            ),
        })

    else:
        return {
            "error": f"未知 goal: {goal}",
            "supported_goals": [
                "reduce_density", "reduce_cost", "increase_omega",
                "promote_bcc", "promote_fcc", "remove_toxicity",
                "improve_oxidation",
            ],
        }

    return {
        "goal": goal,
        "current_composition": {k: round(v, 4) for k, v in c.items()},
        "suggestions": suggestions[:max_suggestions]
        if isinstance(suggestions, list) and suggestions
        and "candidates" not in suggestions[0]
        else suggestions,
        "note": (
            "建议为启发式提示，不替代完整筛选；"
            "替代后请用 full_screening 或 compare_compositions 重新评估"
        ),
    }


def full_screening(composition: dict[str, float]) -> dict[str, Any]:
    """对一个候选成分做完整筛选：规则 + 物理量 + 风险标签。"""
    unknown = _validate_elements(composition)
    if unknown:
        return {
            "error": f"未知元素: {unknown}",
            "known_elements": sorted(ELEMENTS.keys()),
        }
    c = _normalize(composition)
    return {
        "composition_at_fraction": {k: round(v, 4) for k, v in c.items()},
        "hume_rothery": hume_rothery_check(c),
        "density": estimate_density(c),
        "modulus": estimate_modulus(c),
        "pbr": compute_pilling_bedworth(c),
        "cost": estimate_cost(c),
    }
