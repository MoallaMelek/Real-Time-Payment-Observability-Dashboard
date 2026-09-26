# Sécurité et confidentialité

Règles principales :

- Aucun secret ne doit être versionné.
- `.env` est exclu du livrable ; seuls `backend/.env.example` et `frontend/.env.example` doivent rester.
- Les clés OpenAI restent vides dans les exemples et OpenAI est désactivé tant que `CHATBOT_LLM_PROVIDER` n'est pas explicitement mis à `openai`.
- Le chatbot refuse les demandes de RIB, PAN, OTP, PIN, emails, téléphones, clés techniques et données sensibles.
- Le LLM ne reçoit pas d'accès SQL direct.
- Le LLM ne génère pas de SQL libre.
- Les outils SQL sélectionnent uniquement des agrégats ou champs non sensibles et utilisent l'interface publique du provider SQL Server.
- Les réponses non-code sont filtrées par `AnswerSanitizer`.
- Les dumps, logs, caches, environnements virtuels, `node_modules` et builds `dist` sont ignorés et exclus du livrable.

Vérifications recommandées avant livraison :

```powershell
rg -n --hidden --glob '!**/.git/**' "sk-|OPENAI_API_KEY\s*=\s*\S+|password\s*=\s*\S+|secret\s*=\s*\S+" .
Get-ChildItem -Force -Recurse -File -Include *.log,*.dump,*.dmp,*.sqlite,*.sqlite3
python -m pytest backend\tests
npm --prefix frontend audit
```
