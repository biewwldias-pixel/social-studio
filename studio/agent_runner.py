from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Any

from google.antigravity import Agent, CapabilitiesConfig, LocalAgentConfig
from google.antigravity.models import (
    DEFAULT_MODEL,
    GeminiAPIEndpoint,
    GeminiModelOptions,
    ModelTarget,
    ThinkingLevel,
)

from studio.brands import (
    BRANDS_DIR,
    DRAFTS_DIR,
    STUDIO_DIR,
    get_brand,
    list_brand_posts,
    read_brand_guide,
    update_brand,
)

SESSIONS_DIR = STUDIO_DIR / ".agent-sessions"


def _summarize_recent_posts(brand_id: str) -> str:
    posts = list_brand_posts(brand_id)[:6]
    if not posts:
        return "Nenhum post anterior encontrado ainda."
    lines = []
    for post in posts:
        folder_root = (
            DRAFTS_DIR / brand_id if post["source"] == "drafts" else get_brand(brand_id).posts_dir
        )
        folder = folder_root / post["slug"]
        lines.append(
            f"- `{post['slug']}` ({len(post['slides'])} slides, tipo={post['type']}) → pasta: `{folder}`"
        )
    return "\n".join(lines)


def _build_system_instructions(brand_id: str) -> str:
    brand = get_brand(brand_id)
    brand_guide = brand.brand_doc.read_text(encoding="utf-8") if brand.brand_doc.is_file() else ""
    draft_root = DRAFTS_DIR / brand_id
    recent_posts = _summarize_recent_posts(brand_id)
    return f"""Você é o diretor criativo e designer de redes sociais do projeto **{brand.name}** dentro do Social Studio local.

## Guia da Marca & Tokens Obrigatórios
{brand_guide}

## Posts Anteriores desta Marca (para referência e evitar repetição)
{recent_posts}

## Protocolo Obrigatório antes de Criar Qualquer Post
1. **Inspecione os designs anteriores:** antes de desenhar um novo post, leia 1 ou 2 arquivos `render.mjs` recentes das pastas listadas acima (e/ou veja as imagens) para:
   - **Copiar exatamente** o cabeçalho (`topBar`), o rodapé (`bottomBar`), o padding externo e os tokens do design system (cores, fontes, raios, ícones Phosphor fill + setas em traço).
   - **Escolher um arquétipo de miolo DIFERENTE** do último post publicado, para manter o feed dinâmico e variado sem jamais perder a identidade visual da marca.
2. **Consulte o projeto real** em `{brand.project_path}` sempre que o post falar de uma funcionalidade, tela ou texto real do produto.
3. **Salve o rascunho no padrão do Social Studio:**
   - Crie uma pasta nova em `{draft_root}/<slug-curto>/` (ex.: `{draft_root}/novidade-stories/`).
   - Gere os slides em PNG (`1080x1350` para feed/carrossel 4:5 ou `1080x1920` para story 9:16) usando Node.js + `@vercel/og`, nomeando em ordem: `slide-01-capa.png`, `slide-02-....png`, etc.
   - **Mantenha sempre o script `render.mjs` (a receita)** salvo dentro dessa mesma pasta do post para que seja possível editar e re-renderizar qualquer slide no futuro.
   - Salve **obrigatoriamente** um arquivo `meta.json` nessa pasta com `title`, `type` (`"carousel"`, `"feed"` ou `"story"`) e `caption` (legenda completa, pronta para publicar, com gancho, corpo, CTA e hashtags — o Social Studio abrirá automaticamente a prévia em tela cheia com essa arte e essa legenda assim que você terminar).
   - **NUNCA** publique direto no Instagram sem o usuário clicar no botão "Publicar" ou autorizar explicitamente.
4. Responda em português brasileiro, dizendo qual arquétipo visual você escolheu (e como variou em relação aos posts anteriores), o slug criado e a legenda sugerida.
"""


