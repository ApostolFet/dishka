from collections.abc import Sequence

from dishka.entities.key import DependencyKey
from .dependency_source import Factory

try:
    from builtins import ExceptionGroup
except ImportError:
    from exceptiongroup import ExceptionGroup

from .error_rendering import PathRenderer

_renderer = PathRenderer()


class DishkaError(Exception):
    pass


class InvalidGraphError(DishkaError):
    pass


class CycleDependenciesError(DishkaError):
    def __init__(self, path: Sequence[Factory]) -> None:
        self.path = path

    def __str__(self):
        return "Cycle dependencies detected.\n" + _renderer.render(self.path)


class ExitError(ExceptionGroup, DishkaError):
    pass


class UnsupportedFactoryError(DishkaError):
    pass


class NoFactoryError(DishkaError):
    def __init__(self, requested: DependencyKey):
        self.requested = requested
        self.path = []

    def add_path(self, requested_by: Factory):
        self.path.insert(0, requested_by)

    def __str__(self):
        if self.path:
            path = self.path[-1]
            return (
                f"Cannot find factory for {self.requested}. "
                f"It is missing or has invalid scope.\n"
            ) + _renderer.render(self.path, self.requested)
        else:
            return (
                f"Cannot find factory for {self.requested}. "
                f"Check scopes in your providers. "
                f"It is missing or has invalid scope."
            )
