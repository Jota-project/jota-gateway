# Conflicto de namespace entre jota-gateway y jota-speaker

**Fecha:** 2026-07-15
**Detectado durante:** Fix de issue #96

## Problema

`jota-gateway` y `jota-speaker` comparten el nombre de paquete de nivel superior `src/`.
Ambos proyectos usan la estructura `src/<subpaquete>/` (p. ej. `src/core/`, `src/api/`).

Cuando ambos están instalados en el mismo entorno Python:

1. `jota-speaker` está instalado como paquete editable en `site-packages`
2. `jota-gateway` no tenía paquete Python (`pyproject.toml` / `setup.py`), solo `requirements.txt`
3. Python trata `src/` como un **namespace package** — un paquete sin `__init__.py` que puede existir en múltiples distribusi
4. Al importar `from src.core.exceptions`, Python busca en todos los directorios `src/` encontrados en `sys.path`
5. El namespace merge incluye `src/` de **ambos** proyectos, y Python puede resolveder desde cualquiera dependiendo del orden de `sys.path`

## Síntomas

```python
from src.core.exceptions import ClientNotFound
# ModuleNotFoundError: No module named 'src.core.exceptions'
# (jota-speaker tiene src/core/ pero no tiene src/core/exceptions.py)
```

```python
from src.core.config import settings
# Importa desde jota-speaker (que tiene Settings pero no el singleton settings de jota-gateway)
```

## Solución aplicada

### 1.pyproject.toml (Fase 1a)

Creado `pyproject.toml` en la raíz de jota-gateway para poder instalar como paquete editable:

```toml
[project]
name = "jota-gateway"
version = "1.15.1"
dependencies = [...]

[build-system]
requires = ["setuptools>=61.0"]
```

### 2. `__init__.py` faltantes (Fase 1b)

Añadidos `__init__.py` en todos los directorios de paquete para romper el namespace merge:

```
src/__init__.py
src/core/__init__.py
src/models/__init__.py
src/db/__init__.py
src/api/__init__.py
src/services/__init__.py
src/services/openclaw/__init__.py
tests/__init__.py
tests/integration/__init__.py
tests/e2e/__init__.py
```

> `tests/unit/__init__.py` ya existía previamente.

### 3. Instalación como editable

```bash
pip install -e .
```

Esto registra `jota-gateway` en `sys.path` con prioridad sobre el namespace merge de `jota-speaker`.

## Prevención futura

- **Nunca eliminar `__init__.py`** de ningún directorio de paquete en `src/` o `tests/`
- Si se añade un nuevo subpaquete, asegurar que tiene `__init__.py`
- Si se añade un nuevo proyecto Python junto a jota-gateway, usar un prefijo de paquete diferente (p. ej. `jota_gateway/` en lugar de `src/`), o instalar solo uno como editable
- Al trabajar en el entorno local, mantener `pip install -e .` de jota-gateway activo

## Archivos cambiados

| Archivo | Cambio |
|---------|--------|
| `pyproject.toml` | Creado |
| `src/__init__.py` | Creado |
| `src/core/__init__.py` | Creado |
| `src/models/__init__.py` | Creado |
| `src/db/__init__.py` | Creado |
| `src/api/__init__.py` | Creado |
| `src/services/__init__.py` | Creado |
| `src/services/openclaw/__init__.py` | Creado |
| `tests/__init__.py` | Creado |
| `tests/integration/__init__.py` | Creado |
| `tests/e2e/__init__.py` | Creado |
