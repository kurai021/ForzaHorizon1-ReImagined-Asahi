# Auditoría de ReXGlue sobre Fedora Asahi ARM64

> Este documento conserva los resultados de la primera fase. La integración
> posterior y el estado actual de las pruebas están en
> [Port Asahi, fase 2](asahi-port-phase2.md).

Fecha: 2026-10-24. Forza: `43b64ae`. SDK:
`bd833a2ab69d305f99ddf57418fc38ffec8af93d` (0.10.0.24), con los 116 parches
SDK del proyecto y su serie SDL. Pruebas sin archivos, código recompilado ni
shaders extraídos del juego.

## Conclusión

**El backend puede inicializarse y ejecutar shaders sintéticos de ReXGlue sobre
la GPU Apple M2 Pro con Honeykrisp.** La ausencia de
`vertexPipelineStoresAndAtomics` no impide todo renderizado, pero sí impide la
exportación de memoria directamente desde las etapas anteriores al fragmento.

La alternativa compute del traductor puede ejecutar exportaciones sintéticas
con resultados comprobados. **Todavía no está conectada al despacho de dibujos
de este backend:** `IssueDraw` continúa rechazando explícitamente el memexport
de vértices. Esta auditoría no convierte esa ruta en una implementación completa.

Procede una siguiente fase de **compilación e integración experimental** de
Forza, con diagnóstico de las rutas realmente utilizadas. No hay fundamento para
anunciar todavía un build jugable o compatible. Antes de ejecutar sin rechazos
dibujos que exporten memoria desde vértices, hay que integrar y validar el
despacho compute y su sincronización con los dibujos. No se solicitaron ni
procesaron archivos del juego en esta fase.

## Sistema y aislamiento

| Elemento | Medición |
| --- | --- |
| Sistema | Fedora Asahi Remix 44, aarch64 |
| Kernel | `7.1.13-402.asahi.fc44.aarch64+16k` |
| CPU/GPU | Apple M2 Pro, 10 núcleos; GPU `Apple M2 Pro (G14S B1)` |
| Memoria | 15 GiB utilizables, unos 8 GiB disponibles durante las pruebas |
| Páginas | 16.384 bytes |
| Compilador | Clang 22.1.8; CMake 4.3; Ninja 1.13.2 |
| Controlador | Honeykrisp, Mesa 26.2.5; driver ID 26 |
| Vulkan físico | 1.4.354; loader instalado 1.4.341 |
| Vendor Vulkan | `0x10005` (Mesa), no `0x106b` de MoltenVK/macOS |

Todo el checkout SDK, las dependencias, binarios, cachés y registros se encuentran
en `forza-recomp/out/asahi-sdk-lab/`, ignorado por Git. Ocupan aproximadamente
1,2 GiB. El código reproducible del experimento está en
`tools/asahi-sdk-lab/` y el supervisor en `tools/asahi_sdk_lab.py`.

Se descargaron y extrajeron localmente estos RPM, sin instalar paquetes:

- `vulkan-validation-layers-1.4.341.0-2.fc44.aarch64`
- `glslang-16.2.0-1.fc44.aarch64`
- `spirv-tools-2026.1-1.fc44.aarch64`

Se compiló el runtime, el plugin Xenos y la sonda nativos ARM64 en Release,
con tres trabajos. No se construyó ni ejecutó el generador de código del juego.
La configuración deshabilitó Tracy; mantuvo las opciones gráficas del proyecto.

No se modificaron Mesa, Honeykrisp, el kernel, el loader, el ICD ni la
configuración permanente del sistema. El SDK no se instaló globalmente.

### Identificación y límites

Cada proceso gráfico se ejecutó con:

- `VK_DRIVER_FILES=/usr/share/vulkan/icd.d/asahi_icd.aarch64.json`.
- Enumeración previa que exige exactamente un dispositivo integrado Apple M2 Pro.
- Nueva comprobación, dentro de la sonda, del dispositivo realmente creado y
  del nombre `Honeykrisp` del controlador. Los recursos de prueba utilizan ese
  mismo dispositivo del SDK.
- Capas de validación cargadas desde la extracción local, mediante variables
  del proceso. La ejecución final utiliza `VK_LAYER_VALIDATE_SYNC=1`.
- Límite externo de ejecución; las esperas de fences de las cargas sintéticas
  tienen además un límite de cinco segundos.
- `stdout.log`, `result.json`, `kernel.log` y `kernel-errors.log` por ejecución.
  El supervisor suspende pruebas ante errores del kernel, timeout o fallos del
  proceso; conserva el motivo en `STOP-GPU.json`.

No hubo una ejecución aceptada sobre llvmpipe. Los registros finales identifican
`/usr/lib64/libvulkan_asahi.so`, GPU integrada, Honeykrisp y la M2 Pro. No se
registraron nuevos errores del kernel durante las pruebas.

