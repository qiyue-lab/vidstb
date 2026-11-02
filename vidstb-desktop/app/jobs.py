"""批量转码队列。"""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from . import command_builder, fonts as fonts_module, hardware, merger
from . import paths as paths_module
from .ffmpeg_tool import Ffmpeg
from .media_probe import dump_font_attachments, probe
from .models import ProbeResult, VideoMetadata
from .runner import ConversionRunner, ProcessGuard

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_ERROR = "error"
STATUS_CANCELLED = "cancelled"

INVALID_CHARS = re.compile(r'[\\/:*?"<>|]')


KIND_SUBTITLE = "subtitle"
KIND_MERGE = "merge"


class Job:
    def __init__(self, input_path: str, subtitle_indexes: List[int],
                 probe_result: Optional[ProbeResult] = None,
                 kind: str = KIND_SUBTITLE, inputs: Optional[List[str]] = None):
        self.id = uuid.uuid4().hex[:8]
        self.kind = kind
        self.input_path = input_path
        self.inputs: List[str] = list(inputs) if inputs else [input_path]
        self.subtitle_indexes = list(subtitle_indexes)
        self.probe: Optional[ProbeResult] = probe_result
        self.clips: List = []            # 合并任务的片段（MergeClip）
        self.estimated_bytes = 0         # 合并任务的预估体积
        self.total_duration_us = 0       # 合并任务的总时长
        self.status = STATUS_QUEUED
        self.progress = 0
        self.message = "排队中"
        self.output_path = ""
        self.encoder = ""
        self.started_at: Optional[float] = None
        self.finished_at: Optional[float] = None
        self.speed = ""
        self.size_bytes = 0
        self.log: deque = deque(maxlen=300)
        self._runner: Optional[ConversionRunner] = None
        # 停止标志：任意阶段（准备 / 导出字体 / 编码）都能据此中止
        self.cancel = threading.Event()

    @property
    def is_cancelled(self) -> bool:
        return self.cancel.is_set()

    @property
    def name(self) -> str:
        if self.kind == KIND_MERGE:
            return f"合并 {len(self.inputs)} 段 → 4K60"
        return os.path.basename(self.input_path)

    def to_dict(self) -> dict:
        metadata = self.probe.metadata if self.probe else VideoMetadata()
        eta = None
        if self.status == STATUS_RUNNING and self.progress > 0:
            elapsed = time.time() - (self.started_at or time.time())
            eta = max(0, int(elapsed * (100 - self.progress) / max(1, self.progress)))
        duration_us = metadata.duration_us
        if self.kind == KIND_MERGE:
            duration_us = sum(clip.duration_us for clip in self.clips) or self.total_duration_us
        return {
            "id": self.id,
            "kind": self.kind,
            "input": self.input_path,
            "inputs": self.inputs,
            "input_count": len(self.inputs),
            "name": self.name,
            "output": self.output_path,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "encoder": self.encoder,
            "subtitle_indexes": self.subtitle_indexes,
            "speed": self.speed,
            "eta_seconds": eta,
            "size_bytes": self.size_bytes,
            "estimated_bytes": self.estimated_bytes,
            "duration_us": duration_us,
            "resolution": f"{metadata.width}x{metadata.height}" if metadata.width else "",
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class JobManager:
    def __init__(self, ffmpeg_tool: Ffmpeg, config: dict):
        self.ffmpeg_tool = ffmpeg_tool
        self.config = config
        self.jobs: List[Job] = []
        self.lock = threading.RLock()
        self.listeners: List[Callable[[], None]] = []
        self._probe_cache: Dict[str, tuple] = {}
        self._runners: Dict[str, ConversionRunner] = {}
        self._guards: Dict[str, ProcessGuard] = {}
        self._executor: Optional[ThreadPoolExecutor] = None
        self._hardware_info: Optional[List[dict]] = None
        self._gpu_names: Optional[List[str]] = None
        self._hardware_lock = threading.Lock()
        self._active = True
        self._dispatcher: Optional[threading.Thread] = None

    # ---------- 事件 ----------
    def add_listener(self, listener: Callable[[], None]) -> None:
        self.listeners.append(listener)

    def _notify(self) -> None:
        for listener in list(self.listeners):
            try:
                listener()
            except Exception:
                pass

    # ---------- 探测缓存 ----------
    def probe_file(self, path: str) -> ProbeResult:
        try:
            stat = os.stat(path)
            key = (os.path.abspath(path), int(stat.st_mtime), stat.st_size)
        except OSError:
            key = None
        if key and key[0] in self._probe_cache:
            cached_key, cached = self._probe_cache[key[0]]
            if cached_key == key[1:]:
                return cached
        result = probe(self.ffmpeg_tool, path)
        if key:
            self._probe_cache[key[0]] = (key[1:], result)
        return result

    # ---------- 队列 ----------
    def add_jobs(self, items: List[dict]) -> List[Job]:
        created: List[Job] = []
        with self.lock:
            for item in items:
                if (item.get("kind") or KIND_SUBTITLE) == KIND_MERGE:
                    job = self._create_merge_job(item)
                    if job is not None:
                        created.append(job)
                        self.jobs.append(job)
                    continue
                path = item.get("path", "")
                indexes = [int(i) for i in item.get("subtitle_indexes") or []]
                if not path or not indexes:
                    continue
                if any(job.input_path == path and job.status in
                       (STATUS_QUEUED, STATUS_RUNNING) for job in self.jobs):
                    continue
                result = None
                if item.get("probe"):
                    result = self._probe_from_dict(path, item["probe"])
                if result is None:
                    result = self.probe_file(path)
                job = Job(path, indexes, result)
                if result.error:
                    job.status = STATUS_ERROR
                    job.message = result.error
                created.append(job)
                self.jobs.append(job)
        self._ensure_dispatcher()
        self._notify()
        return created

    def _create_merge_job(self, item: dict) -> Optional[Job]:
        """创建一个「多段合并为 4K60」的任务。"""
        paths = [path for path in (item.get("paths") or []) if path]
        if len(paths) < 2:
            return None
        probes = item.get("probes") or []
        clips: List = []
        for index, path in enumerate(paths):
            result = None
            if index < len(probes) and probes[index]:
                result = self._probe_from_dict(path, probes[index])
            if result is None:
                result = self.probe_file(path)
            error = ""
            has_audio = False
            if result is None or result.error:
                error = (result.error if result else "无法读取视频信息") or "无法读取视频信息"
            else:
                has_audio = bool(result.metadata.has_audio)
            clips.append(merger.MergeClip(path=path,
                                          metadata=result.metadata if result else None,
                                          has_audio=has_audio,
                                          error=error))
        job = Job(paths[0], [], None, kind=KIND_MERGE, inputs=paths)
        job.clips = clips
        job.total_duration_us = sum(clip.duration_us for clip in clips)
        job.estimated_bytes = merger.estimate(clips, self.config).get("estimated_bytes", 0)
        broken = [clip for clip in clips if clip.error]
        if broken:
            job.status = STATUS_ERROR
            job.message = f"{broken[0].name}：{broken[0].error}"
        return job

    def _probe_from_dict(self, path: str, data: dict) -> Optional[ProbeResult]:
        try:
            meta = data.get("metadata") or {}
            metadata = VideoMetadata(
                width=int(meta.get("width") or 0),
                height=int(meta.get("height") or 0),
                bit_rate=int(meta.get("bit_rate") or 0),
                duration_us=int(meta.get("duration_us") or 0),
                frame_rate=float(meta.get("frame_rate") or 30),
                has_audio=bool(meta.get("has_audio", True)),
                rotation=int(meta.get("rotation") or 0),
                audio_codec=meta.get("audio_codec") or "",
                format_name=meta.get("format_name") or "",
            )
            from .models import SubtitleTrack
            subtitles = [SubtitleTrack(
                stream_index=int(s.get("stream_index") or 0),
                subtitle_index=int(s.get("subtitle_index") or 0),
                codec_name=s.get("codec_name") or "",
                language=s.get("language") or "",
                title=s.get("title") or "",
                default_track=bool(s.get("default")),
                forced=bool(s.get("forced")),
            ) for s in data.get("subtitles") or []]
            return ProbeResult(path, metadata, subtitles,
                               data.get("font_attachments") or [])
        except Exception:
            return None

    def _next_queued(self) -> Optional[Job]:
        with self.lock:
            for job in self.jobs:
                if job.status == STATUS_QUEUED:
                    job.status = STATUS_RUNNING
                    return job
        return None

    def _parallel(self) -> int:
        return max(1, min(8, int(self.config.get("parallel") or 1)))

    def _ensure_executor(self) -> ThreadPoolExecutor:
        parallel = self._parallel()
        if self._executor is None or self._executor._max_workers != parallel:
            old = self._executor
            self._executor = ThreadPoolExecutor(max_workers=parallel)
            if old is not None:
                old.shutdown(wait=False)
        return self._executor

    def _ensure_dispatcher(self) -> None:
        if self._dispatcher is not None and self._dispatcher.is_alive():
            return
        self._active = True
        self._dispatcher = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._dispatcher.start()

    def _dispatch_loop(self) -> None:
        while self._active:
            # 只放行 parallel 个任务，否则所有任务会被一次性标记成“运行中”，
            # 停止队列时后面那些还没真正开始的任务就管不住了。
            with self.lock:
                running = sum(1 for job in self.jobs if job.status == STATUS_RUNNING)
            if running >= self._parallel():
                time.sleep(0.3)
                continue
            job = self._next_queued()
            if job is None:
                time.sleep(0.4)
                continue
            self._ensure_executor().submit(self._run_job, job)
        # 退出时回收线程池
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None

    def _kill_job(self, job: Job, message: str = "正在取消…",
                  finish_now: bool = False) -> None:
        """给任务打上停止标记，并杀掉它当前可能持有的任何子进程。"""
        job.cancel.set()
        runner = self._runners.get(job.id)
        if runner:
            runner.cancel()
        guard = self._guards.get(job.id)
        if guard:
            guard.cancel()
        if finish_now:
            job.status = STATUS_CANCELLED
            job.finished_at = time.time()
        job.message = message

    def stop_queue(self) -> int:
        """停止队列：正在运行 / 准备中的任务立即中止，其余排队任务标记为已取消。

        返回被中止的任务数量。
        """
        self._active = False
        stopped = 0
        with self.lock:
            for job in self.jobs:
                if job.status in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED):
                    continue
                stopped += 1
                if job.status == STATUS_QUEUED:
                    job.cancel.set()
                    job.status = STATUS_CANCELLED
                    job.message = "队列已停止"
                    job.finished_at = time.time()
                else:
                    self._kill_job(job, "正在取消…")
        self._notify()
        return stopped

    def cancel_job(self, job_id: str) -> bool:
        with self.lock:
            job = self._find(job_id)
            if job is None:
                return False
            if job.status in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED):
                return True
            if job.status == STATUS_QUEUED:
                job.cancel.set()
                job.status = STATUS_CANCELLED
                job.message = "已取消"
                job.finished_at = time.time()
            else:
                self._kill_job(job, "正在取消…")
        self._notify()
        return True

    def resume_queue(self) -> int:
        """把被取消 / 失败的任务重新放回队列。"""
        count = 0
        with self.lock:
            for job in self.jobs:
                if job.status in (STATUS_CANCELLED, STATUS_ERROR):
                    job.cancel.clear()
                    job.status = STATUS_QUEUED
                    job.progress = 0
                    job.message = "排队中"
                    job.finished_at = None
                    count += 1
        if count:
            self._ensure_dispatcher()
        self._notify()
        return count

    def retry_job(self, job_id: str) -> bool:
        with self.lock:
            job = self._find(job_id)
            if job is None:
                return False
            job.cancel.clear()
            job.status = STATUS_QUEUED
            job.progress = 0
            job.message = "排队中"
            job.finished_at = None
            job.started_at = None
        self._ensure_dispatcher()
        self._notify()
        return True

    def remove_job(self, job_id: str) -> bool:
        with self.lock:
            job = self._find(job_id)
            if job is None or job.status == STATUS_RUNNING:
                return False
            self.jobs.remove(job)
        self._notify()
        return True

    def clear_finished(self) -> int:
        with self.lock:
            remaining = [job for job in self.jobs
                         if job.status not in (STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)]
            removed = len(self.jobs) - len(remaining)
            self.jobs = remaining
        self._notify()
        return removed

    def _find(self, job_id: str) -> Optional[Job]:
        for job in self.jobs:
            if job.id == job_id:
                return job
        return None

    def job_log(self, job_id: str) -> List[str]:
        with self.lock:
            job = self._find(job_id)
            return list(job.log) if job else []

    # ---------- 硬件信息 ----------
    def hardware_info(self, refresh: bool = False) -> List[dict]:
        with self._hardware_lock:
            if self._hardware_info is None or refresh:
                self._hardware_info = hardware.detect(self.ffmpeg_tool)
            return list(self._hardware_info)

    def gpu_names(self, refresh: bool = False) -> List[str]:
        if self._gpu_names is None or refresh:
            self._gpu_names = hardware.gpu_names()
        return list(self._gpu_names)

    # ---------- 执行 ----------
    def _abort_if_cancelled(self, job: Job) -> bool:
        """任务已被要求停止：立刻收尾，不再往下走。"""
        if not job.is_cancelled:
            return False
        if job.status != STATUS_CANCELLED:
            self._finish(job, STATUS_CANCELLED, "任务已取消")
        return True

    def _run_job(self, job: Job) -> None:
        guard = ProcessGuard()
        self._guards[job.id] = guard
        try:
            job.started_at = time.time()
            job.progress = 0
            job.message = "正在准备…"
            self._notify()
            if self._abort_if_cancelled(job):
                return
            if job.kind == KIND_MERGE:
                self._run_merge_job(job)
                return

            if job.probe is None or job.probe.error:
                job.probe = self.probe_file(job.input_path)
            if job.probe.error:
                self._finish(job, STATUS_ERROR, job.probe.error)
                return
            if self._abort_if_cancelled(job):
                return

            supported = [s for s in job.probe.subtitles
                         if s.subtitle_index in job.subtitle_indexes and s.is_supported()]
            if not supported:
                self._finish(job, STATUS_ERROR,
                             "没有找到可用的字幕轨：该文件可能没有内嵌字幕，或字幕格式不受支持")
                return

            try:
                output_path = self._resolve_output_path(job)
            except Exception as error:
                self._finish(job, STATUS_ERROR, f"无法创建输出文件：{error}")
                return
            job.output_path = output_path

            temp_dirs: List[str] = []
            subtitle_sources: Optional[List[str]] = None
            work_dir = os.path.dirname(os.path.abspath(job.input_path))
            name = os.path.basename(job.input_path)
            # 文件名里的单引号无法在滤镜表达式中转义，此时改为导出字幕流再烧录
            if "'" in name or '"' in name:
                extracted = self._extract_subtitles(job, guard)
                if self._abort_if_cancelled(job):
                    self._cleanup(temp_dirs)
                    if extracted:
                        self._cleanup(extracted[0])
                    return
                if extracted:
                    subtitle_sources = extracted[1]
                    work_dir = extracted[0]
                    temp_dirs.append(extracted[0])

            fonts_dir = ""
            if (self.config.get("use_embedded_fonts", True) and job.probe.font_attachments
                    and self._needs_fonts(job)):
                try:
                    font_dir = paths_module.make_temp_dir("vidstb-fonts-")
                    temp_dirs.append(font_dir)
                    dumped = dump_font_attachments(
                        self.ffmpeg_tool, job.input_path, job.probe.font_attachments,
                        font_dir, on_process=guard.attach,
                        should_stop=lambda: job.is_cancelled)
                    if dumped:
                        fonts_dir = font_dir
                except Exception:
                    fonts_dir = ""
            if self._abort_if_cancelled(job):
                self._cleanup(temp_dirs)
                return

            env = fonts_module.configure(extra_dirs=[fonts_dir] if fonts_dir else None) or None
            encoder = hardware.choose_encoder(self.ffmpeg_tool, self.config,
                                              self.hardware_info())
            job.encoder = encoder
            mode = (self.config.get("encoder_mode") or "auto").lower()
            attempts = [encoder]
            if mode == "auto" and encoder != "libx264":
                attempts.append("libx264")

            last_error = ""
            for index, current_encoder in enumerate(attempts):
                if self._abort_if_cancelled(job):
                    self._cleanup(temp_dirs)
                    return
                job.encoder = current_encoder
                if index > 0:
                    job.message = "显卡编码不可用，正在回退 CPU 高质量模式…"
                    job.progress = 0
                    self._notify()

                args = command_builder.build(
                    job.input_path, output_path, job.subtitle_indexes,
                    job.probe.metadata, self.config, current_encoder, subtitle_sources,
                    self._selected_tracks(job))
                ok = self._execute(job, args, env, output_path, work_dir)
                if ok:
                    job.size_bytes = (os.path.getsize(output_path)
                                      if os.path.exists(output_path) else 0)
                    job.progress = 100
                    self._finish(job, STATUS_DONE, f"完成：{os.path.basename(output_path)}")
                    self._cleanup(temp_dirs)
                    return
                last_error = job.message
                if self._was_cancelled(job):
                    self._cleanup(temp_dirs)
                    return
            self._cleanup(temp_dirs)
            self._finish(job, STATUS_ERROR, last_error or "转码失败")
        finally:
            self._guards.pop(job.id, None)

    def _run_merge_job(self, job: Job) -> None:
        """把多段视频合并成一条 3840×2160 / 60fps 的视频。"""
        if self._abort_if_cancelled(job):
            return
        broken = [clip for clip in job.clips if clip.error]
        if broken:
            self._finish(job, STATUS_ERROR, f"{broken[0].name}：{broken[0].error}")
            return
        if len(job.clips) < 2:
            self._finish(job, STATUS_ERROR, "至少需要两段视频才能合并")
            return
        missing = [clip.name for clip in job.clips if not os.path.isfile(clip.path)]
        if missing:
            self._finish(job, STATUS_ERROR, f"文件不存在：{missing[0]}")
            return

        try:
            output_path = self._resolve_merge_output_path(job)
        except Exception as error:
            self._finish(job, STATUS_ERROR, f"无法创建输出文件：{error}")
            return
        job.output_path = output_path
        job.estimated_bytes = merger.estimate(job.clips, self.config).get("estimated_bytes", 0)

        encoder = hardware.choose_encoder(self.ffmpeg_tool, self.config,
                                          self.hardware_info())
        job.encoder = encoder
        mode = (self.config.get("encoder_mode") or "auto").lower()
        attempts = [encoder]
        if mode == "auto" and encoder != "libx264":
            attempts.append("libx264")

        last_error = ""
        for index, current_encoder in enumerate(attempts):
            if self._abort_if_cancelled(job):
                return
            job.encoder = current_encoder
            if index > 0:
                job.message = "显卡编码不可用，正在回退 CPU 编码…"
                job.progress = 0
                self._notify()
            try:
                args = merger.build(job.clips, output_path, self.config, current_encoder)
            except Exception as error:
                last_error = f"命令构造失败：{error}"
                continue
            ok = self._execute(job, args, None, output_path,
                               duration_us=job.total_duration_us)
            if ok:
                job.size_bytes = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                job.progress = 100
                self._finish(job, STATUS_DONE, f"完成：{os.path.basename(output_path)}")
                return
            last_error = job.message
            if self._was_cancelled(job):
                return
        self._finish(job, STATUS_ERROR, last_error or "合并失败")

    @staticmethod
    def _selected_tracks(job: Job) -> Optional[List[dict]]:
        """把选中的字幕序号还原成轨道信息，并区分文本字幕与图片字幕。

        图片字幕（PGS / DVDSUB）无法用 subtitles 滤镜渲染，要走 overlay 叠加，
        因此必须带上它在文件里的绝对流号。
        """
        if not job.probe or not job.probe.subtitles:
            return None
        by_index = {track.subtitle_index: track for track in job.probe.subtitles}
        tracks: List[dict] = []
        for index in job.subtitle_indexes:
            track = by_index.get(index)
            if track is None:  # 探测信息缺失时按文本字幕处理
                tracks.append({"kind": "text", "si": int(index), "stream_index": int(index)})
                continue
            tracks.append({
                "kind": "image" if track.is_image() else "text",
                "si": track.subtitle_index,
                "stream_index": track.stream_index,
            })
        return tracks or None

    @staticmethod
    def _needs_fonts(job: Job) -> bool:
        """只有文本字幕才需要内嵌字体；纯图片字幕直接烧图，不必导出字体。"""
        tracks = JobManager._selected_tracks(job)
        if not tracks:
            return True
        return any(track.get("kind") == "text" for track in tracks)

    def _extract_subtitles(self, job: Job, guard: Optional[ProcessGuard] = None):
        """把选中的字幕轨导出到临时目录，返回 (目录, 文件列表)。"""
        directory = paths_module.make_temp_dir("vidstb-subs-")
        paths: List[str] = []
        for position, index in enumerate(job.subtitle_indexes):
            codec = ""
            for track in job.probe.subtitles:
                if track.subtitle_index == index:
                    codec = track.codec_name
                    break
            extension = "ass" if codec in ("ass", "ssa") else "srt"
            target = os.path.join(directory, f"sub{position}.{extension}")
            base = ["-hide_banner", "-loglevel", "error", "-y",
                    "-i", job.input_path, "-map", f"0:s:{index}"]
            ok = False
            for extra in (["-c", "copy"], []):
                if job.is_cancelled:
                    shutil.rmtree(directory, ignore_errors=True)
                    return None
                try:
                    self.ffmpeg_tool.run_ffmpeg(
                        base + extra + [target], timeout=300,
                        on_process=guard.attach if guard else None)
                except Exception:
                    continue
                if os.path.isfile(target) and os.path.getsize(target) > 0:
                    ok = True
                    break
            if not ok:
                shutil.rmtree(directory, ignore_errors=True)
                return None
            paths.append(target)
        return directory, paths

    def _execute(self, job: Job, args: List[str], env: Optional[dict], output_path: str,
                 work_dir: str = "", duration_us: Optional[int] = None) -> bool:
        if duration_us is None:
            duration_us = job.probe.metadata.duration_us if job.probe else 0
        if self._abort_if_cancelled(job):
            return False
        runner = ConversionRunner(
            self.ffmpeg_tool.ffmpeg_path, args,
            duration_us=duration_us,
            env=env, cwd=work_dir or None,
            on_progress=lambda data: self._on_progress(job, data),
            on_log=lambda line: job.log.append(line),
        )
        self._runners[job.id] = runner
        if job.kind == KIND_MERGE:
            job.message = ("正在用显卡合并并升到 4K60…" if job.encoder != "libx264"
                           else "正在用 CPU 合并并升到 4K60…")
        else:
            job.message = ("正在使用显卡加速生成硬字幕…" if job.encoder != "libx264"
                           else "正在使用 CPU 高质量模式生成硬字幕…")
        self._notify()
        try:
            runner.start()
            code = runner.wait()
        except Exception as error:
            self._runners.pop(job.id, None)
            job.message = f"启动失败：{error}"
            return False
        finally:
            pass
        self._runners.pop(job.id, None)

        if code == 0:
            return True
        if runner.cancelled or job.status == STATUS_CANCELLED or job.is_cancelled:
            job.status = STATUS_CANCELLED
            job.message = "任务已取消"
            self._safe_delete(output_path)
            return False
        job.message = self._error_summary(runner)
        self._safe_delete(output_path)
        return False

    @staticmethod
    def _error_summary(runner: ConversionRunner) -> str:
        """优先给出真正的报错行，避免只看到一堆告警。"""
        lines = runner.tail(60)
        keywords = ("error", "failed", "invalid", "unable", "no such",
                    "not supported", "could not", "错误", "失败", "无法")
        hits = [line for line in lines if any(key in line.lower() for key in keywords)]
        picked = hits[-5:] if hits else lines[-15:]
        text = "\n".join(picked).strip()
        return text[-900:] if text else "ffmpeg 返回非零状态码，请检查日志"

    def _on_progress(self, job: Job, data: dict) -> None:
        job.progress = data.get("percent", 0)
        job.speed = data.get("speed", "")
        if job.status == STATUS_RUNNING:
            verb = "合并" if job.kind == KIND_MERGE else "转码"
            job.message = f"{verb}中 {job.progress}%"
        self._notify()

    def _was_cancelled(self, job: Job) -> bool:
        if job.status != STATUS_CANCELLED:
            return False
        job.message = "任务已取消"
        return True

    def _finish(self, job: Job, status: str, message: str) -> None:
        job.status = status
        job.message = message
        job.finished_at = time.time()
        if status != STATUS_DONE:
            job.progress = job.progress if status == STATUS_CANCELLED else 0
        self._notify()

    def _resolve_output_path(self, job: Job) -> str:
        container = (self.config.get("container") or "mkv").lower()
        directory = (self.config.get("output_dir") or "").strip()
        if directory:
            os.makedirs(directory, exist_ok=True)
        else:
            directory = os.path.dirname(job.input_path) or "."
        stem = os.path.splitext(os.path.basename(job.input_path))[0]
        stem = INVALID_CHARS.sub("_", stem)
        candidate = os.path.join(directory, f"{stem}_硬字幕.{container}")
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(directory, f"{stem}_硬字幕_{counter}.{container}")
            counter += 1
        return candidate

    def _resolve_merge_output_path(self, job: Job) -> str:
        container = merger.settings(self.config)["container"]
        directory = (self.config.get("output_dir") or "").strip()
        if directory:
            os.makedirs(directory, exist_ok=True)
        else:
            directory = os.path.dirname(job.inputs[0]) or "."
        first = os.path.splitext(os.path.basename(job.inputs[0]))[0]
        stem = INVALID_CHARS.sub("_", first)[:40] or "merged"
        candidate = os.path.join(directory, f"{stem}_等{len(job.inputs)}段_4K60.{container}")
        counter = 1
        while os.path.exists(candidate):
            candidate = os.path.join(
                directory, f"{stem}_等{len(job.inputs)}段_4K60_{counter}.{container}")
            counter += 1
        return candidate

    @staticmethod
    def _safe_delete(path: str) -> None:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _cleanup(temp_dirs) -> None:
        if not temp_dirs:
            return
        if isinstance(temp_dirs, str):
            temp_dirs = [temp_dirs]
        for directory in temp_dirs:
            shutil.rmtree(directory, ignore_errors=True)

    def state(self) -> dict:
        with self.lock:
            jobs = [job.to_dict() for job in self.jobs]
            counts = {status: 0 for status in
                      (STATUS_QUEUED, STATUS_RUNNING, STATUS_DONE, STATUS_ERROR, STATUS_CANCELLED)}
            for job in self.jobs:
                counts[job.status] = counts.get(job.status, 0) + 1
        return {"jobs": jobs, "counts": counts, "running": self._active}
