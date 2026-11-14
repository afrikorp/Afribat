class ErreurMetier(Exception):
    """Refus d'une règle de gestion, renvoyé tel quel à l'utilisateur (en français)."""

    def __init__(self, message: str, status_code: int = 409, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}
