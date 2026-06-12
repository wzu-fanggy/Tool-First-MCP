"""
HEA MCP Server - 高熵合金规则计算工具集

使用方式：
    python hea_mcp_server.py

或通过 Claude Code / Cursor 配置 mcpServers 自动启动。

工具列表：
    电子/几何/热力学:
    - compute_vec               价电子浓度
    - compute_delta             原子尺寸差
    - compute_mixing_enthalpy   混合焓
    - compute_entropy           配置熵
    - compute_omega             Ω 参数
    物理性能:
    - estimate_density          密度估算
    - estimate_modulus          弹性模量估算
    - compute_pilling_bedworth  PBR 抗氧化指标
    工程性:
    - estimate_cost             原料成本估算
    - check_toxicity            毒性/放射性检查
    综合 & 决策辅助:
    - hume_rothery_check        综合规则检查
    - full_screening            一键完整筛选
    - compare_compositions      多候选横向对比
    - suggest_substitutions     基于目标的替代建议
    严格热力学（需 pycalphad + CoCrFeNiV.tdb）:
    - calc_phase_equilibrium    CALPHAD 平衡相计算（Co-Cr-Fe-Ni-V 五元 HEA）
    元数据:
    - list_known_elements       列出已知元素

资源：
    - hea://elements                所有元素物性数据
    - hea://elements/{symbol}       单个元素物性
"""

from __future__ import annotations

import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# pycalphad 可选依赖：优雅降级
# 如果未安装 pycalphad 或找不到 CoCrFeNiV.tdb，HAS_PYCALPHAD = False，
# calc_phase_equilibrium_tool 会返回友好的错误信息而不是崩溃。
# ---------------------------------------------------------------------------
_ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
_TDB_FILENAMES = ("CoCrFeNiV.tdb", "CoCrFeNiV.TDB-R3.txt")
_TDB_ELEMENTS = frozenset({"CO", "CR", "FE", "NI", "V"})

try:
    import numpy as np
    from pycalphad import Database, equilibrium
    import pycalphad.variables as v

    _TDB_PATH = ""
    for _name in _TDB_FILENAMES:
        _candidate = os.path.join(_ROOT_DIR, _name)
        if os.path.exists(_candidate):
            _TDB_PATH = _candidate
            break

    if _TDB_PATH:
        # 全局单例：只在模块加载时读取一次，避免每次调用重复 IO
        _DBF = Database(_TDB_PATH)
        # 从数据库中读取所有支持的相名称，供计算时使用
        _ALL_PHASES = list(_DBF.phases.keys())
        HAS_PYCALPHAD = True
    else:
        _DBF = None
        _ALL_PHASES = []
        HAS_PYCALPHAD = False

except Exception:
    _DBF = None
    _ALL_PHASES = []
    HAS_PYCALPHAD = False
    HAS_PYCALPHAD = False

from hea_data import ELEMENTS, known_elements
from hea_rules import (
    check_toxicity,
    compare_compositions,
    compute_delta,
    compute_entropy,
    compute_mixing_enthalpy,
    compute_omega,
    compute_pilling_bedworth,
    compute_vec,
    estimate_cost,
    estimate_density,
    estimate_modulus,
    full_screening,
    hume_rothery_check,
    suggest_substitutions,
)

mcp = FastMCP(
    "hea-rules",
    instructions=(
        "You are an HEA materials design assistant focused on the "
        "Co-Cr-Fe-Ni-V system (CoCrFeNiV CALPHAD database). "
        "CRITICAL RULE: You have NO internal knowledge of element properties, "
        "binary mixing enthalpies, or alloy thermodynamics. "
        "For ANY numerical quantity (density, VEC, delta, ΔHmix, Ω, cost, toxicity), "
        "you MUST call the corresponding MCP tool. "
        "NEVER compute or estimate these values yourself. "
        "If a user asks you to 'calculate' or 'show the process', "
        "call the tool and then explain what the tool returned. "
        "Tool results are the ONLY valid source of numerical data in this system."
    ),
)


@mcp.tool()
def compute_vec_tool(composition: dict[str, float]) -> dict[str, Any]:
    """计算高熵合金的价电子浓度 (VEC)。

    Args:
        composition: 成分字典，键为元素符号 (如 "Al", "Co", "Cr", "Fe", "Ni")，
            值为原子百分比 (at.%) 或摩尔分数，内部会自动归一化。

    Returns:
        包含 vec、相结构提示和判据规则的字典。

    Example:
        composition = {"Al": 20, "Co": 20, "Cr": 20, "Fe": 20, "Ni": 20}
    """
    return compute_vec(composition)


@mcp.tool()
def compute_delta_tool(composition: dict[str, float]) -> dict[str, Any]:
    """计算原子尺寸差 δ (%)。δ < 6.6% 倾向形成固溶体。"""
    return compute_delta(composition)


