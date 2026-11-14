# Arquitectura de `carga-operativa`

Caso de demostración. No es una granja real ni datos de un empleador.

Un proceso Python, una base SQLite y una página estática sin JavaScript. Sin
microservicios, sin colas y sin modelo entrenado.

Este documento es el diseño. Lo que la implementación tuvo que adaptar por un
detalle de librería está en la sección "Desviaciones del diseño" del `README.md`.

## Tres reglas que sostienen todo

1. **Frontera de capas dentro de SQLite.** Cada capa abre la base con
   `sqlite3.Connection.set_authorizer` y solo puede leer la capa anterior. Que
   el pronóstico falle si lee tablas crudas es un error real en ejecución, no
   una convención.
2. **Nada se borra.** Landing, filas aceptadas y cuarentena son solo-inserción,
   con claves deterministas: el sha256 del archivo y el grano
   fecha-lote-métrica. Reejecutar un día no duplica porque todo ya existe.
3. **El contrato recibe registros sin tipar.** Los lectores y el extractor
   entregan diccionarios tal cual. Un campo inventado llega vivo al contrato y
   cae en cuarentena, en vez de perderse al convertirlo a una clase.

## 1. Árbol de archivos

```text
carga-operativa/
├── SPEC.md
├── README.md
├── pyproject.toml                # Python 3.11 o 3.12; openpyxl==3.1.5, defusedxml==0.7.1
├── requirements.lock             # == y --hash en cada dependencia
├── Makefile
├── .gitignore                    # /datos/ (solo en la raíz)
├── src/carga_operativa/
│   ├── __main__.py               # CLI: generar | dia AAAA-MM-DD | demo
│   ├── config.py                 # rutas, límites, SEMILLA, DIA_DEMO, elección de extractor
│   ├── tipos.py                  # FilaCruda, Formula, CorreoTexto, RechazoArchivo
│   ├── pipeline.py               # orquesta un día, en orden fijo
│   ├── db/
│   │   ├── esquema.sql           # único DDL (tablas STRICT, CHECK, triggers)
│   │   ├── permisos.py           # tabla capa -> qué lee, inserta, actualiza y borra
│   │   └── conexion.py           # abrir(capa): conexión con autorizador
│   ├── landing/
│   │   ├── rutas.py              # resolver_confinada()
│   │   └── recepcion.py          # buzón -> landing, sha256, registro, abrir_verificado()
│   ├── lectores/
│   │   ├── excel.py
│   │   ├── xml_seguro.py
│   │   └── correo.py
│   ├── extraccion/
│   │   ├── puerto.py             # Protocol Extractor
│   │   ├── determinista.py
│   │   └── api_opcional.py
│   ├── contrato/
│   │   ├── esquema.py            # campos, tipos, unidades, límites, VERSION_CONTRATO
│   │   ├── lotes_demo.csv        # catálogo cerrado de lotes
│   │   ├── motivos.py            # lista cerrada + redacción del detalle
│   │   ├── reglas.py             # validar(): pura, sin E/S
│   │   └── aplicar.py            # por archivo: lee, valida, escribe, cuenta
│   ├── cuarentena.py             # registrar() y listar(); nada más
│   ├── modelo/construir.py
│   ├── mart/construir.py
│   ├── conciliacion.py
│   ├── pronostico/linea_base.py
│   ├── demo/
│   │   ├── exportar.py
│   │   ├── plantilla.html
│   │   └── estilos.css
│   └── sintetico/generar.py      # datos y defectos con semilla
├── tests/
│   ├── conftest.py               # bloqueo de red, reloj fijo, base temporal, buzón de prueba
│   ├── dobles.py                 # ExtractorGuionado, ExtractorCaido
│   ├── test_fronteras.py
│   ├── test_landing.py
│   ├── test_lectores_seguridad.py
│   ├── test_contrato.py
│   ├── test_cuarentena.py
│   ├── test_idempotencia.py
│   ├── test_conciliacion.py
│   ├── test_modelo_mart.py
│   ├── test_pronostico.py
│   ├── test_extraccion.py
│   ├── test_demo.py
│   ├── test_sintetico.py
│   └── test_dependencias.py
├── docs/                         # versionado; lo genera `make demo`
│   ├── arquitectura.md
│   ├── index.html
│   ├── estilos.css
│   └── datos/tablero.json
└── datos/                        # no versionado
    ├── buzon/AAAA-MM-DD/         # donde "llega" el archivo; fuera del sistema
    ├── landing/AAAA-MM-DD/       # <sha256>.<ext>, modo 0444
    ├── carga.db
    └── carga.log                 # trazas técnicas; nunca van a la base ni a la demo
```

