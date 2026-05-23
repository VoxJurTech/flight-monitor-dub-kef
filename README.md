# Flight Monitor — GRU ↔ KEF (Reykjavik)

Monitor automatizado de preços de passagem **round-trip** São Paulo (GRU) ↔ Reykjavik (KEF), Islândia.
Roda 3x/dia no GitHub Actions, gera relatório Excel + dashboard HTML, e envia e-mail quando o preço cai.

**Viagem:** ida 25/09/2026 (GRU→KEF) / volta 11/10/2026 (KEF→GRU) · 1 adulto, econômica, com bagagem despachada
**Modelo de compra monitorado:** UMA passagem round-trip GRU↔KEF com conexão em hub (a forma comum e segura — companhia aérea protege a conexão e cuida da bagagem).

---

## Arquitetura

```
                     ┌─────────────────────────────────────────┐
                     │  GitHub Actions (3x/dia: 8:17 / 14:23 / │
                     │                 20:19 BRT)              │
                     └────────────────┬────────────────────────┘
                                      │
                                      ▼
                          ┌───────────────────────┐
                          │     monitor.py        │
                          └──────────┬────────────┘
                                     │ 1 chamada por sweep
                                     ▼
                  ┌──────────────────────────────────────┐
                  │  SerpAPI Google Flights              │
                  │  GRU<->KEF round-trip, qualquer hub  │
                  │  ~90 searches/mes (cabe nos 250 free)│
                  └────────────────┬─────────────────────┘
                                   │
                                   ▼
                          ┌──────────────────────┐
                          │  SQLite (flights.db) │
                          │  uma linha por voo,  │
                          │  coluna 'hub' marca  │
                          │  o aeroporto de      │
                          │  conexão da ida      │
                          └────────┬─────────────┘
                                   │
                  ┌────────────────┼─────────────────┐
                  ▼                ▼                 ▼
          flights.xlsx      index.html         e-mail alert
          (4 abas)          (dashboard)        (queda ≥10%)
```

**Por que SerpAPI Google Flights e não Travelpayouts:** a Travelpayouts free tier depende de cache populado por usuários da Aviasales; para datas distantes (set/out 2026), simplesmente não retorna dados. O Google Flights via SerpAPI consulta voos reais em tempo real, retornando ~15 opções por busca cobrindo dezenas de hubs.

**Hubs no `routes.yaml`** são apenas referência para nomes de cidades no relatório — o monitor não restringe a busca a esses hubs. O Google Flights retorna o que existe de verdade (inclusive YYZ/Toronto, que não estava na lista mas pode ser o mais barato).

---

## Como subir do zero (passo-a-passo)

### 1. Criar repositório GitHub

1. Acesse https://github.com/new
2. Nome sugerido: `flight-monitor-gru-kef`
3. Visibilidade: **Privado** (você não quer expor seus históricos)
4. **Não** marque "Initialize with README" (vamos subir os arquivos)
5. Crie

### 2. Subir os arquivos

Abra o terminal (PowerShell ou Git Bash) na pasta `flight-monitor`:

```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/flight-monitor-gru-kef.git
git push -u origin main
```

### 3. Adicionar Secrets no GitHub

No repositório → **Settings → Secrets and variables → Actions → New repository secret**

Crie estes 5 secrets:

| Nome | Valor |
|---|---|
| `TRAVELPAYOUTS_TOKEN` | seu token de https://www.travelpayouts.com |
| `SERPAPI_KEY` | sua key de https://serpapi.com |
| `GMAIL_USER` | seu e-mail remetente (Gmail) |
| `GMAIL_APP_PASSWORD` | senha de app de https://myaccount.google.com/apppasswords (16 chars, sem espaços) |
| `ALERT_RECIPIENT` | e-mail que recebe os alertas |

### 4. Habilitar GitHub Actions

1. No repositório → aba **Actions**
2. Clique em **I understand my workflows, go ahead and enable them**
3. Selecione o workflow **Flight Monitor**
4. Clique em **Run workflow** → branch `main` → **Run workflow** para rodar a primeira vez manualmente
5. Acompanhe a execução. Em ~5 min termina e:
   - O DB (`data/flights.db`) e os relatórios (`docs/flights.xlsx`, `docs/index.html`) ficam commitados no repo
   - Os mesmos arquivos ficam disponíveis como **Artifacts** do workflow (botão na página do run)

A partir daí, roda sozinho 3x/dia.

---

## Testar local antes de subir (recomendado)

```bash
# 1. Clone (depois de subir) ou use a pasta local diretamente
cd flight-monitor

# 2. Copie .env.example para .env e edite com suas credenciais reais
cp .env.example .env
# Edite .env no seu editor

# 3. Crie venv e instale deps
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # macOS/Linux
pip install -r requirements.txt

# 4. Rode o monitor uma vez
python monitor.py
```

Se o output mostrar várias linhas tipo `[N/13] Hub XXX ... N resultados`, deu certo.
Verifique:

