from __future__ import annotations

import warnings
from asyncio import Lock
from collections.abc import Callable, MutableMapping
from types import TracebackType
from typing import Any, cast

from dishka.entities.component import DEFAULT_COMPONENT, Component
from dishka.entities.factory_type import FactoryType
from dishka.entities.key import DependencyKey
from dishka.entities.scope import BaseScope, Scope
from .container_objects import Exit
from .context_proxy import ContextProxy
from .dependency_source import Factory
from .exceptions import (
    ExitError,
    NoFactoryError,
)
from .provider import BaseProvider
from .registry import Registry, RegistryBuilder


class AsyncContainer:
    __slots__ = (
        "child_registries",
        "close_parent",
        "lock",
        "parent_container",
        "registry",
        "_cache",
        "_context",
        "_exits",
    )

    def __init__(
            self,
            registry: Registry,
            *child_registries: Registry,
            parent_container: AsyncContainer | None = None,
            context: dict[Any, Any] | None = None,
            lock_factory: Callable[[], Lock] | None = None,
            close_parent: bool = False,
    ):
        self.registry = registry
        self.child_registries = child_registries
        self._context = {DependencyKey(type(self), DEFAULT_COMPONENT): self}
        if context:
            for key, value in context.items():
                if not isinstance(key, DependencyKey):
                    key = DependencyKey(key, DEFAULT_COMPONENT)
                self._context[key] = value
        self._cache = {**self._context}
        self.parent_container = parent_container

        self.lock: Lock | None
        if lock_factory:
            self.lock = lock_factory()
        else:
            self.lock = None
        self._exits: list[Exit] = []
        self.close_parent = close_parent

    @property
    def context(self) -> MutableMapping[DependencyKey, Any]:
        warnings.warn(
            "`container.context` is deprecated",
            DeprecationWarning,
            stacklevel=2,
        )
        return ContextProxy(cache=self._cache, context=self._context)

    def __call__(
            self,
            context: dict[Any, Any] | None = None,
            lock_factory: Callable[[], Lock] | None = None,
            scope: BaseScope | None = None,
    ) -> AsyncContextWrapper:
        """
        Prepare container for entering the inner scope.
        :param context: Data which will available in inner scope
        :param lock_factory: Callable to create lock instance or None
        :param scope: target scope or None to enter next non-skipped scope
        :return: async context manager for inner scope
        """
        if not self.child_registries:
            raise ValueError("No child scopes found")

        child = AsyncContainer(
            *self.child_registries,
            parent_container=self,
            context=context,
            lock_factory=lock_factory,
        )
        if scope is None:
            while child.registry.scope.skip:
                if not child.child_registries:
                    raise ValueError("No non-skipped scopes found.")
                child = AsyncContainer(
                    *child.child_registries,
                    parent_container=child,
                    context=context,
                    lock_factory=lock_factory,
                    close_parent=True,
                )
        else:
            while child.registry.scope is not scope:
                if not child.child_registries:
                    raise ValueError(f"Cannot find {scope} as a child of "
                                     f"current {self.registry.scope}")
                child = AsyncContainer(
                    *child.child_registries,
                    parent_container=child,
                    context=context,
                    lock_factory=lock_factory,
                    close_parent=True,
                )
        return AsyncContextWrapper(child)

    async def get(
            self,
            dependency_type: Any,
            component: Component | None = DEFAULT_COMPONENT,
    ) -> Any:
        lock = self.lock
        key = DependencyKey(dependency_type, component)
        if not lock:
            return await self._get_unlocked(key)
        async with lock:
            return await self._get_unlocked(key)

    async def _get_unlocked(self, key: DependencyKey) -> Any:
        if key in self._cache:
            return self._cache[key]
        compiled = self.registry.get_compiled_async(key)
        if not compiled:
            if not self.parent_container:
                raise NoFactoryError(key)
            return await self.parent_container.get(
                key.type_hint, key.component,
            )
        try:
            return await compiled(self._get_unlocked, self._exits, self._cache)
        except NoFactoryError as e:
            # cast is needed because registry.get_factory will always
            # return Factory. This happens because registry.get_compiled
            # uses the same method and returns None if the factory is not found
            # If None is returned, then go to the parent container
            e.add_path(cast(Factory, self.registry.get_factory(key)))
            raise

    async def close(self, exception: BaseException | None = None) -> None:
        errors = []
        for exit_generator in self._exits[::-1]:
            try:
                if exit_generator.type is FactoryType.ASYNC_GENERATOR:
                    await exit_generator.callable.asend(exception) # type: ignore[attr-defined]
                elif exit_generator.type is FactoryType.GENERATOR:
                    exit_generator.callable.send(exception)  # type: ignore[attr-defined]
            except StopIteration:  # noqa: PERF203
                pass
            except StopAsyncIteration:
                pass
            except Exception as err:  # noqa: BLE001
                errors.append(err)
        self._cache = {**self._context}
        if self.close_parent and self.parent_container:
            try:
                await self.parent_container.close(exception)
            except Exception as err:  # noqa: BLE001
                errors.append(err)
        if errors:
            raise ExitError("Cleanup context errors", errors)


class AsyncContextWrapper:
    def __init__(self, container: AsyncContainer):
        self.container = container

    async def __aenter__(self) -> AsyncContainer:
        return self.container

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
    ) -> None:
        await self.container.close(exception=exc_val)


def make_async_container(
        *providers: BaseProvider,
        scopes: type[BaseScope] = Scope,
        context: dict[Any, Any] | None = None,
        lock_factory: Callable[[], Lock] | None = Lock,
        skip_validation: bool = False,
        start_scope: BaseScope | None = None,
) -> AsyncContainer:
    registries = RegistryBuilder(
        scopes=scopes,
        container_type=AsyncContainer,
        providers=providers,
        skip_validation=skip_validation,
    ).build()
    container = AsyncContainer(
        *registries,
        context=context,
        lock_factory=lock_factory,
    )

    if start_scope is None:
        while container.registry.scope.skip:
            container = AsyncContainer(
                *container.child_registries,
                parent_container=container,
                context=context,
                lock_factory=lock_factory,
                close_parent=True,
            )
    else:
        while container.registry.scope is not start_scope:
            container = AsyncContainer(
                *container.child_registries,
                parent_container=container,
                context=context,
                lock_factory=lock_factory,
                close_parent=True,
            )
    return container
