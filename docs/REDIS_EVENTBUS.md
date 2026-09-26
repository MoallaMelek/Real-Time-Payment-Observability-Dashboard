# Redis et EventBus

Redis sert de cache partagé pour le snapshot KPI du dashboard.

Pour l'installation locale reproductible avec Docker Compose et les scripts
PowerShell, voir `docs/REDIS_LOCAL.md`.
Le snapshot contient une version applicative (`state_version`) et un horodatage
de génération (`snapshot_generated_at`) ajoutés avant écriture en cache.
Un JSON Redis invalide est traité comme un cache miss contrôlé afin de ne pas
faire tomber l'API ou le WebSocket.

Redis ne remplace pas :

- SQL Server portfolio_demo, qui reste la source historique.
- EventBus, qui reste la couche événementielle.
- Aggregator, qui reste responsable du calcul de projection.

EventBus est actuellement in-memory. C'est suffisant pour la démonstration, mais ses limites sont claires :

- pas de persistance durable des événements ;
- pas de rejeu d'événements depuis un log distribué ;
- pas de garantie multi-instance.

Le bus in-memory conserve l'ordre de publication par topic, isole les erreurs
par subscriber et expose des métriques simples (`published`, `delivered`,
`handler_errors`, `subscribers`) pour faciliter le diagnostic. Une erreur dans
un consommateur ne bloque pas les autres consommateurs du même événement.

Trajectoire future :

- garder l'interface `EventBus` ;
- remplacer l'implémentation in-memory par Kafka, Redis Streams ou RabbitMQ selon le contexte bancaire ;
- conserver le contrat côté `ReplayProducer` et `Aggregator`.
