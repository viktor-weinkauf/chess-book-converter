"""Unattended folder conversion. Run `python chess_batch.py --help`."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import chess
import fitz

from chess_converter import BOOK_TYPES, ReviewRequired, convert, djvu_tool
from conversion_quality import report_path_for, sha256_file, write_json


def pipeline_fingerprint():
    digest = hashlib.sha256()
    root = Path(__file__).resolve().parent
    for path in (sorted(root.glob("*.py")) + sorted((root / "tessdata").glob("*.traineddata"))
                 + sorted((root / "ocr_profiles").glob("*.json"))):
        digest.update(path.name.encode())
        digest.update(sha256_file(path).encode())
    for name in ("djvused", "djvutxt", "ddjvu"):
        try:
            digest.update(sha256_file(djvu_tool(name)).encode())
        except (OSError, RuntimeError):
            pass
        except Exception as exc:
            # Missing DjVu tools are diagnosed per DjVu job by the converter.
            digest.update(type(exc).__name__.encode())
    digest.update(f"{sys.version_info[:3]} {chess.__version__} {fitz.VersionBind}".encode())
    return digest.hexdigest()


def can_resume(previous, source_hash, pipeline, options, destination):
    if (previous.get("source_sha256") != source_hash or previous.get("pipeline") != pipeline
            or previous.get("options") != options or previous.get("status") not in ("completed", "needs_review")
            or not previous.get("output_sha256") or not previous.get("report_sha256")):
        return False
    try:
        return (sha256_file(destination) == previous["output_sha256"]
                and sha256_file(report_path_for(destination)) == previous["report_sha256"])
    except OSError:
        return False


def run_batch(source, output, options=None, force=False, progress=print):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError(f"Input folder does not exist: {source}")
    options = dict(options or {})
    if options.get("report"):
        raise ValueError("Batch reports are stored beside each PGN; a shared report path is not supported.")
    books = sorted(p for p in source.rglob("*") if p.is_file() and p.suffix.lower() in BOOK_TYPES)
    if not books:
        raise ValueError("No PDF or DjVu books found in the input folder.")
    output.mkdir(parents=True, exist_ok=True)
    manifest_path = output / "batch-report.json"
    try:
        old = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    # A manifest from another input folder must not be mistaken for this corpus.
    previous = old.get("jobs", {}) if old.get("source") == str(source) else {}
    manifest = {"schema_version": 1, "source": str(source), "jobs": {}}
    pipeline = pipeline_fingerprint()
    for index, book in enumerate(books, 1):
        relative = book.relative_to(source)
        key = relative.as_posix()
        # Keep the input extension: foo.pdf and foo.djvu must not overwrite each other.
        destination = output / relative.parent / (relative.name + ".pgn")
        source_hash = sha256_file(book)
        prior = previous.get(key, {})
        if not force and can_resume(prior, source_hash, pipeline, options, destination):
            manifest["jobs"][key] = {**prior, "resumed": True}
            progress(f"[{index}/{len(books)}] Unchanged: {key} ({prior['status']})")
        else:
            job = {"source_sha256": source_hash, "pipeline": pipeline, "options": options,
                   "output": str(destination), "status": "running", "resumed": False}
            manifest["jobs"][key] = job
            write_json(manifest_path, manifest)
            progress(f"[{index}/{len(books)}] Converting: {key}")
            try:
                job["message"] = convert(book, destination, options)
                report = json.loads(report_path_for(destination).read_text(encoding="utf-8"))
                job.update(status=report["status"], summary=report["summary"],
                           output_sha256=report["output"]["sha256"],
                           report_sha256=sha256_file(report_path_for(destination)))
            except ReviewRequired as exc:
                job.update(status="needs_review", message=str(exc))
            except Exception as exc:
                job.update(status="failed", message=f"{type(exc).__name__}: {exc}")
            # KeyboardInterrupt deliberately propagates; completed jobs were already saved.
        write_json(manifest_path, manifest)
    manifest["summary"] = {status: sum(j["status"] == status for j in manifest["jobs"].values())
                           for status in ("completed", "needs_review", "failed")}
    write_json(manifest_path, manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Convert a folder of books and write per-book quality reports.")
    parser.add_argument("input", help="input folder; subfolders are included")
    parser.add_argument("output", help="output folder")
    parser.add_argument("--lang", default="auto", choices=("auto", "en", "ru", "de"))
    parser.add_argument("--ocr", default="auto", choices=("auto", "always", "off"))
    parser.add_argument("--figurine-ocr", default="auto", choices=("auto", "off"))
    parser.add_argument("--coordinate-ocr", default="auto", choices=("auto", "off"))
    parser.add_argument("--strict", action="store_true", help="save reports but reject PGNs with quality issues")
    parser.add_argument("--no-comments", action="store_true")
    parser.add_argument("--force", action="store_true", help="reconvert even unchanged completed jobs")
    args = parser.parse_args()
    try:
        result = run_batch(args.input, args.output,
                           {"lang": args.lang, "ocr": args.ocr, "strict": args.strict,
                            "figurine_ocr": args.figurine_ocr,
                            "coordinate_ocr": args.coordinate_ocr,
                            "keep_text": not args.no_comments}, args.force)
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 1 if result["summary"]["failed"] else 2 if result["summary"]["needs_review"] else 0


if __name__ == "__main__":
    sys.exit(main())