## Auditoría del código

Las rutas de esta sección son relativas al checkout del SDK.

1. `src/ui/vulkan/vulkan_device.cpp`, `CreateIfSupported`:
   rechazo incondicional de dispositivos sin `vertexPipelineStoresAndAtomics`.
   El cvar existente se describía como obsoleto e ignorado. La característica
   anunciada por Honeykrisp es falsa; el experimento no la habilita ni falsea.
2. `src/graphics/vulkan/command_processor.cpp`, `SetupContext`:
   segundo rechazo al arrancar el contexto. También contiene preparativos para
   añadir compute a las máscaras de etapas cuando no hay escrituras de vértices.
3. El mismo archivo, `IssueDraw`:
   analiza `memexport_eM_written()` del shader de vértices y rechaza ese dibujo
   si falta la característica. **Este rechazo no se eliminó.** La prueba negativa
   carga microcódigo sintético y verifica la respuesta de ese método real.
4. `include/rex/graphics/pipeline/shader/spirv_translator.h`,
   `IsMemoryExportSupported` / `IsMemoryExportUsed`:
   permite memexport desde fragmentos si hay `fragmentStoresAndAtomics`; desde
   vértices/teselación si hay `vertexPipelineStoresAndAtomics`; o desde una
   traducción `kMemExportCompute`.
5. `src/graphics/pipeline/shader/spirv_translator_memexport.cpp` y
   `spirv_translator.cpp`, `StoreUint32ToSharedMemory`:
   exportan a SSBO. Los elementos de 8/16 bits se conservan mediante
   `OpAtomicAnd` / `OpAtomicOr`; otros formatos utilizan `OpStore`.
   No se trata de escribir simplemente `gl_Position` o salidas de interpolación.
6. El traductor tiene `kMemExportCompute`, `GLCompute`, tamaño local 1×1×1,
   reconstrucción de índices, banderas y un contador de invocaciones. La búsqueda
   en el backend Vulkan no encontró un despacho que seleccione ese tipo para
   sustituir los dibujos actualmente rechazados.
7. `src/graphics/vulkan/render_target_cache.cpp`:
   existe una ruta de host render targets que no necesita fragment shader
   interlock. Su inicialización pasó con interlock deshabilitado por ausencia
   de soporte. No es una solución para el memexport de vértices.

Las heurísticas Apple de macOS basadas en vendor `0x106b`, así como MetalFX,
no equivalen a soporte Honeykrisp. No se aplicaron ajustes de Metal a este sistema.

## Capacidades comprobadas

| Prueba | Resultado y alcance |
| --- | --- |
| SDK con política original | Rechaza la GPU antes de crear el dispositivo lógico, como se esperaba |
| SDK experimental, flag explícito | Crea el dispositivo lógico sobre Honeykrisp sin activar la capacidad ausente |
| `VulkanCommandProcessor::SetupContext` | Pasa: memoria compartida, procesador de primitivas, cachés y pipelines de inicialización |
| Memoria huésped | `Memory::Initialize()` pasa sobre páginas del sistema de 16 KB; no valida todos los write watches |
| Microcódigo Xenos sintético | Análisis y traducción de VS/PS básicos, VS con memexport y alternativa compute |
| SPIR-V | Validación offline con `spirv-val --target-env vulkan1.3 --scalar-block-layout` de los cinco fixtures de auditoría y los dos shaders traducidos ejecutados |
| Referencia VS con escrituras | Generada y validada **solo offline**; nunca se presentó a la GPU como una capacidad soportada |
| Prueba negativa de `IssueDraw` | Rechazo explícito de memexport de vértices, sin omisión silenciosa |
| Compute memexport de 8 bits | Pipeline, dispatch, fence y lectura exacta de 65.536 bytes; conserva los bytes vecinos |
| Compute memexport de 16 bits | Igual, incluido el cambio de un medio word y conservación de vecinos |
| Compute memexport de 128 bits float | Cuatro componentes leídas coinciden bit a bit |
| Índice fuera del stream | No modifica el buffer centinela |
| Cero invocaciones válidas | No modifica el buffer, después del parche de límites |
| Renderizado offscreen | VS GLSL sintético compilado por el SDK + PS Xenos sintético traducido por ReXGlue; pipeline gráfico, draw, copia y lectura de 256 píxeles RGBA rojos exactos |
| Validación final | Cero errores y cero advertencias Vulkan, incluida validación de sincronización |
| Política por defecto tras los parches | Sigue rechazando la GPU sin el flag experimental |

