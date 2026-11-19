"""Immutable format-specific export options shared by the editor, preflight and worker."""

from dataclasses import asdict, dataclass, fields
from importlib import metadata

from ..errors import RetouchingError


@dataclass(frozen=True)
class JpegSettings:
    quality: int = 92
    subsampling: str = "4:4:4"
    progressive: bool = False
    format = "JPEG"

    def __post_init__(self):
        if (type(self.quality) is not int or not 1 <= self.quality <= 95
                or self.subsampling not in ("4:4:4", "4:2:2", "4:2:0")
                or type(self.progressive) is not bool):
            raise ValueError("Set JPEG quality to 1-95, chroma to 4:4:4/4:2:2/4:2:0 and progressive to on/off")

    def description(self):
        return f"JPEG {self.quality}, {self.subsampling}" + (", progressive" if self.progressive else "")


@dataclass(frozen=True)
class JxlSettings:
    quality: int = 90
    lossless: bool = False
    effort: int = 7
    format = "JPEG XL"

    def __post_init__(self):
        if (type(self.quality) is not int or not 1 <= self.quality <= 99 or type(self.lossless) is not bool
                or type(self.effort) is not int or not 1 <= self.effort <= 9):
            raise ValueError("Set JPEG XL quality to 1–99, lossless to on/off and effort to 1–9")

    def description(self):
        mode = "lossless" if self.lossless else f"quality {self.quality}"
        return f"JPEG XL {mode}, effort {self.effort}"


@dataclass(frozen=True)
class PngSettings:
    compression: int = 6
    format = "PNG"

    def __post_init__(self):
        if type(self.compression) is not int or not 0 <= self.compression <= 9:
            raise ValueError("Set PNG compression to 0–9")

    def description(self):
        return f"PNG lossless, compression {self.compression}"


@dataclass(frozen=True)
class TiffSettings:
    compression: str = "Deflate"
    format = "TIFF"

    def __post_init__(self):
        if self.compression not in ("None", "Deflate"):
            raise ValueError("Select None or Deflate for TIFF compression")

    def description(self):
        return f"TIFF lossless, compression {self.compression}"


@dataclass(frozen=True)
class BmpSettings:
    dpi: int = 300
    format = "BMP"

    def __post_init__(self):
        if type(self.dpi) is not int or not 1 <= self.dpi <= 2400:
            raise ValueError("Set BMP resolution to 1–2400 DPI")

    def description(self):
        return f"BMP 24-bit, {self.dpi} DPI, no EXIF"


ExportSettings = JpegSettings | JxlSettings | PngSettings | TiffSettings | BmpSettings
FORMATS = {cls.format: cls for cls in (JpegSettings, JxlSettings, PngSettings, TiffSettings, BmpSettings)}
EXTENSIONS = {"JPEG": (".jpg", ".jpeg"), "JPEG XL": (".jxl",), "PNG": (".png",),
              "TIFF": (".tif", ".tiff"), "BMP": (".bmp",)}


def encode_settings(settings):
    return {"format": settings.format, **asdict(settings)}


def parse_settings(data):
    if not isinstance(data, dict):
        raise ValueError("Expected an object containing saved export settings")
    options = data.copy()
    # Schema 1 jobs created before format selection only contain JPEG options.
    cls = FORMATS.get(options.pop("format", "JPEG"))
    if cls is None or options.keys() != {field.name for field in fields(cls)}:
        raise ValueError("Unknown format or incomplete saved export settings")
    return cls(**options)


def export_relative_path(relative, settings):
    if settings.format == "JPEG" and relative.suffix.lower() in EXTENSIONS["JPEG"]:
        return relative
    return relative.with_suffix(EXTENSIONS[settings.format][0])


def output_reservation(settings, source_bytes, working_bytes):
    if settings.format == "JPEG":
        return 2 * source_bytes
    # 4 bytes per pixel covers RGB rows and padding. Include source metadata and container overhead.
    return max(2 * source_bytes, working_bytes // 3 + source_bytes + 256 * 1024)


def require_jxl():
    try:
        import pillow_jxl  # registers the local Pillow encoder and decoder
        version = metadata.version("pillow-jxl-plugin")
        if version != "1.3.8":
            raise ValueError(f"Installed JPEG XL codec version is {version}")
        return version
    except (ImportError, OSError, ValueError, metadata.PackageNotFoundError) as exc:
        raise RetouchingError("JXL_CODEC_UNAVAILABLE",
                              "Install the JPEG XL codec into the project environment with "
                              r".venv\Scripts\python -m pip install --require-hashes --no-deps -r requirements/export-jxl.lock") from exc
