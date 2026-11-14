PYTHON := .venv/bin/python
PY_SOURCES := harness $(wildcard pair.py)

.PHONY: test coverage lint js-test browser-test comfyui-test exposure-test

test:
	$(PYTHON) -m pytest

coverage:
	$(PYTHON) -m pytest --cov --cov-report=term-missing

lint:
	$(PYTHON) -m black --check .
	$(PYTHON) -m flake8
	$(PYTHON) -m mypy $(PY_SOURCES)
	$(PYTHON) -m bandit -q -r $(PY_SOURCES)

js-test:
	npm --prefix tools/testing test

browser-test:
	npm --prefix tools/testing run test:browser

# Not in CI: needs a local ComfyUI checkout (COMFYUI_DIR, default ~/comfy/ComfyUI).
comfyui-test:
	npm --prefix tools/testing run test:comfyui

# Not in CI: needs a local ComfyUI checkout (COMFYUI_DIR, default ~/comfy/ComfyUI).
exposure-test:
	bash tests/comfyui/exposure.sh
