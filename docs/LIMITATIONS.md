# Limites connues

Limites assumées :

- Le replay est historique, pas un flux temps réel bancaire.
- EventBus est in-memory.
- Redis est un cache de snapshot, pas une base source.
- SQL Server reste requis pour répondre aux dates explicites hors snapshot courant.
- Si SQL est indisponible pour une période explicite, le chatbot répond honnêtement au lieu d'utiliser le snapshot `today`.
- OpenAI/Gemini sont optionnels ; le mode local/fallback reste principal.
