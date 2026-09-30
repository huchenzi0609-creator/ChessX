class RuleError(ValueError):
    """A stable, public-safe rule rejection code with a human explanation."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)