- `data/flights.db` foi criado
- `docs/flights.xlsx` e `docs/index.html` foram gerados
- Você recebeu (ou não) e-mail dependendo se já há histórico

### Forçar a verificação SerpAPI (independente do dia):
```bash
python monitor.py --serpapi
```

### Regerar apenas os relatórios (sem chamar APIs):
```bash
python monitor.py --report-only
```

### Rodar o teste sintético (sem chamadas externas, gera dados fake):
```bash
python test_synthetic.py
```

---

## Estrutura dos arquivos

```
flight-monitor/
├── monitor.py              # Orquestrador principal
├── routes.yaml             # Configuração: datas, hubs de referência, alertas
├── storage.py              # Camada SQLite (com coluna 'hub')
├── report.py               # Geração Excel + HTML
├── notify.py               # Alertas por e-mail SMTP Gmail
├── test_synthetic.py       # Teste com dados fake (legado)
├── sources/
│   ├── __init__.py
│   ├── serpapi_flights.py  # Cliente SerpAPI Google Flights (fonte primária)
│   └── travelpayouts.py    # Cliente Aviasales (legado, não usado no fluxo atual)
├── data/
│   └── flights.db          # SQLite (criado na 1ª execução, commitado pelo workflow)
├── docs/
│   ├── flights.xlsx        # Excel (gerado a cada run)
│   └── index.html          # Dashboard (gerado a cada run)
├── .github/workflows/
│   └── monitor.yml         # GitHub Actions: cron 3x/dia + commit DB/reports
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

---

## Como ler os relatórios

### Excel — 4 abas

1. **Top Hubs:** o ranking — melhor preço round-trip GRU↔KEF agrupado por hub de conexão. **Aba mais importante.** Escala de cor verde→vermelho na coluna preço.
2. **Atual:** todas as opções do último sweep (uma linha por voo retornado pelo Google).
3. **Histórico:** série temporal por hub (preço mínimo por dia). Útil pra pivot tables.
4. **Divergências:** preserva espaço pra comparação futura entre fontes diferentes (se reativar Travelpayouts).

### HTML — dashboard

Abra `docs/index.html` no navegador. Contém:

- Cards no topo: melhor preço atual, mínimo histórico do hub vencedor, número de hubs cotados, total de cotações no banco
- Tabela "Top opções por hub" com preço, companhia, duração e número de escalas
- Gráfico de série temporal por hub (evolução dos preços ao longo do tempo)

Para ver renderizado online: ative GitHub Pages em Settings → Pages → branch main → folder /docs. Acesse em `https://SEU_USUARIO.github.io/flight-monitor-gru-kef/`.

---

## Comportamento dos alertas

E-mail é disparado quando, **para cada hub de conexão**, a cotação atual:

1. Bate **novo mínimo histórico** desse hub, OU
2. Cai **≥10%** (configurável em `routes.yaml` → `alert_min_drop_pct`) versus o mínimo anterior

De-duplicação: mesmo hub não dispara mais de 1 alerta a cada 12h.

Você recebe um único e-mail consolidado por sweep, com hub, preço round-trip, companhia, duração, escalas, motivo e link pro Google Flights.

---

## Quando algo der errado

### "SERPAPI_KEY não definida"
Você não configurou o secret no GitHub ou o `.env` local.

### Workflow falha com "Permission denied" no commit
Settings → Actions → General → **Workflow permissions** → marque **Read and write permissions**.

### E-mail não chegou
- Confirme que a senha de app Gmail tem 16 caracteres, sem espaços
- Verifique a caixa de spam
- Confirme que 2FA está ativado na conta Google

### SerpAPI estoura free tier
O monitor usa **1 search por sweep** (3x/dia = ~90/mês). Os 250 do plano free dão folga ampla. Se mesmo assim estourar, reduza a frequência removendo um dos crons do `monitor.yml`.

### Cron do GitHub Actions não dispara nos horários esperados
GitHub Actions tem latência alta em scheduled crons com minutos "redondos" (XX:00). Por isso o workflow usa minutos "ímpares" (`17 11`, `23 17`, `19 23`). Mesmo assim, pode haver atraso de até 30 min em horário de pico — é limitação conhecida do GitHub.

---

## Próximos passos sugeridos

- Cotar em USD/EUR também (às vezes compra em outra moeda compensa)
- Verificação de preços em datas vizinhas (±1 dia) — se aceitar flexibilidade
- Notificação via Telegram em vez de e-mail (mais imediato)
- Limitar tipos de hub (ex: só europeus) editando `routes.yaml`

---

## Notas de segurança

- **Nunca commite `.env`** — está no `.gitignore`
- A senha de app do Gmail dá acesso ao envio de e-mails da conta — **revogue imediatamente** se vazar (https://myaccount.google.com/apppasswords)
- O DB SQLite (`data/flights.db`) e os relatórios em `docs/` **não contêm credenciais** — só preços de voo públicos. Seguros pra repo público.