La prueba de imagen usa el dispositivo del SDK y su traductor, pero construye
el pipeline/draw sintético directamente. **No demuestra todavía un dibujo
completo por `IssueDraw`, una presentación en ventana ni una escena de Forza.**
La alternativa compute se ejecuta desde el arnés, no desde `IssueDraw`.

## Defectos encontrados y parches locales

El parche reproducible es
[`tools/asahi-sdk-lab/experimental-sdk.patch`](../tools/asahi-sdk-lab/experimental-sdk.patch),
aplicado **después** de la serie del proyecto. No está agregado a la serie
normal ni cambia los scripts de compilación de Forza.

### 1. Arranque experimental explícito

Hace efectivo el cvar existente
`vulkan_require_vertex_pipeline_stores_and_atomics` en la selección del
dispositivo y en `SetupContext`. Su valor por defecto permanece `true`.
Solo las sondas usan `false`. Mantiene el rechazo de memexport de vértices y
la información real de características Vulkan.

### 2. Crash de CPU al traducir `alloc export` no soportado

`ProcessAllocInstruction` intentaba escribir variables SPIR-V inexistentes
cuando la exportación no estaba soportada. Un fixture de VS con memexport y
la capacidad deshabilitada produjo SIGSEGV.

`coredumpctl info 156328` identifica `spv::Builder::createStore`, llamado por
`SpirvShaderTranslator::ProcessAllocInstruction`. El hilo del controlador estaba
en espera y no se habían presentado cargas GPU en ese modo de traducción.
El kernel no registró errores. No se extrajo una copia adicional del core.

El parche omite el procesamiento de alloc de memexport cuando no está
soportado, coherente con las otras protecciones del traductor. Eso permite
generar la mitad VS sin efectos laterales de una futura alternativa; **no
autoriza a omitir los efectos de un dibujo real**. `IssueDraw` sigue rechazándolo.

### 3. Invocaciones compute fuera de rango en shaders solo de constantes

La comprobación del contador estaba dentro de `if (register_count())`.
El microcódigo sintético solo usa constantes, por lo que ese contador de
registros es cero y la comprobación no se emitía. Con cero invocaciones
válidas y un dispatch de dos, se modificó indebidamente el byte 256 a 123.

Se conservó el buffer completo de la prueba fallida. El parche emite también
la comprobación de índice lineal y contador para el caso sin registros.
El caso cero pasó después; se volvió a validar su SPIR-V y su ejecución.

### 4. Instrumentación y corrección del arnés

El parche añade un include CMake opcional para compilar las sondas en este
checkout privado. El plugin exporta únicamente una entrada de prueba adicional.

La primera discrepancia de lectura lanzaba una excepción que omitía
`ShutdownContext` del arnés: el proceso esperó durante la destrucción de los
recursos/hilos y alcanzó el límite externo de 120 segundos. El archivo de
lectura fallida tiene timestamp **18:10:42.426 UTC**, inmediatamente después del
inicio de la prueba, lo que demuestra que el fence ya había completado antes
del timeout. No apareció el mensaje de timeout de fence ni un error del kernel.
La falta de apagado explícito es la explicación del timeout del proceso, no
evidencia de un bloqueo de GPU. Se añadió limpieza explícita también al fallar.

Ambas suspensiones fueron revisadas y sus motivos se archivaron junto a los
registros originales antes de reanudar con las correcciones. No se repitió una
carga con un bloqueo de GPU conocido.

## Evidencia local

Todos estos directorios están bajo `forza-recomp/out/asahi-sdk-lab/logs/`.
Los nombres contienen timestamps UTC:

| Directorio | Evidencia |
| --- | --- |
| `20261024T180326.845827Z-provider-baseline-logged` | Rechazo original, capa de validación y GPU identificadas |
| `20261024T180357.195802Z-provider-experimental` | Creación experimental del dispositivo |
| `20261024T180403.547987Z-backend-experimental` | Primer arranque del contexto completo |
| `20261024T180616.174193Z-translator-fixtures` | Crash del traductor; revisión, kernel y artefactos previos |
| `20261024T180645.829687Z-translator-crash-info` | Información y trazas del core de CPU |
| `20261024T181042.323135Z-execution-fixtures` | Primeras cuatro ejecuciones correctas, discrepancia de cero invocaciones y revisión del timeout |
| `20261024T181459.603044Z-translator-bounds-fixed` | Cinco fixtures después de las correcciones |
| `20261024T181504.185481Z-spirv-bounds-fixed-validation` | Validación offline de los cinco fixtures |
| `20261024T181638.797754Z-backend-final-validation` | Suite final, prueba negativa, cinco casos compute, imagen y validación limpia |
| `20261024T181722.998509Z-final-spirv-validation` | Validación offline de los shaders ejecutados |
| `20261024T181730.201038Z-default-policy-preserved` | La política normal sigue siendo restrictiva |

