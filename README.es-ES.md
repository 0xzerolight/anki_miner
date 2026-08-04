<h1 align="center">
  <img src="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/anki_miner/gui/resources/icons/anki_miner.svg" height="76" align="absmiddle" alt=""> Anki Miner
</h1>

<p align="center">
<a href="https://pypi.org/project/anki-miner/"><img src="https://img.shields.io/pypi/v/anki-miner.svg" alt="PyPI version"></a>
<a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"></a>
<a href="https://www.gnu.org/licenses/gpl-3.0"><img src="https://img.shields.io/badge/License-GPLv3-blue.svg" alt="License: GPL v3"></a>
<a href="https://github.com/0xzerolight/anki_miner/releases/latest"><img src="https://img.shields.io/github/downloads/0xzerolight/anki_miner/total.svg" alt="GitHub downloads"></a>
<a href="https://github.com/0xzerolight/anki_miner/stargazers"><img src="https://img.shields.io/github/stars/0xzerolight/anki_miner?style=social" alt="GitHub stars"></a>
<a href="https://discord.com/invite/aDtQyZzUVP"><img src="https://img.shields.io/discord/1517634859110240326?logo=discord&logoColor=white&label=Discord&color=5865F2" alt="Discord community"></a>
</p>

<p align="center">
Convierte contenido japonés nativo en tarjetas de vocabulario de Anki.
</p>

<p align="center">
Por favor, deja una ⭐ estrella si Anki Miner te ha ayudado; ayuda a que otros lo encuentren :).
</p>


# <p align="center">Demo de Minería</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demo completa con sonido (MP4)</a></p>

### Ejemplos de tarjetas

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (sonido)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (sonido)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (sonido)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalación

### Requisitos

