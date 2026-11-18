class RetouchingError(Exception):
    """Actionable error whose original technical cause is preserved by chaining."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ResizeApprovalRequired(RetouchingError):
    def __init__(self, stage_id, message):
        super().__init__("RETOUCH_RESIZE_APPROVAL_REQUIRED", message)
        self.stage_id = stage_id
