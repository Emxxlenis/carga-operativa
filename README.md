# Carga operativa

Caso de demostración. No es una granja real ni datos de un empleador: todos los
datos los genera este repositorio con una semilla fija.

Cada día "llegan" producción, mortalidad y ventas en un Excel, un XML y un
correo exportado. El sistema no espera archivos limpios: lo que no cumple el
contrato queda en cuarentena con el motivo escrito, y el día solo se da por
bueno si todo lo que entró está contado.

Es un solo proceso de Python, una base SQLite y una página estática sin
JavaScript. El diseño completo está en [docs/arquitectura.md](docs/arquitectura.md)
y el encargo original en [SPEC.md](SPEC.md).

## Requisitos

- Python 3.11 o 3.12, con SQLite 3.37 o superior (lo trae el propio Python).
- `make`. Si está instalado [uv](https://docs.astral.sh/uv/) se usa para crear
  el entorno; si no, se usan `venv` y `pip`.
- Nada más: no hay servicios de pago, ni base de datos externa, ni clave de API.

Dependencias de ejecución: `openpyxl==3.1.5` y `defusedxml==0.7.1`. Para
desarrollo, `pytest` y `ruff`. Todas están fijadas con `==` en `pyproject.toml`
y con `==` y `--hash` en `requirements.lock`.

## Cómo correr las pruebas

```sh
make test
```

La primera vez crea `.venv/` e instala exactamente lo que dice
`requirements.lock`, comprobando la huella de cada paquete. Es lo único que usa
la red. Después, el comando equivale a:

```sh
.venv/bin/python -m pytest
```

La suite no usa la red ni necesita una clave de API: `tests/conftest.py`
bloquea los sockets, y si alguna prueba intenta abrir uno, la suite falla.

`make lint` corre `ruff check` y `ruff format --check`.

## Cómo correr la demo

```sh
make demo
```

Hace tres cosas:

1. Genera 35 días de archivos sintéticos en `datos/buzon/` (no versionado).
2. Procesa cada día en orden: landing, contrato, modelo, mart, conciliación y
   línea base.
3. Escribe la página en `docs/`: `index.html`, `estilos.css` y
   `datos/tablero.json`.

Para verla, abrir `docs/index.html` en el navegador. No hace falta servidor.

La página que está versionada en `docs/` es la salida de ese comando. Una
prueba la compara con una generación nueva, así que no se edita a mano: si
cambia el generador o la plantilla, hay que volver a correr `make demo`.

Comandos sueltos, con `PYTHONPATH=src .venv/bin/python -m carga_operativa`:

| Comando | Qué hace |
|---|---|
| `generar` | Escribe el buzón sintético. |
| `dia AAAA-MM-DD` | Procesa un día del buzón. `make dia DIA=2026-11-12` hace lo mismo. |
| `demo --salida docs` | Los tres pasos de arriba. |

Código de salida: 0 si el día cuadra, 2 si la conciliación no cuadra (se
informa qué control falló y no se corre el pronóstico), 64 si el día no es una
fecha `AAAA-MM-DD` o la configuración del extractor está incompleta.

## Qué es dato de demostración

Todo.

- Los lotes (`L-001` a `L-006`), los galpones, los valores, los nombres de
  archivo y el correo, cuyo remitente usa el dominio reservado
  `granja-demo.example`.
- Los genera `src/carga_operativa/sintetico/generar.py` con la semilla
  `20261112`. La misma semilla produce los mismos bytes; hay una prueba con la
  huella del día de la demo.
- El día de la demo (2026-11-12) trae seis defectos sembrados a propósito: un
  libro con macros, una celda con fórmula, una fila en libras, una fecha
  imposible (`2026-11-31`), un lote que no existe (`L-999`) y una línea de
  correo sin unidad. Ese día cuadra igual, porque lo rechazado está contado:
  19 filas leídas son 14 aceptadas más 5 en cuarentena, y un archivo rechazado
  entero.
- La "línea base a 7 días" es un promedio móvil o un estacional semanal
  simple. Está rotulada así en la página. No es un modelo predictivo validado.

La página lo dice arriba, antes del tablero, y lo repite en una sección al
final.

## Extractor del correo: sin clave de API se usa el determinista

El correo pasa por un extractor que devuelve registros sin tipar, y esos
registros pasan por el mismo contrato que el Excel y el XML.

- **Sin `CARGA_EXTRACTOR_API_KEY` se usa el extractor determinista de
  demostración.** Es el caso por defecto, el de las pruebas y el de la demo
  versionada. Lee líneas con la forma `Fecha: AAAA-MM-DD` y
  `<métrica> lote <código>: <valor> <unidad>`. Queda registrado en
  `contrato_archivo.extractor` y al pie de la página.
- Con `CARGA_EXTRACTOR_API_KEY` definida se usa el extractor opcional, que
  además exige `CARGA_EXTRACTOR_API_URL` (solo `https`) y
  `CARGA_EXTRACTOR_API_MODELO`. Habla con un servicio compatible con el formato
  de "chat completions". Manda solo el texto del correo, sin herramientas, con
  tiempo límite y sin seguir redirecciones.
- Una clave con la dirección o el modelo sin definir es un error explícito, no
  un recambio silencioso al determinista.
- Si el servicio no responde tampoco hay recambio: el archivo queda pendiente,
  el día no cuadra y el pronóstico no corre.
- Lo que devuelve el extractor no se confía. Si inventa un campo, la fila va a
  cuarentena por `campo_extra`. Si devuelve un tipo inválido, por
  `tipo_invalido`. Si no devuelve una lista de registros, se rechaza el archivo
  por `extraccion_invalida`.

La clave no se guarda en ningún archivo del repositorio, no se escribe en la
base ni en el registro, y no aparece en `repr()`.

## Las tres reglas que sostienen el resto

1. **Cada capa solo lee la anterior.** Cada capa abre SQLite con su propio
   autorizador (`set_authorizer`). El pronóstico falla con
   `AccesoCapaProhibido` si intenta leer una tabla cruda.
2. **Landing, filas aceptadas y cuarentena son solo-inserción.** Reejecutar un
   día no duplica: los archivos se identifican por el sha256 de su contenido y
   las filas por el grano fecha, lote y métrica. Un archivo "corregido" no pisa
   un dato ya aceptado; el conflicto va a cuarentena.
3. **El contrato recibe registros sin tipar.** Un campo inventado llega vivo al
   contrato y cae en cuarentena, en vez de perderse al convertirlo a una clase.

Las capas, las tablas, los once motivos de fila, los nueve de archivo y los
seis controles de la conciliación están en `docs/arquitectura.md`.

## Desviaciones del diseño

El diseño se implementó como está escrito en `docs/arquitectura.md`. Esta
sección lista cada punto donde la implementación se apartó, y por qué. Ninguna
baja un control; varias agregan uno.

### Detalles de librería

Las tres cosas que podían no cumplirse sí se cumplen, con estos ajustes:

- **Tablas STRICT:** funcionan sin cambios. `db/conexion.py` comprueba al
  inicializar que SQLite sea 3.37 o superior y falla con un mensaje claro si no.
- **openpyxl y fórmulas:** funciona como pide el diseño. Con `read_only=True` y
  `data_only=False`, una celda con fórmula llega con `data_type == "f"` y se
  entrega como `Formula`; no se evalúa ni se usa el valor que dejó calculado
  quien la envió. Hay prueba con un libro escrito por openpyxl y con otro que
  trae un valor ya calculado dentro de la celda.
- **Autorizador de SQLite:** aquí están las desviaciones reales.
  - SQLite consulta al autorizador también por lecturas que el código no
    escribe: al comprobar una clave foránea, al resolver un
    `ON CONFLICT DO UPDATE` y en el `WHERE` de un `DELETE`. Por eso cada capa
    lee, además de la capa anterior, sus propias tablas (`lee_propias` en
    `db/permisos.py`). La tabla "Lee" del diseño listaba solo la capa anterior.
    Ninguna capa lee más que eso, y hay una prueba por capa y por tabla.
  - El autorizador no distingue qué fila se inserta, así que "landing solo
    escribe `entrada_no_regular` en cuarentena" no se podía imponer con él. Lo
    impone un trigger del esquema que consulta `capa_actual()`, una función que
    define cada conexión.
  - Ese trigger lee `NEW.motivo`, y SQLite lo informa al autorizador como una
    lectura de `cuarentena.motivo`. Se permite solo esa combinación exacta de
    trigger, tabla y columna. No se permite "cualquier lectura hecha desde un
    trigger" porque SQLite usa el mismo campo para el alias de una
    subconsulta, y un alias con nombre de trigger habría abierto la frontera.
    Hay prueba de ese caso.
  - `INSERT OR REPLACE` borra la fila anterior sin disparar los triggers de
    `DELETE`, salvo que `recursive_triggers` esté activo. Cada conexión lo
    activa, para que REPLACE no pueda pisar una fila de solo-inserción.
  - `Connection.execute` de `sqlite3` no pasa por un `cursor()` redefinido. La
    traducción del error a `AccesoCapaProhibido` se hace redefiniendo
    `execute` y `executemany` en la conexión, y `executescript` está prohibido.
  - Además de lo que pedía el diseño: después de abrir, toda `PRAGMA`,
    `ATTACH` y sentencia de esquema queda denegada, y las funciones SQL se
    limitan a una lista (`capa_actual`, `coalesce`, `count`, `sum`).

### Decisiones propias

- **`.gitignore` usa `/datos/` y no `datos/`.** Sin la barra inicial también
  se ignoraba `docs/datos/tablero.json`, que sí se versiona.
- **Solo-inserción también en `contrato_fila_aceptada`.** El diseño pedía
  triggers en landing y cuarentena; se agregaron los mismos en las filas
  aceptadas, que el diseño declara de solo `INSERT`.
- **Elección del extractor en dos pasos.** `config.py` no puede importar el
  resto del paquete, así que `config.elegir_extractor` devuelve qué usar
  (nombre, clave, dirección y modelo) y `pipeline.crear_extractor` construye la
  instancia.
- **El generador recibe el catálogo de lotes como parámetro**, porque no puede
  importar el contrato. Escribe el xlsx a mano (un zip mínimo, sin compresión y
  con fecha fija) en vez de usar openpyxl, para que los bytes no dependan de la
  versión de la librería ni del reloj.
- **La hoja de estilos es un archivo aparte** (`docs/estilos.css`), no CSS en
  línea, para poder declarar `style-src 'self'` sin `unsafe-inline`. La
  política completa es
  `default-src 'none'; style-src 'self'; base-uri 'none'; form-action 'none'`.
  `tablero.json` escribe `<`, `>` y `&` como escapes Unicode.
- **Excel, más estricto que el diseño.** El libro debe tener exactamente una
  hoja. Un XML interno con DTD o entidades rechaza el archivo como
  `archivo_ilegible`. Las macros se detectan por el nombre `vbaProject.bin` en
  cualquier carpeta y con cualquier mayúscula, por el tipo de contenido
  declarado y por la extensión.
- **Correo.** Un correo con texto plano y una alternativa HTML se acepta
  usando solo el texto plano; la parte HTML no se lee. Un correo que solo trae
  HTML se rechaza.
- **Un valor de texto `-5` es `tipo_invalido`, y un número `-5` es
  `valor_fuera_de_rango`.** El patrón del diseño para el texto no admite signo
  y se tomó al pie de la letra.
- **Landing.** Al copiar se abre con `O_NOFOLLOW` y `O_NONBLOCK`, y se
  comprueba sobre el descriptor ya abierto que sea un archivo regular. Es una
  segunda barrera por si la entrada cambia entre el listado y la apertura; una
  tubería con nombre no deja el proceso esperando.
- **Extractor opcional.** No sigue redirecciones (llevarían la clave a otro
  servidor) y corta la respuesta en 1 MiB.
- **Bloqueo de red en las pruebas.** Además de reemplazar `socket.socket`, se
  bloquean `socket.create_connection` y `socket.getaddrinfo`, y la sesión falla
  al terminar si quedó registrado algún intento.
- **Pruebas de la demo.** Las de "sin rutas, sin trazas, sin direcciones
  externas" revisan los tres archivos generados, no `docs/arquitectura.md`, que
  nombra esas cadenas para explicarlas. La de emojis sí revisa el repositorio
  entero.
- **`ruff`** ignora el largo de línea solo en `demo/exportar.py`, donde hay
  líneas de HTML que no se pueden partir sin cambiar la página.

## Límites conocidos

- **El contrato valida forma, no verdad.** Un archivo con datos falsos pero
  bien formados produce filas falsas.
- **Un día puede cuadrar con el total incompleto.** "Cuadra" significa que todo
  lo que entró está contado, no que estén todos los lotes. El día de la demo
  tiene cuatro lotes con producción en vez de seis, y ese total parcial entra
  como una observación más en la historia de la línea base. La página muestra
  cuántos lotes tienen dato.
- **Una fila en cuarentena no se repara.** No hay reproceso: si llega un
  archivo nuevo con el dato corregido y el grano está libre, entra; lo
  rechazado queda registrado.
- **Un archivo demasiado grande sí se copia a landing.** Lo rechaza el
  contrato por tamaño, sin abrirlo. Landing guarda lo que llegó.
- **Modelo y mart no borran.** Si alguien agrega a mano una fila al mart, la
  conciliación lo detecta y el día queda en `no_cuadra` hasta que se quite a
  mano.
- **Un solo proceso.** No hay bloqueo entre dos corridas simultáneas del mismo
  día más allá de las transacciones de SQLite.
- **La demo no muestra un día `no_cuadra`.** Con datos sucios pero bien
  contados, la conciliación cuadra. Esa compuerta se demuestra en las pruebas.

## Verificado en

- Python 3.12.3 con SQLite 3.45.1 y Python 3.11.15 con SQLite 3.53.1, en
  Linux: la suite completa pasa en los dos.
- La página se abrió desde el disco (`file://`) en Chrome: la hoja de estilos
  carga con la política `style-src 'self'`.
- No se probó en Windows ni en macOS. Las pruebas de enlaces simbólicos y de
  tuberías con nombre suponen un sistema tipo Unix.
