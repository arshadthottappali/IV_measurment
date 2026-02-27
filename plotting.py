import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter


class IVPlotter:
    @staticmethod
    def show(voltages=None, currents=None, xscale="linear", yscale="linear", title="Keithley I-V Sweep", cycle_series=None):
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
        plt.xlabel("Voltage (V)")
        plt.ylabel("Current (A)")
        plt.title(title)
        plt.grid(True)
        plt.tight_layout()
        plt.show(block=False)

    @staticmethod
    def show_time_series(times, voltages, currents, title="WRER Measurement", current_yscale="linear"):
        if not times:
            return
        plt.figure("Time Series")
        plt.clf()
        ax_v = plt.subplot(211)
        ax_i = plt.subplot(212, sharex=ax_v)
        ax_v.plot(times, voltages, marker="o", linestyle="-")
        if current_yscale == "log":
            t_i = []
            i_i = []
            for t, i in zip(times, currents):
                ai = abs(i)
                if ai == 0:
                    continue
                t_i.append(t)
                i_i.append(ai)
            ax_i.set_yscale("log")
            ax_i.plot(t_i, i_i, marker="o", linestyle="-")
        else:
            ax_i.set_yscale("linear")
            ax_i.plot(times, currents, marker="o", linestyle="-")
        ax_v.set_ylabel("Voltage (V)")
        ax_i.set_ylabel("Current (A)")
        ax_i.set_xlabel("Time (s)")
        ax_v.set_title(title)
        ax_v.grid(True)
        ax_i.grid(True)
        plt.tight_layout()
        plt.show(block=False)
