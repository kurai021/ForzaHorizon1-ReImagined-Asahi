# Port experimental Asahi: fase 2

## Estado al finalizar

Se integró una ruta **opt-in y limitada** de memexport compute dentro del
`IssueDraw` real. Siete casos sin rasterizado pasaron sobre la Apple M2 Pro /
Honeykrisp, con lectura de vuelta y validación de sincronización limpias.
Se compiló e instaló localmente el SDK completo necesario, incluido `rexglue`,
el runtime y el plugin; se compilaron las fuentes anfitrionas de Forza en ARM64.

**No se generó ni enlazó el juego:** faltan los archivos comerciales indicados
abajo. No se probó su arranque, menú ni una escena real; no hay una medición de
720p/30 FPS ni evidencia de jugabilidad.

**Las pruebas gráficas están suspendidas.** Una prueba raster expuso un fallo de
CPU en el traductor, corregido y validado offline. Durante el diagnóstico se
registraron también reconexiones/modos de pantalla y errores `apple-dcp` del
monitor externo. No se atribuyen esos eventos al SDK sin evidencia adicional.
Se conserva `forza-recomp/out/asahi-sdk-lab/STOP-GPU.json`; ninguna prueba GPU se
ejecutó después de identificar esos eventos. Las correcciones finales no se
han revalidado sobre la GPU.

La auditoría y las evidencias anteriores permanecen en
[asahi-sdk-audit.md](asahi-sdk-audit.md). El SDK sigue en
`bd833a2ab69d305f99ddf57418fc38ffec8af93d`; no se cambió su revisión para
conseguir el resultado.

## Integración implementada

El cvar `vulkan_memexport_compute` conserva **false** como valor por defecto.
Se necesita, además del permiso de inicialización de la fase 1, activarlo
explícitamente. No se habilita ni falsea `vertexPipelineStoresAndAtomics`.

- Se traduce la variante `kMemExportCompute` y se crea/cachea su pipeline.
- Los índices DMA originales se copian **en la GPU** a un snapshot distinto de
  la memoria exportada. La copia se alinea a cuatro bytes, preservando el
  desplazamiento de un índice de 16 bits que comience en `base + 2`.
- Una nueva modificación de shader selecciona el snapshot en el binding 2 del
  set compartido. El shader compute conserva endianess, máscara de 24 bits,
  offset base, contador de invocaciones y primitive reset.
- Se usa el número original de vértices, no el número amplificado por la
  conversión de primitivas. Las variantes de teselación y expansión VS se
  rechazan explícitamente antes del dibujo.
- El snapshot se enlaza con un descriptor distinto por despacho, reclamado
  mediante los fences normales de frame. No se modifica un descriptor común
  mientras lo usan comandos anteriores.
- Los buffers scratch ahora declaran `TRANSFER_DST` e `INDEX_BUFFER`, además de
  sus usos previos. Se corrigió la asignación intercambiada de máscaras de etapa
  y acceso al crear un scratch nuevo.
- Se comprueban los rangos con aritmética de 64 bits contra los 512 MiB de
  memoria compartida. Los tamaños de exportación subword incluyen el dword
  final usado por las operaciones atómicas.
- Para un dibujo con rasterizado, el orden experimental es **rasterizar con los
  datos originales y después producir los efectos compute**. Así no se vuelve
  a rasterizar leyendo datos ya modificados por el prepass de exportación.
  Este orden aún necesita validación gráfica; no se asume equivalencia para
  todas las combinaciones de efectos.
- El procesador termina el render pass y establece las barreras entre la copia
  de índices, las lecturas gráficas, los efectos compute y las lecturas/copias
  siguientes. Invalida los rangos escritos a través de `RangeWrittenByGpu`.
- La ruta diagnóstica fuerza readback síncrono a la memoria huésped para no
  dejar obsoletos los índices/datos que el procesador de primitivas lee en CPU.
  Se propagan errores de asignación, espera, mapeo e invalidación de readback.
  **Este camino prioriza diagnóstico y coherencia, no rendimiento.**
- Un pipeline raster placeholder no puede dar por satisfecho un dibujo con
  efectos de memoria. Se espera al pipeline real o se devuelve un fallo.

Se rechazan por ahora: teselación, expansión de primitivas en VS, texturas de
vértices, memexport simultáneo desde pixel shader, FSI, fuentes de índices
distintas de DMA/auto y destinos no identificables por el analizador del SDK.
Estos rechazos son límites del experimento, no pruebas de que Forza no los use.

