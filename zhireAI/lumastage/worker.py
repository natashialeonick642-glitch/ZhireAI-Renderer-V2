"""C4D-compatible background network worker."""

from __future__ import annotations

import threading

import c4d

from .api import GenerationCancelled, ImageApiClient
from .c4d_capture import render_active_animation, render_active_document
from .constants import NETWORK_EVENT_ID, PROMPT_EVENT_ID
from .prompt_ai import (
    PromptGenerationCancelled,
    PromptGenerator,
)


class GenerationThread(c4d.threading.C4DThread):
    def __init__(self, client: ImageApiClient, job: dict):
        super().__init__()
        self.client = client
        self.job = job
        self.results: list[str] = []
        self.error = ""
        self.progress = 0.0
        self.message = "准备生成…"
        self.done = False
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._set_progress(self.progress, "正在取消生成…")

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    def _set_progress(self, value: float, message: str) -> None:
        with self._lock:
            self.progress = max(0.0, min(1.0, float(value)))
            self.message = str(message)

    def snapshot(self) -> tuple[float, str, bool]:
        with self._lock:
            return self.progress, self.message, self.done

    def Main(self) -> None:
        try:
            self.results = self.client.generate(
                self.job,
                self._set_progress,
                self._cancel_event.is_set,
            )
        except GenerationCancelled:
            self.results = []
        except Exception as exc:  # surfaced in the main C4D thread
            self.error = str(exc)
        finally:
            with self._lock:
                self.done = True
            c4d.SpecialEventAdd(NETWORK_EVENT_ID)


class PromptGenerationThread(c4d.threading.C4DThread):
    def __init__(
        self,
        client: PromptGenerator,
        model: str,
        scene_image: str,
        depth_image: str,
        references: list[str],
        draft_prompt: str,
        skill_id: str,
    ):
        super().__init__()
        self.client = client
        self.model = model
        self.scene_image = scene_image
        self.depth_image = depth_image
        self.references = list(references)
        self.draft_prompt = draft_prompt
        self.skill_id = skill_id
        self.result = ""
        self.error = ""
        self.cancelled = False
        self.progress = 0.0
        self.message = "准备生成提示词…"
        self.done = False
        self._lock = threading.Lock()
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._set_progress(self.progress, "正在停止提示词生成…")

    def _set_progress(self, value: float, message: str) -> None:
        with self._lock:
            self.progress = max(0.0, min(1.0, float(value)))
            self.message = str(message)

    def snapshot(self) -> tuple[float, str, bool]:
        with self._lock:
            return self.progress, self.message, self.done

    def Main(self) -> None:
        try:
            self.result = self.client.generate(
                self.model,
                self.scene_image,
                self.depth_image,
                self.references,
                self.draft_prompt,
                self.skill_id,
                self._set_progress,
                self._cancel_event.is_set,
            )
        except PromptGenerationCancelled:
            self.cancelled = True
            self.result = ""
        except Exception as exc:  # surfaced on C4D's main thread
            self.error = str(exc)
        finally:
            with self._lock:
                self.done = True
            c4d.SpecialEventAdd(PROMPT_EVENT_ID)


class RenderLoadTask:
    """Run active-document rendering synchronously on C4D's main thread.

    Cinema 4D scene objects, BaseDraw camera links, document time and
    ``EventAdd`` are main-thread-bound. Network generation remains threaded,
    but this task deliberately never subclasses/starts ``C4DThread``.
    """

    def __init__(
        self,
        output_dir,
        workflow: str,
        sample_count: int = 5,
        model: str = "",
    ):
        super().__init__()
        self.output_dir = output_dir
        self.workflow = workflow
        # Used only by the geometry-capture companion to choose the model's
        # conventional 2K reference canvas.
        self.model = str(model or "").strip()
        self.sample_count = max(3, min(9, int(sample_count)))
        self.paths: list[str] = []
        self.context: dict = {}
        self.error = ""
        self.progress = 0.0
        self.message = "准备渲染…"
        self.done = False
        self._lock = threading.Lock()

    def _set_progress(self, value: float, message: str) -> None:
        with self._lock:
            self.progress = max(0.0, min(1.0, float(value)))
            self.message = str(message or "正在渲染…")

    def snapshot(self) -> tuple[float, str, bool]:
        with self._lock:
            return self.progress, self.message, self.done

    def run(self) -> None:
        try:
            is_main_thread = getattr(c4d.threading, "GeIsMainThread", None)
            if callable(is_main_thread) and not is_main_thread():
                raise RuntimeError("C4D 场景渲染必须在主线程执行。")
            if self.workflow == "animation":
                self.paths, self.context = render_active_animation(
                    self.output_dir,
                    sample_count=self.sample_count,
                    progress=self._set_progress,
                )
            else:
                path, self.context, _bitmap = render_active_document(
                    self.output_dir,
                    progress=self._set_progress,
                )
                self.paths = [path]
        except Exception as exc:  # surfaced in the main C4D thread
            self.error = str(exc)
        finally:
            with self._lock:
                self.done = True
