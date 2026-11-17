# JPEG decoding and verified export

Accepted for the headless milestone 3b adapter on 2026-11-17. Desktop settings,
batch scheduling and broader camera acceptance remain pending.

Use the hash-pinned Pillow 13.0.0 Windows x86-64 CPython 3.14 wheel for JPEG
decoding and encoding and its LittleCMS binding for ICC conversion. Qt image
decoding would tie the worker to the GUI adapter. Pillow's EXIF convenience API
does not establish exact retention of tag types and raw values, so the JPEG
adapter reads and writes bounded classic-TIFF directories directly. The wheel
includes the MIT-CMU Pillow license and notices for LittleCMS and libjpeg-turbo.
Keep the wheel's license notices with redistributed copies and audit the full
distribution before release. [Pinned wheel](https://pypi.org/project/pillow/13.0.0/#pillow-13.0.0-cp314-cp314-win_amd64.whl)

## Decode and working color

- Apply EXIF orientation when present and honor XMP TIFF orientation when EXIF
  omits it. Report a conflict when both disagree. Keep the original metadata
  unchanged and establish the initial pixel coordinates after orientation.
- Convert embedded profiles with LittleCMS using relative colorimetric rendering
  and black-point compensation. The working representation is normalized
  linear-sRGB float32. Conversion currently passes through 8-bit encoded sRGB.
- Treat RGB or grayscale with no usable profile and only absent or uncalibrated
  color declarations as sRGB with a warning. A source explicitly identifying a
  different color space, or an unprofiled CMYK source, fails with an actionable
  message. Invalid embedded profiles also fail.
- Bound JPEG headers to 24 MiB and images to 80 million pixels. Decode one image
  per call. Reject over-limit files at original size and use strips for transfer
  function conversion. Concurrency and machine-specific memory preflight belong
  to batch scheduling.

## Export and metadata

- Default compression is quality 92, 4:4:4 chroma and baseline JPEG. Quality
  1–95, chroma sampling and progressive encoding are explicit settings.
- Retain classic-TIFF EXIF capture values in the primary, EXIF, GPS and
  interoperability directories. Normalize orientation, dimensions and sRGB
  fields and remove obsolete encoding fields and preview thumbnails. An invalid
  EXIF thumbnail payload can be discarded with a warning when the directories
  and capture values remain readable.
- Preserve standard XMP and its existing history, and carry Photoshop IPTC-IIM
  data with a regenerated digest. Append an XMP history event with the software,
  UTC timestamp, pipeline SHA-256 and any previous Software values. Capture
  timestamps and photographer credits stay intact. Other Photoshop resources,
  including previews and editor-specific settings, are outside this adapter's
  supported metadata set.
- Fail explicitly for unsupported MakerNotes at export because their internal
  offsets cannot be verified after reserialization. Extended or multiple XMP
  packets and malformed capture EXIF have explicit error paths. Original EXIF,
  ICC, XMP and IPTC-IIM bytes remain available in decoded states and staged
  artifacts. [CIPA Exif specification](https://www.cipa.jp/std/documents/e/DC-X008-Translation-2019-E.pdf)
  [Adobe XMP specifications](https://developer.adobe.com/xmp/docs/xmp-specifications/)
- Encode a temporary file in the destination directory, flush it, reopen it and
  verify decoding, dimensions, metadata and provenance. Publish by Windows
  rename, which refuses existing targets. Folder policy, output naming, volume
  budgeting and coordination with the job journal belong to the batch layer.

See [verification](../verification/jpeg-boundary.md) for measured coverage.
