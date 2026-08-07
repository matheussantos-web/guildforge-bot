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

## Deploy no Render (grátis)

O plano free do Render não oferece Background Worker (a partir de $7/mês) e
Static Site não roda processo contínuo. A estratégia gratuita é um **Web Service
Free** rodando o bot, com um endpoint `/health` que é pingado periodicamente para
evitar o spin-down do free tier (15 min sem tráfego de entrada derruba o processo
— e um bot Discord precisa ficar online o tempo todo).

O repositório inclui `render.yaml` (deploy via Blueprint) ou configure manualmente:

- **Type:** Web Service (instância **Free**)
- **Runtime:** Python
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `python bot/main.py`
- **Health check path:** `/health`
- **Environment variables:**
  - `DISCORD_TOKEN` (secret)
  - `DATABASE_URL` (secret)
  - `ENVIRONMENT=production`

Para manter o serviço acordado, crie um monitor no **UptimeRobot** (grátis) para a
URL do serviço (ex: `https://guildforge-bot.onrender.com/health`) com intervalo de
5 minutos. Tokens e senhas são secrets no painel do Render e **nunca** devem ser
commitados no repositório.

> Obs: o Postgres free do Render expira após 30 dias. Para continuar sem custo
> depois disso, migre para outro host (ex: Supabase) ou passe a pagar o plano
> Basic.

## Estrutura do projeto

```
bot/
  config.py   # Carrega variáveis de ambiente e constantes
  main.py     # Ponto de entrada, carrega cogs dinamicamente
  cogs/       # Cogs do bot (carregados automaticamente)
```
