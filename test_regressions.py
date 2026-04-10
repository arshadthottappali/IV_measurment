from pathlib import Path
import unittest
from types import SimpleNamespace

from data_logging import DataLogger, Measurement
from gui import KeithleyUI
from connection import KeithleyConnection
from plotting import IVPlotter


class _DummyWidget:
    def __init__(self):
        self._cfg = {}

    def config(self, **kwargs):
        self._cfg.update(kwargs)

    def grid(self, **kwargs):
        self._cfg["grid_visible"] = True
        self._cfg.update(kwargs)

    def grid_remove(self):
        self._cfg["grid_visible"] = False

    def get(self, key, default=None):
        return self._cfg.get(key, default)


class _DummyTabs:
    def __init__(self, idx):
        self._idx = idx

    def select(self):
        return "tab"

    def index(self, _token):
        return self._idx

    def set_index(self, idx):
        self._idx = idx


class _DummyTextVar:
    def __init__(self):
        self.value = ""

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class _DummyEntry:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def delete(self, _start, _end):
        self.value = ""

    def insert(self, _index, value):
        self.value = str(value)


class _DummyThread:
    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


class _DummyRoot:
    def __init__(self):
        self.cancelled = []
        self.destroyed = False
        self.after_calls = []
        self._next_token = 0

    def after_cancel(self, token):
        self.cancelled.append(token)

    def after(self, delay_ms, callback):
        token = f"after-{self._next_token}"
        self._next_token += 1
        self.after_calls.append((token, delay_ms, callback))
        return token

    def destroy(self):
        self.destroyed = True


