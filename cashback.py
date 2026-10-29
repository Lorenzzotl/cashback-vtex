# -*- coding: utf-8 -*-
"""Regras do cashback: do pedido pago ao cupom criado na VTEX.

Fluxo (disparado pelo hook quando o pedido fica `payment-approved`):
  1. Lê a configuração do painel (percentual, janelas, mínimos).
  2. Busca o pedido na VTEX e calcula a base (produtos − descontos, sem frete).
  3. Calcula o valor do cupom (percentual sobre a base) e o valor mínimo da
     compra em que ele poderá ser usado (valor ÷ percentual máximo).
  4. Monta o código: PREFIXO-NOME-NÚMERODOPEDIDO (remete ao pedido e ao
     cliente sem depender do e-mail).
  5. Cria na VTEX a promoção nominal (com vigência e valor mínimo) e o
     cupom de uso único amarrado a ela.
  6. Registra tudo no armazém para o painel e para não duplicar.
"""
import math
import os
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import armazem
import vtex
from util import inteiro, log, nome_para_codigo, numero, parse_data_vtex

FORMATO_VTEX = "%Y-%m-%dT%H:%M:%SZ"


def fuso():
    return ZoneInfo(os.environ.get("FUSO", "America/Sao_Paulo"))


# ------------------------------------------------------------------ cálculo

def valor_base(pedido):
    """Base do cashback em reais: produtos menos descontos, sem frete."""
    totais = {t.get("id"): t.get("value") or 0
              for t in (pedido.get("totals") or [])}
    centavos = totais.get("Items", 0) + totais.get("Discounts", 0)
    return round(centavos / 100.0, 2)


def montar_codigo(prefixo, pedido):
    nome = ((pedido.get("clientProfileData") or {}).get("firstName")
            or "").strip()
    numero_pedido = "".join(c for c in (pedido.get("orderId") or "")
                            if c.isalnum()).upper()
    return f"{prefixo}-{nome_para_codigo(nome)}-{numero_pedido}", nome


def janela_vigencia(cfg, compra_dt):
    """Devolve (inicio, fim) como datetimes com fuso, ou (None, motivo)."""
    if cfg["modo"] == "fixo":
        try:
            ini_d = datetime.strptime(cfg["fixo_inicio"], "%Y-%m-%d").date()
            fim_d = datetime.strptime(cfg["fixo_fim"], "%Y-%m-%d").date()
        except ValueError:
            return None, ("modo fixo sem datas de vigência — preencha "
                          "'vale a partir de' e 'vale até' no painel")
        tz = fuso()
        inicio = datetime.combine(ini_d, time.min, tzinfo=tz)
        fim = datetime.combine(fim_d, time(23, 59, 59), tzinfo=tz)
        # janela de geração (ex.: só compras de novembro ganham cupom)
        compra_dia = compra_dt.astimezone(tz).date()
        for chave, apos in (("fixo_gerar_inicio", False), ("fixo_gerar_fim", True)):
            if not cfg[chave]:
                continue
            try:
                limite = datetime.strptime(cfg[chave], "%Y-%m-%d").date()
            except ValueError:
                continue
            if (apos and compra_dia > limite) or (not apos and compra_dia < limite):
                return None, (f"compra de {compra_dia} fora da janela de "
                              "geração configurada no painel")
    else:
        libera = inteiro(cfg["dias_liberar"], 15)
        validade = inteiro(cfg["dias_validade"], 60)
        if validade <= libera:
            return None, ("configuração inválida: 'expira em' deve ser maior "
                          "que 'libera em' (dias após a compra)")
        inicio = compra_dt + timedelta(days=libera)
        fim = compra_dt + timedelta(days=validade)
    if fim <= datetime.now(timezone.utc):
        return None, "a vigência configurada já terminou — cupom não gerado"
    return (inicio, fim), None


# ------------------------------------------------------------------ geração

