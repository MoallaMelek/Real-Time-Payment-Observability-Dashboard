# Chatbot Data Analyst TPE

Le chatbot est spécialisé dashboard TPE.

Composants :

- `message_normalizer.py` : normalise fautes, accents, abréviations.
- `intent_analyzer.py` : détecte les intentions avec fuzzy matching.
- `business_understanding.py` : produit la compréhension métier canonique (`CognitiveEngine`) et résout les suivis conversationnels.
- `memory.py` : conserve l'historique texte et `ConversationState` structuré.
- `temporal_resolver.py` : transforme les expressions temporelles en plages normalisées.
- `followup_resolver.py` : définit le contrat `FollowUpResolution` partagé avec le sélecteur d'outils.
- `tool_selector.py` : choisit les outils déterministes à partir des structures métier.
- `reasoning.py` : prépare `EvidenceBundle` et qualifie les limites des preuves.
- `business_analysis.py` : transforme les preuves en analyse métier bornée.
- `answer_composer.py` : rédige la réponse naturelle depuis `BusinessAnalysis`.
- `project_code_search.py` : répond aux demandes explicites de code.
- `answer_sanitizer.py` : bloque le code brut dans les réponses non-code.
- `gemini_provider.py` / `gemini_orchestrator.py` : Gemini est le cerveau principal et le seul rédacteur final quand il est activé ; les garde-fous backend collectent et valident les preuves.

Pipeline :

1. API `/api/chat`
2. `ChatbotService`
3. `GeminiPrincipalOrchestrator` si Gemini est disponible
4. `BusinessUnderstandingEngine` / `CognitiveEngine`
5. `ConversationState`
6. `ToolSelector`
7. SQL Tools / Business RAG / Code Search
8. `EvidenceBundle`
9. `ReasoningEngine`
10. `BusinessAnalysisEngine`
11. Composition finale Gemini
12. `AnswerSanitizer`
13. Réponse utilisateur

Règles :

- Salutations, small talk, aide, merci et capacités ne déclenchent ni RAG ni SQL.
- Les questions data passent par des outils backend contrôlés.
- Les questions architecture utilisent l'index documentaire métier.
- Les questions code utilisent l'outil dédié de recherche code.
- Le LLM ne génère jamais de SQL libre.
- Gemini, lorsqu'il est activé, décide des preuves à collecter puis rédige la réponse finale à partir des résultats validés.
- Si `GEMINI_FALLBACK_ENABLED=false`, aucune réponse analytique locale n'est produite quand Gemini est indisponible.
- Les fallbacks historiques restent disponibles uniquement en mode compatibilité.
- `AnswerComposer` ne calcule pas de conclusion : l'analyse vient de `BusinessAnalysisEngine`.

Voir aussi : `docs/GEMINI_CHATBOT.md`.
