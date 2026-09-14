import sys
import time
from typing import Optional, Any

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore


class PipelineProgress:
    """Administrador de barra de progreso y estado en tiempo real para el terminal."""

    def __init__(
        self,
        total_phases: int = 7,
        enabled: Optional[bool] = None,
        desc: str = "Iniciando pipeline",
    ):
        self.total_phases = total_phases
        self.current_phase = 0
        if enabled is None:
            self.enabled = bool(tqdm is not None and getattr(sys.stderr, "isatty", lambda: False)())
        else:
            self.enabled = bool(enabled and tqdm is not None)
        self.start_time = time.time()
        self._bar: Optional[Any] = None
        self._closed = False

        if self.enabled:
            # Formato claro y legible para terminales Windows y POSIX
            bar_format = (
                "{desc}: {percentage:3.0f}%|{bar}| "
                "[{elapsed}<{remaining}, {rate_fmt}{postfix}]"
            )
            self._bar = tqdm(
                total=self.total_phases,
                desc=desc,
                unit="fase",
                leave=True,
                file=sys.stderr,
                bar_format=bar_format,
                dynamic_ncols=True,
            )

    def set_phase(self, phase_num: int, name: str, detail: str = "") -> None:
        """Actualiza la barra a una fase concreta con su descripción y detalle opcional."""
        self.current_phase = phase_num
        desc_text = f"[{phase_num}/{self.total_phases}] {name}"
        if self._bar and not self._closed:
            self._bar.set_description(desc_text)
            if detail:
                self._bar.set_postfix_str(detail)
            else:
                self._bar.set_postfix_str("")
            if phase_num > self._bar.n:
                self._bar.update(phase_num - self._bar.n)
            self._bar.refresh()

    def update_substep(self, detail: str) -> None:
        """Actualiza el detalle del subproceso actual (p. ej. 'Página 2/4') sin cambiar de fase."""
        if self._bar and not self._closed:
            self._bar.set_postfix_str(detail)
            self._bar.refresh()

    def write(self, message: str) -> None:
        """Imprime un mensaje en la terminal de forma segura sin romper la barra de progreso."""
        if self._bar and not self._closed:
            self._bar.write(message)
        else:
            print(message)

    def finish(self, message: str = "Completado") -> None:
        """Finaliza y completa la barra de progreso al 100%."""
        if self._bar and not self._closed:
            self._bar.set_description(f"[{self.total_phases}/{self.total_phases}] {message}")
            if self._bar.n < self.total_phases:
                self._bar.update(self.total_phases - self._bar.n)
            self._bar.set_postfix_str("Listo")
            self._bar.refresh()
            self.close()

    def close(self) -> None:
        """Cierra la barra de progreso de forma segura."""
        if self._bar and not self._closed:
            self._bar.close()
            self._closed = True

    @property
    def elapsed_seconds(self) -> float:
        """Retorna los segundos transcurridos desde el inicio del progreso."""
        return time.time() - self.start_time

    def format_elapsed(self) -> str:
        """Formatea el tiempo transcurrido en formato legible (p. ej. '45.2s' o '2m 15s')."""
        elapsed = self.elapsed_seconds
        if elapsed < 60:
            return f"{elapsed:.1f}s"
        minutes = int(elapsed // 60)
        seconds = int(elapsed % 60)
        return f"{minutes}m {seconds:02d}s"

    def __enter__(self) -> "PipelineProgress":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
