from typing import NewType, Optional

import pytest

from ioc import (
    Resolver,
    ResolutionFailure,
    Singleton,
    DuplicateArgOfSameType,
    UnboundTypeRequested,
    UnknownArgument,
    UnknownKeywordArgument,
)


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


class HasVarArgs:
    def __init__(self, dep: NoArgumentClass, *args):
        self.dep = dep
        self.args = args


class HasVarKwargs:
    def __init__(self, dep: NoArgumentClass, **kwargs):
        self.dep = dep
        self.kwargs = kwargs


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
# Global resolver
# ---------------------------------------------------------------------------

class TestGlobalResolverAdditional:
    def test_get_returns_self_resolving_resolver(self):
        r = Resolver.get()
        assert r(Resolver) is r


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