class BrandAgentPool:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._keys: dict[str, tuple[str | None, str | None]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, brand_id: str) -> asyncio.Lock:
        if brand_id not in self._locks:
            self._locks[brand_id] = asyncio.Lock()
        return self._locks[brand_id]

    async def _ensure_agent(
        self, brand_id: str, model: str | None = None, thinking: str | None = None
    ) -> Agent:
        normalized_model = (model or "").strip() or None
        normalized_thinking = (thinking or "").strip() or None
        config_key = (normalized_model, normalized_thinking)
        if brand_id in self._agents and self._keys.get(brand_id) == config_key:
            return self._agents[brand_id]
        if brand_id in self._agents:
            old = self._agents.pop(brand_id)
            await old.__aexit__(None, None, None)

        brand = get_brand(brand_id)
        (DRAFTS_DIR / brand_id).mkdir(parents=True, exist_ok=True)
        save_dir = SESSIONS_DIR / brand_id
        save_dir.mkdir(parents=True, exist_ok=True)

        model_config: str | ModelTarget | None = normalized_model
        if normalized_thinking is not None:
            model_config = ModelTarget(
                name=normalized_model or DEFAULT_MODEL,
                endpoint=GeminiAPIEndpoint(
                    options=GeminiModelOptions(thinking_level=ThinkingLevel(normalized_thinking))
                ),
            )

        config = LocalAgentConfig(
            system_instructions=_build_system_instructions(brand_id),
            capabilities=CapabilitiesConfig(),
            workspaces=[str(brand.project_path), str(STUDIO_DIR)],
            save_dir=str(save_dir),
            model=model_config,
        )
        agent = Agent(config)
        await agent.__aenter__()
        self._agents[brand_id] = agent
        self._keys[brand_id] = config_key
        return agent

    async def reset(self, brand_id: str) -> None:
        async with self._get_lock(brand_id):
            agent = self._agents.pop(brand_id, None)
            self._keys.pop(brand_id, None)
            if agent is not None:
                await agent.__aexit__(None, None, None)

    async def shutdown(self) -> None:
        for brand_id in list(self._agents.keys()):
            await self.reset(brand_id)

    async def stream_chat(
        self,
        brand_id: str,
        message: str,
        model: str | None = None,
        thinking: str | None = None,
    ) -> AsyncIterator[dict[str, str]]:
        async with self._get_lock(brand_id):
            before_posts = {p["id"]: p["updatedAt"] for p in list_brand_posts(brand_id)}
            try:
                agent = await self._ensure_agent(brand_id, model=model, thinking=thinking)
                response = await agent.chat(message)
            except Exception as exc:
                yield {"type": "error", "message": str(exc)}
                return

            queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue()

            async def pump_tokens() -> None:
                async for token in response:
                    await queue.put({"type": "token", "text": str(token)})

            async def pump_tools() -> None:
                async for call in response.tool_calls:
                    name = getattr(call, "name", "tool")
                    await queue.put({"type": "tool", "name": str(name)})

            async def run_all() -> None:
                try:
                    await asyncio.gather(pump_tokens(), pump_tools())
                except Exception as exc:
                    await queue.put({"type": "error", "message": str(exc)})
                finally:
                    await queue.put(None)

            task = asyncio.create_task(run_all())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield event
            finally:
                await task

            after_posts = list_brand_posts(brand_id)
            changed = [
                p
                for p in after_posts
                if p["id"] not in before_posts or p["updatedAt"] > before_posts[p["id"]]
            ]
            if changed:
                yield {"type": "post_ready", "postId": changed[0]["id"]}
            yield {"type": "done"}

    async def stream_analyze_design(
        self,
        brand_id: str,
        model: str | None = "neon",
        thinking: str | None = "medium",
    ) -> AsyncIterator[dict[str, Any]]:
        brand = get_brand(brand_id)
        if not brand.project_path.is_dir():
            yield {
                "type": "error",
                "message": f"Pasta do projeto não encontrada: {brand.project_path}. Selecione a pasta correta antes de analisar.",
            }
            return

        BRANDS_DIR.mkdir(parents=True, exist_ok=True)
        target_md = BRANDS_DIR / f"{brand.id}.md"
        analyze_save_dir = SESSIONS_DIR / f"_analyze_{brand.id}"
        analyze_save_dir.mkdir(parents=True, exist_ok=True)

        normalized_model = (model or "").strip() or "neon"
        normalized_thinking = (thinking or "").strip() or "medium"
        model_config: str | ModelTarget = ModelTarget(
            name=normalized_model,
            endpoint=GeminiAPIEndpoint(
                options=GeminiModelOptions(thinking_level=ThinkingLevel(normalized_thinking))
            ),
        )

        system_prompt = f"""Você é um arquiteto de design systems e diretor de arte analisando o projeto **{brand.name}** em `{brand.project_path}`.

Sua missão é inspecionar o código-fonte real do projeto e gerar um guia completo em Markdown em `{target_md}` para que um agente de redes sociais consiga criar posts, carrosséis e stories 100% fiéis à marca.

## O que investigar no projeto
1. **Design Tokens & CSS:** procure arquivos de estilo (`globals.css`, `tokens.css`, `theme`, `tailwind.config.*`, `package.json`, variáveis CSS/oklch/hex). Extraia cores exatas (fundo, superfícies, cor primária, acentos, bordas, textos), raios de borda, sombras e espaçamentos.
2. **Tipografia & Ícones:** descubra as fontes usadas (nomes, pesos, caminhos de arquivos `.ttf`/`.woff2` se existirem localmente) e a biblioteca de ícones (ex.: Phosphor, Lucide, Heroicons, estilo fill vs outline).
3. **Logo & Identidade:** localize arquivos de logo/SVGs, nome da marca, slogan, posicionamento, público-alvo e tom de voz (lendo README, landing page ou textos de UI).
4. **Posts já criados:** procure no projeto pastas de posts existentes (ex.: `instagram-posts`, `social-posts`, `posts`, `marketing`, scripts `render.mjs`, imagens de slides). Se encontrar, leia 1 ou 2 `render.mjs` para documentar exatamente o cabeçalho, rodapé, dimensões e padrões visuais já usados.

## Formato do arquivo `{target_md}`
Escreva o arquivo `{target_md}` em português brasileiro, bem estruturado em seções claras:
- `# Guia da Marca — {brand.name}`
- `## Essência & Tom de Voz`
- `## Paleta de Cores (Tokens Exatos)`
- `## Tipografia & Fontes`
- `## Logo, Ícones & Componentes Invariantes` (inclua cabeçalho/rodapé exatos se houver posts anteriores)
- `## Arquétipos de Layout Recomendados`
- `## Posts Existentes Encontrados` (caminho da pasta e resumo)
- `## Regras de Ouro & O Que Evitar`

## Saída final
Depois de salvar `{target_md}`, responda com um resumo curto do que você encontrou e inclua no final da resposta as linhas exatas abaixo (uma por linha, omitindo apenas a que não encontrar):
`PRIMARY_COLOR: #RRGGBB` (cor principal/marca do projeto em hexadecimal)
`POSTS_DIR: /caminho/absoluto/da/pasta` (se encontrou uma pasta com posts já criados dentro do projeto)
"""

        config = LocalAgentConfig(
            system_instructions=system_prompt,
            capabilities=CapabilitiesConfig(),
            workspaces=[str(brand.project_path), str(STUDIO_DIR)],
            save_dir=str(analyze_save_dir),
            model=model_config,
        )

        full_text = ""
        try:
            async with Agent(config) as agent:
                response = await agent.chat(
                    f"Analise todo o design system e procure posts já criados no projeto {brand.name} em {brand.project_path}. Salve o guia completo em {target_md}."
                )
                queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

                async def pump_tokens() -> None:
                    async for token in response:
                        await queue.put({"type": "token", "text": str(token)})

                async def pump_tools() -> None:
                    async for call in response.tool_calls:
                        name = getattr(call, "name", "tool")
                        await queue.put({"type": "tool", "name": str(name)})

                async def run_all() -> None:
                    try:
                        await asyncio.gather(pump_tokens(), pump_tools())
                    except Exception as exc:
                        await queue.put({"type": "error", "message": str(exc)})
                    finally:
                        await queue.put(None)

                task = asyncio.create_task(run_all())
                try:
                    while True:
                        event = await queue.get()
                        if event is None:
                            break
                        if event.get("type") == "token":
                            full_text += event.get("text", "")
                        yield event
                finally:
                    await task
        except Exception as exc:
            yield {"type": "error", "message": str(exc)}
            return

        detected_posts_dir = ""
        match = re.search(r"POSTS_DIR:\s*(.+)", full_text)
        if match:
            candidate = match.group(1).strip().strip("`\"'")
            if candidate and (brand.project_path / candidate).is_dir():
                detected_posts_dir = str((brand.project_path / candidate).resolve())
            elif candidate and (STUDIO_DIR / candidate).is_dir():
                detected_posts_dir = str((STUDIO_DIR / candidate).resolve())
            elif candidate and len(candidate) > 1:
                from pathlib import Path

                if Path(candidate).is_dir():
                    detected_posts_dir = str(Path(candidate).resolve())

        detected_color = ""
        color_match = re.search(r"PRIMARY_COLOR:\s*(#[0-9a-fA-F]{6})\b", full_text)
        if color_match:
            detected_color = color_match.group(1)

        detected_font = ""
        font_match = re.search(r"PRIMARY_FONT:\s*([^\n\r`]+)", full_text)
        if font_match:
            raw_font = font_match.group(1).strip().strip("\"'")
            raw_font = re.sub(r"\s*\(.*?\)\s*$", "", raw_font).strip()
            if raw_font and len(raw_font) <= 50:
                detected_font = raw_font

        if detected_posts_dir or detected_color or detected_font:
            update_brand(
                brand_id,
                posts_dir=detected_posts_dir or None,
                accent=detected_color or None,
                font_family=detected_font or None,
            )

        updated_brand = get_brand(brand_id)
        await self.reset(brand_id)
        yield {
            "type": "done",
            "guide": read_brand_guide(brand_id),
            "postsDir": updated_brand.posts_dir_str,
            "accent": updated_brand.accent,
            "fontFamily": updated_brand.font_family,
        }