Dependencias de ejecución: `openpyxl==3.1.5` y `defusedxml==0.7.1`. Para
desarrollo, `pytest` y `ruff` fijados con `==`. El resto es biblioteca estándar.

## 2. Responsabilidad de cada módulo y qué no puede importar

`tests/test_fronteras.py` recorre `src/` con `ast` y compara los imports contra
esta tabla.

| Módulo | Responsabilidad | Prohibido importar |
|---|---|---|
| `config.py` | Constantes y rutas. Lee el entorno solo para elegir extractor. | Cualquier módulo del paquete |
| `tipos.py` | Tipos compartidos, sin lógica. | Cualquier módulo del paquete |
| `db/` | Abre conexiones por capa. Traduce la denegación del autorizador a `AccesoCapaProhibido`. | Cualquier capa |
| `landing/rutas.py` | `resolver_confinada(raiz, relativa)`: rechaza absolutas, `..`, enlaces simbólicos y todo lo que no quede dentro de la raíz resuelta. | Todo salvo `pathlib` y `os` |
| `landing/recepcion.py` | Copia del buzón a `landing/<día>/<sha256>.<ext>` y registra. `abrir_verificado(archivo_id)` recalcula el sha256 antes de entregar bytes. Es el único lugar que abre archivos de entrada. | `lectores`, `extraccion`, `contrato`, `modelo`, `mart`, `pronostico`, `demo`, `openpyxl`, `defusedxml` |
| `lectores/excel.py` | xlsx a `FilaCruda`. Revisa el zip antes de abrir. Una celda con fórmula se entrega como `Formula`, nunca como valor. | `sqlite3`, `db`, `contrato`, `modelo`, `mart`, `pronostico`, `subprocess`, módulos de red |
| `lectores/xml_seguro.py` | Un `<registro>` es una fila; hijos y atributos son campos. | Lo anterior, más `xml.etree`, `xml.dom`, `xml.sax`, `lxml` |
| `lectores/correo.py` | `.eml` a `CorreoTexto(asunto, cuerpo)` usando solo la parte `text/plain`. No guarda adjuntos. | Lo anterior, más `urllib`, `http`, `socket`, `webbrowser`, `html.parser` |
| `extraccion/puerto.py`, `determinista.py` | Texto a registros crudos. | `db`, `landing`, `lectores`, `contrato`, `pathlib`, `os`, módulos de red |
| `extraccion/api_opcional.py` | Igual, llamando a una API. Único archivo del paquete que puede importar `urllib.request`. | `db`, `landing`, `contrato`, `modelo`, `mart`, `pronostico` |
| `contrato/esquema.py`, `motivos.py`, `reglas.py` | Definición del contrato y validación pura de una fila. | `sqlite3`, `db`, `landing`, `lectores`, `extraccion`, `os`, `pathlib`, `openpyxl`, `defusedxml` |
| `contrato/aplicar.py` | Por archivo pendiente: elige lector, valida cada fila, detecta grano duplicado, escribe aceptadas y cuarentena, y guarda `filas_leidas`. Una transacción por archivo. | `modelo`, `mart`, `pronostico`, `demo`, `conciliacion` |
| `cuarentena.py` | Inserta y lista rechazos. No tiene función de borrar ni de editar. | Todo salvo `db` y `contrato.motivos` |
| `modelo/construir.py` | Pivotea las filas aceptadas a `hecho_dia_lote` y carga dimensiones. Solo SQL. | `landing`, `lectores`, `extraccion`, `contrato`, `cuarentena`, `pathlib`, `open`, `openpyxl`, `defusedxml` |
| `mart/construir.py` | Tablas del tablero desde el modelo. Solo SQL. | Todo salvo `db` |
| `conciliacion.py` | Auditor: cuenta en todas las capas y escribe solo `conciliacion_*`. Es la única excepción a "solo lee la anterior", y no es una capa. | Todo salvo `db` |
| `pronostico/linea_base.py` | Línea base sobre `mart_resumen_dia`. Verifica la conciliación él mismo. | `landing`, `lectores`, `extraccion`, `contrato`, `cuarentena`, `modelo`, `mart`, `pathlib`, `os`, `open`, `numpy`, `pandas`, `sklearn`, `statsmodels` |
| `demo/exportar.py` | Genera `docs/` desde mart, conciliación, pronóstico y cuarentena, con conexión de solo lectura. | `landing`, `lectores`, `extraccion`, `modelo`, módulos de red |
| `sintetico/generar.py` | Escribe el buzón con `random.Random(SEMILLA)`. | `db`, `pipeline`, capas; `time`, `datetime.now`, funciones globales de `random` |
| `pipeline.py`, `__main__.py` | Único lugar que conoce todas las capas. No tiene SQL propio. | — |

