# Guide Complet : Bot de Trading Delta-Neutre sur ASTER DEX

## 📚 Table des Matières

1. [Introduction Générale](#introduction-générale)
2. [Concepts Fondamentaux](#concepts-fondamentaux)
3. [Architecture Technique Détaillée](#architecture-technique-détaillée)
4. [Stratégie de Trading Expliquée](#stratégie-de-trading-expliquée)
5. [Système de Levier et Allocation du Capital](#système-de-levier-et-allocation-du-capital)
6. [Gestion des Risques](#gestion-des-risques)
7. [Calculs et Suivi des Profits/Pertes](#calculs-et-suivi-des-profitspertes)
8. [Filtrage des Paires de Trading](#filtrage-des-paires-de-trading)
9. [Configuration et Déploiement](#configuration-et-déploiement)
10. [Scripts Utilitaires](#scripts-utilitaires)
11. [Monitoring et Debugging](#monitoring-et-debugging)
12. [Exemples Concrets et Cas d'Usage](#exemples-concrets-et-cas-dusage)
13. [Questions Fréquentes](#questions-fréquentes)

---

## Introduction Générale

### Qu'est-ce que ce Projet ?

Ce projet est un **bot de trading automatisé delta-neutre** conçu spécifiquement pour l'exchange décentralisé ASTER DEX. Il s'agit d'un système sophistiqué qui capture les **paiements de taux de financement** (funding rates) des contrats perpétuels tout en maintenant une **exposition neutre au marché**.

### Objectifs Principaux

1. **Générer des profits stables** en collectant les taux de financement sans prendre de risque directionnel
2. **Maximiser le volume de trading** sur ASTER DEX (utile pour l'airdrop Stage 3)
3. **Rotation continue** des positions pour optimiser les rendements
4. **Automation complète** fonctionnant 24h/7j sans intervention humaine

### Pourquoi ce Bot est-il Unique ?

- ✅ **Delta-neutre** : Aucune exposition aux mouvements de prix du marché
- ✅ **Multi-leverage** : Support de 1x à 3x avec transitions automatiques
- ✅ **Filtrage intelligent** : 4 niveaux de filtres pour sélectionner uniquement les meilleures opportunités
- ✅ **Gestion complète du risque** : Stop-loss automatique, health checks, récupération d'état
- ✅ **Tracking PnL avancé** : Suivi en temps réel du portfolio complet et des positions individuelles
- ✅ **Architecture propre** : Séparation claire entre logique métier, API et orchestration

---

## Concepts Fondamentaux

### Qu'est-ce que le Trading Delta-Neutre ?

Le **trading delta-neutre** est une stratégie qui vise à éliminer l'exposition aux mouvements de prix (le "delta" en termes d'options grecques). Dans le contexte de ce bot :

**Position Delta-Neutre = Position Longue Spot + Position Courte Perpétuel**

#### Exemple Concret

Imaginons que vous voulez capturer le taux de financement sur BTC/USDT :

1. **Vous achetez 0.1 BTC sur le marché spot** à 50,000 USDT
2. **Vous shortez 0.1 BTC sur le marché perpétuel** à 50,000 USDT

**Résultat** :
- Si le prix monte à 55,000 USDT :
  - Votre position spot gagne : +5,000 USDT
  - Votre position perp perd : -5,000 USDT
  - **Profit net du mouvement de prix : 0 USDT** ✓

- Si le prix descend à 45,000 USDT :
  - Votre position spot perd : -5,000 USDT
  - Votre position perp gagne : +5,000 USDT
  - **Profit net du mouvement de prix : 0 USDT** ✓

**Vous êtes protégé contre les mouvements de prix dans les deux directions !**

### Qu'est-ce que le Taux de Financement (Funding Rate) ?

Les **taux de financement** sont des paiements périodiques entre les traders longs et courts sur les marchés de contrats perpétuels.

#### Mécanisme

- **Taux positif** : Les longs paient les shorts → Vous **recevez** des paiements en étant short
- **Taux négatif** : Les shorts paient les longs → Vous **payez** en étant short (à éviter !)
- **Fréquence** : Toutes les 8 heures (00:00, 08:00, 16:00 UTC sur ASTER DEX)

#### Pourquoi les Taux de Financement Existent-ils ?

Les taux de financement servent à maintenir le prix du contrat perpétuel aligné avec le prix spot :

- **Marché haussier** : Beaucoup de traders veulent être longs → Taux positif élevé → Incite les shorts
- **Marché baissier** : Beaucoup de traders veulent être shorts → Taux négatif → Incite les longs

#### Calcul du Rendement Annualisé (APR)

Le bot calcule l'APR à partir du taux de financement instantané :

```
APR (%) = Taux de financement × 3 (paiements/jour) × 365 (jours) × 100
```

**Exemple** :
- Taux de financement : 0.01% (0.0001)
- APR = 0.0001 × 3 × 365 × 100 = **10.95% par an**

Sur une position de 10,000 USDT, cela représente ~1,095 USDT de profit annuel juste en collectant les funding rates !

### Pourquoi cette Stratégie est-elle Profitable ?

**Sources de Profit** :
1. **Taux de financement positifs** : Revenus réguliers toutes les 8 heures
2. **Rotation des positions** : Capture des meilleures opportunités en changeant de paires
3. **Effet de levier** : Maximise l'utilisation du capital (jusqu'à 3x)

**Coûts à Couvrir** :
1. **Frais d'entrée** : ~0.1% sur spot + ~0.05% sur perp = 0.15% total
2. **Frais de sortie** : ~0.1% sur spot + ~0.05% sur perp = 0.15% total
3. **Total des frais** : ~0.30% par cycle complet

**Seuil de Rentabilité** :
Le bot attend que les funding rates collectés couvrent les frais × multiplicateur (défaut : 1.8x) avant de fermer une position, garantissant ainsi la rentabilité de chaque cycle.

---

## Architecture Technique Détaillée

### Vue d'Ensemble de l'Architecture

Le bot suit une architecture modulaire avec **séparation stricte des responsabilités** :

```
┌─────────────────────────────────────────────────────────────┐
│                 volume_farming_strategy.py                  │
│                    (Orchestrateur Principal)                │
│  • Boucle principale de stratégie                          │
│  • Gestion d'état (volume_farming_state.json)             │
│  • Logique de décision (quand ouvrir/fermer)              │
│  • Monitoring et health checks                             │
└────────────┬──────────────────────────────┬────────────────┘
             │                              │
             ▼                              ▼
┌────────────────────────┐      ┌──────────────────────────┐
│ aster_api_manager.py   │      │   strategy_logic.py      │
│   (Couche API)         │      │   (Logique Pure)         │
│                        │      │                          │
│ • Auth v1 (HMAC-SHA256)│      │ • Calculs stateless      │
│ • Auth v3 (ETH sig)    │      │ • Analyse funding rates  │
│ • Ordres spot/perp     │      │ • Sizing de positions    │
│ • Transferts USDT      │      │ • Health checks          │
│ • Gestion du levier    │      │ • PnL calculations       │
└────────────┬───────────┘      └──────────────────────────┘
             │
             ▼
┌────────────────────────┐
│     ASTER DEX API      │
│  • Spot Markets (v1)   │
│  • Perpetual (v3)      │
│  • Account Info        │
└────────────────────────┘
```

### Module 1 : `aster_api_manager.py` - Couche API

#### Responsabilités

Ce module est la **seule interface** avec l'exchange ASTER DEX. Il gère :
- Toutes les requêtes HTTP vers l'API
- Deux systèmes d'authentification distincts
- Le formatage des paramètres d'ordres
- La gestion des erreurs API

#### Authentification Dual (v1 + v3)

ASTER DEX utilise **deux systèmes d'authentification différents** :

##### **API v1 (HMAC-SHA256)** - Pour le Spot et Quelques Fonctions Perp

```python
# Endpoints utilisant v1 :
- GET /fapi/v1/leverageBracket  # Obtenir le levier
- POST /fapi/v1/leverage        # Définir le levier
- GET /fapi/v1/income           # Historique des funding rates
- GET /fapi/v1/userTrades       # Historique des trades
```

**Process d'authentification v1** :
1. Créer une query string avec timestamp : `symbol=BTCUSDT&timestamp=1696800000000`
2. Signer avec HMAC-SHA256 : `signature = hmac(query_string, APIV1_PRIVATE_KEY)`
3. Ajouter la signature à la query string
4. Envoyer avec header : `X-MBX-APIKEY: APIV1_PUBLIC_KEY`

##### **API v3 (Ethereum Signature)** - Pour les Ordres et Positions

```python
# Endpoints utilisant v3 :
- POST /v3/order         # Placer un ordre
- GET /v3/account        # Info du compte
- GET /v3/openOrders     # Ordres ouverts
- GET /v3/positionRisk   # Positions perpétuelles
```

**Process d'authentification v3** :
1. Créer un payload JSON des paramètres
2. Hasher avec keccak256 : `message_hash = keccak256(json.dumps(params))`
3. Signer avec la clé privée Ethereum : `signature = eth_account.sign(message_hash)`
4. Envoyer avec headers :
   - `aster-user-address: API_USER` (votre wallet ETH)
   - `aster-signer-address: API_SIGNER` (signer généré par ASTER)
   - `aster-signature: signature`

#### Méthodes Clés de l'API Manager

##### `get_perp_leverage(symbol: str) -> int`
Détecte le levier actuel sur l'exchange pour un symbole donné.

```python
# Retourne : 1, 2, ou 3 (ou None si erreur)
current_leverage = await api_manager.get_perp_leverage("BTCUSDT")
```

##### `set_perp_leverage(symbol: str, leverage: int) -> bool`
Définit le levier sur l'exchange (1x, 2x, ou 3x).

```python
success = await api_manager.set_perp_leverage("BTCUSDT", 3)
```

##### `rebalance_usdt_by_leverage(leverage: int) -> bool`
Redistribue les USDT entre les wallets spot et perp selon le levier.

**Formule de répartition** :
```python
perp_allocation = 1 / (leverage + 1)
spot_allocation = leverage / (leverage + 1)

# Exemples :
# 1x : 50% perp / 50% spot
# 2x : 33.3% perp / 66.7% spot
# 3x : 25% perp / 75% spot
```

##### `prepare_and_execute_dn_position(symbol, capital_usdt, leverage)`
Prépare et exécute une position delta-neutre complète :

1. Calcule les quantités spot et perp
2. Formate les paramètres avec la précision correcte
3. Place l'ordre spot (market buy)
4. Place l'ordre perp (market short)
5. Vérifie l'exécution des deux ordres
6. Retourne les détails complets de la position

### Module 2 : `strategy_logic.py` - Logique Pure

#### Principe de Conception

Ce module contient **uniquement des fonctions pures** :
- ✅ Pas d'appels API
- ✅ Pas de mutations d'état
- ✅ Entrées → Calculs → Sorties
- ✅ Facile à tester

Toutes les méthodes sont **statiques** dans la classe `DeltaNeutralLogic`.

#### Méthodes Principales

##### `calculate_position_sizes(capital_usdt, spot_price, leverage)`
Calcule les tailles de position pour les deux jambes.

```python
# Inputs
capital_usdt = 1000  # Capital total à déployer
spot_price = 50000   # Prix BTC
leverage = 3         # Levier 3x

# Outputs
{
    'spot_qty': 0.015,        # Quantité BTC à acheter en spot
    'perp_qty': 0.015,        # Quantité BTC à shorter en perp
    'spot_value': 750,        # Valeur en USDT (75% du capital)
    'perp_value': 250,        # Marge en USDT (25% du capital)
    'total_position_value': 750  # Valeur notionnelle
}
```

##### `calculate_funding_rate_ma(income_history, periods=10)`
Calcule la moyenne mobile des taux de financement pour lisser la volatilité.

```python
# Input : Historique des funding rates
income_history = [
    {'income': '0.50', 'time': 1696800000000},  # $0.50 reçu
    {'income': '0.45', 'time': 1696771200000},  # $0.45 reçu
    # ... 10 périodes
]

# Output : APR moyen
{
    'effective_apr': 12.5,           # APR moyen sur 10 périodes
    'periods_analyzed': 10,          # Nombre de périodes utilisées
    'latest_funding_rate': 0.0001    # Dernier taux
}
```

##### `assess_health(position_data, config)`
Évalue la santé d'une position et détecte les problèmes.

**Checks effectués** :
1. **Levier valide** : 1 ≤ leverage ≤ 3
2. **Déséquilibre** : |spot_qty - perp_qty| / spot_qty ≤ 10%
3. **Valeur minimale** : position_value > $5

```python
{
    'is_healthy': True,
    'critical_issues': [],         # Problèmes bloquants
    'warnings': [],                # Avertissements
    'metrics': {
        'imbalance_pct': 2.5,      # 2.5% de déséquilibre
        'leverage': 3,
        'position_value': 1000
    }
}
```

### Module 3 : `volume_farming_strategy.py` - Orchestrateur Principal

C'est le **cœur du bot**. Il orchestre tout le système.

#### Structure de la Classe `VolumeFarmingStrategy`

```python
class VolumeFarmingStrategy:
    def __init__(self, config_path, state_path):
        self.api_manager = AsterApiManager(...)
        self.config = load_config()
        self.state = load_state()
        self.check_iteration = 0  # Compteur de vérifications
```

#### Boucle Principale : `run()`

La méthode `run()` est une boucle infinie qui exécute le cycle de stratégie :

```python
async def run(self):
    while True:
        self.check_iteration += 1

        # 1. Health check
        is_healthy = await self._perform_health_check()
        if not is_healthy:
            await asyncio.sleep(loop_interval)
            continue

        # 2. Si position ouverte : évaluer
        if self.state.get('position_open'):
            await self._evaluate_existing_position()

        # 3. Si pas de position : chercher opportunité
        else:
            await self._find_and_open_position()

        # 4. Sauvegarder l'état
        self._save_state()

        # 5. Attendre le prochain cycle
        await asyncio.sleep(loop_interval)  # Défaut: 900s (15min)
```

#### Gestion d'État : `volume_farming_state.json`

Le fichier d'état persiste toutes les informations critiques :

```json
{
  "position_open": true,
  "symbol": "BTCUSDT",
  "position_leverage": 3,              // Levier utilisé pour cette position
  "capital_allocated_usdt": 1000.0,
  "entry_price": 50000.0,              // Prix d'entrée sauvegardé
  "spot_qty": 0.015,
  "perp_qty": 0.015,
  "funding_received_usdt": 2.50,       // Funding collecté
  "entry_fees_usdt": 3.0,              // Frais d'entrée
  "position_opened_at": "2025-10-12T10:00:00",
  "cycle_count": 5,                     // Cycles de trading complétés
  "initial_portfolio_value_usdt": 5000.0,  // Baseline pour PnL total
  "initial_portfolio_timestamp": "2025-10-08T12:00:00",
  "last_updated": "2025-10-12T11:30:00"
}
```

**Points Importants** :
- `position_leverage` ≠ `config.leverage` : Le levier de la position est indépendant du config
- `cycle_count` : Incrémenté **uniquement** à la fermeture d'une position (pas à chaque vérification)
- `initial_portfolio_value_usdt` : Capturé une seule fois au premier lancement
- Supprimer ce fichier force la redécouverte et réinitialise le PnL baseline

#### Réconciliation d'État au Démarrage

Au démarrage, le bot **réconcilie** son état avec l'exchange :

##### **Cas 1 : État sauvegardé mais pas de position sur l'exchange**
```
État local : position_open = true
Exchange : Aucune position

→ Action : Nettoyer l'état (position fermée externalement)
→ Log : "Position was closed externally"
```

##### **Cas 2 : Pas d'état mais position sur l'exchange**
```
État local : Rien ou position_open = false
Exchange : Position BTCUSDT détectée

→ Action : Appeler _discover_existing_position()
→ Détecte le levier depuis l'exchange
→ Reconstruit l'état depuis les données API
→ Log : "Discovered existing position"
```

##### **Cas 3 : État et exchange synchronisés**
```
→ Continuer normalement
```

#### Méthode : `_find_best_funding_opportunity()`

Cette méthode complexe trouve la meilleure opportunité de trading en 4 étapes :

##### **Étape 1 : Découverte des Paires Delta-Neutres**

```python
# Trouver toutes les paires avec spot ET perp
spot_symbols = {s['symbol'] for s in await get_spot_exchange_info()}
perp_symbols = {s['symbol'] for s in await get_perp_exchange_info()}
dn_pairs = spot_symbols & perp_symbols  # Intersection
```

##### **Étape 2 : Filtrage par Volume (≥ $250M)**

```python
volume_data = await fetch_24h_ticker()
filtered = [
    pair for pair in dn_pairs
    if volume_data[pair]['quoteVolume'] >= 250_000_000
]
```

**Pourquoi $250M ?**
- Liquidité suffisante pour exécuter sans slippage
- Taux de financement plus stables
- Moins de risque de manipulation

##### **Étape 3 : Filtrage des Taux Négatifs**

```python
funding_rates = await fetch_current_funding_rates()
filtered = [
    pair for pair in filtered
    if funding_rates[pair] > 0  # Uniquement taux positifs
]
```

**Critique** : Le filtre utilise le taux **actuel**, pas le taux MA !
- Même si la MA est positive, si le taux actuel est négatif → Exclusion
- Évite d'entrer dans des positions qui deviennent négatives

##### **Étape 4 : Filtrage par Spread Spot-Perp (≤ 0.15%)**

```python
spot_prices = await fetch_spot_book_tickers()
perp_prices = await fetch_perp_book_tickers()

for pair in filtered:
    spot_mid = (spot_prices[pair]['bid'] + spot_prices[pair]['ask']) / 2
    perp_mid = (perp_prices[pair]['bid'] + perp_prices[pair]['ask']) / 2
    spread_pct = abs((perp_mid - spot_mid) / spot_mid * 100)

    if spread_pct > 0.15:
        # Exclure cette paire
```

**Pourquoi 0.15% ?**
- Spread trop large = risque de slippage à l'exécution
- Indique des problèmes de liquidité ou inefficiences du marché
- Pour une position DN, un spread large peut déséquilibrer l'entrée

##### **Étape 5 : Sélection de la Meilleure Opportunité**

```python
# Mode MA : Calculer MA pour chaque paire restante
for pair in filtered:
    income_history = await fetch_income_history(pair)
    ma_apr = calculate_funding_rate_ma(income_history, periods=10)

    if ma_apr >= min_funding_apr:
        opportunities[pair] = ma_apr

# Sélectionner l'APR le plus élevé
best_pair = max(opportunities, key=opportunities.get)
```

#### Méthode : `_open_position(symbol, capital_usdt)`

Ouvre une nouvelle position delta-neutre en plusieurs étapes :

```python
async def _open_position(self, symbol, capital_usdt):
    # 1. Récupérer le prix actuel
    spot_price = await self.api_manager.get_spot_ticker_price(symbol)

    # 2. Définir le levier sur l'exchange
    leverage = self.config['leverage_settings']['leverage']
    await self.api_manager.set_perp_leverage(symbol, leverage)

    # 3. Rebalancer les USDT entre wallets
    await self.api_manager.rebalance_usdt_by_leverage(leverage)

    # 4. Exécuter les ordres (spot + perp)
    result = await self.api_manager.prepare_and_execute_dn_position(
        symbol, capital_usdt, leverage
    )

    # 5. Sauvegarder l'état
    self.state['position_open'] = True
    self.state['symbol'] = symbol
    self.state['position_leverage'] = leverage  # Important !
    self.state['entry_price'] = result['entry_price']
    self.state['spot_qty'] = result['spot_qty']
    self.state['perp_qty'] = result['perp_qty']
    self.state['funding_received_usdt'] = 0.0
    self.state['entry_fees_usdt'] = result['fees']
    self.state['position_opened_at'] = datetime.utcnow().isoformat()

    self._save_state()
```

#### Méthode : `_evaluate_existing_position()`

Évalue une position ouverte et décide si elle doit être fermée :

```python
async def _evaluate_existing_position(self):
    # 1. Récupérer les données actuelles
    current_price = await api_manager.get_spot_ticker_price(symbol)
    perp_position = await api_manager.get_perp_positions()
    funding_history = await api_manager.get_income_history(symbol)

    # 2. Calculer les PnL
    spot_pnl = spot_qty * (current_price - entry_price)
    perp_pnl = perp_position['unrealizedProfit']
    funding_received = sum(funding_history since opened)

    # 3. PnL combiné DN (net)
    combined_pnl = spot_pnl + perp_pnl + funding_received - entry_fees

    # 4. Vérifier les conditions de fermeture

    # Condition 1 : Stop-loss (uniquement sur perp PnL)
    stop_loss = self._calculate_safe_stoploss(position_leverage)
    if perp_pnl <= stop_loss * perp_value:
        await self._close_current_position("Emergency stop-loss")
        return

    # Condition 2 : Funding couvre les frais
    total_fees = entry_fees + estimated_exit_fees
    if funding_received >= total_fees * fee_coverage_multiplier:
        await self._close_current_position("Funding covered fees")
        return

    # Condition 3 : Position trop vieille
    age_hours = (now - position_opened_at).total_seconds() / 3600
    if age_hours >= max_position_age_hours:
        await self._close_current_position("Max age reached")
        return

    # Condition 4 : Meilleure opportunité ailleurs
    best_opportunity = await self._find_best_funding_opportunity()
    if best_opportunity['apr'] > current_apr * 1.5:  # 50% meilleur
        await self._close_current_position("Better opportunity found")
        return

    # Sinon : Garder la position ouverte
    logger.info("Position maintained")
```

#### Méthode : `_close_current_position(reason)`

Ferme la position actuelle et met à jour l'état :

```python
async def _close_current_position(self, reason: str):
    logger.info(f"Closing position: {reason}")

    # 1. Fermer la jambe spot (market sell)
    spot_result = await api_manager.place_spot_order(
        symbol=symbol,
        side='SELL',
        type='MARKET',
        quantity=spot_qty
    )

    # 2. Fermer la position perp
    perp_result = await api_manager.close_perp_position(symbol)

    # 3. Calculer le PnL final
    final_pnl = calculate_final_pnl(...)

    # 4. Incrémenter le compteur de cycles COMPLÉTÉS
    self.state['cycle_count'] += 1  # Seulement ici !

    # 5. Nettoyer l'état
    self.state['position_open'] = False
    self.state['symbol'] = None
    # ... réinitialiser tous les champs de position

    self._save_state()

    logger.info(f"Position closed. Final PnL: ${final_pnl:.2f}")
```

---

## Stratégie de Trading Expliquée

### Flux de Décision Complet

```
┌─────────────────────────────────────────────────────────────┐
│                    DÉMARRAGE DU BOT                         │
│  • Charger config & état                                    │
│  • Réconcilier avec l'exchange                             │
│  • Capturer baseline portfolio (si première fois)          │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│               DÉBUT DU CYCLE (toutes les 15min)             │
│  check_iteration += 1                                       │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
             ┌──────────────────────┐
             │   HEALTH CHECK       │
             │  • Balances USDT     │
             │  • API connectivity  │
             │  • État cohérent     │
             └──────────┬───────────┘
                        │
                ┌───────┴────────┐
                │   Healthy ?    │
                └───────┬────────┘
                    No  │  Yes
              ┌─────────┴──────────┐
              │                    ▼
              │         ┌───────────────────┐
              │         │ Position ouverte? │
              │         └─────┬─────────┬───┘
              │           Yes │         │ No
              │               ▼         ▼
              │    ┌──────────────┐  ┌────────────────────┐
              │    │   ÉVALUER    │  │ TROUVER OPPORTUNITÉ│
              │    │   POSITION   │  │                    │
              │    │              │  │ 1. Volume ≥ $250M  │
              │    │ Calculer PnL │  │ 2. Rate > 0%       │
              │    │ Vérifier:    │  │ 3. Spread ≤ 0.15%  │
              │    │ • Stop-loss  │  │ 4. APR ≥ min       │
              │    │ • Funding OK │  └─────────┬──────────┘
              │    │ • Age limite │            │
              │    │ • Meilleure  │            │
              │    │   opportunité│            │
              │    └──────┬───────┘            │
              │           │                    │
              │      ┌────┴─────┐         ┌────┴─────┐
              │      │ Fermer ? │         │ Trouvée? │
              │      └────┬─────┘         └────┬─────┘
              │       Yes │ No               Yes│ No
              │           ▼                    ▼
              │    ┌────────────┐        ┌──────────┐
              │    │   FERMER   │        │  OUVRIR  │
              │    │  POSITION  │        │ POSITION │
              │    │            │        │          │
              │    │ • Sell spot│        │• Set lev │
              │    │ • Close perp│       │• Rebalance│
              │    │ • cycle++  │        │• Buy spot│
              │    └──────┬─────┘        │• Short prp│
              │           │              └─────┬────┘
              │           ▼                    │
              │    ┌─────────────────────────┐│
              │    │   SAUVEGARDER ÉTAT      ││
              └────►  volume_farming_state.json│
                   └────────────┬─────────────┘
                                │
                                ▼
                   ┌─────────────────────────┐
                   │  ATTENDRE 15 MINUTES    │
                   │  (loop_interval_seconds)│
                   └────────────┬────────────┘
                                │
                                └──────► RÉPÉTER
```

### Critères de Sélection des Paires

Le bot applique **4 filtres successifs** pour garantir la qualité des opportunités :

#### Filtre 1 : Volume Minimum ($250M)

**Objectif** : Assurer une liquidité suffisante

**Implémentation** :
```python
volume_threshold = 250_000_000  # $250M en USDT

ticker_24h = await api_manager.fetch_24h_ticker()
eligible_pairs = [
    pair for pair in delta_neutral_pairs
    if ticker_24h[pair]['quoteVolume'] >= volume_threshold
]
```

**Raison** :
- Paires à faible volume → risque de slippage élevé
- Funding rates instables sur faibles volumes
- Difficulté à exécuter de gros ordres

**Exemple** :
- ✅ BTCUSDT : $500M de volume → Éligible
- ✅ ETHUSDT : $300M de volume → Éligible
- ❌ OBSCURECOIN : $50M de volume → Filtré

#### Filtre 2 : Taux de Financement Positif

**Objectif** : Éviter de payer du funding au lieu d'en recevoir

**Implémentation** :
```python
current_funding_rates = await api_manager.get_premium_index()

eligible_pairs = [
    pair for pair in eligible_pairs
    if current_funding_rates[pair] > 0
]
```

**Important** : Le filtre utilise le taux **instantané actuel**, pas la MA !

**Scénario Critique** :
```
Paire: XYZUSDT
MA sur 10 périodes: +0.01% (positif)
Taux actuel: -0.005% (négatif)

→ Bot exclut XYZUSDT malgré la MA positive
→ Évite d'entrer alors que le marché a tourné
```

**Logging** :
```
[2025-10-12 11:30:00] Negative rate filter: 2 pair(s) excluded:
  BTCUSDT (-0.0050%), ETHUSDT (-0.0023%)
```

#### Filtre 3 : Spread Spot-Perp (≤ 0.15%)

**Objectif** : Garantir un alignement de prix entre spot et perp

**Implémentation** :
```python
spot_tickers = await api_manager.get_spot_book_tickers()
perp_tickers = await api_manager.get_perp_book_tickers()

for pair in eligible_pairs:
    spot_mid = (spot_tickers[pair]['bidPrice'] + spot_tickers[pair]['askPrice']) / 2
    perp_mid = (perp_tickers[pair]['bidPrice'] + perp_tickers[pair]['askPrice']) / 2

    spread_pct = abs((perp_mid - spot_mid) / spot_mid * 100)

    if spread_pct > 0.15:
        # Filtrer cette paire
```

**Calcul du Spread** :
```
Exemple:
Spot mid price: 50,000 USDT
Perp mid price: 50,100 USDT

Spread absolu = |50,100 - 50,000| = 100 USDT
Spread % = 100 / 50,000 × 100 = 0.20%

→ 0.20% > 0.15% → Paire filtrée !
```

**Pourquoi 0.15% ?**
- Spread normal sur marchés liquides : 0.01% - 0.05%
- Spread > 0.15% indique :
  - Liquidité insuffisante
  - Inefficience du marché
  - Risque d'arbitrage non résolu
- Pour une stratégie DN, un spread large = risque de déséquilibre à l'ouverture

**Logging** :
```
[2025-10-12 11:30:05] Spread filter: 1 pair(s) excluded (spread > 0.15%):
  GIGGLEUSDT (7.7996%)
```

#### Filtre 4 : APR Minimum

**Objectif** : Seuil de rentabilité minimum

**Implémentation** :
```python
min_funding_apr = config['funding_rate_strategy']['min_funding_apr']  # Défaut: 7%

# Mode MA
for pair in eligible_pairs:
    income_history = await api_manager.get_income_history(pair)
    ma_result = DeltaNeutralLogic.calculate_funding_rate_ma(
        income_history,
        periods=10
    )

    if ma_result['effective_apr'] >= min_funding_apr:
        opportunities[pair] = ma_result['effective_apr']
```

**Pourquoi 7% ?**
```
Capital: 10,000 USDT
Fees par cycle: ~30 USDT (0.3%)
Durée moyenne: 3-5 jours

APR minimum pour rentabilité:
7% APR ≈ 0.019% par jour
Sur 5 jours: 0.095% = 9.5 USDT de funding

Avec fee_coverage_multiplier = 1.8:
30 × 1.8 = 54 USDT nécessaire
7% APR sur 5 jours: ~9.5 USDT ❌ Pas assez !

En réalité, le bot attend que le funding collecté
atteigne le seuil avant de fermer, donc même à 7% APR,
la position peut rester ouverte 15-20 jours si nécessaire.
```

### Mode Moving Average vs Instantané

Le bot supporte deux modes pour évaluer les funding rates :

#### Mode Moving Average (Recommandé)

**Configuration** :
```json
{
  "use_funding_ma": true,
  "funding_ma_periods": 10
}
```

**Avantages** :
- ✅ Lisse la volatilité des taux de financement
- ✅ Évite les opportunités éphémères (spikes)
- ✅ Plus stable sur la durée
- ✅ Réduit les rotations inutiles

**Processus** :
1. Récupère les 10 derniers paiements de funding
2. Calcule le taux moyen
3. Extrapole en APR : `moyenne × 3 × 365`
4. Compare avec le seuil

**Affichage** :
```
┌──────────────────────────────────────────────────────────┐
│ Symbol     │ MA APR % │ Curr APR % │ Next Funding       │
├────────────┼──────────┼────────────┼────────────────────┤
│ BTCUSDT    │   12.50  │    15.30   │ 2025-10-12 16:00   │
│ ETHUSDT    │   10.20  │     8.50   │ 2025-10-12 16:00   │
└──────────────────────────────────────────────────────────┘

MA APR : Moyenne mobile (utilisée pour la sélection)
Curr APR : Taux instantané actuel (pour comparaison)
```

#### Mode Instantané

**Configuration** :
```json
{
  "use_funding_ma": false
}
```

**Caractéristiques** :
- Utilise le taux de financement actuel directement
- Plus réactif aux changements
- Risque de "chasser" des spikes temporaires
- Peut entraîner plus de rotations

### Conditions de Fermeture d'une Position

Le bot ferme une position si **l'une des 4 conditions** est remplie :

#### Condition 1 : Stop-Loss d'Urgence

**Trigger** : Perp PnL ≤ Stop-Loss Threshold

**Important** : Utilise **uniquement le PnL perp**, pas le PnL combiné DN !

**Raison** :
- Le perp est plus volatil (effet de levier)
- Le spot est une couverture, mais pas parfaite en temps réel
- Protège contre la liquidation

**Calcul du Stop-Loss** (voir section dédiée plus bas)

**Exemple** :
```
Levier : 3x
Stop-loss auto-calculé : -24%
Valeur perp : 250 USDT
Perp PnL actuel : -65 USDT (-26%)

→ -26% < -24% → FERMER IMMÉDIATEMENT
```

#### Condition 2 : Funding Couvre les Frais

**Trigger** : `funding_received ≥ total_fees × fee_coverage_multiplier`

**Calcul** :
```python
entry_fees = 3.0 USDT
estimated_exit_fees = 3.0 USDT
total_fees = 6.0 USDT

fee_coverage_multiplier = 1.8  # Config

threshold = 6.0 × 1.8 = 10.8 USDT

if funding_received >= 10.8:
    close_position("Funding covered fees")
```

**Pourquoi 1.8x ?**
- 1.0x = Break-even (pas de profit)
- 1.8x = 80% de profit au-dessus des frais
- Balance entre rentabilité et rotation

**Exemple de Timeline** :
```
T+0h : Position ouverte, funding_received = 0
T+8h : +$2.50 funding → Total = $2.50
T+16h : +$2.40 funding → Total = $4.90
T+24h : +$2.30 funding → Total = $7.20
T+32h : +$2.10 funding → Total = $9.30
T+40h : +$2.00 funding → Total = $11.30 ≥ $10.80 ✓

→ Position fermée après 40h (5 paiements de funding)
```

#### Condition 3 : Âge Maximum

**Trigger** : `position_age ≥ max_position_age_hours`

**Configuration** :
```json
{
  "max_position_age_hours": 336  // 14 jours
}
```

**Raison** :
- Forcer la rotation même si le funding est faible
- Éviter de rester bloqué sur une paire à faible rendement
- Opportunité de capturer de meilleures paires

**Exemple** :
```
Position ouverte : 2025-10-01 10:00 UTC
Maintenant : 2025-10-15 10:00 UTC
Âge : 336 heures (14 jours)

→ max_position_age_hours = 336 → FERMER
```

#### Condition 4 : Meilleure Opportunité

**Trigger** : Nouvelle opportunité avec APR significativement plus élevé

**Implémentation** :
```python
current_symbol_apr = 10.5  # APR actuel de la position ouverte

# Trouver la meilleure opportunité
best_opportunity = await self._find_best_funding_opportunity()

if best_opportunity is None:
    return  # Pas d'autre opportunité

# Seuil : 50% meilleur
if best_opportunity['apr'] > current_symbol_apr * 1.5:
    await self._close_current_position("Better opportunity found")
```

**Exemple** :
```
Position actuelle : BTCUSDT à 10% APR
Nouvelle opportunité : ETHUSDT à 16% APR

16% > 10% × 1.5 (15%) ✓

→ Fermer BTCUSDT, ouvrir ETHUSDT
```

**Note** : Ce seuil de 1.5x évite des rotations trop fréquentes pour de petites améliorations.

---

## Système de Levier et Allocation du Capital

### Comprendre le Levier dans ce Bot

Le bot supporte des **leviers configurables de 1x à 3x** sur les contrats perpétuels. C'est une feature avancée qui améliore l'efficacité du capital.

### Formule d'Allocation du Capital

Pour une stratégie delta-neutre avec levier L :

```
Allocation Perp (marge) = 1 / (L + 1)
Allocation Spot = L / (L + 1)
```

**Démonstration Mathématique** :

Pour maintenir le delta-neutre avec levier L :
- Valeur notionnelle spot = Valeur notionnelle perp
- Capital spot = S
- Capital perp = P
- S × 1 = P × L (le perp a un effet de levier)

Donc : S = P × L

Capital total : S + P = P × L + P = P × (L + 1)

Résoudre pour P :
```
P = Capital Total / (L + 1)
S = Capital Total × L / (L + 1)
```

### Exemples d'Allocation

#### Levier 1x

```
Capital total : 1,000 USDT

Perp : 1,000 / (1 + 1) = 500 USDT (50%)
Spot : 1,000 × 1 / (1 + 1) = 500 USDT (50%)

Position :
- Acheter 500 USDT de BTC en spot
- Shorter 500 USDT de BTC en perp avec 500 USDT de marge (1x)

Exposition : 500 long + 500 short = Delta-neutre ✓
```

#### Levier 2x

```
Capital total : 1,000 USDT

Perp : 1,000 / (2 + 1) = 333.33 USDT (33.3%)
Spot : 1,000 × 2 / (2 + 1) = 666.67 USDT (66.7%)

Position :
- Acheter 666.67 USDT de BTC en spot
- Shorter 666.67 USDT de BTC en perp avec 333.33 USDT de marge (2x)

Exposition : 666.67 long + 666.67 short = Delta-neutre ✓
```

#### Levier 3x

```
Capital total : 1,000 USDT

Perp : 1,000 / (3 + 1) = 250 USDT (25%)
Spot : 1,000 × 3 / (3 + 1) = 750 USDT (75%)

Position :
- Acheter 750 USDT de BTC en spot
- Shorter 750 USDT de BTC en perp avec 250 USDT de marge (3x)

Exposition : 750 long + 750 short = Delta-neutre ✓
```

### Avantages du Levier Élevé

**Efficacité du Capital** :
```
Scénario : 10,000 USDT de capital, funding rate 0.01% (10.95% APR)

Levier 1x :
- Position notionnelle : 5,000 USDT
- Funding reçu par paiement : 5,000 × 0.01% = 0.50 USDT
- Par jour : 1.50 USDT
- Par an : ~547.50 USDT → 5.5% sur capital total

Levier 3x :
- Position notionnelle : 7,500 USDT
- Funding reçu par paiement : 7,500 × 0.01% = 0.75 USDT
- Par jour : 2.25 USDT
- Par an : ~821.25 USDT → 8.2% sur capital total

Amélioration : +50% de rendement ! 🚀
```

### Risques du Levier Élevé

**Liquidation Plus Proche** :
```
Levier 1x : Liquidation à ~-50% de mouvement
Levier 3x : Liquidation à ~-33% de mouvement

→ C'est pourquoi le bot ajuste automatiquement le stop-loss !
```

### Préservation du Levier de Position

**Principe Critique** : Le levier d'une position ouverte **ne change jamais** jusqu'à sa fermeture.

#### Séparation Config vs Position

```python
# Configuration
config['leverage_settings']['leverage'] = 3

# État de la position
state['position_leverage'] = 2  # Peut être différent !
```

**Pourquoi cette Séparation ?**
- L'utilisateur peut changer le config pendant qu'une position est ouverte
- Changer le levier mid-position déséquilibrerait la position delta-neutre
- Le nouveau levier s'applique **uniquement à la prochaine position**

#### Cycle de Vie du Levier

```
Séquence 1 : Position avec Levier 2x
─────────────────────────────────────
1. Config : leverage = 2
2. Ouvrir position → position_leverage = 2
3. L'utilisateur change config : leverage = 3
4. Position ouverte maintient position_leverage = 2 ✓
5. Fermer position
6. Rebalancer USDT pour leverage = 3
7. Ouvrir nouvelle position → position_leverage = 3
```

#### Détection de Levier au Démarrage

Lors du démarrage, si le bot détecte une position existante :

```python
async def _reconcile_position_state(self):
    # Obtenir le levier depuis l'exchange
    exchange_leverage = await self.api_manager.get_perp_leverage(symbol)

    if exchange_leverage:
        self.state['position_leverage'] = exchange_leverage
        logger.info(f"[LEVERAGE] Detected: {exchange_leverage}x")
    else:
        # Fallback au config
        self.state['position_leverage'] = self.config['leverage_settings']['leverage']
        logger.warning("[LEVERAGE] Could not detect, using config")
```

#### Avertissement de Mismatch

Si `position_leverage != config.leverage` :

```
╔══════════════════════════════════════════════════════════╗
║            ⚠️  LEVERAGE MISMATCH DETECTED                ║
╟──────────────────────────────────────────────────────────╢
║  Position Leverage : 2x                                  ║
║  Config Leverage   : 3x                                  ║
║                                                          ║
║  The position will maintain 2x leverage until closed.    ║
║  New positions will use 3x leverage from config.         ║
╚══════════════════════════════════════════════════════════╝
```

### Rebalancement USDT

Avant d'ouvrir une position, le bot **rebalance les USDT** entre les wallets spot et perp :

```python
async def rebalance_usdt_by_leverage(self, leverage: int) -> bool:
    # 1. Obtenir les balances actuelles
    spot_balance = await self.get_spot_balance('USDT')
    perp_balance = await self.get_perp_balance('USDT')
    total_usdt = spot_balance + perp_balance

    # 2. Calculer les allocations cibles
    target_perp = total_usdt / (leverage + 1)
    target_spot = total_usdt * leverage / (leverage + 1)

    # 3. Transférer si nécessaire
    if spot_balance < target_spot:
        # Transférer perp → spot
        amount = target_spot - spot_balance
        await self.transfer_usdt('PERP_TO_SPOT', amount)

    elif perp_balance < target_perp:
        # Transférer spot → perp
        amount = target_perp - perp_balance
        await self.transfer_usdt('SPOT_TO_PERP', amount)

    return True
```

**Exemple** :
```
Avant rebalancement (leverage = 3) :
- Spot : 300 USDT
- Perp : 700 USDT
- Total : 1,000 USDT

Cible :
- Spot : 1,000 × 3/4 = 750 USDT
- Perp : 1,000 × 1/4 = 250 USDT

Action :
- Transférer 450 USDT de Perp vers Spot

Après rebalancement :
- Spot : 750 USDT ✓
- Perp : 250 USDT ✓
```

---

## Gestion des Risques

### Stop-Loss Automatique Calculé

**Principe** : Le stop-loss est **automatiquement calculé** pour chaque levier, pas un paramètre manuel.

### Formule de Calcul

```python
def _calculate_safe_stoploss(self, leverage: int) -> float:
    """
    Calcule le stop-loss sûr basé sur le levier.

    Formule :
    SL = [(1 + 1/L) / (1 + m) - 1 - b] × [L / (L + 1)]

    Où :
    L = leverage
    m = maintenance_margin (0.005 = 0.5%)
    b = safety_buffer (0.007 = 0.7%)
    """
    maintenance_margin = 0.005  # 0.5% (règle ASTER DEX)
    safety_buffer = 0.007       # 0.7% (fees + slippage + volatilité)

    perp_fraction = leverage / (leverage + 1)
    liquidation_price_ratio = (1 + 1/leverage) / (1 + maintenance_margin)
    safe_price_ratio = liquidation_price_ratio - 1 - safety_buffer

    stop_loss_pct = safe_price_ratio * perp_fraction

    return stop_loss_pct
```

### Valeurs de Stop-Loss

| Levier | Stop-Loss | Distance Liquidation |
|--------|-----------|----------------------|
| 1x     | -50.0%    | ~50%                 |
| 2x     | -33.0%    | ~33%                 |
| 3x     | -24.0%    | ~25%                 |

### Explication du Safety Buffer (0.7%)

Le safety buffer inclut :

1. **Frais de trading** : ~0.1%
   - Fermeture spot : ~0.1%
   - Fermeture perp : ~0.05%

2. **Slippage** : ~0.2%
   - Market orders pendant urgence
   - Moins de liquidité sur gros ordres

3. **Volatilité** : ~0.4%
   - Mouvement de prix entre détection et exécution
   - Latence réseau

**Total : 0.7%** → Marge de sécurité confortable

### Exemple de Calcul (Levier 3x)

```
Entrées :
- Leverage (L) = 3
- Maintenance Margin (m) = 0.5%
- Safety Buffer (b) = 0.7%

Étape 1 : Perp Fraction
perp_fraction = 3 / (3 + 1) = 0.75 (75% du capital en perp notionnel)

Étape 2 : Liquidation Price Ratio
liquidation_ratio = (1 + 1/3) / (1 + 0.005)
                  = 1.333 / 1.005
                  = 1.326

Étape 3 : Safe Price Ratio
safe_ratio = 1.326 - 1 - 0.007
           = 0.319

Étape 4 : Stop-Loss
stop_loss = 0.319 × 0.75
          = 0.239 = 23.9% ≈ 24%
```

### Application du Stop-Loss

**Important** : Le stop-loss s'applique au **PnL Perp**, pas au PnL combiné DN !

```python
# Dans _evaluate_existing_position()

perp_position = await api_manager.get_perp_positions(symbol)
perp_pnl = float(perp_position['unrealizedProfit'])

# Valeur de la position perp
perp_value = capital_allocated_usdt * perp_fraction

# Stop-loss en USDT
stop_loss_pct = self._calculate_safe_stoploss(position_leverage)
stop_loss_usdt = perp_value * stop_loss_pct  # Négatif

# Vérification
if perp_pnl <= stop_loss_usdt:
    logger.error(f"STOP-LOSS TRIGGERED! Perp PnL: ${perp_pnl:.2f} ≤ ${stop_loss_usdt:.2f}")
    await self._close_current_position("Emergency stop-loss")
```

**Exemple Numérique** :
```
Position :
- Capital total : 1,000 USDT
- Levier : 3x
- Perp fraction : 25% (250 USDT de marge)
- Stop-loss : -24%

Calcul :
Stop-loss USDT = 250 × (-0.24) = -60 USDT

Scénario :
Perp PnL actuel : -65 USDT

-65 ≤ -60 ? OUI → FERMER IMMÉDIATEMENT ⚠️
```

### Health Checks Continus

À chaque cycle, le bot effectue des health checks :

#### Check 1 : Balances USDT

```python
spot_usdt = await api_manager.get_spot_balance('USDT')
perp_usdt = await api_manager.get_perp_balance('USDT')

if spot_usdt < 10 and perp_usdt < 10:
    logger.error("Insufficient USDT balance in both wallets")
    return False
```

#### Check 2 : Levier Valide

```python
if not (1 <= position_leverage <= 3):
    logger.critical(f"Invalid leverage: {position_leverage}")
    return False
```

#### Check 3 : Déséquilibre de Position

```python
imbalance_pct = abs(spot_qty - perp_qty) / spot_qty * 100

if imbalance_pct > 10:
    logger.critical(f"Critical imbalance: {imbalance_pct:.2f}%")
    return False

if imbalance_pct > 5:
    logger.warning(f"Warning: imbalance {imbalance_pct:.2f}%")
```

**Pourquoi le Déséquilibre est-il Important ?**
```
Exemple de déséquilibre :
- Spot : 0.100 BTC
- Perp : 0.085 BTC
- Déséquilibre : 15%

Si BTC monte de 10% :
- Spot PnL : +10% × 0.100 = +0.010 BTC
- Perp PnL : -10% × 0.085 = -0.0085 BTC
- Net : +0.0015 BTC → Exposition directionnelle !

→ Plus delta-neutre ❌
```

#### Check 4 : Valeur Minimale

```python
if position_value < 5:
    logger.error("Position value too small (< $5)")
    return False
```

### Sortie d'Urgence Manuelle

Le script `emergency_exit.py` permet une fermeture manuelle immédiate :

```bash
$ python emergency_exit.py

╔══════════════════════════════════════════════════════════╗
║              EMERGENCY POSITION EXIT                     ║
╚══════════════════════════════════════════════════════════╝

Current Position:
  Symbol    : BTCUSDT
  Leverage  : 3x
  Capital   : 1,000.00 USDT
  Entry Price: 50,000.00 USDT
  Opened    : 2025-10-10 14:00:00 UTC (2 days ago)

Current PnL:
  Perp PnL  : -15.50 USDT
  Spot PnL  : +12.30 USDT
  Funding   : +8.20 USDT
  Fees      : -6.00 USDT
  ─────────────────────────
  Net DN PnL: -1.00 USDT

⚠️  WARNING: This will close both spot and perp positions
    immediately using MARKET orders (potential slippage).

Type 'CONFIRM' to proceed: _
```

---

## Calculs et Suivi des Profits/Pertes

### Trois Niveaux de PnL

Le bot calcule **3 types de PnL** :

1. **Perp Unrealized PnL** : PnL de la position perpétuelle (de l'exchange)
2. **Spot Unrealized PnL** : PnL de la position spot (calculé)
3. **Combined DN PnL (net)** : PnL total de la stratégie DN incluant funding et frais

### 1. Perp Unrealized PnL

**Source** : Directement de l'exchange via l'API

```python
perp_positions = await api_manager.get_perp_positions()
perp_pnl = float(perp_positions[0]['unrealizedProfit'])
```

**Calcul de l'Exchange** :
```
Perp PnL = Position Size × (Entry Price - Mark Price) × Direction

Pour un SHORT :
PnL = Quantity × (Entry Price - Current Price)
```

**Exemple** :
```
Position :
- Type : SHORT
- Quantité : 0.015 BTC
- Entry : 50,000 USDT
- Current : 49,000 USDT

PnL = 0.015 × (50,000 - 49,000) = 0.015 × 1,000 = +15 USDT
```

**Usage** : Ce PnL est utilisé pour le **stop-loss trigger** car il est le plus volatil.

### 2. Spot Unrealized PnL

**Calcul Manuel** :
```python
spot_pnl = spot_qty × (current_price - entry_price)
```

**Pourquoi Manual ?**
- L'exchange spot ne calcule pas de PnL unrealized
- On doit tracker l'entry price dans l'état

**Important** : `entry_price` est sauvegardé dans `volume_farming_state.json`

**Exemple** :
```
Position :
- Type : LONG (spot)
- Quantité : 0.015 BTC
- Entry : 50,000 USDT
- Current : 49,000 USDT

Spot PnL = 0.015 × (49,000 - 50,000) = 0.015 × (-1,000) = -15 USDT
```

**Fallback** : Si `entry_price` manque dans l'état, le bot utilise `perp_position['entryPrice']` comme approximation.

### 3. Combined DN PnL (Net)

**Formule Complète** :
```
Combined DN PnL = Spot PnL + Perp PnL + Funding Received - Entry Fees - Exit Fees (estimé)
```

**Composants** :

1. **Spot PnL** : Calculé comme ci-dessus
2. **Perp PnL** : De l'exchange
3. **Funding Received** : Somme de tous les paiements depuis l'ouverture
4. **Entry Fees** : Sauvegardé lors de l'ouverture
5. **Exit Fees** : Estimé à ~0.15% de la position

**Code d'Implémentation** :
```python
def _calculate_combined_pnl(self, current_price):
    # 1. Spot PnL
    entry_price = self.state.get('entry_price', current_price)
    spot_qty = self.state['spot_qty']
    spot_pnl = spot_qty * (current_price - entry_price)

    # 2. Perp PnL
    perp_position = await api_manager.get_perp_positions(symbol)
    perp_pnl = float(perp_position['unrealizedProfit'])

    # 3. Funding Received
    funding_received = self.state['funding_received_usdt']

    # 4. Frais
    entry_fees = self.state['entry_fees_usdt']
    position_value = self.state['capital_allocated_usdt']
    exit_fees_estimate = position_value * 0.0015  # 0.15%

    # 5. Combined
    combined_pnl = spot_pnl + perp_pnl + funding_received - entry_fees - exit_fees_estimate

    return {
        'spot_pnl': spot_pnl,
        'perp_pnl': perp_pnl,
        'funding_received': funding_received,
        'entry_fees': entry_fees,
        'exit_fees_estimate': exit_fees_estimate,
        'combined_pnl': combined_pnl
    }
```

**Exemple Complet** :
```
Position : BTCUSDT, 1,000 USDT capital, 3x leverage

État Actuel :
- Entry price : 50,000 USDT
- Current price : 50,500 USDT (+1%)
- Spot qty : 0.015 BTC
- Perp qty : 0.015 BTC

Calculs :
1. Spot PnL = 0.015 × (50,500 - 50,000) = 0.015 × 500 = +7.50 USDT
2. Perp PnL = 0.015 × (50,000 - 50,500) = -7.50 USDT (exchange value)
3. Funding Received = 12.50 USDT (3 paiements)
4. Entry Fees = 3.00 USDT
5. Exit Fees (estimé) = 1,000 × 0.0015 = 1.50 USDT

Combined DN PnL = 7.50 - 7.50 + 12.50 - 3.00 - 1.50 = +8.00 USDT ✅
```

**Interprétation** :
- Spot et Perp s'annulent (delta-neutre fonctionnel)
- Le profit provient du funding (+12.50)
- Après frais, profit net : +8.00 USDT

### 4. Portfolio PnL Total

Le bot track également le **PnL total du portfolio** depuis le début :

#### Capture de la Baseline Initiale

**Une seule fois**, lors du premier lancement :

```python
async def _capture_initial_portfolio(self):
    if 'initial_portfolio_value_usdt' in self.state:
        return  # Déjà capturé

    # Calculer la valeur totale actuelle
    current_value = await self._get_current_portfolio_value()

    # Sauvegarder comme baseline
    self.state['initial_portfolio_value_usdt'] = current_value
    self.state['initial_portfolio_timestamp'] = datetime.utcnow().isoformat()

    logger.info(f"📊 Initial portfolio baseline: ${current_value:.2f}")
```

#### Calcul de la Valeur Portfolio Actuelle

**Inclut TOUS les assets**, pas seulement USDT :

```python
async def _get_current_portfolio_value(self) -> float:
    # 1. Valeur Spot (tous les assets)
    spot_balances = await api_manager.get_spot_balances()
    spot_total_usdt = 0.0

    for asset, balance in spot_balances.items():
        if balance > 0:
            if asset == 'USDT':
                spot_total_usdt += balance
            else:
                # Obtenir le prix actuel
                symbol = f"{asset}USDT"
                price = await api_manager.get_spot_ticker_price(symbol)
                spot_total_usdt += balance * price

    # 2. Wallet Perp (USDT)
    perp_wallet = await api_manager.get_perp_balance('USDT')

    # 3. Unrealized PnL Perp
    perp_positions = await api_manager.get_perp_positions()
    perp_unrealized = sum(float(pos['unrealizedProfit']) for pos in perp_positions)

    # 4. Total
    total_value = spot_total_usdt + perp_wallet + perp_unrealized

    return total_value
```

**Exemple** :
```
Balances :
- Spot USDT : 2,000
- Spot BTC : 0.05 @ 50,000 = 2,500
- Spot ETH : 1.2 @ 3,000 = 3,600
- Perp Wallet : 1,500
- Perp Unrealized PnL : -50

Total = 2,000 + 2,500 + 3,600 + 1,500 - 50 = 9,550 USDT
```

#### Calcul du PnL Total

```python
async def _calculate_total_portfolio_pnl(self):
    if 'initial_portfolio_value_usdt' not in self.state:
        return None

    initial_value = self.state['initial_portfolio_value_usdt']
    current_value = await self._get_current_portfolio_value()

    pnl_usdt = current_value - initial_value
    pnl_pct = (pnl_usdt / initial_value) * 100

    return {
        'initial_value': initial_value,
        'current_value': current_value,
        'pnl_usdt': pnl_usdt,
        'pnl_pct': pnl_pct,
        'since': self.state['initial_portfolio_timestamp']
    }
```

#### Affichage dans le Header de Cycle

```
╔══════════════════════════════════════════════════════════════════╗
║         CHECK #42 | Trading Cycles Completed: 5                  ║
╟──────────────────────────────────────────────────────────────────╢
║  📊 Portfolio: $9,550.32 | PnL: +$550.32 (+6.11%)                ║
║      Since: 2025-10-08 12:00 UTC                                 ║
╚══════════════════════════════════════════════════════════════════╝
```

**Couleurs** :
- PnL positif : Vert
- PnL négatif : Rouge

### Affichage des PnL

Le bot affiche les PnL de manière claire et colorée :

```
═══════════════════════════════════════════════════════════
                    POSITION EVALUATION
═══════════════════════════════════════════════════════════

Symbol          : BTCUSDT
Position Age    : 2 days, 5 hours
Capital         : 1,000.00 USDT
Leverage        : 3x

───────────────────────────────────────────────────────────
                       CURRENT PNL
───────────────────────────────────────────────────────────

Perp Unrealized PnL    : -15.50 USDT (-6.2%)
Spot Unrealized PnL    : +12.30 USDT (+1.6%)
Funding Received       : +8.20 USDT
Entry Fees             : -6.00 USDT
Exit Fees (est.)       : -1.50 USDT
───────────────────────────────────────────────────────────
Combined DN PnL (net)  : -2.50 USDT (-0.25%) ⚠️

═══════════════════════════════════════════════════════════
```

---

## Filtrage des Paires de Trading

Le bot implémente un **système de filtrage à 4 niveaux** pour garantir la qualité :

### Pipeline de Filtrage

```
Toutes les paires (spot ∩ perp)
          ↓
    ┌─────────────────────┐
    │  Filtre 1: Volume   │
    │    ≥ $250M 24h      │
    └─────────┬───────────┘
              ↓
    ┌─────────────────────┐
    │  Filtre 2: Taux     │
    │   Current > 0%      │
    └─────────┬───────────┘
              ↓
    ┌─────────────────────┐
    │  Filtre 3: Spread   │
    │    ≤ 0.15%          │
    └─────────┬───────────┘
              ↓
    ┌─────────────────────┐
    │  Filtre 4: APR Min  │
    │    ≥ min_apr        │
    └─────────┬───────────┘
              ↓
      Paires éligibles
```

### Logs de Filtrage

Le bot affiche des résumés colorés pour chaque filtre :

```
[2025-10-12 11:30:00] Volume filter: 35 pair(s) meet ≥$250M requirement

[2025-10-12 11:30:01] Negative rate filter: 3 pair(s) excluded:
  BTCUSDT (-0.0050%), ETHUSDT (-0.0023%), SOLUSDT (-0.0012%)

[2025-10-12 11:30:02] Spread filter: 2 pair(s) excluded (spread > 0.15%):
  GIGGLEUSDT (7.7996%), NEWCOINUSDT (0.2500%)

[2025-10-12 11:30:03] APR filter: 28 pair(s) meet minimum APR threshold

[2025-10-12 11:30:04] ✅ Best opportunity found: AVAXUSDT (MA APR: 15.30%)
```

### Utilisation des Scripts de Vérification

#### `check_funding_rates.py`

Affiche les taux de financement et filtrage par volume :

```bash
$ python check_funding_rates.py

╔══════════════════════════════════════════════════════════════════╗
║         ASTER DEX - FUNDING RATE ANALYSIS (DELTA-NEUTRAL)        ║
╚══════════════════════════════════════════════════════════════════╝

════════════════════════════════════════════════════════════════════
           ELIGIBLE PAIRS (≥$250M Volume + Positive Rate)
════════════════════════════════════════════════════════════════════

┌────────────┬──────────────┬──────────────┬──────────────────────┐
│ Symbol     │ Current APR  │ 24h Volume   │ Next Funding         │
├────────────┼──────────────┼──────────────┼──────────────────────┤
│ AVAXUSDT   │   15.30%     │  $320.5M     │ 2025-10-12 16:00 UTC │
│ MATICUSDT  │   12.80%     │  $285.2M     │ 2025-10-12 16:00 UTC │
│ OPUSDT     │   10.95%     │  $265.8M     │ 2025-10-12 16:00 UTC │
└────────────┴──────────────┴──────────────┴──────────────────────┘

════════════════════════════════════════════════════════════════════
              FILTERED PAIRS (Low Volume or Negative Rate)
════════════════════════════════════════════════════════════════════

┌────────────┬──────────────┬──────────────┬─────────────────┐
│ Symbol     │ Current APR  │ 24h Volume   │ Exclusion Reason│
├────────────┼──────────────┼──────────────┼─────────────────┤
│ BTCUSDT    │   -0.05%     │  $1.2B       │ Negative rate   │
│ LOWVOLCOIN │   20.00%     │  $50M        │ Low volume      │
└────────────┴──────────────┴──────────────┴─────────────────┘

════════════════════════════════════════════════════════════════════
                            SUMMARY
════════════════════════════════════════════════════════════════════

Total Delta-Neutral Pairs    : 45
Eligible Pairs               : 28 (62.2%)
Filtered Pairs               : 17 (37.8%)
  • Low Volume (<$250M)      : 12
  • Negative Funding Rate    : 5

Best Opportunity             : AVAXUSDT (15.30% APR)
```

#### `check_spot_perp_spreads.py`

Analyse les spreads de prix :

```bash
$ python check_spot_perp_spreads.py

╔══════════════════════════════════════════════════════════════════╗
║        ASTER DEX - SPOT-PERP PRICE SPREAD ANALYSIS               ║
╚══════════════════════════════════════════════════════════════════╝

════════════════════════════════════════════════════════════════════
                      PRICE SPREAD ANALYSIS
════════════════════════════════════════════════════════════════════

┌─────────┬────────────┬────────────┬─────────┬─────────┬────────┐
│ Symbol  │ Spot Mid   │ Perp Mid   │ Abs Diff│ Spread %│ Status │
├─────────┼────────────┼────────────┼─────────┼─────────┼────────┤
│ BTCUSDT │ 50,000.00  │ 50,005.00  │   5.00  │  0.01%  │   ✅   │
│ ETHUSDT │  3,000.00  │  3,001.50  │   1.50  │  0.05%  │   ✅   │
│ AVAXUSDT│    35.20   │    35.25   │   0.05  │  0.14%  │   ✅   │
│ GIGGLE  │    10.00   │    10.78   │   0.78  │  7.80%  │   ❌   │
└─────────┴────────────┴────────────┴─────────┴─────────┴────────┘

Légende:
  ✅ Green  : Spread < 0.05% (excellent)
  🟡 Yellow : Spread 0.05-0.1% (acceptable)
  🟠 Orange : Spread 0.1-0.15% (limite)
  ❌ Red    : Spread > 0.15% (filtré)

════════════════════════════════════════════════════════════════════
                            SUMMARY
════════════════════════════════════════════════════════════════════

Total Pairs Analyzed         : 45
Pairs Passing Filter (≤0.15%): 43 (95.6%)
Pairs Filtered (>0.15%)      : 2 (4.4%)

Average Spread               : 0.08%
Largest Spread               : 7.80% (GIGGLEUSDT)
Smallest Spread              : 0.01% (BTCUSDT)

Perp Premium Count           : 38 (84.4%)
Perp Discount Count          : 7 (15.6%)
```

---

## Configuration et Déploiement

### Structure du Fichier de Configuration

`config_volume_farming_strategy.json` :

```json
{
  "capital_management": {
    "capital_fraction": 0.98
  },
  "funding_rate_strategy": {
    "min_funding_apr": 5.4,
    "use_funding_ma": true,
    "funding_ma_periods": 10
  },
  "position_management": {
    "fee_coverage_multiplier": 1.1,
    "max_position_age_hours": 336,
    "loop_interval_seconds": 900
  },
  "leverage_settings": {
    "leverage": 3
  }
}
```

### Paramètres Détaillés

#### capital_management

**`capital_fraction`** (float, 0-1)
- Fraction du capital USDT total à utiliser par position
- Défaut : 0.98 (98%)
- Laisse 2% en réserve pour les frais et variations

**Exemple** :
```
Total USDT disponible : 10,000
capital_fraction : 0.98

Capital alloué = 10,000 × 0.98 = 9,800 USDT
Réserve = 200 USDT
```

#### funding_rate_strategy

**`min_funding_apr`** (float, %)
- APR minimum pour considérer une opportunité
- Défaut : 5.4%
- Plus bas = plus d'opportunités, moins de rentabilité
- Plus haut = moins d'opportunités, meilleure rentabilité

**`use_funding_ma`** (boolean)
- true : Utilise la moyenne mobile des funding rates (recommandé)
- false : Utilise le taux instantané actuel
- Défaut : true

**`funding_ma_periods`** (int)
- Nombre de périodes pour la MA
- Défaut : 10 (= 10 × 8h = 80 heures ≈ 3.3 jours)
- Plus élevé = plus lisse, moins réactif
- Plus bas = moins lisse, plus réactif

#### position_management

**`fee_coverage_multiplier`** (float)
- Facteur multiplicateur pour les frais avant fermeture
- Défaut : 1.1 (110%)
- 1.0 = break-even
- 1.5 = 50% de profit au-dessus des frais
- 2.0 = 100% de profit au-dessus des frais

**Recommandation** :
- Trading agressif : 1.1 - 1.3
- Trading équilibré : 1.5 - 1.8
- Trading conservateur : 2.0+

**`max_position_age_hours`** (int, heures)
- Durée maximale de maintien d'une position
- Défaut : 336 heures (14 jours)
- Force la rotation même si funding faible

**`loop_interval_seconds`** (int, secondes)
- Intervalle entre chaque cycle de vérification
- Défaut : 900 secondes (15 minutes)
- Plus court = plus réactif, plus de requêtes API
- Plus long = moins réactif, moins de requêtes API

#### leverage_settings

**`leverage`** (int, 1-3)
- Levier pour les positions perpétuelles
- Défaut : 3
- 1x : Moins risqué, moins efficace
- 2x : Équilibré
- 3x : Plus efficace, plus proche de la liquidation

**Important** :
- Le stop-loss est automatiquement calculé (pas de paramètre)
- Les changements s'appliquent aux NOUVELLES positions uniquement

### Variables d'Environnement (.env)

```env
# API v3 (Perpetual - Pro API)
API_USER=0xYourEthereumWalletAddress
API_SIGNER=0xYourApiSignerAddress
API_PRIVATE_KEY=0xYourPrivateKey

# API v1 (Spot - API)
APIV1_PUBLIC_KEY=your_public_key_here
APIV1_PRIVATE_KEY=your_private_key_here
```

**Obtention des Clés** : Voir la section API Authentication dans CLAUDE.md

### Déploiement Docker

#### docker-compose.yml

```yaml
version: '3.8'

networks:
  default:
    driver: bridge
    ipam:
      config:
        - subnet: 172.8.144.0/22

services:
  dn_bot:
    build: .
    container_name: dn_farming_bot
    deploy:
      resources:
        limits:
          cpus: '0.5'
          memory: '512M'
    env_file:
      - .env
    restart: unless-stopped
    stdin_open: true
    tty: true
    volumes:
      - ./:/app/
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
```

#### Commandes Docker

**Démarrer le bot** :
```bash
docker-compose up --build
```

**En arrière-plan** :
```bash
docker-compose up --build -d
```

**Voir les logs** :
```bash
docker-compose logs -f
```

**Arrêter le bot** :
```bash
docker-compose down
```

**Redémarrer** :
```bash
docker-compose restart
```

### Déploiement Local

**Prérequis** : Python 3.8+ (3.10+ recommandé)

```bash
# 1. Créer environnement virtuel
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1

# 2. Installer dépendances
pip install -r requirements.txt

# 3. Configurer .env
cp .env.example .env
# Éditer .env avec vos clés API

# 4. Lancer le bot
python volume_farming_strategy.py
```

### Premier Lancement

Au premier lancement, le bot :

1. **Charge la configuration**
2. **Se connecte à l'API**
3. **Vérifie les balances**
4. **Capture la baseline du portfolio**
5. **Vérifie les positions existantes**
6. **Démarre le cycle de trading**

**Logs typiques** :
```
[2025-10-12 10:00:00] INFO - Bot starting...
[2025-10-12 10:00:01] INFO - Config loaded: leverage=3x, min_apr=5.4%
[2025-10-12 10:00:02] INFO - 📊 Initial portfolio baseline: $10,000.00
[2025-10-12 10:00:03] INFO - No existing position found
[2025-10-12 10:00:04] INFO - [LEVERAGE] Auto-calculated stop-loss: -24.0%
[2025-10-12 10:00:05] INFO - Starting main strategy loop...
```

---

## Scripts Utilitaires

### `check_funding_rates.py`

**Usage** : Analyser les funding rates sans lancer le bot

```bash
python check_funding_rates.py
```

**Fonctionnalités** :
- Liste toutes les paires delta-neutres
- Affiche les funding rates actuels en APR
- Applique les filtres (volume $250M, taux positif)
- Identifie la meilleure opportunité
- Affiche les statistiques récapitulatives

**Cas d'usage** :
- Vérifier les opportunités avant de démarrer le bot
- Débugger pourquoi certaines paires sont exclues
- Analyser les tendances du marché

### `check_spot_perp_spreads.py`

**Usage** : Analyser les spreads de prix spot-perp

```bash
python check_spot_perp_spreads.py
```

**Fonctionnalités** :
- Récupère les prix mid spot et perp
- Calcule le spread absolu et pourcentage
- Color-code selon le niveau de spread
- Identifie les paires problématiques
- Statistiques (moyenne, min, max, premium/discount)

**Cas d'usage** :
- Identifier les problèmes de liquidité
- Débugger les exclusions par spread
- Détecter les opportunités d'arbitrage

### `emergency_exit.py`

**Usage** : Fermer manuellement une position immédiatement

```bash
python emergency_exit.py
```

**Fonctionnalités** :
- Lit la position depuis l'état
- Affiche les détails complets (symbole, levier, capital, PnL)
- Demande confirmation explicite
- Ferme les deux jambes simultanément (market orders)
- Met à jour le fichier d'état

**Cas d'usage** :
- Urgence (événement de marché majeur)
- Intervention manuelle nécessaire
- Test de fermeture sans attendre le bot

**⚠️ Avertissements** :
- Utilise des market orders (risque de slippage)
- Fermeture immédiate (pas de timing optimal)
- À utiliser uniquement en cas de nécessité

### `calculate_safe_stoploss.py`

**Usage** : Valider les calculs de stop-loss

```bash
python calculate_safe_stoploss.py
```

**Sortie** :
```
╔══════════════════════════════════════════════════════════╗
║        SAFE STOP-LOSS CALCULATIONS                       ║
╚══════════════════════════════════════════════════════════╝

Parameters:
  Maintenance Margin  : 0.50%
  Safety Buffer       : 0.70%

═══════════════════════════════════════════════════════════

Leverage 1x:
  Perp Fraction       : 50.0%
  Liquidation Distance: ~50.0%
  Safe Stop-Loss      : -50.0%
  Safety Margin       : 0.7%

Leverage 2x:
  Perp Fraction       : 33.3%
  Liquidation Distance: ~33.3%
  Safe Stop-Loss      : -33.0%
  Safety Margin       : 0.7%

Leverage 3x:
  Perp Fraction       : 25.0%
  Liquidation Distance: ~25.0%
  Safe Stop-Loss      : -24.0%
  Safety Margin       : 0.7%

═══════════════════════════════════════════════════════════
```

**Cas d'usage** :
- Comprendre les calculs de stop-loss
- Vérifier les marges de sécurité
- Valider les modifications de formule

### `get_volume_24h.py`

**Usage** : Obtenir le volume 24h pour une paire spécifique

```bash
python get_volume_24h.py BTCUSDT
```

**Sortie** :
```
BTCUSDT 24h Volume: $1,250,500,000 (1.25B)
Status: ✅ Passes $250M filter
```

**Cas d'usage** :
- Vérifier rapidement le volume d'une paire
- Confirmer si une paire est éligible
- Surveiller l'évolution du volume

---

## Monitoring et Debugging

### Fichiers de Log

**`volume_farming.log`**
- Tous les événements du bot
- Rotation automatique : 10 MB max, 3 fichiers conservés
- Niveaux : DEBUG, INFO, WARNING, ERROR, CRITICAL

**Format de Log** :
```
[2025-10-12 11:30:00] INFO - Message here
[2025-10-12 11:30:01] WARNING - Warning message
[2025-10-12 11:30:02] ERROR - Error message
```

### Filtrer les Logs

**Logs de leverage** :
```bash
grep "\[LEVERAGE\]" volume_farming.log
```

**Logs d'erreur** :
```bash
grep "ERROR" volume_farming.log
```

**Logs de fermeture de position** :
```bash
grep "Closing position" volume_farming.log
```

**Dernières 50 lignes** :
```bash
tail -50 volume_farming.log
```

**Suivi en temps réel** :
```bash
tail -f volume_farming.log
```

### Fichier d'État

**`volume_farming_state.json`**

**Consulter l'état** :
```bash
cat volume_farming_state.json | python -m json.tool
```

**Vérifier le levier** :
```bash
cat volume_farming_state.json | grep position_leverage
```

**Vérifier le PnL baseline** :
```bash
cat volume_farming_state.json | grep initial_portfolio
```

### Problèmes Courants

#### Problème : "Leverage mismatch detected"

**Symptôme** :
```
⚠️  LEVERAGE MISMATCH DETECTED
Position Leverage : 2x
Config Leverage   : 3x
```

**Cause** : Config modifié pendant qu'une position est ouverte

**Solution** : Normal ! La position maintiendra 2x jusqu'à sa fermeture. La prochaine position utilisera 3x.

**Action** : Aucune (sauf si vous voulez forcer la fermeture avec `emergency_exit.py`)

---

#### Problème : "Could not detect leverage"

**Symptôme** :
```
[LEVERAGE] Could not detect leverage from exchange, using config: 3x
```

**Cause** :
- API error temporaire
- Pas de position perp sur l'exchange
- Problème de connexion

**Solution** : Le bot fallback au config, vérifier manuellement :
```bash
python tests/test_leverage_detection.py
```

---

#### Problème : Spot PnL showing $0.00

**Symptôme** : Spot PnL affiché à $0.00 malgré une position ouverte

**Cause** : `entry_price` manquant dans le fichier d'état

**Solution** :
1. Le bot auto-corrige en utilisant `perp_position['entryPrice']`
2. Attendre le prochain cycle d'évaluation
3. Ou relancer le bot (il réconciliera l'état)

---

#### Problème : Portfolio value too low

**Symptôme** : La valeur du portfolio semble ne compter que l'USDT

**Cause** : Bug dans `_get_current_portfolio_value()` - ne fetch pas les prix des autres assets

**Solution** : Vérifier que le code fetch bien les prix pour BTC, ETH, etc.

```python
# Devrait ressembler à ça :
for asset, balance in spot_balances.items():
    if asset != 'USDT' and balance > 0:
        symbol = f"{asset}USDT"
        price = await api_manager.get_spot_ticker_price(symbol)
        spot_total_usdt += balance * price
```

---

#### Problème : Bot not trading certain pairs

**Symptôme** : Le bot ignore des paires avec bon funding rate

**Diagnostic** :
```bash
# 1. Vérifier volume et funding rate
python check_funding_rates.py

# 2. Vérifier spread spot-perp
python check_spot_perp_spreads.py
```

**Causes possibles** :
- Volume < $250M
- Taux actuel négatif (même si MA positive)
- Spread > 0.15%
- APR < min_funding_apr

---

#### Problème : "Insufficient USDT balance"

**Symptôme** :
```
ERROR - Insufficient USDT balance in both wallets
```

**Cause** : Pas assez d'USDT pour ouvrir une position

**Solution** :
1. Déposer plus d'USDT
2. Réduire `capital_fraction` dans le config
3. Vérifier si USDT bloqué dans des ordres ouverts

---

#### Problème : API errors / Rate limiting

**Symptôme** :
```
ERROR - API request failed: 429 Too Many Requests
```

**Cause** : Trop de requêtes API

**Solution** :
- Augmenter `loop_interval_seconds` (ex: 1800 = 30 min)
- Vérifier qu'il n'y a pas plusieurs bots sur les mêmes clés API
- Attendre que le rate limit se réinitialise

---

### Performance Metrics

Le bot affiche des métriques dans chaque cycle :

```
═══════════════════════════════════════════════════════════
               PERFORMANCE METRICS
═══════════════════════════════════════════════════════════

Total Trading Cycles    : 5
Successful Closures     : 5 (100%)
Emergency Exits         : 0
Average Position Age    : 4.2 days
Total Funding Collected : $125.50
Total Fees Paid         : $90.00
Net Profit              : +$35.50

Portfolio Performance:
  Initial Value         : $10,000.00
  Current Value         : $10,035.50
  Total PnL             : +$35.50 (+0.36%)
  Duration              : 4 days

Annualized Return       : ~32.9% APR
```

---

## Exemples Concrets et Cas d'Usage

### Scénario 1 : Position Typique Rentable

**Configuration** :
- Capital : 1,000 USDT
- Levier : 3x
- Paire : AVAXUSDT
- MA APR : 15.30%

**Timeline** :

**T+0h (Ouverture)** :
```
Prix AVAX : 35.00 USDT

Allocation :
- Spot : 750 USDT → 21.43 AVAX
- Perp : 250 USDT margin, short 21.43 AVAX @ 3x

Frais d'entrée : 3.00 USDT

État :
- Position ouverte ✓
- Funding reçu : 0
- PnL combiné : -3.00 (frais)
```

**T+8h (1er funding)** :
```
Prix AVAX : 35.20 (+0.57%)

PnL :
- Spot : 21.43 × (35.20 - 35.00) = +4.29 USDT
- Perp : -4.25 USDT (approximation)
- Funding reçu : +2.80 USDT
- PnL combiné : +4.29 - 4.25 + 2.80 - 3.00 = -0.16 USDT

Décision : MAINTENIR (funding insuffisant)
```

**T+16h (2e funding)** :
```
Prix AVAX : 34.80 (-0.57%)

PnL :
- Spot : 21.43 × (34.80 - 35.00) = -4.29 USDT
- Perp : +4.25 USDT
- Funding reçu : +2.80 + 2.75 = +5.55 USDT
- PnL combiné : -4.29 + 4.25 + 5.55 - 3.00 = +2.51 USDT

Décision : MAINTENIR (besoin ~10.80 pour 1.8x fees)
```

**T+24h à T+48h** :
```
Funding collecté continue...
T+24h : +8.20 USDT
T+32h : +10.80 USDT
T+40h : +13.20 USDT ✓
```

**T+40h (Fermeture)** :
```
Prix AVAX : 35.10 (+0.29%)

PnL Final :
- Spot : 21.43 × (35.10 - 35.00) = +2.14 USDT
- Perp : -2.10 USDT
- Funding total : +13.20 USDT
- Entry fees : -3.00 USDT
- Exit fees : -1.50 USDT
─────────────────────────
PnL combiné net : +8.74 USDT (+0.87%)

Décision : FERMER (funding 13.20 > 10.80 threshold)

Durée : 40 heures (1.67 jours)
ROI : 0.87% en 1.67 jours → ~190% APR 🎉
```

### Scénario 2 : Stop-Loss Déclenché

**Configuration** :
- Capital : 1,000 USDT
- Levier : 3x
- Paire : VOLATILUSDT
- Stop-loss : -24% (auto-calculé)

**Timeline** :

**T+0h (Ouverture)** :
```
Prix : 100.00 USDT
Position : 7.5 VOLATIL (spot) + short 7.5 (perp)
Perp margin : 250 USDT
```

**T+4h (Mouvement violent)** :
```
Prix : 92.00 USDT (-8%)

PnL :
- Spot : 7.5 × (92 - 100) = -60 USDT
- Perp : 7.5 × (100 - 92) × 3 = +180 USDT (avec levier)
  → Unrealized perp PnL via API : ~+58 USDT (net de fees/funding)

🤔 Perp PnL positif, mais...
```

**T+5h (Retournement violent)** :
```
Prix : 108.00 USDT (+8% depuis ouverture)

PnL :
- Spot : 7.5 × (108 - 100) = +60 USDT
- Perp : 7.5 × (100 - 108) × 3 = -180 USDT
  → Unrealized perp PnL : ~-62 USDT

Stop-loss threshold : 250 × (-0.24) = -60 USDT

Perp PnL : -62 USDT < -60 USDT ❌

⚠️ STOP-LOSS TRIGGERED!

Fermeture immédiate :
- Spot : +60 USDT
- Perp : -62 USDT
- Funding : +0.50 USDT (1 paiement)
- Fees : -4.50 USDT
─────────────────────────
PnL net : -6.00 USDT (-0.6%)

Protection : Évite perte plus importante si mouvement continue
```

### Scénario 3 : Rotation pour Meilleure Opportunité

**Position Actuelle** :
- OPUSDT @ 10% APR
- Ouverte depuis 2 jours
- Funding reçu : 5.50 USDT (pas encore seuil)

**Nouvelle Opportunité Détectée** :
- AVAXUSDT @ 18% APR
- 18% > 10% × 1.5 (15%) ✓

**Action** :
```
[2025-10-12 11:30:00] INFO - Better opportunity found: AVAXUSDT (18% vs 10%)

Fermeture OPUSDT :
- PnL combiné : +2.00 USDT (petit profit)

Ouverture AVAXUSDT :
- Capital : 1,000 USDT
- MA APR : 18%

Bénéfice : 80% plus de funding rate !
```

### Scénario 4 : Premier Lancement et Portfolio Tracking

**Baseline Initiale** :
```
[2025-10-08 12:00:00] Démarrage du bot

Balances :
- Spot USDT : 5,000
- Spot BTC : 0.05 @ 50,000 = 2,500
- Spot ETH : 1.0 @ 3,000 = 3,000
- Perp USDT : 2,000

Portfolio Total : 5,000 + 2,500 + 3,000 + 2,000 = 12,500 USDT

État sauvegardé :
{
  "initial_portfolio_value_usdt": 12500.0,
  "initial_portfolio_timestamp": "2025-10-08T12:00:00"
}
```

**Après 7 Jours de Trading** :
```
[2025-10-15 12:00:00] Cycle #150

Balances Actuelles :
- Spot USDT : 5,100
- Spot BTC : 0.048 @ 52,000 = 2,496
- Spot ETH : 1.05 @ 3,100 = 3,255
- Perp USDT : 2,150
- Perp Unrealized : +50

Portfolio Total : 5,100 + 2,496 + 3,255 + 2,150 + 50 = 13,051 USDT

PnL Calcul :
- Initial : 12,500 USDT
- Current : 13,051 USDT
- PnL : +551 USDT (+4.41%)

ROI Annualisé : 4.41% / 7 jours × 365 = ~229% APR 🚀

Affichage :
╔══════════════════════════════════════════════════════════════════╗
║  📊 Portfolio: $13,051.00 | PnL: +$551.00 (+4.41%)               ║
║      Since: 2025-10-08 12:00 UTC (7 days)                        ║
╚══════════════════════════════════════════════════════════════════╝
```

---

## Questions Fréquentes

### Q1 : Puis-je changer le levier pendant qu'une position est ouverte ?

**R** : Vous pouvez changer le config, mais cela n'affectera pas la position actuelle. Le nouveau levier s'appliquera uniquement à la prochaine position.

```
Config : leverage = 2 → Changer à 3
Position actuelle : Reste à 2x jusqu'à fermeture
Prochaine position : Ouvrira à 3x
```

---

### Q2 : Que se passe-t-il si je supprime le fichier d'état ?

**R** : Le bot :
1. Vérifiera l'exchange pour des positions existantes
2. Si position trouvée : la redécouvrira et reconstruira l'état
3. Si pas de position : démarrera frais
4. **Important** : Le PnL baseline sera réinitialisé

---

### Q3 : Le bot peut-il gérer plusieurs positions simultanément ?

**R** : Non, le bot maintient **une seule position delta-neutre à la fois**. C'est par design pour :
- Simplifier la gestion
- Réduire le risque
- Faciliter le monitoring

---

### Q4 : Comment le bot gère-t-il les coupures Internet/redémarrages ?

**R** : Grâce à la persistance d'état :
1. L'état est sauvegardé après chaque changement
2. Au redémarrage : le bot charge l'état
3. Réconcilie avec l'exchange
4. Continue normalement

**Aucune donnée perdue** ✓

---

### Q5 : Est-ce vraiment delta-neutre ? Aucun risque de prix ?

**R** : En théorie oui, en pratique il y a de légers risques :

**Sources de risque résiduel** :
1. **Déséquilibre temporel** : Ordres spot et perp ne s'exécutent pas exactement au même instant
2. **Slippage** : Prix d'exécution ≠ prix attendu
3. **Frais** : Coûts de transaction
4. **Funding rate négatif** : Si le taux devient négatif avant fermeture

**Mitigations** :
- Filtre de spread (≤ 0.15%)
- Health checks (déséquilibre ≤ 10%)
- Stop-loss automatique
- Filtre de taux négatif

---

### Q6 : Combien de capital minimum recommandé ?

**R** :
- **Minimum technique** : ~$100
- **Minimum pratique** : $1,000+
- **Optimal** : $5,000+

**Raison** : Les frais (0.3% par cycle) sont fixes. Sur petit capital, ils mangent une plus grande part du profit.

**Exemple** :
```
Capital $100 :
Frais par cycle : $0.30
Funding à 10% APR sur 3 jours : ~$0.08
Net : -$0.22 ❌ Perte

Capital $5,000 :
Frais par cycle : $15
Funding à 10% APR sur 3 jours : ~$41
Net : +$26 ✓ Profit
```

---

### Q7 : Quelle est la différence entre "cycle count" et "check iteration" ?

**R** :
- **Check Iteration** : Nombre de fois que le bot a exécuté sa boucle (toutes les 15 min)
- **Cycle Count** : Nombre de cycles de trading **complétés** (ouvert → maintenu → fermé)

```
Timeline :
T+0 : Check #1 → Ouvre position → cycle_count = 0
T+15min : Check #2 → Évalue position → cycle_count = 0
T+30min : Check #3 → Évalue position → cycle_count = 0
...
T+40h : Check #160 → Ferme position → cycle_count = 1 ✓
T+40h15min : Check #161 → Ouvre nouvelle position → cycle_count = 1
...
T+80h : Check #320 → Ferme position → cycle_count = 2 ✓
```

---

### Q8 : Comment puis-je augmenter la rentabilité ?

**Options** :

1. **Augmenter le levier** (2x → 3x)
   - +50% de funding rate collecté
   - Mais stop-loss plus proche

2. **Réduire fee_coverage_multiplier** (1.8 → 1.3)
   - Fermeture plus rapide
   - Plus de rotations
   - Risque : moins de profit par cycle

3. **Réduire min_funding_apr** (7% → 5%)
   - Plus d'opportunités
   - Risque : moins rentable

4. **Augmenter capital_fraction** (0.98 → 0.99)
   - Utilise plus de capital
   - Risque : moins de buffer

**⚠️ Attention** : Toute optimisation pour plus de profit augmente le risque !

---

### Q9 : Le bot supporte-t-il d'autres exchanges ?

**R** : Non, il est spécifiquement conçu pour ASTER DEX :
- Utilise l'authentification unique d'ASTER (v1 + v3)
- Adapté aux endpoints spécifiques
- Optimisé pour le schedule de funding d'ASTER (8h)

**Porter vers un autre exchange nécessiterait** :
- Réécrire `aster_api_manager.py`
- Adapter l'authentification
- Ajuster les endpoints
- Modifier les calculs de fees

---

### Q10 : Combien d'APR puis-je espérer en moyenne ?

**R** : Cela dépend énormément des conditions de marché :

**Marché haussier (bull market)** :
- Funding rates élevés : 10-30% APR
- Beaucoup d'opportunités
- APR effectif après fees : **15-25%**

**Marché neutre (sideways)** :
- Funding rates modérés : 5-15% APR
- Opportunités moyennes
- APR effectif après fees : **5-12%**

**Marché baissier (bear market)** :
- Funding rates souvent négatifs
- Peu d'opportunités
- APR effectif : **0-5%** (voire négatif)

**Moyenne réaliste sur long terme : 8-15% APR**

---

## Conclusion

Ce bot de trading delta-neutre sur ASTER DEX est un système sophistiqué qui combine :
- **Stratégie quantitative** : Capture des funding rates
- **Gestion des risques** : Stop-loss, health checks, filtrage multi-niveaux
- **Automation** : 24/7 sans intervention
- **Efficacité** : Levier configurable jusqu'à 3x
- **Monitoring** : Tracking PnL complet et coloré

**Points Clés à Retenir** :

1. ✅ **Delta-neutre** protège contre les mouvements de prix
2. ✅ **Funding rates** sont la source de profit
3. ✅ **4 filtres** garantissent la qualité (volume, taux, spread, APR)
4. ✅ **Levier** maximise l'efficacité (mais augmente le risque)
5. ✅ **Stop-loss automatique** protège contre la liquidation
6. ✅ **Architecture propre** facilite la maintenance et l'extension

**Recommandations** :

- Démarrer avec levier **2x** pour se familiariser
- Utiliser le mode **MA** (plus stable)
- Monitorer régulièrement les logs
- Tester avec **petit capital** d'abord
- Utiliser les scripts utilitaires pour comprendre le marché

**Ressources** :
- CLAUDE.md : Documentation technique détaillée
- README.md : Guide utilisateur
- Scripts utilitaires : Analyse et debugging

**Avertissement** : Le trading crypto comporte des risques. Ce bot ne garantit pas de profits. Utilisez uniquement du capital que vous pouvez vous permettre de perdre.

---

*Document créé le 2025-10-12 | Version 1.0 | Pour ASTER DEX Delta-Neutral Trading Bot*
