# Carga operativa

Caso de demostración, etiquetado como tal. No es una granja real ni datos de un empleador.
Cada día llegan producción, mortalidad y ventas en Excel, XML y un correo exportado.
El sistema no espera un archivo limpio.

## Capas

Cada capa solo lee la anterior.

| Capa | Guarda | No puede |
|---|---|---|
| Landing | El archivo tal como llegó, con fecha y nombre | Editarse |
| Contrato | Columnas, tipos, unidades y grano (día por lote) | Adivinar una columna que falta |
| Cuarentena | La fila rechazada y el motivo | Borrar en silencio |
| Modelo | Hechos y dimensiones, una fila por día y lote | Leer el archivo crudo |
| Mart | Producción, mortalidad y ventas para el tablero | Mezclar formatos |
| Pronóstico | Línea base sobre el mart | Correr si la conciliación del día falló |

## Calidad que las pruebas tienen que fijar

- Filas que entran = filas aceptadas + filas rechazadas.
- Volver a correr el mismo día no duplica.
- Una fecha imposible o una unidad distinta va a cuarentena con el motivo escrito.
- El pronóstico falla si intenta leer la capa cruda.
- La conciliación del día tiene que cuadrar antes del pronóstico.

## IA

Módulo, no otra app. El correo se extrae a un registro con fecha, lote y valor, y pasa por el mismo contrato. Si el modelo inventa un campo, esa fila no llega al mart. La extracción tiene un puerto falso para las pruebas: no hace falta una API key para correr la suite. Si no hay clave, se usa el extractor determinista de demostración y queda dicho en el README.

## Demo

Página estática en docs/, en español. Muestra el tablero del día que cargó y el archivo que no entró, con la razón. Sin backend público, sin secretos, sin emojis.

## Seguridad e ingeniería

- XML sin resolución de entidades externas.
- Excel sin macros ni fórmulas ejecutadas.
- El correo es texto: no se ejecuta, no se siguen enlaces.
- Rutas de entrada confinadas al directorio de landing. Nada de path traversal.
- La salida del extractor se valida contra el contrato. Un campo extra o un tipo inválido es cuarentena, no un parche.
- Datos sintéticos, con semilla, generados por el repo.
- Dependencias con versión fijada. Nada de servicios de pago obligatorios.
- El pronóstico es una línea base honesta (promedio o estacional simple), no un modelo que se presente como científico.
