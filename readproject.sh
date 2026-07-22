#!/usr/bin/env bash
#
# read_project.sh
# Dumps all relevant source files in a FastAPI project into one text file
# (or prints to stdout), skipping venvs, caches, and other noise.
#
# Usage:
#   ./read_project.sh                # dumps current dir to project_dump.txt
#   ./read_project.sh /path/to/repo  # dumps a specific project dir
#   ./read_project.sh /path/to/repo output.txt
#   ./read_project.sh --print        # print to terminal instead of a file
#

set -euo pipefail

PROJECT_DIR="."
OUT_FILE="project_dump.txt"
PRINT_ONLY=false

# --- Parse args ---
positional=()
for arg in "$@"; do
  if [[ "$arg" == "--print" ]]; then
    PRINT_ONLY=true
  else
    positional+=("$arg")
  fi
done

if [[ ${#positional[@]} -ge 1 ]]; then
  PROJECT_DIR="${positional[0]}"
fi
if [[ ${#positional[@]} -ge 2 ]]; then
  OUT_FILE="${positional[1]}"
fi

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "Error: directory '$PROJECT_DIR' not found." >&2
  exit 1
fi

# --- Directories to exclude (name only, matched anywhere in the tree) ---
EXCLUDE_DIRS=(
  ".git" ".venv" "venv" "env" "__pycache__" "node_modules"
  ".mypy_cache" ".pytest_cache" ".ruff_cache" "dist" "build"
  ".idea" ".vscode" "site-packages" ".tox" "htmlcov"
)

# --- File extensions to include ---
INCLUDE_EXT=(
  "py" "toml" "cfg" "ini" "env" "yaml" "yml" "txt" "md" "json" "sql"
)

# Build the `find` prune expression: matches the dir itself OR anything
# beneath it, for every excluded dir name, anywhere in the tree.
prune_expr=(-false)
for d in "${EXCLUDE_DIRS[@]}"; do
  prune_expr+=(-o -name "$d" -type d)
done

# Build the extension match expression
ext_expr=(-false)
for e in "${INCLUDE_EXT[@]}"; do
  ext_expr+=(-o -name "*.${e}")
done

write_output() {
  echo "=================================================================="
  echo " FastAPI Project Dump"
  echo " Source: $(cd "$PROJECT_DIR" && pwd)"
  echo " Generated: $(date)"
  echo "=================================================================="
  echo

  echo "----- Project tree -----"
  if command -v tree >/dev/null 2>&1; then
    local_ignore=$(IFS='|'; echo "${EXCLUDE_DIRS[*]}")
    tree -I "$local_ignore" "$PROJECT_DIR"
  else
    find "$PROJECT_DIR" \( "${prune_expr[@]}" \) -prune -o -print
  fi
  echo

  echo "----- File contents -----"
  find "$PROJECT_DIR" \( "${prune_expr[@]}" \) -prune -o \
    -type f \( "${ext_expr[@]}" \) ! -name "$(basename -- "$OUT_FILE")" -print | sort | while IFS= read -r file; do
    echo
    echo "=================================================================="
    echo "FILE: $file"
    echo "=================================================================="
    cat "$file"
  done
}

if $PRINT_ONLY; then
  write_output
else
  write_output > "$OUT_FILE"
  echo "Done. Project dumped to: $OUT_FILE"
fi