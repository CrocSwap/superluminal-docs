from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Generic, TypeVar, cast

from sortedcontainers import SortedDict

V = TypeVar("V")


class SortedIntMap(Generic[V]):
    __slots__ = ("_data",)

    def __init__(self) -> None:
        self._data: Any = SortedDict()

    def __contains__(self, key: int) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __getitem__(self, key: int) -> V:
        return cast(V, self._data[key])

    def __setitem__(self, key: int, value: V) -> None:
        self._data[key] = value

    def clear(self) -> None:
        self._data.clear()

    def get_optional(self, key: int) -> V | None:
        return cast(V | None, self._data.get(key))

    def pop(self, key: int, default: V | None = None) -> V | None:
        return cast(V | None, self._data.pop(key, default))

    def keys(self) -> Iterator[int]:
        return cast(Iterator[int], iter(self._data.keys()))

    def reversed_keys(self) -> Iterator[int]:
        return cast(Iterator[int], reversed(self._data.keys()))

    def last_key(self) -> int | None:
        if len(self._data) == 0:
            return None
        return cast(int, self._data.peekitem(-1)[0])

    def lower_key(self, key: int) -> int | None:
        index = self._data.bisect_left(key) - 1
        if index < 0:
            return None
        return cast(int, self._data.peekitem(index)[0])
