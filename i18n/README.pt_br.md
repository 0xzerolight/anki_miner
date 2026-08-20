<!-- i18n-source: README.md sha256:bda48395da8dad23 -->

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

<!-- i18n-nav:start -->
<p align="center">
<a href="../README.md">English</a> ·
<a href="README.ja.md">日本語</a> ·
<a href="README.ru.md">Русский</a> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<b>Português (Brasil)</b> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a>
</p>
<!-- i18n-nav:end -->

<p align="center">
Transforme conteúdo japonês nativo em cartões de vocabulário do Anki.
</p>

<p align="center">
Também no Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner for Android</a>.
</p>

<p align="center">
Por favor, deixe uma ⭐ estrela se o Anki Miner ajudou você - isso ajuda outras pessoas a encontrá-lo :).
</p>


# <p align="center">Demonstração de Mineração</p>

![Anki Miner Showcase](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demonstração completa com som (MP4)</a></p>

### Exemplos de cartões

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (som)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (som)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (som)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalação

### Requisitos

- **Anki** com o complemento [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (código `2055492159`)
- **ffmpeg** + **libmpv** (apenas para pré-visualização de vídeo) - necessário somente ao instalar via pip/pipx ou a partir do código-fonte.

Pegue o download para sua plataforma na [versão mais recente](https://github.com/0xzerolight/anki_miner/releases/latest):

| Plataforma | Download |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon / M1-M4) | `AnkiMiner-macOS-arm64.tar.gz` |
| macOS (Intel) | `AnkiMiner-macOS-x86_64.tar.gz` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (outro) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Exclui a geração local de legendas com Whisper e capturas de tela em AVIF. Para funcionalidade completa: `pipx install "anki-miner[asr]"`.

### Notas de primeira execução (compilações não assinadas)

- **macOS**: o Gatekeeper bloqueia o aplicativo. Extraia primeiro e depois `xattr -dr com.apple.quarantine AnkiMiner/`
- **Windows SmartScreen**: **Mais informações** -> **Executar assim mesmo**.
- **Falso positivo do Windows Defender**: restaure pelo **Histórico de proteção** ou [reporte à Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instalar via PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

</details>

<details>
<summary><strong>Instalar a partir do código-fonte</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Para a configuração completa de desenvolvimento, veja [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Abas

- **Vídeo** - minere um único par de vídeo/legenda, uma pasta em lote ou URLs do YouTube.
- **Construtor de Baralho** - minere uma série inteira em um único baralho ordenado por frequência.
- **Audiolivros** - minere audiolivros, podcasts, rádio, músicas (pares de áudio + legenda/transcrição).
- **Leitura** - minere mangás (mokuro), romances (`.epub`, `.txt`; um livro único ou uma pasta inteira), arquivos de legenda avulsos ou texto em japonês colado.
- **Análises** - histórico de mineração, classificações de dificuldade, marcos.
- **Utilitários** - gerar legendas (Whisper local), reajustar o tempo de legendas (ffsubsync/alass), condensar mídia em áudio só com diálogos, copiar a parte que vale a pena aprender de um baralho pronto para um novo, e preencher retroativamente campos em cartões existentes.
- **Configurações** - tudo que é configurável.

## Outros Recursos

- Curador de Palavras - revise cada palavra candidata antes de os cartões serem criados, com a cena, a página do mangá e a entrada do dicionário lado a lado.
- Desfazer uma execução - exclua as notas que uma execução acabou de criar, direto da caixa de diálogo de resultados.
- Filtragem extensa: i+1, faixa de posição de frequência, blacklist, regex, wordsets e muito mais.
- Importação de dicionário Yomitan offline - definições, acento tonal, frequência - encadeados por prioridade.
- Múltiplas listas de frequência encadeadas por prioridade.
- Áudio de palavra nos cartões a partir de pacotes de áudio locais, JapanesePod101 ou Google TTS.
- Áudio de frase nos cartões de Leitura a partir do Google Translate TTS ou Naver Papago (desativado por padrão).
- Estilização de glossário por dicionário, no estilo Yomitan.
- Pré-visualização de vídeo embutida com libmpv - reproduza a cena de uma palavra durante a curadoria, ou ajuste o tempo da legenda com reprodução ao vivo.
- Capturas de tela animadas (veja os exemplos de cartões acima).
- Perfis de configurações - salve configurações nomeadas e alterne entre elas pelo cabeçalho.
- Reestilizar Cartões Minerados - reaplique o estilo atual dos seus cartões aos cartões que você já criou (menu Ferramentas).

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

Licenças dos temas: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Quer sugerir outro tema? Abra uma issue no GitHub.

</details>

<details>
<summary><strong>Como Funciona</strong></summary>

1. **Leia as legendas** e divida o japonês em palavras individuais.
2. **Filtre** as palavras de conteúdo que você ainda não conhece - opcionalmente revisando a lista você mesmo no Curador de Palavras.
3. **Capture uma captura de tela e um clipe de áudio** do vídeo para cada linha.
4. **Busque definições** nos seus dicionários offline configurados, recorrendo opcionalmente ao Jisho online se ativado (mais lento, com limite de taxa).
5. **Envie os cartões finalizados para o Anki.**

</details>

## Recursos Recomendados

| Tipo | Recurso | Download | Adicionar via |
|------|----------|----------|---------|
| Dicionário | [JMdict](https://github.com/yomidevs/jmdict-yomitan) | [Yomitan zip](https://github.com/yomidevs/jmdict-yomitan/releases/latest/download/JMdict_english.zip) | Adicionar dicionário… |
| Dicionário | [Jitendex](https://jitendex.org/) | [Yomitan zip](https://github.com/stephenmk/stephenmk.github.io/releases/latest/download/jitendex-yomitan.zip) | Adicionar dicionário… |
| Dicionário | [Bee's Character Dictionary](https://characterdictionary.tokyo/) | Gerado no site | Adicionar dicionário… |
| Acento Tonal | [Kanjium](https://github.com/mifunetoshiro/kanjium) | [TSV](https://raw.githubusercontent.com/mifunetoshiro/kanjium/master/data/source_files/raw/accents.txt) | Acento Tonal -> Adicionar fonte de acento tonal… |
| Acento Tonal | [アクセント辞典v2](https://learnjapanese.moe/yomichan/#dictionaries) | [Drive](https://drive.google.com/drive/folders/1tTdLppnqMfVC5otPlX_cs4ixlIgjv_lH) | Acento Tonal -> Adicionar fonte de acento tonal… |
| Frequência | [JPDB v2.2 Kana](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/JPDB_v2.2_Frequency_Kana_2024-10-13.zip) | Frequência -> Adicionar fonte de frequência… |
| Frequência | [BCCWJ SUW+LUW](https://github.com/Kuuuube/yomitan-dictionaries) | [Yomitan zip](https://github.com/Kuuuube/yomitan-dictionaries/raw/main/dictionaries/BCCWJ_SUW_LUW_combined.zip) | Frequência -> Adicionar fonte de frequência… |


<details>
<summary><strong>Licença do JMnedict</strong></summary>

Usa conjuntos de nomes empacotados derivados do [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (projeto JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Solução de Problemas

| Problema                    | Solução                                                                         |
|--------------------------|----------------------------------------------------------------------------------|
| "Não é possível conectar ao Anki" | Inicie o Anki e verifique se o AnkiConnect está instalado.                                  |
| "Baralho não encontrado"         | Escolha um baralho existente em Configurações -> Cartões e Anki. Baralhos não são criados automaticamente; crie um no Anki primeiro, se precisar de um novo. |
| "Tipo de nota não encontrado"    | Configure os nomes de campo do seu tipo de nota em Configurações -> Cartões e Anki.               |
| "ffmpeg não encontrado"       | Instale o ffmpeg e adicione-o ao PATH.                                               |
| Nenhuma definição encontrada     | Adicione um dicionário Yomitan em Configurações -> Adicionar dicionário… (recomendado), ou ative o fallback do Jisho (mais lento, com limite de taxa). |
| O instalador do Windows não abre / aviso do SmartScreen | Veja [Notas de primeira execução](#notas-de-primeira-execução-compilações-não-assinadas): selecione **Mais informações** -> **Executar assim mesmo**; restaure falsos positivos do Defender pelo **Histórico de proteção**. |
| Instalação nova sem definições | Execute Ferramentas -> Assistente de Configuração ou Ferramentas -> Baixar Recursos Recomendados. Para importação manual, mantenha o ZIP do Yomitan intacto (não o descompacte). |
| Adicionar dicionário trava ou falha | Anote o último estágio visível e anexe os registros (veja "Onde estão os registros?" abaixo). Inclua o nome do ZIP do dicionário, a fonte e o tamanho no relatório. |
| Onde estão os registros?      | Use Ajuda -> Abrir Pasta de Registros, ou abra `%USERPROFILE%\.anki_miner\anki_miner.log` no Windows ou `~/.anki_miner/anki_miner.log` no macOS/Linux. Os registros rotacionados usam os sufixos `.1` a `.5`. |
| Reportando um bug          | Ajuda -> Exportar Diagnósticos… grava um ZIP com registros e detalhes do sistema no local que você escolher. Revise-o antes de enviar, pois ele contém caminhos e nomes de arquivos do seu computador. Nada é enviado automaticamente. |
| Mais registro de diagnóstico | Defina `ANKI_MINER_LOG_LEVEL=DEBUG` antes de iniciar o Anki Miner para capturar detalhes de terceiros do yt-dlp, urllib3 e fugashi. O padrão é `WARNING`; os registros do Anki Miner permanecem em DEBUG. |
| O áudio está no idioma errado  | A ferramenta tenta primeiro as faixas de áudio em japonês e depois usa a padrão.      |
| Legendas fora de sincronia    | Use o controle de deslocamento de legenda na interface (faixa de ±300 segundos).                 |

## Roteiro

Lista de ideias para futuras versões do Anki Miner. Não estão em ordem de prioridade. Pedidos de recursos têm precedência.
- Sugira um recurso - [Abra uma issue](https://github.com/0xzerolight/anki_miner/issues).
- Discuta o roteiro - [Discussões](https://github.com/0xzerolight/anki_miner/discussions).

- **Recursos**:
  - [x] Seleção de idioma da interface.
  - [x] Aba de criação local de legendas: aba opcional para gerar legendas localmente.
  - [x] Aba de Leitura: minere mangás e livros.
  - [x] Ferramenta de preenchimento retroativo.
  - [ ] Biblioteca de mídia: expandir a aba de Análises para exibir a biblioteca de mídia local em todos os formatos de mídia.
  - [ ] Download automático de legendas.

- **Longo prazo**:
  - [x] Porte para Android -- https://github.com/0xzerolight/anki_miner_android
  - [ ] Além do japonês: mineração de outros idiomas.
  - [ ] Extensão de navegador do Anki Miner.


## Contribuindo

Contribuições de qualquer tipo são bem-vindas.
Se você quiser apoiar o projeto, compartilhe-o com outras pessoas que possam se beneficiar dele.

- Novo por aqui? Comece com [CONTRIBUTING.md](../CONTRIBUTING.md).
- Visão geral da arquitetura: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Código de Conduta: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Segurança: [SECURITY.md](../SECURITY.md).

Relatos de bugs e pedidos de recursos -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Perguntas gerais e discussões -> [Discussões](https://github.com/0xzerolight/anki_miner/discussions) ou [Discord](https://discord.com/invite/aDtQyZzUVP).

## Agradecimentos Especiais

Agradecimentos sinceros às pessoas que fizeram contribuições excepcionais ao projeto:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Sugestões de recursos brilhantes, testes de novos lançamentos, construção de comunidade.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Excelentes sugestões de recursos, construção de comunidade e moderação no Discord.

Veja [CONTRIBUTORS.md](../CONTRIBUTORS.md) para todos que fizeram algum tipo de contribuição ao projeto.


## Licença

GNU General Public License v3.0. Veja [LICENSE](../LICENSE).
