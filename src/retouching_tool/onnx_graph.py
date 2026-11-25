"""Encode the small built-in ONNX graphs without the optional ONNX authoring SDK."""


def _varint(value: int) -> bytes:
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def number(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def message(field: int, value: bytes | str) -> bytes:
    value = value.encode() if isinstance(value, str) else value
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def float_value(name, dimensions):
    """ValueInfoProto for a float32 tensor with fixed or symbolic dimensions."""
    shape = b"".join(message(1, message(2, dim) if isinstance(dim, str) else number(1, dim))
                     for dim in dimensions)
    tensor_type = number(1, 1) + message(2, shape)
    return message(1, name) + message(2, message(1, tensor_type))


def node(op, inputs, output, *, group=None):
    data = b"".join(message(1, name) for name in inputs) + message(2, output) + message(4, op)
    if group is not None:
        # AttributeProto: name, integer value and type INT.
        data += message(5, message(1, "group") + number(3, group) + number(20, 2))
    return data


def initializer(name, values):
    """TensorProto for an integer index or floating-point filter constant."""
    import numpy as np

    array = np.asarray(values)
    if np.issubdtype(array.dtype, np.integer):
        array, data_type = array.astype("<i8", copy=False), 7  # TensorProto.INT64
    else:
        array, data_type = array.astype("<f4", copy=False), 1  # TensorProto.FLOAT
    return (b"".join(number(1, dim) for dim in array.shape) + number(2, data_type)
            + message(8, name) + message(9, array.tobytes()))


def model(name, nodes, inputs, outputs, initializers=()):
    graph = b"".join(message(1, op) for op in nodes) + message(2, name)
    graph += b"".join(message(5, tensor) for tensor in initializers)
    graph += b"".join(message(11, value) for value in inputs) + b"".join(message(12, value) for value in outputs)
    # ModelProto: IR version 8, producer, graph, default-domain opset 13.
    return number(1, 8) + message(2, "Auto ReTouch") + message(7, graph) + message(8, number(2, 13))
