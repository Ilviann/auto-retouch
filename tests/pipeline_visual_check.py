"""Render synthetic exposure comparisons for local inspection, without portraits.

Run with the existing .venv interpreter. Writes only ignored .test-work files.
The display-only sRGB transfer below is not a production decode/export adapter.
"""

import io
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ImageState, SourceMetadata
from retouching_tool.pipeline.registry import reference_registry


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / ".test-work"
    output.mkdir(exist_ok=True)
    app = QApplication.instance() or QApplication([])
    if not QFontDatabase.families():
        font_id = QFontDatabase.addApplicationFont(str(Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf"))
        if font_id < 0:
            raise RuntimeError("Cannot register the system test font")
        app.setFont(QFont("Segoe UI", 10))
    ramp = np.linspace(0, 1, 340, dtype=np.float32)
    pixels = np.broadcast_to(ramp[None, :, None], (220, 340, 3)).copy()
    for row, tint in enumerate(((1, .4, .15), (.15, .7, .3), (.2, .4, 1))):
        pixels[55 * (row + 1):55 * (row + 2)] *= np.array(tint, dtype=np.float32)
    state = ImageState.from_pixels(pixels, SourceMetadata("synthetic-display-fixture"))
    registry = reference_registry()
    log = Diagnostics(output / "visual-logs", console=io.StringIO())
    results = [state]
    try:
        for stops in (1, -1):
            definition = parse_pipeline({"schema_version": 1, "name": "Synthetic reference", "stages": [
                {"id": "exposure", "operation": "core.exposure", "operation_version": 1,
                 "enabled": True, "parameters": {"stops": stops}}]}, registry)
            runtime = StageRuntime(log, "directml", root / "checkpoints", "visual-check", definition.stages[0])
            with StageSession(registry, ModelCatalog(), runtime) as session:
                results.append(session.process(state, image_id="synthetic-display-fixture"))
    finally:
        log.close()
    canvas = QImage(1100, 370, QImage.Format.Format_RGB888)
    canvas.fill(QColor("#f6f4ee"))
    painter = QPainter(canvas)
    painter.setFont(app.font())
    painter.setPen(QColor("#222222"))
    painter.drawText(20, 30, "Auto ReTouch — synthetic exposure reference (linear-sRGB float32)")
    for index, (result, label) in enumerate(zip(results, ("Original", "+1 stop (highlight clipping expected)", "−1 stop"))):
        rgb = result.pixels
        display = np.where(rgb <= .0031308, rgb * 12.92, 1.055 * np.power(rgb, 1 / 2.4) - .055)
        buffer = np.round(np.clip(display, 0, 1) * 255).astype(np.uint8)
        image = QImage(buffer.data, 340, 220, buffer.strides[0], QImage.Format.Format_RGB888).copy()
        x = 20 + index * 360
        painter.drawText(x, 65, label)
        painter.drawImage(x, 80, image)
    painter.drawText(20, 335, "Synthetic gradients only. No portrait quality, ICC/EXIF round-trip, or GPU inference acceptance is implied.")
    painter.end()
    path = output / "exposure-reference.png"
    if not canvas.save(str(path)):
        raise RuntimeError(f"Cannot save comparison: {path}")
    print(path)


if __name__ == "__main__":
    main()
