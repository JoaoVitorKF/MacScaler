# MacScaler

Cole um link do Pinterest, receba a imagem na maior resolução possível.

São **duas etapas de ganho**, e a primeira é gratuita: o Pinterest serve versões
reduzidas por padrão, mas o arquivo que o usuário subiu quase sempre é muito
maior. O app resolve o original antes de invocar o modelo — fazer upscale de IA
em cima de um thumbnail de 236px quando existe um original de 2400px é o erro
que este software existe para evitar.

## Rodar

```bash
./run.sh
```

Ou monte o app e abra pelo Finder, que é como ele vive no dia a dia:

```bash
.venv/bin/python build_app.py            # dist/MacScaler.app
.venv/bin/python build_app.py --install  # e copia para /Applications
```

Na primeira execução ele baixa o motor (`upscayl-ncnn`) e os modelos (~100 MB) em
`~/Library/Application Support/MacScaler/`, tira a quarentena do Gatekeeper e
faz um upscale de teste para provar que a GPU respondeu. Depois disso, funciona
offline.

Requer **Mac com Apple Silicon**. O binário roda nativo em arm64.

## Testes

```bash
.venv/bin/python check.py
```

Roda os critérios de aceite do documento contra o Pinterest de verdade.

## Estrutura

```
build_app.py            monta o .app (bundle fino) e desenha o ícone
app/
  core/
    resolver.py     [A] pin.it / pin / CDN -> URL do maior original
    downloader.py   [B] download com teto e verificação de tamanho
    classifier.py   [C] foto ou arte digital
    models.py           registro dos modelos e coerência de escala
    upscaler.py     [D] subprocess do upscayl-bin, progresso e fallbacks
    finalize.py     [E] Lanczos, giro e gravação
    pipeline.py         orquestra A→E, sem nada de Qt
    tuning.py           perfil da máquina -> configuração recomendada
    pil_limits.py       o Pillow trata imagem gigante como ataque; aqui é o produto
  setup/bootstrap.py    baixa, tira quarentena, testa a GPU
  ui/                   PySide6
check.py                critérios de aceite
```

O `core/` não importa Qt em lugar nenhum — dá para usar em script e em teste.

## Decisões que não são óbvias

**Binário.** `upscayl/upscayl-ncnn`, não o `xinntao/Real-ESRGAN-ncnn-vulkan`. O da
xinntao é o mais citado em tutoriais mas parou em 2022 e é x86_64: no M4 passaria
por Rosetta *e* por MoltenVK. O do Upscayl é universal e nativo em arm64.

**Sempre 4x, depois Lanczos.** Os modelos têm escala fixa. Rodar o 4x e reduzir
com Lanczos até o alvo bate um modelo 2x direto — o downscale recupera detalhe e
disfarça a textura sintética. Por isso a UI pergunta o tamanho de saída, não o
multiplicador.

**Classificador recalibrado.** A regra de partida do documento
(`cores < 1500 E área_plana > 0.35`) produzia 5 falsos "arte" em 30 fotos reais:
foto com fundo desfocado tem tanta área lisa quanto uma ilustração. O que separa
é a combinação de chapado de cor **com borda dura** — bokeh tem o primeiro sem o
segundo. Ver o cabeçalho de `core/classifier.py`. Amostra atual: 35/36.

**Cache do original.** O download fica em `originals/`, então trocar de modelo ou
de tamanho reprocessa sem bater no Pinterest de novo.

**"Ajustar para o meu Mac".** O botão lê o hardware e recomenda modelo, tamanho e
tile para a imagem que está carregada. O que ele *não* faz é vender velocidade:
25 execuções medidas neste M4 mostram que tile e `-j` não movem o relógio — a
matriz inteira coube entre 20 s e 25 s na mesma imagem, e o automático do ncnn
ganhou de todos os valores fixos na imagem grande. O tempo é linear nos
megapixels de **entrada** (~22,5 s/MP), então reduzir o tamanho de saída não
economiza um segundo de GPU. O `-x` (TTA), que a internet vende como "4x mais
lento", custou **7,4x** aqui — 153,7 s contra 20,8 s — e por isso não entra. O que sobra de escolha real é o modelo certo, o
tamanho que vale a pena guardar, e não pisar em configuração quebrada. A tabela
das medições está no cabeçalho de `core/tuning.py`.

**Orientação: EXIF é automático, o resto é botão.** Foto marcada com orientação
EXIF chega em pé sozinha — inclusive no preview, que antes ignorava a marca e
mostrava deitado enquanto o resto da janela já falava das dimensões corrigidas
(`QPixmap` não aplica EXIF; `QImageReader.setAutoTransform` aplica). Quando os
pixels vêm girados **sem** EXIF — que é o caso comum no Pinterest — nenhum dado do
arquivo diz qual é o lado certo, e adivinhar pelo conteúdo erraria calado em
imagens que já estavam certas. Aí são os botões ↺ ↻ (ou ⌘[ e ⌘]), e o giro entra
**antes** do modelo: o alvo de tamanho, a estimativa e o perfil do Mac passam
todos a medir a imagem em pé, então os critérios de upscaling não mudam.

**O `.app` é um bundle fino, de propósito.** O executável dentro dele é um script
que chama o venv deste projeto — 1,4 MB, quase tudo ícone, monta em dois segundos, e o código
mudado vale na abertura seguinte sem rebuild. O preço é não ser redistribuível:
leva o caminho de `~/pin-upscaler` embutido. Um bundle autocontido com PySide6
passa de 400 MB e obriga a reconstruir a cada linha; para um app que só roda
nesta máquina, seria pagar caro por nada.

**O Finder abria o app sob Rosetta.** O Python do Xcode é universal, e sem
`LSArchitecturePriority` no `Info.plist` o LaunchServices escolheu x86_64 para o
script do lançador: o Pillow arm64 não carregava e o app sumia do Dock sem deixar
nada na tela. Pelo terminal isso nunca aparece, porque lá o shell já é nativo. O
lançador também chama `arch -$(uname -m)` — os dois ficam, o custo é zero e o
sintoma era invisível.

**Tile 1024 grava imagem preta e diz que deu certo.** Código de saída 0,
"Upscayled Successfully!", PNG do tamanho certo e inteiramente preto: 1080×1920 em
1,8 s, 1800×3200 em 3,9 s, reproduzido. A mesma flag passa limpa em 720×1280, então
"funcionou uma vez" não vale como prova. Nenhum marcador de falta de memória
aparece no stderr — a única defesa é olhar o pixel, que é o que
`upscaler._is_blank()` faz antes de aceitar o resultado.
