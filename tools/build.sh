#!/bin/sh
# Windows onedir build. Usage: sh tools/build.sh OUT_DIR [PYTHON]
#   PYTHON = a clean venv with only requirements-build.txt installed
#            (anything else installed there gets bundled by PyInstaller).
# Bundles the Temurin JRE and Okapi 1.48 next to the exe under vendor/.
# Set IDMLBRIEF_TOOLS to a folder holding jre/ and okapi/ (default: ./vendor).
set -e
OUT="$1"; [ -n "$OUT" ] || { echo "usage: build.sh OUT_DIR [PYTHON]"; exit 2; }
PY="${2:-python}"
NAME=IDMLBriefTranslate
VER=$("$PY" -c "import sys; sys.path.insert(0, '.'); from idmlbrief.pipeline import APP_VERSION; print(APP_VERSION)")
HERE="$(cd "$(dirname "$0")/.." && pwd -W)"
TOOLS="${IDMLBRIEF_TOOLS:-$HERE/vendor}"
cd "$HERE"
"$PY" -m PyInstaller --noconfirm --onedir --name "$NAME" --console \
  --distpath "$OUT/dist" --workpath "$OUT/build" --specpath "$OUT" \
  --collect-submodules uvicorn \
  --exclude-module tkinter --exclude-module setuptools --exclude-module _distutils_hack \
  --exclude-module pkg_resources --exclude-module packaging \
  --add-data "$HERE/idmlbrief/static;idmlbrief/static" \
  --add-data "$HERE/idmlbrief/samples;idmlbrief/samples" \
  --add-data "$HERE/idmlbrief/resources;idmlbrief/resources" \
  "$HERE/app.py"
D="$OUT/dist/$NAME"
mkdir -p "$D/vendor"
cp -rL "$TOOLS/jre" "$D/vendor/jre"
cp -rL "$TOOLS/okapi" "$D/vendor/okapi"
"$PY" "$HERE/tools/notices.py" "$D" "$D/THIRD_PARTY_NOTICES.txt"
mkdir -p "$D/sample"
cp "$HERE/fixtures/tx40_ja_base.idml" "$D/sample/"
sed "s/@VERSION@/$VER/" "$HERE/tools/README-dist.txt" > "$D/README.txt"
cp "$HERE/LICENSE" "$D/LICENSE.txt"
du -sh "$D"
mkdir -p "$HERE/dist"
"$PY" -c "import shutil,sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2], sys.argv[3])" \
  "$HERE/dist/$NAME-$VER-win64" "$OUT/dist" "$NAME"
ls -la "$HERE/dist"
