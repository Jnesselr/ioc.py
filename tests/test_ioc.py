import abc
import threading
from typing import Annotated, NewType, Optional, Protocol

import pytest

from ioc import (
    Resolver,
    ResolutionFailure,
    Singleton,
    CircularDependency,
    DuplicateArgOfSameType,
    InvalidBinding,
    UnboundTypeRequested,
    UnknownArgument,
    UnknownKeywordArgument,
    UnresolvablePrimitive,
)


# ---------------------------------------------------------------------------
# Annotated test helpers
# ---------------------------------------------------------------------------

class _Base:
    pass


class _FooQualifier:
    pass


class _BarQualifier:
    pass


FooBase = Annotated[_Base, _FooQualifier]
BarBase = Annotated[_Base, _BarQualifier]


class _UsesFooAndBar:
    def __init__(self, foo: FooBase, bar: BarBase):
        self.foo = foo
        self.bar = bar


class _UsesOptionalFoo:
    def __init__(self, foo: Optional[FooBase] = None):
        self.foo = foo


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

class NoArgumentClass:
    pass


class OneAnnotatedArgumentClass:
    def __init__(self, arg: NoArgumentClass):
        self.arg = arg


class OptionalAnnotatedArgumentClass:
    def __init__(self, arg: Optional[NoArgumentClass]):
        self.arg = arg


class OptionalUnionSyntaxClass:
    def __init__(self, arg: NoArgumentClass | None):
        self.arg = arg


AcsInstance = NewType('AcsInstance', NoArgumentClass)
LogInstance = NewType('LogInstance', NoArgumentClass)


class UsesBothNewTypes:
    def __init__(self, log: LogInstance, acs: AcsInstance):
        self.log: NoArgumentClass = log
        self.acs: NoArgumentClass = acs


class OneInt:
    def __init__(self, num: int):
        self.num = num


class TwoInts:
    def __init__(self, a: int, b: int):
        self.a = a
        self.b = b


class AutoSingletonClass(Singleton):
    pass


class HasIntDefault:
    def __init__(self, count: int = 5):
        self.count = count


class HasClassDefault:
    def __init__(self, dep: NoArgumentClass | None = None):
        self.dep = dep


class HasVarArgs:
    def __init__(self, dep: NoArgumentClass, *args):
        self.dep = dep
        self.args = args


class HasVarKwargs:
    def __init__(self, dep: NoArgumentClass, **kwargs):
        self.dep = dep
        self.kwargs = kwargs


# ---------------------------------------------------------------------------
# Contextual binding helpers
# ---------------------------------------------------------------------------

class _Server:
    def __init__(self, host: str = 'default', port: int = 80):
        self.host = host
        self.port = port


class _CacheWithTtl:
    def __init__(self, ttl: int = 3600, max_entries: int = 100):
        self.ttl = ttl
        self.max_entries = max_entries


class _ServiceUsingCache:
    def __init__(self, cache: _CacheWithTtl):
        self.cache = cache


class _OtherServiceUsingCache:
    def __init__(self, cache: _CacheWithTtl):
        self.cache = cache


class _ServiceUsingServer:
    def __init__(self, server: _Server):
        self.server = server


_SpecialCacheKey = object()
SpecialCache = Annotated[_CacheWithTtl, _SpecialCacheKey]


class _ServiceUsingSpecialCache:
    def __init__(self, cache: SpecialCache):
        self.cache = cache


# Circular dependency helpers — forward refs resolved at call time by get_type_hints
class _CircA:
    def __init__(self, dep: '_CircB'):
        self.dep = dep


class _CircB:
    def __init__(self, dep: _CircA):
        self.dep = dep


class _ChainA:
    def __init__(self, dep: '_ChainB'):
        self.dep = dep


class _ChainB:
    def __init__(self, dep: '_ChainC'):
        self.dep = dep


class _ChainC:
    def __init__(self, dep: _ChainA):
        self.dep = dep


# ---------------------------------------------------------------------------
# Basic resolution
# ---------------------------------------------------------------------------

