# Port experimental Asahi: fase 3

## Estado al finalizar

Se extrajo la copia local de la ISO del usuario, se validaron sus tres
ejecutables con los comprobadores del proyecto, se generó el código C++ del juego
y se compiló el juego diagnóstico completo para **Linux ARM64** (ejecutable y dos
módulos). El enlace se verificó de forma estática: arquitectura, dependencias,
runpath y correspondencia byte a byte con el SDK parcheado del laboratorio.

**El juego ya arranca y muestra la primera escena 3D y el coche con HUD.** Una
entrada automática de acelerador produjo movimiento y una captura a 29 mph.
Se corrigió una discrepancia de 4 KiB en la traducción del heap físico en Linux
con páginas de 16 KiB; no hace falta omitir el lock HSI ni las esperas GPU.
La integración final del SDK volvió a pasar 9/9 con validación 0/0.

**720p/30 FPS sostenidos no está alcanzado:** sin validación, la ventana de
aceleración dio 20,96 FPS de media y p95 de 66,85 ms. Esto demuestra arranque,
render y respuesta a entrada en el tramo probado, no compatibilidad completa,
una carrera completada ni estabilidad prolongada.

La extracción, el código generado y los artefactos de compilación están ignorados
por Git. El SDK sigue en `bd833a2ab69d305f99ddf57418fc38ffec8af93d` con los 116
parches del proyecto; no se modificaron Mesa, Honeykrisp, el kernel, el loader,
el ICD ni la configuración Vulkan del sistema.

## Copia local y extracción

Fuente, usada solo en lectura:

```text
/ruta/privada/Forza Horizon (USA) (En,Ja,Fr,De,Es,It,Nl,Pt,Sv,No,Da,Fi,Zh,Ko,Pl,Ru,Cs,Hu).iso
```

| Dato | Valor |
| --- | --- |
| Formato | XGD3 |
| Bytes de la ISO | 8.738.846.720 |
| SHA-256 antes y después | `83dfc7d0b58b804232385de6f794d9acb92b89a37cf13da14b824b286c824490` |
| Destino | `extracted/game` (nuevo; se rechaza si ya existe) |
| Archivos extraídos | 2.400 (sin la actualización de sistema), 7.221.319.555 bytes |
| Comprobación | `compatible: true` |

Los validadores del proyecto reconocieron esta impresión USA:

> This is a different printing of the game (version 0.0.0.10, media id
> 2DC7007B): its code is the same as the supported one.

| Ejecutable | SHA-256 |
| --- | --- |
| `default.xex` | `db40df605ade49a612b35a7a24c38f6004bcb17a88ed6b48288de16df9e3987c` |
| `XMediaFacade_default.xex` | `ed285beefeafcdc90cf64d6085d19c874e3f1e15072c12cdf9ba2237c88e2ce5` |
| `SpeechFacade_default.xex` | `d54f6aae6ae2fd6da9b8c37d48f0a6bb295caa18ad82d0d9df66026141a41594` |

La extracción es reproducible con `tools/asahi-sdk-lab/extract_iso.py`, que usa
el lector y el instalador existentes, escribe primero en un directorio temporal,
comprueba los ejecutables y solo entonces publica `extracted/game`. Evidencia:
`logs/20261025T001400.366150Z-phase3-extract-and-validate/artifacts/extraction.json`.

## Generación de código

`build.py game` ejecutó `rexglue 0.10.0.24-dev.gbd833a2` con el manifiesto base
(`FORZA_TITLE_UPDATE=0`) y `FORZA_CPU_TARGET=baseline`.

| Dato | Valor |
| --- | --- |
| Título / Media ID / versión | `4D5309C9` / `2DC7007B` / `0.0.0.10` |
| Módulos | `default.xex`, `XMediaFacade_default.xex`, `SpeechFacade_default.xex` |
| Resultado | 617 archivos escritos, 0 sin cambios, 0 borrados, 3 módulos al día |
| Salida | 623 archivos, 309 MB, 304 `.cpp` (`forza_recomp.0..233` más registro e init) |
| Tiempo | 151,3 s `default.xex`; 152,8 s en total |

