# Automatic exposure and tone verification

`core.auto_exposure` version 1 runs on the CPU with the prepared DirectML
environment. It uses the same descriptor and image-stage implementation in
the editor, previews, and both batch workers.

## Focused checks

- Generated linear-RGB inputs covered correction in both directions, the
  correction limit, zero controls, insufficient midtones, immutable source
  metadata, analysis invalidation, and highlight and channel-ratio preservation.
  An injected allocation failure propagated with the image and stage context.
- Generated JPEGs exercised in-memory stage and whole-pipeline previews, both
  worker schedules, verified exports with capture EXIF, and matching decoded
  output pixels. The outputs kept their source dimensions.
- Offscreen Qt checks added the operation from the picker, edited strength,
  and toggled the stage. The existing modal comparison regression passed.
  Local visual inspection of generated grayscale ramps and color swatches
  showed the dark ramp brighten and the bright ramp darken, with a subtler
  change near white.

```bat
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_auto_exposure.py -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_worker.py -k auto_exposure -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_desktop_batch.py -k modal_stage_comparison -v
.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_foundation.py -k import_boundary -v
```

## Local study JPEGs

On Windows 11 with Python 3.14.7, all seven authorized 4,000 × 6,000 JPEGs ran
through the stage comparison and exported successfully in each worker schedule.
Both schedules produced identical decoded output pixels and verified JPEGs
with tool provenance. The original file hashes matched before and after the
runs. The verification used temporary destinations and removed its exports.

The provisional defaults darkened all seven study images. Visual acceptance on
the study shots and on a broader range of studio lighting is pending. Preset
strengths still need user review.
