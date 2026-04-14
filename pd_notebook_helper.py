from dataclasses import dataclass
from pathlib import Path

import matplotlib.pyplot as plt

from connection import KeithleyConnection
from data_logging import DataLogger


@dataclass
class PDConfig:
    pot_v: float
    pot_t: float
    pot_pulses: int
    read_v: float
    read_t: float
    settle_t: float
    compliance_uA: float
    dep_v: float
    dep_t: float
    dep_pulses: int
    gap_delay_s: float
    cycles: int = 1
    sample_name: str = ""
    operator: str = ""
    notes: str = ""


def build_run_description(config: PDConfig) -> str:
    return (
        "PD mode | instrument_timing=tsp2600 | "
        f"compliance={config.compliance_uA:g} uA | "
        f"pot_v={config.pot_v:g} V | pot_t={config.pot_t:g} s | pot_pulses={config.pot_pulses} | "
        f"read_v={config.read_v:g} V | read_t={config.read_t:g} s | "
        f"stim_to_read_delay_zero_v={config.settle_t:g} s | "
        f"dep_v={config.dep_v:g} V | dep_t={config.dep_t:g} s | dep_pulses={config.dep_pulses} | "
        f"common_delay_after_read={config.gap_delay_s:g} s | cycles={config.cycles}"
    )


def run_pd_experiment(resource_name: str, config: PDConfig, output_path: str | None = None):
    conn = KeithleyConnection()
    logger = DataLogger()
    logger.set_metadata(config.sample_name, config.operator, config.notes)
    logger.set_run_description(build_run_description(config))

    resolved_output = Path(output_path) if output_path else None
    if resolved_output:
        logger.set_output_file(str(resolved_output), reset_file=True)

    try:
        identity = conn.connect(resource_name)
        conn.set_current_compliance_ua(config.compliance_uA)
        rows = conn.run_tsp_pd_sequence(
            pot_v=config.pot_v,
            pot_t=config.pot_t,
            pot_pulses=config.pot_pulses,
            read_v=config.read_v,
            read_t=config.read_t,
            settle_t=config.settle_t,
            dep_v=config.dep_v,
            dep_t=config.dep_t,
            dep_pulses=config.dep_pulses,
            gap_delay_s=config.gap_delay_s,
            cycles=config.cycles,
        )
        for row in rows:
            logger.add(row["voltage"], row["current"], auto_save=False)
            last = logger.rows[-1]
            last.elapsed_s = row["elapsed_s"]
            last.cycle_id = row["cycle_id"]
            last.plot_mode = "pd"
        if resolved_output:
            logger.save_csv(str(resolved_output))
        return {
            "identity": identity,
            "rows": rows,
            "output_path": str(resolved_output) if resolved_output else None,
        }
    finally:
        conn.close()


def plot_pd_results(result, log_current: bool = True):
    rows = result["rows"]
    if not rows:
        raise RuntimeError("No PD rows to plot")

    pulse_no = [row["pulse_no"] for row in rows]
    current = [abs(row["current"]) if log_current else row["current"] for row in rows]
    phases = [row["phase"] for row in rows]

    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = ["tab:blue" if phase == "pot" else "tab:orange" for phase in phases]
    ax.scatter(pulse_no, current, c=colors, s=28)
    ax.plot(pulse_no, current, color="0.75", linewidth=0.8, zorder=0)
    ax.set_xlabel("Pulse Number")
    ax.set_ylabel("|Current| (A)" if log_current else "Current (A)")
    ax.set_title("PD Read Current vs Pulse Number")
    if log_current:
        ax.set_yscale("log")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig, ax