Mensajes informativos del generador, sin error: `Function 0x8241A370 is 2532259
bytes, exceeds max_file_size_bytes`, dos ramas `bdz` fuera de función y
`BaseHeap::Release failed because address is not a region start`. Son avisos del
propio `rexglue`, presentes también en ejecuciones sin cambios.

Evidencia: `logs/20261025T001444.758244Z-asahi-game-configure`.

## Compilación y enlace

CMake 4.3, Ninja 1.13.2 y Clang 22.1.8 (aarch64). Configuración y compilación
completas desde un directorio vacío, sin advertencias ni errores:

| Artefacto | Bytes | SHA-256 |
| --- | --- | --- |
| `forza` | 139.885.416 | `988b5ce7984ee6c39894823407d9ade5aa0c57a0607bab3b0bece36d8fbb382e` |
| `forza_XMediaFacade_default` | 18.384.352 | `14b2981aaa010c6288f81628133bb70bde199a6f885803396396f4a1a624928e` |
| `forza_SpeechFacade_default` | 13.894.504 | `5b681add398eb66eadda6c362fc823806ed0f2e6e6177646d49b9965403adc4c` |
| `librexruntime.so` | 13.151.896 | `77715816f71cb96522dcd1168a9a386d5a3c661d2e277adc260638efbc1e4dd3` |
| `librexgpu-xenos.so` | 6.660.824 | `57743b967dd6f62f1f4df89cb91c0977ff17d9138c02c62fdced83f503a1e38a` |

Esta tabla conserva la **compilación inicial**, anterior a la corrección de
traducción física. Los hashes actuales se indican más adelante.

La configuración tardó 153,5 s y la compilación 217,3 s con tres trabajos. La
segunda pasada de Ninja (necesaria porque el generador escribe fuentes de los
módulos durante la compilación) solo repitió el generador sin cambios (0
archivos escritos) y no recompiló ni enlazó nada. El runtime y el plugin
copiados junto al ejecutable son **idénticos byte a byte** a los del prefijo del
SDK del laboratorio, es decir, incluyen el traductor parcheado de la fase 2. El
plugin contiene el cvar `vulkan_memexport_compute` y su descripción
experimental; el binario los carga desde su propio directorio.

Evidencias: `logs/20261025T001718.283824Z-asahi-game-build`,
`logs/20261025T002055.617305Z-asahi-game-build-second-pass` y
`logs/20261025T002424.*-asahi-game-stage-*`.

### Verificación estática

| Comprobación | Resultado |
| --- | --- |
| `forza` | ELF64 little-endian, `EXEC`, máquina **AArch64**, entrada `0x41f940` |
| Alineación de `LOAD` | `0x10000`, compatible con páginas de 16 KiB |
| `GNU_STACK` | `RW`, no ejecutable; `GNU_RELRO` presente |
| Runpath | `$ORIGIN` |
| `ldd` | Todas las dependencias resueltas; `librexruntime.so` sale del directorio del juego |
| Módulos | `forza_XMediaFacade_default` y `forza_SpeechFacade_default`: DSO AArch64 |
| Runtime/plugin | SHA-256 iguales a `install/lib`; plugin con la ruta compute de la fase 2 |
| Build incremental | `ninja -n` sobreestima el trabajo (véase abajo); una ejecución real solo repite el generador sin cambios (0,1 s) |

Evidencias: `logs/20261025T002444.541897Z-phase3-static-elf`,
`logs/20261025T002428.316225Z-phase3-link-dependencies-staged`,
`logs/20261025T002445.476653Z-phase3-static-sha256` y
`logs/20261025T002609.717089Z-phase3-static-plugin-memexport-marker`.

