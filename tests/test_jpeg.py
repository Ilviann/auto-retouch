"""Headless JPEG boundary, metadata preservation and publication failures."""

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageCms, JpegImagePlugin, TiffImagePlugin

from retouching_tool.diagnostics.service import Diagnostics
from retouching_tool.errors import RetouchingError
from retouching_tool.imaging.exif import MAKER_NOTE, Entry, read_exif, write_exif
from retouching_tool.imaging.export import export_image
from retouching_tool.imaging.export_settings import BmpSettings, JxlSettings, PngSettings, TiffSettings
from retouching_tool.imaging.iptc import iptc_marker
from retouching_tool.imaging.jpeg import JpegRuntime, JpegSettings, decode_jpeg, export_jpeg
from retouching_tool.imaging.xmp import EVENT, RDF, TIFF, Xmp
from retouching_tool.pipeline.assets import ModelCatalog
from retouching_tool.pipeline.definition import parse_pipeline
from retouching_tool.pipeline.executor import StageRuntime, StageSession
from retouching_tool.pipeline.image_state import ImageState, SourceMetadata
from retouching_tool.pipeline.registry import reference_registry
from retouching_tool.storage.artifacts import ArtifactIdentity, ArtifactStore, fingerprint

HASH = "7" * 64
SOFTWARE = "Auto ReTouch 0.1.0"
ROOT = Path(__file__).resolve().parents[1]
IPTC = b"\x1c\x02\x78\x00\x04note"
XMP = b"""<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <r:RDF xmlns:r="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <r:Description r:about="" xmlns:t="http://ns.adobe.com/tiff/1.0/"
   xmlns:mm="http://ns.adobe.com/xap/1.0/mm/" xmlns:e="http://ns.adobe.com/xap/1.0/sType/ResourceEvent#"
   xmlns:a="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
   t:Orientation="6" t:Software="Other XMP Tool">
   <t:ImageWidth>111</t:ImageWidth>
   <a:Thumbnails><r:Alt><r:li>obsolete preview</r:li></r:Alt></a:Thumbnails>
   <dc:creator><r:Seq><r:li>Photographer</r:li></r:Seq></dc:creator>
   <mm:History><r:Seq><r:li r:parseType="Resource"><e:action>created</e:action></r:li></r:Seq></mm:History>
  </r:Description>
 </r:RDF>
</x:xmpmeta>"""


def linear_icc():
    """Use sRGB primaries with a linear TRC to require an observable ICC transform."""
    profile = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    count, = struct.unpack_from(">I", profile, 128)
    for position in range(132, 132 + count * 12, 12):
        tag, offset, _ = struct.unpack_from(">4sII", profile, position)
        if tag in (b"rTRC", b"gTRC", b"bTRC"):
            curve = struct.pack(">4sIIi", b"para", 0, 0, 65536)
            profile[offset:offset + len(curve)] = curve
            struct.pack_into(">I", profile, position + 8, len(curve))
    return bytes(profile)


def camera_exif(orientation=6, order="<", *, make="Test Camera Co", maker_note=None, color_space=1):
    rational = TiffImagePlugin.IFDRational
    exif = Image.Exif()
    exif.endian = order
    exif.update({271: make, 272: "Test Model", 274: orientation, 305: "Camera Software",
                 306: "2024:11:08 10:05:04", 315: "Photographer", 33432: "Copyright notice"})
    exif[34665] = {33434: rational(1, 125), 36867: "2024:11:08 10:05:03", 36881: "+03:00",
                   37521: "123", 40961: color_space, 40962: 999, 40963: 999, 42036: "Test Lens",
                   40965: {1: "R98", 2: b"0100"}}
    exif[34853] = {1: "N", 2: (rational(1), rational(2), rational(3, 7))}
    if maker_note is not None:
        # Pillow infers TIFF BYTE for a Python bytes value. Cameras store tag 37500 as UNDEFINED.
        fields = read_exif(exif.tobytes())
        fields.capture[MAKER_NOTE] = Entry(7, len(maker_note), maker_note)
        return write_exif(fields)
    return exif.tobytes()


