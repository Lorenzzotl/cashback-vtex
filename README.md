# Cashback VTEX

App que roda na Heroku e gera um **cupom de cashback** a cada pedido **pago**
na VTEX (status `payment-approved`):

- O cupom é criado na própria VTEX como **promoção de valor nominal + cupom de
  uso único** — aparece no admin da VTEX em *Promoções* e *Cupons*.
- O código remete ao pedido e ao cliente sem depender do e-mail:
  `PREFIXO-NOME-NºDOPEDIDO` (ex.: `CASH-MARIA-149838200057401`). Qualquer
  pessoa com o código consegue usar — ele **não** fica amarrado ao e-mail.
- O **painel** (`/painel?token=…`) define tudo: percentual do cashback, desconto
  máximo na compra em que o cupom for usado (que vira compra mínima), janela de
  liberação/validade em dias **ou** datas fixas de calendário, compra mínima
  para gerar e prefixo do código. Também lista os cupons já gerados.

## Por que cupom e não vale-compra (gift card)?

O vale-compra da VTEX não atende dois requisitos do projeto: ele **não aceita
valor mínimo de pedido** (um vale de R$ 125 poderia ser usado numa compra de
R$ 130) e **não tem data de liberação futura** (só data de expiração). A
promoção com cupom suporta os dois (`totalValueFloor` + `beginDateUtc`), além
das datas fixas de calendário. Por isso o cashback sai como cupom.

## Como funciona

1. A VTEX avisa o app via Hook quando um pedido muda para `payment-approved`.
2. O app busca o pedido, calcula a base (produtos − descontos, **sem frete**)
   e aplica o percentual configurado.
3. Com `percentual_maximo` (ex.: 12,5%), a compra mínima para usar o cupom é
   `valor ÷ 12,5%` — um cupom de R$ 125 só vale em compras a partir de R$ 1.000.
4. A vigência segue o painel:
   - **Relativo** — libera X dias após a compra e expira Y dias após a compra
     (ex.: comprou hoje, usa entre o dia 15 e o dia 60);
   - **Fixo** — datas de calendário (ex.: compras de novembro ganham cupom
     válido só em dezembro), com janela opcional de quais compras geram cupom.
5. O app cria a promoção + cupom na VTEX e registra no Postgres (não duplica:
   o mesmo pedido nunca gera dois cupons, mesmo que a VTEX reenvie o evento).
6. Se o pedido for **cancelado** na VTEX (`canceled`), o app desativa a
   promoção e arquiva o cupom — o cashback morre junto com a compra. O painel
   mostra o cupom como "cancelado".

## Rotas

| Rota | O que faz |
|---|---|
| `GET /painel?token=` | Painel de configuração + lista de cupons gerados |
| `POST /vtex/hook` | Recebe os eventos de pedido da VTEX |
| `GET /admin/diagnostico?token=` | Testa credenciais VTEX, promoções, banco e hook |
| `GET /admin/registrar-hook?token=` | Registra/reativa o hook de pedidos na VTEX |
| `GET /admin/hook?token=` | Mostra a configuração atual do hook |
| `GET /admin/gerar?token=&pedido=ID` | Gera o cupom de um pedido manualmente (ignora o liga/desliga) |
| `GET /admin/cancelar?token=&pedido=ID` | Cancela o cupom de um pedido manualmente |
| `GET /admin/cupons?token=` | Lista os cupons gerados em JSON |

O `token` é o valor da Config Var `HOOK_SECRET`.

## Config Vars (Heroku → Settings → Config Vars)

| Variável | Valor |
|---|---|
| `VTEX_ACCOUNT` | nome da conta VTEX (ex.: `patoge`) |
| `VTEX_APP_KEY` | appKey da VTEX — **dedicada a este app** (veja abaixo) |
| `VTEX_APP_TOKEN` | appToken correspondente |
| `HOOK_SECRET` | token longo aleatório — protege o webhook, o painel e as rotas /admin |
| `DATABASE_URL` | criada automaticamente pelo add-on Heroku Postgres |

Opcionais: `HOOK_STATUSES` (padrão `payment-approved,canceled`), `FUSO` (padrão
`America/Sao_Paulo` — fuso das datas fixas do painel), `APP_URL` (força a URL
pública do app, se necessário).

**Nunca** coloque credenciais no código ou no repositório — só nas Config Vars.

## Permissões da appKey na VTEX

O perfil de acesso (License Manager) da appKey precisa incluir:

1. **OMS — Full access** (ler os pedidos pagos)
2. **Feed v3 and Hook — Admin** (registrar o hook de pedidos)
3. **CheckoutResources / Promoções — Save CalculatorConfiguration e Coupons**
   (criar as promoções e os cupons)

A rota `/admin/diagnostico` acusa exatamente qual permissão está faltando.

> **Atenção:** cada appKey suporta **um** hook de pedidos. Se a conta já usa o
> middleware da Reportana (`reportana-vtex`), **crie uma appKey nova só para o
> cashback** — registrar o hook com a mesma appKey derrubaria o da Reportana.

## Subir e ativar

1. Suba estes arquivos num repositório do GitHub e conecte na Heroku
   (Deploy → GitHub → Enable Automatic Deploys).
2. Resources → add-on **Heroku Postgres** (plano Essential/Mini basta). O dyno
   web deve ser **Basic** (o Eco dorme e perde eventos do hook).
3. Preencha as Config Vars acima.
4. No navegador, com `SEU_APP` e `TOKEN` (= `HOOK_SECRET`):
   - `https://SEU_APP.herokuapp.com/admin/diagnostico?token=TOKEN` — tudo deve dar "ok"
   - `https://SEU_APP.herokuapp.com/admin/registrar-hook?token=TOKEN` — registra o hook
   - `https://SEU_APP.herokuapp.com/painel?token=TOKEN` — configure e **ligue** o cashback
5. Faça um pedido de teste, pague, e acompanhe em More → View logs. O cupom
   aparece no painel e no admin da VTEX (Promoções → Cupons).

Se os cupons pararem de sair: abra o Diagnóstico e olhe o `isPaused` do hook —
a VTEX pausa o hook se o app ficar fora do ar por muito tempo. Estando pausado,
abra o link de registrar o hook, que reativa na hora.

## Arquivos

| Arquivo | O que faz |
|---|---|
| `app.py` | Servidor Flask: webhook da VTEX, painel e rotas administrativas |
| `cashback.py` | Regras do cashback: cálculo, vigência, código e criação na VTEX |
| `vtex.py` | Cliente das APIs da VTEX (OMS, Hook, Promoções, Cupons) |
| `armazem.py` | Persistência (Heroku Postgres; JSON local em desenvolvimento) |
| `util.py` | Log e normalizações |
| `painel_html.py` | O HTML do painel (embutido no código) |
| `Procfile` / `requirements.txt` | Configuração da Heroku |

## Limitações conhecidas

- O cupom é **de uso único e não cumulativo** com outras promoções de cupom;
  quem tiver dois cupons usa um por compra.
- O desconto do cupom é **nominal e fixo** (valor em reais calculado na
  geração); o "máximo de 12,5%" vira a compra mínima — em compras maiores o
  desconto representa menos de 12,5%, nunca mais.
- O cancelamento automático depende do evento `canceled` da VTEX; se o cupom
  cancelado já tiver sido usado antes do cancelamento, a VTEX não estorna o
  desconto da outra compra.
- O hook cobre eventos daqui pra frente; para gerar cupom de um pedido antigo
  use `/admin/gerar?token=…&pedido=ID`.
