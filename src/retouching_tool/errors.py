class RetouchingError(Exception):
    """Actionable error whose original technical cause is preserved by chaining."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