Permisos de `db/permisos.py`:

| Capa | Lee | Escribe |
|---|---|---|
| landing | `landing_archivo` | `landing_archivo`, `cuarentena` (solo `entrada_no_regular`) |
| contrato | `landing_archivo`, `ref_lote`, `contrato_*` | `contrato_*`, `ref_lote`, `cuarentena` |
| modelo | `contrato_fila_aceptada`, `ref_lote` | `dim_*`, `hecho_dia_lote` |
| mart | `dim_*`, `hecho_dia_lote` | `mart_*` |
| conciliacion | todas | `conciliacion_*` |
| pronostico | `mart_resumen_dia`, `conciliacion_dia` | `pronostico_linea_base` |
| demo | `mart_*`, `conciliacion_*`, `pronostico_linea_base`, `cuarentena` | nada (`mode=ro`) |

`DELETE` está denegado para todas las capas, salvo pronóstico sobre su propia
tabla. `ATTACH` y `PRAGMA` están denegados siempre una vez instalado el
autorizador.

## 3. Esquema SQLite

Tablas `STRICT` y `PRAGMA foreign_keys=ON`. No hay claves foráneas entre capas
no adyacentes.

| Tabla | Grano | Notas |
|---|---|---|
| `landing_archivo` | Un archivo por contenido (`sha256` único) | `dia_carga`, `nombre_original` saneado, `ruta_relativa` armada por el sistema, `formato` en (`xlsx`, `xml`, `eml`, `otro`), `bytes`, `recibido_en`. Triggers abortan `UPDATE` y `DELETE`. |
| `ref_lote` | Un lote | Catálogo cerrado; se carga de `lotes_demo.csv`. |
| `contrato_archivo` | Un archivo procesado | Su existencia significa "ya se procesó". `estado` en (`procesado`, `rechazado`), `filas_leidas`, `version_contrato`, `extractor` (solo eml). Un rechazado tiene cero filas leídas. |
| `contrato_fila_aceptada` | fecha x lote x métrica | Solo `INSERT`. `UNIQUE (fecha, lote, metrica)` y `UNIQUE (archivo_id, n_fila)`. `CHECK`: `kg` para producción y ventas, `aves` para mortalidad. |
| `cuarentena` | Un rechazo por (archivo, fila) | `nivel` en (`archivo`, `fila`), `n_fila` (0 = archivo completo), `motivo` de la lista cerrada, `detalle` en español, `fila_cruda_json`. Triggers abortan `UPDATE` y `DELETE`. |
| `dim_fecha` | Una fecha | año, mes, día, día de la semana, semana ISO. |
| `dim_lote` | Un lote | Copia del catálogo con clave propia. |
| `hecho_dia_lote` | Una fila por fecha y lote | `produccion_kg`, `mortalidad_aves`, `ventas_kg`. `NULL` significa no reportado; no es cero. |
| `mart_produccion`, `mart_mortalidad`, `mart_ventas` | fecha x lote | Sin columnas de formato ni de archivo; la unidad va en el nombre de la columna. |
| `mart_resumen_dia` | Un día | Totales y cantidad de lotes con dato. Entrada del pronóstico. |
| `conciliacion_dia` | Un día | `estado` en (`pendiente`, `cuadra`, `no_cuadra`). |
| `conciliacion_control` | día x control | Seis controles: `archivos`, `filas`, `filas_por_archivo`, `grano_modelo`, `totales_modelo`, `totales_mart`. |
| `pronostico_linea_base` | día base x fecha objetivo x métrica | `metodo` en (`promedio_movil_7`, `estacional_semanal`), `n_observaciones`, `mae_retro`. |