class TestBasicResolution:
    def test_resolves_itself(self, resolver: Resolver):
        assert resolver(Resolver) is resolver

    def test_resolves_no_argument_class(self, resolver: Resolver):
        obj = resolver(NoArgumentClass)
        assert isinstance(obj, NoArgumentClass)

    def test_resolves_annotated_class_implicitly(self, resolver: Resolver):
        obj = resolver(OneAnnotatedArgumentClass)
        assert isinstance(obj, OneAnnotatedArgumentClass)
        assert isinstance(obj.arg, NoArgumentClass)

    def test_resolves_annotated_class_with_explicit_arg(self, resolver: Resolver):
        arg = resolver(NoArgumentClass)
        obj = resolver(OneAnnotatedArgumentClass, arg)
        assert isinstance(obj, OneAnnotatedArgumentClass)
        assert obj.arg is arg

    def test_default_resolution_creates_new_objects_each_time(self, resolver: Resolver):
        obj_a = resolver(OneAnnotatedArgumentClass)
        obj_b = resolver(OneAnnotatedArgumentClass)
        assert obj_a is not obj_b
        assert obj_a.arg is not obj_b.arg

    def test_singleton_via_method_with_no_instance(self, resolver: Resolver):
        instance = resolver.singleton(NoArgumentClass)
        assert resolver(NoArgumentClass) is instance
        assert resolver(NoArgumentClass) is instance

    def test_singleton_via_method_with_explicit_instance(self, resolver: Resolver):
        instance = NoArgumentClass()
        returned = resolver.singleton(NoArgumentClass, instance)
        assert returned is instance
        assert resolver(NoArgumentClass) is instance

    def test_singleton_via_method_with_instance_only(self, resolver: Resolver):
        instance = NoArgumentClass()
        returned = resolver.singleton(instance)
        assert returned is instance
        assert resolver(NoArgumentClass) is instance

    def test_calling_singleton_twice_returns_same_instance(self, resolver: Resolver):
        a = resolver.singleton(NoArgumentClass)
        b = resolver.singleton(NoArgumentClass)
        assert a is b

    def test_newtype_resolution(self, resolver: Resolver):
        acs = resolver.singleton(AcsInstance, AcsInstance(NoArgumentClass()))
        log = resolver.singleton(LogInstance, LogInstance(NoArgumentClass()))
        obj = resolver(UsesBothNewTypes)
        assert obj.log is log
        assert obj.acs is acs

    def test_contains_true_for_singleton(self, resolver: Resolver):
        assert NoArgumentClass not in resolver
        resolver.singleton(NoArgumentClass)
        assert NoArgumentClass in resolver

    def test_contains_true_for_factory_binding(self, resolver: Resolver):
        assert NoArgumentClass not in resolver
        resolver.bind(NoArgumentClass)
        assert NoArgumentClass in resolver

    def test_contains_false_for_implicitly_resolved_class(self, resolver: Resolver):
        resolver(OneAnnotatedArgumentClass)
        assert OneAnnotatedArgumentClass not in resolver

    def test_optional_resolves_via_factory_when_bound(self, resolver: Resolver):
        resolver.bind(NoArgumentClass)
        obj = resolver(OptionalAnnotatedArgumentClass)
        assert isinstance(obj.arg, NoArgumentClass)

    def test_singleton_abc_auto_registers(self, resolver: Resolver):
        first = resolver(AutoSingletonClass)
        second = resolver(AutoSingletonClass)
        assert first is second
        assert AutoSingletonClass in resolver


# ---------------------------------------------------------------------------
# Args and kwargs
# ---------------------------------------------------------------------------

class TestArgsAndKwargs:
    def test_single_primitive_by_positional_arg(self, resolver: Resolver):
        obj = resolver(OneInt, 5)
        assert obj.num == 5

    def test_two_same_primitive_types_by_positional_raises(self, resolver: Resolver):
        with pytest.raises(DuplicateArgOfSameType) as exc_info:
            resolver(TwoInts, 3, 4)
        ex = exc_info.value
        assert ex.duplicate_type is int
        assert ex.arguments == [3, 4]

    def test_two_same_primitives_resolved_when_one_is_kwarg(self, resolver: Resolver):
        obj = resolver(TwoInts, 3, b=4)
        assert obj.a == 3
        assert obj.b == 4

    def test_unmatched_positional_arg_raises(self, resolver: Resolver):
        with pytest.raises(UnknownArgument) as exc_info:
            resolver(NoArgumentClass, 3)
        ex = exc_info.value
        assert ex.argument_type is int
        assert ex.argument == 3

    def test_unmatched_kwarg_raises(self, resolver: Resolver):
        with pytest.raises(UnknownKeywordArgument) as exc_info:
            resolver(NoArgumentClass, a=3)
        ex = exc_info.value
        assert ex.argument_type is int
        assert ex.argument_name == 'a'
        assert ex.argument == 3

    def test_optional_arg_is_none_when_not_bound(self, resolver: Resolver):
        obj = resolver(OptionalAnnotatedArgumentClass)
        assert isinstance(obj, OptionalAnnotatedArgumentClass)
        assert obj.arg is None

    def test_optional_arg_resolved_when_singleton_bound(self, resolver: Resolver):
        arg = resolver.singleton(NoArgumentClass)
        obj = resolver(OptionalAnnotatedArgumentClass)
        assert obj.arg is arg

    def test_optional_union_syntax_is_none_when_not_bound(self, resolver: Resolver):
        obj = resolver(OptionalUnionSyntaxClass)
        assert obj.arg is None

    def test_optional_union_syntax_resolved_when_singleton_bound(self, resolver: Resolver):
        arg = resolver.singleton(NoArgumentClass)
        obj = resolver(OptionalUnionSyntaxClass)
        assert obj.arg is arg

    def test_unannotated_param_resolved_by_kwarg(self, resolver: Resolver):
        class UnannotatedClass:
            def __init__(self, value):
                self.value = value

        obj = resolver(UnannotatedClass, value=42)
        assert obj.value == 42


