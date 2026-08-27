"""Crea el entorno local reproducible y selecciona PyTorch para el hardware disponible."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PYTHON = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def has_nvidia_gpu() -> bool:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return False
    return subprocess.run(
        [executable],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def main() -> int:
    if shutil.which("uv") is None:
        print("Error: instala uv y vuelve a ejecutar este script.", file=sys.stderr)
        return 1

    if not PYTHON.exists():
        run("uv", "venv", "--python", "3.11", str(VENV))

    if has_nvidia_gpu():
        print("GPU NVIDIA detectada: instalando PyTorch CUDA 12.4.")
        run(
            "uv",
            "pip",
            "install",
            "--python",
            str(PYTHON),
            "torch==2.6.0",
            "torchvision==0.21.0",
            "--index-url",
            "https://download.pytorch.org/whl/cu124",
        )
    else:
        print("No se detectó GPU NVIDIA: se instalará PyTorch para CPU.")

    run("uv", "pip", "install", "--python", str(PYTHON), "-r", "requirements.txt")
    print(f"Entorno preparado en {VENV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