El DDL completo es `src/carga_operativa/db/esquema.sql`.

Modelo y mart se escriben con `INSERT ... ON CONFLICT DO UPDATE` por clave
primaria. Como las filas aceptadas nunca desaparecen, no hace falta borrar.
Todas las marcas de tiempo salen de un reloj inyectado, fijo en tests.

## 4. Contrato de una fila aceptada y motivos de cuarentena

Una fila tiene exactamente estas cinco claves, ni una más ni una menos:

| Campo | Regla |
|---|---|
| `fecha` | Texto `AAAA-MM-DD`, fecha de calendario existente, igual al día de carga. Una celda de Excel con tipo fecha se pasa a ISO; un texto `12/11/2026` no se interpreta. |
| `lote` | Texto que existe en `ref_lote`. |
| `metrica` | `produccion`, `mortalidad` o `ventas`. |
| `valor` | Número, o texto `^\d+(\.\d{1,3})?$`. Sin coma decimal ni separador de miles; no booleano, no NaN. Entero si es mortalidad. Entre 0 y el máximo de `esquema.py`. |
| `unidad` | Tras `strip().lower()`: `kg` para producción y ventas, `aves` para mortalidad. No hay conversión. |

El grano es fecha por lote por métrica, y debe estar libre. El modelo lo
pivotea a una fila por fecha y lote.

Hay un solo motivo por fila: el primero que falla, en este orden.

| Orden | Motivo | Cuándo |
|---|---|---|
| 1 | `campo_extra` | Hay una clave fuera de las cinco. |
| 2 | `columna_faltante` | Falta una de las cinco o viene vacía. |
| 3 | `formula_no_evaluada` | Algún valor es una fórmula de Excel. |
| 4 | `tipo_invalido` | `valor` no numérico, decimal en mortalidad, `lote` no texto. |
| 5 | `fecha_invalida` | No es ISO o la fecha no existe (2026-02-30). |
| 6 | `fecha_fuera_de_dia` | Fecha válida pero distinta del día de carga, incluida una futura. |
| 7 | `metrica_desconocida` | Fuera de las tres. |
| 8 | `unidad_distinta` | No es la unidad del contrato para esa métrica (`lb`, `t`, `docenas`). |
| 9 | `lote_desconocido` | No está en el catálogo. |
| 10 | `valor_fuera_de_rango` | Negativo o sobre el máximo. |
| 11 | `grano_duplicado` | Ese fecha-lote-métrica ya fue aceptado, en este archivo o en uno anterior. |

Motivos a nivel archivo:

| Motivo | Cuándo |
|---|---|
| `entrada_no_regular` | En el buzón hay un enlace simbólico, directorio o dispositivo. No se copia ni se sigue. |
| `archivo_alterado` | El sha256 en landing ya no coincide con el registrado. |
| `archivo_demasiado_grande` | Supera `MAX_BYTES`, o el zip descomprimido supera `MAX_DESCOMPRIMIDO`. |
| `extension_no_admitida` | No es `.xlsx`, `.xml` ni `.eml` (incluye `.xls`, `.xlsb`, `.csv`). |
| `excel_con_macros` | Extensión `.xlsm` o `.xltm`, o el zip contiene `vbaProject.bin`. |
| `xml_con_dtd_o_entidades` | `defusedxml` lanza `DTDForbidden`, `EntitiesForbidden` o `ExternalReferenceForbidden`. |
| `correo_sin_texto_plano` | El `.eml` no tiene parte `text/plain`. |
| `extraccion_invalida` | La salida del extractor no es una lista de objetos con claves de texto. |
| `archivo_ilegible` | Zip corrupto, XML mal formado, encabezado repetido, o cero filas. |

