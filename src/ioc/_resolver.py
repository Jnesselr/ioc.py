from __future__ import annotations

import abc
import inspect
import threading
import types
import typing
from typing import TypeVar, Union

T = TypeVar("T")


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


class UnresolvablePrimitive(ResolutionFailure):
    def __init__(self, message: str, type_: type):
        super().__init__(message)
        self.type = type_


class CircularDependency(ResolutionFailure):
    def __init__(self, message: str, type_: type, chain: list):
        super().__init__(message)
        self.type = type_
        self.chain = chain


class InvalidBinding(ResolutionFailure):
    def __init__(self, message: str, expected_type: type, instance):
        super().__init__(message)
        self.expected_type = expected_type
        self.instance = instance


def _is_primitive(t: type) -> bool:
    return getattr(t, "__module__", None) == "builtins"


def _get_base_type(t):
    """For Annotated[T, ...], return T. Otherwise return t unchanged."""
    if typing.get_origin(t) is typing.Annotated:
        return typing.get_args(t)[0]
    return t


def _check_instance_type(cls: type, instance) -> None:
    try:
        if not isinstance(instance, cls):
            raise InvalidBinding(
                f"Cannot register instance of `{type(instance).__name__}` as `{cls.__name__}`",
                expected_type=cls,
                instance=instance,
            )
    except TypeError:
        pass  # e.g. non-runtime_checkable Protocol


def _check_subclass(abstract: type, concrete: type) -> None:
    if _is_primitive(concrete):
        raise UnresolvablePrimitive(
            f"`{concrete.__name__}` is a primitive type and cannot be used as a concrete binding",
            type_=concrete,
        )
    try:
        if not issubclass(concrete, abstract):
            raise InvalidBinding(
                f"`{concrete.__name__}` is not a subclass of `{abstract.__name__}`",
                expected_type=abstract,
                instance=concrete,
            )
    except TypeError:
        pass  # e.g. non-runtime_checkable Protocol


