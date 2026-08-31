#!/usr/bin/env bash
# Download and validate the 16 kHz Silero VAD ONNX model for RoboAgent.
set -euo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly DEFAULT_DESTINATION="${PROJECT_ROOT}/roboagent/speech/audio/data/silero_vad.onnx"
readonly REPOSITORY_URL="https://raw.githubusercontent.com/snakers4/silero-vad"

destination="${DEFAULT_DESTINATION}"
revision="master"
force=false
verify_only=false

usage() {
    cat <<EOF
Usage: $(basename "$0") [options]

Download the official Silero VAD 16 kHz ONNX model and validate it with the
current project's ONNX Runtime. The existing valid model is preserved unless
--force is supplied.

Options:
  --destination PATH  Target ONNX path (default: ${DEFAULT_DESTINATION})
  --revision REF      Silero Git revision or branch (default: master)
  --force             Download and atomically replace an existing valid model
  --verify-only       Validate an existing model without downloading
  -h, --help          Show this help text
EOF
}

die() {
    echo "error: $*" >&2
    exit 1
}

while (($#)); do
    case "$1" in
        --destination)
            (($# >= 2)) || die "--destination requires a path"
            destination="$2"
            shift 2
            ;;
        --revision)
            (($# >= 2)) || die "--revision requires a revision"
            revision="$2"
            shift 2
            ;;
        --force)
            force=true
            shift
            ;;
        --verify-only)
            verify_only=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option: $1"
            ;;
    esac
done

command -v curl >/dev/null || die "curl is required"
command -v sha256sum >/dev/null || die "sha256sum is required"

run_python() {
    if [[ -x "${PROJECT_ROOT}/.venv/bin/python" ]]; then
        "${PROJECT_ROOT}/.venv/bin/python" "$@"
    elif command -v uv >/dev/null; then
        (cd "${PROJECT_ROOT}" && uv run --extra speech python "$@")
    else
        die "Python environment not found. Run: uv sync --extra speech"
    fi
}

run_python -c 'import onnxruntime' >/dev/null 2>&1 || die \
    "onnxruntime is required. Run: uv sync --extra speech"

validate_model() {
    local path="$1"
    [[ -s "${path}" ]] || die "model file is missing or empty: ${path}"
    run_python - "${path}" <<'PY'
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort

path = Path(sys.argv[1])
if path.stat().st_size < 100_000:
    raise SystemExit(f"model is unexpectedly small ({path.stat().st_size} bytes)")

options = ort.SessionOptions()
options.inter_op_num_threads = 1
options.intra_op_num_threads = 1
session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"], sess_options=options)
input_names = {item.name for item in session.get_inputs()}
required = {"input", "state", "sr"}
if not required.issubset(input_names):
    raise SystemExit(f"unexpected ONNX inputs: {sorted(input_names)}")

outputs = session.run(
    None,
    {
        "input": np.zeros((1, 576), dtype=np.float32),
        "state": np.zeros((2, 1, 128), dtype=np.float32),
        "sr": np.array(16_000, dtype=np.int64),
    },
)
if len(outputs) < 2 or not np.isfinite(np.asarray(outputs[0])).all():
    raise SystemExit("ONNX inference returned invalid output")
print(f"validated ONNX model: {path} ({path.stat().st_size} bytes)")
PY
}

if [[ "${verify_only}" == true ]]; then
    validate_model "${destination}"
    echo "File size: $(stat --format=%s "${destination}") bytes"
    echo "SHA-256: $(sha256sum "${destination}" | awk '{print $1}')"
    exit 0
fi

if [[ -e "${destination}" && "${force}" != true ]]; then
    validate_model "${destination}"
    echo "Silero VAD model already exists; use --force to refresh from ${revision}."
    echo "File size: $(stat --format=%s "${destination}") bytes"
    echo "SHA-256: $(sha256sum "${destination}" | awk '{print $1}')"
    exit 0
fi

destination_dir="$(dirname -- "${destination}")"
mkdir -p -- "${destination_dir}"
temporary_model="$(mktemp "${destination_dir}/.silero_vad.XXXXXX")"
temporary_manifest="${temporary_model}.sha256"
cleanup() {
    rm -f -- "${temporary_model}" "${temporary_manifest}"
}
trap cleanup EXIT

source_url="${REPOSITORY_URL}/${revision}/src/silero_vad/data/silero_vad.onnx"
echo "Downloading Silero VAD revision ${revision}..."
curl --fail --location --retry 3 --retry-all-errors --output "${temporary_model}" "${source_url}"
validate_model "${temporary_model}"

checksum="$(sha256sum "${temporary_model}" | awk '{print $1}')"
printf '%s  %s\n' "${checksum}" "$(basename -- "${destination}")" >"${temporary_manifest}"
mv -f -- "${temporary_model}" "${destination}"
mv -f -- "${temporary_manifest}" "${destination_dir}/.silero_vad.onnx.sha256"
trap - EXIT

echo "Installed Silero VAD model: ${destination}"
echo "Revision: ${revision}"
echo "File size: $(stat --format=%s "${destination}") bytes"
echo "SHA-256: ${checksum}"
printf 'export ROBOAGENT_SILERO_VAD_MODEL=%q\n' "${destination}"
