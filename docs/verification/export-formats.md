# Export formats

Checked on Windows 11 with the project CPython 3.14 environment on 2026-11-19.
The main window offers five formats. JPEG remains the default and preserves
the original filename. The other formats retain the relative folder and stem.
All five encode the oriented working image to 8-bit-per-channel sRGB.

| Format | Filename | Settings and defaults |
| --- | --- | --- |
| JPEG | Original .jpg or .jpeg | Quality 92, 4:4:4 chroma, baseline |
| JPEG XL | .jxl | Quality 90 or lossless, effort 7 |
| PNG | .png | Lossless compression level 6 |
| TIFF | .tif | Lossless Deflate or uncompressed |
| BMP | .bmp | Uncompressed RGB, 300 DPI |

JPEG, JPEG XL, PNG and TIFF carry supported capture and descriptive metadata,
normalized technical fields, and processing history. PNG uses an eXIf chunk
and Adobe XMP iTXt. JPEG XL uses Exif and XML container boxes and a codestream
sRGB declaration. TIFF contains native capture and GPS directories, ICC and XMP.
These three formats carry source IPTC-IIM bytes in TIFF tag 33723. BMP omits EXIF,
XMP, IPTC and ICC. Its print resolution changes the BMP header without resampling.

The worker reopens the pending output before publishing it. Lossless exports
also compare every decoded RGB row against the 8-bit encoder input. Both batch
modes use the selected format for disk estimates, collisions, status and receipt
recovery. Earlier snapshots containing only JPEG options still load as JPEG.

## Verification

- A local 4,000 × 6,000 study JPEG with EXIF, XMP and IPTC passed verification
  in all five formats using their defaults. The source SHA-256 matched after
  the run. The temporary exports and logs were removed.
- Generated fixtures covered both EXIF byte orders, nested camera/GPS fields,
  IPTC, normalized orientation, both TIFF compression modes, lossy and lossless
  JPEG XL, PNG compression and BMP DPI. Simulated omission of a PNG ICC
  profile produced a correlated failure with a traceback before publication.
- Headless checks covered an unavailable JPEG XL codec, estimated disk space,
  two inputs mapping to the same converted filename, and recovery of interrupted
  sequential PNG and staged TIFF jobs using the original verified pending file.
- Offscreen Qt tests exercised format-specific controls, disk rechecks, an
  existing PNG collision, and real worker exports for staged PNG and sequential
  JPEG. Job summaries and the local diagnostics report used the frozen options.

43 focused tests passed across JPEG and multi-format export, batch preflight,
diagnostic reports, selected scheduler cases and desktop integration. This check
covered file integrity and metadata. Portrait appearance and full-batch peak
memory were outside the verification scope.

### ASUS MakerNote regression

Two local ASUS_AI2302 JPEGs, including the reported 4,096 × 3,072 input,
exported to JPEG XL at default settings. Reopening both outputs confirmed
byte-identical `imginfo` MakerNotes, normalized orientation, expected dimensions
and the Auto ReTouch Software tag. Both source SHA-256 hashes matched after
the run. Synthetic fixtures exercised JPEG, JPEG XL, PNG and TIFF in both EXIF
byte orders. Unrecognized binary notes, malformed XML, unknown fields and
trailing binary data produced correlated errors before publication.
