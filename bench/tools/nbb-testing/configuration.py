# netbox-branching's own testing/configuration.py (mounted at /testing).
import importlib.util

_spec = importlib.util.spec_from_file_location('_nbb_testing_configuration', '/testing/configuration.py')
_base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_base)
globals().update({k: v for k, v in vars(_base).items() if not k.startswith('__')})
