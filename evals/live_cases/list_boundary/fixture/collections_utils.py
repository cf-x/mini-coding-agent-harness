from typing import TypeVar

T = TypeVar("T")


def last_item(items: list[T]) -> T | None:
    if not items:
        return None
    return items[len(items)]
