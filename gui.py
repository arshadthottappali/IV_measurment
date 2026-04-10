import os
import re
import json
import threading
import math
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from datetime import datetime
from pathlib import Path

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import FormatStrFormatter
from matplotlib import cm

from connection import KeithleyConnection
from data_logging import DataLogger
from plotting import IVPlotter


class KeithleyUI:
    SAFE_VOLTAGE_LIMIT = 5.0
    SETTINGS_FILE = "keithley_ui_settings.json"
    WRER_WARN_POINTS = 20000

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
        self.custom_segments = []
        self.row_cycle_ids = []
        self.row_time_s = []
        self._sweep_cycle_ids = []
        self._sweep_point_times = []
        self.active_plot_mode = "iv"
        self._last_run_mode = "iv"
        self._pd_steps = []
        self._pd_read_index = 0
        self._fast_sweep_thread = None
        self._fast_sweep_result = None
        self._fast_sweep_error = None
        self._fast_sweep_poll_after_id = None
        self._close_wait_after_id = None
        self._closing = False

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
        self._load_ui_settings()
        self._update_button_states()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.after(250, self._run_startup_prereq_check)

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
        self.controls_tabs = controls_tabs

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
        frame.columnconfigure(1, weight=1)

        self.listbox = tk.Listbox(frame, width=44, height=4)
        self.listbox.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 6))

        ttk.Button(frame, text="Detect Devices", command=self.detect).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        self.connect_btn = ttk.Button(frame, text="Connect", command=self.connect)
        self.connect_btn.grid(row=1, column=1, sticky="ew", padx=(0, 4))
        ttk.Button(frame, text="Prereq Check", command=self.show_prereq_check).grid(row=1, column=2, sticky="ew")
        ttk.Label(frame, textvariable=self.instrument_info_text, wraplength=360).grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0)
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
        frame.columnconfigure(0, weight=1)

        common = ttk.LabelFrame(frame, text="Common Settings", padding=6)
        common.grid(row=0, column=0, sticky="ew")
        common.columnconfigure(1, weight=1)

        self.step_label = ttk.Label(common, text="Step (V)")
        self.step_label.grid(row=0, column=0, sticky="w")
        self.sweep_step_entry = ttk.Entry(common, width=12)
        self.sweep_step_entry.insert(0, "0.1")
        self.sweep_step_entry.grid(row=0, column=1, sticky="ew")

        self.delay_label = ttk.Label(common, text="Delay (s)")
        self.delay_label.grid(row=1, column=0, sticky="w")
        self.sweep_delay_entry = ttk.Entry(common, width=12)
        self.sweep_delay_entry.insert(0, "1")
        self.sweep_delay_entry.grid(row=1, column=1, sticky="ew")

        self.pd_compliance_label = ttk.Label(common, text="PD Compliance (uA)")
        self.pd_compliance_label.grid(row=2, column=0, sticky="w")
        self.pd_compliance_entry = ttk.Entry(common, width=12)
        self.pd_compliance_entry.insert(0, "1")
        self.pd_compliance_entry.grid(row=2, column=1, sticky="ew")

        self.pd_test_no_label = ttk.Label(common, text="PD Test No")
        self.pd_test_no_label.grid(row=3, column=0, sticky="w")
        self.pd_test_no_entry = ttk.Entry(common, width=12)
        self.pd_test_no_entry.insert(0, "01")
        self.pd_test_no_entry.grid(row=3, column=1, sticky="ew")

        self.pd_sample_label = ttk.Label(common, text="PD Sample Name")
        self.pd_sample_label.grid(row=4, column=0, sticky="w")
        self.pd_sample_entry = ttk.Entry(common, width=12)
        self.pd_sample_entry.grid(row=4, column=1, sticky="ew")

        self.pd_electrode_label = ttk.Label(common, text="Electrode Sign")
        self.pd_electrode_label.grid(row=5, column=0, sticky="w")
        self.pd_electrode_entry = ttk.Entry(common, width=12)
        self.pd_electrode_entry.grid(row=5, column=1, sticky="ew")

        self.pd_electrode_no_label = ttk.Label(common, text="Electrode No")
        self.pd_electrode_no_label.grid(row=6, column=0, sticky="w")
        self.pd_electrode_no_entry = ttk.Entry(common, width=12)
        self.pd_electrode_no_entry.grid(row=6, column=1, sticky="ew")

        ttk.Label(common, text="Execution").grid(row=7, column=0, sticky="w")
        self.sweep_exec_combo = ttk.Combobox(
            common,
            state="readonly",
            values=["Host (UI timing)", "Fast TSP (instrument timing)"],
        )
        self.sweep_exec_combo.set("Host (UI timing)")
        self.sweep_exec_combo.grid(row=7, column=1, sticky="ew")
        self.sweep_exec_combo.bind("<<ComboboxSelected>>", self._on_sweep_exec_change)

        self.fast_limit_label = ttk.Label(common, text="Fast Limit")
        self.fast_limit_label.grid(row=8, column=0, sticky="w")
        self.fast_limit_combo = ttk.Combobox(
            common,
            state="readonly",
            values=["1 ms", "500 ns"],
        )
        self.fast_limit_combo.set("1 ms")
        self.fast_limit_combo.grid(row=8, column=1, sticky="ew")
        self._on_sweep_exec_change()

        sweep_tabs = ttk.Notebook(frame)
        sweep_tabs.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        standard_tab = ttk.Frame(sweep_tabs, padding=6)
        custom_tab = ttk.Frame(sweep_tabs, padding=6)
        wrer_tab = ttk.Frame(sweep_tabs, padding=6)
        pd_tab = ttk.Frame(sweep_tabs, padding=6)
        standard_tab.columnconfigure(1, weight=1)
        custom_tab.columnconfigure(1, weight=1)
        wrer_tab.columnconfigure(1, weight=1)
        pd_tab.columnconfigure(1, weight=1)
        sweep_tabs.add(standard_tab, text="Standard Sweep")
        sweep_tabs.add(custom_tab, text="Custom Sequence")
        sweep_tabs.add(wrer_tab, text="WRER")
        sweep_tabs.add(pd_tab, text="PD")
        self.sweep_subtabs = sweep_tabs
        self.sweep_subtabs.bind("<<NotebookTabChanged>>", self._on_sweep_subtab_changed)

        ttk.Label(standard_tab, text="Preset").grid(row=0, column=0, sticky="w")
        self.preset_combo = ttk.Combobox(
            standard_tab,
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

        ttk.Label(standard_tab, text="Start (V)").grid(row=1, column=0, sticky="w")
        self.sweep_start_entry = ttk.Entry(standard_tab, width=12)
        self.sweep_start_entry.insert(0, "0")
        self.sweep_start_entry.grid(row=1, column=1, sticky="ew")

        ttk.Label(standard_tab, text="Stop (V)").grid(row=2, column=0, sticky="w")
        self.sweep_stop_entry = ttk.Entry(standard_tab, width=12)
        self.sweep_stop_entry.insert(0, "1")
        self.sweep_stop_entry.grid(row=2, column=1, sticky="ew")

        ttk.Label(standard_tab, text="Mode").grid(row=3, column=0, sticky="w")
        self.sweep_mode_combo = ttk.Combobox(
            standard_tab,
            state="readonly",
            values=["One-way", "Simple Cycle (0->+V->0->-V->0)"],
        )
        self.sweep_mode_combo.set("Simple Cycle (0->+V->0->-V->0)")
        self.sweep_mode_combo.grid(row=3, column=1, sticky="ew")

        ttk.Label(standard_tab, text="Cycle Peak V").grid(row=4, column=0, sticky="w")
        self.cycle_peak_entry = ttk.Entry(standard_tab, width=12)
        self.cycle_peak_entry.insert(0, "1")
        self.cycle_peak_entry.grid(row=4, column=1, sticky="ew")

        ttk.Label(standard_tab, text="Cycles").grid(row=5, column=0, sticky="w")
        self.sweep_cycles_entry = ttk.Entry(standard_tab, width=12)
        self.sweep_cycles_entry.insert(0, "1")
        self.sweep_cycles_entry.grid(row=5, column=1, sticky="ew")

        self.run_sweep_btn = ttk.Button(standard_tab, text="Run Sweep (F5)", command=self.run_sweep_from_inputs)
        self.run_sweep_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        ttk.Label(custom_tab, text="Custom Sequence Builder").grid(row=0, column=0, columnspan=2, sticky="w")

        ttk.Label(custom_tab, text="Seg Start V").grid(row=1, column=0, sticky="w")
        self.seq_start_entry = ttk.Entry(custom_tab, width=12)
        self.seq_start_entry.insert(0, "0")
        self.seq_start_entry.grid(row=1, column=1, sticky="ew")

        ttk.Label(custom_tab, text="Seg End V").grid(row=2, column=0, sticky="w")
        self.seq_end_entry = ttk.Entry(custom_tab, width=12)
        self.seq_end_entry.insert(0, "1")
        self.seq_end_entry.grid(row=2, column=1, sticky="ew")

        self.add_segment_btn = ttk.Button(custom_tab, text="Add Segment (Append)", command=self._add_sequence_segment)
        self.add_segment_btn.grid(row=3, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.reset_segment_btn = ttk.Button(custom_tab, text="Reset Segments", command=self._reset_sequence_segments)
        self.reset_segment_btn.grid(row=3, column=1, sticky="ew", pady=(6, 0))

        self.segment_listbox = tk.Listbox(custom_tab, height=4)
        self.segment_listbox.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        self.segment_listbox.bind("<<ListboxSelect>>", self._on_segment_selected)

        self.update_segment_btn = ttk.Button(custom_tab, text="Update Selected", command=self._update_sequence_segment)
        self.update_segment_btn.grid(row=5, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.delete_segment_btn = ttk.Button(custom_tab, text="Delete Selected", command=self._delete_sequence_segment)
        self.delete_segment_btn.grid(row=5, column=1, sticky="ew", pady=(6, 0))

        ttk.Label(custom_tab, text="Sequence Cycles").grid(row=6, column=0, sticky="w")
        self.seq_cycles_entry = ttk.Entry(custom_tab, width=12)
        self.seq_cycles_entry.insert(0, "1")
        self.seq_cycles_entry.grid(row=6, column=1, sticky="ew")
        self.run_custom_sweep_btn = ttk.Button(
            custom_tab, text="Run Custom Sequence", command=self.run_custom_sequence_from_builder
        )
        self.run_custom_sweep_btn.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        ttk.Label(wrer_tab, text="Write V").grid(row=0, column=0, sticky="w")
        self.wrer_write_v_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_write_v_entry.insert(0, "1")
        self.wrer_write_v_entry.grid(row=0, column=1, sticky="ew")

        ttk.Label(wrer_tab, text="Write Time (s)").grid(row=1, column=0, sticky="w")
        self.wrer_write_t_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_write_t_entry.insert(0, "0.1")
        self.wrer_write_t_entry.grid(row=1, column=1, sticky="ew")

        ttk.Label(wrer_tab, text="Read V").grid(row=2, column=0, sticky="w")
        self.wrer_read_v_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_read_v_entry.insert(0, "0.1")
        self.wrer_read_v_entry.grid(row=2, column=1, sticky="ew")

        ttk.Label(wrer_tab, text="Read Time (s)").grid(row=3, column=0, sticky="w")
        self.wrer_read_t_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_read_t_entry.insert(0, "0.1")
        self.wrer_read_t_entry.grid(row=3, column=1, sticky="ew")

        ttk.Label(wrer_tab, text="Erase V").grid(row=4, column=0, sticky="w")
        self.wrer_erase_v_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_erase_v_entry.insert(0, "-1")
        self.wrer_erase_v_entry.grid(row=4, column=1, sticky="ew")

        ttk.Label(wrer_tab, text="Erase Time (s)").grid(row=5, column=0, sticky="w")
        self.wrer_erase_t_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_erase_t_entry.insert(0, "0.1")
        self.wrer_erase_t_entry.grid(row=5, column=1, sticky="ew")

        ttk.Label(wrer_tab, text="Cycles").grid(row=6, column=0, sticky="w")
        self.wrer_cycles_entry = ttk.Entry(wrer_tab, width=12)
        self.wrer_cycles_entry.insert(0, "1")
        self.wrer_cycles_entry.grid(row=6, column=1, sticky="ew")

        ttk.Label(
            wrer_tab,
            text="Uses Common Delay as read interval. Step is not used.",
            wraplength=320,
        ).grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.preview_wrer_btn = ttk.Button(wrer_tab, text="Preview Sequence", command=self.preview_wrer)
        self.preview_wrer_btn.grid(row=8, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.run_wrer_btn = ttk.Button(wrer_tab, text="Run WRER", command=self.run_wrer_from_inputs)
        self.run_wrer_btn.grid(row=8, column=1, sticky="ew", pady=(6, 0))

        ttk.Label(pd_tab, text="Potentiation", font=("", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Label(pd_tab, text="V").grid(row=1, column=0, sticky="w")
        self.pd_pot_v_entry = ttk.Entry(pd_tab, width=12)
        self.pd_pot_v_entry.insert(0, "1")
        self.pd_pot_v_entry.grid(row=1, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Time (s)").grid(row=2, column=0, sticky="w")
        self.pd_pot_t_entry = ttk.Entry(pd_tab, width=12)
        self.pd_pot_t_entry.insert(0, "0.1")
        self.pd_pot_t_entry.grid(row=2, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Pulses").grid(row=3, column=0, sticky="w")
        self.pd_pot_pulses_entry = ttk.Entry(pd_tab, width=12)
        self.pd_pot_pulses_entry.insert(0, "10")
        self.pd_pot_pulses_entry.grid(row=3, column=1, sticky="ew")

        ttk.Separator(pd_tab, orient="horizontal").grid(row=4, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        ttk.Label(pd_tab, text="Depression", font=("", 9, "bold")).grid(row=5, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Label(pd_tab, text="V").grid(row=6, column=0, sticky="w")
        self.pd_dep_v_entry = ttk.Entry(pd_tab, width=12)
        self.pd_dep_v_entry.insert(0, "-1")
        self.pd_dep_v_entry.grid(row=6, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Time (s)").grid(row=7, column=0, sticky="w")
        self.pd_dep_t_entry = ttk.Entry(pd_tab, width=12)
        self.pd_dep_t_entry.insert(0, "0.1")
        self.pd_dep_t_entry.grid(row=7, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Pulses").grid(row=8, column=0, sticky="w")
        self.pd_dep_pulses_entry = ttk.Entry(pd_tab, width=12)
        self.pd_dep_pulses_entry.insert(0, "10")
        self.pd_dep_pulses_entry.grid(row=8, column=1, sticky="ew")

        ttk.Separator(pd_tab, orient="horizontal").grid(row=9, column=0, columnspan=2, sticky="ew", pady=(8, 6))

        ttk.Label(pd_tab, text="Read", font=("", 9, "bold")).grid(row=10, column=0, columnspan=2, sticky="w", pady=(0, 2))
        ttk.Label(pd_tab, text="V").grid(row=11, column=0, sticky="w")
        self.pd_read_v_entry = ttk.Entry(pd_tab, width=12)
        self.pd_read_v_entry.insert(0, "0.1")
        self.pd_read_v_entry.grid(row=11, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Time (s)").grid(row=12, column=0, sticky="w")
        self.pd_read_t_entry = ttk.Entry(pd_tab, width=12)
        self.pd_read_t_entry.insert(0, "0.1")
        self.pd_read_t_entry.grid(row=12, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Stim->Read Delay (s)").grid(row=13, column=0, sticky="w")
        self.pd_settle_t_entry = ttk.Entry(pd_tab, width=12)
        self.pd_settle_t_entry.insert(0, "0.01")
        self.pd_settle_t_entry.grid(row=13, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Read->Stim Delay (s)").grid(row=14, column=0, sticky="w")
        self.pd_gap_t_entry = ttk.Entry(pd_tab, width=12)
        self.pd_gap_t_entry.insert(0, "0")
        self.pd_gap_t_entry.grid(row=14, column=1, sticky="ew")

        ttk.Label(pd_tab, text="Cycles").grid(row=15, column=0, sticky="w")
        self.pd_cycles_entry = ttk.Entry(pd_tab, width=12)
        self.pd_cycles_entry.insert(0, "1")
        self.pd_cycles_entry.grid(row=15, column=1, sticky="ew")

        ttk.Label(
            pd_tab,
            text="Applies fixed pot/dep pulses, forces 0 V during Stim->Read Delay, then measures at Read V. Read->Stim Delay is the gap after each read.",
            wraplength=320,
        ).grid(row=16, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.preview_pd_btn = ttk.Button(pd_tab, text="Preview Sequence", command=self.preview_pd)
        self.preview_pd_btn.grid(row=17, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.run_pd_btn = ttk.Button(pd_tab, text="Run PD", command=self.run_pd_from_inputs)
        self.run_pd_btn.grid(row=17, column=1, sticky="ew", pady=(6, 0))

        progress_row = ttk.Frame(frame)
        progress_row.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        progress_row.columnconfigure(0, weight=1)
        progress_row.columnconfigure(1, weight=0)
        self.sweep_progress = ttk.Progressbar(progress_row, mode="determinate", maximum=100, value=0)
        self.sweep_progress.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.sweep_stop_btn = ttk.Button(progress_row, text="Stop Sweep", command=self.stop)
        self.sweep_stop_btn.grid(row=0, column=1, sticky="ew")
        ttk.Label(frame, textvariable=self.progress_text).grid(row=3, column=0, sticky="w")
        ttk.Label(frame, textvariable=self.eta_text).grid(row=4, column=0, sticky="w")
        self._on_sweep_subtab_changed()

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

    def _settings_path(self):
        appdata = os.environ.get("APPDATA")
        if appdata:
            settings_dir = Path(appdata) / "KeithleyIV"
        else:
            settings_dir = Path.home() / ".keithley_iv"
        try:
            settings_dir.mkdir(parents=True, exist_ok=True)
            return settings_dir / self.SETTINGS_FILE
        except Exception:
            return Path(self.SETTINGS_FILE)

    def _set_entry_value(self, entry, value):
        entry.delete(0, tk.END)
        entry.insert(0, str(value))

    def _set_combo_if_valid(self, combo, value):
        values = list(combo.cget("values"))
        if value in values:
            combo.set(value)

    def _load_ui_settings(self):
        path = self._settings_path()
        if not path.exists():
            legacy_path = Path(self.SETTINGS_FILE)
            if legacy_path.exists():
                path = legacy_path
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return

        entry_map = {
            "voltage": self.voltage_entry,
            "compliance_ua": self.compliance_entry,
            "sweep_start_v": self.sweep_start_entry,
            "sweep_stop_v": self.sweep_stop_entry,
            "sweep_step_v": self.sweep_step_entry,
            "sweep_delay_s": self.sweep_delay_entry,
            "pd_compliance_ua": self.pd_compliance_entry,
            "pd_test_no": self.pd_test_no_entry,
            "pd_sample_name": self.pd_sample_entry,
            "pd_electrode_sign": self.pd_electrode_entry,
            "pd_electrode_no": self.pd_electrode_no_entry,
            "cycle_peak_v": self.cycle_peak_entry,
            "sweep_cycles": self.sweep_cycles_entry,
            "seq_start_v": self.seq_start_entry,
            "seq_end_v": self.seq_end_entry,
            "seq_cycles": self.seq_cycles_entry,
            "wrer_write_v": self.wrer_write_v_entry,
            "wrer_write_t": self.wrer_write_t_entry,
            "wrer_read_v": self.wrer_read_v_entry,
            "wrer_read_t": self.wrer_read_t_entry,
            "wrer_erase_v": self.wrer_erase_v_entry,
            "wrer_erase_t": self.wrer_erase_t_entry,
            "wrer_cycles": self.wrer_cycles_entry,
            "pd_pot_v": self.pd_pot_v_entry,
            "pd_pot_t": self.pd_pot_t_entry,
            "pd_pot_pulses": self.pd_pot_pulses_entry,
            "pd_read_v": self.pd_read_v_entry,
            "pd_read_t": self.pd_read_t_entry,
            "pd_settle_t": self.pd_settle_t_entry,
            "pd_gap_t": self.pd_gap_t_entry,
            "pd_dep_v": self.pd_dep_v_entry,
            "pd_dep_t": self.pd_dep_t_entry,
            "pd_dep_pulses": self.pd_dep_pulses_entry,
            "pd_cycles": self.pd_cycles_entry,
            "sample_name": self.sample_entry,
            "operator": self.operator_entry,
            "notes": self.notes_entry,
        }
        for key, widget in entry_map.items():
            if key in data:
                self._set_entry_value(widget, data[key])

        self._set_combo_if_valid(self.sample_rate, data.get("sample_rate_ms"))
        self._set_combo_if_valid(self.preset_combo, data.get("preset"))
        self._set_combo_if_valid(self.sweep_mode_combo, data.get("sweep_mode"))
        self._set_combo_if_valid(self.sweep_exec_combo, data.get("sweep_exec"))
        self._set_combo_if_valid(self.fast_limit_combo, data.get("fast_limit"))
        self._set_combo_if_valid(self.x_axis_scale_combo, data.get("x_axis_scale"))
        self._set_combo_if_valid(self.y_axis_scale_combo, data.get("y_axis_scale"))
        active_plot_mode = data.get("active_plot_mode")
        if active_plot_mode in ("iv", "wrer"):
            self.active_plot_mode = active_plot_mode
        last_run_mode = data.get("last_run_mode")
        if last_run_mode in ("iv", "wrer"):
            self._last_run_mode = last_run_mode
        self._on_sweep_exec_change()

        preferred_save_dir = data.get("preferred_save_dir")
        if isinstance(preferred_save_dir, str) and preferred_save_dir and os.path.isdir(preferred_save_dir):
            self.preferred_save_dir = preferred_save_dir
            self.save_dir_text.set(f"Save folder: {self.preferred_save_dir}")

        saved_segments = data.get("custom_segments")
        if isinstance(saved_segments, list):
            parsed = []
            for item in saved_segments:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    try:
                        parsed.append((float(item[0]), float(item[1])))
                    except Exception:
                        continue
            self.custom_segments = parsed
            self._refresh_sequence_list()

        tab_idx = data.get("sweep_tab_index")
        if isinstance(tab_idx, int):
            try:
                self.sweep_subtabs.select(tab_idx)
            except Exception:
                pass

        main_tab_idx = data.get("main_tab_index")
        if isinstance(main_tab_idx, int):
            try:
                self.controls_tabs.select(main_tab_idx)
            except Exception:
                pass

        geometry = data.get("window_geometry")
        if isinstance(geometry, str) and geometry:
            try:
                self.root.geometry(geometry)
            except Exception:
                pass
        self._on_sweep_subtab_changed()

    def _save_ui_settings(self):
        data = {
            "voltage": self.voltage_entry.get(),
            "compliance_ua": self.compliance_entry.get(),
            "sweep_start_v": self.sweep_start_entry.get(),
            "sweep_stop_v": self.sweep_stop_entry.get(),
            "sweep_step_v": self.sweep_step_entry.get(),
            "sweep_delay_s": self.sweep_delay_entry.get(),
            "pd_compliance_ua": self.pd_compliance_entry.get(),
            "pd_test_no": self.pd_test_no_entry.get(),
            "pd_sample_name": self.pd_sample_entry.get(),
            "pd_electrode_sign": self.pd_electrode_entry.get(),
            "pd_electrode_no": self.pd_electrode_no_entry.get(),
            "cycle_peak_v": self.cycle_peak_entry.get(),
            "sweep_cycles": self.sweep_cycles_entry.get(),
            "seq_start_v": self.seq_start_entry.get(),
            "seq_end_v": self.seq_end_entry.get(),
            "seq_cycles": self.seq_cycles_entry.get(),
            "wrer_write_v": self.wrer_write_v_entry.get(),
            "wrer_write_t": self.wrer_write_t_entry.get(),
            "wrer_read_v": self.wrer_read_v_entry.get(),
            "wrer_read_t": self.wrer_read_t_entry.get(),
            "wrer_erase_v": self.wrer_erase_v_entry.get(),
            "wrer_erase_t": self.wrer_erase_t_entry.get(),
            "wrer_cycles": self.wrer_cycles_entry.get(),
            "pd_pot_v": self.pd_pot_v_entry.get(),
            "pd_pot_t": self.pd_pot_t_entry.get(),
            "pd_pot_pulses": self.pd_pot_pulses_entry.get(),
            "pd_read_v": self.pd_read_v_entry.get(),
            "pd_read_t": self.pd_read_t_entry.get(),
            "pd_settle_t": self.pd_settle_t_entry.get(),
            "pd_gap_t": self.pd_gap_t_entry.get(),
            "pd_dep_v": self.pd_dep_v_entry.get(),
            "pd_dep_t": self.pd_dep_t_entry.get(),
            "pd_dep_pulses": self.pd_dep_pulses_entry.get(),
            "pd_cycles": self.pd_cycles_entry.get(),
            "sample_name": self.sample_entry.get(),
            "operator": self.operator_entry.get(),
            "notes": self.notes_entry.get(),
            "sample_rate_ms": self.sample_rate.get(),
            "preset": self.preset_combo.get(),
            "sweep_mode": self.sweep_mode_combo.get(),
            "sweep_exec": self.sweep_exec_combo.get(),
            "fast_limit": self.fast_limit_combo.get(),
            "x_axis_scale": self.x_axis_scale_combo.get(),
            "y_axis_scale": self.y_axis_scale_combo.get(),
            "active_plot_mode": self.active_plot_mode,
            "last_run_mode": self._last_run_mode,
            "custom_segments": self.custom_segments,
            "sweep_tab_index": self.sweep_subtabs.index(self.sweep_subtabs.select()),
            "main_tab_index": self.controls_tabs.index(self.controls_tabs.select()),
            "preferred_save_dir": self.preferred_save_dir,
            "window_geometry": self.root.winfo_geometry(),
        }
        try:
            self._settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _bind_shortcuts(self):
        self.root.bind("<Control-s>", lambda _event: self.save_csv_manual())
        self.root.bind("<F5>", lambda _event: self.run_sweep_from_inputs())
        self.root.bind("<Escape>", lambda _event: self.stop())

    def _update_button_states(self):
        connected_state = "normal" if self.connected else "disabled"
        idle_connected_state = "normal" if self.connected and not (self.live_running or self.sweep_running) else "disabled"
        live_state = "normal" if self.connected and not self.live_running and not self.sweep_running else "disabled"
        preview_state = "disabled" if (self.live_running or self.sweep_running) else "normal"
        stop_state = "normal" if self.live_running or self.sweep_running else "disabled"

        self.apply_voltage_btn.config(state=idle_connected_state)
        self.measure_btn.config(state=idle_connected_state)
        self.apply_compliance_btn.config(state=idle_connected_state)
        self.start_live_btn.config(state=live_state)
        self.run_sweep_btn.config(state=live_state)
        self.run_custom_sweep_btn.config(state=live_state)
        self.run_wrer_btn.config(state=live_state)
        self.run_pd_btn.config(state=live_state)
        self.preview_wrer_btn.config(state=preview_state)
        self.preview_pd_btn.config(state=preview_state)
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

    def _annotate_last_row(self, cycle_id=0, point_t=None):
        if not self.logger.rows:
            return
        row = self.logger.rows[-1]
        row.cycle_id = int(cycle_id)
        row.elapsed_s = None if point_t is None else float(point_t)
        active_mode = getattr(self, "active_plot_mode", "iv")
        row.plot_mode = active_mode if active_mode in ("iv", "wrer", "pd") else "iv"

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

    def _run_startup_prereq_check(self):
        self.show_prereq_check(silent_if_ok=True)

    def _format_prereq_report(self, report):
        lines = []
        if report["visa_ready"]:
            lines.append("VISA runtime: OK")
        else:
            lines.append("VISA runtime: NOT READY")

        resources = report.get("resources", [])
        gpib_resources = report.get("gpib_resources", [])
        lines.append(f"Detected VISA resources: {len(resources)}")
        lines.append(f"Detected GPIB resources: {len(gpib_resources)}")
        if resources:
            preview = resources[:6]
            lines.append("Resources:")
            for item in preview:
                lines.append(f"- {item}")
            if len(resources) > len(preview):
                lines.append(f"- ... and {len(resources) - len(preview)} more")
        if report.get("issues"):
            lines.append("")
            lines.append("Issues:")
            for issue in report["issues"]:
                lines.append(f"- {issue}")
        lines.append("")
        lines.append("Required downloads/drivers:")
        for item in report["downloads"]:
            lines.append(f"- {item}")
        return "\n".join(lines)

    def show_prereq_check(self, silent_if_ok=False):
        report = self.connection.diagnose_prerequisites()
        details = self._format_prereq_report(report)
        if report["ok"] and not report.get("issues"):
            if not silent_if_ok:
                messagebox.showinfo("Prerequisite Check", details)
            return
        messagebox.showwarning("Prerequisite Check", details)

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
            self.logger.set_run_description("")
            current = self.connection.measure_current()
            self.active_plot_mode = "iv"
            self._last_run_mode = "iv"
            self.logger.add(self.last_voltage, current, auto_save=self.autosave_enabled and self.logger.output_file is not None)
            self.row_cycle_ids.append(0)
            self.row_time_s.append(None)
            self._annotate_last_row(cycle_id=0, point_t=None)
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
            self.logger.set_run_description("")
            current = self.connection.measure_current()
            self.active_plot_mode = "iv"
            self._last_run_mode = "iv"
            self.logger.add(self.last_voltage, current, auto_save=False)
            self.row_cycle_ids.append(0)
            self.row_time_s.append(None)
            self._annotate_last_row(cycle_id=0, point_t=None)
            if len(self.logger.rows) > self.max_live_points:
                self.logger.rows = self.logger.rows[-self.max_live_points:]
                self.row_cycle_ids = self.row_cycle_ids[-self.max_live_points:]
                self.row_time_s = self.row_time_s[-self.max_live_points:]
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

    def preview_wrer(self):
        try:
            delay = float(self.sweep_delay_entry.get())
            write_v = float(self.wrer_write_v_entry.get())
            write_t = float(self.wrer_write_t_entry.get())
            read_v = float(self.wrer_read_v_entry.get())
            read_t = float(self.wrer_read_t_entry.get())
            erase_v = float(self.wrer_erase_v_entry.get())
            erase_t = float(self.wrer_erase_t_entry.get())
            cycles = int(self.wrer_cycles_entry.get())
        except ValueError:
            messagebox.showerror("Error", "WRER fields must be valid numbers")
            return

        if cycles < 1 or write_t <= 0 or read_t <= 0 or erase_t <= 0 or delay <= 0:
            messagebox.showerror("Error", "Invalid time/cycle parameters")
            return
        voltages_to_check = [write_v, read_v, erase_v]
        if any(abs(v) > self.connection.MAX_ABS_VOLTAGE for v in voltages_to_check):
            messagebox.showerror(
                "Error",
                f"WRER voltages must be within +/-{self.connection.MAX_ABS_VOLTAGE:g} V",
            )
            return
        if max(abs(v) for v in voltages_to_check) > self.SAFE_VOLTAGE_LIMIT:
            proceed = messagebox.askyesno(
                "High Voltage Warning",
                f"WRER includes voltage above {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue preview?",
            )
            if not proceed:
                return
        total_points = self._estimate_wrer_total_points(write_t, read_t, erase_t, delay, cycles)
        if not self._confirm_large_wrer_sequence(total_points, action="Preview"):
            return

        values, _ = self._build_wrer_values_with_cycles(
            write_v=write_v,
            write_t=write_t,
            read_v=read_v,
            read_t=read_t,
            erase_v=erase_v,
            erase_t=erase_t,
            sample_interval_s=delay,
            cycles=cycles,
        )

        if not values:
            messagebox.showinfo("Preview", "No points generated")
            return

        times = [i * delay for i in range(len(values))]
        dummy_currents = [0.0] * len(values)
        IVPlotter.show_time_series(times, values, dummy_currents, title="WRER Preview (Voltage Profile)", current_yscale="linear")

    def preview_pd(self):
        try:
            gap_delay = float(self.pd_gap_t_entry.get())
            pot_v = float(self.pd_pot_v_entry.get())
            pot_t = float(self.pd_pot_t_entry.get())
            pot_pulses = int(self.pd_pot_pulses_entry.get())
            read_v = float(self.pd_read_v_entry.get())
            read_t = float(self.pd_read_t_entry.get())
            settle_t = float(self.pd_settle_t_entry.get())
            compliance_uA = float(self.pd_compliance_entry.get())
            dep_v = float(self.pd_dep_v_entry.get())
            dep_t = float(self.pd_dep_t_entry.get())
            dep_pulses = int(self.pd_dep_pulses_entry.get())
            cycles = int(self.pd_cycles_entry.get())
        except ValueError:
            messagebox.showerror("Error", "PD fields must be valid numbers")
            return

        steps = self._validate_and_build_pd_steps(
            gap_delay=gap_delay,
            pot_v=pot_v,
            pot_t=pot_t,
            pot_pulses=pot_pulses,
            read_v=read_v,
            read_t=read_t,
            settle_t=settle_t,
            compliance_uA=compliance_uA,
            dep_v=dep_v,
            dep_t=dep_t,
            dep_pulses=dep_pulses,
            cycles=cycles,
            action="Preview",
        )
        if not steps:
            return

        times = [step.get("elapsed_s", float(idx + 1)) for idx, step in enumerate(steps)]
        voltages = [step["voltage"] for step in steps]
        if not times:
            messagebox.showinfo("Preview", "No PD points generated")
            return
        dummy_currents = [0.0] * len(times)
        IVPlotter.show_time_series(
            times,
            voltages,
            dummy_currents,
            title="PD Preview (Voltage Profile)",
            current_yscale="linear",
        )

    def run_sweep_from_inputs(self):
        if self.live_running:
            messagebox.showerror("Error", "Stop live mode before running a sweep")
            return
        sweep_mode = self.sweep_mode_combo.get()
        sweep_exec = self.sweep_exec_combo.get()
        try:
            step = float(self.sweep_step_entry.get())
            delay = float(self.sweep_delay_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Step and delay must be valid numbers")
            return

        if step == 0:
            messagebox.showerror("Error", "Sweep step cannot be zero")
            return
        fast_limit = self.fast_limit_combo.get()
        if sweep_exec == "Fast TSP (instrument timing)":
            min_delay = 5e-7 if fast_limit == "500 ns" else 0.001
        else:
            min_delay = 0.01
        if delay < min_delay:
            if min_delay < 1e-6:
                min_text = "500 ns"
            elif min_delay < 0.001:
                min_text = f"{min_delay:.1e} s"
            else:
                min_text = f"{min_delay:.3f} s"
            messagebox.showerror("Error", f"Delay must be at least {min_text}")
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
            values, cycle_ids = self._build_simple_cycle_values_with_cycles(cycle_peak, step, cycles)
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
            cycle_ids = [1] * len(values)
        if not values:
            messagebox.showerror("Error", "Sweep range/step produced no points")
            return
        self._start_sweep_run(values=values, cycle_ids=cycle_ids, delay=delay, sweep_exec=sweep_exec, plot_mode="iv")

    def run_custom_sequence_from_builder(self):
        if self.live_running:
            messagebox.showerror("Error", "Stop live mode before running a sweep")
            return
        if not self.custom_segments:
            messagebox.showerror("Error", "Add at least one custom segment first")
            return
        sweep_exec = self.sweep_exec_combo.get()
        try:
            step = float(self.sweep_step_entry.get())
            delay = float(self.sweep_delay_entry.get())
            seq_cycles = int(self.seq_cycles_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Step/delay must be numbers and sequence cycles must be an integer")
            return
        if step == 0:
            messagebox.showerror("Error", "Sweep step cannot be zero")
            return
        if seq_cycles < 1:
            messagebox.showerror("Error", "Sequence cycles must be >= 1")
            return
        fast_limit = self.fast_limit_combo.get()
        if sweep_exec == "Fast TSP (instrument timing)":
            min_delay = 5e-7 if fast_limit == "500 ns" else 0.001
        else:
            min_delay = 0.01
        if delay < min_delay:
            if min_delay < 1e-6:
                min_text = "500 ns"
            elif min_delay < 0.001:
                min_text = f"{min_delay:.1e} s"
            else:
                min_text = f"{min_delay:.3f} s"
            messagebox.showerror("Error", f"Delay must be at least {min_text}")
            return
        endpoints = [v for pair in self.custom_segments for v in pair]
        if any(abs(v) > self.connection.MAX_ABS_VOLTAGE for v in endpoints):
            messagebox.showerror(
                "Error",
                f"Sequence values must be within +/-{self.connection.MAX_ABS_VOLTAGE:g} V",
            )
            return
        if max(abs(v) for v in endpoints) > self.SAFE_VOLTAGE_LIMIT:
            proceed = messagebox.askyesno(
                "High Voltage Warning",
                f"Sequence includes voltage above {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue?",
            )
            if not proceed:
                return
        values, cycle_ids = self._build_custom_sequence_values_with_cycles(step, seq_cycles)
        if not values:
            messagebox.showerror("Error", "Custom sequence produced no points")
            return
        self._start_sweep_run(values=values, cycle_ids=cycle_ids, delay=delay, sweep_exec=sweep_exec, plot_mode="iv")

    def run_wrer_from_inputs(self):
        if self.live_running:
            messagebox.showerror("Error", "Stop live mode before running a sweep")
            return
        sweep_exec = self.sweep_exec_combo.get()
        try:
            delay = float(self.sweep_delay_entry.get())
            write_v = float(self.wrer_write_v_entry.get())
            write_t = float(self.wrer_write_t_entry.get())
            read_v = float(self.wrer_read_v_entry.get())
            read_t = float(self.wrer_read_t_entry.get())
            erase_v = float(self.wrer_erase_v_entry.get())
            erase_t = float(self.wrer_erase_t_entry.get())
            cycles = int(self.wrer_cycles_entry.get())
        except ValueError:
            messagebox.showerror("Error", "WRER fields must be valid numbers (cycles must be integer)")
            return
        if cycles < 1:
            messagebox.showerror("Error", "WRER cycles must be >= 1")
            return
        if write_t <= 0 or read_t <= 0 or erase_t <= 0:
            messagebox.showerror("Error", "Write/Read/Erase time must be > 0")
            return
        fast_limit = self.fast_limit_combo.get()
        if sweep_exec == "Fast TSP (instrument timing)":
            min_delay = 5e-7 if fast_limit == "500 ns" else 0.001
        else:
            min_delay = 0.01
        if delay < min_delay:
            if min_delay < 1e-6:
                min_text = "500 ns"
            elif min_delay < 0.001:
                min_text = f"{min_delay:.1e} s"
            else:
                min_text = f"{min_delay:.3f} s"
            messagebox.showerror("Error", f"Delay must be at least {min_text}")
            return
        voltages_to_check = [write_v, read_v, erase_v]
        if any(abs(v) > self.connection.MAX_ABS_VOLTAGE for v in voltages_to_check):
            messagebox.showerror(
                "Error",
                f"WRER voltages must be within +/-{self.connection.MAX_ABS_VOLTAGE:g} V",
            )
            return
        if max(abs(v) for v in voltages_to_check) > self.SAFE_VOLTAGE_LIMIT:
            proceed = messagebox.askyesno(
                "High Voltage Warning",
                f"WRER includes voltage above {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue?",
            )
            if not proceed:
                return
        total_points = self._estimate_wrer_total_points(write_t, read_t, erase_t, delay, cycles)
        if not self._confirm_large_wrer_sequence(total_points, action="Run"):
            return
        values, cycle_ids = self._build_wrer_values_with_cycles(
            write_v=write_v,
            write_t=write_t,
            read_v=read_v,
            read_t=read_t,
            erase_v=erase_v,
            erase_t=erase_t,
            sample_interval_s=delay,
            cycles=cycles,
        )
        if not values:
            messagebox.showerror("Error", "WRER produced no points")
            return
        point_times = [idx * delay for idx in range(len(values))]
        self._start_sweep_run(
            values=values,
            cycle_ids=cycle_ids,
            delay=delay,
            sweep_exec=sweep_exec,
            plot_mode="wrer",
            point_times=point_times,
        )

    def run_pd_from_inputs(self):
        if self.live_running:
            messagebox.showerror("Error", "Stop live mode before running a sweep")
            return
        if self.sweep_exec_combo.get() != "Host (UI timing)":
            messagebox.showerror("Error", "PD currently supports Host (UI timing) only")
            return
        try:
            gap_delay = float(self.pd_gap_t_entry.get())
            pot_v = float(self.pd_pot_v_entry.get())
            pot_t = float(self.pd_pot_t_entry.get())
            pot_pulses = int(self.pd_pot_pulses_entry.get())
            read_v = float(self.pd_read_v_entry.get())
            read_t = float(self.pd_read_t_entry.get())
            settle_t = float(self.pd_settle_t_entry.get())
            compliance_uA = float(self.pd_compliance_entry.get())
            dep_v = float(self.pd_dep_v_entry.get())
            dep_t = float(self.pd_dep_t_entry.get())
            dep_pulses = int(self.pd_dep_pulses_entry.get())
            cycles = int(self.pd_cycles_entry.get())
        except ValueError:
            messagebox.showerror("Error", "PD fields must be valid numbers (pulse counts/cycles must be integer)")
            return

        steps = self._validate_and_build_pd_steps(
            gap_delay=gap_delay,
            pot_v=pot_v,
            pot_t=pot_t,
            pot_pulses=pot_pulses,
            read_v=read_v,
            read_t=read_t,
            settle_t=settle_t,
            compliance_uA=compliance_uA,
            dep_v=dep_v,
            dep_t=dep_t,
            dep_pulses=dep_pulses,
            cycles=cycles,
            action="Run",
        )
        if not steps:
            return

        file_path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialdir=self.preferred_save_dir,
            initialfile=self._pd_output_filename(),
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")],
            title="Select save file before starting PD run",
        )
        if not file_path:
            return

        try:
            self._sync_metadata()
            self.logger.set_run_description(
                f"{self._pd_graph_title()} | " + self._build_pd_run_description(
                    pot_v=pot_v,
                    pot_t=pot_t,
                    pot_pulses=pot_pulses,
                    read_v=read_v,
                    read_t=read_t,
                    settle_t=settle_t,
                    compliance_uA=compliance_uA,
                    dep_v=dep_v,
                    dep_t=dep_t,
                    dep_pulses=dep_pulses,
                    gap_delay=gap_delay,
                    cycles=cycles,
                )
            )
            self.logger.clear()
            self.row_cycle_ids = []
            self.row_time_s = []
            self.active_plot_mode = "pd"
            self._last_run_mode = "pd"
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
            self.connection.set_current_compliance_ua(compliance_uA)
            self._pd_steps = steps
            self._pd_read_index = 0
            self.stop_flag = False
            self.sweep_running = True
            self._sweep_values = [step["voltage"] for step in steps]
            self._sweep_index = 0
            self._sweep_start_time = datetime.now()
            self.sweep_progress.config(maximum=len(self._pd_steps), value=0)
            self.progress_text.set(f"Sweep progress: 0/{len(self._pd_steps)}")
            self.eta_text.set("Elapsed: 00:00 | ETA: --:--")
            self.status_text.set("PD sequence running")
            self._update_button_states()
            self.connection.zero_output()
            self._run_next_pd_step()
        except Exception as e:
            self._finish_sweep()
            messagebox.showerror("Error", str(e))

    def _start_sweep_run(self, values, cycle_ids, delay, sweep_exec, plot_mode="iv", point_times=None):
        file_path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            initialdir=self.preferred_save_dir,
            initialfile=self._default_data_filename(),
            filetypes=[("CSV Files", "*.csv"), ("All Files", "*.*")],
            title="Select save file before starting sweep",
        )
        if not file_path:
            return

        if sweep_exec == "Fast TSP (instrument timing)" and self.connection.mode != "tsp2600":
            messagebox.showerror(
                "Error",
                "Fast TSP execution is available only when connected to Keithley 2600 in TSP mode.",
            )
            return

        try:
            self._sync_metadata()
            self.logger.set_run_description("")
            self.logger.clear()
            self.row_cycle_ids = []
            self.row_time_s = []
            self.active_plot_mode = plot_mode
            self._last_run_mode = plot_mode
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
            if sweep_exec == "Fast TSP (instrument timing)":
                self._run_fast_instrument_sweep(values, delay, cycle_ids=cycle_ids, point_times=point_times)
            else:
                self.sweep_example(values, cycle_ids=cycle_ids, point_times=point_times)
            self._update_button_states()
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def _on_sweep_exec_change(self, _event=None):
        fast_mode = self.sweep_exec_combo.get() == "Fast TSP (instrument timing)"
        if fast_mode:
            self.fast_limit_label.grid()
            self.fast_limit_combo.grid()
        else:
            self.fast_limit_label.grid_remove()
            self.fast_limit_combo.grid_remove()

    def _on_sweep_subtab_changed(self, _event=None):
        if not hasattr(self, "sweep_subtabs"):
            return
        has_pd_compliance = hasattr(self, "pd_compliance_label") and hasattr(self, "pd_compliance_entry")
        current_idx = self.sweep_subtabs.index(self.sweep_subtabs.select())
        if current_idx == 2:
            self.step_label.config(text="Step (V) [unused in WRER]")
            self.sweep_step_entry.config(state="disabled")
            self.delay_label.grid()
            self.sweep_delay_entry.grid()
            if has_pd_compliance:
                self.pd_compliance_label.grid_remove()
                self.pd_compliance_entry.grid_remove()
            for name in ("pd_test_no_label", "pd_test_no_entry", "pd_sample_label", "pd_sample_entry", "pd_electrode_label", "pd_electrode_entry", "pd_electrode_no_label", "pd_electrode_no_entry"):
                if hasattr(self, name):
                    getattr(self, name).grid_remove()
        elif current_idx == 3:
            self.step_label.grid_remove()
            self.sweep_step_entry.grid_remove()
            self.delay_label.grid_remove()
            self.sweep_delay_entry.grid_remove()
            if has_pd_compliance:
                self.pd_compliance_label.grid()
                self.pd_compliance_entry.grid()
            for name in ("pd_test_no_label", "pd_test_no_entry", "pd_sample_label", "pd_sample_entry", "pd_electrode_label", "pd_electrode_entry", "pd_electrode_no_label", "pd_electrode_no_entry"):
                if hasattr(self, name):
                    getattr(self, name).grid()
        else:
            self.step_label.grid()
            self.sweep_step_entry.grid()
            self.delay_label.grid()
            self.sweep_delay_entry.grid()
            if has_pd_compliance:
                self.pd_compliance_label.grid_remove()
                self.pd_compliance_entry.grid_remove()
            for name in ("pd_test_no_label", "pd_test_no_entry", "pd_sample_label", "pd_sample_entry", "pd_electrode_label", "pd_electrode_entry", "pd_electrode_no_label", "pd_electrode_no_entry"):
                if hasattr(self, name):
                    getattr(self, name).grid_remove()
            self.step_label.config(text="Step (V)")
            self.sweep_step_entry.config(state="normal")
        if current_idx != 3:
            self.step_label.config(text="Step (V)" if current_idx != 2 else "Step (V) [unused in WRER]")
            if current_idx != 2:
                self.sweep_step_entry.config(state="normal")
        # Reflect selected sweep mode immediately on the embedded plot view.
        if hasattr(self, "x_axis_scale_combo") and hasattr(self, "y_axis_scale_combo") and hasattr(self, "figure"):
            self._refresh_embedded_plot()

    def _add_sequence_segment(self):
        try:
            start_v = float(self.seq_start_entry.get())
            end_v = float(self.seq_end_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Segment start/end must be valid numbers")
            return
        self.custom_segments.append((start_v, end_v))
        self._refresh_sequence_list()

    def _update_sequence_segment(self):
        idx = self._get_selected_segment_index()
        if idx is None:
            messagebox.showerror("Error", "Select a segment to update")
            return
        try:
            start_v = float(self.seq_start_entry.get())
            end_v = float(self.seq_end_entry.get())
        except ValueError:
            messagebox.showerror("Error", "Segment start/end must be valid numbers")
            return
        self.custom_segments[idx] = (start_v, end_v)
        self._refresh_sequence_list()
        self.segment_listbox.selection_set(idx)
        self.segment_listbox.activate(idx)

    def _delete_sequence_segment(self):
        idx = self._get_selected_segment_index()
        if idx is None:
            messagebox.showerror("Error", "Select a segment to delete")
            return
        del self.custom_segments[idx]
        self._refresh_sequence_list()
        if self.custom_segments:
            new_idx = min(idx, len(self.custom_segments) - 1)
            self.segment_listbox.selection_set(new_idx)
            self.segment_listbox.activate(new_idx)
            self._on_segment_selected()

    def _reset_sequence_segments(self):
        self.custom_segments.clear()
        self._refresh_sequence_list()

    def _get_selected_segment_index(self):
        selected = self.segment_listbox.curselection()
        if not selected:
            return None
        idx = int(selected[0])
        if idx < 0 or idx >= len(self.custom_segments):
            return None
        return idx

    def _on_segment_selected(self, _event=None):
        idx = self._get_selected_segment_index()
        if idx is None:
            return
        start_v, end_v = self.custom_segments[idx]
        self.seq_start_entry.delete(0, tk.END)
        self.seq_start_entry.insert(0, f"{start_v:g}")
        self.seq_end_entry.delete(0, tk.END)
        self.seq_end_entry.insert(0, f"{end_v:g}")

    def _refresh_sequence_list(self):
        self.segment_listbox.delete(0, tk.END)
        for idx, (a, b) in enumerate(self.custom_segments, start=1):
            self.segment_listbox.insert(tk.END, f"{idx}. {a:g} -> {b:g}")

    def _build_custom_sequence_values_with_cycles(self, step: float, cycles: int):
        step_mag = abs(step)
        if step_mag == 0 or cycles < 1 or not self.custom_segments:
            return [], []
        single = []
        for idx, (a, b) in enumerate(self.custom_segments):
            if a == b:
                seg = [round(a, 12)]
            else:
                signed = step_mag if b > a else -step_mag
                seg = self._build_sweep_values(a, b, signed)
            if not seg:
                return [], []
            # Skip the first point of later segments only when it duplicates the
            # previous segment's end point. Single-point segments must be kept.
            if idx == 0 or single[-1] != seg[0]:
                single.extend(seg)
            else:
                single.extend(seg[1:])
        values = list(single)
        cycle_ids = [1] * len(single)
        for cycle_idx in range(2, cycles + 1):
            repeat_points = single if values[-1] != single[0] else single[1:]
            values.extend(repeat_points)
            cycle_ids.extend([cycle_idx] * len(repeat_points))
        return values, cycle_ids

    @staticmethod
    def _build_hold_values(voltage: float, hold_time_s: float, sample_interval_s: float):
        if hold_time_s <= 0 or sample_interval_s <= 0:
            return []
        points = max(1, int(round(hold_time_s / sample_interval_s)))
        return [round(voltage, 12)] * points

    @classmethod
    def _build_wrer_values_with_cycles(
        cls,
        write_v: float,
        write_t: float,
        read_v: float,
        read_t: float,
        erase_v: float,
        erase_t: float,
        sample_interval_s: float,
        cycles: int,
    ):
        if cycles < 1:
            return [], []
        single = []
        single.extend(cls._build_hold_values(write_v, write_t, sample_interval_s))
        single.extend(cls._build_hold_values(read_v, read_t, sample_interval_s))
        single.extend(cls._build_hold_values(erase_v, erase_t, sample_interval_s))
        single.extend(cls._build_hold_values(read_v, read_t, sample_interval_s))
        if not single:
            return [], []
        values = list(single)
        cycle_ids = [1] * len(single)
        for cycle_idx in range(2, cycles + 1):
            values.extend(single)
            cycle_ids.extend([cycle_idx] * len(single))
        return values, cycle_ids

    @staticmethod
    def _estimate_hold_points(hold_time_s: float, sample_interval_s: float):
        if hold_time_s <= 0 or sample_interval_s <= 0:
            return 0
        return max(1, int(round(hold_time_s / sample_interval_s)))

    @classmethod
    def _estimate_wrer_total_points(
        cls,
        write_t: float,
        read_t: float,
        erase_t: float,
        sample_interval_s: float,
        cycles: int,
    ):
        if cycles < 1:
            return 0
        single = (
            cls._estimate_hold_points(write_t, sample_interval_s)
            + cls._estimate_hold_points(read_t, sample_interval_s)
            + cls._estimate_hold_points(erase_t, sample_interval_s)
            + cls._estimate_hold_points(read_t, sample_interval_s)
        )
        return single * cycles

    def _confirm_large_wrer_sequence(self, total_points: int, action: str):
        if total_points <= self.WRER_WARN_POINTS:
            return True
        return messagebox.askyesno(
            "Large WRER Sequence",
            (
                f"{action} will generate about {total_points:,} points.\n"
                "This may take a long time and can make plotting slower.\n"
                "Continue?"
            ),
        )

    def _validate_and_build_pd_steps(
        self,
        gap_delay: float,
        pot_v: float,
        pot_t: float,
        pot_pulses: int,
        read_v: float,
        read_t: float,
        settle_t: float,
        compliance_uA: float,
        dep_v: float,
        dep_t: float,
        dep_pulses: int,
        cycles: int,
        action: str,
    ):
        if cycles < 1:
            messagebox.showerror("Error", "PD cycles must be >= 1")
            return None
        if pot_pulses < 1 or dep_pulses < 1:
            messagebox.showerror("Error", "Potentiation and depression pulses must both be >= 1")
            return None
        if gap_delay < 0:
            messagebox.showerror("Error", "Common Delay must be >= 0")
            return None
        if settle_t < 0:
            messagebox.showerror("Error", "Stim->Read Delay must be >= 0")
            return None
        if min(pot_t, read_t, dep_t) <= 0:
            messagebox.showerror("Error", "PD pulse/read times must be > 0")
            return None
        if min(pot_t, read_t, dep_t) < 0.001:
            messagebox.showerror("Error", "PD pulse/read times must be at least 0.001 s in Host mode")
            return None
        try:
            self.connection._validate_compliance(compliance_uA)
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return None

        voltages_to_check = [pot_v, read_v, dep_v]
        if any(abs(v) > self.connection.MAX_ABS_VOLTAGE for v in voltages_to_check):
            messagebox.showerror(
                "Error",
                f"PD voltages must be within +/-{self.connection.MAX_ABS_VOLTAGE:g} V",
            )
            return None
        if max(abs(v) for v in voltages_to_check) > self.SAFE_VOLTAGE_LIMIT:
            proceed = messagebox.askyesno(
                "High Voltage Warning",
                f"PD includes voltage above {self.SAFE_VOLTAGE_LIMIT:g} V.\nContinue {action.lower()}?",
            )
            if not proceed:
                return None

        total_reads = cycles * (pot_pulses + dep_pulses)
        if not self._confirm_large_wrer_sequence(total_reads * 2, action=action):
            return None

        return self._build_pd_steps_with_cycles(
            pot_v=pot_v,
            pot_t=pot_t,
            pot_pulses=pot_pulses,
            read_v=read_v,
            read_t=read_t,
            settle_t=settle_t,
            dep_v=dep_v,
            dep_t=dep_t,
            dep_pulses=dep_pulses,
            gap_delay_s=gap_delay,
            cycles=cycles,
        )

    @staticmethod
    def _build_pd_steps_with_cycles(
        pot_v: float,
        pot_t: float,
        pot_pulses: int,
        read_v: float,
        read_t: float,
        settle_t: float,
        dep_v: float,
        dep_t: float,
        dep_pulses: int,
        gap_delay_s: float,
        cycles: int,
    ):
        steps = []
        read_index = 0
        elapsed_s = 0.0
        for cycle_idx in range(1, cycles + 1):
            for _ in range(pot_pulses):
                elapsed_s += float(pot_t)
                steps.append(
                    {
                        "voltage": round(pot_v, 12),
                        "hold_s": float(pot_t),
                        "measure": False,
                        "post_delay_s": 0.0,
                        "phase": "pot",
                        "cycle_id": cycle_idx,
                        "elapsed_s": elapsed_s,
                    }
                )
                if settle_t > 0:
                    elapsed_s += float(settle_t)
                    steps.append(
                        {
                            "voltage": 0.0,
                            "hold_s": float(settle_t),
                            "measure": False,
                            "post_delay_s": 0.0,
                            "phase": "settle",
                            "cycle_id": cycle_idx,
                            "elapsed_s": elapsed_s,
                        }
                    )
                read_index += 1
                elapsed_s += float(read_t)
                steps.append(
                    {
                        "voltage": round(read_v, 12),
                        "hold_s": float(read_t),
                        "measure": True,
                        "post_delay_s": float(gap_delay_s),
                        "cycle_id": cycle_idx,
                        "point_t": float(read_index),
                        "phase": "pot",
                        "elapsed_s": elapsed_s,
                    }
                )
                elapsed_s += float(gap_delay_s)
            for _ in range(dep_pulses):
                elapsed_s += float(dep_t)
                steps.append(
                    {
                        "voltage": round(dep_v, 12),
                        "hold_s": float(dep_t),
                        "measure": False,
                        "post_delay_s": 0.0,
                        "phase": "dep",
                        "cycle_id": cycle_idx,
                        "elapsed_s": elapsed_s,
                    }
                )
                if settle_t > 0:
                    elapsed_s += float(settle_t)
                    steps.append(
                        {
                            "voltage": 0.0,
                            "hold_s": float(settle_t),
                            "measure": False,
                            "post_delay_s": 0.0,
                            "phase": "settle",
                            "cycle_id": cycle_idx,
                            "elapsed_s": elapsed_s,
                        }
                    )
                read_index += 1
                elapsed_s += float(read_t)
                steps.append(
                    {
                        "voltage": round(read_v, 12),
                        "hold_s": float(read_t),
                        "measure": True,
                        "post_delay_s": float(gap_delay_s),
                        "cycle_id": cycle_idx,
                        "point_t": float(read_index),
                        "phase": "dep",
                        "elapsed_s": elapsed_s,
                    }
                )
                elapsed_s += float(gap_delay_s)
        return steps

    @staticmethod
    def _build_pd_run_description(
        pot_v: float,
        pot_t: float,
        pot_pulses: int,
        read_v: float,
        read_t: float,
        settle_t: float,
        compliance_uA: float,
        dep_v: float,
        dep_t: float,
        dep_pulses: int,
        gap_delay: float,
        cycles: int,
    ):
        return (
            "PD mode | "
            f"compliance={compliance_uA:g} uA | "
            f"pot_v={pot_v:g} V | pot_t={pot_t:g} s | pot_pulses={pot_pulses} | "
            f"read_v={read_v:g} V | read_t={read_t:g} s | "
            f"stim_to_read_delay_zero_v={settle_t:g} s | "
            f"dep_v={dep_v:g} V | dep_t={dep_t:g} s | dep_pulses={dep_pulses} | "
            f"common_delay_after_read={gap_delay:g} s | cycles={cycles} | "
            "host_timing_recommended_overhead_addition=+0.005 to +0.010 s per transition"
        )

    def _run_next_pd_step(self):
        if self.stop_flag or self._sweep_index >= len(self._pd_steps):
            if not self.stop_flag:
                self.status_text.set("Measurement finished. Output zeroed.")
            self._finish_sweep()
            return

        step = self._pd_steps[self._sweep_index]
        try:
            self.connection.set_voltage(step["voltage"])
            self.last_voltage = step["voltage"]
            hold_ms = max(0, int(round(step["hold_s"] * 1000)))
            if step.get("measure"):
                self.status_text.set(
                    f"PD: read {int(step['point_t'])}/{self._count_pd_reads()} at {step['voltage']:.6g} V"
                )
                self.sweep_after_id = self.root.after(hold_ms, self._complete_pd_read_step)
            else:
                self.status_text.set(
                    f"PD: pulse step {self._sweep_index + 1}/{len(self._pd_steps)} at {step['voltage']:.6g} V"
                )
                self.sweep_after_id = self.root.after(hold_ms, self._complete_pd_pulse_step)
        except Exception as e:
            self._finish_sweep()
            messagebox.showerror("Error", str(e))

    def _complete_pd_pulse_step(self):
        step = self._pd_steps[self._sweep_index]
        self.logger.add(step["voltage"], math.nan, auto_save=self.autosave_enabled)
        self.row_cycle_ids.append(step.get("cycle_id", 0))
        self.row_time_s.append(step.get("elapsed_s"))
        self._annotate_last_row(cycle_id=step.get("cycle_id", 0), point_t=step.get("elapsed_s"))
        self._advance_pd_step()

    def _complete_pd_read_step(self):
        step = self._pd_steps[self._sweep_index]
        try:
            current = self.connection.measure_current()
            self.logger.add(step["voltage"], current, auto_save=self.autosave_enabled)
            self.row_cycle_ids.append(step.get("cycle_id", 0))
            self.row_time_s.append(step.get("elapsed_s"))
            self._annotate_last_row(cycle_id=step.get("cycle_id", 0), point_t=step.get("elapsed_s"))
            self.status_text.set(
                f"PD: read {int(step['point_t'])}/{self._count_pd_reads()} | I={current:.6e} A @ V={step['voltage']:.6g} V"
            )
            self._refresh_embedded_plot()
            self._update_button_states()
            self._advance_pd_step(post_delay_s=step.get("post_delay_s", 0.0))
        except Exception as e:
            self._finish_sweep()
            messagebox.showerror("Error", str(e))

    def _advance_pd_step(self, post_delay_s=0.0):
        if post_delay_s > 0:
            try:
                self.connection.set_voltage(0.0)
                self.last_voltage = 0.0
            except Exception as e:
                self._finish_sweep()
                messagebox.showerror("Error", str(e))
                return
        self._sweep_index += 1
        self.sweep_progress.config(value=self._sweep_index)
        self.progress_text.set(f"Sweep progress: {self._sweep_index}/{len(self._pd_steps)}")
        self._update_eta()
        if self.stop_flag or self._sweep_index >= len(self._pd_steps):
            if not self.stop_flag and self._sweep_index >= len(self._pd_steps):
                self.status_text.set("Measurement finished. Output zeroed.")
            self._finish_sweep()
            return
        delay_ms = max(0, int(round(post_delay_s * 1000)))
        self.sweep_after_id = self.root.after(delay_ms, self._run_next_pd_step)

    def _count_pd_reads(self):
        return sum(1 for step in self._pd_steps if step.get("measure"))

    def sweep_example(self, voltages, cycle_ids=None, point_times=None):
        self.stop_flag = False
        self.sweep_running = True
        self._sweep_values = list(voltages)
        self._sweep_cycle_ids = list(cycle_ids) if cycle_ids else [0] * len(self._sweep_values)
        self._sweep_point_times = list(point_times) if point_times else [None] * len(self._sweep_values)
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
            cycle_id = self._sweep_cycle_ids[self._sweep_index] if self._sweep_index < len(self._sweep_cycle_ids) else 0
            self.row_cycle_ids.append(cycle_id)
            point_t = self._sweep_point_times[self._sweep_index] if self._sweep_index < len(self._sweep_point_times) else None
            self.row_time_s.append(point_t)
            self._annotate_last_row(cycle_id=cycle_id, point_t=point_t)
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

    def _run_fast_instrument_sweep(self, voltages, delay_s, cycle_ids=None, point_times=None):
        self.stop_flag = False
        self.sweep_running = True
        self._sweep_values = list(voltages)
        self._sweep_cycle_ids = list(cycle_ids) if cycle_ids else [0] * len(self._sweep_values)
        self._sweep_point_times = list(point_times) if point_times else [None] * len(self._sweep_values)
        self._sweep_index = 0
        self._sweep_start_time = datetime.now()
        self.sweep_progress.config(maximum=len(self._sweep_values), value=0)
        self.progress_text.set(f"Sweep progress: 0/{len(self._sweep_values)}")
        self.eta_text.set("Elapsed: 00:00 | ETA: --:--")
        self.status_text.set("Fast TSP sweep running on instrument...")
        self._update_button_states()
        self.root.update_idletasks()
        self._fast_sweep_result = None
        self._fast_sweep_error = None
        self._fast_sweep_thread = threading.Thread(
            target=self._fast_sweep_worker,
            args=(list(voltages), delay_s),
            daemon=True,
        )
        self._fast_sweep_thread.start()
        self._fast_sweep_poll_after_id = self.root.after(100, self._poll_fast_sweep_result)

    def _fast_sweep_worker(self, voltages, delay_s):
        try:
            self._fast_sweep_result = self.connection.run_tsp_sweep(voltages, delay_s)
        except Exception as e:
            self._fast_sweep_error = e

    def _poll_fast_sweep_result(self):
        self._fast_sweep_poll_after_id = None
        if getattr(self, "_closing", False):
            return
        if self._fast_sweep_thread and self._fast_sweep_thread.is_alive():
            self._fast_sweep_poll_after_id = self.root.after(100, self._poll_fast_sweep_result)
            return

        pairs = self._fast_sweep_result or []
        error = self._fast_sweep_error
        self._fast_sweep_thread = None
        self._fast_sweep_result = None
        self._fast_sweep_error = None

        try:
            if error is not None:
                messagebox.showerror("Error", str(error))
                return
            if self.stop_flag:
                self.status_text.set("Fast sweep stopped after instrument run completed")
                return
            total = len(pairs)
            for idx, (v, i) in enumerate(pairs, start=1):
                self.last_voltage = v
                self._sync_metadata()
                self.logger.add(v, i, auto_save=self.autosave_enabled)
                cycle_id = self._sweep_cycle_ids[idx - 1] if idx - 1 < len(self._sweep_cycle_ids) else 0
                self.row_cycle_ids.append(cycle_id)
                point_t = self._sweep_point_times[idx - 1] if idx - 1 < len(self._sweep_point_times) else None
                self.row_time_s.append(point_t)
                self._annotate_last_row(cycle_id=cycle_id, point_t=point_t)
                self._sweep_index = idx
                self.sweep_progress.config(value=idx)
                self.progress_text.set(f"Sweep progress: {idx}/{total}")
            self._refresh_embedded_plot()
            self._update_eta()
            self.status_text.set(f"Fast sweep complete: {self._sweep_index} points")
        finally:
            self._finish_sweep()

    def stop(self):
        self.stop_flag = True
        self.live_running = False
        if self.sweep_after_id:
            self.root.after_cancel(self.sweep_after_id)
            self.sweep_after_id = None
        if self.live_after_id:
            self.root.after_cancel(self.live_after_id)
            self.live_after_id = None
        fast_thread_alive = self._fast_sweep_thread and self._fast_sweep_thread.is_alive()
        if fast_thread_alive:
            if not self._fast_sweep_poll_after_id:
                self._fast_sweep_poll_after_id = self.root.after(100, self._poll_fast_sweep_result)
            self.status_text.set("Stop requested. Waiting for instrument fast sweep to finish...")
            self._update_button_states()
            return
        if self._fast_sweep_poll_after_id:
            self.root.after_cancel(self._fast_sweep_poll_after_id)
            self._fast_sweep_poll_after_id = None
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
        self._fast_sweep_poll_after_id = None
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
            filetypes=[("Measurement Files", "*.csv *.txt"), ("CSV Files", "*.csv"), ("Text Files", "*.txt"), ("All Files", "*.*")],
            title="Load Existing Measurement File",
        )
        if not file_path:
            return
        try:
            self.logger.load_csv(file_path)
            self.save_path_text.set(f"Save path: {file_path}")
            self.preferred_save_dir = os.path.dirname(file_path) or self.preferred_save_dir
            self.save_dir_text.set(f"Save folder: {self.preferred_save_dir}")
            self.autosave_enabled = False
            self.autosave_text.set("Auto-save: OFF (loaded data)")
            self.row_cycle_ids = [getattr(row, "cycle_id", 0) for row in self.logger.rows]
            self.row_time_s = [getattr(row, "elapsed_s", None) for row in self.logger.rows]
            row_modes = [getattr(row, "plot_mode", "iv") for row in self.logger.rows]
            if any(mode == "pd" for mode in row_modes):
                loaded_mode = "pd"
            elif any(mode == "wrer" for mode in row_modes) or any(t is not None for t in self.row_time_s):
                loaded_mode = "wrer"
            else:
                loaded_mode = "iv"
            self.active_plot_mode = loaded_mode
            self._last_run_mode = loaded_mode
            self._refresh_embedded_plot()
            self._update_button_states()
            messagebox.showinfo("Loaded", f"Loaded data from:\n{file_path}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def clear_data(self):
        self.logger.clear()
        self.logger.set_run_description("")
        self.row_cycle_ids = []
        self.row_time_s = []
        self.active_plot_mode = "iv"
        self._last_run_mode = "iv"
        self._refresh_embedded_plot()
        self._update_button_states()
        self.status_text.set("Data cleared")

    def plot_iv_popup(self):
        if not self.logger.rows:
            messagebox.showinfo("Plot", "No data to plot")
            return
        self._sync_active_plot_mode_from_data()
        if self.active_plot_mode == "pd":
            iv, vy, ii, iy = self._build_pd_plot_series()
            if not iv:
                messagebox.showinfo("Plot", "No PD time data to plot")
                return
            _, yscale = self._get_axis_scales()
            x_right = max(iv) if iv else 0.0
            if x_right <= 0:
                x_right = 1.0
            IVPlotter.show_time_series(
                times=iv,
                voltages=vy,
                current_times=ii,
                currents=iy,
                title=self._current_plot_title(),
                current_yscale=yscale,
                xlim=(0.0, x_right),
                xlabel="Index",
                current_linestyle="None",
                current_use_abs=True,
                current_ylabel="|Current| (A)",
                voltage_linestyle="None",
            )
            return
        if self.active_plot_mode == "wrer":
            tx, vy, iy = self._build_wrer_plot_series()
            if not tx:
                messagebox.showinfo("Plot", "No WRER time data to plot")
                return
            _, yscale = self._get_axis_scales()
            x_right = max(tx) if tx else 0.0
            if x_right <= 0:
                x_right = 1.0
            IVPlotter.show_time_series(
                times=tx,
                voltages=vy,
                currents=iy,
                title=self._current_plot_title(),
                current_yscale=yscale,
                xlim=(0.0, x_right),
            )
            return
        xscale, yscale = self._get_axis_scales()
        cycle_series = self._build_cycle_series(xscale, yscale)
        has_points = any(series["x"] for series in cycle_series.values())
        if not has_points:
            messagebox.showinfo("Plot", "No plottable points for selected log axis (needs positive values)")
            return
        plot_series = []
        for idx, (cycle_id, series) in enumerate(cycle_series.items(), start=1):
            if not series["x"]:
                continue
            label = f"Cycle {cycle_id}" if cycle_id > 0 else None
            plot_series.append(
                {
                    "x": series["x"],
                    "y": series["y"],
                    "label": label,
                    "color_index": idx - 1,
                }
            )
        IVPlotter.show(
            xscale=xscale,
            yscale=yscale,
            title=self._current_plot_title(),
            cycle_series=plot_series,
        )

    def _refresh_embedded_plot(self):
        self._sync_active_plot_mode_from_data()
        if self.active_plot_mode == "pd":
            iv, vy, ii, iy = self._build_pd_plot_series()
            _, yscale = self._get_axis_scales()
            self.figure.clear()
            self.ax = None
            ax_v = self.figure.add_subplot(211)
            ax_i = self.figure.add_subplot(212, sharex=ax_v)
            ax_v.grid(True)
            ax_i.grid(True)
            ax_v.plot(iv, vy, linestyle="None", marker="o")
            if yscale == "log":
                tx_i, iy_i = IVPlotter.prepare_log_y_data(ii, iy)
                ax_i.set_yscale("log")
                ax_i.plot(tx_i, iy_i, linestyle="None", marker="o")
            else:
                ax_i.set_yscale("linear")
                ax_i.plot(ii, [abs(y) for y in iy], linestyle="None", marker="o")
            ax_v.set_ylabel("Voltage (V)")
            ax_i.set_ylabel("|Current| (A)")
            ax_i.set_xlabel("Index")
            ax_v.set_title(self._current_plot_title())
            x_right = max(iv) if iv else 0.0
            if x_right <= 0:
                x_right = 1.0
            ax_v.set_xlim(left=0.0, right=x_right)
            self.canvas.draw_idle()
            return
        if self.active_plot_mode == "wrer":
            tx, vy, iy = self._build_wrer_plot_series()
            _, yscale = self._get_axis_scales()
            self.figure.clear()
            # Re-assign self.ax to None since it's destroyed, and create the two new axes
            self.ax = None
            ax_v = self.figure.add_subplot(211)
            ax_i = self.figure.add_subplot(212, sharex=ax_v)
            ax_v.grid(True)
            ax_i.grid(True)
            ax_v.plot(tx, vy, linestyle="-", marker="o")
            if yscale == "log":
                tx_i, iy_i = IVPlotter.prepare_log_y_data(tx, iy)
                ax_i.set_yscale("log")
                ax_i.plot(tx_i, iy_i, linestyle="-", marker="o")
            else:
                ax_i.set_yscale("linear")
                ax_i.plot(tx, iy, linestyle="-", marker="o")
            ax_v.set_ylabel("Voltage (V)")
            ax_i.set_ylabel("Current (A)")
            ax_i.set_xlabel("Time (s)")
            ax_v.set_title(self._current_plot_title())
            x_right = max(tx) if tx else 0.0
            if x_right <= 0:
                x_right = 1.0
            ax_v.set_xlim(left=0.0, right=x_right)
            self.canvas.draw_idle()
            return
        xscale, yscale = self._get_axis_scales()
        cycle_series = self._build_cycle_series(xscale, yscale)

        # If we switched from WRER, the single axis `self.ax` was destroyed. Recreate it.
        if not self.ax or not self.figure.axes or len(self.figure.axes) != 1:
            self.figure.clear()
            self.ax = self.figure.add_subplot(111)
        else:
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
        self.ax.set_title(self._current_plot_title())
        cmap = cm.get_cmap("tab20")
        for idx, (cycle_id, series) in enumerate(cycle_series.items()):
            if not series["x"]:
                continue
            label = f"Cycle {cycle_id}" if cycle_id > 0 else None
            self.ax.plot(
                series["x"],
                series["y"],
                marker="o",
                linestyle="-",
                color=cmap(idx % 20),
                label=label,
            )
        if any(cycle_id > 0 and series["x"] for cycle_id, series in cycle_series.items()):
            self.ax.legend(loc="best")
        self.canvas.draw_idle()

    def _get_axis_scales(self):
        xscale = "log" if self.x_axis_scale_combo.get().lower() == "log" else "linear"
        yscale = "log" if self.y_axis_scale_combo.get().lower() == "log" else "linear"
        return xscale, yscale

    def _current_plot_title(self):
        if self.active_plot_mode == "pd":
            if self.logger.rows:
                stored_description = (getattr(self.logger.rows[0], "run_description", "") or "").strip()
                if stored_description:
                    return stored_description.split(" | ", 1)[0].strip()
            return self._pd_graph_title()
        if self.logger.output_file:
            return f"I-V Data - {self.logger.output_file.name}"
        return "Live I-V Data"

    def _infer_plot_mode_from_data(self):
        rows_len = len(self.logger.rows)
        if rows_len == 0:
            return self._selected_sweep_plot_mode()
        row_modes = [getattr(row, "plot_mode", "") for row in self.logger.rows]
        if any(mode == "pd" for mode in row_modes):
            return "pd"
        if any(mode == "wrer" for mode in row_modes):
            return "wrer"
        times = list(self.row_time_s)
        if len(times) < rows_len:
            times.extend([None] * (rows_len - len(times)))
        elif len(times) > rows_len:
            times = times[:rows_len]
        return "wrer" if any(t is not None for t in times) else "iv"

    def _selected_sweep_plot_mode(self):
        try:
            if hasattr(self, "sweep_subtabs"):
                current_idx = self.sweep_subtabs.index(self.sweep_subtabs.select())
                if current_idx == 2:
                    return "wrer"
                if current_idx == 3:
                    return "pd"
                return "iv"
        except Exception:
            pass
        return self._last_run_mode if self._last_run_mode in ("iv", "wrer", "pd") else "iv"

    def _sync_active_plot_mode_from_data(self):
        inferred = self._infer_plot_mode_from_data()
        if inferred in ("iv", "wrer", "pd"):
            self.active_plot_mode = inferred

    def _build_pd_plot_series(self):
        iv = []
        vy = []
        ii = []
        iy = []
        for idx, row in enumerate(self.logger.rows):
            x_value = float(idx + 1)
            iv.append(x_value)
            vy.append(row.voltage)
            if isinstance(row.current, (int, float)) and math.isfinite(row.current):
                ii.append(x_value)
                iy.append(row.current)
        return iv, vy, ii, iy

    def _build_cycle_series(self, xscale, yscale):
        cycle_ids = list(self.row_cycle_ids)
        rows_len = len(self.logger.rows)
        if len(cycle_ids) < rows_len:
            cycle_ids.extend([0] * (rows_len - len(cycle_ids)))
        elif len(cycle_ids) > rows_len:
            cycle_ids = cycle_ids[:rows_len]

        series = {}
        for row, cycle_id in zip(self.logger.rows, cycle_ids):
            voltage = row.voltage
            current = row.current
            if xscale == "log" and voltage <= 0:
                continue
            if yscale == "log":
                current = abs(current)
                if current == 0:
                    continue
            if cycle_id not in series:
                series[cycle_id] = {"x": [], "y": []}
            series[cycle_id]["x"].append(voltage)
            series[cycle_id]["y"].append(current)
        return series

    def _build_wrer_plot_series(self):
        times = list(self.row_time_s)
        rows_len = len(self.logger.rows)
        if len(times) < rows_len:
            times.extend([None] * (rows_len - len(times)))
        elif len(times) > rows_len:
            times = times[:rows_len]
        tx = []
        vy = []
        iy = []
        for idx, row in enumerate(self.logger.rows):
            t = times[idx]
            if t is None:
                t = float(idx)
            tx.append(float(t))
            vy.append(row.voltage)
            iy.append(row.current)
        return tx, vy, iy

    def _sync_metadata(self):
        pd_sample = ""
        if self._selected_sweep_plot_mode() == "pd" and hasattr(self, "pd_sample_entry"):
            pd_sample = self.pd_sample_entry.get().strip()
        if pd_sample:
            self.sample_entry.delete(0, tk.END)
            self.sample_entry.insert(0, pd_sample)
        self.logger.set_metadata(
            sample_name=self.sample_entry.get(),
            operator=self.operator_entry.get(),
            notes=self.notes_entry.get(),
        )

    def _pd_graph_title(self):
        test_no = self.pd_test_no_entry.get().strip() if hasattr(self, "pd_test_no_entry") else ""
        sample_name = self.pd_sample_entry.get().strip() if hasattr(self, "pd_sample_entry") else self.sample_entry.get().strip()
        electrode_sign = self.pd_electrode_entry.get().strip() if hasattr(self, "pd_electrode_entry") else ""
        electrode_no = self.pd_electrode_no_entry.get().strip() if hasattr(self, "pd_electrode_no_entry") else ""
        pot_v = self.pd_pot_v_entry.get().strip() if hasattr(self, "pd_pot_v_entry") else ""
        dep_v = self.pd_dep_v_entry.get().strip() if hasattr(self, "pd_dep_v_entry") else ""
        read_v = self.pd_read_v_entry.get().strip() if hasattr(self, "pd_read_v_entry") else ""
        date_text = datetime.now().strftime("%Y%m%d")
        parts = [
            f"Test{test_no or '00'}",
            "I-V data",
            "PD",
            f"P({pot_v or '?'} V)",
            f"D({dep_v or '?'} V)",
            f"R({read_v or '?'} V)",
        ]
        if sample_name:
            parts.append(sample_name)
        if electrode_sign:
            parts.append(electrode_sign)
        if electrode_no:
            parts.append(electrode_no)
        parts.append(date_text)
        return "_".join(parts)

    def _pd_output_filename(self):
        name = re.sub(r'[<>:"/\\|?*\s]+', "_", self._pd_graph_title()).strip("._")
        return f"{name or 'pd_run'}.txt"

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
        self._closing = True
        self._save_ui_settings()
        self.stop()
        if self._fast_sweep_thread and self._fast_sweep_thread.is_alive():
            self.status_text.set("Closing after fast sweep completes...")
            self._wait_for_fast_sweep_then_close()
            return
        self.connection.close()
        self.root.destroy()

    def _wait_for_fast_sweep_then_close(self):
        if self._fast_sweep_thread and self._fast_sweep_thread.is_alive():
            self._close_wait_after_id = self.root.after(100, self._wait_for_fast_sweep_then_close)
            return
        self._close_wait_after_id = None
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
    def _build_simple_cycle_values_with_cycles(cls, peak_v: float, step: float, cycles: int):
        step_mag = abs(step)
        peak = abs(peak_v)
        if step_mag == 0 or cycles < 1:
            return [], []
        if peak == 0:
            return [0.0], [1]

        seg1 = cls._build_sweep_values(0.0, peak, step_mag)
        seg2 = cls._build_sweep_values(peak, 0.0, -step_mag)
        seg3 = cls._build_sweep_values(0.0, -peak, -step_mag)
        seg4 = cls._build_sweep_values(-peak, 0.0, step_mag)
        if not seg1 or not seg2 or not seg3 or not seg4:
            return [], []

        single = seg1 + seg2[1:] + seg3[1:] + seg4[1:]
        values = list(single)
        cycle_ids = [1] * len(single)
        for cycle_idx in range(2, cycles + 1):
            values.extend(single[1:])
            cycle_ids.extend([cycle_idx] * (len(single) - 1))
        return values, cycle_ids

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
