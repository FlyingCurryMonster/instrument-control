import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pymeasure.instruments.andeenhagerling import AH2500A
from pymeasure.instruments.hp import HP53132A
from pymeasure.display.curves import ResultsCurve
import pyqtgraph as pg
import types
from pymeasure.display.Qt import QtWidgets, QtCore
from pymeasure.display.windows.managed_dock_window import ManagedDockWindow
from pymeasure.display.widgets import PlotWidget  # MyPlotWidget
from pymeasure.experiment import Procedure, Results, unique_filename
from pymeasure.experiment import IntegerParameter, FloatParameter, Parameter
from pyqtgraph import DateAxisItem


log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

LabView_t0 = 2082844800
timezone = 4 * 60 * 60


def paro_to_press(T, T0):
    '''
    Converts the period from the parosci pressure gauge to a
    pressure in bar.
    '''
    C = 4800.068
    D = -0.009654
    # T0 = 24.818
    # T0 =24.9137905912 # THIS IS MY TO
    # T0 = 24.9142702639  # THIS IS ERIC'S T0 param
    term1 = 1 - (T0/T) ** 2
    term2 = D * (1 - (T0 / T)) ** 2
    P = C * (term1 - term2)
    psi_to_bar = 14.5038
    return P/psi_to_bar


class MCT_calib(Procedure):
    comments = Parameter('Comments/Notes')

    T0 = FloatParameter('Zero pressure paro sci period',
                        default=24.9142702639e-6, units='s')

    hp_gatetime = FloatParameter('counter gate time', units='s', default=4)
    ah_avgexp = IntegerParameter('AH2500 avg. exponent', default=7,
                                 minimum=0,
                                 maximum=15)

    ah_address = Parameter('AH2500A address', default='GPIB1::3::INSTR')
    hp_address = Parameter('HP counter address', default='GPIB0::9::INSTR')

    params = [
        'comments', 'T0',
        'hp_gatetime', 'ah_avgexp',
        'ah_address', 'hp_address',
    ]

    # Updated data columns for continuous measurement
    DATA_COLUMNS = [
        'UTC', 'timestamp',
        'C', 'Loss', 'Vex',
        'Period', 'P_paro',
    ]

    def startup(self):
        log.info('Starting Melting curve calibration readout')
        self.ah = AH2500A(self.ah_address)
        self.hp = HP53132A(self.hp_address)

        self.hp.measure_mode = 'PER'
        log.info(f'HP measure mode is set to {self.hp.measure_mode}')

        self.ah.avg_exp = self.ah_avgexp
        self.hp.gate_time = self.hp_gatetime

        queried_avgexp = self.ah_avgexp
        queried_gate_time = self.hp.gate_time
        ah_approx_meas_time = self.ah.AVG_TIMES[queried_avgexp]

        log.info(f'AH2500 average exponent is set to {queried_avgexp}')
        log.info(f'AH2500A approx meas. time is {ah_approx_meas_time}s')
        log.info(f'HP counter gate time is {queried_gate_time}')
        self.t_start = time.time()

    def execute(self):
        while not self.should_stop():
            utc_time = time.time() + LabView_t0
            ts = utc_time - self.t_start

            with ThreadPoolExecutor(max_workers=2) as exe:
                # submit both calls at once
                futures = {
                    exe.submit(self.ah.long_avgtime_caplossvolt): "AH",
                    exe.submit(self.hp.long_gate_read): "HP",
                }
                results = {}
                for fut in as_completed(futures):
                    label = futures[fut]
                    try:
                        results[label] = fut.result()
                    except Exception as e:
                        log.error(f"Error: {e}")

            cap, loss, Vex = results['AH']
            period = results['HP']
            P_paro = paro_to_press(period, self.T0)

            data = {
                'UTC': utc_time,
                'timestamp': ts,
                'C': cap,
                'Loss': loss,
                'Vex': Vex,
                'Period': period,
                'P_paro': P_paro,
            }

            self.emit('results', data)


# class zurich_graph(ManagedWindow):
class MCT_calib_graph(ManagedDockWindow):
    def __init__(self):

        time_axis_plot = PlotWidget(
            name='Datetime Plot',
            columns=MCT_calib.DATA_COLUMNS,
            x_axis='UTC',
            y_axis='C')

        # need to add offset for EST time
        time_axis_plot.plot.setAxisItems(
            {'bottom': DateAxisItem(utcOffset=LabView_t0 + timezone)})

        xy_plot = PlotWidget(
            name='XY Plot',
            columns=MCT_calib.DATA_COLUMNS,
            x_axis='C',
            y_axis='P_paro',
        )

        # Make this XY plot display points only (no connecting line).
        # Override new_curve on this instance so created ResultsCurve objects
        # have a pen (so ResultsCurve can query color()) but the pen is set
        # to NoPen so no line is drawn; markers are used for points.
        def _xy_new_curve(self, results, color=pg.intColor(0), **kwargs):
            # Provide a QPen object so ResultsCurve can query its color(),
            # but disable line drawing by setting the pen style to NoPen.
            pen = pg.mkPen(color=color)
            try:
                pen.setStyle(QtCore.Qt.NoPen)
            except Exception:
                # Some pen implementations may not support setStyle; ignore
                pass
            kwargs.setdefault('pen', pen)
            kwargs.setdefault('antialias', False)
            curve = ResultsCurve(results,
                                 wdg=self,
                                 x=self.plot_frame.x_axis,
                                 y=self.plot_frame.y_axis,
                                 **kwargs)
            # set marker style for the points
            try:
                curve.setSymbol('o')
                curve.setSymbolSize(7)
                curve.setSymbolBrush((0, 150, 255, 200))
                curve.setSymbolPen((0, 0, 0, 200))
            except Exception:
                pass
            return curve

        xy_plot.new_curve = types.MethodType(_xy_new_curve, xy_plot)

        super().__init__(
            procedure_class=MCT_calib,
            inputs=MCT_calib.params,
            displays=MCT_calib.params,
            x_axis=['UTC'],
            y_axis=['C', 'P_paro', 'Loss'],
            widget_list=(time_axis_plot, xy_plot,)

        )

        for plot_frame in self.dock_widget.plot_frames:
            plot_frame.plot.setAxisItems(
                {'bottom': DateAxisItem(utcOffset=LabView_t0 + timezone)})

        try:
            for item in xy_plot.plot.listDataItems():
                item.setPen(None)
                item.setSymbol('o')
                item.setSymbolSize(6)
                item.setSymbolBrush((255, 255, 255, 200))  # fill RGBA
                item.setSymbolPen((0, 0, 0, 200))  # edge RGBA
        except Exception as e:
            log.debug(f"Could not set symbol for xy_plot: {e}")

        self.setWindowTitle('Melting curve calibration')
        self.directory = r'D:/Data/Fall25-Summer26/MCT-calib'
        self.file_input.filename_fixed = False

    def queue(self):
        directory = self.directory
        filename = unique_filename(directory, prefix=f'{self.filename}_')
        procedure = self.make_procedure()
        results = Results(procedure, filename)
        experiment = self.new_experiment(results)
        self.manager.queue(experiment)


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    window = MCT_calib_graph()
    window.show()
    sys.exit(app.exec_())
