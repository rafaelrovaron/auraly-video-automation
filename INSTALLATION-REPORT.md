# Auraly Pipeline — Relatório de Instalação

**Data:** 2026-07-22 20:50:55 ESAST
**Diretório:** `<AURALY_ROOT>/pipeline`
**Escopo:** ambiente local isolado para pós-produção de vídeos HeyGen. Nenhum asset existente do Auraly foi alterado.

## Componentes instalados

### Base já disponível

| Componente | Versão |
|---|---:|
| Windows | 10 x64 |
| Node.js | 22.23.0 |
| npm/npx | 10.9.8 |
| Python | 3.11.15 |
| uv | 0.11.26 |
| FFmpeg/ffprobe | 8.1.1 |
| Git | 2.54.0.windows.1 |

### Ambiente Python isolado

Local: `pipeline/.venv`

Dependências principais:

- `faster-whisper==1.2.1`
- `ctranslate2==4.8.1`
- `pydantic==2.13.4`
- `pydantic-settings==2.14.2`
- `typer==0.27.0`
- `rich==14.3.4`
- `PyYAML==6.0.3`
- `pysubs2==1.8.1`
- `pytest==8.4.2`
- `pytest-cov==6.3.0`
- `ruff==0.15.22`
- `mypy==1.20.2`

Verificações executadas:

```text
uv sync --locked --all-groups: OK
uv pip check: All installed packages are compatible
Importação das dependências principais: OK
```

### faster-whisper small.en

- Repositório: `Systran/faster-whisper-small.en`
- Revisão: `d1d751a5f8271d482d14ca55d9e2deeebbae577f`
- Local: `pipeline/models/faster-whisper-small.en`
- Tamanho local: aproximadamente 464 MB
- Modo validado: CPU + INT8
- Carregamento e inferência de smoke test: OK

O modelo é ignorado pelo Git e não será duplicado em commits.

### HyperFrames

- Versão fixada: `0.7.103`
- Instalação: `pipeline/node_modules`
- Lockfile: `pipeline/package-lock.json`
- Telemetria: desativada
- Chrome Headless Shell: `152.0.7928.2`
- Executável:
  `<USER_HOME>/.cache/hyperframes/chrome/chrome-headless-shell/win64-152.0.7928.2/chrome-headless-shell-win64/chrome-headless-shell.exe`

Validações executadas:

```text
hyperframes lint: 0 errors, 0 warnings
hyperframes check --strict: aprovado
Runtime: 0 errors, 0 warnings
Layout: 0 issues
Motion: 0 errors, 0 warnings
WCAG AA: 6/6 verificações aprovadas
```

Render canário real:

```text
Formato: H.264 MP4
Resolução: 1080×1920
FPS: 30
Duração: 1,0 segundo
Workers: 1
Modo: low-memory / draft
Tempo de renderização: 3,4 segundos
Browser GPU: aceleração disponível via Direct3D 11
Resultado: OK
```

Artefato temporário:

`pipeline/work/install-smoke/hyperframes-canary.mp4`

### Auto-Editor

- Versão: `31.3.2`
- Instalação: binário oficial Windows x86_64
- Local: `pipeline/tools/auto-editor/auto-editor.exe`
- SHA-256 verificado:
  `ba508838026d2878f598f6d6ceccebfb113f9ac784abd8b2f5f0a07b18bb5674`
- Digest confere com o publicado no release oficial do GitHub.

A distribuição por PyPI foi descontinuada. A instalação usa corretamente o binário oficial, sem depender de um pacote Python antigo.

Smoke test realizado com vídeo sintético de três segundos:

```text
Entrada: 3,00 s
Saída prevista: 1,40 s
Cortes detectados: 2
Resultado: OK
```

O binário é ignorado pelo Git.

## Estrutura criada

```text
Auraly/pipeline/
├── .git/
├── .env.example
├── .gitignore
├── .venv/
├── models/
│   └── faster-whisper-small.en/
├── node_modules/
├── tools/
│   └── auto-editor/
│       └── auto-editor.exe
├── work/
│   └── install-smoke/
├── package.json
├── package-lock.json
├── pyproject.toml
└── uv.lock
```

O diretório foi inicializado como repositório Git. O código-fonte não inclui mídia, modelos, ambientes virtuais ou credenciais.

## Uso do ambiente

Executar comandos Python:

```bash
cd '<AURALY_ROOT>/pipeline'
uv run python ...
```

Verificar o HyperFrames:

```bash
npm run hf:doctor
npm run hf:lint -- <diretório-da-composição>
npx hyperframes check --strict <diretório-da-composição>
```

Executar Auto-Editor:

```bash
./tools/auto-editor/auto-editor.exe input.mp4 --preview
```

## Alertas conhecidos

O HyperFrames foi atualizado de `0.7.66` para `0.7.103`. Em 2026-08-09,
`npm audit --omit=dev` reportou **zero vulnerabilidades conhecidas** no lockfile.

Controles mantidos para o piloto:

1. Studio e servidor somente em localhost;
2. não expor porta do HyperFrames à rede;
3. usar apenas imagens, vídeos e arquivos ZIP aprovados;
4. manter o HyperFrames atrás de um adapter substituível;
5. executar `npm audit` em toda atualização do lockfile.

O diagnóstico do HyperFrames também lista Docker, Whisper.cpp, Kokoro e MusicGen como ausentes. Eles são opcionais e foram deliberadamente excluídos desta pipeline porque:

- `faster-whisper` será o transcritor;
- o HeyGen fornece a voz e o talking head;
- músicas virão da biblioteca licenciada;
- Docker não é necessário para o render local.

## Consumo de disco

Valores aproximados após a instalação:

| Componente | Espaço |
|---|---:|
| Pipeline completa | 1,2 GB |
| Modelo small.en | 464 MB |
| Ambiente Python | 331 MB |
| node_modules | 385 MB |
| Auto-Editor | 43 MB |
| Chrome Headless | 115 MB baixados, armazenado no cache do usuário |

Espaço livre após instalação: aproximadamente 187 GB.

## Resultado

O ambiente necessário para iniciar a implementação está instalado e validado:

- [x] FFmpeg e ffprobe
- [x] ambiente Python isolado
- [x] dependências Python fixadas
- [x] faster-whisper small.en
- [x] HyperFrames 0.7.103
- [x] Chrome Headless Shell
- [x] telemetria desativada
- [x] Auto-Editor 31.3.2 com checksum validado
- [x] smoke test de cortes
- [x] render canário 1080×1920
- [x] repositório Git local inicializado
