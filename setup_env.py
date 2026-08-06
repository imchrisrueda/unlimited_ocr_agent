import sys
import subprocess
import shutil

def is_nvidia_gpu_available():
    """Comprueba si hay una GPU NVIDIA disponible mediante nvidia-smi o PyTorch."""
    if shutil.which("nvidia-smi"):
        try:
            result = subprocess.run(["nvidia-smi"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False

def install_environment():
    print("Detectando hardware del sistema...")
    gpu_found = is_nvidia_gpu_available()
    
    if gpu_found:
        print("GPU NVIDIA detectada. Instalando PyTorch con aceleración CUDA 12.4...")
        cmd = ["uv", "pip", "install", "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cu124", "-p", ".venv"]
    else:
        print("GPU NVIDIA no detectada. Instalando versión de PyTorch para CPU...")
        cmd = ["uv", "pip", "install", "-r", "requirements.txt", "-p", ".venv"]
        
    try:
        subprocess.run(cmd, check=True)
        print("Entorno de dependencias instalado correctamente.")
    except Exception as e:
        print(f"Error al instalar dependencias: {e}")

if __name__ == "__main__":
    install_environment()
