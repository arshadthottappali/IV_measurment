import unittest
from types import SimpleNamespace

from gui import KeithleyUI
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


if __name__ == "__main__":
    unittest.main()
