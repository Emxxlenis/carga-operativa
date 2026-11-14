-- Único DDL de carga-operativa. Tablas STRICT; requiere SQLite 3.37 o superior.
-- No hay claves foráneas entre capas no adyacentes.

-- LANDING. Grano: un archivo por contenido. Solo-inserción.
CREATE TABLE IF NOT EXISTS landing_archivo (
  archivo_id      INTEGER PRIMARY KEY,
  sha256          TEXT NOT NULL UNIQUE,
  dia_carga       TEXT NOT NULL,
  nombre_original TEXT NOT NULL,
  ruta_relativa   TEXT NOT NULL UNIQUE,
  formato         TEXT NOT NULL CHECK (formato IN ('xlsx', 'xml', 'eml', 'otro')),
  bytes           INTEGER NOT NULL CHECK (bytes >= 0),
  recibido_en     TEXT NOT NULL
) STRICT;

CREATE TRIGGER IF NOT EXISTS landing_archivo_sin_update
BEFORE UPDATE ON landing_archivo
BEGIN
  SELECT RAISE(ABORT, 'landing_archivo es solo-insercion');
END;

CREATE TRIGGER IF NOT EXISTS landing_archivo_sin_delete
BEFORE DELETE ON landing_archivo
BEGIN
  SELECT RAISE(ABORT, 'landing_archivo es solo-insercion');
END;

-- CONTRATO
CREATE TABLE IF NOT EXISTS ref_lote (
  codigo         TEXT PRIMARY KEY,
  galpon         TEXT NOT NULL,
  fecha_ingreso  TEXT NOT NULL,
  aves_iniciales INTEGER NOT NULL CHECK (aves_iniciales > 0)
) STRICT;

-- Grano: un archivo procesado. Su existencia significa "ya se procesó".
CREATE TABLE IF NOT EXISTS contrato_archivo (
  archivo_id       INTEGER PRIMARY KEY REFERENCES landing_archivo,
  estado           TEXT NOT NULL CHECK (estado IN ('procesado', 'rechazado')),
  filas_leidas     INTEGER NOT NULL CHECK (filas_leidas >= 0),
  version_contrato TEXT NOT NULL,
  extractor        TEXT CHECK (extractor IN ('determinista', 'api')),
  procesado_en     TEXT NOT NULL,
  CHECK (estado = 'procesado' OR filas_leidas = 0)
) STRICT;

-- Grano: fecha x lote x metrica. Solo-inserción.
CREATE TABLE IF NOT EXISTS contrato_fila_aceptada (
  fila_id    INTEGER PRIMARY KEY,
  archivo_id INTEGER NOT NULL REFERENCES contrato_archivo,
  n_fila     INTEGER NOT NULL,
  fecha      TEXT NOT NULL,
  lote       TEXT NOT NULL REFERENCES ref_lote,
  metrica    TEXT NOT NULL CHECK (metrica IN ('produccion', 'mortalidad', 'ventas')),
  valor      REAL NOT NULL CHECK (valor >= 0),
  unidad     TEXT NOT NULL,
  CHECK ((metrica IN ('produccion', 'ventas') AND unidad = 'kg')
      OR (metrica = 'mortalidad' AND unidad = 'aves')),
  UNIQUE (archivo_id, n_fila),
  UNIQUE (fecha, lote, metrica)
) STRICT;

CREATE TRIGGER IF NOT EXISTS contrato_fila_aceptada_sin_update
BEFORE UPDATE ON contrato_fila_aceptada
BEGIN
  SELECT RAISE(ABORT, 'contrato_fila_aceptada es solo-insercion');
END;

CREATE TRIGGER IF NOT EXISTS contrato_fila_aceptada_sin_delete
BEFORE DELETE ON contrato_fila_aceptada
BEGIN
  SELECT RAISE(ABORT, 'contrato_fila_aceptada es solo-insercion');
END;

