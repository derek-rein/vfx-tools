# EXR Converter — build, run, lint, release
#
# Usage:
#   make help
#   make run                              # launch the GUI
#   make bump PART=minor                  # bump semver + sync APP_VERSION + uv lock
#   make release PART=patch               # bump + lock + commit + tag + push (triggers Release workflow)
#   make release PUSH=0                   # … local only; push branch + tag yourself to trigger CI

.PHONY: help run lint typecheck fmt test test-unit resources bundle clean bump release \
	sync ensure-ocio docs-serve docs-build

APP_NAME := exr_converter
MACOS_BUNDLE_NAME := EXR Converter
ENTRY    := main.py
# Drop any inherited VIRTUAL_ENV (e.g. from another activated project) so uv
# silently uses this project's .venv instead of warning about the mismatch.
UV       := env -u VIRTUAL_ENV uv
PYTHON   := $(UV) run python
# Qt rcc from the PySide6 package (console-script wrappers can point at a stale venv).
RCC      := $(PYTHON) -c 'import subprocess,sys; from pathlib import Path; import PySide6; r=Path(PySide6.__file__).resolve().parent/"Qt"/"libexec"/"rcc"; sys.exit(subprocess.call([str(r),"-g","python",*sys.argv[1:]]))'
BUMP     := python3 scripts/bump_app_version.py
PART     ?= patch
# PUSH=1 (default): push branch + tag so GitHub receives the tag and runs .github/workflows/release.yml
PUSH     ?= 1

export NUITKA_ASSUME_YES_FOR_DOWNLOADS := 1

# ── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo "EXR Converter"
	@echo ""
	@echo "  make run                               # launch the GUI"
	@echo "  make sync                              # uv sync + ensure OCIO 2.5+ linkage"
	@echo "  make ensure-ocio                       # repair OpenColorIO if oiio rewired it to 2.4"
	@echo "  make lint / fmt                        # ruff check / format"
	@echo "  make typecheck                         # basedpyright"
	@echo "  make test / make test-unit             # full suite / unit tests only"
	@echo "  make resources                         # regenerate Qt resources"
	@echo "  make bundle                            # Nuitka standalone build"
	@echo "  make clean                             # remove build artifacts"
	@echo "  make docs-serve / docs-build           # Hugo site from docs/ (local / CI)"
	@echo ""
	@echo "  make bump PART=patch|minor|major       # bump version (no git)"
	@echo "  make release PART=… PUSH=0             # preferred: bump+commit+tag on release/* branch"
	@echo "  make release PART=…                    # also pushes (fails if main is protected)"
	@echo ""
	@echo "  Docs: docs/*.md → Hugo site/ → GitHub Pages (workflow Docs)"
	@echo "        AGENTS.md          (release/deploy + agent context; main is protected)"
	@echo "        CHANGELOG.md       (user-facing history — update every release)"
	@echo "Current tags: git tag -l 'v*' --sort=-v:refname | head"

# ── Dependencies ─────────────────────────────────────────────────────────────
# oiio-python can rewire PyOpenColorIO.so to its vendored OCIO 2.4 dylib, which
# cannot load the bundled ACES Studio v4 config (profile 2.5). Reinstall OCIO last.

sync:
	$(UV) sync
	$(PYTHON) scripts/ensure_ocio.py

ensure-ocio:
	$(PYTHON) scripts/ensure_ocio.py

# ── Run ──────────────────────────────────────────────────────────────────────

run: ensure-ocio
	$(PYTHON) $(ENTRY)

# ── Lint & Format ────────────────────────────────────────────────────────────

lint:
	$(UV) run ruff check src/ main.py tests/

typecheck:
	$(UV) run basedpyright src/ main.py

fmt:
	$(UV) run ruff format src/ main.py tests/
	$(UV) run ruff check --fix src/ main.py tests/

# ── Tests ────────────────────────────────────────────────────────────────────

test:
	QT_QPA_PLATFORM=offscreen $(UV) run pytest

test-unit:
	QT_QPA_PLATFORM=offscreen $(UV) run pytest -m "not integration"

# ── Qt Resources ─────────────────────────────────────────────────────────────

resources: src/rc_resources.py

src/rc_resources.py: resources.qrc resources/icons/icon.png resources/style.qss
	$(RCC) resources.qrc -o src/rc_resources.py