def add_thumbnail(exif, *, invalid=False):
    order = "<" if exif[6:8] == b"II" else ">"
    data = bytearray(exif)
    root, = struct.unpack_from(order + "I", data, 10)
    count, = struct.unpack_from(order + "H", data, 6 + root)
    offset = len(data) - 6
    struct.pack_into(order + "I", data, 6 + root + 2 + 12 * count, offset)
    image = b"\xff\xd8stale-preview\xff\xd9"
    data.extend(struct.pack(order + "HHHIIHHIII", 2, 513, 4, 1, offset + 30,
                            514, 4, 1, len(image), 0))
    if not invalid:
        data.extend(image)
    return bytes(data)


class JpegTests(unittest.TestCase):
    def setUp(self):
        (ROOT / ".test-work").mkdir(exist_ok=True)
        folder = tempfile.TemporaryDirectory(dir=ROOT / ".test-work", prefix="jpeg пример ")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.log = Diagnostics(self.root / "logs", console=io.StringIO())
        self.addCleanup(self.log.close)
        self.runtime = JpegRuntime(self.log, "image-1", "job-1", attempt=2, preview_request_id="request-1")
        self.source = self.root / "вход.jpg"
        self.output = self.root / "выход.jpg"

    def save(self, **metadata):
        samples = np.random.default_rng(21).integers(0, 256, (24, 32, 3), dtype=np.uint8)
        with Image.fromarray(samples) as image:
            image.save(self.source, quality=98, subsampling=0, **metadata)

    def decode(self):
        return decode_jpeg(self.source, source_id="source-1", runtime=self.runtime)

    def export(self, state, *, settings=JpegSettings()):
        return export_jpeg(state, self.output, settings=settings, pipeline_sha256=HASH, runtime=self.runtime)

    def failure(self, code, operation):
        with self.assertRaises(RetouchingError) as caught:
            operation()
        record = self.log.records[-1]
        self.assertEqual(caught.exception.code, code)
        self.assertEqual((record["code"], record["job_id"], record["image_id"], record["attempt"]),
                         (code, "job-1", "image-1", 2))
        self.assertEqual(record["preview_request_id"], "request-1")
        self.assertIn("Traceback", record["exception"])
        self.assertFalse(list(self.root.glob("*.pending")))
        return record

    def test_all_exif_orientation_transforms_before_linear_conversion(self):
        transforms = (lambda a: a, np.fliplr, lambda a: np.rot90(a, 2), np.flipud,
                      lambda a: a.swapaxes(0, 1), lambda a: np.rot90(a, -1),
                      lambda a: np.rot90(a, 2).swapaxes(0, 1), np.rot90)
        for orientation, transform in enumerate(transforms, 1):
            with self.subTest(orientation=orientation):
                self.save(exif=camera_exif(orientation))
                with Image.open(self.source) as raw:
                    encoded = np.asarray(raw) / 255.0
                linear = np.where(encoded <= .04045, encoded / 12.92, ((encoded + .055) / 1.055) ** 2.4)
                state = self.decode()
                np.testing.assert_allclose(state.pixels, transform(linear), atol=3e-7, rtol=0)
                self.assertEqual(state.pixels.dtype, np.float32)
                self.assertFalse(state.pixels.flags.writeable)
                self.assertEqual(state.metadata.exif, camera_exif(orientation))

    def test_icc_transform_uses_embedded_profile_and_output_replaces_it(self):
        profile = linear_icc()
        Image.new("RGB", (24, 16), (64, 64, 64)).save(self.source, icc_profile=profile, quality=95)
        state = self.decode()
        self.assertEqual(state.metadata.icc_profile, profile)
        np.testing.assert_allclose(state.pixels, 64 / 255, atol=.003, rtol=0)
        self.export(state)
        with Image.open(self.output) as image:
            self.assertAlmostEqual(image.getpixel((0, 0))[0], 137, delta=1)
            actual = ImageCms.ImageCmsProfile(io.BytesIO(image.info["icc_profile"]))
            self.assertIn("sRGB", ImageCms.getProfileName(actual))
            self.assertNotEqual(image.info["icc_profile"], profile)

    def test_camera_metadata_xmp_iptc_and_compression_roundtrip(self):
        for order in ("<", ">"):
            with self.subTest(order=order):
                raw = add_thumbnail(camera_exif(order=order))
                self.save(exif=raw, xmp=XMP, extra=iptc_marker(IPTC))
                original = self.source.read_bytes()
                state = self.decode()
                self.assertEqual(state.metadata, SourceMetadata("source-1", raw, b"", XMP, IPTC))
                self.assertEqual(self.export(state, settings=JpegSettings(85, "4:2:2", True)), self.output)
                with Image.open(self.source) as source, Image.open(self.output) as output:
                    self.assertEqual(output.size, (24, 32))
                    self.assertTrue(output.info["progressive"])
                    self.assertEqual(JpegImagePlugin.get_sampling(output), 1)
                    before, after = source.getexif(), output.getexif()
                    for tag in (271, 272, 306, 315, 33432):
                        self.assertEqual(after[tag], before[tag])
                    self.assertEqual(after.get_ifd(34853), before.get_ifd(34853))
                    for tag in (33434, 36867, 36881, 37521, 42036):
                        self.assertEqual(after.get_ifd(34665)[tag], before.get_ifd(34665)[tag])
                    self.assertEqual(after[305], SOFTWARE)
                    self.assertEqual((after[274], after[256], after[257]), (1, 24, 32))
                    self.assertEqual([after.get_ifd(34665)[tag] for tag in (40961, 40962, 40963)], [1, 24, 32])
                    self.assertEqual(after.get_ifd(40965)[1], "R98")
                    self.assertFalse(read_exif(output.info["exif"]).had_thumbnail)
                    self.assertNotIn(b"stale-preview", output.info["exif"])
                    self.assertEqual(output.info["photoshop"][1028], IPTC)
                    self.assertEqual(output.info["photoshop"][1061], hashlib.md5(IPTC, usedforsecurity=False).digest())
                    raw_xmp = output.info["xmp"]
                    xmp = Xmp(raw_xmp)
                    self.assertEqual(xmp.number(TIFF, "Orientation"), 1)
                    self.assertEqual(xmp.number(TIFF, "ImageWidth"), 24)
                    self.assertIn(b"Photographer", raw_xmp)
                    self.assertNotIn(b"obsolete preview", raw_xmp)
                    action = [node.firstChild.data for node in xmp.document.getElementsByTagNameNS(EVENT, "action")]
                    self.assertEqual(action, ["created", "saved"])
                    record, = xmp.document.getElementsByTagNameNS(EVENT, "parameters")
                    self.assertEqual(json.loads(record.firstChild.data), {
                        "pipelineSHA256": HASH, "previousSoftware": "Camera Software",
                        "previousXmpSoftware": ["Other XMP Tool"]})
                self.assertEqual(self.source.read_bytes(), original)
                self.output.unlink()

    def test_new_formats_roundtrip_metadata_and_pixels(self):
        outputs = [(PngSettings(0), ".png"), (TiffSettings("None"), ".tif"), (TiffSettings(), ".tif"),
                   (JxlSettings(70), ".jxl"), (JxlSettings(lossless=True), ".jxl"), (BmpSettings(144), ".bmp")]
        for order in ("<", ">"):
            self.save(exif=add_thumbnail(camera_exif(order=order)), xmp=XMP, extra=iptc_marker(IPTC))
            state = self.decode()
            with Image.open(self.source) as source:
                reference = source.transpose(Image.Transpose.ROTATE_270).tobytes()
                before = source.getexif()
                gps = before.get_ifd(34853)
            for settings, suffix in outputs:
                with self.subTest(order=order, settings=settings):
                    destination = self.output.with_suffix(suffix)
                    export_image(state, destination, settings=settings, pipeline_sha256=HASH, runtime=self.runtime)
                    with Image.open(destination) as image:
                        self.assertEqual(image.size, (24, 32))
                        if not isinstance(settings, JxlSettings) or settings.lossless:
                            self.assertEqual(image.tobytes(), reference)
                        if isinstance(settings, BmpSettings):
                            self.assertFalse(image.getexif())
                            self.assertAlmostEqual(image.info["dpi"][0], 144, delta=.02)
                        else:
                            exif = image.getexif()
                            self.assertEqual(exif[271], before[271])
                            self.assertEqual(exif[33723], IPTC)
                            self.assertEqual(exif[305], SOFTWARE)
                            self.assertEqual(exif[274], 1)
                            self.assertEqual(exif.get_ifd(34853), gps)
                            self.assertEqual(exif.get_ifd(34665)[36867], "2024:11:08 10:05:03")
                            if isinstance(settings, TiffSettings):
                                self.assertEqual(exif[259], 1 if settings.compression == "None" else 8)
                                self.assertIn(HASH.encode(), exif[700])
                    destination.unlink()

    def test_png_metadata_verification_blocks_publication(self):
        self.save(exif=camera_exif())
        state = self.decode()
        save = Image.Image.save

        def omit_profile(image, stream, **options):
            options.pop("icc_profile")
            save(image, stream, **options)

        destination = self.output.with_suffix(".png")
        with patch.object(Image.Image, "save", omit_profile):
            record = self.failure("OUTPUT_VALIDATION_FAILED", lambda: export_image(
                state, destination, settings=PngSettings(), pipeline_sha256=HASH, runtime=self.runtime))
        self.assertIn("PNG ICC", record["exception"])
        self.assertFalse(destination.exists())

    def test_xmp_only_orientation_and_missing_exif(self):
        self.save(xmp=XMP)
        state = self.decode()
        self.assertEqual((state.width, state.height), (24, 32))
        self.assertEqual(state.metadata.exif, b"")
        self.export(state)
        with Image.open(self.output) as image:
            self.assertEqual(image.getexif()[305], SOFTWARE)
            self.assertNotIn(36867, image.getexif().get_ifd(34665))
            history = Xmp(image.info["xmp"]).document.getElementsByTagNameNS(RDF, "li")
            self.assertTrue(history)

    def test_reference_edit_and_tiff_reopen_produce_the_same_jpeg_samples(self):
        self.save(exif=camera_exif(), xmp=XMP, extra=iptc_marker(IPTC))
        source = self.decode()
        registry = reference_registry()
        pipeline = parse_pipeline({"schema_version": 1, "name": "JPEG reference", "stages": [{
            "id": "exposure", "operation": "core.exposure", "operation_version": 1,
            "enabled": True, "parameters": {"stops": .25},
        }]}, registry)
        runtime = StageRuntime(self.log, "directml", self.root / "models", "job-1", pipeline.stages[0], "image-1")
        with StageSession(registry, ModelCatalog(), runtime) as session:
            edited = session.process(source, image_id="image-1")
        self.assertEqual(edited.pixel_revision, 1)
        self.assertGreater(edited.pixels.mean(), source.pixels.mean())
        fake_no_assets = fingerprint({})
        identity = ArtifactIdentity("source-1", hashlib.sha256(self.source.read_bytes()).hexdigest(),
                                    fingerprint(pipeline.to_json()), fingerprint("reference CPU"),
                                    fake_no_assets, fake_no_assets, 42)
        store = ArtifactStore.create(self.root / "scratch", "job-1", self.log)
        ref = store.commit(edited, identity, runtime=runtime)
        stored = store.read(ref, identity, runtime=runtime)
        self.assertEqual(stored.metadata, source.metadata)
        self.export(edited)
        direct_path = self.output
        self.output = self.root / "reopened.jpg"
        self.export(stored)
        with Image.open(direct_path) as direct, Image.open(self.output) as reopened:
            np.testing.assert_array_equal(np.asarray(direct), np.asarray(reopened))
            self.assertEqual(direct.info["exif"], reopened.info["exif"])
            self.assertEqual(direct.info["photoshop"][1028], reopened.info["photoshop"][1028])

    def test_bad_exif_thumbnail_warns_and_capture_metadata_survives(self):
        self.save(exif=add_thumbnail(camera_exif(), invalid=True))
        self.export(self.decode())
        self.assertIn("INPUT_EXIF_THUMBNAIL_INVALID", [record["code"] for record in self.log.records])
        with Image.open(self.output) as image:
            self.assertEqual(image.getexif().get_ifd(34665)[36867], "2024:11:08 10:05:03")

    def test_metadata_and_color_failures_and_untagged_assumption(self):
        self.save(exif=b"Exif\0\0bad")
        self.assertIn("Invalid JPEG EXIF", self.failure("INPUT_METADATA_INVALID", self.decode)["exception"])
        self.save(icc_profile=b"bad profile")
        self.failure("INPUT_METADATA_INVALID", self.decode)
        self.save(exif=camera_exif(color_space=2))
        self.failure("INPUT_COLOR_PROFILE_REQUIRED", self.decode)
        self.save(exif=camera_exif(color_space=65535))
        self.decode()
        self.assertTrue(any(record["code"] == "INPUT_SRGB_ASSUMED" for record in self.log.records))
        self.save(xmp=b'<!DOCTYPE x [<!ENTITY x "test">]><x>&x;</x>')
        self.assertIn("DTD", self.failure("INPUT_METADATA_INVALID", self.decode)["exception"])

    def test_exports_omit_maker_notes_and_private_tags_in_each_directory(self):
        for order, make, note in [("<", "Canon", b"opaque\x00camera data"),
                                  (">", "ASUS", b"<imginfo><mode>photo</mode></imginfo>")]:
            fields = read_exif(camera_exif(order=order, make=make, maker_note=note))
            for directory in (fields.primary, fields.capture, fields.gps, fields.interop):
                directory[0xCAFE] = Entry(7, 7, b"private")
                directory[MAKER_NOTE] = fields.capture[MAKER_NOTE]
            raw = add_thumbnail(write_exif(fields))
            self.save(exif=raw)
            state = self.decode()
            for settings, suffix in [(JxlSettings(), ".jxl"), (JpegSettings(), ".jpg"),
                                     (PngSettings(), ".png"), (TiffSettings(), ".tif")]:
                with self.subTest(order=order, settings=settings):
                    destination = self.output.with_suffix(suffix)
                    export_image(state, destination, settings=settings, pipeline_sha256=HASH, runtime=self.runtime)
                    with Image.open(destination) as image:
                        exif = image.getexif()
                        for directory in (exif, exif.get_ifd(34665), exif.get_ifd(34853), exif.get_ifd(40965)):
                            self.assertNotIn(MAKER_NOTE, directory)
                            self.assertNotIn(0xCAFE, directory)
                        self.assertEqual(exif[271], make)
                        self.assertEqual(exif.get_ifd(34665)[42036], "Test Lens")
                        self.assertEqual(exif.get_ifd(34853)[1], "N")
                        self.assertEqual(exif.get_ifd(40965)[2], b"0100")
                        self.assertEqual((exif[274], exif[305]), (1, SOFTWARE))
                    self.assertEqual(state.metadata.exif, raw)
                    destination.unlink()

    def test_embedded_iptc_survives_standard_exif_filter(self):
        fields = read_exif(camera_exif())
        fields.primary[33723] = Entry(7, len(IPTC), IPTC)
        self.save(exif=write_exif(fields))
        self.export(self.decode())
        with Image.open(self.output) as image:
            self.assertNotIn(33723, image.getexif())
            self.assertEqual(image.info["photoshop"][1028], IPTC)

    def test_new_standard_tag_ids_are_private_in_older_exif_versions(self):
        for version in (b"0230", b"0310"):
            with self.subTest(version=version):
                fields = read_exif(camera_exif())
                fields.capture[0x9000] = Entry(7, 4, version)
                fields.capture[0x9287] = Entry(7, 2, b"\x01\0")
                if version == b"0310":
                    credit = "René\0".encode("utf-8")
                    fields.capture[0xA437] = Entry(129, len(credit), credit)
                self.save(exif=write_exif(fields))
                self.export(self.decode())
                with Image.open(self.output) as image:
                    capture = read_exif(image.info["exif"]).capture
                    self.assertEqual(0x9287 in capture, version == b"0310")
                    self.assertEqual(capture.get(0xA437), fields.capture.get(0xA437))
                self.output.unlink()

    def test_invalid_pixels_fail_without_output(self):
        invalid = ImageState.from_pixels(np.zeros((8, 8, 3), np.float32), SourceMetadata("test"))
        invalid = replace(invalid, representation="unsupported")
        self.failure("OUTPUT_STATE_INVALID", lambda: self.export(invalid))
        self.assertFalse(self.output.exists())

    def test_missing_exif_on_encode_and_disk_error_leave_no_published_file(self):
        self.save()
        state = self.decode()
        save = Image.Image.save

        def omit_exif(image, stream, **settings):
            settings.pop("exif")
            save(image, stream, **settings)

        with patch.object(Image.Image, "save", omit_exif):
            record = self.failure("OUTPUT_VALIDATION_FAILED", lambda: self.export(state))
            self.assertIn("EXIF", record["exception"])
        with patch("retouching_tool.imaging.jpeg.os.fsync", side_effect=OSError("simulated disk exhaustion")):
            self.assertIn("simulated disk exhaustion",
                          self.failure("OUTPUT_WRITE_FAILED", lambda: self.export(state))["exception"])
        self.assertFalse(self.output.exists())
        self.export(state)  # Same destination can be retried after both failures.

    def test_existing_output_original_and_concurrent_collision_are_preserved(self):
        self.save()
        state = self.decode()
        original = self.source.read_bytes()
        with self.assertRaisesRegex(RetouchingError, "already exists"):
            export_jpeg(state, self.source, settings=JpegSettings(), pipeline_sha256=HASH, runtime=self.runtime)
        self.assertEqual(self.source.read_bytes(), original)

        rename = Path.rename

        def create_collision(pending, target):
            target.write_bytes(b"other export")
            return rename(pending, target)

        with patch.object(Path, "rename", create_collision):
            self.failure("OUTPUT_COLLISION", lambda: self.export(state))
        self.assertEqual(self.output.read_bytes(), b"other export")

    def test_nonjpeg_truncation_and_pixel_limit(self):
        self.source.write_bytes(b"not a JPEG")
        self.failure("INPUT_DECODE_FAILED", self.decode)
        self.save()
        with patch("retouching_tool.imaging.jpeg.MAX_PIXELS", 200):
            self.failure("INPUT_IMAGE_TOO_LARGE", self.decode)
        self.source.write_bytes(self.source.read_bytes()[:900])
        self.failure("INPUT_DECODE_FAILED", self.decode)

    def test_adapter_import_does_not_load_qt_or_image_libraries(self):
        script = """import sys
for name in ('PySide6', 'numpy', 'onnxruntime', 'PIL'):
    sys.modules[name] = None
from retouching_tool.imaging.jpeg import JpegSettings
assert JpegSettings().quality == 92
"""
        result = subprocess.run([sys.executable, "-B", "-c", script], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