- **Anki** con el complemento [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (código `2055492159`)
- **ffmpeg** + **libmpv** (solo para vista previa de video) - necesario únicamente al instalar vía pip/pipx, `.deb` o desde el código fuente.

Descarga la versión para tu plataforma desde el [último lanzamiento](https://github.com/0xzerolight/anki_miner/releases/latest):

| Plataforma | Descarga |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (otros) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Excluye la generación local de subtítulos con Whisper y capturas de pantalla AVIF. Para funcionalidad completa: `pipx install "anki-miner[asr]"`.

### Notas para la primera ejecución (versiones no firmadas)

- **macOS**: Gatekeeper bloquea la aplicación. Primero extrae los archivos y luego ejecuta `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: Haz clic en **Más información** -> **Ejecutar de todas formas**.
- **Falso positivo de Windows Defender**: restaura desde el **Historial de protección** o [infórmalo a Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instalar desde PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # o: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Instalar desde el código fuente</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Para la configuración completa de desarrollo, consulta [CONTRIBUTING.md](CONTRIBUTING.md).

</details>

## Pestañas

- **Video** - minera un solo par de video/subtítulo, una carpeta por lotes o URLs de YouTube.
- **Deck Builder** - minera una serie completa en un solo mazo clasificado por frecuencia.
- **Audiobooks** - minera audiolibros, podcasts, radio, canciones (pares de audio + subtítulo/transcripción).
- **Reading** - minera manga (mokuro), novelas (`.epub`, `.txt`; un libro individual o una carpeta completa), archivos de subtítulos independientes o texto japonés pegado.
- **Analytics** - historial de minería, rankings de dificultad, hitos, deshacer.
- **Utilities** - genera subtítulos (Whisper local), ajusta el tiempo de los subtítulos (alass), condensa medios a audio solo de diálogos y rellena campos en tarjetas existentes.
- **Settings** - todo lo configurable.

## Otras Características

- Word Curator - revisa cada palabra candidata antes de crear las tarjetas, con su escena, su página de manga y su entrada de diccionario al lado.
- Filtrado extenso: i+1, límites de frecuencia, lista negra, regex, conjuntos de palabras y más.
- Importación de diccionario Yomitan offline - definiciones, acento tonal (pitch accent), frecuencia - encadenados por prioridad.
- Múltiples listas de frecuencia encadenadas por prioridad.
- Audio de palabras en las tarjetas desde packs de audio locales, JapanesePod101 o Google TTS.
- Audio de frases en las tarjetas de Reading desde Google Translate TTS o Naver Papago (desactivado por defecto).
- Estilizado de glosario por diccionario, al estilo Yomitan.
- Vista previa de video integrada con libmpv - reproduce la escena de cada palabra mientras curas, o ajusta la temporización de los subtítulos con reproducción en vivo.
- Capturas de pantalla animadas (ver ejemplos de tarjetas arriba).
- Perfiles de configuración - guarda configuraciones con nombre y cambia entre ellas desde la cabecera.
- Restyle Mined Cards - vuelve a aplicar tu estilo de tarjeta actual a las tarjetas que ya creaste (menú Tools).

<details>
<summary><strong>Temas integrados (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (light); Frappé, Macchiato, Mocha (dark)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (light), Wave (dark)
- **Rosé Pine** - Dawn (light); Main, Moon (dark)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licencias de temas: [LICENSE-THEMES.md](LICENSE-THEMES.md). 
¿Quieres que añadamos otro tema? Sugierelo en un Issue de GitHub.

</details>

<details>
<summary><strong>Cómo Funciona</strong></summary>

1. **Lee los subtítulos** y divide el japonés en palabras individuales.
2. **Filtra** para obtener palabras de contenido que aún no conozcas, con la opción de revisar la lista tú mismo en el Word Curator.
3. **Toma una captura de pantalla y un clip de audio** del video para cada línea.
4. **Busca definiciones** en tus diccionarios offline configurados, con la opción de recurrir a Jisho online si está habilitado (más lento, limitado por tasa de peticiones).
5. **Envía las tarjetas finalizadas a Anki.**

</details>

## Recursos Recomendados

| Tipo | Recurso | Descarga | Añadir vía |
|------|----------|----------|---------|
| Diccionario | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Add Dictionary… |
| Diccionario | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Add Dictionary… |
| Diccionario | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Generado en el sitio | Add Dictionary… |
| Pitch | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Pitch Accent -> Add pitch source… |
| Pitch | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Pitch Accent -> Add pitch source… |
| Frecuencia | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Frequency -> Add frequency source… |
| Frecuencia | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Frequency -> Add frequency source… |


<details>
<summary><strong>Licencia de JMnedict</strong></summary>

Utiliza conjuntos de palabras de nombres incluidos derivados de [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (proyecto JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Solución de Problemas

| Problema                    | Solución                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Cannot connect to Anki" | Inicia Anki y asegúrate de que AnkiConnect esté instalado.                                  |
| "Deck not found"         | Selecciona un mazo existente en Settings -> Cards & Anki. Los mazos no se crean automáticamente; créalo en Anki primero si necesitas uno nuevo. |
| "Note type not found"    | Configura los nombres de los campos de tu tipo de nota en Settings -> Cards & Anki.               |
| "ffmpeg not found"       | Instala ffmpeg y añádelo al PATH.                                               |
| No se encuentran definiciones | Añade un diccionario de Yomitan en Settings -> Add Dictionary… (recomendado), o activa el respaldo de Jisho (más lento, limitado por tasa de peticiones). |
| El instalador de Windows no abre / advertencia de SmartScreen | Mira las [Notas para la primera ejecución](#notas-para-la-primera-ejecución-versiones-no-firmadas): selecciona **Más información** -> **Ejecutar de todas formas**; restaura los falsos positivos de Defender desde el **Historial de protección**. |
| Instalación limpia sin definiciones | Ejecuta Tools -> Setup Wizard o Tools -> Download Recommended Resources. Para importación manual, mantén el archivo ZIP de Yomitan intacto (no lo descomprimas). |
| Add Dictionary se congela o falla | Anota la última etapa visible y adjunta los logs (ver "¿Dónde están los logs?" abajo). Incluye el nombre, fuente y tamaño del ZIP del diccionario en el reporte. |
| ¿Dónde están los logs?      | Usa Help -> Open Log Folder, o abre `%USERPROFILE%\.anki_miner\anki_miner.log` en Windows o `~/.anki_miner/anki_miner.log` en macOS/Linux. Los logs rotados usan los sufijos `.1` al `.5`. |
| Informar de un error        | Help → Export Diagnostics… crea un ZIP con los logs y los detalles del sistema en la ubicación que elijas. Revísalo antes de subirlo porque contiene rutas y nombres de archivos de tu ordenador. No se sube nada automáticamente. |
| Registro de diagnóstico ampliado | Define `ANKI_MINER_LOG_LEVEL=DEBUG` antes de iniciar Anki Miner para capturar detalles de terceros de yt-dlp, urllib3 y fugashi. El valor predeterminado es `WARNING`; los logs de Anki Miner permanecen en DEBUG. |
| El audio está en el idioma incorrecto  | La herramienta intenta primero las pistas de audio en japonés, luego recurre a la predeterminada.      |
| Subtítulos desincronizados    | Usa el control de desplazamiento (offset) de subtítulos en la GUI (rango ±300 segundos).                 |

## Hoja de Ruta (Roadmap)

Lista de ideas para futuras versiones de Anki Miner. No están en orden de prioridad. Las solicitudes de funciones tienen prioridad.
- Sugiere una función - [Abre un issue](https://github.com/0xzerolight/anki_miner/issues).
- Discute la hoja de ruta - [Discussions](https://github.com/0xzerolight/anki_miner/discussions).

- **Funciones**:
  - [x] Selección de idioma de la interfaz de usuario.
  - [x] Pestaña de creación de subtítulos locales: pestaña opcional para generar subtítulos localmente.
  - [x] Pestaña de lectura: minera manga y libros.
  - [x] Herramienta de relleno (Backfill).
  - [ ] Biblioteca de medios: Expandir la pestaña de Analytics para mostrar la biblioteca de medios local en todas las formas de medios.
  - [ ] Descarga automática de subtítulos.

- **Largo plazo**:
  - [x] Puerto a Android -- https://github.com/0xzerolight/anki_miner_android
  - [ ] Más allá del japonés: minería de otros idiomas.
  - [ ] Extensión de navegador para Anki Miner.


## Contribuciones

Todas las contribuciones de cualquier tipo son bienvenidas.
Si quieres apoyar el proyecto, por favor compártelo con otros que puedan beneficiarse.

- ¿Eres nuevo aquí? Empieza con [CONTRIBUTING.md](CONTRIBUTING.md).
- Descripción general de la arquitectura: [ARCHITECTURE.md](ARCHITECTURE.md).
- Estrategia de pruebas: [TESTING.md](TESTING.md).
- Código de Conducta: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
- Seguridad: [SECURITY.md](SECURITY.md).

Reportes de errores y solicitudes de funciones -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Preguntas generales y discusión -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) o [Discord](https://discord.com/invite/aDtQyZzUVP).

## Agradecimientos Especiales

Sincero agradecimiento a las personas que hicieron contribuciones excepcionales al proyecto:

★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Brillantes sugerencias de funciones, pruebas de nuevos lanzamientos, creación de comunidad

Mira [CONTRIBUTORS.md](CONTRIBUTORS.md) para ver a todos los que han hecho cualquier tipo de contribución al proyecto.


## Licencia

Licencia Pública General de GNU v3.0. Mira [LICENSE](LICENSE).
