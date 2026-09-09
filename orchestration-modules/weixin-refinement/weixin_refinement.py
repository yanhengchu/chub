"""First external Weixin refinement implementation.

The module deliberately has one input: Chub's bounded refinement callback.
It does not receive a route, Session, Worker, path, command or network handle.
"""

from collections.abc import Callable


def execute_refinement(*, enqueue_refinement: Callable[[], object]) -> object:
    return enqueue_refinement()
