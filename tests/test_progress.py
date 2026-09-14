import io
import unittest
from unittest.mock import patch, MagicMock
from src.fieldnotes.progress import PipelineProgress


class TestPipelineProgress(unittest.TestCase):
    def test_progress_disabled(self):
        progress = PipelineProgress(total_phases=5, enabled=False)
        self.assertFalse(progress.enabled)
        progress.set_phase(1, "Fase 1", "Detalle")
        progress.update_substep("Subpaso")
        progress.write("Mensaje")
        progress.finish("Finalizado")
        progress.close()

    def test_progress_enabled(self):
        progress = PipelineProgress(total_phases=7, enabled=True, desc="Prueba")
        self.assertTrue(progress.enabled)
        self.assertIsNotNone(progress._bar)

        progress.set_phase(1, "Preparando", "Validando archivos")
        self.assertEqual(progress.current_phase, 1)

        progress.update_substep("Página 1/3")
        progress.set_phase(2, "OCR", "Inferencia")
        self.assertEqual(progress.current_phase, 2)

        progress.finish("Completado")
        self.assertTrue(progress._closed)

    def test_context_manager(self):
        with PipelineProgress(total_phases=3, enabled=True) as progress:
            progress.set_phase(1, "Inicio")
            self.assertFalse(progress._closed)
        self.assertTrue(progress._closed)

    def test_format_elapsed(self):
        progress = PipelineProgress(total_phases=3, enabled=False)
        progress.start_time = progress.start_time - 125.0  # 2m 05s
        formatted = progress.format_elapsed()
        self.assertIn("2m", formatted)
        self.assertIn("05s", formatted)

        progress.start_time = progress.start_time + 95.0  # 30s
        formatted_short = progress.format_elapsed()
        self.assertIn("s", formatted_short)


if __name__ == "__main__":
    unittest.main()