def processar(order_id, estado="payment-approved", forcar=False):
    """Gera o cupom de cashback de um pedido pago. Idempotente."""
    cfg = armazem.ler_config()
    if cfg["ativo"] != "1" and not forcar:
        log(f"pedido {order_id}: geração de cashback DESLIGADA no painel — ignorado")
        return {"ignorado": "cashback desligado no painel"}

    ja = armazem.cupom_do_pedido(order_id)
    if ja:
        log(f"pedido {order_id}: cupom {ja} já havia sido gerado — ignorado")
        return {"ignorado": f"cupom {ja} já gerado para este pedido"}

    try:
        pedido = vtex.get_order(order_id)
    except Exception as e:  # noqa: BLE001
        log(f"ERRO ao buscar pedido {order_id} na VTEX: {e}")
        return {"erro": f"falha ao buscar o pedido na VTEX: {e}"}

    base = valor_base(pedido)
    if base <= 0:
        log(f"pedido {order_id}: base de cálculo zerada — ignorado")
        return {"ignorado": "pedido sem valor de produtos"}

    minimo_gerar = numero(cfg["pedido_minimo"])
    if minimo_gerar > 0 and base < minimo_gerar:
        log(f"pedido {order_id}: base R$ {base:.2f} abaixo do mínimo "
            f"R$ {minimo_gerar:.2f} — ignorado")
        return {"ignorado": f"compra abaixo do mínimo de R$ {minimo_gerar:.2f}"}

    percentual = numero(cfg["percentual"], 15)
    valor = round(base * percentual / 100.0, 2)
    if valor < 0.01:
        return {"ignorado": "valor de cashback menor que 1 centavo"}

    pct_max = numero(cfg["percentual_maximo"])
    minimo_uso = (math.ceil(valor / (pct_max / 100.0) * 100) / 100.0
                  if pct_max > 0 else 0.0)

    compra_dt = parse_data_vtex(pedido.get("creationDate"))
    janela, motivo = janela_vigencia(cfg, compra_dt)
    if not janela:
        log(f"pedido {order_id}: {motivo}")
        return {"ignorado": motivo}
    inicio, fim = janela

    codigo, nome = montar_codigo(cfg["prefixo"] or "CASH", pedido)
    if vtex.cupom_existe(codigo):
        log(f"pedido {order_id}: cupom {codigo} já existe na VTEX — "
            "registrando localmente sem recriar")
        armazem.registrar_cupom(order_id, codigo, nome, valor, minimo_uso,
                                inicio.isoformat(), fim.isoformat())
        return {"ignorado": f"cupom {codigo} já existia na VTEX"}

    inicio_utc = inicio.astimezone(timezone.utc).strftime(FORMATO_VTEX)
    fim_utc = fim.astimezone(timezone.utc).strftime(FORMATO_VTEX)
    nome_promocao = f"Cashback {codigo}"
    descricao = (f"Cashback de {percentual:g}% do pedido {order_id} "
                 f"({nome or 'cliente'}). Gerado automaticamente.")

    r = vtex.criar_promocao(nome_promocao, descricao, codigo, valor,
                            minimo_uso, inicio_utc, fim_utc)
    if r.status_code not in (200, 201):
        log(f"ERRO ao criar promoção do pedido {order_id}: "
            f"{r.status_code} {r.text[:300]}")
        return {"erro": f"VTEX recusou a promoção ({r.status_code}): {r.text[:300]}"}
    id_promocao = str((r.json() or {}).get("idCalculatorConfiguration") or "")

    r2 = vtex.criar_cupom(codigo)
    if r2.status_code not in (200, 201, 204):
        log(f"ERRO ao criar cupom {codigo} (promoção {id_promocao} criada): "
            f"{r2.status_code} {r2.text[:300]}")
        return {"erro": f"promoção criada ({id_promocao}) mas a VTEX recusou "
                        f"o cupom ({r2.status_code}): {r2.text[:300]}"}

    armazem.registrar_cupom(order_id, codigo, nome, valor, minimo_uso,
                            inicio.isoformat(), fim.isoformat(), id_promocao)
    log(f"pedido {order_id}: cupom {codigo} criado — R$ {valor:.2f}"
        + (f", compra mínima R$ {minimo_uso:.2f}" if minimo_uso else "")
        + f", vale de {inicio_utc} a {fim_utc} (promoção {id_promocao})")
    return {"ok": True, "cupom": codigo, "valor": valor,
            "compra_minima": minimo_uso, "inicio": inicio.isoformat(),
            "fim": fim.isoformat(), "id_promocao": id_promocao}


# ------------------------------------------------------------------ cancelamento

def cancelar(order_id):
    """Pedido cancelado na VTEX => desativa a promoção e arquiva o cupom."""
    dados = armazem.dados_cupom(order_id)
    if not dados:
        log(f"pedido {order_id} cancelado: nenhum cupom gerado — nada a fazer")
        return {"ignorado": "pedido sem cupom de cashback"}
    if dados["status"] == "cancelado":
        log(f"pedido {order_id} cancelado: cupom {dados['codigo']} "
            "já estava cancelado")
        return {"ignorado": f"cupom {dados['codigo']} já cancelado"}

    codigo = dados["codigo"]
    erros = []

    if dados["id_promocao"]:
        try:
            r = vtex.desativar_promocao(dados["id_promocao"])
            if r.status_code not in (200, 201, 204):
                erros.append(f"promoção {dados['id_promocao']}: "
                             f"{r.status_code} {r.text[:200]}")
        except Exception as e:  # noqa: BLE001
            erros.append(f"promoção {dados['id_promocao']}: {e}")
    else:
        erros.append("cupom sem id de promoção registrado — desative a "
                     "promoção manualmente no admin da VTEX")

    try:
        r = vtex.arquivar_cupom(codigo)
        if r.status_code not in (200, 201, 204):
            erros.append(f"cupom {codigo}: {r.status_code} {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        erros.append(f"cupom {codigo}: {e}")

    if erros:
        log(f"pedido {order_id} cancelado: falha ao cancelar o cashback — "
            + "; ".join(erros))
        return {"erro": "; ".join(erros)}

    armazem.marcar_cancelado(order_id)
    log(f"pedido {order_id} cancelado: cupom {codigo} arquivado e "
        f"promoção {dados['id_promocao']} desativada")
    return {"ok": True, "cupom_cancelado": codigo}