## Pruebas y resultados

Todos los timestamps siguientes son UTC y los directorios están bajo
`forza-recomp/out/asahi-sdk-lab/logs/`.

### Ejecución real en Honeykrisp

`20261024T233558.086870Z-phase2-issue-draw-integration`:

| Caso por `IssueDraw` | Resultado |
| --- | --- |
| Índices automáticos | PASS |
| DMA 16 bits big-endian, base desplazada dos bytes | PASS |
| DMA 16 bits little-endian | PASS |
| DMA 32 bits big-endian y máscara de 24 bits | PASS |
| Offset de índice base | PASS |
| Primitive reset de 16 bits | PASS |
| Solapamiento entre buffer de índices y destino exportado | PASS |

Cada caso comparó 4096 bytes del espejo CPU con el resultado esperado, incluidos
los bytes sin modificar. La sonda y el supervisor identificaron la GPU integrada
Apple M2 Pro y Honeykrisp. El proceso usó exclusivamente el ICD de Asahi; no se
aceptó llvmpipe. Hubo cero errores/advertencias Vulkan y ningún error del kernel
en esa ejecución. Esto valida los casos enumerados, no todas las variantes de
memexport ni una escena del juego.

### Intento raster y suspensión

`20261024T234344.828351Z-phase2-raster-issue-draw` completó otra vez los siete
casos y alcanzó el timeout al añadir rasterizado. La prueba no produjo una imagen
raster verificada.

La sonda diagnóstica limitada
`20261024T234605.493322Z-phase2-pipeline-cpu-stacks` capturó
`cpu-stacks.log`: el hilo principal estaba entrando repetidamente en
`ExceptionHandlerCallback`, desde `SpirvShaderTranslator::StoreResult`, al
traducir la variante compute de un shader que también exporta `oPos`.
El hilo de Honeykrisp y el de submissions del SDK estaban esperando.
Esto identifica un fallo de traducción en CPU; no demuestra un bloqueo de GPU.

La variante compute no tiene `output_per_vertex_`, pero `StoreResult` intentaba
escribir la posición. Se añadió un guard para omitir los exports de rasterizado
en la mitad compute, conservándolos en la mitad VS ordinaria. Se añadió un
fixture de regresión con posición y memexport en el mismo microcódigo.

El mismo registro del kernel contiene hotplug/modesets y `IOMFB ... EDT ERROR`
del monitor externo. Mensajes similares ya existían antes de esta fase, pero
su relación con el experimento sigue sin establecerse. Se detuvieron las
pruebas gráficas y se mantuvo el bloqueo en el supervisor. **El arnés contiene
la prueba de readback raster, pero no hay un resultado PASS de esa prueba.**

### Compilación y validación final sin GPU

- `20261024T235412.907343Z-asahi-sdk-install`: SDK/runtime/plugin/generador
  compilados e instalados dentro del laboratorio. Binarios ELF aarch64.
- `20261024T235427.574575Z-asahi-host-build`: integración anfitriona de Forza
  compilada. Incluye la cabecera de la app y las fuentes reales, sin sustituir
  código generado. No es un ejecutable del juego.
- `20261024T235049.349653Z-phase2-codegen-tool`: `rexglue --version` devuelve
  `0.10.0.24-dev.gbd833a2`.
- `20261024T235427.432830Z-phase2-final-offline`: siete fixtures traducidos sin
  crear instancia, dispositivo ni submissions Vulkan.
- `20261024T235437.080568Z-phase2-final-spirv`: los siete pasan
  `spirv-val --target-env vulkan1.3 --scalar-block-layout`, incluido el nuevo
  caso compute con outputs de rasterizado.
- `20261024T235049.337397Z-phase2-game-build-preflight`: build del juego
  bloqueado explícitamente por archivos ausentes, salida 2.

Los fixtures anteriores de 8/16/128 bits, límites y fragment shader permanecen
en el arnés. Sus registros de la fase 1 siguen disponibles; la suspensión impidió
repetir toda esa suite sobre el binario final de esta fase.

## Archivos comerciales necesarios

Para generar y enlazar la versión base:

```text
extracted/game/default.xex
extracted/game/XMediaFacade_default.xex
extracted/game/SpeechFacade_default.xex
```