class KeithleyUIRegressions(unittest.TestCase):
    def _make_ui_for_state_update(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.connected = True
        ui.live_running = False
        ui.sweep_running = False
        ui.logger = SimpleNamespace(rows=[])

        for name in [
            "apply_voltage_btn",
            "measure_btn",
            "apply_compliance_btn",
            "start_live_btn",
            "run_sweep_btn",
            "run_custom_sweep_btn",
            "run_wrer_btn",
            "run_pd_btn",
            "preview_wrer_btn",
            "preview_pd_btn",
            "sweep_stop_btn",
            "stop_btn",
            "plot_btn",
            "save_btn",
            "choose_folder_btn",
            "load_btn",
            "open_folder_btn",
            "clear_data_btn",
            "connect_btn",
            "sample_rate",
        ]:
            setattr(ui, name, _DummyWidget())
        return ui

    def test_tab_switch_does_not_change_active_plot_mode(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.sweep_subtabs = _DummyTabs(idx=2)
        ui.step_label = _DummyWidget()
        ui.sweep_step_entry = _DummyWidget()
        ui.delay_label = _DummyWidget()
        ui.sweep_delay_entry = _DummyWidget()
        ui._refresh_embedded_plot = lambda: None

        ui.active_plot_mode = "iv"
        ui._on_sweep_subtab_changed()
        self.assertEqual(ui.active_plot_mode, "iv")
        self.assertEqual(ui.step_label.get("text"), "Step (V) [unused in WRER]")
        self.assertEqual(ui.sweep_step_entry.get("state"), "disabled")

        ui.sweep_subtabs.set_index(0)
        ui.active_plot_mode = "wrer"
        ui._on_sweep_subtab_changed()
        self.assertEqual(ui.active_plot_mode, "wrer")
        self.assertEqual(ui.step_label.get("text"), "Step (V)")
        self.assertEqual(ui.sweep_step_entry.get("state"), "normal")

    def test_empty_data_mode_follows_selected_tab(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.logger = SimpleNamespace(rows=[])
        ui.row_time_s = []
        ui._last_run_mode = "iv"
        ui.sweep_subtabs = _DummyTabs(idx=2)
        self.assertEqual(ui._infer_plot_mode_from_data(), "wrer")
        ui.sweep_subtabs.set_index(3)
        self.assertEqual(ui._infer_plot_mode_from_data(), "pd")
        ui.sweep_subtabs.set_index(0)
        self.assertEqual(ui._infer_plot_mode_from_data(), "iv")

    def test_preview_button_disabled_while_running(self):
        ui = self._make_ui_for_state_update()

        ui.live_running = True
        ui._update_button_states()
        self.assertEqual(ui.preview_wrer_btn.get("state"), "disabled")

        ui.live_running = False
        ui.sweep_running = False
        ui._update_button_states()
        self.assertEqual(ui.preview_wrer_btn.get("state"), "normal")

    def test_prepare_log_y_data_keeps_xy_alignment(self):
        x, y = IVPlotter.prepare_log_y_data([1, 2, 3, 4], [0, -4, 5, 0])
        self.assertEqual(x, [2, 3])
        self.assertEqual(y, [4, 5])

    def test_annotate_last_row_persists_auxiliary_series_metadata(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.logger = SimpleNamespace(rows=[Measurement("2026-03-11T12:00:00", 1.0, 2.0e-6)])

        ui._annotate_last_row(cycle_id=3, point_t=1.25)

        self.assertEqual(ui.logger.rows[-1].cycle_id, 3)
        self.assertEqual(ui.logger.rows[-1].elapsed_s, 1.25)

    def test_loaded_wrer_data_restores_plot_metadata_and_disables_autosave(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.logger = SimpleNamespace(
            rows=[
                Measurement("2026-03-11T12:00:00", 1.0, 2.0e-6, elapsed_s=0.0, cycle_id=1),
                Measurement("2026-03-11T12:00:01", 0.1, 1.0e-6, elapsed_s=1.0, cycle_id=1),
            ],
            load_csv=lambda _path: None,
        )
        ui.preferred_save_dir = "C:\\data"
        ui.autosave_enabled = True
        ui.save_path_text = _DummyTextVar()
        ui.save_dir_text = _DummyTextVar()
        ui.autosave_text = _DummyTextVar()
        ui._refresh_embedded_plot = lambda: None
        ui._update_button_states = lambda: None

        import gui as gui_module

        original_dialog = gui_module.filedialog.askopenfilename
        original_info = gui_module.messagebox.showinfo
        original_error = gui_module.messagebox.showerror
        try:
            gui_module.filedialog.askopenfilename = lambda **_kwargs: "C:\\data\\wrer.csv"
            gui_module.messagebox.showinfo = lambda *_args, **_kwargs: None
            gui_module.messagebox.showerror = lambda *_args, **_kwargs: None
            ui.load_csv_data()
        finally:
            gui_module.filedialog.askopenfilename = original_dialog
            gui_module.messagebox.showinfo = original_info
            gui_module.messagebox.showerror = original_error

        self.assertFalse(ui.autosave_enabled)
        self.assertEqual(ui.row_cycle_ids, [1, 1])
        self.assertEqual(ui.row_time_s, [0.0, 1.0])
        self.assertEqual(ui.active_plot_mode, "wrer")
        self.assertEqual(ui._last_run_mode, "wrer")

    def test_fast_tsp_detection_accepts_other_2600_models(self):
        self.assertTrue(KeithleyConnection._looks_like_2600_tsp_model("KEITHLEY,MODEL 2612B,1234,1.0"))
        self.assertTrue(KeithleyConnection._looks_like_2600_tsp_model("KEITHLEY,2601A,TSP"))
        self.assertFalse(KeithleyConnection._looks_like_2600_tsp_model("KEITHLEY,2450,1234,1.0"))

    def test_build_pd_steps_creates_read_after_each_program_pulse(self):
        steps = KeithleyUI._build_pd_steps_with_cycles(
            pot_v=1.5,
            pot_t=0.1,
            pot_pulses=2,
            read_v=0.2,
            read_t=0.05,
            settle_t=0.01,
            dep_v=-1.2,
            dep_t=0.1,
            dep_pulses=1,
            gap_delay_s=0.02,
            cycles=2,
        )

        read_steps = [step for step in steps if step.get("measure")]
        settle_steps = [step for step in steps if step.get("phase") == "settle"]
        self.assertEqual(len(steps), 18)
        self.assertEqual(len(settle_steps), 6)
        self.assertEqual([step["phase"] for step in read_steps], ["pot", "pot", "dep", "pot", "pot", "dep"])
        self.assertEqual([step["point_t"] for step in read_steps], [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
        self.assertTrue(all(step["voltage"] == 0.2 for step in read_steps))
        self.assertAlmostEqual(read_steps[0]["elapsed_s"], 0.16)

    def test_pd_plot_series_keeps_voltage_steps_and_only_measured_currents(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.logger = SimpleNamespace(
            rows=[
                Measurement("2026-04-02T12:00:00", 1.0, float("nan"), plot_mode="pd"),
                Measurement("2026-04-02T12:00:01", 0.0, float("nan"), plot_mode="pd"),
                Measurement("2026-04-02T12:00:02", 0.1, 2.5e-6, plot_mode="pd"),
            ]
        )
        ui.row_time_s = [0.10, 0.11, 0.16]

        iv, vy, ii, iy = ui._build_pd_plot_series()

        self.assertEqual(iv, [1.0, 2.0, 3.0])
        self.assertEqual(vy, [1.0, 0.0, 0.1])
        self.assertEqual(ii, [3.0])
        self.assertEqual(iy, [2.5e-6])

    def test_custom_sequence_keeps_single_point_segments_and_repeats_cycles(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.custom_segments = [(0.0, 0.0), (1.0, 1.0), (0.0, 0.0)]
        ui._build_sweep_values = KeithleyUI._build_sweep_values

        values, cycle_ids = ui._build_custom_sequence_values_with_cycles(step=1.0, cycles=5)

        self.assertEqual(values, [0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0])
        self.assertEqual(cycle_ids, [1, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5])

    def test_stop_defers_finish_while_fast_sweep_thread_is_running(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.stop_flag = False
        ui.live_running = True
        ui.sweep_after_id = None
        ui.live_after_id = None
        ui.root = _DummyRoot()
        ui._fast_sweep_poll_after_id = "poll-1"
        ui._fast_sweep_thread = _DummyThread(alive=True)
        ui.status_text = _DummyTextVar()
        ui._update_button_states = lambda: None
        ui._finish_sweep = lambda **_kwargs: self.fail("_finish_sweep should not run while fast thread is active")
        ui.connection = SimpleNamespace(zero_output=lambda: self.fail("zero_output should be deferred"))

        ui.stop()

        self.assertTrue(ui.stop_flag)
        self.assertFalse(ui.live_running)
        self.assertIn("Stop requested", ui.status_text.get())
        self.assertEqual(ui.root.cancelled, [])
        self.assertEqual(ui._fast_sweep_poll_after_id, "poll-1")

    def test_pd_txt_round_trip_and_append_numbering(self):
        path = Path("pd_test_round_trip_tmp.txt")
        try:
            path.unlink(missing_ok=True)
            logger = DataLogger()
            logger.set_run_description("Test01_I-V data_PD_P(1 V)_D(-1 V)_R(0.1 V)_S1_E+_1_20260403")
            logger.set_output_file(str(path), reset_file=True)
            logger.rows = []
            logger.add(0.1, 1.2e-6, auto_save=False)
            logger.rows[-1].plot_mode = "pd"
            logger.add(0.1, float("nan"), auto_save=False)
            logger.rows[-1].plot_mode = "pd"
            logger.add(0.1, 2.5e-6, auto_save=False)
            logger.rows[-1].plot_mode = "pd"
            logger.save_csv()

            loaded = DataLogger()
            loaded.load_csv(str(path))
            self.assertEqual(len(loaded.rows), 2)
            self.assertEqual(loaded.rows[0].plot_mode, "pd")
            self.assertEqual(loaded.rows[0].elapsed_s, 1.0)
            self.assertEqual(loaded.rows[1].elapsed_s, 2.0)
            self.assertIn("Test01_I-V data_PD", loaded.rows[0].run_description)

            append_logger = DataLogger()
            append_logger.set_run_description("Test01_I-V data_PD_P(1 V)_D(-1 V)_R(0.1 V)_S1_E+_1_20260403")
            append_logger.set_output_file(str(path), reset_file=False)
            append_logger.add(0.1, 3.3e-6, auto_save=False)
            append_logger.rows[-1].plot_mode = "pd"
            append_logger._append_row(append_logger.rows[-1])

            lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(lines[-1].split()[0], "3")
        finally:
            path.unlink(missing_ok=True)

    def test_sync_metadata_uses_pd_sample_only_in_pd_mode(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.sample_entry = _DummyEntry("base-sample")
        ui.pd_sample_entry = _DummyEntry("pd-sample")
        ui.operator_entry = _DummyEntry("op")
        ui.notes_entry = _DummyEntry("notes")
        ui.logger = DataLogger()
        ui._selected_sweep_plot_mode = lambda: "iv"

        ui._sync_metadata()

        self.assertEqual(ui.logger.sample_name, "base-sample")

    def test_current_plot_title_uses_stored_pd_description_when_loaded(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.active_plot_mode = "pd"
        ui.logger = SimpleNamespace(
            rows=[
                Measurement(
                    timestamp="",
                    voltage=0.1,
                    current=1e-6,
                    plot_mode="pd",
                    run_description="Test09_I-V data_PD_P(1 V)_D(-1 V)_R(0.1 V)_S1_E+_2_20260403 | PD mode | compliance=1 uA",
                )
            ],
            output_file=None,
        )

        self.assertEqual(
            ui._current_plot_title(),
            "Test09_I-V data_PD_P(1 V)_D(-1 V)_R(0.1 V)_S1_E+_2_20260403",
        )

    def test_load_dialog_allows_pd_text_files(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.logger = SimpleNamespace(rows=[], load_csv=lambda _path: None)
        ui.preferred_save_dir = "C:\\data"
        ui.autosave_enabled = True
        ui.save_path_text = _DummyTextVar()
        ui.save_dir_text = _DummyTextVar()
        ui.autosave_text = _DummyTextVar()
        ui._refresh_embedded_plot = lambda: None
        ui._update_button_states = lambda: None

        import gui as gui_module

        captured = {}
        original_dialog = gui_module.filedialog.askopenfilename
        original_info = gui_module.messagebox.showinfo
        original_error = gui_module.messagebox.showerror
        try:
            def fake_dialog(**kwargs):
                captured.update(kwargs)
                return ""

            gui_module.filedialog.askopenfilename = fake_dialog
            gui_module.messagebox.showinfo = lambda *_args, **_kwargs: None
            gui_module.messagebox.showerror = lambda *_args, **_kwargs: None
            ui.load_csv_data()
        finally:
            gui_module.filedialog.askopenfilename = original_dialog
            gui_module.messagebox.showinfo = original_info
            gui_module.messagebox.showerror = original_error

        self.assertEqual(captured["title"], "Load Existing Measurement File")
        self.assertIn(("Measurement Files", "*.csv *.txt"), captured["filetypes"])

    def test_on_close_defers_connection_close_while_fast_thread_alive(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.root = _DummyRoot()
        ui._fast_sweep_thread = _DummyThread(alive=True)
        ui._fast_sweep_poll_after_id = None
        ui.status_text = _DummyTextVar()
        ui._save_ui_settings = lambda: None
        ui.stop = lambda: None
        close_called = {"value": False}
        ui.connection = SimpleNamespace(close=lambda: close_called.__setitem__("value", True))

        ui.on_close()

        self.assertTrue(ui._closing)
        self.assertFalse(ui.root.destroyed)
        self.assertFalse(close_called["value"])
        self.assertEqual(len(ui.root.after_calls), 1)
        token, _delay_ms, callback = ui.root.after_calls[0]
        self.assertEqual(ui._close_wait_after_id, token)

        ui._fast_sweep_thread = _DummyThread(alive=False)
        callback()
        self.assertTrue(close_called["value"])
        self.assertTrue(ui.root.destroyed)


if __name__ == "__main__":
    unittest.main()
