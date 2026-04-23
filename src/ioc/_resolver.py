from __future__ import annotations

import abc
import inspect
import types
import typing
from typing import Optional, TypeVar, Union

T = TypeVar('T')


class ResolutionFailure(Exception):
    pass


class DuplicateArgOfSameType(ResolutionFailure):
    def __init__(self, message: str, duplicate_type: type, arguments: list):
        super().__init__(message)
        self.duplicate_type = duplicate_type
        self.arguments = arguments


class UnknownArgument(ResolutionFailure):
    def __init__(self, message: str, argument_type: type, argument):
        super().__init__(message)
        self.argument_type = argument_type
        self.argument = argument


class UnknownKeywordArgument(UnknownArgument):
    def __init__(self, message: str, argument_name: str, argument_type: type, argument):
        super().__init__(message, argument_type, argument)
        self.argument_name = argument_name


class UnboundTypeRequested(ResolutionFailure):
    def __init__(self, message: str, type_: type):
        super().__init__(message)
        self.type = type_


class Singleton(abc.ABC):
    """Inherit from this to make a class auto-register as a singleton on first resolution."""


def _unwrap_optional(annotation) -> tuple[bool, type]:
    """Return (is_optional, inner_type). Handles both Optional[X] and X | None."""
    # Python 3.10+ union syntax: X | None
    if isinstance(annotation, types.UnionType):
        args = annotation.__args__
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            return True, non_none[0]
    # typing.Optional[X] / Union[X, None]
    if typing.get_origin(annotation) is Union:
        args = typing.get_args(annotation)
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            return True, non_none[0]
    return False, annotation


class Resolver:
    _global_instance: Optional[Resolver] = None

    def __init__(self):
        self._singletons: dict = {}
        self._factories: dict = {}
        self._singletons[Resolver] = self

    def __call__(self, cls: type[T], *args, **kwargs) -> T:
        if cls in self._singletons:
            return self._singletons[cls]
        if cls in self._factories:
            return self._factories[cls](*args, **kwargs)
        return self._make(cls, *args, **kwargs)

    def _make(self, cls: type[T], *args, **kwargs) -> T:
        try:
            hints = typing.get_type_hints(cls.__init__)
        except Exception:
            hints = {}
        hints.pop('return', None)

        sig = inspect.signature(cls.__init__)
        params = [
            (name, param)
            for name, param in sig.parameters.items()
            if name != 'self'
            and param.kind
            not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        ]

        args_dict: dict = {}
        for arg in args:
            arg_type = type(arg)
            if arg_type in args_dict:
                raise DuplicateArgOfSameType(
                    f"Multiple arguments of type `{arg_type}`, cannot determine which to use",
                    duplicate_type=arg_type,
                    arguments=[x for x in args if type(x) is arg_type],
                )
            args_dict[arg_type] = arg

        cls_kwargs: dict = {}
        for name, _param in params:
            if name in kwargs:
                cls_kwargs[name] = kwargs.pop(name)
            elif name in hints:
                annotation = hints[name]
                is_opt, inner_type = _unwrap_optional(annotation)

                if inner_type in args_dict:
                    cls_kwargs[name] = args_dict.pop(inner_type)
                elif is_opt and inner_type not in self:
                    cls_kwargs[name] = None
                else:
                    cls_kwargs[name] = self(inner_type)

        if args_dict:
            arg_type, arg = next(iter(args_dict.items()))
            raise UnknownArgument(
                f"Cannot determine where to use argument `{arg}` of type `{arg_type}`",
                argument_type=arg_type,
                argument=arg,
            )

        if kwargs:
            name, arg = next(iter(kwargs.items()))
            raise UnknownKeywordArgument(
                f"Did not find keyword argument `{name}` in `{cls.__name__}.__init__`",
                argument_name=name,
                argument_type=type(arg),
                argument=arg,
            )

        instance = cls(**cls_kwargs)

        if inspect.isclass(cls) and Singleton in inspect.getmro(cls):
            self._singletons[cls] = instance

        return instance

    def bind(self, cls: type[T], factory=None) -> None:
        """Register a factory for cls. Each call to resolver(cls) invokes the factory anew."""
        if factory is None:
            def factory(*a, **kw):
                return self._make(cls, *a, **kw)
        self._factories[cls] = factory

    def singleton(self, cls_or_instance, instance=None) -> T:  # type: ignore[misc]
        """Register or retrieve a singleton.

        Forms:
          resolver.singleton(MyClass)              # create and register
          resolver.singleton(MyClass, my_instance) # register existing instance
          resolver.singleton(my_instance)          # infer class from instance
        """
        if instance is None:
            if inspect.isclass(cls_or_instance):
                if cls_or_instance in self._singletons:
                    return self._singletons[cls_or_instance]
                instance = self._make(cls_or_instance)
                self._singletons[cls_or_instance] = instance
                return instance
            else:
                instance = cls_or_instance
                cls_or_instance = type(instance)
        self._singletons[cls_or_instance] = instance
        return instance

    def __contains__(self, cls: type) -> bool:
        return cls in self._singletons

    def clear(self, cls: type = None) -> None:
        """Remove a binding. With no argument, clears all bindings."""
        if cls is None:
            self._singletons.clear()
            self._factories.clear()
            self._singletons[Resolver] = self
        else:
            self._singletons.pop(cls, None)
            self._factories.pop(cls, None)

    def clone(self, *types: type) -> Resolver:
        """Return a new Resolver that inherits the specified singleton bindings.

        With no arguments, copies all current singleton bindings.
        Raises UnboundTypeRequested if a requested type has no singleton binding.
        """
        new_resolver = Resolver()
        source = (
            [t for t in self._singletons if t is not Resolver]
            if not types
            else list(types)
        )
        for t in source:
            if t in self._singletons:
                new_resolver._singletons[t] = self._singletons[t]
            else:
                raise UnboundTypeRequested(
                    f"Cannot clone with type {t}: not bound as a singleton in this resolver",
                    type_=t,
                )
        return new_resolver

    @classmethod
    def get(cls) -> Resolver:
        """Return the process-wide singleton Resolver, creating it if needed."""
        if cls._global_instance is None:
            cls._global_instance = Resolver()
        return cls._global_instance

    @classmethod
    def reset(cls) -> None:
        """Discard the process-wide Resolver."""
        cls._global_instance = None
