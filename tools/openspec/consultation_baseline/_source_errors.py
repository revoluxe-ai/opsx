"""Общие исключения загрузки historical source @ BASE_SHA."""


class SourceLoadError(RuntimeError):
    """Не удалось загрузить или верифицировать исходник consultation baseline."""