`ninja -n` anuncia 241 pasos porque `rexglue` actualiza `forza_manifest.toml`
después de tocar el sello `codegen.build.stamp`; el sello queda una fracción de
milisegundo más antiguo que su entrada. En una ejecución real, el generador
detecta que no hay cambios (0,1 s) y Ninja no recompila nada. Es un
comportamiento del generador, no del parche del laboratorio; no afecta a los
artefactos. Evidencia: `logs/20261025T002500.640800Z-phase3-ninja-explain`.

## Revalidación GPU

La regresión sintética comprobó la ruta de renderizado y memexport del SDK.
Estos resultados se limitan a los casos de prueba indicados; no establecen
estabilidad general durante carreras o sesiones prolongadas.

| Prueba | Resultado |
| --- | --- |
| Integración completa | 7 variantes memexport + raster + espejo CPU, 9/9 PASS |
| Validación Vulkan | 0 errores, 0 avisos |
| Raster | 64 píxeles rojos, primero en (4,4), target 1280x2048 |
| GPU | Apple M2 Pro (G14S B1), Honeykrisp, Mesa 26.2.5 |
| Kernel | Sin errores GPU/pantalla ni suspensión |

Evidencias: `logs/20261025T014809.015711Z-phase3-gpu-integration-final` y
`logs/20261025T014743.390041Z-phase3-gpu-raster-blend-fixed`.

La primera lectura raster salió negra por dos defectos **del fixture**, no del
SDK: el scissor era de 16x16 (el punto quedaba fuera si el viewport se invertía)
y `RB_BLENDCONTROL0` conservaba el valor cero (`src=0, dst=0`), que escribe negro.
Se amplió el scissor al target, se leyó el target completo y se fijó el blend en
One/Zero para el punto de prueba. En esa revalidación el plugin del juego no cambió: era
`57743b...` de `install/lib`; el plugin del árbol de compilación (`82fbef...`)
solo añade los cambios de fixture del laboratorio.

Después de corregir la traducción de memoria y retirar el log temporal de
`WAIT_REG_MEM`, la integración volvió a pasar **9/9**, con **0 errores y 0
avisos**, en `logs/20261025T050822.190743Z-phase3-gpu-after-physical-alias`.

## Arranque: causa de los bloqueos y corrección

Los primeros lanzamientos crearon dispositivo y ventana pero presentaban una
imagen negra con 0 FPS. La actividad Discord de «menú principal» se registra
antes de renderizar y no era prueba de que el menú estuviera visible.

1. `20261025T015401.274473Z-asahi-game-720p30`: cierre normal a 180 s,
   render thread en `RtlEnterCriticalSection(FFCAB000)`, count 1, owner 0.
2. `20261025T020151.618090Z-asahi-game-sdk-cs`: con
   `FORZA_SDK_CRITICAL_SECTIONS=1` también quedaba bloqueado.
3. `20261025T021012.641926Z-asahi-game-short-movies`: acortar los vídeos
   tampoco resolvió el bloqueo.
4. `20261025T021246.643765Z-asahi-game-hsio-bypass`: omitir el lock permitió
   enviar comandos, pero el procesador quedó en `WAIT_REG_MEM`.
5. `20261025T043404.342436Z-asahi-game-wait-fields`: el log temporal mostró
   **memoria física `0x1FCA4000`, valor 4, referencia 0, máscara `FFFFFFFF`,
   info `0x13`, espera `0x100`**. No era una espera de registro Vulkan.

La captura `logs/20261025T043650.720016Z-asahi-game-alias-before/guest-memory.txt`
demostró la diferencia: el acceso directo a `base + 0xFFCAB000` veía ceros,
mientras que `base + 0xFFCAC000` y el alias físico `physical_base + 0x1FCAC000`
veían el lock correctamente inicializado (`01280000 ... FFFFFFFF ...`).

`Memory::MapViews` redondea el offset del archivo del heap `0xE...` a la
granularidad del host. `PhysicalHeap` y `Memory::TranslateVirtual` ya compensaban
con `0x1000` cuando la granularidad supera 4 KiB. Sin embargo,
`PhysicalHostOffset` y las plantillas de código generado suponían que **todo
Linux usa páginas de 4 KiB**, de modo que el juego y los hooks leían/escribían
4 KiB antes que el runtime y el procesador de comandos.