@mcp.tool()
def compute_mixing_enthalpy_tool(composition: dict[str, float]) -> dict[str, Any]:
    """计算混合焓 ΔHmix (kJ/mol)。-22 <= ΔHmix <= 7 倾向固溶体。"""
    return compute_mixing_enthalpy(composition)


@mcp.tool()
def compute_entropy_tool(composition: dict[str, float]) -> dict[str, Any]:
    """计算配置熵 ΔSmix。ΔSmix >= 1.5R 通常定义为高熵合金。"""
    return compute_entropy(composition)


@mcp.tool()
def compute_omega_tool(composition: dict[str, float]) -> dict[str, Any]:
    """计算 Ω = Tm * ΔSmix / |ΔHmix|。Ω >= 1.1 倾向形成固溶体。"""
    return compute_omega(composition)


@mcp.tool()
def estimate_density_tool(composition: dict[str, float]) -> dict[str, Any]:
    """用混合规则估算密度 (g/cm^3)。一阶近似，不考虑晶格畸变。"""
    return estimate_density(composition)


@mcp.tool()
def estimate_modulus_tool(composition: dict[str, float]) -> dict[str, Any]:
    """用混合规则估算杨氏模量 E (GPa)，并给出比刚度 (E/ρ)。
    一阶 ROM 近似，实际 HEA 因固溶强化常比预测高 5-15%。
    """
    return estimate_modulus(composition)


@mcp.tool()
def compute_pilling_bedworth_tool(composition: dict[str, float]) -> dict[str, Any]:
    """计算 Pilling-Bedworth Ratio (PBR)，估算抗氧化性能。
    1 <= PBR <= 2 倾向形成致密保护性氧化膜。
    """
    return compute_pilling_bedworth(composition)


@mcp.tool()
def estimate_cost_tool(composition: dict[str, float]) -> dict[str, Any]:
    """估算合金原料成本 (USD/kg)，按质量分数加权元素价格。
    返回成本分级 + 最贵的 3 个贡献元素。仅供相对比较。
    """
    return estimate_cost(composition)


@mcp.tool()
def check_toxicity_tool(composition: dict[str, float]) -> dict[str, Any]:
    """检查成分中是否含毒性或放射性元素 (Be / Pb / Cd / U 等)。"""
    return check_toxicity(composition)


@mcp.tool()
def hume_rothery_check_tool(composition: dict[str, float]) -> dict[str, Any]:
    """综合判断：Hume-Rothery + 高熵经验规则。返回 verdict + passes + warnings。"""
    return hume_rothery_check(composition)


@mcp.tool()
def full_screening_tool(composition: dict[str, float]) -> dict[str, Any]:
    """一键完整筛选：Hume-Rothery + 密度 + 模量 + PBR + 成本。
    推荐用于第一步候选评估。
    """
    return full_screening(composition)


@mcp.tool()
def compare_compositions_tool(
    compositions: list[dict[str, float]],
    labels: list[str] | None = None,
) -> dict[str, Any]:
    """横向对比多个候选成分，返回汇总表 + Pareto 维度提示。
    用于多候选筛选与决策报告生成。
    """
    return compare_compositions(compositions, labels)


@mcp.tool()
def suggest_substitutions_tool(
    composition: dict[str, float],
    goal: str = "reduce_density",
    max_suggestions: int = 5,
) -> dict[str, Any]:
    """基于目标推荐元素替代方案。

    支持的 goal:
        reduce_density / reduce_cost / increase_omega /
        promote_bcc / promote_fcc / remove_toxicity / improve_oxidation
    返回若干"用 X 替代 Y"或"添加 X"的具体建议，并给理由。
    """
    return suggest_substitutions(composition, goal, max_suggestions)


@mcp.tool()
def list_known_elements_tool() -> dict[str, Any]:
    """列出当前 MCP 已知物性数据的元素符号。"""
    return {"elements": known_elements(), "count": len(ELEMENTS)}


