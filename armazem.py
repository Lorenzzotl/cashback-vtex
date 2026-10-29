# -*- coding: utf-8 -*-
"""Persistência das configurações do painel e dos cupons gerados.

Na Heroku usa o Postgres do add-on (Config Var DATABASE_URL, criada
automaticamente). Sem DATABASE_URL (desenvolvimento local), cai num
arquivo JSON ao lado do código.
"""
import json
import os
import threading
from datetime import datetime, timezone

_trava = threading.Lock()
_ARQUIVO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "dados_local.json")

# Valores iniciais do painel (tudo texto; o painel grava por cima).
PADROES = {
    "ativo": "0",               # "1" liga a geração de cupons
    "prefixo": "CASH",          # prefixo do código do cupom
    "percentual": "15",         # % da compra que vira cashback
    "percentual_maximo": "12.5",  # % máximo que o desconto pode representar na compra em que for usado (0 = sem mínimo)
    "pedido_minimo": "0",       # valor mínimo da compra para gerar cashback (0 = qualquer)
    "modo": "relativo",         # "relativo" (dias após a compra) ou "fixo" (datas de calendário)
    "dias_liberar": "15",       # modo relativo: libera X dias após a compra
    "dias_validade": "60",      # modo relativo: expira Y dias após a compra
    "fixo_inicio": "",          # modo fixo: cupom vale a partir de (AAAA-MM-DD)
    "fixo_fim": "",             # modo fixo: cupom vale até (AAAA-MM-DD)
    "fixo_gerar_inicio": "",    # modo fixo: só gera para compras a partir de (vazio = sempre)
    "fixo_gerar_fim": "",       # modo fixo: só gera para compras até (vazio = sempre)
}


def _usa_postgres():
    return bool(os.environ.get("DATABASE_URL"))


def _conexao():
    import psycopg2
    url = os.environ["DATABASE_URL"]
    if url.startswith("postgres://"):  # formato antigo da Heroku
        url = url.replace("postgres://", "postgresql://", 1)
    return psycopg2.connect(url)