# ── Bundle with Nuitka ───────────────────────────────────────────────────────
# macOS: dist/"EXR Converter.app"   Linux: dist/exr_converter   Windows: dist/exr_converter.exe

ICON ?= resources/icons/icon.icns

# Shared Nuitka strip flags must stay aligned with .github/workflows/release.yml
# (LTO + Qt noinclude set). Local target is macOS-oriented; CI is multi-OS.
bundle: resources
	$(UV) sync --group bundle
	$(PYTHON) scripts/ensure_ocio.py
	$(PYTHON) -m nuitka \
		--standalone \
		--output-dir=dist \
		--output-filename=$(APP_NAME) \
		--assume-yes-for-downloads \
		--python-flag=-OO \
		--lto=yes \
		--enable-plugin=pyside6 \
		--macos-create-app-bundle \
		--macos-app-name="EXR Converter" \
		--macos-app-icon=$(ICON) \
		--nofollow-import-to=tkinter \
		--nofollow-import-to=unittest \
		--nofollow-import-to=pydoc \
		--nofollow-import-to=PIL \
		--nofollow-import-to='PySide6.QtWebEngine*' \
		--noinclude-qt-translations \
		--noinclude-qt-plugins=printsupport,mediaservice,iconengines \
		--noinclude-dlls='*Qt6WebEngine*' \
		--noinclude-dlls='*Qt6Svg*' \
		--noinclude-dlls='*Qt6Pdf*' \
		--noinclude-dlls='*Qt6Positioning*' \
		--noinclude-dlls='*Qt6PrintSupport*' \
		--include-package-data=av \
		--include-package=OpenImageIO \
		--include-package-data=OpenImageIO \
		--include-package=PyOpenColorIO \
		--include-package-data=PyOpenColorIO \
		--include-package=fileseq \
		--include-package=OpenGL \
		--include-data-dir=resources/ocio=resources/ocio \
		--include-module=ssl \
		$(ENTRY)
	mv dist/main.app "dist/$(MACOS_BUNDLE_NAME).app"
	# Nuitka rewires PyOpenColorIO → OIIO’s OCIO 2.4; put 2.5 back.
	$(PYTHON) scripts/fix_bundle_ocio.py "dist/$(MACOS_BUNDLE_NAME).app"

# ── Docs site (Hugo → GitHub Pages) ──────────────────────────────────────────
# Source of truth: docs/*.md  ·  site config/theme: site/  ·  preview: http://127.0.0.1:1313/

docs-serve:
	hugo server -s site -D --disableFastRender

docs-build:
	hugo --minify --gc -s site

clean:
	rm -rf dist build *.build *.dist *.onefile-build __pycache__ site/public site/resources

# ── Version bump (no git) ────────────────────────────────────────────────────

bump:
	@$(BUMP) bump $(PART)
	@$(UV) lock
	@echo "Done. Review diff, then: make release PART=$(PART)  (or commit manually)"

# ── Release: bump + commit + tag (+ optional push) ──────────────────────────

release:
	@set -e; \
	$(BUMP) bump $(PART); \
	$(UV) lock; \
	eval $$($(BUMP) show); \
	if [ -z "$${TAG}" ]; then echo "ERROR: TAG is empty — bump show failed"; exit 1; fi; \
	git add pyproject.toml src/core/constants.py uv.lock; \
	if git diff --staged --quiet; then echo "No changes to commit."; exit 1; fi; \
	git commit -m "release: $${VERSION}"; \
	git tag -a "$${TAG}" -m "release: $${VERSION}"; \
	echo "Created commit + annotated tag $${TAG}"; \
	if [ "$(PUSH)" = "1" ]; then \
	  git push origin HEAD; \
	  git push origin "$${TAG}"; \
	  echo "Pushed branch and tag $${TAG} (Release workflow runs on tag push)."; \
	  echo "NOTE: under protected main prefer PUSH=0 + PR; Auto-tag dispatches Release after merge."; \
	  echo "Do NOT also re-push the same tag after merge — Auto-tag handles that path."; \
	else \
	  echo "PUSH=0: tag is local only. Preferred path: push branch → PR → merge → Auto-tag dispatches Release."; \
	  echo "Manual fallback if Auto-tag is disabled: git push origin HEAD && git push origin $${TAG}"; \
	fi