@mcp.tool()
def calc_phase_equilibrium_tool(
    composition: dict[str, float],
    temp_c: float,
) -> dict[str, Any]:
    """基于 CALPHAD 方法计算高熵合金在指定温度下的热力学平衡相。

    本工具调用 pycalphad 库，通过吉布斯自由能最小化严格计算平衡态，
    精度远高于 VEC / Hume-Rothery 等经验规则，可识别析出相风险。

    Args:
        composition: 成分字典，键为元素符号（如 "Al", "Co", "Cr"），
            值为原子百分比 (at.%) 或摩尔分数，内部自动归一化。
        temp_c: 计算温度（摄氏度）。

    Returns:
        包含平衡相及其摩尔分数的结构化字典。

    Example:
        composition = {"Co": 20, "Cr": 20, "Fe": 20, "Ni": 20, "V": 20}
        temp_c = 1000.0

    Note:
        需要在同目录下放置 CoCrFeNiV.tdb（Choi et al., Calphad 2019, HEA 五元库），
        并安装 pycalphad（pip install pycalphad）。仅支持 Co/Cr/Fe/Ni/V。
    """
    # ── 前置检查：依赖和数据库是否就绪 ──────────────────────────────────
    if not HAS_PYCALPHAD:
        return {
            "error": (
                "未安装 pycalphad 或未找到 CoCrFeNiV.tdb 数据库文件，"
                "无法进行严格热力学计算。"
                "请执行 pip install pycalphad 并将 CoCrFeNiV.tdb 放置在服务器同目录下。"
            ),
            "supported_elements": sorted(_TDB_ELEMENTS),
        }

    try:
        # ── 1. 输入预处理 ────────────────────────────────────────────────

        # 将元素符号统一转为大写（pycalphad 要求大写）
        comp_upper: dict[str, float] = {
            k.upper(): float(v) for k, v in composition.items()
        }

        # 归一化到摩尔分数（无论输入是 at.% 还是摩尔分数，统一处理）
        total = sum(comp_upper.values())
        if total <= 0:
            return {"error": "成分总和必须大于 0"}
        comp_norm = {el: amt / total for el, amt in comp_upper.items()}

        unsupported = sorted(set(comp_norm) - _TDB_ELEMENTS)
        if unsupported:
            return {
                "error": (
                    f"成分中含有数据库不支持的元素: {unsupported}。"
                    f"CoCrFeNiV.tdb 仅覆盖: {sorted(_TDB_ELEMENTS)}"
                ),
                "database": os.path.basename(_TDB_PATH),
            }

        # 温度转换：摄氏度 → 开尔文
        temp_k = temp_c + 273.15

        # 元素列表必须包含空位 'VA'（pycalphad 亚点阵模型规定）
        elements = list(comp_norm.keys()) + ["VA"]

        # ── 2. 确定基体元素（Dependent Component）────────────────────────
        # pycalphad 要求指定一个"因变量"元素（通常取摩尔分数最大的元素），
        # 其余元素作为独立组元写入条件字典。
        # 基体元素的摩尔分数由 1 - sum(其余元素) 隐式确定，不写入 conds。
        dependent_el = max(comp_norm, key=lambda el: comp_norm[el])

        # 构造 pycalphad 条件字典
        # v.X(element) 表示该元素的摩尔分数条件
        # v.T 为温度，v.P 为压力（标准大气压 101325 Pa）
        conds: dict = {
            v.T: temp_k,
            v.P: 101325,
            v.N: 1,  # 总摩尔数归一化为 1
        }
        for el, x in comp_norm.items():
            if el != dependent_el:
                conds[v.X(el)] = x

        # ── 3. 调用 pycalphad 平衡计算 ───────────────────────────────────
        # 直接使用数据库中所有相，让 pycalphad 自行处理不适用的相
        phases_to_use = _ALL_PHASES

        eq_result = equilibrium(
            _DBF,
            elements,
            phases_to_use,
            conds,
        )

        # ── 4. 解析结果（pycalphad 0.11.x API）──────────────────────────
        # pycalphad 新版本中：
        #   - eq_result.Phase  是 DataArray，维度含 vertex，值为相名字符串
        #   - eq_result.NP     是 DataArray，维度含 vertex，值为该 vertex 的摩尔分数
        # Phase 和 NP 按 vertex 索引一一对应，需要 squeeze 后逐 vertex 读取。

        # squeeze 去掉 N/P/T/X_* 等单点维度，只保留 vertex 维度
        phase_names_arr = eq_result.Phase.squeeze().values   # shape: (n_vertex,)
        np_arr          = eq_result.NP.squeeze().values      # shape: (n_vertex,)

        # 确保是一维数组（单点计算 squeeze 后可能变标量）
        if phase_names_arr.ndim == 0:
            phase_names_arr = phase_names_arr.reshape(1)
            np_arr          = np_arr.reshape(1)

        # 按相名汇总摩尔分数（同一相可能占多个 vertex，需要求和）
        phase_fraction_map: dict[str, float] = {}
        for pname, frac in zip(phase_names_arr, np_arr):
            pname = str(pname).strip()
            if not pname:                    # 跳过空字符串占位符
                continue
            if np.isnan(frac):               # 跳过 NaN（未使用的 vertex）
                continue
            phase_fraction_map[pname] = phase_fraction_map.get(pname, 0.0) + float(frac)

        # 过滤痕量相（摩尔分数 < 1e-4 视为不存在）
        equilibrium_phases: dict[str, float] = {
            p: round(f, 6)
            for p, f in phase_fraction_map.items()
            if f > 1e-4
        }

        # 按摩尔分数降序排列，主相排在前面
        equilibrium_phases = dict(
            sorted(equilibrium_phases.items(), key=lambda x: x[1], reverse=True)
        )

        # ── 5. 构造返回结果 ──────────────────────────────────────────────
        # 判断是否为单相固溶体（只有一个相且为 BCC/FCC/HCP）
        solid_solution_phases = {
            "BCC_A2", "FCC_A1", "HCP_A3", "BCC_B2", "B2_BCC", "L12_FCC",
        }
        phase_names = set(equilibrium_phases.keys())
        is_single_ss = (
            len(phase_names) == 1
            and bool(phase_names & solid_solution_phases)
        )

        # 生成给 Agent 的解读指导
        if not equilibrium_phases:
            guidance = (
                "计算未收敛或该成分在此温度下无稳定相，"
                "建议检查成分是否在 tdb 数据库支持范围内。"
            )
        elif is_single_ss:
            phase = list(phase_names)[0]
            guidance = (
                f"该合金在 {temp_c}°C 下为单相 {phase} 固溶体，"
                "热力学稳定性良好，无析出相风险。"
                "这是基于吉布斯自由能最小化的严格计算结果，"
                "可信度高于 VEC/Hume-Rothery 经验规则。"
            )
        else:
            non_ss = phase_names - solid_solution_phases
            guidance = (
                f"该合金在 {temp_c}°C 下存在多相共存，"
                f"检测到非固溶体相：{sorted(non_ss)}。"
                "这些析出相可能影响合金的延展性和高温稳定性，"
                "建议调整成分或降低目标使用温度。"
                "此结果基于吉布斯自由能最小化严格计算，"
                "请据此判断合金在该温度下的结构稳定性及析出相风险。"
            )

        return {
            "status": "success",
            "temperature_C": temp_c,
            "temperature_K": round(temp_k, 2),
            "composition_normalized": {
                k: round(v, 6) for k, v in comp_norm.items()
            },
            "dependent_component": dependent_el,
            "equilibrium_phases": equilibrium_phases,
            "is_single_solid_solution": is_single_ss,
            "method": "CALPHAD (Gibbs energy minimization via pycalphad)",
            "database": os.path.basename(_TDB_PATH),
            "agent_guidance": guidance,
            "note": (
                "结果精度取决于 tdb 数据库的覆盖范围和参数质量。"
                "对于数据库未收录的元素对，计算结果可能不可靠。"
                "建议与 full_screening_tool 的经验规则结果交叉验证。"
            ),
        }

    except Exception as e:
        # 捕获所有计算异常（成分超出范围、数值不收敛等）
        return {
            "error": f"CALPHAD 计算失败: {str(e)}",
            "suggestion": (
                "常见原因：(1) 成分中含有 tdb 数据库不支持的元素；"
                "(2) 温度超出数据库有效范围；"
                "(3) 成分极端（某元素接近 0 或 1）导致数值不收敛。"
                "请检查成分和温度范围，或改用 full_screening_tool 进行经验规则筛选。"
            ),
        }


