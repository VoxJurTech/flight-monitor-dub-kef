"""
Notificacoes por e-mail via SMTP do Gmail.

Regra de disparo: para cada hub de conexao com cotacao atual, compara o preco
do ultimo sweep com o minimo historico para o mesmo hub (excluindo o sweep atual).
Dispara alerta quando:
  - E' novo minimo historico para esse hub, OU
  - Caiu >= alert_min_drop_pct% comparado ao minimo historico

De-duplicacao: mesmo alerta para o mesmo hub nao e' re-enviado nas ultimas 12h.
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from filters import in_arrival_window, has_window, window_label

log = logging.getLogger(__name__)


def check_and_alert(storage, config: dict) -> int:
    """Verifica condicoes de alerta e envia e-mails. Retorna quantos foram enviados."""
    trip = config["trip"]
    hubs_by_code = {h["code"]: h for h in config["hubs"]}
    threshold_pct = config["monitoring"].get("alert_min_drop_pct", 10)
    arrival_window = (config.get("monitoring") or {}).get("arrival_window")

    recipient = os.environ.get("ALERT_RECIPIENT") or config["monitoring"].get("alert_email") or ""
    gmail_user = os.environ.get("GMAIL_USER")
    gmail_pwd = os.environ.get("GMAIL_APP_PASSWORD")

    if not (gmail_user and gmail_pwd and recipient):
        log.warning("Credenciais Gmail ou destinatario nao definidos. Pulando alertas.")
        return 0

    latest = storage.get_latest_by_hub(
        trip["origin"], trip["destination"],
        trip["outbound_date"], trip["return_date"],
    )

    # Filtra por janela de chegada quando configurada
    if has_window(config):
        before = len(latest)
        latest = [q for q in latest if in_arrival_window(q.get("arrival_time"), arrival_window)]
        log.info(f"Janela chegada {window_label(config)}: {len(latest)}/{before} voos elegiveis para alerta")

    alerts_to_send = []
    for q in latest:
        hub = q.get("hub")
        if not hub:
            continue
        current = q["price"]
        route_key = f'{trip["origin"]}-{hub}-{trip["destination"]}-{trip["outbound_date"]}-{trip["return_date"]}'

        # Minimo historico para esse hub, excluindo a ultima hora (sweep atual)
        hist_min = _historical_min_excluding_recent(storage, trip, hub)

        reason = None
        if hist_min is None:
            continue  # primeira cotacao, sem base de comparacao
        elif current < hist_min:
            reason = f"Novo minimo historico! Antes: R$ {hist_min:,.2f} -> agora: R$ {current:,.2f}"
        else:
            drop_pct = ((hist_min - current) / hist_min) * 100
            if drop_pct >= threshold_pct:
                reason = (f"Queda de {drop_pct:.1f}% (minimo anterior R$ {hist_min:,.2f}, "
                          f"agora R$ {current:,.2f})")

        if reason:
            if storage.get_alert_count_recent(route_key, hours=12) > 0:
                log.info(f"  {hub}: alerta similar nas ultimas 12h, pulando")
                continue
            alerts_to_send.append({
                "hub": hub,
                "city": hubs_by_code.get(hub, {}).get("city", ""),
                "total": current,
                "hist_min": hist_min,
                "airline": q.get("airline") or "?",
                "transfers": q.get("transfers") or 0,
                "duration_min": q.get("duration_min") or 0,
                "reason": reason,
                "route_key": route_key,
            })

    if not alerts_to_send:
        log.info("Nenhum alerta para enviar.")
        return 0

    subject = f'[Flight Monitor] {len(alerts_to_send)} alerta(s) {trip["origin"]}<->{trip["destination"]}'
    body = _build_email_body(alerts_to_send, config)

    try:
        _send_email(gmail_user, gmail_pwd, recipient, subject, body)
        log.info(f"E-mail enviado para {recipient} com {len(alerts_to_send)} alerta(s)")
        for a in alerts_to_send:
            storage.save_alert(a["route_key"], a["total"], a["reason"])
        return len(alerts_to_send)
    except Exception as e:
        log.error(f"Falha ao enviar e-mail: {e}")
        return 0


def _historical_min_excluding_recent(storage, trip, hub: str):
    """Minimo historico para esse hub, excluindo cotacoes da ultima hora (sweep atual)."""
    import sqlite3
    from datetime import datetime, timedelta
    cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat(timespec="seconds")
    with sqlite3.connect(storage.db_path) as c:
        cur = c.execute(
            """
            SELECT MIN(price) FROM quotes
            WHERE source = 'travelpayouts'
              AND origin = ? AND destination = ?
              AND outbound_date = ? AND COALESCE(return_date,'') = COALESCE(?,'')
              AND hub = ?
              AND fetched_at < ?
            """,
            (trip["origin"], trip["destination"],
             trip["outbound_date"], trip["return_date"], hub, cutoff),
        )
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else None


def _build_email_body(alerts: list[dict], config: dict) -> str:
    trip = config["trip"]
    dashboard_url = config["monitoring"].get("dashboard_url", "")
    return_date = trip.get("return_date") or None
    is_one_way = not return_date
    arrow = "&rarr;" if is_one_way else "&harr;"
    trip_kind = "one-way" if is_one_way else "round-trip"
    if is_one_way:
        viagem_str = f"Viagem (ida): <strong>{trip['outbound_date']}</strong>, "
    else:
        viagem_str = (f"Viagem: <strong>{trip['outbound_date']}</strong> a "
                      f"<strong>{return_date}</strong>, ")
    window = (config.get("monitoring") or {}).get("arrival_window") or {}
    window_note = ""
    if window.get("start") and window.get("end"):
        window_note = (
            f' <span style="color:#92400e;">(apenas voos com chegada em '
            f'<strong>{trip["destination"]}</strong> entre '
            f'<strong>{window["start"]}-{window["end"]}</strong> hora local)</span>'
        )
    rows_html = ""
    for a in alerts:
        h, m = divmod(int(a["duration_min"] or 0), 60)
        dur = f"{h}h{m:02d}" if a["duration_min"] else "-"
        rows_html += f"""
        <tr>
          <td><strong>{a['hub']}</strong> ({a['city'] or '-'})</td>
          <td>R$ {a['total']:,.2f}</td>
          <td>{a['airline']}</td>
          <td>{dur}</td>
          <td>{a['transfers']} escala(s)</td>
          <td style="color:#16a34a;"><strong>{a['reason']}</strong></td>
        </tr>
        """

    dashboard_button = ""
    dashboard_footer = ""
    if dashboard_url:
        dashboard_button = f"""
