"""Estimate a global studio cast or replay one sampled neutral correction."""

from ..errors import RetouchingError
from ..pipeline.definition import ChoiceParameter, OperationDescriptor, Parameter
from ..pipeline.image_state import ImageState


DESCRIPTOR = OperationDescriptor(
    "core.white_balance", 1, "White balance and color cast",
    parameters=(
        ChoiceParameter("mode", "Correction", "Automatic", ("Automatic", "Fixed"), preview_on_change=True),
        Parameter("red_stops", "Fixed red adjustment (stops)", 0, -2, 2, preview_on_change=True, active_choice=("mode", "Fixed")),
        Parameter("blue_stops", "Fixed blue adjustment (stops)", 0, -2, 2, preview_on_change=True, active_choice=("mode", "Fixed")),
    ),
    changes_pixels=True, neutral_picker=True,
)


def sample_neutral(state, x, y):
    """Measure a 5×5 patch of oriented stage input, including partial edge patches."""
    import numpy as np

    if not (0 <= x < state.width and 0 <= y < state.height):
        raise RetouchingError("NEUTRAL_SAMPLE_OUTSIDE", "Click inside the input photo.")
    patch = state.pixels[max(0, y-2):y+3, max(0, x-2):x+3].reshape(-1, 3)
    usable = patch[(patch.min(axis=1) > .015) & (patch.max(axis=1) < .95)]
    if len(usable) <= len(patch) // 2:
        raise RetouchingError("NEUTRAL_SAMPLE_INVALID", "Choose a midtone neutral patch away from clipping or shadows.")
    red, green, blue = np.median(usable, axis=0)
    stops = np.log2(green / np.array([red, blue]))
    if np.max(np.abs(stops)) > 2:
        raise RetouchingError("NEUTRAL_SAMPLE_INVALID", "Choose a less saturated neutral patch.")
    return dict(mode="Fixed", red_stops=round(float(stops[0]), 4), blue_stops=round(float(stops[1]), 4))


def _estimate(state):
    import numpy as np

    # Count each brightness band once so a large lightly tinted backdrop cannot
    # dominate the vote by area alone. Require agreement across brightness bands.
    sy, sx = (max(1, (n + 191) // 192) for n in (state.height, state.width))
    grid = state.pixels[sy//2::sy, sx//2::sx].reshape(-1, 3)
    low, high = grid.min(axis=1), grid.max(axis=1)
    candidates = grid[(low > .035) & (high < .9) & (high < low * 1.4)]
    minimum = max(4, len(grid) // 200)
    votes = []
    for lower, upper in ((.035, .15), (.15, .36), (.36, .91)):
        band = candidates[(candidates[:, 1] >= lower) & (candidates[:, 1] < upper)]
        if len(band) >= minimum:
            ratios = np.log2(band[:, 1:2] / band[:, (0, 2)])
            vote = np.median(ratios, axis=0)
            if np.mean(np.max(np.abs(ratios - vote), axis=1) < .10) >= .75:
                votes.append(vote)
    if len(votes) < 2 or np.max(np.ptp(votes, axis=0)) > .12:
        return None
    return np.median(votes, axis=0)


class WhiteBalance:
    def __init__(self, stage):
        self.stage = stage

    def prepare(self, runtime):
        self.runtime = runtime

    def release(self):
        self.runtime = None

    def process(self, state):
        import numpy as np

        mode = self.stage.parameter("mode")
        if mode == "Automatic":
            stops = _estimate(state)
            if stops is None:
                self.runtime.emit("WHITE_BALANCE_UNCERTAIN", "Insufficient consistent neutrals. "
                                  "Use Pick neutral in stage preview to set a fixed correction.", level="WARNING")
                return state
        else:
            stops = (self.stage.parameter("red_stops"), self.stage.parameter("blue_stops"))
        if max(abs(float(v)) for v in stops) <= (.025 if mode == "Automatic" else 0):
            self.runtime.emit("WHITE_BALANCE_UNCHANGED", "The neutral correction is already balanced.", mode=mode)
            return state
        gains = np.exp2(np.array([stops[0], 0, stops[1]], dtype=np.float32))
        # Keep a sampled neutral's linear luminance when equalizing its channels.
        gains *= np.dot(np.array([.2126, .7152, .0722], dtype=np.float32), 1 / gains)
        pixels = np.empty_like(state.pixels)
        for start in range(0, state.height, 64):
            source = state.pixels[start:start + 64]
            corrected = source * gains
            # Roll off the correction near white, limiting the change before any channel clips.
            peak = source.max(axis=2, keepdims=True)
            weight = np.minimum(1, (1 - peak) / np.maximum(.15, (corrected - source).max(axis=2, keepdims=True)))
            np.clip(source + weight * (corrected - source), 0, 1, out=pixels[start:start + 64])
        self.runtime.emit("WHITE_BALANCE_APPLIED", "Applied the global white balance.", mode=mode,
                          red_stops=float(stops[0]), blue_stops=float(stops[1]))
        return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)
