# Replay historique portfolio_demo

Le terme officiel du projet est :

**Replay historique des transactions portfolio_demo**.

Ce n'est pas un flux bancaire temps réel de production. Le système rejoue des transactions historiques dans l'ordre, par lots, afin de produire une expérience live et reproductible pour la démonstration.

Le replay permet :

- de visualiser l'évolution des KPI ;
- de tester Redis, EventBus, Aggregator et WebSocket ;
- de faire varier les périodes sans exposer SQL au dashboard ;
- de conserver une démo déterministe.
