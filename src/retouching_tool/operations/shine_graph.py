"""Estimate the neutral reflection in a masked RGB tile and its neighborhood."""

from ..onnx_graph import FloatGraph


def _window_sums(graph, samples, axis, length, window):
    """Sum sliding windows by joining adjacent partial sums, without prefix subtraction."""
    sums = samples
    width = 1
    # Each successive binary digit doubles the current window and optionally adds one sample.
    for bit in bin(window)[3:]:
        remaining = length - 2 * width + 1
        left = graph.slice(sums, [0], [remaining], [axis])
        right = graph.slice(sums, [width], [width + remaining], [axis])
        sums = graph.op("Add", left, right)
        width *= 2
        if bit == "1":
            sums = graph.op("Add", graph.slice(sums, [0], [length - width], [axis]),
                            graph.slice(samples, [width], [length], [axis]))
            width += 1
    return sums


def reflection_graph(tile_size, radius):
    graph = FloatGraph()
    op = graph.op
    size = tile_size + 2 * radius

    def channel(source, index):
        return graph.slice(source, [index], [index + 1], [1])

    def center(source):
        return graph.slice(source, [radius, radius], [size - radius, size - radius], [2, 3])

    def unit(value):
        return op("Clip", value, 0, 1)

    red, green, blue = [channel("rgb", index) for index in range(3)]
    luma = op("Add", op("Add", op("Mul", red, .2126), op("Mul", green, .7152)), op("Mul", blue, .0722))
    peak = op("ReduceMax", "rgb", axes=[1])
    span = op("Sub", peak, op("ReduceMin", "rgb", axes=[1]))
    # Estimate diffuse skin from well-covered samples with headroom in every channel.
    weight = op("Mul", unit(op("Div", op("Sub", "mask", 192), 63)),
                unit(op("Div", op("Sub", .98, peak), .10)))
    moments = op("Concat", weight, op("Mul", weight, luma), op("Mul", weight, span), axis=1)
    for axis in (3, 2):
        moments = _window_sums(graph, moments, axis, size, 2 * radius + 1)
    mass, sum_luma, sum_span = [channel(moments, index) for index in range(3)]
    reference = op("Div", sum_luma, op("Max", mass, 1e-8))
    chroma = op("Div", sum_span, op("Max", sum_luma, 1e-8))

    light = center(luma)
    brightness_excess = op("Sub", op("Sub", light, op("Mul", reference, 1.08)), .008)
    neutral_excess = op("Sub", op("Mul", light, .92), op("Div", center(span), op("Max", chroma, 1e-8)))
    reflection = op("Min", op("Max", 0, op("Min", brightness_excess, neutral_excess)), op("Mul", light, .30))
    confidence = op("Mul", unit(op("Div", op("Sub", mass, 4), 12)), unit(op("Div", op("Sub", chroma, .08), .16)))
    confidence = op("Mul", confidence, unit(op("Div", op("Sub", 1, center(peak)), .04)))
    output = op("Mul", op("Mul", reflection, confidence), op("Div", center("mask"), 255))
    return graph.encode("skin-shine", {"rgb": [1, 3, size, size], "mask": [1, 1, size, size]},
                        output, [1, 1, tile_size, tile_size])