La corrección exportada en `tools/asahi-sdk-lab/linux-16k-sdk.patch`:

- Consulta y cachea la granularidad real en Linux; añade `0x1000` solo para
  direcciones `>= 0xE0000000` cuando corresponde.
- Hace que las plantillas de juego y de pruebas usen el mismo helper que
  `GuestPtr`, evitando dos reglas de traducción distintas.
- Conserva el caso sin offset en Linux de 4 KiB y las reglas existentes de
  Windows/macOS; esas otras plataformas no se han ejecutado aquí.

Se reconstruyeron SDK, código generado, juego y módulos. El bypass
`FORZA_HSIO_LOCK=bypass` se **retiró** de `critical_section.cpp`, que vuelve a
su contenido original. También se retiró el log temporal de `WAIT_REG_MEM`.
`tools/asahi-sdk-lab/memory_alias.cpp` comprueba, sin GPU, que una palabra
escrita por el alias físico se ve desde el runtime y desde `GuestPtr`, y que
la escritura inversa también llega al alias físico. Pasó con página 16384 y
offset 4096: `logs/20261025T043924.420548Z-phase3-physical-alias`.

## Primera escena y rendimiento medido

| Ejecución | Resultado observado |
| --- | --- |
| `20261025T044938.881452Z-asahi-game-physical-offset-fixed` | Sin bypass: pipelines y presentación ~60 FPS, todavía imagen negra de arranque |
| `20261025T045056.028624Z-asahi-game-original-autopress` | Extracción original: primera escena 3D visible, captura del ciervo a 85 s |
| `20261025T045433.654025Z-asahi-game-first-scene-180` | Extracción original: coche, carretera, minimapa y velocímetro visibles a 175 s |
| `20261025T045855.942272Z-asahi-game-short-perf` | Sin validación: primera escena y coche, ~19–30 FPS según el tramo |
| `20261025T050241.620498Z-asahi-game-driving-perf` | Sin validación: acelerador automático, captura a 85 s con 29 mph, CSV por frame |
| `20261025T051015.362724Z-asahi-game-final-staged-validation` | Runtime/plugin finales copiados desde install; cierre normal y escena con validación |
| `20261025T051210.262452Z-asahi-game-reproducible-probe` | Helper publicado verificado: 35 s, sin aborto por memoria |

Las capturas están en `artifacts/screen-*.png` de cada ejecución. Todas
terminaron mediante su límite interno con SIGINT y código 0, sin fallo del
kernel ni dispositivo perdido. El supervisor no agotó su propio timeout.

En los lanzamientos con validación se registraron **0 errores Vulkan** y
**10 líneas de aviso** de un mismo ID (`1183284798`,
`Undefined-Value-ShaderOutputNotConsumed-DynamicRendering`): el fragment shader
escribe Location 0 en un draw sin attachment de color. La décima línea es el
aviso de límite de duplicados; no son diez tipos distintos de fallo. No se
silenciaron esos mensajes. El supervisor ahora recoge también `--log_file`
como `guest.log` y cuenta `Vulkan Warning (`; antes solo miraba stdout y podía
omitir avisos escritos únicamente al archivo del juego.

Los perfiles de rendimiento usan `resolution=720p`, `resolution_scale=1`,
`frame_rate=30`, compute habilitado, compilación de shaders síncrona y caché
reutilizada, **sin validación ni sincronización de validación**. La ventana del
escritorio/swapchain es 2560x1080; ese tamaño de presentación no es el objetivo
interno de resolución.

CSV de `asahi-game-driving-perf`, con 2922 filas completas y una fila final
incompleta descartada por el cierre inmediato de la aplicación:

