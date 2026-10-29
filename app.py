# -*- coding: utf-8 -*-
"""Cashback VTEX — gera cupom de cashback a cada pedido pago.

Rotas:
  GET  /                               verificação de vida
  POST /vtex/hook                      recebe os eventos de pedido da VTEX
  GET  /painel?token=                  painel de configuração + cupons gerados
  POST /painel                         salva a configuração do painel
  GET  /admin/diagnostico?token=       testa credenciais, permissões e banco
  GET  /admin/registrar-hook?token=    registra o hook de pedidos na VTEX
  GET  /admin/hook?token=              mostra a configuração atual do hook
  GET  /admin/gerar?token=&pedido=ID   gera o cupom de um pedido manualmente
  GET  /admin/cupons?token=            lista os cupons gerados (JSON)

O `token` é o valor da Config Var HOOK_SECRET.
"""
import os
import threading

from flask import Flask, jsonify, redirect, render_template, request

import armazem
import cashback
import vtex
from util import log, numero

app = Flask(__name__)

try:
    armazem.preparar()
except Exception as e:  # noqa: BLE001
    log(f"aviso: não preparei o banco ainda: {e}")


def _token_ok():
    token = request.args.get("token") or request.form.get("token")
    return bool(token) and token == os.environ.get("HOOK_SECRET")


@app.get("/")
def raiz():
    return "ok", 200


# ------------------------------------------------------------------ hook da VTEX

@app.post("/vtex/hook")
def hook_vtex():
    if request.headers.get("x-hook-secret") != os.environ.get("HOOK_SECRET"):
        return jsonify({"erro": "nao autorizado"}), 401
    corpo = request.get_json(silent=True) or {}

    # ping que a VTEX envia na hora de registrar o hook
    if corpo.get("hookConfig") == "ping":
        log("ping de configuração da VTEX recebido — hook validado")
        return jsonify({"pong": True}), 200

    order_id = corpo.get("OrderId") or corpo.get("orderId")
    estado = corpo.get("State") or corpo.get("state") or "?"
    if not order_id:
        return jsonify({"ignorado": True}), 200

    log(f"evento: pedido {order_id} -> {estado}")
    if estado != "payment-approved":
        return jsonify({"ignorado": f"status {estado}"}), 200

    # responde 200 na hora (a VTEX exige resposta rápida) e processa depois
    threading.Thread(target=cashback.processar,
                     args=(order_id, estado), daemon=True).start()
    return jsonify({"ok": True}), 200


# ------------------------------------------------------------------ painel

@app.get("/painel")
def painel():
    if not _token_ok():
        return "token inválido — use /painel?token=SEU_HOOK_SECRET", 401
    cfg = armazem.ler_config()
    try:
        cupons = armazem.listar_cupons(100)
    except Exception as e:  # noqa: BLE001
        log(f"aviso: não li os cupons: {e}")
        cupons = []
    exemplo = _exemplo(cfg)
    return render_template("painel.html", cfg=cfg, cupons=cupons,
                           exemplo=exemplo, token=request.args.get("token"),
                           salvo=request.args.get("salvo") == "1")


@app.post("/painel")
def painel_salvar():
    if not _token_ok():
        return "token inválido", 401
    campos = {}
    for chave in armazem.PADROES:
        if chave == "ativo":
            campos["ativo"] = "1" if request.form.get("ativo") else "0"
        elif chave in request.form:
            campos[chave] = request.form.get(chave, "").strip()
    if "prefixo" in campos:
        campos["prefixo"] = "".join(
            c for c in campos["prefixo"].upper() if c.isalnum()) or "CASH"
    armazem.salvar_config(campos)
    log(f"painel: configuração salva ({campos})")
    return redirect(f"/painel?token={request.form.get('token')}&salvo=1")


def _exemplo(cfg):
    """Texto de exemplo exibido no painel com os números atuais."""
    pct = numero(cfg["percentual"], 15)
    pct_max = numero(cfg["percentual_maximo"])
    valor = round(1000 * pct / 100, 2)
    frase = (f"Numa compra de R$ 1.000,00 o cliente ganha um cupom de "
             f"R$ {valor:.2f} ({pct:g}%).")
    if pct_max > 0:
        minimo = round(valor / (pct_max / 100), 2)
        frase += (f" Como o desconto pode representar no máximo {pct_max:g}% "
                  f"da nova compra, o cupom só vale em compras a partir de "
                  f"R$ {minimo:.2f}.")
    else:
        frase += " Sem compra mínima para usar o cupom."
    return frase


# ------------------------------------------------------------------ administração

@app.get("/admin/diagnostico")
def diagnostico():
    if not _token_ok():
        return jsonify({"erro": "token inválido"}), 401
    res = {}
    try:
        lidos = vtex.listar_pedidos(per_page=1)
        res["1_vtex_pedidos_oms"] = f"ok — {len(lidos)} pedido lido"
    except Exception as e:  # noqa: BLE001
        res["1_vtex_pedidos_oms"] = f"ERRO: {e}"
    try:
        r = vtex.listar_promocoes()
        res["2_vtex_promocoes"] = ("ok — acesso às promoções"
                                   if r.status_code == 200
                                   else f"ERRO {r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        res["2_vtex_promocoes"] = f"ERRO: {e}"
    try:
        res["3_banco_de_dados"] = armazem.testar()
    except Exception as e:  # noqa: BLE001
        res["3_banco_de_dados"] = f"ERRO: {e}"
    try:
        r = vtex.hook_atual()
        res["4_hook_na_vtex"] = (r.json() if r.status_code == 200
                                 else f"{r.status_code}: {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        res["4_hook_na_vtex"] = f"ERRO: {e}"
    cfg = armazem.ler_config()
    res["5_cashback"] = ("LIGADO" if cfg["ativo"] == "1"
                         else "DESLIGADO — ligue no /painel")
    return jsonify(res)


@app.get("/admin/registrar-hook")
def registrar_hook():
    if not _token_ok():
        return jsonify({"erro": "token inválido"}), 401
    base = (os.environ.get("APP_URL") or request.url_root)
    base = base.replace("http://", "https://").rstrip("/")
    url = f"{base}/vtex/hook"
    statuses = [s.strip() for s in os.environ.get(
        "HOOK_STATUSES", "payment-approved").split(",") if s.strip()]
    r = vtex.registrar_hook(url, os.environ["HOOK_SECRET"], statuses)
    return jsonify({
        "url_configurada": url,
        "status_da_vtex": r.status_code,
        "resposta": r.text[:500],
        "dica": "esperado 2xx; a VTEX manda um ping para a URL na hora — "
                "ele deve aparecer nos logs do app",
    })


@app.get("/admin/hook")
def hook_config():
    if not _token_ok():
        return jsonify({"erro": "token inválido"}), 401
    r = vtex.hook_atual()
    try:
        return jsonify(r.json())
    except Exception:  # noqa: BLE001
        return jsonify({"status": r.status_code, "resposta": r.text[:500]})


@app.get("/admin/gerar")
def gerar_manual():
    if not _token_ok():
        return jsonify({"erro": "token inválido"}), 401
    order_id = (request.args.get("pedido") or "").strip()
    if not order_id:
        return jsonify({"erro": "informe ?pedido=ID_DO_PEDIDO"}), 400
    resultado = cashback.processar(order_id, forcar=True)
    return jsonify(resultado)


@app.get("/admin/cupons")
def cupons_json():
    if not _token_ok():
        return jsonify({"erro": "token inválido"}), 401
    return jsonify(armazem.listar_cupons(500))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
