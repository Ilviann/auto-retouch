import argparse
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchOptions:
    sources: tuple[Path, ...]
    pipeline: Path | None
    config: Path | None


def parse_args(argv=None) -> LaunchOptions:
    def path_argument(value):
        if not value.strip() or "\x00" in value:
            raise argparse.ArgumentTypeError("Path must be nonempty")
        try:
            return Path(value).resolve()
        except (OSError, ValueError) as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    parser = argparse.ArgumentParser(description="Auto ReTouch by Neyrome: local Windows portrait workflow", allow_abbrev=False)
    parser.add_argument("--source", action="append", default=[], type=path_argument, metavar="PATH", help="Source folder or photo file (JPEG, AVIF, PNG, TIFF, BMP, JPEG XL, DNG); repeatable")
    parser.add_argument("--pipeline", type=path_argument, metavar="FILE", help="Explicit pipeline instead of last-used state")
    parser.add_argument("--config", type=path_argument, metavar="FILE", help="Machine-local config (default: application root/config.json)")
    args = parser.parse_args(argv)
    return LaunchOptions(tuple(dict.fromkeys(args.source)), args.pipeline, args.config)