Reglas del detalle:

- Lo arma solo `contrato/motivos.py::redactar(motivo, **campos)` desde una
  plantilla fija por motivo.
- Los valores ajenos se truncan a 80 caracteres y se les quitan los caracteres
  de control.
- Ningún `str(exc)` ni traza entra a la base. La traza técnica va a
  `datos/carga.log`.

## 5. Conciliación del día y compuerta del pronóstico

Orden fijo de `pipeline.dia(D)`:

1. Valida `D` con `date.fromisoformat` antes de armar ninguna ruta.
2. Marca `conciliacion_dia(D) = 'pendiente'` y borra el pronóstico de
   `dia_base = D`.
3. Landing recibe el buzón de `D`. Un sha256 ya registrado se informa como "ya
   recibido" y no se toca.
4. El contrato procesa cada archivo de `D` sin fila en `contrato_archivo`,
   ordenado por `archivo_id`, en una transacción por archivo.
5. Modelo y mart se construyen para `fecha = D`.
6. `conciliacion.conciliar(D)` escribe los seis controles y el estado.
7. Si el estado es `cuadra`, corre el pronóstico. Si no, el proceso sale con
   código 2 y el nombre del control que falló.

Una fila "entra" cuando el lector o el extractor la emite. En Excel, una fila
con todas las celdas vacías no es fila y no se cuenta.

| Control | Esperado | Obtenido |
|---|---|---|
| `archivos` | Archivos en landing para `D` | Archivos con fila en `contrato_archivo` |
| `filas` | Suma de `filas_leidas` | Aceptadas más cuarentena de nivel fila |
| `filas_por_archivo` | 0 | Archivos donde la igualdad anterior falla, o rechazados sin exactamente una cuarentena de nivel archivo |
| `grano_modelo` | Filas aceptadas con `fecha = D` | Medidas no nulas en `hecho_dia_lote` para `D` |
| `totales_modelo` | 0 | Métricas cuyo total aceptado difiere del total del hecho en más de 0,001 |
| `totales_mart` | 0 | Métricas cuyo total o conteo en el mart difiere del hecho |

`filas_por_archivo` existe para que un sobrante en un archivo no compense un
faltante en otro. El estado es `cuadra` solo si los seis controles cuadran.

Un archivo rechazado entero cuadra: se leyeron cero filas y hay una cuarentena.
Un archivo que quedó sin procesar, por ejemplo porque la API del extractor no
respondió, rompe `archivos` y el día no cuadra.

**Cuándo corre el pronóstico.** La verificación está dentro de
`pronostico.ejecutar(D)`, no solo en el orquestador:

- Lee `conciliacion_dia(D)`. Si no hay fila, o el estado es `pendiente` o
  `no_cuadra`, lanza `ConciliacionNoCuadra` y no escribe nada.
- La historia son las filas de `mart_resumen_dia` de los últimos 28 días cuyo
  día está en `cuadra`.
- Con 14 o más observaciones usa `estacional_semanal`: promedio del mismo día
  de la semana en hasta cuatro semanas.
- Con entre 3 y 13 usa `promedio_movil_7`.
- Con menos de 3 no escribe fila para esa métrica.
- El horizonte es de 7 días, por métrica y a nivel total.
- `mae_retro` es el error de aplicar el mismo método, un paso adelante, sobre
  los últimos 7 días.

Es un método honesto y va rotulado como línea base, no como modelo predictivo.

## 6. Extractor

Puerto (`extraccion/puerto.py`):

```python
class Extractor(Protocol):
    nombre: str  # 'determinista' | 'api'

    def extraer(self, correo: CorreoTexto) -> object: ...
```

- **Entrada:** `CorreoTexto(asunto, cuerpo)`, inmutable, con el cuerpo truncado
  a 20 000 caracteres. No recibe ruta, conexión ni día de carga, así que no
  puede acomodar la fecha.
