from typing import Any

from app.chatbot.knowledge_base import KnowledgeChunk
from app.chatbot.router import ChatIntent
from app.schemas.chat import ChatSource


PERIOD_LABELS = {
    "fr": {
        "today": "Aujourd'hui",
        "yesterday": "Hier",
        "7d": "7 jours",
        "30d": "30 jours",
        "quarter": "Trimestre",
        "year": "Annee",
    },
    "en": {
        "today": "Today",
        "yesterday": "Yesterday",
        "7d": "7 days",
        "30d": "30 days",
        "quarter": "Quarter",
        "year": "Year",
    },
}


def period_label(intent: ChatIntent) -> str:
    return PERIOD_LABELS[intent.language][intent.period]


class ResponseBuilder:
    def security_refusal(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "I cannot display sensitive information such as card, RIB, OTP, PIN, contact or technical key data. I can provide aggregated, non-sensitive operational analysis instead.",
                ["Show aggregated merchant risk?", "Explain the confidentiality rules?"],
            )
        return (
            "Je ne peux pas afficher ce type d'information sensible : carte, RIB, OTP, PIN, coordonnees client ou cles techniques. Je peux en revanche fournir une analyse agregee et non sensible.",
            ["Voir le risque agrege par commercant ?", "Expliquer les regles de confidentialite ?"],
        )

    def kpi_summary(self, intent: ChatIntent, data: dict[str, Any]) -> tuple[str, list[str]]:
        kpis = data.get("snapshot", {}).get("kpis", data)
        total = int(kpis.get("total_transactions") or 0)
        refusal = float(kpis.get("refusal_rate") or 0)
        success = float(kpis.get("success_rate") or 0)
        non_completed = int(kpis.get("non_completed_transactions") or 0)
        refused_count = round(total * refusal / 100)
        if intent.language == "en":
            answer = (
                f"For {period_label(intent)}, the replay projection has processed {total:,} transactions. "
                f"The success rate is {success:.1f}%, the refusal rate is {refusal:.1f}% ({refused_count:,} refused transactions), "
                f"and non-completed transactions total {non_completed:,}. These KPI come from the historical portfolio_demo replay and are served from the cached dashboard snapshot."
            )
            return answer, ["Show top merchants?", "Analyze incidents by hour?"]
        answer = (
            f"Sur la periode {period_label(intent)}, la projection de replay a traite {total:,} transactions. "
            f"Le taux de succes est de {success:.1f} %, le taux de refus de {refusal:.1f} % ({refused_count:,} transactions refusees), "
            f"et les transactions non abouties sont au nombre de {non_completed:,}. Ces KPI viennent du replay historique portfolio_demo et du snapshot cache du dashboard."
        )
        return answer, ["Voir les top commercants ?", "Analyser les incidents par heure ?"]

    def affiliation(self, intent: ChatIntent, data: dict[str, Any]) -> tuple[str, list[str]]:
        remaining = data.get("affiliations_remaining")
        total = data.get("affiliations_total", 0)
        reservees = data.get("affiliations_reservees", 0)
        affectees = data.get("affiliations_affectees", 0)
        if remaining is None:
            if intent.language == "en":
                return "The affiliation stock is unavailable for the current snapshot. The source table may be empty or unavailable.", ["Explain affiliation rules?"]
            return "Le stock d'affiliations est indisponible dans le snapshot courant. La table source peut etre vide ou indisponible.", ["Expliquer les regles d'affiliation ?"]
        if intent.language == "en":
            return (
                f"The projected remaining affiliation stock is {remaining:,} out of {total:,}. "
                f"{reservees:,} affiliations are reserved and {affectees:,} are affected. This is a business projection: the initial stock comes from SQL Server, then evolves during replay according to observed merchant behaviour.",
                ["Which merchants are most active?", "Explain the projection model?"],
            )
        return (
            f"Le stock projete d'affiliations restantes est de {remaining:,} sur {total:,}. "
            f"{reservees:,} affiliations sont reservees et {affectees:,} sont affectees. C'est une projection metier : le stock initial vient de SQL Server, puis evolue pendant le replay selon le comportement observe des commercants.",
            ["Quels commercants sont les plus actifs ?", "Expliquer le modele de projection ?"],
        )

    def rows(self, intent: ChatIntent, title: str, rows: list[dict[str, Any]], empty_label: str) -> tuple[str, list[str]]:
        if not rows:
            if intent.language == "en":
                return f"No data was found for {period_label(intent)}.", ["Try another period?", "Ask for KPI summary?"]
            return f"Aucune donnee trouvee sur la periode {period_label(intent)}.", ["Essayer une autre periode ?", "Voir le resume KPI ?"]
        lines = []
        for index, row in enumerate(rows[:5], start=1):
            name = row.get("terminal_id") if empty_label == "terminal" else None
            name = name or row.get("merchant_name") or row.get("name") or row.get("terminal_id") or empty_label
            transactions = row.get("transactions")
            refused = row.get("refused")
            risk = row.get("risk_score")
            parts = [f"{index}. {name}"]
            if transactions is not None:
                parts.append(f"{transactions} trx")
            if refused is not None:
                parts.append(f"{refused} refus")
            if risk is not None:
                parts.append(f"risk {risk}")
            lines.append(" - ".join(parts))
        prefix = f"{title} ({period_label(intent)})"
        return f"{prefix} :\n" + "\n".join(lines), ["Afficher le resume KPI ?", "Analyser les incidents par heure ?"]

    def incidents(self, intent: ChatIntent, rows: list[dict[str, Any]]) -> tuple[str, list[str]]:
        if not rows:
            return self.rows(intent, "Incidents", rows, "hour")
        peak = max(rows, key=lambda row: int(row.get("refused") or 0))
        hour = peak.get("hour")
        refused = int(peak.get("refused") or 0)
        transactions = int(peak.get("transactions") or 0)
        if intent.language == "en":
            return f"For {intent.period}, the highest incident hour is {hour}h with {refused} refusals over {transactions} transactions.", ["Show top TPE?"]
        return f"Sur {intent.period}, l'heure la plus incidente est {hour}h avec {refused} refus sur {transactions} transactions.", ["Voir les top TPE ?"]

    def docs(self, intent: ChatIntent, chunks: list[KnowledgeChunk]) -> tuple[str, list[str]]:
        explicit = self._explicit_doc_answer(intent)
        if explicit:
            return explicit
        if not chunks:
            if intent.language == "en":
                return "I do not have enough local documentation to answer this precisely.", ["Ask about Redis?", "Ask about Fast Forward?"]
            return "Je n'ai pas assez de documentation locale pour repondre precisement.", ["Demander pourquoi Redis ?", "Demander le Fast Forward ?"]
        main = chunks[0].text.strip()
        if len(main) > 620:
            main = main[:620].rsplit(" ", 1)[0] + "..."
        main = self._sanitize_user_text(main, intent)
        if intent.language == "en":
            return main, ["Ask for KPI summary?", "Ask about confidentiality?"]
        return main, ["Voir le resume KPI ?", "Demander les regles de confidentialite ?"]

    def greeting(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "Hello, I can help you analyze KPI, merchants, anomalies, affiliations and the historical replay architecture.",
                ["Show KPI summary?", "Explain Redis?"],
            )
        return (
            "Bonjour, je peux vous aider à analyser les KPI, les commerçants, les anomalies, les affiliations et l’architecture du replay historique.",
            ["Voir le résumé KPI ?", "Expliquer Redis ?"],
        )

    def small_talk(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "I am doing well, thanks. I am ready to help you analyze the TPE dashboard, KPI, anomalies, merchants, Redis, EventBus or the historical replay.",
                ["Show KPI summary?", "Explain EventBus?"],
            )
        return (
            "Je vais bien, merci. Je suis prêt à vous aider à analyser le dashboard TPE, les KPI, les anomalies, les commerçants, Redis, EventBus ou le replay historique.",
            ["Voir le résumé KPI ?", "Expliquer EventBus ?"],
        )

    def help_request(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "Yes, of course. You can ask me about transactions, KPI, anomalies, merchants, affiliations or the dashboard architecture.",
                ["How many transactions today?", "Explain Redis simply?"],
            )
        return (
            "Oui, bien sûr. Vous pouvez me poser des questions sur les transactions, les KPI, les anomalies, les commerçants, les affiliations ou l’architecture du dashboard.",
            ["Combien de transactions aujourd’hui ?", "Expliquer Redis simplement ?"],
        )

    def thanks(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return "You are welcome. I am here if you want to analyze another KPI, merchant or architectural point.", ["Show KPI summary?", "Explain replay?"]
        return "Avec plaisir. Je reste disponible si vous voulez analyser un autre KPI, un commerçant, une anomalie ou un point d’architecture.", ["Voir les KPI ?", "Expliquer le replay ?"]

    def capabilities(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "I can help with TPE dashboard KPI, transaction volumes, anomalies, merchants, affiliations, Redis, EventBus, historical replay, limits and defense-oriented explanations.",
                ["How many transactions today?", "What are the limits?"],
            )
        return (
            "Je peux vous aider sur les KPI du dashboard TPE, les volumes de transactions, les anomalies, les commerçants, les affiliations, Redis, EventBus, le replay historique, les limites et les explications pour une soutenance.",
            ["Combien de transactions aujourd’hui ?", "Quelles sont les limites ?"],
        )

    def clarification(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "I can answer, but I need one clarification: are you asking about transactions, anomalies or merchants?",
                ["Transactions", "Anomalies", "Merchants"],
            )
        return (
            "Je peux répondre, mais j’ai besoin de préciser : vous parlez des transactions, des anomalies ou des commerçants ?",
            ["Transactions", "Anomalies", "Commerçants"],
        )

    @staticmethod
    def _sanitize_user_text(text: str, intent: ChatIntent) -> str:
        if intent.intent in {"code_request", "code_question"}:
            return text
        code_markers = ("useState", "useEffect", "<div", "const ", "interface ", "export ", "import ", "def ", "class ", "from app.", "async def", "```", "flowchart", "-->")
        if not any(marker in text for marker in code_markers):
            return text
        if intent.language == "en":
            return (
                "This part belongs to the technical frontend implementation. For the dashboard user, the important point is that the assistant reads controlled KPI evidence, "
                "project documentation and architecture rules, then returns a business explanation instead of source code."
            )
        return (
            "Cette partie relève de l’implémentation technique frontend. Pour l’utilisateur du dashboard, l’idée importante est que l’assistant s’appuie sur les KPI contrôlés, "
            "la documentation projet et les règles d’architecture pour produire une explication métier, sans afficher de code source."
        )

    def _explicit_doc_answer(self, intent: ChatIntent) -> tuple[str, list[str]] | None:
        if intent.intent == "explain_redis":
            if intent.language == "en":
                return (
                    "Redis is used as the shared KPI snapshot cache. The backend computes the dashboard indicators during replay, stores the snapshot in Redis, then serves it through WebSocket/API. This avoids recalculating KPI from SQL Server at each screen refresh.",
                    ["Explain EventBus?", "Show current KPI summary?"],
                )
            return (
                "Redis sert de cache partage du snapshot KPI. Le backend calcule les indicateurs pendant le replay, stocke le snapshot dans Redis, puis le sert au dashboard via WebSocket/API. Cela evite de recalculer les KPI depuis SQL Server a chaque rafraichissement.",
                ["Expliquer EventBus ?", "Voir le resume KPI courant ?"],
            )
        if intent.intent == "explain_fast_forward":
            if intent.language == "en":
                return (
                    "Fast Forward accelerates the historical replay without jumping directly to the final state. Transactions are still applied in visible batches, so KPI, Redis and WebSocket updates keep showing intermediate operational states.",
                    ["What is LIVE REPLAY?", "Show incidents by hour?"],
                )
            return (
                "Fast Forward accelere le replay historique sans sauter directement a l'etat final. Les transactions restent appliquees par lots visibles, donc les KPI, Redis et WebSocket continuent de montrer des etats operationnels intermediaires.",
                ["C'est quoi LIVE REPLAY ?", "Voir les incidents par heure ?"],
            )
        if intent.intent == "explain_eventbus":
            if intent.language == "en":
                return (
                    "EventBus decouples SQL replay from KPI computation. ReplayProducer emits transaction events, while Aggregator and RiskAlertEngine consume them incrementally. The dashboard therefore observes a live projection instead of forcing SQL reads on every update.",
                    ["Why Redis?", "Explain Fast Forward?"],
                )
            return (
                "EventBus decouple la lecture SQL du calcul KPI. ReplayProducer publie des evenements transactionnels, puis Aggregator et RiskAlertEngine les consomment incrementiellement. Le dashboard observe donc une projection vivante sans relire SQL a chaque mise a jour.",
                ["Pourquoi Redis ?", "Expliquer Fast Forward ?"],
            )
        if intent.intent == "explain_replay":
            if intent.language == "en":
                return (
                    "LIVE REPLAY is not real production real time. It replays historical portfolio_demo transactions in order and publishes them as events, which gives the dashboard live behaviour while staying reproducible for demos and analysis.",
                    ["Explain EventBus?", "Show current KPI summary?"],
                )
            return (
                "LIVE REPLAY n'est pas un vrai temps reel de production. Il rejoue les transactions historiques portfolio_demo dans l'ordre et les publie comme evenements, ce qui donne un comportement vivant au dashboard tout en restant reproductible pour la demo et l'analyse.",
                ["Expliquer EventBus ?", "Voir le resume KPI courant ?"],
            )
        return None

    def unknown(self, intent: ChatIntent) -> tuple[str, list[str]]:
        if intent.language == "en":
            return (
                "I can answer questions about KPI, merchants, anomalies, affiliations, replay, Redis, EventBus and SQL Server portfolio_demo. I could not classify this question precisely.",
                ["What is today's refusal rate?", "Why is Redis used?"],
            )
        return (
            "Je peux repondre aux questions sur les KPI, commercants, anomalies, affiliations, replay, Redis, EventBus et SQL Server portfolio_demo. Je n'ai pas pu classer cette question precisement.",
            ["Quel est le taux de refus aujourd'hui ?", "Pourquoi Redis est utilise ?"],
        )


def source(source_type: str, label: str) -> ChatSource:
    return ChatSource(type=source_type, label=label)  # type: ignore[arg-type]