-- CUARENTENA. Grano: un rechazo por (archivo, fila). Solo-inserción.
CREATE TABLE IF NOT EXISTS cuarentena (
  cuarentena_id   INTEGER PRIMARY KEY,
  dia_carga       TEXT NOT NULL,
  nombre_archivo  TEXT NOT NULL,
  archivo_id      INTEGER REFERENCES landing_archivo,
  nivel           TEXT NOT NULL CHECK (nivel IN ('archivo', 'fila')),
  n_fila          INTEGER NOT NULL DEFAULT 0,
  motivo          TEXT NOT NULL CHECK (motivo IN (
    'campo_extra',
    'columna_faltante',
    'formula_no_evaluada',
    'tipo_invalido',
    'fecha_invalida',
    'fecha_fuera_de_dia',
    'metrica_desconocida',
    'unidad_distinta',
    'lote_desconocido',
    'valor_fuera_de_rango',
    'grano_duplicado',
    'entrada_no_regular',
    'archivo_alterado',
    'archivo_demasiado_grande',
    'extension_no_admitida',
    'excel_con_macros',
    'xml_con_dtd_o_entidades',
    'correo_sin_texto_plano',
    'extraccion_invalida',
    'archivo_ilegible'
  )),
  detalle         TEXT NOT NULL,
  fila_cruda_json TEXT,
  creado_en       TEXT NOT NULL,
  CHECK ((nivel = 'archivo') = (n_fila = 0)),
  CHECK ((nivel = 'archivo') = (motivo IN (
    'entrada_no_regular',
    'archivo_alterado',
    'archivo_demasiado_grande',
    'extension_no_admitida',
    'excel_con_macros',
    'xml_con_dtd_o_entidades',
    'correo_sin_texto_plano',
    'extraccion_invalida',
    'archivo_ilegible'
  ))),
  CHECK (archivo_id IS NOT NULL OR motivo = 'entrada_no_regular')
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_cuarentena
  ON cuarentena (dia_carga, nombre_archivo, IFNULL(archivo_id, 0), n_fila);

CREATE TRIGGER IF NOT EXISTS cuarentena_sin_update
BEFORE UPDATE ON cuarentena
BEGIN
  SELECT RAISE(ABORT, 'cuarentena es solo-insercion');
END;

CREATE TRIGGER IF NOT EXISTS cuarentena_sin_delete
BEFORE DELETE ON cuarentena
BEGIN
  SELECT RAISE(ABORT, 'cuarentena es solo-insercion');
END;

-- La capa landing solo puede registrar entradas no regulares. capa_actual()
-- la define cada conexión de db/conexion.py; sin ella la inserción falla.
CREATE TRIGGER IF NOT EXISTS cuarentena_landing_solo_entrada_no_regular
BEFORE INSERT ON cuarentena
WHEN capa_actual() = 'landing' AND NEW.motivo <> 'entrada_no_regular'
BEGIN
  SELECT RAISE(ABORT, 'landing solo registra entrada_no_regular');
END;

-- MODELO
CREATE TABLE IF NOT EXISTS dim_fecha (
  fecha      TEXT PRIMARY KEY,
  anio       INTEGER NOT NULL,
  mes        INTEGER NOT NULL,
  dia        INTEGER NOT NULL,
  dia_semana INTEGER NOT NULL CHECK (dia_semana BETWEEN 1 AND 7),
  semana_iso INTEGER NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS dim_lote (
  lote_id        INTEGER PRIMARY KEY,
  codigo         TEXT NOT NULL UNIQUE,
  galpon         TEXT NOT NULL,
  fecha_ingreso  TEXT NOT NULL,
  aves_iniciales INTEGER NOT NULL
) STRICT;

-- Grano: una fila por fecha y lote. NULL = no reportado; no es cero.
CREATE TABLE IF NOT EXISTS hecho_dia_lote (
  fecha           TEXT NOT NULL REFERENCES dim_fecha,
  lote_id         INTEGER NOT NULL REFERENCES dim_lote,
  produccion_kg   REAL,
  mortalidad_aves INTEGER,
  ventas_kg       REAL,
  PRIMARY KEY (fecha, lote_id),
  CHECK (produccion_kg IS NOT NULL OR mortalidad_aves IS NOT NULL OR ventas_kg IS NOT NULL)
) STRICT;

-- MART. Sin columnas de formato ni de archivo; la unidad va en el nombre.
CREATE TABLE IF NOT EXISTS mart_produccion (
  fecha         TEXT NOT NULL,
  lote          TEXT NOT NULL,
  galpon        TEXT NOT NULL,
  produccion_kg REAL NOT NULL,
  PRIMARY KEY (fecha, lote)
) STRICT;

CREATE TABLE IF NOT EXISTS mart_mortalidad (
  fecha           TEXT NOT NULL,
  lote            TEXT NOT NULL,
  galpon          TEXT NOT NULL,
  mortalidad_aves INTEGER NOT NULL,
  PRIMARY KEY (fecha, lote)
) STRICT;

CREATE TABLE IF NOT EXISTS mart_ventas (
  fecha     TEXT NOT NULL,
  lote      TEXT NOT NULL,
  galpon    TEXT NOT NULL,
  ventas_kg REAL NOT NULL,
  PRIMARY KEY (fecha, lote)
) STRICT;

-- Grano: un día. Entrada del pronóstico.
CREATE TABLE IF NOT EXISTS mart_resumen_dia (
  fecha                TEXT PRIMARY KEY,
  produccion_kg        REAL,
  mortalidad_aves      INTEGER,
  ventas_kg            REAL,
  lotes_con_produccion INTEGER NOT NULL,
  lotes_con_mortalidad INTEGER NOT NULL,
  lotes_con_ventas     INTEGER NOT NULL
) STRICT;

-- CONCILIACIÓN
CREATE TABLE IF NOT EXISTS conciliacion_dia (
  dia_carga    TEXT PRIMARY KEY,
  estado       TEXT NOT NULL CHECK (estado IN ('pendiente', 'cuadra', 'no_cuadra')),
  calculado_en TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS conciliacion_control (
  dia_carga TEXT NOT NULL REFERENCES conciliacion_dia,
  control   TEXT NOT NULL CHECK (control IN (
    'archivos', 'filas', 'filas_por_archivo', 'grano_modelo', 'totales_modelo', 'totales_mart'
  )),
  esperado  REAL NOT NULL,
  obtenido  REAL NOT NULL,
  cuadra    INTEGER NOT NULL CHECK (cuadra IN (0, 1)),
  PRIMARY KEY (dia_carga, control)
) STRICT;

-- PRONÓSTICO. Grano: día base x fecha objetivo x metrica (total, no por lote).
CREATE TABLE IF NOT EXISTS pronostico_linea_base (
  dia_base        TEXT NOT NULL,
  fecha_objetivo  TEXT NOT NULL,
  metrica         TEXT NOT NULL CHECK (metrica IN ('produccion', 'mortalidad', 'ventas')),
  valor           REAL NOT NULL,
  metodo          TEXT NOT NULL CHECK (metodo IN ('promedio_movil_7', 'estacional_semanal')),
  n_observaciones INTEGER NOT NULL,
  mae_retro       REAL,
  generado_en     TEXT NOT NULL,
  PRIMARY KEY (dia_base, fecha_objetivo, metrica)
) STRICT;