- **Salida:** `object`, no confiable. Se espera una lista de diccionarios con
  `fecha`, `lote`, `metrica`, `valor` y `unidad`.
- **Validación:** `contrato/aplicar.py` comprueba la forma (si falla,
  `extraccion_invalida`), numera los registros y pasa cada uno por el mismo
  `reglas.validar` que usan Excel y XML. `filas_leidas` es la cantidad de
  registros devueltos.

Determinista (`extraccion/determinista.py`), gramática por líneas:

- `Fecha: AAAA-MM-DD` fija la fecha de las líneas siguientes.
- `<métrica> lote <código>: <valor> <unidad>` es un registro.
- Toda línea que mencione `lote` emite un registro con las claves que se
  pudieron leer. Si le falta algo, sale incompleta y el contrato la rechaza por
  `columna_faltante`; no se descarta en silencio.
- No resuelve fechas relativas ni usa la cabecera `Date` del correo.

Selección (`config.elegir_extractor`): sin `CARGA_EXTRACTOR_API_KEY` se usa el
determinista, y queda escrito en `contrato_archivo.extractor`, en el README y
en la demo. Con clave se usa `api_opcional`, que manda solo el texto, sin
herramientas y con tiempo límite, y entrega el `json.loads` de la respuesta sin
retocar. Si la API falla no hay recambio al determinista: el archivo queda
pendiente y el día no cuadra.

En tests:

- `conftest.py` reemplaza `socket.socket` por una función que lanza error en
  toda la suite.
- Las pruebas de punta a punta usan `ExtractorDeterminista`.
- `tests/dobles.py::ExtractorGuionado(respuesta)` implementa el puerto y
  devuelve lo que se le indique. Simula al modelo que inventa un campo,
  devuelve `valor: "mucho"` o devuelve un texto en vez de una lista.

Límite conocido: el contrato valida forma, no verdad. Un correo con datos
falsos bien formados produce filas falsas con cualquiera de los dos
extractores.

## 7. Entradas no confiables

- **Excel:** antes de abrir se revisa el zip. `vbaProject.bin` rechaza el
  archivo. Se abre con `data_only=False`, así que una fórmula llega como
  fórmula y no se evalúa ni se confía en el valor que calculó el Excel de otro.
- **XML:** solo `defusedxml`. DTD y entidades externas rechazan el archivo.
- **Correo:** solo la parte `text/plain`. No se interpreta HTML, no se siguen
  enlaces y no se guardan adjuntos.
- **Rutas:** el día de carga se valida con `date.fromisoformat` antes de armar
  rutas, y `resolver_confinada` rechaza absolutas, `..` y enlaces simbólicos.

## 8. Pruebas

Agrupadas en los archivos del árbol. Cada una nombra la regla que rompe.

