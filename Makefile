# Carga operativa: caso de demostración. Ver README.md.
#
# PYTHON es el intérprete con el que se crea el entorno (3.11 o 3.12). Si se
# deja vacío, uv elige uno que cumpla requires-python de pyproject.toml; sin uv
# se usa python3.
PYTHON ?=
VENV := .venv
PY := $(VENV)/bin/python
LISTO := $(VENV)/.instalado

.PHONY: entorno test lint demo dia limpiar

entorno: $(LISTO)

# Instala exactamente lo que dice requirements.lock, comprobando cada huella.
# Usa uv si está; si no, venv y pip de la biblioteca estándar.
$(LISTO): requirements.lock
	@if command -v uv >/dev/null 2>&1; then \
		uv venv --allow-existing $(if $(PYTHON),--python $(PYTHON)) $(VENV) && \
		uv pip install --python $(PY) --require-hashes -r requirements.lock; \
	else \
		$(or $(PYTHON),python3) -m venv $(VENV) && \
		$(PY) -m pip install --require-hashes -r requirements.lock; \
	fi
	@touch $(LISTO)

# La suite no usa la red ni necesita una clave de API.
test: entorno
	$(PY) -m pytest

lint: entorno
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .

# Genera los datos sintéticos en datos/, corre cada día y escribe docs/.
demo: entorno
	PYTHONPATH=src $(PY) -m carga_operativa demo --salida docs

# Procesa un solo día del buzón: make dia DIA=2026-11-12
dia: entorno
	PYTHONPATH=src $(PY) -m carga_operativa dia $(DIA)

# Borra lo que se regenera. No toca docs/.
limpiar:
	rm -rf datos .pytest_cache .ruff_cache
	find src tests -name __pycache__ -type d -prune -exec rm -rf {} +