# ---------------------------------------------------------------------------
# bind() — factory pattern
# ---------------------------------------------------------------------------

class TestBind:
    def test_bind_with_custom_factory_creates_new_instance_each_time(self, resolver: Resolver):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return NoArgumentClass()

        resolver.bind(NoArgumentClass, factory)

        first = resolver(NoArgumentClass)
        second = resolver(NoArgumentClass)

        assert first is not second
        assert call_count == 2

    def test_bind_without_factory_creates_new_instance_each_time(self, resolver: Resolver):
        resolver.bind(NoArgumentClass)
        first = resolver(NoArgumentClass)
        second = resolver(NoArgumentClass)
        assert first is not second

    def test_bind_can_be_overridden_by_singleton(self, resolver: Resolver):
        resolver.bind(NoArgumentClass)
        instance = NoArgumentClass()
        resolver.singleton(NoArgumentClass, instance)
        assert resolver(NoArgumentClass) is instance


# ---------------------------------------------------------------------------
# clear()
# ---------------------------------------------------------------------------

class TestClear:
    def test_clear_specific_class_removes_singleton(self, resolver: Resolver):
        instance = NoArgumentClass()
        resolver.singleton(NoArgumentClass, instance)
        assert resolver(NoArgumentClass) is instance

        resolver.clear(NoArgumentClass)

        assert resolver(NoArgumentClass) is not instance

    def test_clear_specific_class_removes_factory(self, resolver: Resolver):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return NoArgumentClass()

        resolver.bind(NoArgumentClass, factory)
        resolver(NoArgumentClass)
        assert call_count == 1

        resolver.clear(NoArgumentClass)
        resolver(NoArgumentClass)
        # After clear the factory is gone; implicit _make is used instead
        assert call_count == 1

    def test_clear_unbound_class_does_not_raise(self, resolver: Resolver):
        resolver.clear(NoArgumentClass)

    def test_clear_all_removes_all_singletons(self, resolver: Resolver):
        instance = NoArgumentClass()
        resolver.singleton(NoArgumentClass, instance)

        resolver.clear()

        assert resolver(NoArgumentClass) is not instance

    def test_clear_all_removes_singleton_so_next_resolve_is_fresh(self, resolver: Resolver):
        resolver.singleton(NoArgumentClass)
        first = resolver(NoArgumentClass)

        resolver.clear()

        second = resolver(NoArgumentClass)
        assert first is not second

    def test_clear_all_keeps_resolver_self_reference(self, resolver: Resolver):
        resolver.clear()
        assert resolver(Resolver) is resolver


# ---------------------------------------------------------------------------
# Resolver.get() / Resolver.reset()
# ---------------------------------------------------------------------------

class TestGlobalResolver:
    def test_get_returns_same_instance(self):
        r1 = Resolver.get()
        r2 = Resolver.get()
        assert r1 is r2

    def test_reset_creates_new_instance_on_next_get(self):
        r1 = Resolver.get()
        Resolver.reset()
        r2 = Resolver.get()
        assert r1 is not r2

    def test_get_returns_self_resolving_resolver(self):
        r = Resolver.get()
        assert r(Resolver) is r


# ---------------------------------------------------------------------------
# clone()
# ---------------------------------------------------------------------------