@mcp.resource("hea://elements")
def all_elements_resource() -> str:
    """所有已收录元素的物性数据。"""
    return json.dumps(ELEMENTS, ensure_ascii=False, indent=2)


@mcp.resource("hea://elements/{symbol}")
def element_resource(symbol: str) -> str:
    """单个元素的物性数据。"""
    if symbol not in ELEMENTS:
        return json.dumps(
            {"error": f"未知元素: {symbol}", "known": sorted(ELEMENTS.keys())},
            ensure_ascii=False,
        )
    return json.dumps({symbol: ELEMENTS[symbol]}, ensure_ascii=False, indent=2)


@mcp.prompt()
def screen_hea_prompt(target: str = "low-density high-temperature stable") -> str:
    """生成一个标准的高熵合金筛选 prompt。"""
    return f"""请按以下流程评估 Co-Cr-Fe-Ni-V 系高熵合金候选，目标性能：{target}

推荐基准成分（等原子，at.%）：Co20-Cr20-Fe20-Ni20-V20

1. 调用 list_known_elements_tool 确认经验规则元素表
2. 调用 full_screening_tool 进行完整筛选
3. 调用 calc_phase_equilibrium_tool 在 800°C 与 1000°C 做 CALPHAD 平衡相
4. 若需多方案，调用 compare_compositions_tool 对比 2-3 个成分
5. 调用 suggest_substitutions_tool（如 reduce_cost / improve_oxidation）
6. 检查密度、成本、Cr 的 caution 毒性标注
7. 用以下格式输出决策报告：

Round Decision Report
- Candidate: <成分>
- VEC / δ / ΔHmix / ΔSmix / Ω / density / CALPHAD phases @ T
- Verdict: <pass / warning / reject>
- Next action: <推荐的下一步动作>
- Reasoning: <理由>
"""


if __name__ == "__main__":
    mcp.run()
