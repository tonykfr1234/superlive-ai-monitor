<p align="center">
  <a href="README.md">English</a> · <a href="README.zh-CN.md">简体中文</a> · <a href="README.zh-TW.md">繁體中文</a> · <a href="README.ja.md">日本語</a> · <a href="README.ko.md">한국어</a><br>
  <a href="README.de.md">Deutsch</a> · <strong>Español</strong> · <a href="README.fr.md">Français</a> · <a href="README.pt-BR.md">Português (Brasil)</a> · <a href="README.vi.md">Tiếng Việt</a> · <a href="README.id.md">Bahasa Indonesia</a> · <a href="README.it.md">Italiano</a>
</p>

<p align="center"><img src="assets/superlive-ai.png" width="112" alt="SuperLive AI icon"></p>
<h1 align="center">SuperLive AI Monitor</h1>
<p align="center">Selecciona un polígono. Graba la actividad de las personas. Recibe los vídeos en Telegram.</p>
<p align="center"> <img src="https://img.shields.io/badge/platform-Windows-0078D6" alt="Windows"> <img src="https://img.shields.io/badge/Python-3.12-3776AB" alt="Python 3.12"> <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License"></a> </p>
<p align="center"><a href="#quick-start">Inicio rápido</a> · <a href="#workflow">Funcionamiento</a> · <a href="#features">Funciones</a> · <a href="#demo">Demostración</a> · <a href="#faq">Preguntas frecuentes</a> · <a href="#documentation">Documentación</a></p>

SuperLive AI Monitor añade grabación por eventos a la imagen de SuperLive visible en MuMu en Windows. Selecciona un área poligonal: YOLO detecta personas en ella y los vídeos terminados se envían por Telegram. No hace falta una dirección RTSP de la cámara.

Es un proyecto comunitario no oficial, sin relación con los proveedores de SuperLive o MuMu. Los doce idiomas del README corresponden a la documentación; la interfaz del programa utiliza actualmente chino tradicional.

<a id="quick-start"></a>
## Inicio rápido

1. Instala MuMu y SuperLive, inicia sesión y abre la vista en directo que quieras vigilar. Mantén iniciada la sesión de Windows.
2. Clona o descarga este repositorio. Ejecuta `installer\INSTALL_FROM_REPO.bat` o el comando siguiente.
3. Configura tu propio bot y chat localmente con `installer\SET_TELEGRAM.bat`. El repositorio no incluye credenciales.
4. Ejecuta `installer\VERIFY_ALL.bat` con la vista en directo abierta.
5. Inicia `%USERPROFILE%\Downloads\SuperLive_AI\Start_AI_Monitor.bat`; utiliza `Stop_AI_Monitor.bat` para detenerlo.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer\INSTALL_FROM_REPO.ps1
```

**¿Ya está instalado?** Ejecuta `installer\UPDATE_FROM_REPO.bat`. La actualización crea una copia de seguridad y detiene los procesos, conserva la región y los ajustes locales de Telegram y almacenamiento, y deja la vigilancia detenida. Comprueba la imagen y reiníciala manualmente. No se configura el inicio automático de Windows, ninguna tarea programada ni ningún servicio.

<a id="workflow"></a>
## Funcionamiento

```text
Nueva actividad de personas dentro del polígono
  → iniciar la grabación; bloquear nuevos envíos automáticos a Telegram durante 120 segundos
  → seguir detectando durante toda la grabación
  → movimiento desde el segundo 120 y antes del 300: finalizar el vídeo e iniciar otro de inmediato
  → cada vídeo nuevo abre otro periodo de 120 segundos sin envíos a Telegram
  → finalizar también tras 120 segundos sin movimiento o al alcanzar 300 segundos
  → añadir el vídeo terminado a la cola de Telegram
  → seguir vigilando; volver a grabar si hay movimiento nuevo o entra otra persona
