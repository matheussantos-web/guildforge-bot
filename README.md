# GuildForge

Bot multi-guild para Albion Online com sistema de LFG, registro e economia de pontos.

## Descrição

GuildForge é um bot Discord para gerenciar grupos (LFG), registro de membros e
economia de pontos dentro de guildas do Albion Online. Atualmente encontra-se em
estágio inicial: apenas o esqueleto de configuração e deploy.

## Configuração local

1. Instale o Python 3.10+.
2. Crie um arquivo `.env` a partir do exemplo:

   ```
   cp .env.example .env
   ```

   Preencha `DISCORD_TOKEN` com o token do bot criado no [Discord Developer Portal](https://discord.com/developers/applications).

3. Instale as dependências:

   ```
   pip install -r requirements.txt
   ```

4. Execute o bot:

   ```
   python bot/main.py
   ```

O bot deve conectar e logar "GuildForge iniciado" no console.

## Deploy no Render

O repositório inclui `render.yaml` para deploy automático como **Background Worker**
(não Web Service), já que um bot Discord é um processo contínuo sem porta HTTP.

Alternativamente, configure manualmente no painel do Render:

- **Type:** Background Worker
- **Runtime:** Python
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python bot/main.py`
- **Environment variables:**
  - `DISCORD_TOKEN` (secret)
  - `DATABASE_URL` (secret)
  - `ENVIRONMENT=production`

Tokens e senhas são configurados como secrets no painel do Render e **nunca**
devem ser commitados no repositório.

## Estrutura do projeto

```
bot/
  config.py   # Carrega variáveis de ambiente e constantes
  main.py     # Ponto de entrada, carrega cogs dinamicamente
  cogs/       # Cogs do bot (carregados automaticamente)
```