class TestCloning:
    def test_clone_all_copies_singleton_bindings(self, resolver: Resolver):
        a = resolver.singleton(NoArgumentClass)
        r2 = resolver.clone()
        assert NoArgumentClass in r2
        assert r2(NoArgumentClass) is a

    def test_clone_with_specified_types_copies_only_those(self, resolver: Resolver):
        a = resolver.singleton(NoArgumentClass)
        b = resolver.singleton(OneAnnotatedArgumentClass)

        r2 = resolver.clone(OneAnnotatedArgumentClass)

        assert NoArgumentClass not in r2
        assert r2(NoArgumentClass) is not a

        assert OneAnnotatedArgumentClass in r2
        assert r2(OneAnnotatedArgumentClass) is b

    def test_clone_unbound_type_raises(self, resolver: Resolver):
        with pytest.raises(UnboundTypeRequested) as exc_info:
            resolver.clone(NoArgumentClass)
        assert exc_info.value.type is NoArgumentClass

    def test_clone_does_not_share_new_bindings(self, resolver: Resolver):
        a = resolver.singleton(NoArgumentClass)
        r2 = resolver.clone()

        new_instance = NoArgumentClass()
        r2.singleton(NoArgumentClass, new_instance)

        assert resolver(NoArgumentClass) is a
        assert r2(NoArgumentClass) is new_instance

    def test_cloned_resolver_resolves_to_itself(self, resolver: Resolver):
        r2 = resolver.clone()
        assert r2(Resolver) is r2

    def test_clone_does_not_copy_factory_bindings(self, resolver: Resolver):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return NoArgumentClass()

        resolver.bind(NoArgumentClass, factory)
        r2 = resolver.clone()

        assert NoArgumentClass not in r2
        r2(NoArgumentClass)
        assert call_count == 0  # clone's resolve went through _make, not the factory


# ---------------------------------------------------------------------------
# Singleton ABC
# ---------------------------------------------------------------------------

class TestSingletonABC:
    def test_auto_registers_on_first_resolve(self, resolver: Resolver):
        first = resolver(AutoSingletonClass)
        second = resolver(AutoSingletonClass)
        assert first is second
        assert AutoSingletonClass in resolver

    def test_survives_clear_of_specific_class(self, resolver: Resolver):
        first = resolver(AutoSingletonClass)
        resolver.clear(AutoSingletonClass)
        second = resolver(AutoSingletonClass)
        assert second is not first
        assert AutoSingletonClass in resolver

    def test_fresh_after_reset(self):
        r1 = Resolver.get()
        first = r1(AutoSingletonClass)
        Resolver.reset()
        r2 = Resolver.get()
        second = r2(AutoSingletonClass)
        assert second is not first
        assert AutoSingletonClass in r2


# ---------------------------------------------------------------------------
# clear() — additional cases
# ---------------------------------------------------------------------------

class TestClearAdditional:
    def test_clear_all_removes_factory_bindings(self, resolver: Resolver):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return NoArgumentClass()

        resolver.bind(NoArgumentClass, factory)
        resolver.clear()
        resolver(NoArgumentClass)
        assert call_count == 0  # factory is gone; implicit _make used instead


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class TestExceptionHierarchy:
    def test_duplicate_arg_is_resolution_failure(self, resolver: Resolver):
        with pytest.raises(ResolutionFailure):
            resolver(TwoInts, 3, 4)

    def test_unknown_argument_is_resolution_failure(self, resolver: Resolver):
        with pytest.raises(ResolutionFailure):
            resolver(NoArgumentClass, 3)

    def test_unknown_kwarg_is_resolution_failure(self, resolver: Resolver):
        with pytest.raises(ResolutionFailure):
            resolver(NoArgumentClass, a=3)

    def test_unknown_kwarg_is_unknown_argument(self, resolver: Resolver):
        with pytest.raises(UnknownArgument):
            resolver(NoArgumentClass, a=3)

    def test_unresolvable_primitive_is_resolution_failure(self, resolver: Resolver):
        with pytest.raises(ResolutionFailure):
            resolver(OneInt)

    def test_resolving_primitive_directly_raises(self, resolver: Resolver):
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver(int)
        assert exc_info.value.type is int


# ---------------------------------------------------------------------------
# *args and **kwargs passthrough
# ---------------------------------------------------------------------------

class TestVariadicPassthrough:
    def test_extra_kwargs_passed_through_to_var_keyword(self, resolver: Resolver):
        dep = resolver.singleton(NoArgumentClass)
        obj = resolver(HasVarKwargs, extra=42, label="hi")
        assert obj.dep is dep
        assert obj.kwargs == {'extra': 42, 'label': 'hi'}

    def test_named_dep_resolved_and_unmatched_kwargs_passed_through(self, resolver: Resolver):
        resolver.singleton(NoArgumentClass)
        obj = resolver(HasVarKwargs, dep=NoArgumentClass(), note="x")
        assert isinstance(obj.dep, NoArgumentClass)
        assert obj.kwargs == {'note': 'x'}

    def test_extra_positional_args_passed_through_to_var_positional(self, resolver: Resolver):
        dep = resolver.singleton(NoArgumentClass)
        obj = resolver(HasVarArgs, "extra", 42)
        assert obj.dep is dep
        assert obj.args == ("extra", 42)

    def test_no_extra_args_var_positional_is_empty(self, resolver: Resolver):
        obj = resolver(HasVarArgs)
        assert isinstance(obj.dep, NoArgumentClass)
        assert obj.args == ()