| Ventana | Frames | FPS medios | p95 frame time | CPU del command processor | Espera GPU |
| --- | --- | --- | --- | --- | --- |
| 20–60 s, secuencia inicial | 1014 | 25,35 | 50,89 ms | 11,59 ms/frame | 10,08 ms/frame |
| 60–80 s, coche sin acelerar | 407 | 20,36 | 66,77 ms | 16,74 ms/frame | 17,73 ms/frame |
| 80–110 s, acelerador | 629 | 20,96 | 66,85 ms | 16,68 ms/frame | 16,06 ms/frame |

En 60–80 s no hubo tiempo de compilación de pipelines, por lo que la falta
de 30 FPS en ese tramo no se explica solo por caché fría. Se ejecutaron unos
3170 draws/frame. Las esperas son contadores de CPU; no equivalen a medidas
de tiempo GPU por timestamp ni deben sumarse a ciegas. Se conserva el CSV
para perfilar antes de elegir una optimización.

No apareció `ASAHI memexport compute: shader=...` en los logs del juego de
estos tramos: compute estaba habilitado, pero no se ha demostrado que el
juego necesitara un draw memexport real aquí. Su cobertura sigue siendo la
batería de fixtures, no una afirmación sobre todos los shaders del juego.

### Vídeos de prueba y memoria

`extracted/linux-test-game` es un clon con hardlinks de la extracción. Solo se
reemplazaron en el clon cinco splash movies por versiones de 0,05 s:
`Dolby_Corona_Intro`, `Forza_Intro`, `Forza_Tone`, `MSGS_Logo` y
`T10_MS_Combined`. El marcador es `.forza-linux-short-intros`; los originales
permanecen intactos. Esto reduce la espera de arranque, no corrige el bloqueo.
Las pruebas con la extracción original también alcanzaron la escena 3D.

Se continuó con la excepción monitorizada ya usada en esta sesión al guard
de 10 GiB: suelo de emergencia de 400 MiB y cierre SIGINT si se alcanza. El
mínimo observado en la medición con acelerador fue 3,94 GiB; no hubo aborto.
Los cierres inmediatos dejan backing files `xenia_memory_*` en `/dev/shm`.
Se retiraron once huérfanos de estas pruebas, sin juego activo y comprobando
referencias de procesos, recuperando 2,53 GiB. Esta fuga de nombres/backing al
salir sigue pendiente; no se cambió globalmente la gestión de memoria del SDK.

### Artefactos actuales

Tras retirar diagnósticos y copiar de nuevo ambas bibliotecas:

| Artefacto | SHA-256 |
| --- | --- |
| `forza` | `641b1f17ba91437e84a56dc36b13b5cb39b21a22dcaef80eae9e13e72a18df62` |
| `forza_XMediaFacade_default` | `67a4cd85d094998da9596e71fc286a4748dbe30f9271b64e907626676dc0f8b8` |
| `forza_SpeechFacade_default` | `cc664795534bff21f673ca7e28204105fea98fe03336499c1958b65c484e2a85` |
| `librexruntime.so` | `b108b25d056da3b1cf582962b8793a886bfa1a01fb23c133280118c9cf7c5bd1` |
| `librexgpu-xenos.so` | `a96a9b61aed3844abe943262aea4314f1151618efa82e100f715ed5b38ac9e0c` |

Ambas bibliotecas coinciden byte a byte con `install/lib`. La medición CSV se
obtuvo antes del último restaging del runtime (hash entonces `249937...`);
el último restaging se volvió a probar con validación. Los hashes iniciales
de la sección de compilación ya no describen estos binarios.

## Reproducción

