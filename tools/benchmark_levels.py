"""Compare full-stage NumPy and ONNX Levels on a local JPEG without exporting it."""

import argparse
from dataclasses import replace
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np

from retouching_tool.environment import read_ready_backends
from retouching_tool.imaging.jpeg import JpegRuntime, decode_jpeg, encode_srgb_strip
from retouching_tool.imaging.transfer import srgb8_to_linear
from retouching_tool.operations.levels import CHANNELS, DESCRIPTOR, Levels
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import StageDefinition
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ImageState
from retouching_tool.pipeline.registry import OperationRegistry


class NumpyLevels(Levels):
    def process(self, state):
        p = self.settings
        black, white, low, high = srgb8_to_linear(
            [p["input_black"], p["input_white"], p["output_black"], p["output_white"]])
        channel = p["channel"]
        target = slice(None) if channel == "RGB" else CHANNELS.index(channel) - 1
        pixels = np.empty_like(state.pixels) if channel == "RGB" else state.pixels.copy()
        for top in range(0, state.height, 32):
            source = state.pixels[top:top + 32, :, target]
            values = np.where(source <= .0031308, 12.92 * source, 1.055 * source ** (1 / 2.4) - .055)
            values *= 255
            values -= p["input_black"]
            values /= p["input_white"] - p["input_black"]
            np.clip(values, 0, 1, out=values)
            np.power(values, 1 / p["gamma"], out=values)
            values *= (p["output_white"] - p["output_black"]) / 255
            values += p["output_black"] / 255
            result = np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)
            result[source <= black] = low
            result[source >= white] = high
            pixels[top:top + 32, :, target] = result
        return ImageState.from_pixels(pixels, state.metadata, pixel_revision=state.pixel_revision + 1,
                                      geometry_revision=state.geometry_revision)


class BenchmarkLog:
    def emit(self, code, message, **details):
        if details.get("level") in ("ERROR", "WARNING"):
            print(code, message, flush=True)


def run(state, runtime, registry):
    started = perf_counter()
    with StageSession(registry, ModelCatalog(), runtime) as session:
        first = session.process(state, image_id=state.metadata.source_id)
        elapsed = perf_counter() - started
        del first
        started = perf_counter()
        result = session.process(state, image_id=state.metadata.source_id)
        reused = perf_counter() - started
    return elapsed, reused, result


def compare(cpu, gpu):
    max_linear = max_code = different_codes = 0
    for top in range(0, cpu.height, 32):
        left, right = (state.pixels[top:top + 32] for state in (cpu, gpu))
        max_linear = max(max_linear, float(np.abs(left - right).max()))
        delta = np.abs(encode_srgb_strip(left).astype(np.int16) - encode_srgb_strip(right))
        max_code = max(max_code, int(delta.max()))
        different_codes += np.count_nonzero(delta)
    return max_linear, max_code, different_codes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("photo", type=Path)
    args = parser.parse_args()
    backend, _ = read_ready_backends(Path(__file__).resolve().parents[1])
    log = BenchmarkLog()
    state = decode_jpeg(args.photo, source_id="benchmark", runtime=JpegRuntime(log, "benchmark"))
    print(f"{state.width} x {state.height}, {backend}", flush=True)
    registries = {}
    for name, cls in (("NumPy", NumpyLevels), ("ONNX", Levels)):
        registries[name] = OperationRegistry()
        registries[name].register(DESCRIPTOR, cls)
    for channel in ("RGB", "Red"):
        settings = dict(channel=channel, input_black=16, input_white=235, gamma=1.2,
                        output_black=8, output_white=246)
        stage = StageDefinition("levels", DESCRIPTOR.operation, 1, True, tuple(settings.items()))
        runtime = StageRuntime(log, backend, Path("."), "benchmark", stage)
        timings = {name: [] for name in registries}
        for iteration in range(3):
            results = {}
            for name in (registries if iteration % 2 == 0 else reversed(registries)):
                device = "cpu" if name == "NumPy" else backend
                cold, warm, results[name] = run(state, replace(runtime, backend=device), registries[name])
                timings[name].append((cold, warm))
            error = compare(results["NumPy"], results["ONNX"])
            del results
            print(f"{channel} run {iteration + 1} (fresh, reused): "
                  f"NumPy {timings['NumPy'][-1]}, ONNX {timings['ONNX'][-1]}, "
                  f"max linear / max code / differing codes = {error}", flush=True)
            if error[0] > 1e-5 or error[1] > 1:
                raise ValueError("Output agreement exceeded tolerance")
        for i, label in enumerate(("fresh", "reused")):
            cpu, onnx = (median(pair[i] for pair in times) for times in timings.values())
            print(f"{channel} {label}: {cpu:.3f}s / {onnx:.3f}s = {cpu / onnx:.2f}x", flush=True)


if __name__ == "__main__":
    main()
