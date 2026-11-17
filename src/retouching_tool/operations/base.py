"""Lifecycle required by operation factories; schedulers use StageSession.

Factories construct lightweight objects. Allocate resources only in prepare and
allow release after partial preparation; process must not mutate its input state.
"""

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from ..pipeline.executor import StageRuntime
    from ..pipeline.image_state import ImageState


class Operation(Protocol):
    runtime: StageRuntime

    def prepare(self, runtime: StageRuntime) -> None: ...

    def process(self, state: ImageState) -> ImageState: ...

    def release(self) -> None: ...
