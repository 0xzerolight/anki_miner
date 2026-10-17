<!-- i18n-source: README.md sha256:6f9163baa7a87e06 -->

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
<div align="center">
<details>
<summary>Idioma: Português (Portugal)</summary>
<p>
<a href="../README.md">English</a> ·
<a href="README.ja.md">日本語</a> ·
<a href="README.ru.md">Русский</a> ·
<a href="README.fr.md">Français</a> ·
<a href="README.es.md">Español</a> ·
<a href="README.de.md">Deutsch</a> ·
<a href="README.pt_br.md">Português (Brasil)</a> ·
<a href="README.id.md">Bahasa Indonesia</a> ·
<a href="README.vi.md">Tiếng Việt</a> ·
<a href="README.zh_cn.md">简体中文</a> ·
<a href="README.zh_tw.md">繁體中文</a> ·
<a href="README.it.md">Italiano</a> ·
<a href="README.ar.md">العربية</a> ·
<a href="README.yue.md">粵語</a> ·
<a href="README.sl.md">Slovenščina</a> ·
<a href="README.ko.md">한국어</a> ·
<b>Português (Portugal)</b> ·
<a href="README.nl.md">Nederlands</a> ·
<a href="README.th.md">ไทย</a> ·
<a href="README.he.md">עברית</a> ·
<a href="README.fa.md">فارسی</a> ·
<a href="README.ca.md">Català</a> ·
<a href="README.nb.md">Norsk bokmål</a> ·
<a href="README.sv.md">Svenska</a> ·
<a href="README.da.md">Dansk</a> ·
<a href="README.pl.md">Polski</a> ·
<a href="README.uk.md">Українська</a> ·
<a href="README.hr.md">Hrvatski</a> ·
<a href="README.lt.md">Lietuvių</a> ·
<a href="README.fi.md">Suomi</a> ·
<a href="README.hu.md">Magyar</a> ·
<a href="README.el.md">Ελληνικά</a> ·
<a href="README.ro.md">Română</a> ·
<a href="README.tr.md">Türkçe</a>
</p>
</details>
</div>
<!-- i18n-nav:end -->

<p align="center">
Transforme conteúdo nativo em japonês, chinês, coreano e mais vinte e nove idiomas em cartões de vocabulário do Anki.
</p>

<p align="center">
Também disponível para Android - <a href="https://github.com/0xzerolight/anki_miner_android">Anki Miner para Android</a>.
</p>

<p align="center">
Se o Anki Miner lhe foi útil, deixe uma ⭐ estrela - ajuda outras pessoas a encontrá-lo :).
</p>


# <p align="center">Demonstração de mineração</p>

![Demonstração do Anki Miner](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.gif)

<p align="center">⬇️ <a href="https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/demo.mp4">Demonstração completa com som (MP4)</a></p>

### Exemplos de cartões