```

Los vídeos normales duran **entre 120 y 300 segundos**. Una persona observada por primera vez al iniciar el programa cuenta como actividad nueva. Tanto la ausencia como la inmovilidad cuentan como falta de movimiento; cada movimiento detectado reinicia el plazo de inactividad. Una vez transcurridos los primeros 120 segundos, el movimiento también provoca el cambio inmediato al siguiente vídeo. Una persona que permanece quieta no inicia otro vídeo inmediatamente después de terminar el anterior.

No se envía una foto de Telegram al iniciar: los únicos envíos automáticos son vídeos terminados. La pausa de envío de cada vídeo nuevo también bloquea los vídeos anteriores y sus reintentos. Los cambios de vídeo repetidos pueden retrasar toda la cola. Se bloquean nuevas llamadas de envío; una transferencia ya iniciada no puede retirarse y podría llegar durante la pausa.

La selección de una región o la falta de resultados válidos de captura o detección interrumpe la observación. Ese tiempo desconocido no cuenta como inactividad: al recuperarse, comienza un nuevo intervalo de observación.

<a id="features"></a>
## Funciones

| Función | Comportamiento |
| --- | --- |
| Región de detección | Polígonos convexos o cóncavos; compatibilidad con los rectángulos anteriores |
| Inclusión de personas | El cuadro de la persona debe solaparse con la región en una superficie positiva; tocar el borde no basta |
| Imagen de cámara | Excluye el título y los menús de MuMu; conserva las marcas de tiempo y los nombres de canal de la imagen |
| Grabación | Normalmente 2–5 minutos; desde el segundo 120, un movimiento inicia otro vídeo de inmediato; finaliza también tras 120 segundos sin movimiento |
| Avisos y envío | Avisos locales de Windows; solo vídeos H.264 MP4 terminados por Telegram; todos los envíos esperan durante los primeros 120 segundos de cada vídeo |
| Operación y almacenamiento | Supervisores independientes y reintentos de captura; valores predeterminados de 10 FPS y `C:\SuperLive_Recordings` |

Cuando la unidad de grabación alcanza un 80 % de uso, se eliminan los vídeos antiguos aptos hasta bajar al 78 %. Se protegen la grabación activa y los archivos pendientes de envío. El vídeo conserva toda la imagen recortada de la cámara; no se recorta con la forma del polígono.

<a id="controls"></a>
## Seleccionar el polígono

| Tecla o acción | Resultado |
| --- | --- |
| R / Seleccionar región | Comenzar una selección |
| Clic izquierdo | Añadir al menos tres vértices distintos en orden alrededor del borde |
| Clic derecho / Retroceso | Quitar el último vértice |
| Enter / Guardar | Cerrar y guardar un polígono válido sin lados cruzados |
| Esc / Cancelar | Conservar la región anterior |
| C / Imagen completa | Eliminar la restricción de región |
| Q / Detener | Detener la vigilancia y finalizar el vídeo en curso |

Los atajos requieren que la ventana de vista previa tenga el foco. Las coordenadas se guardan respecto al contenido de la cámara. Si una región antigua no puede adaptarse con seguridad al recorte, selecciónala de nuevo o elige expresamente la imagen completa.

<a id="demo"></a>
## Demostración

Estos ejemplos **silenciosos de 36 segundos** utilizan el renderizador real de la vista previa con una escena sintética, cuadros de personas programados y estados de grabación simulados. Los rótulos en chino e inglés muestran la pausa de envío, el cambio de vídeo y la espera de nueva actividad. No son grabaciones reales ni demuestran la precisión de YOLO. Los intervalos de 120 segundos se acortan para la demostración.

![Demostración sintética del polígono y la grabación por actividad](docs/media/superlive-demo.gif)

[Abrir o descargar la demostración en MP4](docs/media/superlive-demo.mp4)

| Seleccionar la región | Grabar la actividad | Esperar un nuevo movimiento |
| --- | --- | --- |
| ![Selección de vértices](docs/media/polygon-selection.png) | ![Grabación de actividad](docs/media/activity-recording.png) | ![Vigilancia tras la inactividad](docs/media/inactivity-rearm.png) |

<a id="faq"></a>
## Preguntas frecuentes

**¿Por qué no vuelve a grabar a una persona quieta?** Para iniciar otro vídeo debe haber un nuevo movimiento o una persona que entre en la región. Al arrancar el programa, la primera observación sí cuenta como actividad. No se envía una alerta de inicio por Telegram.

**¿Cómo se determina el movimiento?** Se analizan cambios de posición o tamaño del cuadro de la persona. Se filtran pequeñas oscilaciones y se acumulan desplazamientos lentos. No es reconocimiento de posturas: un movimiento que no cambie el cuadro puede pasar inadvertido. Los movimientos de cámara, las oclusiones y los errores de detección también pueden afectar al resultado.

**¿Todos los vídeos duran cinco minutos?** No. Normalmente duran entre 2 y 5 minutos. Un movimiento desde el segundo 120 y antes del 300 finaliza el vídeo e inicia otro de inmediato; 120 segundos sin movimiento también lo finalizan. Una parada manual o un fallo pueden producir un fragmento más corto, que se conserva localmente. No se inicia su envío antes de que termine la pausa vigente.

**¿Funciona en VMware y si Telegram no responde?** Se admite inferencia por CPU; la aceleración gráfica de VMware no proporciona CUDA. Los envíos de vídeo fallidos permanecen en la cola y se reintentan.

<a id="documentation"></a>
## Documentación y seguridad

[Guía en inglés](docs/USER_GUIDE.md) · [Guía en chino tradicional](docs/USER_GUIDE.zh-TW.md) · [Arquitectura](docs/ARCHITECTURE.md) · [Diseño de grabación](docs/RECORDING.md) · [Cambios](CHANGELOG.md) · [Seguridad](SECURITY.md)

El código está en `src/`, las plantillas públicas de configuración en `config/` y las herramientas de instalación en `installer/`. Comprueba la cámara real y el envío por Telegram en tu entorno; las pruebas sin conexión no verifican esa configuración.

No publiques tokens reales, identificadores de chat, ajustes locales, registros, imágenes de cámara ni copias de seguridad privadas. Antes de subir cambios, ejecuta `python scripts/security_scan.py .`.

## Licencia

[Licencia MIT](LICENSE).

## Daily CSV · 23:00 Asia/Taipei

A las 23:00 (Taiwán), envía y guarda junto a los vídeos un CSV del día desde las 00:00 inclusive hasta las 23:00 exclusive. El CSV diario está exento del silencio de grabación. Excluye eventos posteriores y no envía suplementos a medianoche. Informa incluso con cero eventos y reintenta tras fallos o reinicios.