# ---------------------------------------------------------------------------
# Default values
# ---------------------------------------------------------------------------

class TestDefaults:
    def test_primitive_with_default_uses_default(self, resolver: Resolver):
        obj = resolver(HasIntDefault)
        assert obj.count == 5

    def test_primitive_with_no_default_raises(self, resolver: Resolver):
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver(OneInt)
        assert exc_info.value.type is int

    def test_class_default_used_when_type_not_registered(self, resolver: Resolver):
        obj = resolver(HasClassDefault)
        assert obj.dep is None

    def test_class_default_overridden_when_type_is_registered(self, resolver: Resolver):
        dep = resolver.singleton(NoArgumentClass)
        obj = resolver(HasClassDefault)
        assert obj.dep is dep

    def test_kwarg_overrides_default(self, resolver: Resolver):
        explicit = NoArgumentClass()
        obj = resolver(HasClassDefault, dep=explicit)
        assert obj.dep is explicit

    def test_cannot_register_primitive_via_singleton_class_form(self, resolver: Resolver):
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver.singleton(int, 99)
        assert exc_info.value.type is int

    def test_cannot_register_primitive_via_singleton_instance_form(self, resolver: Resolver):
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver.singleton(42)
        assert exc_info.value.type is int

    def test_cannot_register_primitive_via_bind(self, resolver: Resolver):
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver.bind(str)
        assert exc_info.value.type is str


# ---------------------------------------------------------------------------
# Annotated / qualified bindings
# ---------------------------------------------------------------------------

class TestAnnotated:
    def test_annotated_singleton_resolved_via_annotation(self, resolver: Resolver):
        foo = _Base()
        resolver.singleton(FooBase, foo)
        obj = resolver(_UsesFooAndBar, bar=_Base())
        assert obj.foo is foo

    def test_two_annotated_keys_same_class_different_instances(self, resolver: Resolver):
        foo = _Base()
        bar = _Base()
        resolver.singleton(FooBase, foo)
        resolver.singleton(BarBase, bar)
        obj = resolver(_UsesFooAndBar)
        assert obj.foo is foo
        assert obj.bar is bar
        assert obj.foo is not obj.bar

    def test_annotated_type_alias_works_as_annotation(self, resolver: Resolver):
        foo = _Base()
        bar = _Base()
        resolver.singleton(FooBase, foo)
        resolver.singleton(BarBase, bar)
        result = resolver(_UsesFooAndBar)
        assert result.foo is foo
        assert result.bar is bar

    def test_annotated_factory_resolved_via_annotation(self, resolver: Resolver):
        call_count = 0

        def factory():
            nonlocal call_count
            call_count += 1
            return _Base()

        resolver.bind(FooBase, factory)
        resolver.singleton(BarBase, _Base())
        resolver(_UsesFooAndBar)
        resolver(_UsesFooAndBar)
        assert call_count == 2

    def test_annotated_hard_crash_when_not_registered(self, resolver: Resolver):
        resolver.singleton(BarBase, _Base())
        with pytest.raises(UnboundTypeRequested):
            resolver(_UsesFooAndBar)

    def test_annotated_direct_resolution_when_registered(self, resolver: Resolver):
        foo = _Base()
        resolver.singleton(FooBase, foo)
        assert resolver(FooBase) is foo

    def test_annotated_direct_resolution_raises_when_not_registered(self, resolver: Resolver):
        with pytest.raises(UnboundTypeRequested) as exc_info:
            resolver(FooBase)
        assert exc_info.value.type is FooBase

    def test_annotated_contains_true_after_registration(self, resolver: Resolver):
        assert FooBase not in resolver
        resolver.singleton(FooBase, _Base())
        assert FooBase in resolver

    def test_annotated_singleton_created_from_base_type(self, resolver: Resolver):
        instance = resolver.singleton(FooBase)
        assert isinstance(instance, _Base)
        assert resolver(FooBase) is instance

    def test_annotated_bind_no_factory_creates_new_each_time(self, resolver: Resolver):
        resolver.bind(FooBase)
        first = resolver(FooBase)
        second = resolver(FooBase)
        assert isinstance(first, _Base)
        assert first is not second

    def test_optional_annotated_is_none_when_not_registered(self, resolver: Resolver):
        obj = resolver(_UsesOptionalFoo)
        assert obj.foo is None

    def test_optional_annotated_resolved_when_registered(self, resolver: Resolver):
        foo = _Base()
        resolver.singleton(FooBase, foo)
        obj = resolver(_UsesOptionalFoo)
        assert obj.foo is foo

    def test_annotated_primitive_blocked_in_singleton(self, resolver: Resolver):
        AnnotatedInt = Annotated[int, _FooQualifier]
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver.singleton(AnnotatedInt, 42)
        assert exc_info.value.type is int

    def test_annotated_primitive_blocked_in_bind(self, resolver: Resolver):
        AnnotatedStr = Annotated[str, _FooQualifier]
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver.bind(AnnotatedStr)
        assert exc_info.value.type is str


