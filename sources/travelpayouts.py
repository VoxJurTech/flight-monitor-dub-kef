"""
Cliente para a API Travelpayouts/Aviasales V3.

Endpoint principal: GET https://api.travelpayouts.com/aviasales/v3/prices_for_dates
Documentação: https://support.travelpayouts.com/hc/en-us/articles/360022660294
"""

import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com/aviasales/v3"


class TravelpayoutsClient:
    def __init__(self, token: str, currency: str = "brl", timeout: int = 30):
        if not token:
            raise ValueError("Travelpayouts token é obrigatório")
        self.token = token
        self.currency = currency.lower()
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "FlightMonitor/1.0 (+github.com/voxjur/flight-monitor)",
            "Accept": "application/json",
        })

    def get_prices_for_dates(
        self,
        origin: str,
        destination: str,
        departure_at: str,
        return_at: Optional[str] = None,
        direct: bool = True,
        limit: int = 30,
        retries: int = 2,
    ) -> list[dict]:
        """
        Busca preços para uma rota em datas específicas.

        Retorna lista de dicts; cada dict tem chaves típicas:
        - price (float, na moeda solicitada)
        - airline (código IATA da companhia)
        - flight_number
        - departure_at (ISO 8601 com timezone)
        - return_at (ISO 8601, se round-trip)
        - duration (minutos, ida)
        - duration_to, duration_back (minutos por trecho)
        - transfers, return_transfers (número de escalas)
        - link (URL relativa para o motor de busca da Aviasales)
        """
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": departure_at,
            "unique": "false",
            "sorting": "price",
            "direct": "true" if direct else "false",
            "currency": self.currency,
            "limit": limit,
            "token": self.token,
        }
        if return_at:
            params["return_at"] = return_at

        url = f"{BASE_URL}/prices_for_dates"

        last_err = None
        for attempt in range(retries + 1):
            try:
                r = self.session.get(url, params=params, timeout=self.timeout)
                if r.status_code == 429:
                    # Rate limit — espera e tenta de novo
                    wait = 5 * (attempt + 1)
                    log.warning(
                        f"Rate limit em {origin}->{destination}, aguardando {wait}s..."
                    )
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                data = r.json()
                if not data.get("success", False):
                    log.warning(
                        f"API retornou success=false para {origin}->{destination}: "
                        f"{data.get('error', 'sem detalhes')}"
                    )
                    return []
                results = data.get("data", [])
                log.info(
                    f"  {origin}->{destination} "
                    f"({'ida+volta' if return_at else 'só ida'}, "
                    f"{'direto' if direct else 'qualquer'}): "
                    f"{len(results)} resultados"
                )
                return results
            except requests.HTTPError as e:
                last_err = e
                log.warning(
                    f"HTTP {e.response.status_code} em {origin}->{destination} "
                    f"(tentativa {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    time.sleep(2 ** attempt)
            except requests.RequestException as e:
                last_err = e
                log.warning(
                    f"Erro de rede em {origin}->{destination}: {e} "
                    f"(tentativa {attempt + 1}/{retries + 1})"
                )
                if attempt < retries:
                    time.sleep(2 ** attempt)

        log.error(f"Falhou {origin}->{destination} após {retries + 1} tentativas: {last_err}")
        return []

    def build_booking_url(self, link: str) -> str:
        """Monta URL completa de booking a partir do 'link' relativo do retorno."""
        if not link:
            return ""
        if link.startswith("http"):
            return link
        return f"https://www.aviasales.com{link}"
