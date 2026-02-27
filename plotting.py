import matplotlib.pyplot as plt
from matplotlib.ticker import FormatStrFormatter


class IVPlotter:
    @staticmethod
    def show(voltages, currents, xscale="linear", yscale="linear"):
        if not voltages:
            return
        plt.figure("I-V Curve")
        plt.clf()
        plt.plot(voltages, currents, marker="o")
        plt.xscale(xscale)
        plt.yscale(yscale)
        if xscale == "linear":
            plt.gca().xaxis.set_major_formatter(FormatStrFormatter("%.6g"))
        if yscale == "linear":
            plt.gca().yaxis.set_major_formatter(FormatStrFormatter("%.4e"))
        plt.xlabel("Voltage (V)")
        plt.ylabel("Current (A)")
        plt.title("Keithley I-V Sweep")
        plt.grid(True)
        plt.tight_layout()
        plt.show(block=False)
