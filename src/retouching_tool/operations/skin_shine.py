"""Attenuate localized near-neutral reflections on exposed skin in linear light."""

from ..imaging.skin_matte import full_skin_matte
from ..pipeline.definition import BooleanParameter, OperationDescriptor, Parameter
from ..pipeline.image_state import EXPOSED_SKIN, FACIAL_FEATURES, ImageState
from .onnx_session import open_session
from .shine_graph import reflection_graph


DESCRIPTOR = OperationDescriptor(
    "retouch.skin_shine_reduction", 1, "Skin shine reduction",
    parameters=(Parameter("strength", "Strength", 0.5, 0, 1, preview_on_change=True),
                BooleanParameter("protect_features", "Use facial feature protection when available", True,
                                 FACIAL_FEATURES.key, preview_on_change=True)),
    requires=(EXPOSED_SKIN,), optional=(FACIAL_FEATURES,), changes_pixels=True,
    backends=("cpu", "directml", "cuda"), inference_runtime="onnxruntime",
)
TILE_SIZE = 256


class SkinShineReduction:
    def __init__(self, stage):
        self.strength = stage.parameter("strength")
        self.protect_features = stage.parameter("protect_features")
        self.session = None
        self.radius = None

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = self.session = self.radius = None

    def _session_for_radius(self, radius):
        if self.radius != radius:
            # A staged pass can encounter different photo sizes. Keep only the active graph.
            self.session = self.radius = None
            self.session = open_session(reflection_graph(TILE_SIZE, radius), self.runtime.backend)
            self.radius = radius
        return self.session

    def process(self, state):
        import numpy as np

        mask = state.entry(EXPOSED_SKIN.key).value
        if not self.strength:
            self.runtime.emit("SKIN_SHINE_UNCHANGED", "Skin shine reduction strength is zero.")
            return state
        if not any(mask.labels):
            self.runtime.emit("SKIN_SHINE_EMPTY_MASK", "No exposed skin was detected. Inspect the Exposed Skin Mask preview.",
                              level="WARNING")
            return state

        entry = state.entry(FACIAL_FEATURES.key) if self.protect_features else None
        matte = full_skin_matte(mask, (state.width, state.height), entry.value if entry else None)
        radius = max(8, min(96, round(min(state.width, state.height) * .025)))
        rgb_tile = mask_tile = None
        output = None
        changed = 0
        for y in range(0, state.height, TILE_SIZE):
            bottom = min(state.height, y + TILE_SIZE)
            for x in range(0, state.width, TILE_SIZE):
                right = min(state.width, x + TILE_SIZE)
                if not matte[y:bottom, x:right].any():
                    continue
                top, left = max(0, y - radius), max(0, x - radius)
                halo = (slice(top, bottom + radius), slice(left, right + radius))
                selection = (slice(y, bottom), slice(x, right))
                source = state.pixels[selection]
                if rgb_tile is None:
                    session = self._session_for_radius(radius)
                    side = TILE_SIZE + 2 * radius
                    rgb_tile = np.empty((1, 3, side, side), dtype=np.float32)
                    mask_tile = np.empty((1, 1, side, side), dtype=np.float32)
                # Zero mask coverage outside the true image gives clipped box neighborhoods.
                rgb_tile.fill(0)
                mask_tile.fill(0)
                coverage = matte[halo]
                dy, dx = top - y + radius, left - x + radius
                tile_rows = slice(dy, dy + coverage.shape[0])
                tile_cols = slice(dx, dx + coverage.shape[1])
                rgb_tile[0, :, tile_rows, tile_cols] = state.pixels[halo].transpose(2, 0, 1)
                mask_tile[0, 0, tile_rows, tile_cols] = coverage
                result, = session.run(None, {"rgb": rgb_tile, "mask": mask_tile})
                reflection = result[0, 0, :bottom - y, :right - x]
                # Cap neutral subtraction at the available room in the darkest channel.
                correction = self.strength * np.minimum(reflection, source.min(axis=2))
                edited = source - correction[:, :, None]
                count = np.count_nonzero(np.any(edited != source, axis=2))
                if count:
                    if output is None:
                        output = state.pixels.copy()
                    output[selection] = edited
                    changed += int(count)
        if output is None:
            self.runtime.emit("SKIN_SHINE_UNCHANGED", "No eligible skin highlights were found.")
            return state
        self.runtime.emit("SKIN_SHINE_APPLIED", "Reduced localized shine on exposed skin.",
                          changed_pixels=changed, strength=self.strength, radius_px=radius)
        return ImageState.from_pixels(output, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)