| Prueba | Qué falla si la regla se rompe |
|---|---|
| `test_filas_entran_igual_aceptadas_mas_rechazadas` | Las leídas de un archivo no son la suma de aceptadas y rechazadas. |
| `test_conciliacion_detecta_fila_perdida` | Con `cuarentena.registrar` anulado el estado sale `cuadra`. |
| `test_conciliacion_no_compensa_entre_archivos` | Un sobrante y un faltante en archivos distintos no rompen `filas_por_archivo`. |
| `test_conciliacion_detecta_mart_adulterado` | Quitar una fila del mart por fuera no rompe `totales_mart`. |
| `test_archivo_sin_procesar_no_cuadra` | Un archivo en landing sin fila de contrato no rompe `archivos`. |
| `test_reejecutar_mismo_dia_no_duplica` | El volcado de la base cambia tras la segunda corrida. |
| `test_mismo_contenido_con_otro_nombre_no_reingresa` | Aparece una segunda fila en `landing_archivo`. |
| `test_archivo_corregido_no_pisa_grano_aceptado` | El valor del hecho cambia, o la fila no queda como `grano_duplicado`. |
| `test_fecha_imposible_va_a_cuarentena_con_motivo` | `2026-02-30`, `31/12/2026`, vacía, futura u otro día dan otro motivo o se aceptan. |
| `test_unidad_distinta_va_a_cuarentena_con_motivo` | `lb`, `t` o `docenas` llegan al hecho, o el detalle no nombra la unidad recibida y la esperada. |
| `test_columna_faltante_no_se_adivina` | Un Excel sin columna `unidad` produce alguna fila aceptada. |
| `test_fila_vacia_de_excel_no_cuenta` | `filas_leidas` incluye filas con todas las celdas vacías. |
| `test_motivos_son_lista_cerrada` | La lista del código y el `CHECK` difieren, o un motivo `otro` se inserta. |
| `test_detalle_de_cuarentena_sin_traza` | El detalle contiene `Traceback`, `File "`, el nombre de una librería o una ruta. |
| `test_cuarentena_no_se_borra_ni_edita` | `DELETE` o `UPDATE` no devuelven error. |
| `test_landing_no_se_edita` | `UPDATE` pasa, el archivo es escribible, o alterar bytes no da `archivo_alterado`. |
| `test_ruta_no_escapa_de_landing` | `../x`, ruta absoluta o enlace simbólico no lanzan `RutaNoConfinada`. |
| `test_dia_invalido_no_construye_ruta` | Un día `../../etc` toca el disco antes de fallar. |
| `test_enlace_simbolico_en_buzon_va_a_cuarentena` | Se copia el destino del enlace, o no queda `entrada_no_regular`. |
| `test_xml_con_entidad_externa_se_rechaza` | El contenido del archivo referido aparece en la base. |
| `test_xml_bomba_de_entidades_se_rechaza` | El proceso expande entidades en vez de rechazar. |
| `test_excel_con_macros_se_rechaza` | Un zip con `vbaProject.bin` produce filas. |
| `test_formula_de_excel_no_se_evalua` | Una celda `=1+1` llega como 2 a cualquier tabla. |
| `test_correo_html_y_enlaces_no_se_siguen` | Un correo solo HTML produce filas, o se intenta abrir un socket. |
| `test_correo_pasa_por_el_mismo_contrato` | Las ventas del `.eml` no llegan al hecho, o llegan por otro camino que `reglas.validar`. |
| `test_campo_inventado_por_extractor_no_llega_al_mart` | Con una clave extra la fila aparece en el mart o no queda `campo_extra`. |
| `test_tipo_invalido_del_extractor_va_a_cuarentena` | `valor: "mucho"` es aceptado. |
| `test_salida_no_lista_del_extractor_rechaza_archivo` | No queda `extraccion_invalida` con cero filas leídas. |
| `test_sin_clave_se_usa_determinista` | Sin variable de entorno se elige otro extractor. |
| `test_suite_no_usa_red` | Algún módulo fuera de `api_opcional.py` importa red, o el socket no está bloqueado. |
| `test_cada_capa_solo_lee_la_anterior` | Leer una tabla ajena no lanza `AccesoCapaProhibido`. |
| `test_fronteras_de_import` | Algún módulo importa algo de su columna de prohibidos. |
| `test_pronostico_no_puede_leer_capa_cruda` | Con la conexión de pronóstico, un `SELECT` sobre landing, contrato, cuarentena o hecho devuelve datos. |
| `test_pronostico_no_corre_sin_conciliacion` | Sin fila, con `pendiente` o con `no_cuadra`, se escribe alguna fila de pronóstico. |
| `test_pronostico_excluye_dias_no_conciliados` | Un día `no_cuadra` entra al promedio. |
| `test_pronostico_es_linea_base_reproducible` | El valor no coincide con el cálculo a mano de una serie fija. |
| `test_modelo_una_fila_por_dia_y_lote` | Tres métricas de tres formatos producen más de una fila. |
| `test_mart_sin_columnas_de_formato` | Alguna tabla `mart_*` tiene una columna `formato`, `archivo` o `fuente`. |
| `test_generador_con_misma_semilla_es_reproducible` | Dos corridas con la misma semilla dan contenido distinto. |
| `test_generador_no_usa_reloj_ni_random_global` | `sintetico/` importa `time` o usa `random.random`. |
| `test_dependencias_fijadas` | Alguna línea de `requirements.lock` no tiene `==` y `--hash`. |
| `test_demo_sin_script_ni_recursos_externos` | La demo contiene una etiqueta de script o una dirección externa. |
| `test_demo_sin_trazas_rutas_ni_secretos` | Aparece una traza, una ruta del sistema o el nombre de una variable de clave. |
| `test_demo_sin_emojis` | Hay un carácter de los rangos Unicode de emoji. |
| `test_demo_declara_datos_de_demostracion` | Falta el aviso o la semilla. |
| `test_demo_escapa_texto_de_cuarentena` | Un texto con etiquetas rechazado aparece sin escapar. |

