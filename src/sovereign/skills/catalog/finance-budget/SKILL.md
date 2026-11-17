---
name: finance-budget
description: >-
  Suit les revenus réellement encaissés, calcule automatiquement l'enveloppe de dépense autorisée (10 % des
  encaissements nets), contrôle et autorise ou refuse chaque dépense, prévoit les coûts et empêche tout
  dépassement. À utiliser pour toute question de budget, dépense, coût, trésorerie, facture ou encaissement.
  Aucun accès bancaire : SOVEREIGN autorise, l'humain paie.
license: MIT
metadata:
  version: "1.0.0"
  module: "sovereign.skills.finance_budget"
  actions: "assess, status, record_revenue, issue_invoice, collect_invoice, request_expense, confirm_expense, check_spend, forecast"
  keywords: "budget, dépense, dépenses, coût, coûts, revenu, revenus, encaissement, encaissé, trésorerie, finance, facture, argent, rentabilité, spend, cost, revenue, cash, invoice"
  events: "finance.*, expense.*, budget.*, cycle.monthly, cycle.daily"
  data: "ledger, invoice"
  risk: "write"
---

# Finance et Budget

## Description

Gardien financier du directeur. Trois règles non négociables, garanties par le code ET par la base de données :

1. **Seul l'argent réellement encaissé compte.** Une facture émise n'ouvre aucun budget ; un encaissement ne peut
   pas être daté dans le futur ; les remboursements réduisent l'assiette.
2. **Enveloppe = 10 % des encaissements nets cumulés.** Le ratio est configurable à la baisse uniquement :
   toute valeur > 10 % est rejetée au démarrage.
3. **Aucun dépassement.** Chaque demande est évaluée dans une transaction SQL exclusive (`BEGIN IMMEDIATE`) :
   deux demandes concurrentes ne peuvent pas consommer deux fois le même euro.

Le registre des encaissements et le journal d'audit sont **append-only** (triggers SQL qui refusent `UPDATE` et
`DELETE`). Un montant approuvé est immuable ; le réel déclaré ne peut pas l'excéder.

## Quand l'utiliser

- « Combien puis-je dépenser ? », « Quel est notre budget ? » → `status`
- Un client a payé → `record_revenue` (ou `collect_invoice` si une facture existait)
- Besoin d'acheter un outil, une campagne, une prestation → `request_expense`
- Préparer le mois prochain, anticiper un épuisement → `forecast`
- Le directeur l'appelle via `assess` à chaque cycle quotidien et mensuel.

## Actions

| Action | Rôle | Risque |
|---|---|---|
| `assess` | Diagnostic complet : statut, tendance des encaissements, factures échues, recommandations | lecture |
| `status` | Encaissé, enveloppe, engagé, dépensé, disponible, alertes | lecture |
| `record_revenue` | Enregistre un encaissement réel (idempotent sur `reference`) | écriture |
| `issue_invoice` | Émet une facture (créance, sans effet sur le budget) | écriture |
| `collect_invoice` | Déclare l'encaissement d'une facture | écriture |
| `request_expense` | Approuve, refuse ou envoie en revue humaine | écriture |
| `confirm_expense` | Déclare le règlement réel (≤ montant approuvé) | écriture |
| `check_spend` | Vérifie un montant sans rien réserver | lecture |
| `forecast` | Projette encaissements, enveloppe et coûts, détecte la date d'épuisement | lecture |

## Entrées et sorties

Les schémas exacts (JSON Schema) sont publiés par `sovereign skill show finance-budget`, par l'API
(`GET /v1/skills/finance-budget`) et par le serveur MCP. Exemples :

```bash
sovereign skill run finance-budget record_revenue --input '{"amount": "1200.00", "counterparty": "Client A", "reference": "pay_123"}'
sovereign skill run finance-budget request_expense --input '{"amount": "80", "category": "outils", "vendor": "SaaS X"}'
```

Sortie de `request_expense` : `decision.approved`, `decision.needs_review`, `decision.expense.status`
(`approved` | `rejected` | `review`), `decision.available_before/after`, `decision.message`.

Toutes les sorties portent aussi `summary`, `insights`, `recommendations` et `metrics` (contrat commun).

## Déclencheurs

- Mots-clés : budget, dépense, coût, revenu, encaissement, trésorerie, facture…
- Événements : `finance.*`, `expense.*`, `budget.*` (dont `budget.threshold_crossed` à 80 % et 100 %),
  `cycle.daily`, `cycle.monthly`.
- Webhooks : un paiement Stripe signé (`payment_intent.succeeded`, `invoice.paid`) devient un encaissement.

## Sécurité

- Aucune intégration bancaire, aucun moyen de paiement : la Skill ne peut pas déplacer d'argent.
- Une dépense > 50 % du disponible part en **revue humaine** ; l'approbation re-vérifie le disponible.
- Les appels LLM sont eux-mêmes des dépenses soumises à ce garde-fou (catégorie `ia`).
- Devise unique par registre : aucune conversion implicite.
- Toute opération est tracée dans `audit_log` (append-only).

## Tests

`tests/skills/test_finance_budget.py` couvre : enveloppe de 10 %, refus de dépassement, revue humaine,
factures sans effet budgétaire, idempotence, immutabilité SQL du registre, concurrence (threads),
plafond du ratio, prévision et alertes de seuil.