<p style="text-align:center; margin: 20px 0;">
  <a href="{dashboard_url}"
     style="display:inline-block; background:#1e40af; color:white; padding:12px 28px;
            text-decoration:none; border-radius:6px; font-weight:600;">
    Ver dashboard completo
  </a>
</p>"""
        dashboard_footer = f"""
<p style="color:#6b7280; font-size:12px; margin-top:24px; border-top:1px solid #e5e7eb; padding-top:12px;">
Dashboard ao vivo: <a href="{dashboard_url}">{dashboard_url}</a>
</p>"""

    return f"""\
<html>
<body style="font-family: Arial, sans-serif; color: #1f2937; max-width: 720px;">
<h2 style="color: #1e40af;">Alerta de Voo {trip['origin']} {arrow} {trip['destination']}</h2>
<p>{viagem_str}{trip['passengers']} adulto(s) economica com bagagem.{window_note}</p>
{dashboard_button}
<p>Os hubs abaixo tiveram queda significativa de preco ({trip_kind} {trip['origin']}-{trip['destination']}):</p>

<table cellpadding="8" cellspacing="0" border="0" style="border-collapse: collapse; width: 100%;">
  <thead>
    <tr style="background:#1e40af; color:white;">
      <th align="left">Hub</th>
      <th align="left">Preco total</th>
      <th align="left">Companhia</th>
      <th align="left">Duracao</th>
      <th align="left">Escalas</th>
      <th align="left">Motivo</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>

<p style="margin-top:20px; padding:12px; background:#fef3c7; border-left:4px solid #f59e0b;">
<strong>Proximo passo:</strong> confira no
<a href="https://www.google.com/travel/flights?q=Flights%20from%20{trip['origin']}%20to%20{trip['destination']}">Google Flights</a>
e compre direto la se bater.
</p>
{dashboard_footer}
</body>
</html>
"""


def _send_email(user: str, password: str, recipient: str, subject: str, body_html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = recipient
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)