# ---------------------------------------------------------------------------
# Instance type validation
# ---------------------------------------------------------------------------

class _Sub(NoArgumentClass):
    pass


# ---------------------------------------------------------------------------
# Abstract/Protocol binding test helpers
# ---------------------------------------------------------------------------

class _AbstractService(abc.ABC):
    @abc.abstractmethod
    def value(self) -> int: ...


class _ConcreteService(_AbstractService):
    def value(self) -> int:
        return 42


class _ConcreteServiceWithDep(_AbstractService):
    def __init__(self, dep: NoArgumentClass):
        self.dep = dep

    def value(self) -> int:
        return 0


class _UnrelatedService:
    pass


class _ServiceProtocol(Protocol):
    def value(self) -> int: ...


class _ProtocolImpl:
    def value(self) -> int:
        return 99


class TestInstanceTypeValidation:
    def test_valid_instance_registers_fine(self, resolver: Resolver):
        instance = NoArgumentClass()
        resolver.singleton(NoArgumentClass, instance)
        assert resolver(NoArgumentClass) is instance

    def test_subclass_instance_registers_fine(self, resolver: Resolver):
        instance = _Sub()
        resolver.singleton(NoArgumentClass, instance)
        assert resolver(NoArgumentClass) is instance

    def test_wrong_type_raises_invalid_binding(self, resolver: Resolver):
        with pytest.raises(InvalidBinding) as exc_info:
            resolver.singleton(NoArgumentClass, OneAnnotatedArgumentClass(NoArgumentClass()))
        ex = exc_info.value
        assert ex.expected_type is NoArgumentClass
        assert isinstance(ex.instance, OneAnnotatedArgumentClass)

    def test_invalid_binding_is_resolution_failure(self, resolver: Resolver):
        with pytest.raises(ResolutionFailure):
            resolver.singleton(NoArgumentClass, OneAnnotatedArgumentClass(NoArgumentClass()))

    def test_instance_only_form_always_valid(self, resolver: Resolver):
        instance = NoArgumentClass()
        returned = resolver.singleton(instance)
        assert returned is instance

    def test_annotated_wrong_type_raises_invalid_binding(self, resolver: Resolver):
        with pytest.raises(InvalidBinding) as exc_info:
            resolver.singleton(FooBase, OneAnnotatedArgumentClass(NoArgumentClass()))
        ex = exc_info.value
        assert ex.expected_type is _Base
        assert isinstance(ex.instance, OneAnnotatedArgumentClass)

    def test_annotated_valid_instance_registers_fine(self, resolver: Resolver):
        instance = _Base()
        resolver.singleton(FooBase, instance)
        assert resolver(FooBase) is instance


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestThreadSafety:
    _N = 50

    def test_singleton_method_returns_same_instance_under_contention(self, resolver: Resolver):
        results = []
        barrier = threading.Barrier(self._N)

        def resolve():
            barrier.wait()
            results.append(resolver.singleton(NoArgumentClass))

        threads = [threading.Thread(target=resolve) for _ in range(self._N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == self._N
        assert all(r is results[0] for r in results)

    def test_singleton_abc_auto_registration_returns_same_instance_under_contention(self, resolver: Resolver):
        results = []
        barrier = threading.Barrier(self._N)

        def resolve():
            barrier.wait()
            results.append(resolver(AutoSingletonClass))

        threads = [threading.Thread(target=resolve) for _ in range(self._N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == self._N
        assert all(r is results[0] for r in results)

    def test_global_get_returns_same_instance_under_contention(self):
        results = []
        barrier = threading.Barrier(self._N)

        def get():
            barrier.wait()
            results.append(Resolver.get())

        threads = [threading.Thread(target=get) for _ in range(self._N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == self._N
        assert all(r is results[0] for r in results)


# ---------------------------------------------------------------------------
# Abstract / Protocol → concrete class binding
# ---------------------------------------------------------------------------

class TestAbstractBinding:
    def test_bind_abstract_to_concrete_resolves_concrete(self, resolver: Resolver):
        resolver.bind(_AbstractService, _ConcreteService)
        result = resolver(_AbstractService)
        assert isinstance(result, _ConcreteService)
        assert result.value() == 42

    def test_bind_abstract_to_concrete_creates_new_instance_each_time(self, resolver: Resolver):
        resolver.bind(_AbstractService, _ConcreteService)
        assert resolver(_AbstractService) is not resolver(_AbstractService)

    def test_bind_abstract_to_concrete_resolves_concrete_dependencies(self, resolver: Resolver):
        dep = resolver.singleton(NoArgumentClass)
        resolver.bind(_AbstractService, _ConcreteServiceWithDep)
        result = resolver(_AbstractService)
        assert isinstance(result, _ConcreteServiceWithDep)
        assert result.dep is dep

    def test_bind_abstract_to_unrelated_type_raises(self, resolver: Resolver):
        with pytest.raises(InvalidBinding) as exc_info:
            resolver.bind(_AbstractService, _UnrelatedService)
        ex = exc_info.value
        assert ex.expected_type is _AbstractService
        assert ex.instance is _UnrelatedService

    def test_bind_protocol_to_impl_skips_subclass_check(self, resolver: Resolver):
        # _ServiceProtocol is not @runtime_checkable so issubclass raises TypeError;
        # _check_subclass silently passes and the binding works
        resolver.bind(_ServiceProtocol, _ProtocolImpl)
        result = resolver(_ServiceProtocol)
        assert isinstance(result, _ProtocolImpl)
        assert result.value() == 99

    def test_bind_abstract_to_primitive_raises(self, resolver: Resolver):
        with pytest.raises(UnresolvablePrimitive) as exc_info:
            resolver.bind(_AbstractService, int)
        assert exc_info.value.type is int

    def test_abstract_bound_to_concrete_in_resolver(self, resolver: Resolver):
        resolver.bind(_AbstractService, _ConcreteService)
        assert _AbstractService in resolver

    def test_bind_abstract_to_concrete_overridable_by_singleton(self, resolver: Resolver):
        resolver.bind(_AbstractService, _ConcreteService)
        instance = _ConcreteService()
        resolver.singleton(_AbstractService, instance)
        assert resolver(_AbstractService) is instance


# ---------------------------------------------------------------------------
# Circular dependency detection
# ---------------------------------------------------------------------------

class TestCircularDependency:
    def test_two_way_cycle_raises(self, resolver: Resolver):
        with pytest.raises(CircularDependency):
            resolver(_CircA)

    def test_three_way_cycle_raises(self, resolver: Resolver):
        with pytest.raises(CircularDependency):
            resolver(_ChainA)

    def test_circular_dependency_is_resolution_failure(self, resolver: Resolver):
        with pytest.raises(ResolutionFailure):
            resolver(_CircA)

    def test_exception_type_is_re_entrant_type(self, resolver: Resolver):
        with pytest.raises(CircularDependency) as exc_info:
            resolver(_CircA)
        assert exc_info.value.type is _CircA

    def test_exception_chain_shows_full_cycle(self, resolver: Resolver):
        with pytest.raises(CircularDependency) as exc_info:
            resolver(_ChainA)
        chain = exc_info.value.chain
        assert chain[0] is _ChainA
        assert chain[-1] is _ChainA

    def test_resolver_still_works_after_circular_dependency_error(self, resolver: Resolver):
        with pytest.raises(CircularDependency):
            resolver(_CircA)
        obj = resolver(NoArgumentClass)
        assert isinstance(obj, NoArgumentClass)


# ---------------------------------------------------------------------------
# Contextual bindings — when / needs / give
# ---------------------------------------------------------------------------

class TestContextualBindings:
    # --- give(factory) replacement ---

    def test_give_factory_overrides_dependency(self, resolver: Resolver):
        special = _CacheWithTtl(ttl=999)
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(lambda: special)
        obj = resolver(_ServiceUsingCache)
        assert obj.cache is special

    def test_give_class_overrides_dependency(self, resolver: Resolver):
        class _AltCache(_CacheWithTtl):
            pass
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(_AltCache)
        obj = resolver(_ServiceUsingCache)
        assert type(obj.cache) is _AltCache

    def test_give_only_applies_to_named_consumer(self, resolver: Resolver):
        special = _CacheWithTtl(ttl=999)
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(lambda: special)
        other = resolver(_OtherServiceUsingCache)
        assert other.cache is not special

    # --- give(**kwargs) contribution ---

    def test_give_kwargs_passed_to_dependency_constructor(self, resolver: Resolver):
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(ttl=500)
        obj = resolver(_ServiceUsingCache)
        assert obj.cache.ttl == 500

    def test_give_kwargs_accumulate_across_calls(self, resolver: Resolver):
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(ttl=500)
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(max_entries=10)
        obj = resolver(_ServiceUsingCache)
        assert obj.cache.ttl == 500
        assert obj.cache.max_entries == 10

    def test_give_kwargs_last_wins_on_conflict(self, resolver: Resolver):
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(ttl=500)
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(ttl=999)
        obj = resolver(_ServiceUsingCache)
        assert obj.cache.ttl == 999

    # --- give(class, **kwargs) combined ---

    def test_give_class_with_kwargs(self, resolver: Resolver):
        class _AltCache(_CacheWithTtl):
            pass
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(_AltCache, ttl=42)
        obj = resolver(_ServiceUsingCache)
        assert type(obj.cache) is _AltCache
        assert obj.cache.ttl == 42

    def test_give_same_class_with_kwargs_is_valid(self, resolver: Resolver):
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(_CacheWithTtl, ttl=77)
        obj = resolver(_ServiceUsingCache)
        assert obj.cache.ttl == 77

    # --- give(Annotated, **kwargs) — contextual identity ---

    def test_give_annotated_uses_annotated_context(self, resolver: Resolver):
        resolver.when(SpecialCache).needs(_CacheWithTtl).give(ttl=123)
        # SpecialCache = Annotated[_CacheWithTtl, _SpecialCacheKey]
        # This is nonsensical (Cache needs itself) but demonstrates lookup_key
        # Better test: use give(SpecialCache) for a consumer
        resolver.when(_ServiceUsingSpecialCache).needs(SpecialCache).give(ttl=456)
        obj = resolver(_ServiceUsingSpecialCache)
        assert obj.cache.ttl == 456

    def test_give_annotated_profile_for_nested_context(self, resolver: Resolver):
        # PhotoController pattern: when(Consumer).needs(Dep).give(AnnotatedDep)
        # and when(AnnotatedDep).needs(SubDep).give(...)
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(SpecialCache, ttl=789)
        # when(_ServiceUsingCache) resolves _CacheWithTtl as SpecialCache context with ttl=789
        obj = resolver(_ServiceUsingCache)
        assert obj.cache.ttl == 789

    # --- when(X).give(**kwargs) — global defaults (no needs) ---

    def test_global_defaults_applied_to_own_constructor(self, resolver: Resolver):
        resolver.when(_CacheWithTtl).give(ttl=2000)
        cache = resolver(_CacheWithTtl)
        assert cache.ttl == 2000

    def test_global_defaults_apply_to_all_consumers(self, resolver: Resolver):
        resolver.when(_CacheWithTtl).give(ttl=2000)
        a = resolver(_ServiceUsingCache)
        b = resolver(_OtherServiceUsingCache)
        assert a.cache.ttl == 2000
        assert b.cache.ttl == 2000

    def test_global_defaults_overridden_by_contextual(self, resolver: Resolver):
        resolver.when(_CacheWithTtl).give(ttl=2000)
        resolver.when(_ServiceUsingCache).needs(_CacheWithTtl).give(ttl=500)
        a = resolver(_ServiceUsingCache)
        b = resolver(_OtherServiceUsingCache)
        assert a.cache.ttl == 500   # contextual wins
        assert b.cache.ttl == 2000  # global applies

    def test_global_defaults_overridden_by_explicit_kwarg(self, resolver: Resolver):
        resolver.when(_CacheWithTtl).give(ttl=2000)
        cache = resolver(_CacheWithTtl, ttl=99)
        assert cache.ttl == 99

    def test_global_defaults_for_unannotated_primitive_param(self, resolver: Resolver):
        resolver.when(_Server).give(host='myhost', port=9000)
        server = resolver(_Server)
        assert server.host == 'myhost'
        assert server.port == 9000

    # --- chaining multiple needs ---

    def test_chained_needs_on_same_when(self, resolver: Resolver):
        special_cache = _CacheWithTtl(ttl=1)
        special_server = _Server(host='x')

        class _Multi:
            def __init__(self, cache: _CacheWithTtl, server: _Server):
                self.cache = cache
                self.server = server

        (resolver.when(_Multi)
            .needs(_CacheWithTtl).give(lambda: special_cache)
            .needs(_Server).give(lambda: special_server))

        obj = resolver(_Multi)
        assert obj.cache is special_cache
        assert obj.server is special_server