| ![ホント](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.gif) | ![いちゃいちゃ](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.gif) | ![代](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.gif) |
|:--:|:--:|:--:|
| ⬇️ [MP4 (com som)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/ホント.mp4) | ⬇️ [MP4 (com som)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/いちゃいちゃ.mp4) | ⬇️ [MP4 (com som)](https://raw.githubusercontent.com/0xzerolight/anki_miner/main/gifs/代.mp4) |

## Instalação

### Requisitos

- **Anki** com o suplemento [AnkiConnect](https://ankiweb.net/shared/info/2055492159) (código `2055492159`)
- **ffmpeg** + **libmpv** (apenas para a pré-visualização de vídeo) - só são necessários ao instalar através de pip/pipx ou a partir do código-fonte.

Transfira o ficheiro para a sua plataforma a partir da [versão mais recente](https://github.com/0xzerolight/anki_miner/releases/latest):

| Plataforma | Transferência |
|----------|----------|
| Windows | `AnkiMiner-*-Setup.exe` |
| macOS (Apple Silicon) | `AnkiMiner-*-macOS-arm64.dmg` |
| macOS (Intel) | `AnkiMiner-*-macOS-x86_64.dmg` ¹ |
| Linux (Debian/Ubuntu) | `anki-miner_*_amd64.deb` |
| Linux (outras) | `AnkiMiner-*-Linux-x86_64.AppImage` |

¹ Sem capturas de ecrã AVIF nem remoção de silêncios nas legendas geradas.

### Notas para a primeira execução (versões não assinadas)

- **macOS**: abra o `.dmg`, arraste o **AnkiMiner** para **Aplicações** e inicie-o. O primeiro arranque é bloqueado porque a aplicação não está notarizada: feche a caixa de diálogo, abra novamente a aplicação e escolha **Abrir mesmo assim** (também em **Definições do Sistema** -> **Privacidade e segurança**). Só é preciso uma vez.
- **Linux AppImage**: torne o ficheiro executável antes de o executar - `chmod +x AnkiMiner-*.AppImage`, ou **Propriedades** -> **Permitir execução** no gestor de ficheiros.
- **Windows SmartScreen**: **Mais informações** -> **Executar mesmo assim**.
- **Falso positivo do Windows Defender**: restaure-o a partir do **Histórico de proteção** ou [comunique-o à Microsoft](https://www.microsoft.com/en-us/wdsi/filesubmission).

<details>
<summary><strong>Instalar a partir do PyPI (Python 3.11+)</strong></summary>

```bash
pipx install anki-miner   # or: pip install anki-miner
anki_miner_gui
```

O japonês, o indonésio e o hebraico não precisam de nada adicional. Para outro idioma de mineração, adicione o respetivo motor:

```bash
pipx install "anki-miner[languages]"   # all; or one of [zh], [ko], [yue], [en], [ca], [de], [pt], [fr], [es], [it], [nl], [nb], [ro], [el], [fi], [hu], [hr], [sv], [pl], [lt], [da], [ru], [uk], [sl], [tr], [th], [vi]
```

O árabe e o persa não têm extra. Os respetivos dados, e os modelos spaCy dos idiomas europeus, são transferidos dentro da aplicação em Definições -> Idioma de mineração, onde as transferências acima obtêm tudo.

</details>

<details>
<summary><strong>Instalar a partir do código-fonte</strong></summary>

```bash
git clone https://github.com/0xzerolight/anki_miner.git
cd anki_miner
pip install -e .
anki_miner_gui
```

Para a configuração completa de desenvolvimento, consulte [CONTRIBUTING.md](../CONTRIBUTING.md).

</details>

## Separadores

- **Vídeo** - minere um único par vídeo/legendas, uma pasta em lote, URLs do YouTube ou do Bilibili, ou uma série inteira para um único baralho ordenado por frequência.
- **Audiolivros** - minere audiolivros, podcasts, rádio e canções (pares de áudio + legendas/transcrição).
- **Leitura** - minere manga (mokuro), romances (`.epub`, `.txt`; um só livro ou uma pasta inteira), ficheiros de legendas avulsos ou texto colado.
- **Análises** - histórico de mineração, classificações de dificuldade, marcos.
- **Utilitários** - gere legendas (Whisper local), retemporize legendas (ffsubsync/alass), condense multimédia em áudio só com diálogo, transfira vídeo/áudio/legendas de qualquer site suportado pelo yt-dlp, copie para um novo baralho a parte de um baralho pré-feito que vale a pena aprender, complete campos de cartões existentes, aplique OCR a imagens de páginas de manga para ficheiros .mokuro (mokuro) e sincronize um audiolivro com o texto do livro (Sinc. audiolivro).
- **Definições** - tudo o que é configurável.

## Outras funcionalidades

- Idiomas de mineração - japonês, chinês, coreano, inglês, catalão, alemão, português, francês, espanhol, italiano, neerlandês, norueguês bokmål, romeno, grego, finlandês, húngaro, croata, sueco, polaco, lituano, dinamarquês, turco, indonésio, russo, árabe, tailandês, persa, esloveno, ucraniano, vietnamita, cantonês e hebraico, escolhidos nas Definições. Os idiomas além do japonês transferem o respetivo motor dentro da aplicação; o indonésio e o hebraico não precisam de nenhum.
- Curador de palavras - reveja cada palavra candidata antes de os cartões serem criados, com a respetiva cena, página de manga e entrada do dicionário lado a lado.
- Anular uma execução - elimine as notas que uma execução acabou de criar, diretamente na janela de resultados.
- Filtragem extensa: i+1, intervalo de posições de frequência, lista negra, regex, conjuntos de palavras e muito mais.
- Importação offline de dicionários Yomitan - definições, acento tonal, frequência - encadeados por prioridade.
- Várias listas de frequência encadeadas por prioridade.
- Áudio da palavra nos cartões a partir de pacotes de áudio locais, JapanesePod101, Google TTS ou Microsoft Edge TTS.
- Áudio da frase nos cartões de Leitura a partir do TTS do Google Tradutor, ou do Naver Papago para japonês e coreano (desativado por predefinição).
- Estilo do glossário por dicionário, ao estilo do Yomitan.
- Pré-visualização de vídeo libmpv integrada - reproduza a cena de uma palavra durante a curadoria ou ajuste a temporização das legendas com reprodução em direto.
- Capturas de ecrã animadas (veja os exemplos de cartões acima).
- Perfis de definições - guarde configurações com nome e alterne entre elas a partir do cabeçalho.
- Reaplicar estilo aos cartões minerados - aplique o estilo de cartão atual aos cartões que já criou; para cartões de versões anteriores, use em vez disso Completar cartões (Utilitários → Completar cartões).

<details>
<summary><strong>Temas integrados (29)</strong></summary>

- **Ayu** - Light, Mirage, Dark
- **Catppuccin** - Latte (claro); Frappé, Macchiato, Mocha (escuros)
- **Dracula** - Dracula, Alucard
- **Everforest** - Light, Dark
- **GitHub** - Light; Dark, Dark Dimmed
- **Gruvbox** - Light Medium, Dark Medium
- **Kanagawa** - Lotus (claro), Wave (escuro)
- **Rosé Pine** - Dawn (claro); Main, Moon (escuros)
- **Solarized** - Light, Dark
- **Standalone** - Light, Dark, Sakura, Nord, One Dark, Tokyo Night

Licenças dos temas: [LICENSE-THEMES.md](../LICENSE-THEMES.md). 
Gostaria que fosse adicionado outro tema? Sugira-o numa Issue do GitHub.

</details>

<details>
<summary><strong>Como funciona</strong></summary>

1. **Lê as legendas** e divide o texto em palavras individuais.
2. **Filtra** as palavras de conteúdo que ainda não conhece - com a opção de rever a lista no Curador de palavras.
3. **Obtém uma captura de ecrã e um clipe de áudio** do vídeo para cada linha.
4. **Procura as definições** nos dicionários offline configurados.
5. **Envia os cartões concluídos para o Anki.**

</details>

## Recursos recomendados

Dicionários e listas de frequência para todos os idiomas de mineração, com ligações de transferência: [RESOURCES.md](../RESOURCES.md). O assistente de configuração propõe o conjunto certo para o seu idioma de mineração.

<details>
<summary><strong>Licença do JMnedict</strong></summary>

Utiliza conjuntos de nomes incluídos, derivados do [JMnedict](https://www.edrdg.org/enamdict/enamdict_doc.html) (projeto JMdict/EDICT, EDRDG, CC BY-SA 4.0).

</details>

## Resolução de problemas

| Problema                 | Solução                                                                          |
|--------------------------|----------------------------------------------------------------------------------|
| "Não é possível ligar ao Anki" | Inicie o Anki e confirme que o AnkiConnect está instalado.                 |
| "Baralho não encontrado" | Escolha um baralho existente em Definições -> Cartões e Anki. Os baralhos não são criados automaticamente; se precisar de um novo, crie-o primeiro no Anki. |
| "Tipo de nota não encontrado" | Configure os nomes dos campos do seu tipo de nota em Definições -> Cartões e Anki. |
| "ffmpeg não encontrado"  | Instale o ffmpeg e adicione-o ao PATH.                                           |
| Nenhuma definição encontrada | Adicione um dicionário Yomitan em Definições -> Dicionários -> Adicionar dicionário… |
| O instalador do Windows não abre / aviso do SmartScreen | Consulte as [Notas para a primeira execução](#notas-para-a-primeira-execução-versões-não-assinadas): selecione **Mais informações** -> **Executar mesmo assim**; restaure os falsos positivos do Defender a partir do **Histórico de proteção**. |
| Uma instalação nova não tem definições | Execute Ferramentas -> Assistente de configuração ou Ferramentas -> Transferir recursos recomendados. Na importação manual, mantenha o ZIP do Yomitan intacto (não o descompacte). |
| Adicionar dicionário bloqueia ou falha | Anote a última fase visível e anexe os registos (consulte "Onde estão os registos?" abaixo). Inclua no relatório o nome, a origem e o tamanho do ZIP do dicionário. |
| Onde estão os registos?  | Utilize Ajuda -> Abrir pasta de registos, ou abra `%USERPROFILE%\.anki_miner\anki_miner.log` no Windows ou `~/.anki_miner/anki_miner.log` no macOS/Linux. Os registos rodados usam os sufixos `.1` a `.5`. Envie também o `anki_miner.crash`, se existir (uma falha que encerrou a aplicação escreve a pilha nesse ficheiro, e não no registo), e o `anki_miner.child.log`, que contém a saída de um processo auxiliar. |
| Comunicar um erro        | Ajuda -> Exportar diagnóstico… escreve um ZIP no local que escolher, com os registos (`anki_miner.log` e as respetivas rotações, `anki_miner.crash`, `anki_miner.child.log`), o seu `settings.json`, os ficheiros de configuração e de estado da interface, instantâneos das filas e manifestos de transferências, e relatórios gerados sobre o estado do computador e da aplicação (`environment.txt`, `health.txt`, `resources.txt`, `stores.txt`, `disk.txt`, `screens.txt`). Reveja-o antes de o enviar, porque contém caminhos e nomes de ficheiros do seu computador. Nada é enviado automaticamente. |
| Mais registo de diagnóstico | Defina `ANKI_MINER_LOG_LEVEL=DEBUG` antes de iniciar o Anki Miner para registar os detalhes de terceiros do yt-dlp, urllib3 e fugashi. A predefinição é `WARNING`; os registos do Anki Miner mantêm-se em DEBUG. |
| O áudio está no idioma errado | A ferramenta escolhe a faixa de áudio do idioma de mineração ou, se não existir, a primeira. Escolha-a manualmente com Faixa de áudio… (Vídeo -> Individual). |
| Legendas dessincronizadas | Utilize o controlo de desfasamento das legendas na interface (intervalo de ±300 segundos). |

## Contribuir

São bem-vindas contribuições de qualquer tipo.
Se quiser apoiar o projeto, partilhe-o com outras pessoas a quem possa ser útil.

- É novo por aqui? Comece por [CONTRIBUTING.md](../CONTRIBUTING.md).
- Visão geral da arquitetura: [ARCHITECTURE.md](../ARCHITECTURE.md).
- Código de conduta: [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md).
- Segurança: [SECURITY.md](../SECURITY.md).

Relatórios de erros e pedidos de funcionalidades -> [Issues](https://github.com/0xzerolight/anki_miner/issues).
Perguntas gerais e discussão -> [Discussions](https://github.com/0xzerolight/anki_miner/discussions) ou [Discord](https://discord.com/invite/aDtQyZzUVP).

## Agradecimentos especiais

Um sincero agradecimento às pessoas que deram contributos excecionais ao projeto:

- ★ **[StyraxBenzoin](https://github.com/StyraxBenzoin)** - Sugestões brilhantes de funcionalidades, testes de novas versões, construção da comunidade.
- ★ **[rob-olvr](https://github.com/rob-olvr)** - Excelentes sugestões de funcionalidades, construção da comunidade e moderação no Discord.

Consulte [CONTRIBUTORS.md](../CONTRIBUTORS.md) para ver todas as pessoas que contribuíram de alguma forma para o projeto.


## Licença

GNU General Public License v3.0. Consulte [LICENSE](../LICENSE).
