# Como reutilizar este monitor em outras viagens

Este monitor foi escrito de forma genérica: a viagem específica (origem, destino,
datas, e-mail, etc.) vive **inteira no [`routes.yaml`](routes.yaml)**. Pra mudar
de viagem, basta editar esse arquivo — você não precisa tocar em código Python.

Há duas formas de reusar:

---

## Cenário A — Reaproveitar o mesmo repositório

Use quando a viagem antiga já passou e você quer começar uma nova, sem manter
o histórico anterior.

**1. Edite `routes.yaml`** com os dados da nova viagem (veja seção
[Campos editáveis](#campos-editáveis) abaixo).

**2. Limpe o histórico antigo** (opcional mas recomendado, pra não
mostrar preços velhos no dashboard):

```powershell
# Zera o banco SQLite
Remove-Item data\flights.db -Force

# Apaga relatorios antigos (serao regerados no proximo run)
Remove-Item docs\flights.xlsx, docs\index.html -Force -ErrorAction SilentlyContinue
```

**3. Commit + push**:

```powershell
git add routes.yaml data\.gitkeep docs\.gitkeep
git commit -m "nova viagem: ORIGEM<->DESTINO datas X a Y"
git push
```

**4. Dispare um run manual** pra popular o dashboard:

```powershell
gh workflow run "Flight Monitor" --repo SEU_USUARIO/flight-monitor-gru-kef
```

Pronto. Os 3 cronjobs no cron-job.org continuam apontando pro mesmo repo —
nenhuma mudança lá.

> Se quiser **manter o histórico antigo intacto** (talvez pra comparar
> preços entre viagens em outra ferramenta), pule o passo 2. As queries
> filtram por `outbound_date` / `return_date`, então dados da viagem
> antiga ficam no banco mas não aparecem nos relatórios da nova.

---

## Cenário B — Criar um repositório separado por viagem

Use quando você quer monitorar **duas viagens em paralelo** (ex.: férias em
setembro E reveillon em dezembro) ou prefere manter cada histórico isolado.

**1. Crie um novo repo a partir deste** (via web ou CLI):

```powershell
# Clone este como template
git clone https://github.com/SEU_USUARIO/flight-monitor-gru-kef flight-monitor-NOVO
cd flight-monitor-NOVO

# Apaga historico do git e comeca limpo
Remove-Item -Recurse -Force .git
git init -b main
Remove-Item data\flights.db, docs\flights.xlsx, docs\index.html -Force -ErrorAction SilentlyContinue

# Edita routes.yaml com a nova viagem
notepad routes.yaml

# Inicializa commit + push como repo novo
git add .
git commit -m "Initial commit: monitor ORIGEM<->DESTINO"
gh repo create flight-monitor-NOVO --private --source=. --remote=origin --push
```

**2. Configure os 5 secrets no novo repo**:

```powershell
gh secret set TRAVELPAYOUTS_TOKEN --repo SEU_USUARIO/flight-monitor-NOVO
gh secret set SERPAPI_KEY --repo SEU_USUARIO/flight-monitor-NOVO
gh secret set GMAIL_USER --repo SEU_USUARIO/flight-monitor-NOVO --body "seu_email@gmail.com"
gh secret set GMAIL_APP_PASSWORD --repo SEU_USUARIO/flight-monitor-NOVO
gh secret set ALERT_RECIPIENT --repo SEU_USUARIO/flight-monitor-NOVO --body "destino@gmail.com"
```

**3. Habilite write permission e Pages**:

```powershell
gh api -X PUT "repos/SEU_USUARIO/flight-monitor-NOVO/actions/permissions/workflow" `
  -F "default_workflow_permissions=write" `
  -F "can_approve_pull_request_reviews=false"

# Tornar publico (pra usar Pages free) — opcional
gh repo edit SEU_USUARIO/flight-monitor-NOVO --visibility public --accept-visibility-change-consequences

# Habilita Pages
gh api -X POST "repos/SEU_USUARIO/flight-monitor-NOVO/pages" `
  -F "source[branch]=main" -F "source[path]=/docs"
```

**4. Atualize `monitoring.dashboard_url` no `routes.yaml`** pra apontar pro novo URL:

```yaml
monitoring:
  dashboard_url: "https://SEU_USUARIO.github.io/flight-monitor-NOVO/"
```

Commit + push.

**5. No cron-job.org**: duplique os 3 cronjobs existentes e mude a URL na aba
COMMON pra apontar pra `dispatches` do novo repo:
`https://api.github.com/repos/SEU_USUARIO/flight-monitor-NOVO/dispatches`

(O token PAT pode ser o mesmo, desde que tenha permissão `Contents: Write`
no novo repo também — ou crie outro.)

---

## Campos editáveis

Tudo que normalmente muda por viagem está em [`routes.yaml`](routes.yaml):

| Seção | Campo | O que é |
|---|---|---|
| `trip` | `origin` | IATA do aeroporto de saída (3 letras, ex: GRU, GIG, BSB) |
| `trip` | `destination` | IATA do aeroporto de chegada (ex: KEF, LIS, NRT) |
| `trip` | `destination_name` | Nome amigável da cidade (só pra título do dashboard) |
| `trip` | `outbound_date` | Data da ida (YYYY-MM-DD) |
| `trip` | `return_date` | Data da volta. Vazio para one-way. |
| `trip` | `passengers` | 1 a 9 |
| `trip` | `cabin_class` | `economy`, `premium_economy`, `business`, `first` |
| `hubs` | (lista) | Apenas referência pra nomes de cidade. Pode esvaziar. |
| `constraints` | `direct_only` | `true` força só voos diretos (raro pra Europa) |
| `monitoring` | `currency` | BRL, USD, EUR, etc. |
| `monitoring` | `dashboard_url` | URL pública do GitHub Pages (botão no e-mail) |
| `monitoring` | `alert_min_drop_pct` | % de queda que dispara alerta (default 10) |

E-mail de destino e usuário Gmail **NÃO ficam no `routes.yaml`** — ficam
em secrets do GitHub (`ALERT_RECIPIENT`, `GMAIL_USER`). Pra mudar:

```powershell
gh secret set ALERT_RECIPIENT --repo SEU_USUARIO/SEU_REPO --body "novo@email.com"
```

---

## Ajustes finos opcionais

### Mudar horários do cron

Edite `.github/workflows/monitor.yml`:

```yaml
schedule:
  - cron: "17 11 * * *"   # 08:17 BRT
  - cron: "23 17 * * *"   # 14:23 BRT
  - cron: "19 23 * * *"   # 20:19 BRT
```

E atualize os 3 jobs no cron-job.org (no campo Hour/Minute na aba COMMON).

### Mudar threshold de alerta

Em `routes.yaml`:

```yaml
monitoring:
  alert_min_drop_pct: 15    # alerta só com queda >=15% (em vez de 10%)
```

### Reduzir consumo SerpAPI

O free tier permite 250 searches/mês. Cada sweep = 1 search. 3 sweeps/dia × 30 dias = 90/mês.

Pra cortar pra ~30/mês: remova 2 dos 3 crons do workflow e desative 2 dos cronjobs no cron-job.org. Ou mude o cron pra rodar só 1x por dia.
