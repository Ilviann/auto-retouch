"""Explicit registration; importing the catalog never loads an inference stack."""

from ..errors import RetouchingError


class OperationRegistry:
    def __init__(self):
        self._entries = {}

    def register(self, descriptor, factory):
        key = (descriptor.operation, descriptor.version)
        if key in self._entries:
            raise ValueError(f"Duplicate operation registration: {key}")
        self._entries[key] = (descriptor, factory)

    def descriptor(self, operation, version):
        try:
            return self._entries[(operation, version)][0]
        except KeyError as exc:
            raise RetouchingError("OPERATION_UNKNOWN", f"Unsupported operation {operation!r} version {version}; install a compatible application or correct the pipeline") from exc

    def create(self, stage):
        self.descriptor(stage.operation, stage.operation_version)
        return self._entries[(stage.operation, stage.operation_version)][1](stage)

    def descriptors(self):
        return tuple(value[0] for value in self._entries.values())


def reference_registry():
    from ..operations.reference import EXPOSURE, STATISTICS, Exposure, Statistics
    registry = OperationRegistry()
    registry.register(STATISTICS, Statistics)
    registry.register(EXPOSURE, Exposure)
    return registry


def application_registry():
    from ..operations.auto_exposure import DESCRIPTOR as AUTO_EXPOSURE, AutoExposure
    from ..operations.person_foreground import DESCRIPTOR, PersonForeground

    registry = reference_registry()
    registry.register(AUTO_EXPOSURE, AutoExposure)
    registry.register(DESCRIPTOR, PersonForeground)
    return registry
