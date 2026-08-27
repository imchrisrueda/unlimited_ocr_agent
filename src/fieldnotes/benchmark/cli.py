import argparse
import json
import sys
from pathlib import Path
from typing import Optional, List

from .schemas import BenchmarkManifest, validate_confined_relative_path
from .report import generate_benchmark_report, render_report_markdown, save_benchmark_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.fieldnotes.benchmark",
        description="Benchmark cuantitativo, offline y determinista para extracción de estadillos",
    )
    parser.add_argument(
        "--manifest",
        "-m",
        default="tests/fixtures/benchmark/manifest.json",
        help="Ruta al archivo manifest.json del benchmark (por defecto tests/fixtures/benchmark/manifest.json)",
    )
    parser.add_argument(
        "--dataset-dir",
        "-d",
        default=None,
        help="Directorio raíz del dataset (por defecto el directorio contenedor de manifest.json)",
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        default="./benchmark_output",
        help="Directorio de salida para report.json y report.md (por defecto ./benchmark_output)",
    )
    parser.add_argument(
        "--pipelines",
        "-p",
        nargs="+",
        default=None,
        help="Lista de identificadores de pipeline a evaluar (ej: ocr_only ocr_plus_vlm)",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Marca de tiempo ISO fija para ejecuciones de prueba deterministas",
    )
    parser.add_argument(
        "--run-pipelines",
        action="store_true",
        help="Interfaz opt-in documentada para ejecutar modelos reales (no se ejecuta en modo offline normal)",
    )
    return parser


def main(args: Optional[List[str]] = None) -> int:
    parser = build_parser()
    parsed_args = parser.parse_args(args)

    if parsed_args.run_pipelines:
        print(
            "AVISO: La ejecución de pipelines reales (--run-pipelines) requiere GPU y LM Studio activo. "
            "Para evaluación offline reproducible y auditoría, utiliza las predicciones ya generadas en el dataset."
        )
        # La suite de tests y la CLI normal funcionan en modo offline sobre predicciones guardadas
        return 1

    manifest_path = Path(parsed_args.manifest).resolve()
    if not manifest_path.is_file():
        print(f"ERROR: No se encontró el archivo de manifiesto: {manifest_path}", file=sys.stderr)
        return 1

    dataset_dir = (
        Path(parsed_args.dataset_dir).resolve()
        if parsed_args.dataset_dir
        else manifest_path.parent.resolve()
    )

    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest = BenchmarkManifest.model_validate(manifest_data)
    except Exception as exc:
        print(f"ERROR: Fallo al validar el manifiesto del benchmark: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(parsed_args.output_dir).resolve()

    try:
        report = generate_benchmark_report(
            manifest=manifest,
            dataset_dir=dataset_dir,
            pipelines=parsed_args.pipelines,
            fixed_timestamp=parsed_args.timestamp,
        )
        json_path, md_path = save_benchmark_report(report, output_dir)
    except Exception as exc:
        print(f"ERROR durante la ejecución del benchmark: {exc}", file=sys.stderr)
        return 1

    total_cat = report.summary.get("total_catalogued_cases", len(report.case_results))
    total_eval = report.summary.get("total_evaluated_cases", len({c.case_id for c in report.case_results}))
    total_excl = report.summary.get("total_excluded_cases", 0)

    print(f"\n=======================================================")
    print(f"BENCHMARK COMPLETADO: {manifest.dataset_id} (v{manifest.dataset_version})")
    print(f"=======================================================")
    print(f"Casos catalogados: {total_cat}")
    print(f"Casos evaluados: {total_eval}")
    if total_excl > 0:
        print(f"Casos excluidos: {total_excl}")
    print(f"Pipelines: {', '.join(p.pipeline_id for p in report.pipelines)}")
    print(f"Informe JSON guardado en: {json_path}")
    print(f"Informe Markdown guardado en: {md_path}")
    print(f"=======================================================\n")

    # Resumen conciso en stdout
    print(render_report_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