## 9. Demo

`demo/exportar.py` genera `docs/index.html` ya renderizado, sin JavaScript:

- Usa la plantilla con `html.escape` en cada valor.
- Dibuja los gráficos como SVG en línea.
- Declara `Content-Security-Policy: default-src 'none'; style-src 'self'`.
- Escribe los mismos datos en `docs/datos/tablero.json`.

La página muestra, en español:

1. Título "Carga operativa: caso de demostración" y el aviso de datos
   sintéticos, con la semilla.
2. El día cargado (`DIA_DEMO`, fijo en `config.py`) y el estado de la
   conciliación, con los seis controles.
3. Tres paneles (producción en kg, mortalidad en aves, ventas en kg) con el
   total del día, una tabla por lote y la serie de 14 días.
4. La línea base a 7 días, con el método, las observaciones usadas y el error
   retrospectivo. Lleva el rótulo "Línea base (promedio o estacional simple).
   No es un modelo predictivo validado." Si no se emitió, dice por qué.
5. "Lo que no entró": archivo, alcance (archivo completo o N filas), motivo en
   lenguaje llano y un detalle de ejemplo.
6. Al pie: extractor usado, versión del contrato y semilla.

Todo es de demostración: lotes, galpones, valores, nombres de archivo y el
correo (remitente en dominio `.example`). El generador siembra a propósito, en
el día de la demo, un archivo con macros, una celda con fórmula, una fila en
`lb`, una fecha `2026-11-31`, un lote inexistente y una línea de correo sin
unidad.

La demo no muestra un día `no_cuadra`: con datos sucios pero bien contados, la
conciliación cuadra. Esa compuerta se demuestra en las pruebas.

## 10. Decisiones descartadas

- **pandas o polars para leer:** infieren tipos y fechas, que es lo que el contrato prohíbe.
- **Mart como vistas SQL:** el autorizador tendría que abrirle el modelo al pronóstico; con tablas la frontera se puede probar.
- **Conversión de unidades (`lb` a `kg`):** es un parche; va a cuarentena.
- **Alias o coincidencia aproximada de encabezados:** es adivinar la columna.
- **Último archivo gana sobre el grano:** pisa en silencio un dato aceptado; el conflicto va a cuarentena.
- **Aceptar filas de días anteriores:** cambia un día ya conciliado y rompe la idempotencia por día.
- **Borrar y recargar el día:** obligaría a borrar cuarentena.
- **Varios motivos por fila:** uno solo, por orden fijo, es determinista y fácil de probar.
- **Clase tipada como salida del extractor:** descartaría el campo inventado antes de que el contrato lo vea.
- **Recambio al determinista si la API falla:** mezcla extractores en un mismo día sin dejar rastro.
- **Modelo de lenguaje también para Excel y XML:** ya son estructurados.
- **`data_only=True` en openpyxl:** confía en un valor que calculó el Excel de otro.
- **`xml.etree` de la biblioteca estándar para leer:** no rechaza el DTD de forma explícita.
- **Procesar el archivo donde llegó, sin copiar:** el remitente puede sobrescribirlo; la copia por contenido conserva lo que llegó.
- **`import-linter`:** una dependencia más para lo que resuelve una prueba con `ast`.
- **SQLAlchemy y Alembic:** un `esquema.sql` alcanza, y el ORM oculta el SQL que el autorizador vigila.
- **Página con JavaScript y librería de gráficos:** agrega pedidos externos y superficie de inyección.
- **ARIMA, Prophet o scikit-learn:** con 35 días sintéticos no serían honestos.
- **Versionar `datos/`:** se regenera con la semilla; solo se versiona `docs/`.
