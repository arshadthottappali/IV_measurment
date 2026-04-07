import matplotlib.pyplot as plt
import math
from matplotlib.ticker import FormatStrFormatter


class IVPlotter:
    @staticmethod
    def prepare_log_y_data(x_data, y_data):
        """Prepares data for log scale plotting on y-axis by taking abs(y) and removing zeros."""
        new_x, new_y = [], []
        for x, y in zip(x_data, y_data):
            if not isinstance(y, (int, float)) or not math.isfinite(y):
                continue
            ay = abs(y)
            if ay == 0:
                continue
            new_x.append(x)
            new_y.append(ay)
        return new_x, new_y

    @staticmethod
    def show(
        voltages=None,
        currents=None,
        xscale="linear",
        yscale="linear",
        title="Keithley I-V Sweep",
        cycle_series=None,
        xlabel="Voltage (V)",
        ylabel="Current (A)",
    ):
        has_data = bool(cycle_series) or bool(voltages)
        if not has_data:
            return
        plt.figure("I-V Curve")
        plt.clf()
        ax = plt.gca()
        if cycle_series:
            cmap = plt.get_cmap("tab20")
            for entry in cycle_series:
                xs = entry.get("x", [])
                ys = entry.get("y", [])
                if not xs:
                    continue
                color_index = entry.get("color_index", 0)
                label = entry.get("label")
                ax.plot(xs, ys, marker="o", linestyle="-", color=cmap(color_index % 20), label=label)
            if any(entry.get("label") for entry in cycle_series):
                ax.legend(loc="best")
        else:
            ax.plot(voltages, currents, marker="o")
        plt.xscale(xscale)
        plt.yscale(yscale)
        if xscale == "linear":
            plt.gca().xaxis.set_major_formatter(FormatStrFormatter("%.6g"))
        if yscale == "linear":
            plt.gca().yaxis.set_major_formatter(FormatStrFormatter("%.4e"))
        plt.xlabel(xlabel)
        plt.ylabel(ylabel)
        plt.title(title)
        plt.grid(True)
        plt.tight_layout()
        plt.show(block=False)

    @staticmethod
    def show_time_series(
        times,
        voltages,
        currents,
        title="WRER Measurement",
        current_yscale="linear",
        xlim=None,
        current_times=None,
        xlabel="Time (s)",
        current_linestyle="-",
        current_use_abs=False,
        current_ylabel="Current (A)",
        voltage_linestyle="-",
    ):
        if not times:
            return
        plt.figure("Time Series")
        plt.clf()
        ax_v = plt.subplot(211)
        ax_i = plt.subplot(212, sharex=ax_v)
        ax_v.plot(times, voltages, marker="o", linestyle=voltage_linestyle)
        current_x = current_times if current_times is not None else times
        if current_yscale == "log":
            t_i, i_i = IVPlotter.prepare_log_y_data(current_x, currents)
            ax_i.set_yscale("log")
            ax_i.plot(t_i, i_i, marker="o", linestyle=current_linestyle)
        else:
            ax_i.set_yscale("linear")
            plot_y = [abs(y) if current_use_abs and isinstance(y, (int, float)) and math.isfinite(y) else y for y in currents]
            ax_i.plot(current_x, plot_y, marker="o", linestyle=current_linestyle)
        ax_v.set_ylabel("Voltage (V)")
        ax_i.set_ylabel(current_ylabel)
        ax_i.set_xlabel(xlabel)
        ax_v.set_title(title)
        ax_v.grid(True)
        ax_i.grid(True)
        if xlim is not None and len(xlim) == 2:
            ax_v.set_xlim(xlim[0], xlim[1])
        plt.tight_layout()
        plt.show(block=False)
