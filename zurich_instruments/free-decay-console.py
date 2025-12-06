"""Console variant of the free-decay capture."""
from importlib import util
from pathlib import Path
import sys

from pymeasure.display.console import ManagedConsole


def load_free_decay_procedure():
    """Load FreeDecayProcedure from free-decay.py (hyphenated filename)."""
    module_path = Path(__file__).with_name("free-decay.py")
    spec = util.spec_from_file_location("free_decay_module", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module from {module_path}")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.FreeDecayProcedure


class FreeDecayConsole(ManagedConsole):
    """ManagedConsole with None-valued CLI params stripped out."""

    def __init__(self, procedure_class):
        super().__init__(procedure_class=procedure_class)
        # Drop parameters that were left unspecified (default None)
        self.parameter_values = {
            k: v for k, v in self.parameter_values.items() if v is not None
        }


def main():
    ProcedureCls = load_free_decay_procedure()
    app = FreeDecayConsole(procedure_class=ProcedureCls)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
