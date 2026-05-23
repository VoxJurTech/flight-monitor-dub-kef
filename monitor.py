"""
Monitor de voos GRU -> KEF (Reykjavik) via hubs.

Estratégia:
- Camada A (diária): Travelpayouts/Aviasales — para cada hub, busca GRU<->hub
  ida-volta direto + hub<->KEF ida-volta direto. Persiste em SQLite.
- Camada B (semanal, segunda): SerpAPI Google Flights para os top 3 candidatos.
- Relatórios: Excel (4 abas) + HTML dashboard.
- Alertas: e-mail quando rota bate novo mínimo histórico ou cai >= N%.

Uso:
    python monitor.py             # roda monitor completo
    python monitor.py --serpapi   # força verificação SerpAPI
    python monitor.py --report-only  # só regera relatórios
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from sources.travelpayouts import TravelpayoutsClient
from sources.serpapi_flights import SerpApiFlightsClient
from storage import Storage
import report
import notify

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "docs"
DB_PATH = DATA_DIR / "flights.db"
CONFIG_PATH = ROOT / "routes.yaml"

DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    for k in ("outbound_date", "return_date"):
        v = cfg["trip"].get(k)
        if v and not isinstance(v, str):
            cfg["trip"][k] = v.isoformat()
    # Normaliza return_date vazio/null -> None (one-way)
    if not cfg["trip"].get("return_date"):
        cfg["trip"]["return_date"] = None
    return cfg


def _persist_results(results, storage, source, trip, direct, currency):
    count = 0
    for q in results:
        try:
            storage.save_quote(
                source=source,
                origin=trip["origin"],
                destination=trip["destination"],
                outbound_date=trip["outbound_date"],
                return_date=trip.get("return_date"),
                direct=direct,
                price=float(q.get("price", 0)),
                currency=currency,
                airline=q.get("airline"),
                flight_number=str(q.get("flight_number") or ""),
                departure_time=q.get("departure_at"),
                arrival_time=q.get("return_at"),
                duration_min=q.get("duration"),
                transfers=q.get("transfers", 0),
                booking_link=q.get("link"),
                raw=q,
            )
            count += 1
        except (ValueError, TypeError) as e:
            log.warning(f"Falhou ao salvar cotacao: {e}")
    return count


def run_travelpayouts_sweep(config, storage):
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        log.error("TRAVELPAYOUTS_TOKEN nao definido. Abortando.")
        sys.exit(1)

    trip = config["trip"]
    hubs = config["hubs"]
    direct_only = config["constraints"].get("direct_only", True)
    currency = config["monitoring"]["currency"]
    client = TravelpayoutsClient(token, currency=currency)

    stats = {"hubs_queried": 0, "quotes_saved": 0, "empty_routes": []}

    log.info("=" * 60)
    log.info("Camada A: Travelpayouts (sweep diario)")
    log.info("=" * 60)
    log.info(f"Datas: ida {trip['outbound_date']}, volta {trip['return_date']}")
    log.info(f"Voos diretos apenas: {direct_only}")

    log.info("[1/13] Direto GRU<->KEF (checagem)")
    results = client.get_prices_for_dates(
        origin=trip["origin"], destination=trip["destination"],
        departure_at=trip["outbound_date"], return_at=trip["return_date"],
        direct=direct_only, limit=5,
    )
    stats["quotes_saved"] += _persist_results(
        results, storage, "travelpayouts", trip, direct_only, currency)
    if not results:
        stats["empty_routes"].append(f"{trip['origin']}->{trip['destination']}")

    for i, hub in enumerate(hubs, start=2):
        hub_code = hub["code"]
        log.info(f"[{i}/13] Hub {hub_code} ({hub['city']})")

        r1 = client.get_prices_for_dates(
            origin=trip["origin"], destination=hub_code,
            departure_at=trip["outbound_date"], return_at=trip["return_date"],
            direct=direct_only, limit=10,
        )
        s1 = _persist_results(
            r1, storage, "travelpayouts",
            {**trip, "origin": trip["origin"], "destination": hub_code},
            direct_only, currency)
        if not r1:
            stats["empty_routes"].append(f"{trip['origin']}<->{hub_code}")

        r2 = client.get_prices_for_dates(
            origin=hub_code, destination=trip["destination"],
            departure_at=trip["outbound_date"], return_at=trip["return_date"],
            direct=direct_only, limit=10,
        )
        s2 = _persist_results(
            r2, storage, "travelpayouts",
            {**trip, "origin": hub_code, "destination": trip["destination"]},
            direct_only, currency)
        if not r2:
            stats["empty_routes"].append(f"{hub_code}<->{trip['destination']}")

        stats["hubs_queried"] += 1
        stats["quotes_saved"] += s1 + s2

    log.info("")
    log.info(f"Sweep concluido: {stats['hubs_queried']} hubs, "
             f"{stats['quotes_saved']} cotacoes salvas, "
             f"{len(stats['empty_routes'])} rotas vazias")
    if stats["empty_routes"]:
        log.info(f"Rotas sem resultado: {', '.join(stats['empty_routes'])}")
    return stats


def _compute_top_combinations(storage, config, top_n=3):
    trip = config["trip"]
    hubs = [h["code"] for h in config["hubs"]]
    cheapest = storage.get_cheapest_per_route(source="travelpayouts")
    by_route = {(q["origin"], q["destination"]): q for q in cheapest}

    combos = []
    for hub in hubs:
        a = by_route.get((trip["origin"], hub))
        b = by_route.get((hub, trip["destination"]))
        if a and b:
            combos.append({
                "hub": hub,
                "total_price": a["price"] + b["price"],
                "leg_a": a, "leg_b": b,
            })
    combos.sort(key=lambda x: x["total_price"])
    return combos[:top_n]


def run_serpapi_round_trip_search(config, storage):
    """
    Busca round-trip (ou one-way) origem<->destino em uma unica chamada SerpAPI.
    O retorno traz multiplas opcoes com diferentes hubs de conexao;
    salva cada uma com (origin, destination, hub=<codigo>).

    Modo one-way: ativado quando trip.return_date estah vazio/None.

    Custo: 1 search SerpAPI por sweep -> ~90/mes em 3 sweeps/dia (cabe nos 250 free).
    """
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        log.warning("SERPAPI_KEY nao definida. Pulando busca.")
        return 0

    trip = config["trip"]
    currency = config["monitoring"]["currency"]
    client = SerpApiFlightsClient(key, currency=currency)

    return_date = trip.get("return_date") or None
    is_one_way = not return_date
    trip_kind = "one-way" if is_one_way else "round-trip"

    log.info("=" * 60)
    log.info(f"Busca {trip_kind} {trip['origin']}->{trip['destination']} via SerpAPI")
    if is_one_way:
        log.info(f"Ida {trip['outbound_date']}")
    else:
        log.info(f"Ida {trip['outbound_date']} | Volta {return_date}")
    log.info("=" * 60)

    if is_one_way:
        data = client.search_one_way(
            trip["origin"], trip["destination"],
            trip["outbound_date"],
            adults=trip["passengers"], nonstop_only=False,
        )
    else:
        data = client.search_round_trip(
            trip["origin"], trip["destination"],
            trip["outbound_date"], return_date,
            adults=trip["passengers"], nonstop_only=False,
        )
    if not data:
        log.warning("SerpAPI nao retornou dados.")
        return 0

    all_flights = data.get("best_flights", []) + data.get("other_flights", [])
    log.info(f"Opcoes retornadas: {len(all_flights)}")

    saved = 0
    best_per_hub: dict[str, tuple[float, str]] = {}
    for f in all_flights:
        segments = f.get("flights", [])
        if not segments:
            continue
        layovers = f.get("layovers", [])
        # Hub principal: primeiro layover da ida (ou aeroporto de chegada do 1o segmento)
        if layovers:
            hub = layovers[0].get("id")
        elif len(segments) > 1:
            hub = segments[0].get("arrival_airport", {}).get("id")
        else:
            hub = None
        hub_label = hub or "DIRECT"

        price = f.get("price")
        if not price:
            continue

        airlines = []
        for s in segments:
            a = s.get("airline")
            if a and a not in airlines:
                airlines.append(a)
        airline_str = " + ".join(airlines) if airlines else None
        first_seg, last_seg = segments[0], segments[-1]

        storage.save_quote(
            source="travelpayouts",  # mantem compatibilidade com pipeline existente
            origin=trip["origin"], destination=trip["destination"],
            outbound_date=trip["outbound_date"], return_date=return_date,
            direct=(not layovers),
            price=float(price), currency=currency,
            airline=airline_str,
            flight_number=first_seg.get("flight_number"),
            departure_time=first_seg.get("departure_airport", {}).get("time"),
            arrival_time=last_seg.get("arrival_airport", {}).get("time"),
            duration_min=f.get("total_duration"),
            transfers=len(layovers),
            hub=hub_label,
            raw={"_via": "serpapi_round_trip", **f},
        )
        saved += 1
        if hub_label not in best_per_hub or price < best_per_hub[hub_label][0]:
            best_per_hub[hub_label] = (price, airline_str or "?")

    log.info(f"Cotacoes salvas: {saved}")
    log.info("Mais barato por hub:")
    from filters import currency_symbol
    sym = currency_symbol(currency)
    for h, (p, a) in sorted(best_per_hub.items(), key=lambda x: x[1][0]):
        log.info(f"  {h}: {sym} {p:>6,.0f}  ({a})")
    return saved


def run_serpapi_full_sweep(config, storage):
    """
    Sweep completo via SerpAPI Google Flights nos 12 hubs.
    Usado quando Travelpayouts free tier nao tem cache (datas distantes).
    As cotacoes sao salvas com source='travelpayouts' para que o pipeline
    de relatorios/alertas existente funcione sem alteracao (a coluna 'transfers'
    no raw_json indica que veio do Google Flights).
    """
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        log.warning("SERPAPI_KEY nao definida. Pulando full sweep SerpAPI.")
        return 0

    trip = config["trip"]
    hubs = config["hubs"]
    currency = config["monitoring"]["currency"]
    client = SerpApiFlightsClient(key, currency=currency)

    log.info("=" * 60)
    log.info("Camada A2: SerpAPI Full Sweep (12 hubs, Google Flights real)")
    log.info("=" * 60)

    saved = 0
    for i, hub in enumerate(hubs, start=1):
        hub_code = hub["code"]
        log.info(f"[{i}/12] Hub {hub_code} ({hub['city']})")

        for src, dst in [(trip["origin"], hub_code), (hub_code, trip["destination"])]:
            data = client.search_round_trip(
                src, dst, trip["outbound_date"], trip["return_date"],
                adults=trip["passengers"], nonstop_only=False)
            if not data:
                continue
            cheapest = SerpApiFlightsClient.extract_cheapest_flight(data)
            if not cheapest or not cheapest.get("price"):
                continue
            storage.save_quote(
                source="travelpayouts",
                origin=src, destination=dst,
                outbound_date=trip["outbound_date"], return_date=trip["return_date"],
                direct=(cheapest.get("layovers", 0) == 0),
                price=float(cheapest["price"]), currency=currency,
                airline=cheapest.get("airline"),
                flight_number=cheapest.get("flight_number"),
                departure_time=cheapest.get("departure_time"),
                arrival_time=cheapest.get("arrival_time"),
                duration_min=cheapest.get("total_duration"),
                transfers=cheapest.get("layovers", 0),
                raw={"_via": "serpapi_full_sweep", **cheapest},
            )
            saved += 1

    log.info(f"Full sweep concluido: {saved} cotacoes salvas via SerpAPI.")
    return saved


def run_serpapi_verification(config, storage):
    key = os.environ.get("SERPAPI_KEY")
    if not key:
        log.warning("SERPAPI_KEY nao definida. Pulando verificacao semanal.")
        return 0

    trip = config["trip"]
    top_n = config["monitoring"].get("top_n_for_serpapi", 3)
    currency = config["monitoring"]["currency"]

    log.info("=" * 60)
    log.info(f"Camada B: SerpAPI Google Flights (verificacao dos top {top_n})")
    log.info("=" * 60)

    combos = _compute_top_combinations(storage, config, top_n=top_n)
    if not combos:
        log.warning("Sem combinacoes para verificar.")
        return 0

    client = SerpApiFlightsClient(key, currency=currency)
    verified = 0
    for combo in combos:
        hub = combo["hub"]
        log.info(f"Verificando GRU<->{hub}...")
        data1 = client.search_round_trip(
            trip["origin"], hub, trip["outbound_date"], trip["return_date"],
            adults=trip["passengers"], nonstop_only=True)
        if data1:
            c = SerpApiFlightsClient.extract_cheapest_flight(data1)
            if c:
                storage.save_quote(
                    source="serpapi", origin=trip["origin"], destination=hub,
                    outbound_date=trip["outbound_date"], return_date=trip["return_date"],
                    direct=True, price=float(c.get("price") or 0.0), currency=currency,
                    airline=c.get("airline"), flight_number=c.get("flight_number"),
                    departure_time=c.get("departure_time"),
                    duration_min=c.get("total_duration"), transfers=c.get("layovers", 0),
                    raw=c)
                verified += 1

        log.info(f"Verificando {hub}<->KEF...")
        data2 = client.search_round_trip(
            hub, trip["destination"], trip["outbound_date"], trip["return_date"],
            adults=trip["passengers"], nonstop_only=True)
        if data2:
            c = SerpApiFlightsClient.extract_cheapest_flight(data2)
            if c:
                storage.save_quote(
                    source="serpapi", origin=hub, destination=trip["destination"],
                    outbound_date=trip["outbound_date"], return_date=trip["return_date"],
                    direct=True, price=float(c.get("price") or 0.0), currency=currency,
                    airline=c.get("airline"), flight_number=c.get("flight_number"),
                    departure_time=c.get("departure_time"),
                    duration_min=c.get("total_duration"), transfers=c.get("layovers", 0),
                    raw=c)
                verified += 1

    log.info(f"SerpAPI: {verified} cotacoes verificadas")
    return verified


def main():
    parser = argparse.ArgumentParser(description="Flight Monitor GRU<->KEF")
    parser.add_argument("--serpapi", action="store_true",
                        help="Forca verificacao SerpAPI top-N")
    parser.add_argument("--no-serpapi", action="store_true",
                        help="Pula verificacao SerpAPI top-N")
    parser.add_argument("--serpapi-full", action="store_true",
                        help="Forca SerpAPI full sweep (12 hubs)")
    parser.add_argument("--report-only", action="store_true",
                        help="Apenas regera relatorios")
    args = parser.parse_args()

    load_dotenv()
    config = load_config()
    storage = Storage(str(DB_PATH))

    log.info(f"Flight Monitor iniciando em {datetime.utcnow().isoformat()} UTC")
    log.info(f"Banco: {DB_PATH}")
    log.info(f"Relatorios: {REPORTS_DIR}")

    if not args.report_only:
        # Busca primaria: 1 search SerpAPI GRU<->KEF round-trip, retorna varias opcoes
        # com diferentes hubs de conexao. Roda em todos os sweeps (1 search = ~90/mes).
        run_serpapi_round_trip_search(config, storage)

    log.info("")
    log.info("Gerando relatorios...")
    excel_path = REPORTS_DIR / "flights.xlsx"
    html_path = REPORTS_DIR / "index.html"
    report.generate_excel(storage, config, str(excel_path))
    report.generate_html(storage, config, str(html_path))
    log.info(f"Excel: {excel_path}")
    log.info(f"HTML:  {html_path}")

    log.info("")
    log.info("Verificando alertas...")
    sent = notify.check_and_alert(storage, config)
    log.info(f"Alertas enviados: {sent}")

    log.info("Concluido.")


if __name__ == "__main__":
    main()
