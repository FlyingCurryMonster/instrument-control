import sys
from typing import Dict

from pymeasure.display.Qt import QtWidgets
from pymeasure.display.widgets import PlotWidget
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.experiment import FloatParameter, IntegerParameter, Parameter, Results, unique_filename

from fsweep_zurich_select_pts_mod_dual_sideband import (
    DualSidebandFrequencySweepProcedure,
    HighPrecisionScientificInput,
)


class DualSidebandFrequencySweepV12Procedure(DualSidebandFrequencySweepProcedure):
    """Sweep fc+fm2 while phase-locking the symmetric fc-fm1 response."""

    file_prefix = Parameter(
        "File prefix",
        default="dual_sideband_frequency_sweep_v12_fixed_carrier",
    )
    symmetric_linewidth = FloatParameter(
        "Symmetric linewidth",
        units="Hz",
        default=1.47e-3,
        decimals=10,
        ui_class=HighPrecisionScientificInput,
    )
    symmetric_phase_band = FloatParameter("Symmetric phase band", units="deg", default=6.0)
    symmetric_max_retune_iterations = IntegerParameter(
        "Symmetric max retune iterations",
        default=3,
    )
    symmetric_delay = FloatParameter("Symmetric delay", units="s", default=1000.0)

    PARAMETERS = DualSidebandFrequencySweepProcedure.PARAMETERS + [
        "symmetric_linewidth",
        "symmetric_phase_band",
        "symmetric_max_retune_iterations",
        "symmetric_delay",
    ]

    DATA_COLUMNS = DualSidebandFrequencySweepProcedure.DATA_COLUMNS + [
        "sweep_point_index",
        "symmetric_sample_index",
        "symmetric_phase_initial",
        "symmetric_phase_current",
        "symmetric_in_band",
        "symmetric_is_final",
        "symmetric_retuned",
        "symmetric_retune_iterations",
        "symmetric_frequency_correction",
    ]

    def startup(self):
        self.current_sideband1_diff_target = float(self.fixed_sideband1_diff_target)
        super().startup()

    def execute(self):
        for i, sum_target in enumerate(self.sum_target_points):
            if self.should_stop():
                break

            state = self._state_for_sum_target(float(sum_target))
            self._apply_sideband2_frequency(state["f_m2"])
            if not self._sleep_with_abort(float(self.delay)):
                break

            completed = self._measure_emit_and_retune_symmetric(
                sweep_point_index=i,
                sum_target=float(sum_target),
            )
            if not completed:
                break
            self.emit("progress", 100 * (i + 1) / len(self.sum_target_points))

    def _validate_parameters(self) -> None:
        super()._validate_parameters()
        if self.symmetric_linewidth <= 0:
            raise ValueError("Symmetric linewidth must be > 0.")
        if self.symmetric_phase_band < 0:
            raise ValueError("Symmetric phase band must be >= 0.")
        if self.symmetric_max_retune_iterations < 0:
            raise ValueError("Symmetric max retune iterations must be >= 0.")
        if self.symmetric_delay < 0:
            raise ValueError("Symmetric delay must be >= 0.")

    def _state_for_sum_target(self, sum_target: float) -> Dict[str, float]:
        f_c = float(self.initial_carrier_frequency)
        sideband1_target = float(
            getattr(self, "current_sideband1_diff_target", self.fixed_sideband1_diff_target)
        )
        f_m1 = f_c - sideband1_target
        f_m2 = float(sum_target) - f_c
        self._validate_frequency_state(f_c, f_m1, f_m2, context=f"sum target {sum_target:.10f}")
        return {"f_c": float(f_c), "f_m1": float(f_m1), "f_m2": float(f_m2)}

    def _initial_state(self) -> Dict[str, float]:
        return self._state_for_sum_target(float(self.resonance_pt))

    def _apply_sideband2_frequency(self, f_m2: float) -> None:
        self._validate_frequency_state(
            float(self.initial_carrier_frequency),
            self._sideband1_osc_freq_for_current_target(),
            float(f_m2),
            context="sideband 2 sweep point",
        )
        self.daq.setDouble(self._freq_path(self.sideband2_osc_index), float(f_m2))
        self.daq.sync()

    def _apply_current_sideband1_target(self) -> None:
        f_m1 = self._sideband1_osc_freq_for_current_target()
        self._validate_frequency_state(
            float(self.initial_carrier_frequency),
            f_m1,
            self._get_freq(self.sideband2_osc_index),
            context="symmetric retune",
        )
        self.daq.setDouble(self._freq_path(self.sideband1_osc_index), float(f_m1))
        self.daq.sync()

    def _sideband1_osc_freq_for_current_target(self) -> float:
        return float(self.initial_carrier_frequency) - float(self.current_sideband1_diff_target)

    def _measure_emit_and_retune_symmetric(
        self,
        sweep_point_index: int,
        sum_target: float,
    ) -> bool:
        measurement = self._measure_once()
        initial_phase = float(measurement["sideband1_phase"])
        total_correction = 0.0
        iterations = 0
        sample_index = 0

        while True:
            phase = float(measurement["sideband1_phase"])
            in_band = abs(phase) <= float(self.symmetric_phase_band)
            is_final = in_band or iterations >= int(self.symmetric_max_retune_iterations)
            self._emit_symmetric_measurement(
                sweep_point_index=sweep_point_index,
                sum_target=sum_target,
                measurement=measurement,
                sample_index=sample_index,
                initial_phase=initial_phase,
                current_phase=phase,
                in_band=in_band,
                is_final=is_final,
                retune_iterations=iterations,
                total_correction=total_correction,
            )

            if is_final:
                return True

            correction = self._symmetric_frequency_correction(phase)
            self.current_sideband1_diff_target = float(
                self.current_sideband1_diff_target + correction
            )
            total_correction += correction
            iterations += 1

            self._apply_current_sideband1_target()
            if not self._sleep_with_abort(float(self.symmetric_delay)):
                return False
            measurement = self._measure_once()
            sample_index += 1

    def _emit_symmetric_measurement(
        self,
        sweep_point_index: int,
        sum_target: float,
        measurement: dict,
        sample_index: int,
        initial_phase: float,
        current_phase: float,
        in_band: bool,
        is_final: bool,
        retune_iterations: int,
        total_correction: float,
    ) -> None:
        state = self._state_for_sum_target(float(sum_target))
        measurement.update(
            {
                "step_index": int(self.step_index),
                "sweep_point_index": int(sweep_point_index),
                "sweep_direction": 0 if self.reverse else 1,
                "carrier_drive_set": float(self.carrier_drive),
                "sideband1_drive_set": float(self.sideband1_drive),
                "sideband2_drive_set": float(self.sideband2_drive),
                "f_carrier_set": float(state["f_c"]),
                "f_sideband1_osc_set": float(state["f_m1"]),
                "f_sideband2_osc_set": float(state["f_m2"]),
                "f_demod_sideband1_diff_set": float(state["f_c"] - state["f_m1"]),
                "f_demod_sideband2_sum_set": float(state["f_c"] + state["f_m2"]),
                "symmetric_sample_index": int(sample_index),
                "symmetric_phase_initial": float(initial_phase),
                "symmetric_phase_current": float(current_phase),
                "symmetric_in_band": int(in_band),
                "symmetric_is_final": int(is_final),
                "symmetric_retuned": int(retune_iterations > 0),
                "symmetric_retune_iterations": int(retune_iterations),
                "symmetric_frequency_correction": float(total_correction),
            }
        )
        self.emit("results", measurement)
        self.step_index += 1

    def _symmetric_frequency_correction(self, phase_deg: float) -> float:
        return float(self.symmetric_linewidth) * float(phase_deg) / 90.0


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
