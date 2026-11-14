PYTHON := .venv/bin/python
PY_SOURCES := harness $(wildcard pair.py)

.PHONY: test coverage lint js-test browser-test

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
