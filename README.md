# type-ioc

A lightweight IoC/DI container for Python that wires dependencies automatically using type hints — no decorators, no config files, no magic strings.

```python
from ioc import Resolver

class Database:
    def __init__(self, url: str = "sqlite://"):
        self.url = url

class UserRepository:
    def __init__(self, db: Database):
        self.db = db

class UserService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

resolver = Resolver()
service = resolver(UserService)
# UserService → UserRepository → Database, all wired automatically
```

## Installation

```
pip install type-ioc
```
```
uv add type-ioc
```

## Basic resolution

Call the resolver with any class and it recursively resolves the full dependency graph via `__init__` type hints.

```python
resolver = Resolver()
service = resolver(UserService)
```

Each call creates a new instance unless the type is registered as a singleton. The resolver itself is always available:

```python
resolver(Resolver) is resolver  # True
```

## Singletons

Register a class as a singleton — the resolver creates the instance and caches it:

```python
db = resolver.singleton(Database)
resolver(Database) is db  # True
```

Register an existing instance:

```python
db = Database(url="postgresql://localhost/mydb")
resolver.singleton(Database, db)
```

Infer the class from the instance:

```python
resolver.singleton(db)  # same as resolver.singleton(Database, db)
```

### Singleton ABC

Inherit from `Singleton` to make a class register itself automatically on first resolution:

```python
from ioc import Singleton

class AppConfig(Singleton):
    def __init__(self):
        self.debug = False

first = resolver(AppConfig)
second = resolver(AppConfig)
first is second  # True — auto-registered after first call
```

## Factory bindings

`bind()` registers a factory that is called fresh on every resolution:

```python
resolver.bind(Database)  # creates a new Database each time
resolver(Database) is resolver(Database)  # False
```

Supply a custom factory:

```python
resolver.bind(Database, lambda: Database(url=os.environ["DB_URL"]))
```

Bind an abstract class or interface to a concrete implementation:

```python
import abc

class Storage(abc.ABC):
    @abc.abstractmethod
    def save(self, data: bytes) -> None: ...

class DiskStorage(Storage):
    def save(self, data: bytes) -> None: ...

resolver.bind(Storage, DiskStorage)
result = resolver(Storage)  # returns a DiskStorage instance
```

## Annotated / qualified bindings

Use `Annotated` to register multiple bindings for the same base type:

```python
from typing import Annotated

class _ReadKey: pass
class _WriteKey: pass

ReadDB = Annotated[Database, _ReadKey]
WriteDB = Annotated[Database, _WriteKey]

resolver.singleton(ReadDB, Database(url="postgresql://read-replica/mydb"))
resolver.singleton(WriteDB, Database(url="postgresql://primary/mydb"))
```

Any class whose `__init__` uses `ReadDB` or `WriteDB` as type hints will receive the correct instance automatically:

```python
class ReportingService:
    def __init__(self, db: ReadDB):
        self.db = db

class WriteService:
    def __init__(self, db: WriteDB):
        self.db = db

resolver(ReportingService).db is resolver(ReadDB)  # True
resolver(WriteService).db is resolver(WriteDB)     # True
```

`bind()` and `singleton()` both accept `Annotated` keys.

## Optional dependencies

Parameters typed as `T | None` or `Optional[T]` resolve to `None` when the type isn't registered, rather than raising an error:

```python
class Service:
    def __init__(self, cache: Cache | None = None):
        self.cache = cache

resolver(Service).cache  # None — Cache not registered, no error
resolver.singleton(Cache)
resolver(Service).cache  # Cache instance — now it's registered
```

## Passing arguments explicitly

Override any parameter by passing positional or keyword arguments directly to the resolver call. Positional arguments are matched by type:

```python
db = Database(url="postgresql://localhost/test")
repo = resolver(UserRepository, db)         # positional, matched by type
repo = resolver(UserRepository, db=db)      # keyword
```

Primitive parameters that have no default must be provided explicitly:

```python
class Paginator:
    def __init__(self, page: int, page_size: int = 20):
        ...

resolver(Paginator, page=1)
resolver(Paginator, 1)        # also works — matched by type
```

## Contextual bindings

Use `when/needs/give` to control how a specific consumer resolves one of its dependencies, without affecting any other consumer.

### Override with a factory or class

```python
class PhotoController:
    def __init__(self, storage: Storage):
        self.storage = storage

class LocalStorage(Storage):
    def save(self, data: bytes) -> None: ...

# Give PhotoController a LocalStorage specifically
resolver.when(PhotoController).needs(Storage).give(LocalStorage)

# Or supply a factory
resolver.when(PhotoController).needs(Storage).give(lambda: LocalStorage())
```

### Pass specific kwargs to a dependency's constructor

