"""Diagnostics and storage-error classification shared by job schedules."""

import errno


class ImageDiagnostics:
    def __init__(self, diagnostics, warnings=0):
        self.diagnostics = diagnostics
        self.warnings = warnings
        self.traced_error = False
        self.failed_stage = None

    def emit(self, code, message, **details):
        if details.get("level") == "WARNING":
            self.warnings += 1
        if details.get("level") == "ERROR" and details.get("exc_info"):
            self.traced_error = True
            self.failed_stage = details.get("stage_instance_id")
        self.diagnostics.emit(code, message, **details)


def disk_full(error):
    while error is not None:
        if isinstance(error, OSError) and (error.errno in (errno.ENOSPC, errno.EDQUOT)
                                          or getattr(error, "winerror", None) in (39, 112)):
            return True
        error = error.__cause__
    return False
