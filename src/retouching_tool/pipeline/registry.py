"""Explicit registration; importing the catalog never loads an inference stack."""

from functools import partial

from ..errors import RetouchingError


class OperationRegistry:
    def __init__(self):
        self._entries = {}
        self._retired = set()

    def register(self, descriptor, factory, *, retired=False):
        key = (descriptor.operation, descriptor.version)
        if key in self._entries:
            raise ValueError(f"Duplicate operation registration: {key}")
        self._entries[key] = (descriptor, factory)
        if retired:
            self._retired.add(key)

    def is_retired(self, operation, version):
        return (operation, version) in self._retired

    def descriptor(self, operation, version):
        try:
            return self._entries[(operation, version)][0]
        except KeyError as exc:
            raise RetouchingError("OPERATION_UNKNOWN", f"Unsupported operation {operation!r} version {version}; install a compatible application or correct the pipeline") from exc

    def create(self, stage):
        self.descriptor(stage.operation, stage.operation_version)
        return self._entries[(stage.operation, stage.operation_version)][1](stage)

    def descriptors(self):
        return tuple(descriptor for key, (descriptor, _) in self._entries.items() if key not in self._retired)


def reference_registry():
    from ..operations.reference import EXPOSURE, STATISTICS, Exposure, Statistics
    registry = OperationRegistry()
    registry.register(STATISTICS, Statistics)
    registry.register(EXPOSURE, Exposure)
    return registry


def application_registry():
    from ..operations.alibaba_skin import (
        BLEMISH, BLEMISH_MODEL, DESCRIPTOR as ALIBABA_SKIN, SMOOTHING, SMOOTHING_MODEL, AlibabaSkin,
    )
    from ..operations.auto_exposure import DESCRIPTOR as AUTO_EXPOSURE, AutoExposure
    from ..operations.frequency_filters import HIGH_PASS, LOW_PASS, FrequencyFilter
    from ..operations.levels import DESCRIPTOR as LEVELS, Levels
    from ..operations.person_foreground import DESCRIPTOR, PersonForeground
    from ..operations.white_balance import DESCRIPTOR as WHITE_BALANCE, WhiteBalance
    from ..operations.face_detection import DESCRIPTOR as FACE_DETECTION, FaceDetection
    from ..operations.facial_features import DESCRIPTOR as FACIAL_FEATURES, ProtectFacialFeatures
    from ..operations.exposed_skin import DESCRIPTOR as EXPOSED_SKIN, ExposedSkin
    from ..operations.skin_tone_evening import DESCRIPTOR as SKIN_TONE_EVENING, LocalSkinToneEvening
    from ..operations.skin_shine import DESCRIPTOR as SKIN_SHINE, SkinShineReduction

    registry = reference_registry()
    registry.register(WHITE_BALANCE, WhiteBalance)
    registry.register(AUTO_EXPOSURE, AutoExposure)
    registry.register(LEVELS, Levels)
    registry.register(HIGH_PASS, FrequencyFilter)
    registry.register(LOW_PASS, FrequencyFilter)
    registry.register(DESCRIPTOR, PersonForeground)
    registry.register(FACE_DETECTION, FaceDetection)
    registry.register(FACIAL_FEATURES, ProtectFacialFeatures)
    registry.register(EXPOSED_SKIN, ExposedSkin)
    registry.register(SKIN_TONE_EVENING, LocalSkinToneEvening)
    registry.register(SKIN_SHINE, SkinShineReduction)
    registry.register(BLEMISH, partial(AlibabaSkin, descriptor=BLEMISH, model=BLEMISH_MODEL))
    registry.register(SMOOTHING, partial(AlibabaSkin, descriptor=SMOOTHING, model=SMOOTHING_MODEL))
    registry.register(ALIBABA_SKIN, AlibabaSkin, retired=True)
    return registry