```sh
python3 -B tools/asahi_sdk_lab.py --name extract --seconds 900 -- \
  python3 -B tools/asahi-sdk-lab/extract_iso.py /ruta/a/la/iso.iso

python3 -B tools/asahi-sdk-lab/build.py game --jobs 3

# Regresión de alias físicos, sin GPU:
python3 -B tools/asahi_sdk_lab.py --name physical-alias --seconds 45 -- \
  forza-recomp/out/asahi-sdk-lab/sdk/out/linux-arm64/Release/asahi_memory_alias

# Prueba con validación, vídeos originales y cierre a 180 s:
python3 -B tools/asahi-sdk-lab/play_probe.py --name asahi-validation \
  --seconds 180 --autopress 3:A,7:START,11:A,14:A,18:A,22:A

# Medición con el clon local ya preparado y acelerador a partir de 80 s:
python3 -B tools/asahi-sdk-lab/play_probe.py --name asahi-performance \
  --seconds 120 --no-validation --game-root extracted/linux-test-game \
  --autopress 3:A,7:START,11:A,14:A,18:A,22:A,40:A,60:A,80:RT:30

python3 -B tools/asahi-sdk-lab/analyse_frames.py \
  forza-recomp/out/asahi-sdk-lab/logs/<ejecucion>/artifacts/frame-cost.csv

python3 -B tools/asahi-sdk-lab/snapshot.py --phase2 --publish
```

`build.py game` comprueba los archivos comerciales, ejecuta el generador,
compila `forza` en dos pasadas y copia `librexruntime.so` y
`librexgpu-xenos.so` desde el prefijo del SDK junto al ejecutable. El modo
`play` ya no está bloqueado (el marcador de la fase 2 está archivado); aplica un
límite interno de 180 s con SIGINT para que el supervisor no lo confunda con un
cuelgue. Ya se ejecutó; no es un benchmark sin validación. `play_probe.py`
añade monitor de memoria, copia CSV y capturas si `grim` está disponible, y
captura pilas si `eu-stack` está disponible. No prepara ni descarga archivos
comerciales. Su suelo de 400 MiB es una protección de emergencia de la
excepción monitorizada, no el cumplimiento del guard previo de 10 GiB.

Para reconstruir desde un SDK nuevo, aplicar primero los 116 parches del
proyecto, después `experimental-sdk.patch`, `memexport-compute-sdk.patch` y
`linux-16k-sdk.patch` de `tools/asahi-sdk-lab`, configurar/instalar con
`build.py sdk` y reconstruir con `build.py game`. No ejecutar el preparador
canónico sobre este árbol parcheado. `snapshot.py` publica por separado el
parche de memoria y conserva el combinado en `combined-sdk.patch`, además
de hashes del juego, módulos, bibliotecas, capturas y CSV.

La aplicación secuencial de los 116 parches del proyecto y los tres parches
publicados se comprobó en un índice Git temporal, sin cambiar el checkout ni
crear commits: `logs/20261025T051617.647218Z-phase3-patch-reproduction`.
El resumen CSV reproducible está en
`logs/20261025T051537.770827Z-phase3-driving-csv-summary/stdout.log`.

Resultado: `forza-recomp/out/asahi-sdk-lab/game-base/forza`, con los dos módulos,
el runtime y el plugin en el mismo directorio.

## Reversión

Borrar los artefactos privados de `extracted/linux-test-game`,
`forza-recomp/generated` y `forza-recomp/out/asahi-sdk-lab/game-base` si ya no
se necesitan. Para retirar solo la corrección de memoria, aplicar a la inversa
`tools/asahi-sdk-lab/linux-16k-sdk.patch` en el SDK privado y reconstruir SDK,
código generado y juego; retirar solo el parche sin reconstruir no revierte
los binarios. Los originales comerciales no son necesarios para la reversión.
El comentario de `forza-recomp/src/platform/guest_memory.h` describe ahora
también los hosts de 16 KiB. `critical_section.cpp` no conserva el bypass.

## Pendiente

1. Optimizar para 720p/30 sostenidos: el tramo con acelerador medido da 20,96 FPS,
   no 30. Perfilar CPU/submisiones y espera GPU antes de cambiar comportamientos.
2. Completar conducción/carrera y pruebas prolongadas; la respuesta al acelerador
   y las capturas actuales solo cubren la escena inicial.
3. Investigar el aviso de salida de fragment shader sin attachment de color y
   verificar memexport compute cuando aparezca un shader real que lo use.
4. Corregir la retención de backing files al cierre inmediato del juego.
