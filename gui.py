import os
import re
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FormatStrFormatter

from connection import KeithleyConnection
from data_logging import DataLogger
from plotting import IVPlotter


class KeithleyUI:
    SAFE_VOLTAGE_LIMIT = 5.0

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Keithley Control Panel")
        self.root.minsize(1100, 680)

        self.connection = KeithleyConnection()
        self.logger = DataLogger()

        self.stop_flag = False
        self.connected = False
        self.live_running = False
        self.sweep_running = False
        self.autosave_enabled = False
        self.live_after_id = None
        self.sweep_after_id = None
        self._sweep_values = []
        self._sweep_index = 0
        self._sweep_start_time = None
        self.last_voltage = 0.0
        self.max_live_points = 5000

        self.status_text = tk.StringVar(value="Disconnected")
        self.connection_text = tk.StringVar(value="Disconnected")
        self.device_text = tk.StringVar(value="Device: None")
        self.instrument_info_text = tk.StringVar(value="Instrument: Not connected")
        self.autosave_text = tk.StringVar(value="Auto-save: OFF")
        self.save_path_text = tk.StringVar(value="Save path: not selected")
        self.preferred_save_dir = os.getcwd()
        self.save_dir_text = tk.StringVar(value=f"Save folder: {self.preferred_save_dir}")
        self.progress_text = tk.StringVar(value="Sweep progress: 0/0")
        self.eta_text = tk.StringVar(value="Elapsed: 00:00 | ETA: --:--")

        self._build_ui()
        self._bind_shortcuts()
        self._update_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        self.root.columnconfigure(0, weight=0)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)

        controls_host = ttk.Frame(self.root, padding=(12, 12, 8, 12), width=430)
        controls_host.grid(row=0, column=0, sticky="nsw")
        controls_host.columnconfigure(0, weight=1)
        controls_host.rowconfigure(0, weight=1)

        controls_tabs = ttk.Notebook(controls_host)
        controls_tabs.grid(row=0, column=0, sticky="nsew")

        setup_tab = ttk.Frame(controls_tabs, padding=8)
        control_tab = ttk.Frame(controls_tabs, padding=8)
        sweep_tab = ttk.Frame(controls_tabs, padding=8)
        for tab in (setup_tab, control_tab, sweep_tab):
            tab.columnconfigure(0, weight=1)

        controls_tabs.add(setup_tab, text="Setup")
        controls_tabs.add(control_tab, text="Control")
        controls_tabs.add(sweep_tab, text="Sweep")

        plot_panel = ttk.Frame(self.root, padding=(0, 12, 12, 12))
        plot_panel.grid(row=0, column=1, sticky="nsew")
        plot_panel.columnconfigure(0, weight=1)
        plot_panel.rowconfigure(1, weight=1)

        self._build_connection_frame(setup_tab)
        self._build_data_frame(setup_tab)
        self._build_manual_frame(control_tab)
        self._build_live_frame(control_tab)
        self._build_sweep_frame(sweep_tab)

        axis_bar = ttk.Frame(plot_panel)
        axis_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(axis_bar, text="X Axis").grid(row=0, column=0, sticky="w")
        self.x_axis_scale_combo = ttk.Combobox(axis_bar, values=["Linear", "Log"], state="readonly", width=10)
        self.x_axis_scale_combo.set("Linear")
        self.x_axis_scale_combo.grid(row=0, column=1, sticky="w", padx=(6, 16))
        self.x_axis_scale_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_embedded_plot())
        ttk.Label(axis_bar, text="Y Axis").grid(row=0, column=2, sticky="w")
        self.y_axis_scale_combo = ttk.Combobox(axis_bar, values=["Linear", "Log"], state="readonly", width=10)
        self.y_axis_scale_combo.set("Linear")
        self.y_axis_scale_combo.grid(row=0, column=3, sticky="w", padx=(6, 0))
        self.y_axis_scale_combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_embedded_plot())

        self.figure = Figure(figsize=(7.5, 5), dpi=100)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_title("Live I-V Data")
        self.ax.set_xlabel("Voltage (V)")
        self.ax.set_ylabel("Current (A)")
        self.ax.grid(True)
        self.canvas = FigureCanvasTkAgg(self.figure, master=plot_panel)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew")

        status_bar = ttk.Frame(self.root, padding=(12, 6))
        status_bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        status_bar.columnconfigure(0, weight=1)
        status_bar.columnconfigure(1, weight=1)
        status_bar.columnconfigure(2, weight=1)
        ttk.Label(status_bar, textvariable=self.connection_text).grid(row=0, column=0, sticky="w")
        ttk.Label(status_bar, textvariable=self.device_text).grid(row=0, column=1, sticky="w")
        ttk.Label(status_bar, textvariable=self.status_text).grid(row=0, column=2, sticky="e")

    def _build_connection_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Connection", padding=8)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(0, weight=1)

        self.listbox = tk.Listbox(frame, width=44, height=4)
        self.listbox.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))

        ttk.Button(frame, text="Detect Devices", command=self.detect).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.connect_btn = ttk.Button(frame, text="Connect", command=self.connect)
        self.connect_btn.grid(row=1, column=1, sticky="ew")
        ttk.Label(frame, textvariable=self.instrument_info_text, wraplength=360).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0)
        )

    def _build_manual_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Manual Control", padding=8)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Voltage (V)").grid(row=0, column=0, sticky="w")
        self.voltage_entry = ttk.Entry(frame, width=14)
        self.voltage_entry.insert(0, "0")
        self.voltage_entry.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        ttk.Label(frame, text="Compliance (uA)").grid(row=1, column=0, sticky="w")
        self.compliance_entry = ttk.Entry(frame, width=14)
        self.compliance_entry.insert(0, "1")
        self.compliance_entry.grid(row=1, column=1, sticky="ew", padx=(6, 0))

        self.apply_voltage_btn = ttk.Button(frame, text="Apply Voltage", command=self.set_voltage)
        self.apply_voltage_btn.grid(row=2, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.measure_btn = ttk.Button(frame, text="Measure Current", command=self.measure)
        self.measure_btn.grid(row=2, column=1, sticky="ew", pady=(8, 0))

        self.apply_compliance_btn = ttk.Button(
            frame, text="Apply Compliance", command=self.apply_compliance
        )
        self.apply_compliance_btn.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(
            frame,
            text=(
                f"Safety limits: Voltage +/-{self.connection.MAX_ABS_VOLTAGE:g} V, "
                f"Compliance <= {self.connection.MAX_COMPLIANCE_UA:g} uA"
            ),
            wraplength=360,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def _build_live_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Live", padding=8)
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Sample Rate (ms)").grid(row=0, column=0, sticky="w")
        self.sample_rate = ttk.Combobox(frame, values=["100", "250", "500", "1000"], width=10, state="readonly")
        self.sample_rate.set("500")
        self.sample_rate.grid(row=0, column=1, sticky="w")

        self.start_live_btn = ttk.Button(frame, text="Start Live", command=self.start_live_measurement)
        self.start_live_btn.grid(row=1, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.stop_btn = ttk.Button(frame, text="STOP", command=self.stop)
        self.stop_btn.grid(row=1, column=1, sticky="ew", pady=(8, 0))

    def _build_sweep_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Sweep", padding=8)
        frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Preset").grid(row=0, column=0, sticky="w")
        self.preset_combo = ttk.Combobox(
            frame,
            state="readonly",
            values=[
                "Custom",
                "0 to 1 by 0.1",
                "0 to 5 by 0.5",
                "-1 to 1 by 0.1",
            ],
        )
        self.preset_combo.set("Custom")
        self.preset_combo.grid(row=0, column=1, sticky="ew")
        self.preset_combo.bind("<<ComboboxSelected>>", self._apply_preset)

        ttk.Label(frame, text="Start (V)").grid(row=1, column=0, sticky="w")
        self.sweep_start_entry = ttk.Entry(frame, width=12)
        self.sweep_start_entry.insert(0, "0")
        self.sweep_start_entry.grid(row=1, column=1, sticky="ew")

        ttk.Label(frame, text="Stop (V)").grid(row=2, column=0, sticky="w")
        self.sweep_stop_entry = ttk.Entry(frame, width=12)
        self.sweep_stop_entry.insert(0, "1")
        self.sweep_stop_entry.grid(row=2, column=1, sticky="ew")

        ttk.Label(frame, text="Step (V)").grid(row=3, column=0, sticky="w")
        self.sweep_step_entry = ttk.Entry(frame, width=12)
        self.sweep_step_entry.insert(0, "0.1")
        self.sweep_step_entry.grid(row=3, column=1, sticky="ew")

        ttk.Label(frame, text="Delay (s)").grid(row=4, column=0, sticky="w")
        self.sweep_delay_entry = ttk.Entry(frame, width=12)
        self.sweep_delay_entry.insert(0, "1")
        self.sweep_delay_entry.grid(row=4, column=1, sticky="ew")

        ttk.Label(frame, text="Mode").grid(row=5, column=0, sticky="w")
        self.sweep_mode_combo = ttk.Combobox(
            frame,
            state="readonly",
            values=["One-way", "Simple Cycle (0->+V->0->-V->0)"],
        )
        self.sweep_mode_combo.set("Simple Cycle (0->+V->0->-V->0)")
        self.sweep_mode_combo.grid(row=5, column=1, sticky="ew")

        ttk.Label(frame, text="Cycle Peak V").grid(row=6, column=0, sticky="w")
        self.cycle_peak_entry = ttk.Entry(frame, width=12)
        self.cycle_peak_entry.insert(0, "1")
        self.cycle_peak_entry.grid(row=6, column=1, sticky="ew")

        ttk.Label(frame, text="Cycles").grid(row=7, column=0, sticky="w")
        self.sweep_cycles_entry = ttk.Entry(frame, width=12)
        self.sweep_cycles_entry.insert(0, "1")
        self.sweep_cycles_entry.grid(row=7, column=1, sticky="ew")

        self.run_sweep_btn = ttk.Button(frame, text="Run Sweep (F5)", command=self.run_sweep_from_inputs)
        self.run_sweep_btn.grid(row=8, column=0, sticky="ew", pady=(8, 4), padx=(0, 4))
        self.sweep_stop_btn = ttk.Button(frame, text="Stop Sweep", command=self.stop)
        self.sweep_stop_btn.grid(row=8, column=1, sticky="ew", pady=(8, 4))

        self.sweep_progress = ttk.Progressbar(frame, mode="determinate", maximum=100, value=0)
        self.sweep_progress.grid(row=9, column=0, columnspan=2, sticky="ew")
        ttk.Label(frame, textvariable=self.progress_text).grid(row=10, column=0, columnspan=2, sticky="w")
        ttk.Label(frame, textvariable=self.eta_text).grid(row=11, column=0, columnspan=2, sticky="w")

    def _build_data_frame(self, parent):
        frame = ttk.LabelFrame(parent, text="Data", padding=8)
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Sample").grid(row=0, column=0, sticky="w")
        self.sample_entry = ttk.Entry(frame)
        self.sample_entry.grid(row=0, column=1, sticky="ew")

        ttk.Label(frame, text="Operator").grid(row=1, column=0, sticky="w")
        self.operator_entry = ttk.Entry(frame)
        self.operator_entry.grid(row=1, column=1, sticky="ew")

        ttk.Label(frame, text="Notes").grid(row=2, column=0, sticky="w")
        self.notes_entry = ttk.Entry(frame)
        self.notes_entry.grid(row=2, column=1, sticky="ew")

        ttk.Label(frame, textvariable=self.autosave_text).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(frame, textvariable=self.save_path_text, wraplength=340).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(frame, textvariable=self.save_dir_text, wraplength=340).grid(
            row=5, column=0, columnspan=2, sticky="w"
        )

        self.plot_btn = ttk.Button(frame, text="Plot I-V", command=self.plot_iv_popup)
        self.plot_btn.grid(row=6, column=0, sticky="ew", pady=(8, 0), padx=(0, 4))
        self.save_btn = ttk.Button(frame, text="Save CSV (Ctrl+S)", command=self.save_csv_manual)
        self.save_btn.grid(row=6, column=1, sticky="ew", pady=(8, 0))

        self.choose_folder_btn = ttk.Button(frame, text="Choose Save Folder", command=self.choose_save_folder)
        self.choose_folder_btn.grid(row=7, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.load_btn = ttk.Button(frame, text="Load CSV", command=self.load_csv_data)
        self.load_btn.grid(row=7, column=1, sticky="ew", pady=(6, 0))
        self.open_folder_btn = ttk.Button(frame, text="Open Save Folder", command=self.open_save_folder)
        self.open_folder_btn.grid(row=8, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))

        self.clear_data_btn = ttk.Button(frame, text="Clear Data", command=self.clear_data)
        self.clear_data_btn.grid(row=8, column=1, sticky="ew", pady=(6, 0))

    def _bind_shortcuts(self):
        self.root.bind("<Control-s>", lambda _event: self.save_csv_manual())
        self.root.bind("<F5>", lambda _event: self.run_sweep_from_inputs())
        self.root.bind("<Escape>", lambda _event: self.stop())

    def _update_button_states(self):
        connected_state = "normal" if self.connected else "disabled"
        idle_connected_state = "normal" if self.connected and not (self.live_running or self.sweep_running) else "disabled"
        live_state = "normal" if self.connected and not self.live_running and not self.sweep_running else "disabled"
        stop_state = "normal" if self.live_running or self.sweep_running else "disabled"

        self.apply_voltage_btn.config(state=idle_connected_state)
        self.measure_btn.config(state=idle_connected_state)
        self.apply_compliance_btn.config(state=idle_connected_state)
        self.start_live_btn.config(state=live_state)
        self.run_sweep_btn.config(state=live_state)
        self.sweep_stop_btn.config(state=stop_state)
        self.stop_btn.config(state=stop_state)
        self.plot_btn.config(state="normal" if self.logger.rows else "disabled")
        self.save_btn.config(state="normal" if self.logger.rows else "disabled")
        self.choose_folder_btn.config(state="normal")
        self.load_btn.config(state="normal")
        self.open_folder_btn.config(state="normal")
        self.clear_data_btn.config(state="normal" if self.logger.rows else "disabled")
        self.connect_btn.config(state="normal")
        self.sample_rate.config(state="readonly" if connected_state == "normal" else "disabled")

    def detect(self):
        self.listbox.delete(0, tk.END)
        try:
            devices = self.connection.list_devices()
            for device in devices:
                self.listbox.insert(tk.END, device)
            if not devices:
                messagebox.showinfo("Detect", "No VISA devices found")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def connect(self):
        selection = self.listbox.curselection()
        if not selection:
            messagebox.showerror("Error", "Select a device from the list first")
            return
        try:
            resource = self.listbox.get(selection[0])
            idn = self.connection.connect(resource)
            self.connected = True
            self.connection_text.set("Connected")
            self.device_text.set(f"Device: {self.connection.last_resource}")
            self.status_text.set(f"Ready ({self.connection.mode.upper()}) {idn}")
            self.instrument_info_text.set(f"Instrument: {idn} | Mode: {self.connection.mode.upper()}")
            self._update_button_states()
        except Exception as e:
            self.connected = False
            self.connection_text.set("Disconnected")
            self.status_text.set("Connection failed")
            self.instrument_info_text.set("Instrument: Not connected")
            messagebox.showerror("Error", str(e))

    def apply_compliance(self):
        try:
            compliance_uA = float(self.compliance_entry.get())
            if compliance_uA <= 0:
                raise ValueError
            if compliance_uA > self.connection.MAX_COMPLIANCE_UA:
                messagebox.showerror(
                    "Error",
                    f"Compliance must be <= {self.connection.MAX_COMPLIANCE_UA:g} uA",
                )
                return
            self.connection.set_current_compliance_ua(compliance_uA)
            self.status_text.set(f"Compliance set: {compliance_uA:g} uA")
        except ValueError:
            messagebox.showerror("Error", "Compliance must be a positive number (uA)")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def set_voltage(self):
        try:
            voltage = float(self.voltage_entry.get())
            if abs(voltage) > self.connection.MAX_ABS_VOLTAGE:
                messagebox.showerror(
                    "Error",
                    f"Voltage out of instrument-safe UI range (+/-{self.connection.MAX_ABS_VOLTAGE:g} V)",
                )
                return
            if abs(voltage) > self.SAFE_VOLTAGE_LIMIT:
                proceed = messagebox.askyesno(
                    "High Voltage Warning",
                    f"You entered {voltage:g} V.\nThis exceeds the safety threshold of {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue?",
                )
                if not proceed:
                    return
            self.connection.set_voltage(voltage)
            self.last_voltage = voltage
            self.status_text.set(f"Voltage applied: {voltage:g} V")
        except ValueError:
            messagebox.showerror("Error", "Invalid voltage value")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def measure(self):
        try:
            self._sync_metadata()
            current = self.connection.measure_current()
            self.logger.add(self.last_voltage, current, auto_save=self.autosave_enabled and self.logger.output_file is not None)
            self.status_text.set(f"I={current:.6e} A @ V={self.last_voltage:.6g} V")
            self._refresh_embedded_plot()
            self._update_button_states()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def start_live_measurement(self):
        self.live_running = True
        self.status_text.set("Live measurement running")
        self._update_button_states()
        self._run_live_measurement_step()

    def _run_live_measurement_step(self):
        if not self.live_running:
            self.live_after_id = None
            return
        try:
            self._sync_metadata()
            current = self.connection.measure_current()
            self.logger.add(self.last_voltage, current, auto_save=False)
            if len(self.logger.rows) > self.max_live_points:
                self.logger.rows = self.logger.rows[-self.max_live_points:]
            self.status_text.set(f"Live: I={current:.6e} A @ V={self.last_voltage:.6g} V")
            self._refresh_embedded_plot()
            interval_ms = int(self.sample_rate.get())
            self.live_after_id = self.root.after(interval_ms, self._run_live_measurement_step)
            self._update_button_states()
        except Exception as e:
            self.live_running = False
            self.live_after_id = None
            self._update_button_states()
            messagebox.showerror("Error", str(e))

    def run_sweep_from_inputs(self):
        if self.live_running:
            messagebox.showerror("Error", "Stop live mode before running a sweep")
            return
        sweep_mode = self.sweep_mode_combo.get()
        try:
            step = float(self.sweep_step_entry.get())
            delay = float(self.sweep_delay_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Step and delay must be valid numbers")
            return

        if step == 0:
            messagebox.showerror("Error", "Sweep step cannot be zero")
            return
        if delay < 0.001:
            messagebox.showerror("Error", "Delay must be at least 0.001 seconds (1 ms)")
            return

        if sweep_mode == "Simple Cycle (0->+V->0->-V->0)":
            try:
                cycles = int(self.sweep_cycles_entry.get())
                cycle_peak = float(self.cycle_peak_entry.get())
            except ValueError:
                messagebox.showerror("Error", "Cycle peak must be number and cycles must be integer")
                return
            if cycles < 1:
                messagebox.showerror("Error", "Cycles must be >= 1")
                return
            if cycle_peak < 0:
                messagebox.showerror("Error", "Cycle Peak V must be >= 0")
                return
            if abs(cycle_peak) > self.connection.MAX_ABS_VOLTAGE:
                messagebox.showerror(
                    "Error",
                    f"Cycle peak must be within +/-{self.connection.MAX_ABS_VOLTAGE:g} V",
                )
                return
            if abs(cycle_peak) > self.SAFE_VOLTAGE_LIMIT:
                proceed = messagebox.askyesno(
                    "High Voltage Warning",
                    f"Cycle peak includes voltage above {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue?",
                )
                if not proceed:
                    return
            values = self._build_simple_cycle_values(cycle_peak, step, cycles)
        else:
            try:
                start = float(self.sweep_start_entry.get())
                stop = float(self.sweep_stop_entry.get())
            except ValueError:
                messagebox.showerror("Error", "Start/Stop must be valid numbers for One-way mode")
                return
            if abs(start) > self.connection.MAX_ABS_VOLTAGE or abs(stop) > self.connection.MAX_ABS_VOLTAGE:
                messagebox.showerror(
                    "Error",
                    f"Sweep voltage must be within +/-{self.connection.MAX_ABS_VOLTAGE:g} V",
                )
                return
            if max(abs(start), abs(stop)) > self.SAFE_VOLTAGE_LIMIT:
                proceed = messagebox.askyesno(
                    "High Voltage Warning",
                    f"Sweep range includes voltage above {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue?",
                )
                if not proceed:
                    return
            values = self._build_sweep_values(start, stop, step)
        if not values:
            messagebox.showerror("Error", "Sweep range/step produced no points")
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialdir=self.preferred_save_dir,
            initialfile=self._default_data_filename(),
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            title="Select save file before starting sweep",
        )
        if not file_path:
            return

        try:
            self._sync_metadata()
            self.logger.clear()
            reset_file = True
            if os.path.exists(file_path):
                choice = messagebox.askyesnocancel(
                    "File Exists",
                    "Selected file already exists.\n\nYes: Append this run\nNo: Overwrite file\nCancel: Abort run",
                )
                if choice is None:
                    return
                reset_file = not choice
            self.logger.set_output_file(file_path, reset_file=reset_file)
            self.autosave_enabled = True
            mode_text = "append" if not reset_file else "overwrite/new"
            self.autosave_text.set(f"Auto-save: ON (each scan point, {mode_text})")
            self.save_path_text.set(f"Save path: {file_path}")
            self.preferred_save_dir = os.path.dirname(file_path) or self.preferred_save_dir
            self.save_dir_text.set(f"Save folder: {self.preferred_save_dir}")
            self.sweep_delay_ms = int(delay * 1000)
            self.sweep_example(values)
            self._update_button_states()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def sweep_example(self, voltages):
        self.stop_flag = False
        self.sweep_running = True
        self._sweep_values = list(voltages)
        self._sweep_index = 0
        self._sweep_start_time = datetime.now()
        self.sweep_progress.config(maximum=len(self._sweep_values), value=0)
        self.progress_text.set(f"Sweep progress: 0/{len(self._sweep_values)}")
        self.eta_text.set("Elapsed: 00:00 | ETA: --:--")
        self._update_button_states()
        try:
            self.connection.zero_output()
            self._run_next_sweep_step()
        except Exception as e:
            self._finish_sweep()
            messagebox.showerror("Error", str(e))

    def _run_next_sweep_step(self):
        if self.stop_flag or self._sweep_index >= len(self._sweep_values):
            self._finish_sweep()
            return

        voltage = self._sweep_values[self._sweep_index]
        try:
            self.connection.set_voltage(voltage)
            current = self.connection.measure_current()
            self.last_voltage = voltage
            self._sync_metadata()
            self.logger.add(voltage, current, auto_save=self.autosave_enabled)
            self.status_text.set(f"Sweep: point {self._sweep_index + 1}/{len(self._sweep_values)} | I={current:.6e} A")
            self._sweep_index += 1
            self.sweep_progress.config(value=self._sweep_index)
            self.progress_text.set(f"Sweep progress: {self._sweep_index}/{len(self._sweep_values)}")
            self._update_eta()
            self._refresh_embedded_plot()
            self.sweep_after_id = self.root.after(self.sweep_delay_ms, self._run_next_sweep_step)
        except Exception as e:
            self._finish_sweep()
            messagebox.showerror("Error", str(e))

    def stop(self):
        self.stop_flag = True
        self.live_running = False
        if self.sweep_after_id:
            self.root.after_cancel(self.sweep_after_id)
            self.sweep_after_id = None
        if self.live_after_id:
            self.root.after_cancel(self.live_after_id)
            self.live_after_id = None
        self._finish_sweep(reset_progress=False)
        try:
            self.connection.zero_output()
        except Exception:
            pass
        self.status_text.set("Stopped. Output zeroed.")
        self._update_button_states()

    def _finish_sweep(self, reset_progress=True):
        self.sweep_running = False
        self.sweep_after_id = None
        try:
            if self.connected:
                self.connection.zero_output()
        except Exception:
            pass
        if reset_progress and self._sweep_values:
            self.progress_text.set(f"Sweep progress: {self._sweep_index}/{len(self._sweep_values)}")
        self._update_button_states()

    def save_csv_manual(self):
        if not self.logger.rows:
            messagebox.showinfo("Save CSV", "No data to save")
            return
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialdir=self.preferred_save_dir,
            initialfile=self._default_data_filename(prefix="iv_manual"),
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            title="Save I-V Data",
        )
        if not file_path:
            return
        try:
            self.logger.save_csv(file_path)
            self.logger.set_output_file(file_path, reset_file=False)
            self.save_path_text.set(f"Save path: {file_path}")
            self.preferred_save_dir = os.path.dirname(file_path) or self.preferred_save_dir
            self.save_dir_text.set(f"Save folder: {self.preferred_save_dir}")
            self._update_button_states()
            messagebox.showinfo("Saved", f"Saved data to:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def choose_save_folder(self):
        folder = filedialog.askdirectory(
            initialdir=self.preferred_save_dir,
            title="Choose Default Save Folder",
        )
        if not folder:
            return
        self.preferred_save_dir = folder
        self.save_dir_text.set(f"Save folder: {self.preferred_save_dir}")
        if self.logger.output_file:
            self.save_path_text.set(f"Save path: {self.logger.output_file}")

    def open_save_folder(self):
        try:
            if self.logger.output_file:
                os.startfile(str(self.logger.output_file.parent))
            else:
                os.startfile(self.preferred_save_dir)
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def load_csv_data(self):
        file_path = filedialog.askopenfilename(
            initialdir=self.preferred_save_dir,
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            title="Load Existing Measurement CSV",
        )
        if not file_path:
            return
        try:
            self.logger.load_csv(file_path)
            self.save_path_text.set(f"Save path: {file_path}")
            self.preferred_save_dir = os.path.dirname(file_path) or self.preferred_save_dir
            self.save_dir_text.set(f"Save folder: {self.preferred_save_dir}")
            self.autosave_text.set("Auto-save: OFF (loaded data)")
            self._refresh_embedded_plot()
            self._update_button_states()
            messagebox.showinfo("Loaded", f"Loaded data from:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def clear_data(self):
        self.logger.clear()
        self._refresh_embedded_plot()
        self._update_button_states()
        self.status_text.set("Data cleared")

    def plot_iv_popup(self):
        voltages = [row.voltage for row in self.logger.rows]
        currents = [row.current for row in self.logger.rows]
        if not voltages:
            messagebox.showinfo("Plot", "No data to plot")
            return
        xscale, yscale = self._get_axis_scales()
        px, py = self._prepare_plot_data(voltages, currents, xscale, yscale)
        if not px:
            messagebox.showinfo("Plot", "No plottable points for selected log axis (needs positive values)")
            return
        IVPlotter.show(px, py, xscale=xscale, yscale=yscale)

    def _refresh_embedded_plot(self):
        voltages = [row.voltage for row in self.logger.rows]
        currents = [row.current for row in self.logger.rows]
        xscale, yscale = self._get_axis_scales()
        px, py = self._prepare_plot_data(voltages, currents, xscale, yscale)
        self.ax.clear()
        self.ax.grid(True)
        self.ax.set_xscale(xscale)
        self.ax.set_yscale(yscale)
        if xscale == "linear":
            self.ax.xaxis.set_major_formatter(FormatStrFormatter("%.6g"))
        if yscale == "linear":
            self.ax.yaxis.set_major_formatter(FormatStrFormatter("%.4e"))
        self.ax.set_xlabel("Voltage (V)")
        self.ax.set_ylabel("Current (A)")
        self.ax.set_title("Live I-V Data")
        if px:
            self.ax.plot(px, py, marker="o", linestyle="-")
        self.canvas.draw_idle()

    def _get_axis_scales(self):
        xscale = "log" if self.x_axis_scale_combo.get().lower() == "log" else "linear"
        yscale = "log" if self.y_axis_scale_combo.get().lower() == "log" else "linear"
        return xscale, yscale

    @staticmethod
    def _prepare_plot_data(voltages, currents, xscale, yscale):
        if xscale == "linear" and yscale == "linear":
            return voltages, currents
        px = []
        py = []
        for v, i in zip(voltages, currents):
            if xscale == "log" and v <= 0:
                continue
            if yscale == "log" and i <= 0:
                continue
            px.append(v)
            py.append(i)
        return px, py

    def _sync_metadata(self):
        self.logger.set_metadata(
            sample_name=self.sample_entry.get(),
            operator=self.operator_entry.get(),
            notes=self.notes_entry.get(),
        )

    def _update_eta(self):
        if not self._sweep_start_time or self._sweep_index == 0:
            self.eta_text.set("Elapsed: 00:00 | ETA: --:--")
            return
        elapsed = datetime.now() - self._sweep_start_time
        elapsed_sec = int(elapsed.total_seconds())
        avg_per_point = elapsed.total_seconds() / self._sweep_index
        remaining_points = max(0, len(self._sweep_values) - self._sweep_index)
        remaining_sec = int(avg_per_point * remaining_points)
        self.eta_text.set(
            f"Elapsed: {elapsed_sec // 60:02d}:{elapsed_sec % 60:02d} | ETA: {remaining_sec // 60:02d}:{remaining_sec % 60:02d}"
        )

    def _apply_preset(self, _event=None):
        preset = self.preset_combo.get()
        presets = {
            "0 to 1 by 0.1": ("0", "1", "0.1"),
            "0 to 5 by 0.5": ("0", "5", "0.5"),
            "-1 to 1 by 0.1": ("-1", "1", "0.1"),
        }
        if preset not in presets:
            return
        start, stop, step = presets[preset]
        self.sweep_start_entry.delete(0, tk.END)
        self.sweep_stop_entry.delete(0, tk.END)
        self.sweep_step_entry.delete(0, tk.END)
        self.sweep_start_entry.insert(0, start)
        self.sweep_stop_entry.insert(0, stop)
        self.sweep_step_entry.insert(0, step)

    def on_close(self):
        self.stop()
        self.connection.close()
        self.root.destroy()

    @staticmethod
    def _build_sweep_values(start: float, stop: float, step: float):
        if (stop - start) * step < 0:
            return []
        values = []
        value = start
        if step > 0:
            while value <= stop + (abs(step) / 1000):
                values.append(round(value, 12))
                value += step
        else:
            while value >= stop - (abs(step) / 1000):
                values.append(round(value, 12))
                value += step
        return values

    @classmethod
    def _build_simple_cycle_values(cls, peak_v: float, step: float, cycles: int):
        step_mag = abs(step)
        peak = abs(peak_v)
        if step_mag == 0 or cycles < 1:
            return []
        if peak == 0:
            return [0.0]

        seg1 = cls._build_sweep_values(0.0, peak, step_mag)
        seg2 = cls._build_sweep_values(peak, 0.0, -step_mag)
        seg3 = cls._build_sweep_values(0.0, -peak, -step_mag)
        seg4 = cls._build_sweep_values(-peak, 0.0, step_mag)
        if not seg1 or not seg2 or not seg3 or not seg4:
            return []

        single = seg1 + seg2[1:] + seg3[1:] + seg4[1:]
        values = list(single)
        for _ in range(cycles - 1):
            values.extend(single[1:])
        return values

    def _default_data_filename(self, prefix="iv"):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sample = self._slug_text(self.sample_entry.get())
        operator = self._slug_text(self.operator_entry.get())
        parts = [prefix]
        if sample:
            parts.append(sample)
        if operator:
            parts.append(operator)
        parts.append(timestamp)
        return "_".join(parts) + ".csv"

    @staticmethod
    def _slug_text(value: str):
        cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
        cleaned = cleaned.strip("-_")
        return cleaned[:40]



