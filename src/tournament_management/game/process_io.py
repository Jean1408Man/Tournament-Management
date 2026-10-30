"""Tuberías no bloqueantes y límites de salida de los bots locales."""

from __future__ import annotations

import json
import os
import resource
import selectors
import signal
import subprocess
import threading
import time

from tournament_management.game.protocol import MAX_MESSAGE_BYTES, encode


class BotFault(Exception):
    def __init__(self, player: int, reason: str):
        self.player = player
        super().__init__(reason)


class RefereeStalled(OSError):
    """El árbitro no se ejecutó durante la partida (worker congelado, 4.4 D4.8).

    Sus plazos dejan de medir al bot: un timeout es un fallo de infraestructura.
    """


class BotPipes:
    def __init__(self, memory_limit: int, output_limit: int, stall_threshold_ms: int = 500):
        self.memory_limit = memory_limit
        self.output_limit = output_limit
        self.stall_threshold = stall_threshold_ms / 1000
        # Tiempo total que el árbitro estuvo sin ejecutarse en esta partida.
        self.stalled = 0.0
        self.processes: list[subprocess.Popen] = []
        self.selector = selectors.DefaultSelector()
        self.buffers = [bytearray(), bytearray()]
        self.output_bytes = [0, 0]
        self.stdout_open = [False, False]

    def start(self, command: tuple[str, ...], player: int) -> None:
        if not command or any(type(arg) is not str for arg in command):
            raise ValueError("Comando de bot inválido")
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, start_new_session=True,
        )
        self.processes.append(process)
        # Aplicar el límite desde el padre evita preexec_fn en un proceso con hilos.
        resource.prlimit(process.pid, resource.RLIMIT_AS, (self.memory_limit, self.memory_limit))
        for channel, pipe in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(pipe.fileno(), False)
            self.selector.register(pipe, selectors.EVENT_READ, (player, channel))
        os.set_blocking(process.stdin.fileno(), False)
        self.stdout_open[player] = True

    def send(
        self, player: int, message: dict, deadline: float,
        cancel: threading.Event | None,
    ) -> None:
        data = encode(message).encode("utf-8")
        fd = self.processes[player].stdin.fileno()
        offset = 0
        started = last = time.monotonic()
        max_gap = 0.0
        while offset < len(data):
            now, max_gap = self._tick(last, max_gap)
            last = now
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            remaining = deadline - now
            if remaining <= 0:
                self._timeout(player, "timeout al enviar mensaje", started, now, deadline, max_gap)
            with selectors.DefaultSelector() as writer:
                writer.register(fd, selectors.EVENT_WRITE)
                if not writer.select(min(remaining, 0.05)):
                    continue
            try:
                offset += os.write(fd, data[offset:])
            except (BrokenPipeError, OSError) as exc:
                raise BotFault(player, "El bot cerró stdin") from exc

    def read(
        self, player: int, deadline: float, cancel: threading.Event | None,
    ) -> dict:
        # Diagnóstico (4.4): cada vuelta espera ≤ 50 ms. Un salto mucho mayor entre
        # dos vueltas significa que el propio árbitro no se ejecutó (worker congelado).
        started = last = time.monotonic()
        max_gap = 0.0
        while True:
            now, max_gap = self._tick(last, max_gap)
            last = now
            line_end = self.buffers[player].find(b"\n")
            if line_end >= 0:
                line = bytes(self.buffers[player][:line_end])
                del self.buffers[player][: line_end + 1]
                try:
                    message = json.loads(line.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise BotFault(player, "JSON del bot inválido") from exc
                if type(message) is not dict:
                    raise BotFault(player, "El mensaje del bot debe ser un objeto")
                return message
            if not self.stdout_open[player]:
                raise BotFault(player, "El bot terminó prematuramente")
            if cancel is not None and cancel.is_set():
                raise InterruptedError("cancelled")
            remaining = deadline - now
            if remaining <= 0:
                self._timeout(player, "timeout de respuesta", started, now, deadline, max_gap)
            for key, _ in self.selector.select(min(remaining, 0.05)):
                index, channel = key.data
                chunk = os.read(key.fileobj.fileno(), 4096)
                if not chunk:
                    self.selector.unregister(key.fileobj)
                    if channel == "stdout":
                        self.stdout_open[index] = False
                    continue
                self.output_bytes[index] += len(chunk)
                if self.output_bytes[index] > self.output_limit:
                    raise BotFault(index, "Salida del bot demasiado grande")
                if channel == "stdout":
                    self.buffers[index].extend(chunk)
                    if len(self.buffers[index]) > MAX_MESSAGE_BYTES:
                        raise BotFault(index, "Mensaje del bot demasiado grande")

    def _tick(self, last: float, max_gap: float) -> tuple[float, float]:
        """Mide el salto desde la vuelta anterior; cada vuelta espera ≤ 50 ms."""
        now = time.monotonic()
        gap = now - last
        if gap > self.stall_threshold:
            self.stalled += gap
        return now, max(max_gap, gap)

    def _timeout(
        self, player: int, what: str, started: float, now: float, deadline: float,
        max_gap: float,
    ) -> None:
        measured = (
            f"plazo {(deadline - started) * 1000:.0f} ms, "
            f"transcurrido {(now - started) * 1000:.0f} ms, "
            f"mayor espera del bucle {max_gap * 1000:.0f} ms"
        )
        if self.stalled:
            # Si el árbitro estuvo congelado en algún momento de la partida, sus
            # plazos ya no miden al bot: se reintenta en vez de declarar derrota.
            raise RefereeStalled(
                f"referee_stalled ({what}; congelado {self.stalled * 1000:.0f} ms; {measured})"
            )
        raise BotFault(player, f"{what} ({measured})")

    def close(self) -> None:
        for process in self.processes:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for process in self.processes:
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            for pipe in (process.stdin, process.stdout, process.stderr):
                pipe.close()
        self.selector.close()