def preparar():
    """Cria as tabelas na primeira execução (seguro chamar sempre)."""
    if not _usa_postgres():
        return
    with _conexao() as cx, cx.cursor() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS cashback_config (
                         chave TEXT PRIMARY KEY,
                         valor TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS cashback_cupons (
                         order_id   TEXT PRIMARY KEY,
                         codigo     TEXT NOT NULL,
                         nome       TEXT,
                         valor      NUMERIC,
                         minimo     NUMERIC,
                         inicio     TEXT,
                         fim        TEXT,
                         id_promocao TEXT,
                         criado_em  TEXT)""")
        c.execute("""ALTER TABLE cashback_cupons
                     ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'ativo'""")


# ------------------------------------------------------------------ JSON local

def _ler_arquivo():
    try:
        with open(_ARQUIVO, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"config": {}, "cupons": {}}


def _gravar_arquivo(dados):
    with open(_ARQUIVO, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ configuração

def ler_config():
    cfg = dict(PADROES)
    if _usa_postgres():
        with _conexao() as cx, cx.cursor() as c:
            c.execute("SELECT chave, valor FROM cashback_config")
            cfg.update(dict(c.fetchall()))
    else:
        with _trava:
            cfg.update(_ler_arquivo().get("config") or {})
    return cfg


def salvar_config(novos):
    itens = {k: str(v) for k, v in novos.items() if k in PADROES}
    if _usa_postgres():
        with _conexao() as cx, cx.cursor() as c:
            for chave, valor in itens.items():
                c.execute("""INSERT INTO cashback_config (chave, valor)
                             VALUES (%s, %s)
                             ON CONFLICT (chave) DO UPDATE SET valor = EXCLUDED.valor""",
                          (chave, valor))
    else:
        with _trava:
            dados = _ler_arquivo()
            dados.setdefault("config", {}).update(itens)
            _gravar_arquivo(dados)


# ------------------------------------------------------------------ cupons

def cupom_do_pedido(order_id):
    dados = dados_cupom(order_id)
    return dados.get("codigo") if dados else None


def dados_cupom(order_id):
    """Registro completo do cupom de um pedido (ou None)."""
    if _usa_postgres():
        with _conexao() as cx, cx.cursor() as c:
            c.execute("""SELECT codigo, id_promocao, status
                         FROM cashback_cupons WHERE order_id = %s""",
                      (order_id,))
            linha = c.fetchone()
            if not linha:
                return None
            return {"codigo": linha[0], "id_promocao": linha[1],
                    "status": linha[2] or "ativo"}
    with _trava:
        cupom = (_ler_arquivo().get("cupons") or {}).get(order_id)
    if not cupom:
        return None
    return {"codigo": cupom.get("codigo"),
            "id_promocao": cupom.get("id_promocao"),
            "status": cupom.get("status") or "ativo"}


def marcar_cancelado(order_id):
    if _usa_postgres():
        with _conexao() as cx, cx.cursor() as c:
            c.execute("""UPDATE cashback_cupons SET status = 'cancelado'
                         WHERE order_id = %s""", (order_id,))
        return
    with _trava:
        dados = _ler_arquivo()
        cupom = (dados.get("cupons") or {}).get(order_id)
        if cupom:
            cupom["status"] = "cancelado"
            _gravar_arquivo(dados)


def registrar_cupom(order_id, codigo, nome, valor, minimo,
                    inicio, fim, id_promocao=None):
    criado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if _usa_postgres():
        with _conexao() as cx, cx.cursor() as c:
            c.execute("""INSERT INTO cashback_cupons
                           (order_id, codigo, nome, valor, minimo,
                            inicio, fim, id_promocao, criado_em, status)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'ativo')
                         ON CONFLICT (order_id) DO NOTHING""",
                      (order_id, codigo, nome, valor, minimo,
                       inicio, fim, id_promocao, criado))
        return
    with _trava:
        dados = _ler_arquivo()
        dados.setdefault("cupons", {})[order_id] = {
            "codigo": codigo, "nome": nome, "valor": valor,
            "minimo": minimo, "inicio": inicio, "fim": fim,
            "id_promocao": id_promocao, "criado_em": criado,
            "status": "ativo",
        }
        _gravar_arquivo(dados)


def listar_cupons(limite=100):
    if _usa_postgres():
        with _conexao() as cx, cx.cursor() as c:
            c.execute("""SELECT order_id, codigo, nome, valor, minimo,
                                inicio, fim, criado_em, status
                         FROM cashback_cupons
                         ORDER BY criado_em DESC LIMIT %s""", (limite,))
            return [{"order_id": l[0], "codigo": l[1], "nome": l[2],
                     "valor": float(l[3] or 0), "minimo": float(l[4] or 0),
                     "inicio": l[5], "fim": l[6], "criado_em": l[7],
                     "status": l[8] or "ativo"}
                    for l in c.fetchall()]
    with _trava:
        cupons = _ler_arquivo().get("cupons") or {}
    lista = [{"order_id": oid, **c} for oid, c in cupons.items()]
    lista.sort(key=lambda c: c.get("criado_em") or "", reverse=True)
    return lista[:limite]


def testar():
    """Para o diagnóstico: diz onde os dados estão sendo guardados."""
    if _usa_postgres():
        preparar()
        with _conexao() as cx, cx.cursor() as c:
            c.execute("SELECT COUNT(*) FROM cashback_cupons")
            total = c.fetchone()[0]
        return f"ok — Postgres ({total} cupom(ns) registrado(s))"
    return ("AVISO: sem DATABASE_URL — usando arquivo local, que a Heroku "
            "APAGA a cada deploy/reinício. Adicione o add-on Heroku Postgres.")