```python
class Cache:
    def __init__(self, ttl: int = 3600, max_entries: int = 100):
        ...

class SessionService:
    def __init__(self, cache: Cache):
        self.cache = cache

class ReportService:
    def __init__(self, cache: Cache):
        self.cache = cache

# Each service gets a Cache with different settings
resolver.when(SessionService).needs(Cache).give(ttl=300, max_entries=500)
resolver.when(ReportService).needs(Cache).give(ttl=7200)
```

Multiple `.give()` calls on the same `needs()` accumulate; last write wins on conflicts:

```python
resolver.when(SessionService).needs(Cache).give(ttl=300)
resolver.when(SessionService).needs(Cache).give(max_entries=500)
# SessionService gets Cache(ttl=300, max_entries=500)
```

### Override class and pass kwargs together

```python
class LruCache(Cache):
    def __init__(self, ttl: int = 3600, max_entries: int = 100, policy: str = "lru"):
        ...

resolver.when(ReportService).needs(Cache).give(LruCache, ttl=7200, max_entries=50)
```

### Global defaults

`when(X).give(**kwargs)` with no `needs()` sets default constructor arguments for every resolution of `X`, regardless of which consumer triggers it:

```python
resolver.when(Cache).give(ttl=600)

resolver(Cache).ttl                       # 600
resolver(SessionService).cache.ttl       # 600
resolver(ReportService).cache.ttl        # 600
```

Contextual `needs` rules take priority over global defaults:

```python
resolver.when(Cache).give(ttl=600)
resolver.when(ReportService).needs(Cache).give(ttl=7200)

resolver(SessionService).cache.ttl   # 600 — global default
resolver(ReportService).cache.ttl    # 7200 — contextual override
```

### Annotated types as resolution profiles

You can use an `Annotated` type as a named resolution profile. When a consumer's dependency is redirected to an `Annotated` type, any `when(AnnotatedType)` rules apply during that resolution — enabling nested contextual configuration:

```python
_ReportCacheKey = object()
ReportCache = Annotated[Cache, _ReportCacheKey]

# Set defaults for the ReportCache profile
resolver.when(ReportCache).give(ttl=7200, max_entries=1000)

# Consumers can type-hint ReportCache directly
class ReportService:
    def __init__(self, cache: ReportCache):
        ...

# Or redirect another consumer to use the ReportCache profile
resolver.when(AnalyticsService).needs(Cache).give(ReportCache)
```

## Global resolver

For applications that want a process-wide default resolver:

```python
resolver = Resolver.get()   # creates on first call, returns same instance thereafter
Resolver.reset()            # discards it; next .get() creates a fresh one
```

## Cloning

Create a child resolver that inherits specific singleton bindings from a parent. Pass as many types as you want to keep:

```python
parent = Resolver()
parent.singleton(Database, prod_db)
parent.singleton(AppConfig, config)
parent.singleton(UserRepository, repo)

# Inherit any subset of singletons; anything not listed must be re-registered or re-resolved
child = parent.clone(Database, UserRepository)
child.singleton(AppConfig, test_config)
```

`clone()` with no arguments copies all current singleton bindings:

```python
child = parent.clone()
```

Factory bindings are never copied — they belong to the resolver that registered them.

## Clearing bindings

Remove a specific binding:

```python
resolver.clear(Database)
```

Reset everything (singletons and factories) back to a blank state:

```python
resolver.clear()
```

The resolver always keeps itself registered after a clear.

## Circular dependency detection

Circular dependencies are detected at resolution time and raise `CircularDependency`:

```python
from ioc import CircularDependency

try:
    resolver(ServiceA)
except CircularDependency as e:
    print(e.chain)   # [ServiceA, ServiceB, ServiceA]
    print(e.type)    # ServiceA
```

The resolver remains fully usable after a `CircularDependency` is raised.

## Exception reference

All exceptions inherit from `ResolutionFailure`.

| Exception | Raised when |
|---|---|
| `CircularDependency` | A dependency cycle is detected. Has `.type` and `.chain`. |
| `DuplicateArgOfSameType` | Two positional arguments of the same type are passed. Has `.duplicate_type` and `.arguments`. |
| `InvalidBinding` | An instance or class fails its type check during `singleton()` or `bind()`. Has `.expected_type` and `.instance`. |
| `UnboundTypeRequested` | An `Annotated` type is used as a dependency but has no registered binding. Has `.type`. |
| `UnknownArgument` | A positional argument can't be matched to any constructor parameter. Has `.argument_type` and `.argument`. |
| `UnknownKeywordArgument` | A keyword argument doesn't match any constructor parameter. Has `.argument_name`, `.argument_type`, and `.argument`. |
| `UnresolvablePrimitive` | A builtin type (`int`, `str`, etc.) is encountered with no default and no explicit value provided. Has `.type`. |

## License

MIT
