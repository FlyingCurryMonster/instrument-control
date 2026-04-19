import sys
from typing import Dict

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import PlotWidget
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import Parameter, Results, unique_filename

from fsweep_zurich_select_pts_mod_dual_sideband import DualSidebandFrequencySweepProcedure


class DualSidebandFrequencySweepV12Procedure(DualSidebandFrequencySweepProcedure):
    """Sweep fc+fm2 while holding both fc and fm1 fixed."""

    file_prefix = Parameter(
        "File prefix",
        default="dual_sideband_frequency_sweep_v12_fixed_carrier",
    )

    def _state_for_sum_target(self, sum_target: float) -> Dict[str, float]:
        f_c = float(self.initial_carrier_frequency)
        f_m1 = f_c - float(self.fixed_sideband1_diff_target)
        f_m2 = float(sum_target) - f_c
        self._validate_frequency_state(f_c, f_m1, f_m2, context=f"sum target {sum_target:.10f}")
        return {"f_c": float(f_c), "f_m1": float(f_m1), "f_m2": float(f_m2)}

    def _initial_state(self) -> Dict[str, float]:
        return self._state_for_sum_target(float(self.resonance_pt))


class DualSidebandFrequencySweepV12Window(ManagedDockWindow):
    def __init__(self):
        carrier_plot = PlotWidget(
            name="Carrier Response",
            columns=DualSidebandFrequencySweepV12Procedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="carrier_R",
        )
        sideband1_plot = PlotWidget(
            name="Fixed Sideband 1 Response",
            columns=DualSidebandFrequencySweepV12Procedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband1_R",
        )
        sideband2_plot = PlotWidget(
            name="Swept Sideband 2 Response",
            columns=DualSidebandFrequencySweepV12Procedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband2_R",
        )
        carrier_phase_plot = PlotWidget(
            name="Carrier Phase",
            columns=DualSidebandFrequencySweepV12Procedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="carrier_phase",
        )
        sideband1_phase_plot = PlotWidget(
            name="Fixed Sideband 1 Phase",
            columns=DualSidebandFrequencySweepV12Procedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband1_phase",
        )
        sideband2_phase_plot = PlotWidget(
            name="Swept Sideband 2 Phase",
            columns=DualSidebandFrequencySweepV12Procedure.DATA_COLUMNS,
            x_axis="f_demod_sideband2_sum_readback",
            y_axis="sideband2_phase",
        )

        super().__init__(
            procedure_class=DualSidebandFrequencySweepV12Procedure,
            inputs=DualSidebandFrequencySweepV12Procedure.PARAMETERS,
            displays=DualSidebandFrequencySweepV12Procedure.PARAMETERS,
            x_axis=["f_demod_sideband2_sum_readback"],
            y_axis=[
                "carrier_R",
                "sideband1_R",
                "sideband2_R",
                "carrier_phase",
                "sideband1_phase",
                "sideband2_phase",
            ],
            widget_list=(
                carrier_plot,
                sideband1_plot,
                sideband2_plot,
                carrier_phase_plot,
                sideband1_phase_plot,
                sideband2_phase_plot,
            ),
            inputs_in_scrollarea=True,
        )
        self.setWindowTitle("Zurich Dual Sideband Frequency Sweep V12 Fixed Carrier")
        self.directory = r"D:/Data/Fall25-Summer26/TO freq-sweeps"

    def queue(self):
        directory = self.directory
        procedure = self.make_procedure()
        prefix = f"{procedure.file_prefix}_" if procedure.file_prefix else ""
        filename = unique_filename(directory, prefix=prefix)
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == "__main__":
    app = QtWidgets.QApplication([])
    window = DualSidebandFrequencySweepV12Window()
    window.show()
    sys.exit(app.exec_())