La imagen leída está en
`20261024T181638.797754Z-backend-final-validation/artifacts/translated-fragment.ppm`.
Los ficheros `.spv`, `.spvasm` y `.ucode.txt` son sintéticos.

`manifest.json` en la raíz del laboratorio registra commits, hashes de parches,
fuentes, binarios y artefactos, y el resultado de cada ejecución.
`experimental-sdk.patch` exporta los cambios relativos a la serie del proyecto;
`sdk-working-tree.diff` incluye también toda esa serie; `submodules.txt` conserva
las revisiones de dependencias. `snapshot.py` regenera estos registros sin
commits ni cambios del índice real.

## Reproducción y reversión

Desde la raíz del proyecto, en **un directorio de laboratorio nuevo**:

```sh
ROOT="$PWD"
LAB="$ROOT/forza-recomp/out/asahi-sdk-lab"
mkdir -p "$LAB"
REXSDK_SRC="$LAB/sdk" bash tools/build_rexglue_sdk.sh --source-only
git -C "$LAB/sdk" apply "$ROOT/tools/asahi-sdk-lab/experimental-sdk.patch"

python3 -B tools/asahi_sdk_lab.py --name configure --seconds 300 --cwd "$LAB/sdk" -- \
  cmake --preset linux-arm64 \
  -DCMAKE_CONFIGURATION_TYPES=Release -DCMAKE_DEFAULT_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$LAB/install" -DCMAKE_INSTALL_LIBDIR=lib \
  -DREXGLUE_ENABLE_TRACY=OFF -DREX_ASAHI_LAB_SOURCE="$ROOT/tools/asahi-sdk-lab"
python3 -B tools/asahi_sdk_lab.py --name build --seconds 1800 -- \
  cmake --build "$LAB/sdk/out/build/linux-arm64" --config Release \
  --target asahi_sdk_probe --parallel 3
```

La primera preparación descargó los tres paquetes de diagnóstico con `dnf
download`, usando `cachedir`, `logdir` y `destdir` dentro de `$LAB`. Los extrajo
con `rpm2cpio | cpio -idmu -D "$LAB/local-tools"`. `--validation` requiere esa
extracción local; no instala la capa en Fedora.

```sh
python3 -B tools/asahi_sdk_lab.py --name translator --gpu --validation --seconds 30 -- \
  "$LAB/sdk/out/linux-arm64/Release/asahi_sdk_probe" translate \
  --vulkan_validation_enabled=true --vulkan_require_vertex_pipeline_stores_and_atomics=false
# Validar el directorio artifacts de la ejecución que imprime el supervisor:
python3 -B tools/asahi-sdk-lab/validate_spirv.py "$LAB/logs/<ejecucion>/artifacts"

python3 -B tools/asahi_sdk_lab.py --name workloads --gpu --validation --seconds 60 -- \
  env VK_LAYER_VALIDATE_SYNC=1 "$LAB/sdk/out/linux-arm64/Release/asahi_sdk_probe" workloads \
  --vulkan_validation_enabled=true --vulkan_require_vertex_pipeline_stores_and_atomics=false
python3 -B tools/asahi-sdk-lab/snapshot.py
```

No volver a ejecutar el preparador canónico sobre el checkout experimental sin
guardar antes su diff: ese script restaura los archivos a la serie del proyecto.
Los comandos originales y sus límites también están en cada `result.json`.

Para revertir solo el experimento del SDK, con las sondas detenidas:

```sh
git -C "$LAB/sdk" apply -R "$ROOT/tools/asahi-sdk-lab/experimental-sdk.patch"
```

Los binarios ya construidos seguirán siendo experimentales hasta reconstruirlos.
La reversión completa consiste en archivar los registros y retirar el directorio
privado del laboratorio y los archivos nuevos de la auditoría. Ningún componente
gráfico instalado necesita restauración.

## Próximos criterios de avance

- Conectar `kMemExportCompute` a `IssueDraw`, manteniendo resultados y efectos
  laterales: índices 16/32 bits, primitive reset, teselación, dibujos con y sin
  rasterizado, rangos modificados, barreras, readback y cachés.
- Ampliar los fixtures a variantes con registros y múltiples índices diferentes;
  los actuales verifican exportaciones desde constantes y sus límites.
- Validar un dibujo ordinario completo por el procesador del SDK y presentación
  real; estas pruebas son offscreen y no miden FPS.
- Validar coherencia CPU/GPU, alias y seguimiento de escrituras de memoria con
  páginas de 16 KB bajo carga. El éxito de inicialización no cubre todo eso.
- En la futura fase Forza, registrar qué shaders y rutas utiliza el juego antes
  de afirmar compatibilidad. No se ha medido rendimiento, carreras ni guardados.
