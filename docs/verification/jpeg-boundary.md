# Headless JPEG decode and export verification

Verified on 2026-11-17 using Windows 11, CPython 3.14.7 and Pillow 13.0.0
with LittleCMS. The project-local DirectML environment was retained. Installed
pins, Pillow codec support and `pip check` passed. The prepared lock identity
was updated for the added shared wheel. Setup and backend inference probes
were outside this check.

## Results

- All eight EXIF orientation cases agree with independent NumPy transforms.
  The ICC test uses a generated RGB profile with a linear transfer curve.
  Six generated grayscale and color swatch centers match their expected sRGB
  export values within one 8-bit count. The generated reference and export
  swatches were also compared visually.
- Little- and big-endian EXIF fixtures preserve camera and lens fields,
  capture timestamps, timezone offsets, subsecond values, GPS and photographer
  credits. JPEG dimensions, orientation, sRGB, prior XMP history, IPTC-IIM
  and the new provenance entry were checked after reopening. Cases cover
  absent EXIF, bad preview thumbnails, profile warnings and rejection, malformed
  metadata, and an unsupported MakerNote.
- Reference exposure produces matching JPEG samples from in-memory and restored
  TIFF states. Focused artifact checks cover older manifests and retained,
  corrupt or orphaned metadata blobs. The headless import check blocks Qt,
  Pillow, NumPy and ONNX imports.
- Simulated disk-flush failure, missing EXIF after encoding and a concurrent
  destination collision leave the source and any existing output intact.
  Failed operations emit correlated, traceable diagnostics. A valid retry
  publishes after verification.
- All seven local study JPEGs round-tripped privately at 4,000 × 6,000. Original
  files had the same SHA-256 before and after. Reopened outputs passed checks
  for capture fields, GPS where present, normalized orientation, software,
  prior XMP history and IPTC-IIM. Two inputs included ICC, XMP and IPTC-IIM.
  Five inputs referenced missing EXIF preview data. One input declared an
  uncalibrated color space with no ICC and generated an sRGB-assumption warning.
  Temporary local exports and logs from this check were cleaned up.

Focused commands:

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_jpeg.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_artifacts.py -v -k metadata -k no_exif -k collect
```

12 JPEG tests and 6 artifact tests passed. Proprietary MakerNote preservation,
extended XMP, wider camera and gamut coverage, portrait visual acceptance,
and full batch resource and recovery checks remain pending.
