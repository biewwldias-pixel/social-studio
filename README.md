# Social Studio

Estúdio local desktop/web para criar, visualizar, agendar e publicar posts, carrosséis e stories no Instagram com IA — mantendo o design system real de cada projeto.

## Recursos

- **Multi-projetos dinâmicos:** cadastre quantos projetos quiser (`⚙ → +`) com pasta local, conta do Instagram e guia visual próprio.
- **✨ Entender Design:** o agente inspeciona o código do seu projeto (CSS, tokens, fontes, logos e posts anteriores), gera um guia `.md` e aplica a cor principal do projeto no gradiente da tela inicial.
- **Criação + prévia em tela cheia:** ao terminar uma arte, abre automaticamente a visualização estilo Instagram com slides, legenda pronta e `render.mjs` salvo.
- **Publicação direta e agendador local:** publica Feed, Carrossel e Stories direto pela Meta Graph API v22.0, com calendário/relógio customizado e gerenciamento de agendamentos.
- **Gestão completa de modelos:** clique direito para selecionar múltiplos, duplicar, apagar ou baixar direto em uma pasta escolhida.
- **Histórico de conversas:** salva automaticamente cada conversa por projeto.
- **Guia interativo (`/guia`):** passo a passo completo para obter todas as credenciais + prompt pronto de 1 clique para enviar à sua IA.

## Como rodar localmente

Requisitos: Python 3.11+ e [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run uvicorn app:app --host 127.0.0.1 --port 4000
```

Abra `http://127.0.0.1:4000` (ou use `./social-studio.sh` no Linux para abrir em modo aplicativo).

### Windows (`.exe`)

No PowerShell:

```powershell
.\build-exe.ps1
```

Ou use o workflow `.github/workflows/build-windows.yml` no GitHub Actions para baixar `SocialStudio.exe` pronto.

## Configuração das variáveis

Todas as credenciais ficam **apenas no seu computador** (`.env` e `brands.json`, ambos no `.gitignore`).
Abra o app e clique em **`?`** no topo direito (ou acesse `http://127.0.0.1:4000/guia`) para ver o guia completo e o prompt copiável para sua IA.
