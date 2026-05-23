"""
Cliente para SerpAPI Google Flights.

Endpoint: GET https://serpapi.com/search?engine=google_flights
Documentação: https://serpapi.com/google-flights-api

Free tier: 100 buscas/mês. Por isso esta camada é usada apenas SEMANALMENTE
para validar os top 3 candidatos contra o que aparece de verdade no Google.
"""

import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://serpapi.com/search"

# SerpAPI Google Flights usa códigos numéricos para "stops":
# 0 = qualquer, 1 = nonstop only, 2 = 1 stop or less, 3 = 2 stops or less
STOPS_NONSTOP = 1


class SerpApiFlightsClient:
    def __init__(self, api_key: str, currency: str = "BRL", timeout: int = 60):
        if not api_key:
            raise ValueError("SerpAPI key é obrigatória")
        self.api_key = api_key
        self.currency = currency
        self.timeout = timeout

    def search_round_trip(
        self,
        departure_id: str,
        arrival_id: str,
        outbound_date: str,
        return_date: str,
        adults: int = 1,
        nonstop_only: bool = True,
    ) -> Optional[dict]:
        """
        Busca round-trip no Google Flights via SerpAPI.

        Retorna o JSON completo (com chaves 'best_flights' e 'other_flights'),
        ou None em caso de erro.
        """
        params = {
            "engine": "google_flights",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "return_date": return_date,
            "currency": self.currency,
            "hl": "pt-br",
            "gl": "br",
            "adults": adults,
            "type": 1,  # 1 = round trip
            "api_key": self.api_key,
        }
        if nonstop_only:
            params["stops"] = STOPS_NONSTOP

        return self._do_request(params, departure_id, arrival_id)

    def search_one_way(
        self,
        departure_id: str,
        arrival_id: str,
        outbound_date: str,
        adults: int = 1,
        nonstop_only: bool = True,
    ) -> Optional[dict]:
        """
        Busca one-way no Google Flights via SerpAPI (type=2).
        """
        params = {
            "engine": "google_flights",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "currency": self.currency,
            "hl": "pt-br",
            "gl": "br",
            "adults": adults,
            "type": 2,
            "api_key": self.api_key,
        }
        if nonstop_only:
            params["stops"] = STOPS_NONSTOP

        return self._do_request(params, departure_id, arrival_id)

    def _do_request(self, params: dict, departure_id: str, arrival_id: str) -> Optional[dict]:
        try:
            r = requests.get(BASE_URL, params=params, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                log.warning(
                    f"SerpAPI retornou erro para {departure_id}->{arrival_id}: "
                    f"{data['error']}"
                )
                return None
            best = len(data.get("best_flights", []))
            other = len(data.get("other_flights", []))
            log.info(
                f"  SerpAPI {departure_id}->{arrival_id}: "
                f"{best} best + {other} other"
            )
            return data
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                log.error("SerpAPI: chave inválida (401)")
            else:
                log.error(f"SerpAPI HTTP {e.response.status_code}: {e}")
            return None
        except requests.RequestException as e:
            log.error(f"SerpAPI erro de rede: {e}")
            return None

    @staticmethod
    def extract_cheapest_flight(data: dict) -> Optional[dict]:
        """
        Extrai o voo mais barato de uma resposta da SerpAPI.
        Retorna dict normalizado com chaves: price, airline, total_duration,
        departure_time, arrival_time, layovers, booking_token.
        """
        if not data:
            return None

        # 'best_flights' já vem ordenado pela Google como "melhor" (mistura preço/duração).
        # Para "mais barato" estrito, pegamos o mínimo de price em best + other.
        all_flights = data.get("best_flights", []) + data.get("other_flights", [])
        if not all_flights:
            return None

        cheapest = min(all_flights, key=lambda f: f.get("price", float("inf")))

        # Estrutura típica de um item: 'flights' (lista de segmentos), 'price', 'total_duration'
        segments = cheapest.get("flights", [])
        first_seg = segments[0] if segments else {}
        last_seg = segments[-1] if segments else {}

        return {
            "price": cheapest.get("price"),
            "total_duration": cheapest.get("total_duration"),
            "airline": first_seg.get("airline"),
            "flight_number": first_seg.get("flight_number"),
            "departure_time": first_seg.get("departure_airport", {}).get("time"),
            "arrival_time": last_seg.get("arrival_airport", {}).get("time"),
            "layovers": len(cheapest.get("layovers", [])),
            "booking_token": cheapest.get("booking_token"),
        }