_NO_NEEDS = object()  # sentinel: when(X).give(**kw) with no needs() call


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
    _global_instance: Resolver | None = None
    _global_lock: threading.Lock = threading.Lock()

    def __init__(self):
        self._singletons: dict = {}
        self._factories: dict = {}
        self._contextual: dict = {}
        self._singletons[Resolver] = self
        self._lock = threading.RLock()
        self._local = threading.local()

    def __call__(self, cls: type[T], *args, **kwargs) -> T:
        if cls in self._singletons:
            return self._singletons[cls]
        if cls in self._factories:
            return self._factories[cls](*args, **kwargs)
        if typing.get_origin(cls) is typing.Annotated:
            raise UnboundTypeRequested(
                f"Annotated type `{cls}` is not registered; "
                f"call resolver.singleton() or resolver.bind() first",
                type_=cls,
            )
        if _is_primitive(cls):
            raise UnresolvablePrimitive(
                f"`{cls.__name__}` is a primitive type and cannot be resolved",
                type_=cls,
            )
        return self._make(cls, *args, **kwargs)

    def _make(self, cls: type[T], *args, _contextual_key=None, **kwargs) -> T:
        stack: list = getattr(self._local, "stack", None)
        if stack is None:
            self._local.stack = stack = []

        if cls in stack:
            chain = [*stack, cls]
            raise CircularDependency(
                "Circular dependency detected: " + " → ".join(t.__name__ for t in chain),
                type_=cls,
                chain=chain,
            )

        stack.append(cls)
        try:
            return self._make_inner(cls, *args, _contextual_key=_contextual_key, **kwargs)
        finally:
            stack.pop()

    def _make_inner(self, cls: type[T], *args, _contextual_key=None, **kwargs) -> T:
        lookup_key = _contextual_key if _contextual_key is not None else cls
        own_defaults = self._contextual.get((lookup_key, _NO_NEEDS), {}).get("kwargs", {})

        try:
            hints = typing.get_type_hints(cls.__init__, include_extras=True)
        except Exception:
            hints = {}
        hints.pop("return", None)

        sig = inspect.signature(cls)

        # Classes with no custom __init__ inherit object.__init__(*args, **kwargs).
        # Treat them as having no variadic params so we still raise our custom errors
        # for unexpected arguments rather than silently passing them to object.__init__.
        if cls.__init__ is object.__init__:
            var_positional_name = None
            has_var_keyword = False
        else:
            var_positional_name = next(
                (
                    name
                    for name, p in sig.parameters.items()
                    if p.kind == inspect.Parameter.VAR_POSITIONAL
                ),
                None,
            )
            has_var_keyword = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )

        params = [
            (name, param)
            for name, param in sig.parameters.items()
            if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
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
        for name, param in params:
            if name in kwargs:
                cls_kwargs[name] = kwargs.pop(name)
            elif name in hints:
                annotation = hints[name]
                is_opt, inner_type = _unwrap_optional(annotation)
                base_type = _get_base_type(inner_type)

                ctx = self._contextual.get((lookup_key, inner_type))
                if ctx is not None:
                    cls_kwargs[name] = self._resolve_contextual(ctx, inner_type)
                elif inner_type in args_dict:
                    cls_kwargs[name] = args_dict.pop(inner_type)
                elif name in own_defaults:
                    cls_kwargs[name] = own_defaults[name]
                elif is_opt and inner_type not in self:
                    cls_kwargs[name] = None
                elif param.default is not inspect.Parameter.empty and inner_type not in self:
                    pass  # unregistered type with a default — let Python apply it
                elif _is_primitive(base_type):
                    raise UnresolvablePrimitive(
                        f"`{base_type.__name__}` is a primitive type; "
                        f"provide it explicitly or give `{cls.__name__}.{name}` a default value",
                        type_=base_type,
                    )
                else:
                    cls_kwargs[name] = self(inner_type)
            elif name in own_defaults:
                cls_kwargs[name] = own_defaults[name]

        if args_dict and var_positional_name is None:
            arg_type, arg = next(iter(args_dict.items()))
            raise UnknownArgument(
                f"Cannot determine where to use argument `{arg}` of type `{arg_type}`",
                argument_type=arg_type,
                argument=arg,
            )

        if kwargs:
            if has_var_keyword:
                cls_kwargs.update(kwargs)
            else:
                name, arg = next(iter(kwargs.items()))
                raise UnknownKeywordArgument(
                    f"Did not find keyword argument `{name}` in `{cls.__name__}.__init__`",
                    argument_name=name,
                    argument_type=type(arg),
                    argument=arg,
                )

        bound = sig.bind_partial(**cls_kwargs)
        if args_dict:
            bound.arguments[var_positional_name] = tuple(args_dict.values())
        instance = cls(*bound.args, **bound.kwargs)

        if inspect.isclass(cls) and Singleton in inspect.getmro(cls):
            with self._lock:
                if cls not in self._singletons:
                    self._singletons[cls] = instance
                else:
                    instance = self._singletons[cls]

        return instance

    def when(self, consumer) -> _WhenBuilder:
        """Start a contextual binding rule for consumer."""
        return _WhenBuilder(self, consumer)

    def _add_contextual(self, consumer, needed, factory=None, kw=None) -> None:
        key = (consumer, needed)
        with self._lock:
            if key not in self._contextual:
                self._contextual[key] = {"factory": None, "kwargs": {}}
            entry = self._contextual[key]
            if factory is not None:
                entry["factory"] = factory
            if kw:
                entry["kwargs"].update(kw)

    def _resolve_contextual(self, ctx: dict, target_type) -> object:
        factory = ctx.get("factory")
        ctx_kwargs = ctx.get("kwargs", {})

        if factory is None:
            if typing.get_origin(target_type) is typing.Annotated:
                base = typing.get_args(target_type)[0]
                return self._make(base, _contextual_key=target_type, **ctx_kwargs)
            return self._make(target_type, **ctx_kwargs)

        if typing.get_origin(factory) is typing.Annotated:
            base = typing.get_args(factory)[0]
            return self._make(base, _contextual_key=factory, **ctx_kwargs)

        if inspect.isclass(factory):
            return self._make(factory, **ctx_kwargs)

        return factory()  # plain callable — ctx_kwargs ignored

    def bind(self, cls: type[T], factory=None) -> None:
        """Register a factory for cls. Each call to resolver(cls) invokes the factory anew."""
        if typing.get_origin(cls) is typing.Annotated:
            base_type = typing.get_args(cls)[0]
            if _is_primitive(base_type):
                raise UnresolvablePrimitive(
                    f"`{base_type.__name__}` is a primitive type and cannot be registered"
                    " with the resolver",
                    type_=base_type,
                )
            if factory is None:

                def factory(*a, **kw):
                    return self._make(base_type, *a, **kw)
            elif inspect.isclass(factory):
                _check_subclass(base_type, factory)
                concrete_cls = factory

                def factory(*a, **kw):
                    return self._make(concrete_cls, *a, **kw)

            with self._lock:
                self._factories[cls] = factory
            return
        if _is_primitive(cls):
            raise UnresolvablePrimitive(
                f"`{cls.__name__}` is a primitive type and cannot be registered with the resolver",
                type_=cls,
            )
        if factory is None:

            def factory(*a, **kw):
                return self._make(cls, *a, **kw)
        elif inspect.isclass(factory):
            _check_subclass(cls, factory)
            concrete_cls = factory

            def factory(*a, **kw):
                return self._make(concrete_cls, *a, **kw)

        with self._lock:
            self._factories[cls] = factory

    def singleton(self, cls_or_instance, instance=None) -> T:  # type: ignore[misc]
        """Register or retrieve a singleton.

        Forms:
          resolver.singleton(MyClass)              # create and register
          resolver.singleton(MyClass, my_instance) # register existing instance
          resolver.singleton(my_instance)          # infer class from instance
          resolver.singleton(Annotated[T, spec], my_instance)  # qualified binding
        """
        if typing.get_origin(cls_or_instance) is typing.Annotated:
            key = cls_or_instance
            base_type = typing.get_args(cls_or_instance)[0]
            if _is_primitive(base_type):
                raise UnresolvablePrimitive(
                    f"`{base_type.__name__}` is a primitive type and cannot be registered"
                    " with the resolver",
                    type_=base_type,
                )
            if instance is None:
                with self._lock:
                    if key in self._singletons:
                        return self._singletons[key]
                    instance = self._make(base_type)
                    self._singletons[key] = instance
                return instance
            _check_instance_type(base_type, instance)
            with self._lock:
                self._singletons[key] = instance
            return instance

        target_cls = (
            cls_or_instance
            if inspect.isclass(cls_or_instance) or instance is not None
            else type(cls_or_instance)
        )
        if _is_primitive(target_cls):
            raise UnresolvablePrimitive(
                f"`{target_cls.__name__}` is a primitive type and cannot be registered"
                " with the resolver",
                type_=target_cls,
            )

        if instance is None:
            if inspect.isclass(cls_or_instance):
                with self._lock:
                    if cls_or_instance in self._singletons:
                        return self._singletons[cls_or_instance]
                    instance = self._make(cls_or_instance)
                    self._singletons[cls_or_instance] = instance
                return instance
            else:
                instance = cls_or_instance
                cls_or_instance = type(instance)
        else:
            if inspect.isclass(cls_or_instance):
                _check_instance_type(cls_or_instance, instance)
        with self._lock:
            self._singletons[cls_or_instance] = instance
        return instance

    def __contains__(self, cls: type) -> bool:
        return cls in self._singletons or cls in self._factories

    def clear(self, cls: type | None = None) -> None:
        """Remove a binding. With no argument, clears all bindings."""
        with self._lock:
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
        with self._lock:
            new_resolver = Resolver()
            source = (
                [t for t in self._singletons if t is not Resolver] if not types else list(types)
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
            with cls._global_lock:
                if cls._global_instance is None:
                    cls._global_instance = Resolver()
        return cls._global_instance

    @classmethod
    def reset(cls) -> None:
        """Discard the process-wide Resolver."""
        with cls._global_lock:
            cls._global_instance = None


_GIVE_UNSET = object()


class _WhenBuilder:
    def __init__(self, resolver: Resolver, consumer):
        self._resolver = resolver
        self._consumer = consumer

    def needs(self, needed) -> _NeedsBuilder:
        return _NeedsBuilder(self._resolver, self._consumer, needed)

    def give(self, **kwargs) -> _WhenBuilder:
        """Contribute default kwargs to the consumer's own constructor."""
        if kwargs:
            self._resolver._add_contextual(self._consumer, _NO_NEEDS, kw=kwargs)
        return self


class _NeedsBuilder:
    def __init__(self, resolver: Resolver, consumer, needed):
        self._resolver = resolver
        self._consumer = consumer
        self._needed = needed

    def give(self, factory=_GIVE_UNSET, **kwargs) -> None:
        """
        Forms:
          give(factory_or_class)         — replacement
          give(factory_or_class, **kw)   — replacement + kwargs to its constructor
          give(**kw)                     — kwargs contribution only (no replacement)
        """
        f = None if factory is _GIVE_UNSET else factory
        if f is not None or kwargs:
            self._resolver._add_contextual(self._consumer, self._needed, factory=f, kw=kwargs)