Para arrancar y cargar una escena se requiere la extracción **completa y
coherente del disco 1**, incluyendo todo `media/` y en particular
`media/db/gamedb.slt`, no solo esos ejecutables. Conservar también el resto de
archivos de la extracción. Title ID esperado: `4D5309C9`; la compatibilidad de
la edición se comprobará con los validadores existentes antes de generar código.

También sirve indicar la ruta a una ISO propia del disco 1 o a un Games on
Demand completo (cabecera y todos sus fragmentos) para extraerlo localmente.
No se necesita TU4 ni DLC para el primer ensayo con la versión base. No se
buscaron ni descargaron archivos comerciales.

## Uso del flujo preparado

`forza-recomp/CMakeLists.txt` ahora distingue `linux-arm64` de `linux-amd64`.
El helper evita el launcher x86-64 y selecciona explícitamente el SDK local:

```sh
python3 -B tools/asahi-sdk-lab/build.py sdk
python3 -B tools/asahi-sdk-lab/build.py host
python3 -B tools/asahi-sdk-lab/build.py check-game
# Solo cuando estén los archivos comerciales:
python3 -B tools/asahi-sdk-lab/build.py game
```

El SDK debe estar preparado con los parches; el helper no restaura ni sobrescribe
su checkout mediante el preparador canónico. `game` verifica los archivos antes
de invocar codegen, y no fabrica fuentes para aparentar un build completo.

Hay un modo `play` preparado para Vulkan, 720p, escala 1, objetivo de 30 FPS,
perfil offline separado, validación, FPS log y límite de 120 segundos. **No se
ejecutó** y se niega a iniciar mientras exista `STOP-GPU.json`. El perfil está
en `forza-recomp/out/asahi-sdk-lab/profile-base`, separado de perfiles normales.
El supervisor registra identidad de GPU, salida, kernel y estado de validación;
no cambia variables permanentemente en la sesión ni archivos globales de Vulkan.

## Parches, procedencia y reversión

La fase 1 se conserva en `tools/asahi-sdk-lab/experimental-sdk.patch` y en
`forza-recomp/out/asahi-sdk-lab/phase1/`. La integración incremental se exporta
como `tools/asahi-sdk-lab/memexport-compute-sdk.patch`. Se aplica **después** de
la serie original del proyecto y del parche de fase 1, en un checkout dedicado
del SDK fijado.

```sh
git -C forza-recomp/out/asahi-sdk-lab/sdk apply --check \
  "$PWD/tools/asahi-sdk-lab/memexport-compute-sdk.patch"
# Aplicar solo en un checkout preparado que aún no contenga la integración.
```

Para revertir únicamente la fase 2, con todos los procesos del experimento
detenidos, comprobar primero y después invertir el parche:

```sh
git -C forza-recomp/out/asahi-sdk-lab/sdk apply -R --check \
  "$PWD/tools/asahi-sdk-lab/memexport-compute-sdk.patch"
git -C forza-recomp/out/asahi-sdk-lab/sdk apply -R \
  "$PWD/tools/asahi-sdk-lab/memexport-compute-sdk.patch"
```

Eso conserva la fase 1. Los binarios deben reconstruirse para reflejar la
reversión. El cambio de plataforma del CMake principal tiene su diff separado
en `forza-recomp/out/asahi-sdk-lab/project-platform.patch`; no descartar otros
cambios de usuario al revertirlo.

`snapshot.py --phase2 --publish` regenera el parche incremental, el combinado,
hashes de fuentes/binarios y el inventario de ejecuciones en `manifest.json`.
No modifica el índice real ni crea commits. Los logs originales y el marcador
de suspensión permanecen archivados. No se tocó Mesa, Honeykrisp, kernel, ICD,
controladores ni configuración gráfica global.

## Pendientes para la siguiente sesión

1. Establecer estabilidad del monitor/sistema antes de reanudar cualquier prueba
   gráfica; el marcador de suspensión no se elimina automáticamente.
2. Repetir las regresiones sobre el SDK final, incluida la prueba raster con
   readback. Ampliar cobertura a readbacks consecutivos, variantes de reset y
   formatos subword a través de `IssueDraw`.
3. Proporcionar los archivos propios y generar/enlazar el juego completo.
4. Ensayar primero arranque y luego una escena real a 720p/30 como objetivo,
   registrando fallos, frame times y comportamiento visual. El menú por sí solo
   no satisface esa validación.
5. Ampliar variantes de memexport únicamente con pruebas de semántica y orden;
   los rechazos actuales deben seguir siendo explícitos hasta entonces.
