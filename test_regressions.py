import unittest
from types import SimpleNamespace

from data_logging import Measurement
from gui import KeithleyUI
from connection import KeithleyConnection
from plotting import IVPlotter


class _DummyWidget:
    def __init__(self):
        self._cfg = {}

    def config(self, **kwargs):
        self._cfg.update(kwargs)

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


class _DummyThread:
    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


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
            "preview_wrer_btn",
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

    def test_stop_defers_finish_while_fast_sweep_thread_is_running(self):
        ui = KeithleyUI.__new__(KeithleyUI)
        ui.stop_flag = False
        ui.live_running = True
        ui.sweep_after_id = None
        ui.live_after_id = None
        ui._fast_sweep_thread = _DummyThread(alive=True)
        ui.status_text = _DummyTextVar()
        ui._update_button_states = lambda: None
        ui._finish_sweep = lambda **_kwargs: self.fail("_finish_sweep should not run while fast thread is active")
        ui.connection = SimpleNamespace(zero_output=lambda: self.fail("zero_output should be deferred"))

        ui.stop()

        self.assertTrue(ui.stop_flag)
        self.assertFalse(ui.live_running)
        self.assertIn("Stop requested", ui.status_text.get())


if __name__ == "__main__":
    unittest.main()
