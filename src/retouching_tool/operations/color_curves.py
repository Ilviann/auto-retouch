"""Built-in ONNX graphs for pointwise corrections to linear RGB."""

from ..onnx_graph import FloatGraph


# Independent pixels can be packed across row boundaries, including very wide panoramas.
CHUNK_PIXELS = 480_000
RGB_SHAPE = ["pixels", 3]


def encode_srgb(graph, source):
    op = graph.op
    return op("Where", op("LessOrEqual", source, .0031308), op("Mul", source, 12.92),
              op("Sub", op("Mul", 1.055, op("Pow", source, 1 / 2.4)), .055))


def decode_srgb(graph, source):
    op = graph.op
    return op("Where", op("LessOrEqual", source, .04045), op("Div", source, 12.92),
              op("Pow", op("Div", op("Add", source, .055), 1.055), 2.4))


def exposure_graph():
    graph = FloatGraph()
    op = graph.op
    peak = op("ReduceMax", "rgb", axes=[1])
    shoulder = op("Div", op("Sub", 1, peak), "gain")
    output = op("Div", "rgb", op("Add", peak, shoulder))
    return graph.encode("auto-exposure", {"rgb": RGB_SHAPE, "gain": [1]}, output, RGB_SHAPE)


def white_balance_graph():
    graph = FloatGraph()
    op = graph.op
    delta = op("Sub", op("Mul", "rgb", "gain"), "rgb")
    headroom = op("Sub", 1, op("ReduceMax", "rgb", axes=[1]))
    weight = op("Min", 1, op("Div", headroom, op("Max", .15, op("ReduceMax", delta, axes=[1]))))
    output = op("Clip", op("Add", "rgb", op("Mul", weight, delta)), 0, 1)
    return graph.encode("fixed-white-balance", {"rgb": RGB_SHAPE, "gain": [3]}, output, RGB_SHAPE)


def apply_curve(session, pixels, *, channel_index=None, **parameters):
    import numpy as np

    source = pixels.reshape(-1, 3)
    output = np.empty_like(source) if channel_index is None else source.copy()
    if channel_index is not None:
        source = source[:, channel_index:channel_index + 1]
    target = output if channel_index is None else output[:, channel_index:channel_index + 1]
    for start in range(0, len(source), CHUNK_PIXELS):
        block = slice(start, start + CHUNK_PIXELS)
        corrected, = session.run(None, {"rgb": np.ascontiguousarray(source[block]), **parameters})
        target[block] = corrected
    return output.reshape(pixels.shape)
