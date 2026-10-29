# -*- coding: utf-8 -*-
"""Cliente das APIs da VTEX: pedidos (OMS), Hook, Promoções e Cupons."""
import os

import requests

from util import log


def _conta():
    return os.environ["VTEX_ACCOUNT"].strip()


def _base():
    return f"https://{_conta()}.vtexcommercestable.com.br"


def _headers():
    return {
        "X-VTEX-API-AppKey": os.environ["VTEX_APP_KEY"].strip(),
        "X-VTEX-API-AppToken": os.environ["VTEX_APP_TOKEN"].strip(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# ------------------------------------------------------------------ pedidos (OMS)

def get_order(order_id):
    r = requests.get(f"{_base()}/api/oms/pvt/orders/{order_id}",
                     headers=_headers(), timeout=20)
    r.raise_for_status()
    return r.json()


def listar_pedidos(per_page=1):
    r = requests.get(f"{_base()}/api/oms/pvt/orders",
                     headers=_headers(),
                     params={"per_page": per_page,
                             "orderBy": "creationDate,desc"},
                     timeout=20)
    r.raise_for_status()
    return (r.json() or {}).get("list") or []


# ------------------------------------------------------------------ hook de pedidos

def registrar_hook(url, secret, statuses):
    corpo = {
        "filter": {"type": "FromWorkflow", "status": statuses},
        "hook": {"url": url, "headers": {"x-hook-secret": secret}},
    }
    return requests.post(f"{_base()}/api/orders/hook/config",
                         headers=_headers(), json=corpo, timeout=30)


def hook_atual():
    return requests.get(f"{_base()}/api/orders/hook/config",
                        headers=_headers(), timeout=20)


# ------------------------------------------------------------------ promoções

def criar_promocao(nome, descricao, codigo, valor, minimo,
                   inicio_utc, fim_utc):
    """Cria a promoção nominal (valor em reais) amarrada ao cupom.

    A promoção só dispara quando o carrinho carrega o utm_source E o
    utm_campaign do cupom — exatamente o que o Checkout grava quando o
    cliente digita o código. `totalValueFloor` é o valor mínimo da compra.
    """
    corpo = {
        "name": nome,
        "description": descricao,
        "beginDateUtc": inicio_utc,
        "endDateUtc": fim_utc,
        "isActive": True,
        "isArchived": False,
        "isFeatured": False,
        "type": "regular",
        "utmSource": codigo,
        "utmCampain": codigo,  # grafia errada é da própria API da VTEX
        "nominalDiscountValue": valor,
        "percentualDiscountValue": 0,
        "maximumUnitPriceDiscount": 0,
        "nominalShippingDiscountValue": 0,
        "absoluteShippingDiscountValue": 0,
        "percentualShippingDiscountValue": 0,
        "nominalRewardValue": 0,
        "percentualRewardValue": 0,
        "totalValueFloor": minimo,
        "totalValueCeling": 0,
        "cumulative": False,
        "maxNumberOfAffectedItems": 0,
        "activeDaysOfWeek": [],
        "activateGiftsMultiplier": False,
    }
    r = requests.post(f"{_base()}/api/rnb/pvt/calculatorconfiguration",
                      headers=_headers(), json=corpo, timeout=30)
    return r


def listar_promocoes():
    return requests.get(
        f"{_base()}/api/rnb/pvt/benefits/calculatorconfiguration",
        headers=_headers(), timeout=30)


def desativar_promocao(id_promocao):
    """Arquiva uma promoção (usado só em manutenção manual)."""
    r = requests.get(f"{_base()}/api/rnb/pvt/calculatorconfiguration/{id_promocao}",
                     headers=_headers(), timeout=30)
    if r.status_code != 200:
        return r
    corpo = r.json() or {}
    corpo["isActive"] = False
    corpo["isArchived"] = True
    return requests.post(f"{_base()}/api/rnb/pvt/calculatorconfiguration",
                         headers=_headers(), json=corpo, timeout=30)


# ------------------------------------------------------------------ cupons

def criar_cupom(codigo, max_usos=1):
    corpo = {
        "utmSource": codigo,
        "utmCampaign": codigo,
        "couponCode": codigo,
        "isArchived": False,
        "maxItemsPerClient": max_usos,
        "expirationIntervalPerUse": "00:00:00",
        "maxUsage": max_usos,
    }
    return requests.post(f"{_base()}/api/rnb/pvt/coupon",
                         headers=_headers(), json=corpo, timeout=30)


def cupom_existe(codigo):
    try:
        r = requests.get(f"{_base()}/api/rnb/pvt/coupon/{codigo}",
                         headers=_headers(), timeout=20)
    except requests.RequestException as e:  # noqa: BLE001
        log(f"aviso: falha ao consultar cupom {codigo}: {e}")
        return False
    if r.status_code != 200:
        return False
    corpo = r.json() if r.text else None
    return bool(corpo)
