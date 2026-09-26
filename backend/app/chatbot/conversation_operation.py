from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chatbot.business_understanding import BusinessUnderstanding
from app.chatbot.memory import ConversationState
from app.chatbot.message_normalizer import MessageNormalizer


@dataclass(frozen=True, slots=True)
class ConversationOperation:
    kind: str
    capability: str
    payload: dict[str, Any]
    purpose: str


class ConversationOperationEngine:
    """Recognizes operations on previous answers before selecting business tools."""

    COMBINE = {"combine les deux", "combine", "fusionne les deux", "fusionne"}
    COMPARE = {"compare", "compare les deux", "compare-les", "mets-les en comparaison"}
    SUMMARIZE = {"resume", "résume", "fais le resume", "fais le résumé"}
    CONTINUE = {"continue", "encore", "poursuis", "suite"}
    EXPAND = {"developpe", "développe", "explique davantage", "plus de detail", "plus de détail"}
    RESET_CONTEXT = {"repartons de zero", "repartons de zéro", "oublions cette journee", "oublions cette journée", "oublie cette journee", "oublie cette journée"}
    CHANGE_TOPIC = {"changeons de sujet", "on change de sujet", "autre sujet"}
    TODAY_CONTEXT = {"revenons a aujourd'hui", "revenons à aujourd'hui", "retour a aujourd'hui", "retour à aujourd'hui"}
    FRUSTRATION = {
        "tu m'emmerdes",
        "tu m emmerdes",
        "ca m'emmerde",
        "ça m'emmerde",
        "ca m emmerde",
        "ça m emmerde",
        "n'importe quoi",
        "n importe quoi",
        "arrete",
        "arrête",
    }
    COMPARISON_WINNER_CUES = (
        "lequel est le plus eleve",
        "lequel est le plus élevé",
        "laquelle est la plus elevee",
        "laquelle est la plus élevée",
        "qui est le plus eleve",
        "qui est le plus élevé",
        "quel est le plus eleve",
        "quel est le plus élevé",
    )
    WHY_FOLLOW_UPS = {"pourquoi", "pourquoi ca", "pourquoi ça", "why"}
    OPINION_CUES = (
        "qu'en pensez-vous",
        "qu en pensez vous",
        "qu'en penses-tu",
        "qu en penses tu",
        "est-ce eleve",
        "est ce eleve",
        "est-ce élevé",
        "est ce élevé",
        "est-ce bas",
        "est ce bas",
        "est-ce faible",
        "est ce faible",
        "est-ce inquietant",
        "est ce inquietant",
        "est-ce inquiétant",
        "est ce inquiétant",
        "est-ce normal",
        "est ce normal",
        "est-ce acceptable",
        "est ce acceptable",
        "que recommandez-vous",
        "que recommandez - vous",
        "que recommandez vous",
        "que recommanderiez-vous",
        "que recommanderiez vous",
        "que recommandes - tu",
        "que recommandes tu",
        "cela est-il considere eleve",
        "cela est il considere eleve",
        "cela est-il considéré élevé",
        "cela est il considéré élevé",
    )

    @classmethod
    def resolve(
        cls,
        message: str,
        understanding: BusinessUnderstanding | None,
        state: ConversationState | None,
    ) -> ConversationOperation | None:
        if understanding is None or state is None:
            return None
        normalized = MessageNormalizer.normalize_text(message).strip(" ?!.,;:")
        if not normalized:
            return None

        meta = cls._meta_operation(normalized)
        if meta:
            return meta
        outputs = list(state.recent_outputs or [])
        comparison = cls._comparison_follow_up(normalized, state)
        if comparison:
            return comparison
        opinion = cls._opinion_on_last_result(normalized, state, outputs)
        if opinion:
            return opinion
        if normalized in cls.COMBINE:
            return cls._combine(outputs)
        if normalized in cls.COMPARE:
            return cls._compare(outputs)
        if normalized in cls.SUMMARIZE or normalized.startswith("resume ") or normalized.startswith("résume "):
            return cls._summarize(outputs, one_sentence="phrase" in normalized)
        if normalized in cls.CONTINUE:
            return cls._continue(outputs)
        if normalized in cls.EXPAND:
            return cls._expand(outputs)
        if cls._should_stay_business_question(normalized, understanding):
            return None
        return None

    @classmethod
    def _opinion_on_last_result(
        cls,
        normalized: str,
        state: ConversationState,
        outputs: list[dict[str, Any]],
    ) -> ConversationOperation | None:
        if not any(cue in normalized for cue in cls.OPINION_CUES) and normalized not in cls.WHY_FOLLOW_UPS:
            return None
        analytical_context = state.current_analysis_context
        summary = analytical_context.analysis_result if analytical_context and analytical_context.analysis_result else {}
        if not summary and outputs:
            maybe_summary = outputs[-1].get("result_summary")
            summary = maybe_summary if isinstance(maybe_summary, dict) else {}
        if not summary:
            return None
        if not any(isinstance(summary.get(key), (int, float)) for key in ("rate", "refusal_rate", "count", "secondary_value", "primary_value")):
            summary = {**summary, **dict(state.last_numeric_values or {})}
        rate = cls._number((analytical_context.rate if analytical_context else None) or summary.get("rate") or summary.get("refusal_rate"))
        count = cls._number((analytical_context.count if analytical_context else None) or summary.get("count") or summary.get("secondary_value"))
        primary = cls._number((analytical_context.primary_value if analytical_context else None) or summary.get("primary_value"))
        period = str((analytical_context.period if analytical_context else None) or summary.get("period") or state.last_result_period or state.last_period or "la période").strip()
        metric = str((analytical_context.metric if analytical_context else None) or summary.get("metric") or state.last_result_metric or state.last_metric or "").strip()
        label = str(summary.get("label") or summary.get("value_label") or "valeur").strip()
        if rate is not None:
            level = cls._rate_level(rate)
            count_part = f" ({int(count)} refus)" if count is not None else ""
            if "bas" in normalized or "faible" in normalized:
                if rate < 3:
                    answer = (
                        f"Oui, {rate:.2f} % de refus{count_part} sur {period} reste bas. "
                        "Je garderais quand même un œil sur la répartition, surtout si les refus se concentrent sur quelques commerçants, TPE ou horaires."
                    )
                else:
                    answer = (
                        f"Non, je ne le qualifierais pas de bas : {rate:.2f} % de refus{count_part} sur {period}, c'est {level}. "
                        "La bonne lecture est de vérifier si ces refus sont dispersés ou concentrés sur quelques commerçants, TPE ou horaires."
                    )
            elif "eleve" in normalized or "élevé" in normalized:
                if rate >= 8:
                    answer = (
                        f"Oui, {rate:.2f} % de refus{count_part} sur {period} est {level} pour une journée. "
                        "Ce n'est pas forcément un incident à lui seul, mais c'est suffisamment significatif pour vérifier la concentration par commerçant, TPE ou horaire."
                    )
                else:
                    answer = (
                        f"Non, {rate:.2f} % de refus{count_part} sur {period} n'est pas élevé; je le lirais comme {level}. "
                        "La suite utile reste de regarder si les refus sont concentrés quelque part."
                    )
            elif "normal" in normalized or "acceptable" in normalized:
                if rate >= 8:
                    answer = (
                        f"Je ne dirais pas que c'est pleinement normal ou acceptable sans vérification : {rate:.2f} % de refus{count_part} sur {period} est {level}. "
                        "Il faut regarder la concentration par commerçant, TPE ou horaire avant de conclure."
                    )
                elif rate >= 3:
                    answer = (
                        f"{rate:.2f} % de refus{count_part} sur {period} semble modéré. "
                        "C'est acceptable si les refus sont dispersés; ça devient à surveiller s'ils se concentrent sur les mêmes acteurs."
                    )
                else:
                    answer = (
                        f"Oui, {rate:.2f} % de refus{count_part} sur {period} paraît normal à ce niveau. "
                        "Je vérifierais simplement qu'il n'y a pas de concentration locale."
                    )
            elif normalized in cls.WHY_FOLLOW_UPS:
                answer = (
                    f"Parce que {rate:.2f} % de refus{count_part} sur {period} donne un signal {level}. "
                    "Le taux seul ne prouve pas la cause, mais il justifie de regarder la concentration par commerçant, TPE ou horaire avant de conclure."
                )
            elif "recommand" in normalized:
                answer = (
                    f"Avec {rate:.2f} % de refus{count_part} sur {period}, je recommanderais de prioriser la concentration : "
                    "d'abord les commerçants les plus touchés, puis les TPE et les heures où les refus montent."
                )
            elif "pense" in normalized or "pensez" in normalized:
                answer = (
                    f"Mon avis : {rate:.2f} % de refus{count_part} sur {period}, c'est {level}. "
                    "Je ne conclurais pas tout de suite à une panne globale; je regarderais d'abord si les refus sont regroupés sur quelques commerçants, TPE ou horaires."
                )
            else:
                answer = (
                    f"{rate:.2f} % de refus{count_part} sur {period}, c'est {level}. "
                    "La bonne suite est de vérifier si les refus sont dispersés ou concentrés sur certains commerçants, TPE ou horaires."
                )
        elif primary is not None:
            value = int(primary) if float(primary).is_integer() else round(primary, 2)
            answer = (
                f"Pour {period}, {value} {label} donne un signal à interpréter avec son contexte. "
                "Je regarderais surtout la comparaison avec une période voisine et la concentration par commerçant, TPE ou horaire avant de conclure."
            )
        else:
            return None
        return ConversationOperation(
            kind="opinion_on_previous_result",
            capability="analytical_context_opinion",
            payload={
                "operation": "opinion_on_previous_result",
                "answer": answer,
                "follow_up": ["Voir les commerçants concernés", "Analyser les incidents par heure"],
                "summary": f"Opinion derived from memorized result metric={metric or label}, period={period}.",
                "used_memory_only": True,
                "referenced_outputs": 1,
            },
            purpose="Analyser le dernier résultat numérique mémorisé sans relancer SQL ni KPI",
        )

    @classmethod
    def _meta_operation(cls, normalized: str) -> ConversationOperation | None:
        if normalized in cls.FRUSTRATION:
            return ConversationOperation(
                kind="frustration_recovery",
                capability="conversation_recovery",
                payload={
                    "operation": "frustration_recovery",
                    "answer": (
                        "Je reprends proprement. Je garde le dernier contexte métier au lieu de repartir sur une question vague. "
                        "La suite utile est de relancer l'action demandée sur la même période : afficher les commerçants concernés, comparer avec la période précédente, ou reformuler le comptage."
                    ),
                    "follow_up": ["Afficher les commerçants concernés", "Comparer avec la période précédente"],
                    "summary": "Recovered from user frustration while preserving analytical context.",
                    "used_memory_only": True,
                    "referenced_outputs": 1,
                },
                purpose="Répondre à la frustration sans perdre le contexte analytique",
            )
        if normalized in cls.RESET_CONTEXT:
            return ConversationOperation(
                kind="reset_context",
                capability="conversation_memory_update",
                payload={
                    "operation": "reset_context",
                    "answer": "D'accord, j'oublie le contexte analytique précédent. On repart proprement.",
                    "follow_up": ["Poser une nouvelle question métier"],
                    "summary": "Cleared analytical and comparison context from memory only.",
                    "used_memory_only": True,
                    "referenced_outputs": 0,
                },
                purpose="Réinitialiser la mémoire analytique sans appel SQL ni snapshot",
            )
        if normalized in cls.CHANGE_TOPIC:
            return ConversationOperation(
                kind="change_topic",
                capability="conversation_memory_update",
                payload={
                    "operation": "change_topic",
                    "answer": "D'accord, on change de sujet. Le contexte analytique précédent ne sera plus utilisé pour la prochaine question.",
                    "follow_up": ["Quel indicateur voulez-vous regarder ?"],
                    "summary": "Changed topic by clearing analytical and comparison context from memory only.",
                    "used_memory_only": True,
                    "referenced_outputs": 0,
                },
                purpose="Changer de sujet en mémoire sans lancer d'outil métier",
            )
        if normalized in cls.TODAY_CONTEXT:
            return ConversationOperation(
                kind="set_topic",
                capability="conversation_memory_update",
                payload={
                    "operation": "set_topic",
                    "answer": "D'accord, je reprends aujourd'hui comme période de référence pour la suite.",
                    "follow_up": ["Quel indicateur d'aujourd'hui voulez-vous analyser ?"],
                    "summary": "Set today as conversational reference period from memory only.",
                    "used_memory_only": True,
                    "referenced_outputs": 0,
                    "period": "today",
                },
                purpose="Modifier la période conversationnelle sans appel SQL ni snapshot",
            )
        if normalized == "parlons des anomalies maintenant":
            return ConversationOperation(
                kind="set_topic",
                capability="conversation_memory_update",
                payload={
                    "operation": "set_topic",
                    "answer": "D'accord, on passe aux anomalies. Je n'utilise plus l'ancien contexte pour interpréter la suite.",
                    "follow_up": ["Voir les anomalies les plus visibles ?", "Analyser les incidents par heure ?"],
                    "summary": "Set anomaly topic from memory only.",
                    "used_memory_only": True,
                    "referenced_outputs": 0,
                    "object": "anomaly",
                    "metric_subject": "anomalies",
                },
                purpose="Changer le sujet métier mémorisé sans lancer d'outil",
            )
        return None

    @classmethod
    def _comparison_follow_up(cls, normalized: str, state: ConversationState) -> ConversationOperation | None:
        if not any(cue in normalized for cue in cls.COMPARISON_WINNER_CUES):
            return None
        context = state.comparison_context
        if context is None:
            return None
        left = context.left_period or "la première période"
        right = context.right_period or "la seconde période"
        if context.winner == "left":
            answer = f"{left} est le plus élevé : {cls._format_number(context.left_value)} contre {cls._format_number(context.right_value)} pour {right}."
        elif context.winner == "right":
            answer = f"{right} est le plus élevé : {cls._format_number(context.right_value)} contre {cls._format_number(context.left_value)} pour {left}."
        else:
            answer = f"Les deux périodes sont au même niveau : {cls._format_number(context.left_value)} contre {cls._format_number(context.right_value)}."
        if context.difference is not None:
            answer += f" L'écart est de {cls._format_number(abs(context.difference))} transactions."
        return ConversationOperation(
            kind="comparison_follow_up",
            capability="comparison_context_answer",
            payload={
                "operation": "comparison_follow_up",
                "answer": answer,
                "follow_up": ["Résumer la comparaison ?", "Voir les causes possibles ?"],
                "summary": context.summary or "Answered from memorized comparison context.",
                "used_memory_only": True,
                "referenced_outputs": 1,
            },
            purpose="Répondre depuis le résultat de comparaison mémorisé sans relancer d'outil",
        )

    @staticmethod
    def _number(value: Any) -> float | None:
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _should_stay_business_question(normalized: str, understanding: BusinessUnderstanding) -> bool:
        if understanding.context.get("topic_changed"):
            return True
        if understanding.period or understanding.object or understanding.metric or understanding.dimension:
            return normalized not in {
                "compare",
                "compare les deux",
                "combine",
                "combine les deux",
                "resume",
                "résume",
                "continue",
                "developpe",
                "développe",
                "explique davantage",
            }
        return False

    @classmethod
    def _combine(cls, outputs: list[dict[str, Any]]) -> ConversationOperation:
        if len(outputs) < 2:
            return cls._not_enough_context("combine", 2, "combiner")
        left, right = outputs[-2], outputs[-1]
        left_desc = cls._describe_output(left)
        right_desc = cls._describe_output(right)
        answer = (
            f"Je combine les deux résultats précédents : {left_desc} {right_desc} "
            "Ensemble, ils donnent une vue plus complète du contexte sans relancer une nouvelle requête."
        )
        return ConversationOperation(
            kind="combine",
            capability="combine_previous_results",
            payload={
                "operation": "combine",
                "answer": answer,
                "follow_up": ["Comparer ces deux résultats ?", "Développer un des deux points ?"],
                "summary": "Combined the two latest conversation results from memory only.",
                "used_memory_only": True,
                "referenced_outputs": 2,
            },
            purpose="Combiner les deux derniers résultats mémorisés sans nouvel appel aux outils métier",
        )

    @classmethod
    def _compare(cls, outputs: list[dict[str, Any]]) -> ConversationOperation:
        if len(outputs) < 2:
            return cls._not_enough_context("compare", 2, "comparer")
        left, right = outputs[-2], outputs[-1]
        left_summary = left.get("result_summary") if isinstance(left.get("result_summary"), dict) else {}
        right_summary = right.get("result_summary") if isinstance(right.get("result_summary"), dict) else {}
        numeric_left = cls._numeric_primary_value(left_summary)
        numeric_right = cls._numeric_primary_value(right_summary)
        if numeric_left is not None and numeric_right is not None and left_summary.get("value_label") == right_summary.get("value_label"):
            delta = numeric_right - numeric_left
            direction = "au-dessus" if delta > 0 else "en dessous" if delta < 0 else "au même niveau"
            label = str(right_summary.get("value_label") or "unité(s)")
            left_period = str(left_summary.get("period") or left.get("period") or "premier résultat")
            right_period = str(right_summary.get("period") or right.get("period") or "second résultat")
            winner = "right" if delta > 0 else "left" if delta < 0 else "tie"
            percentage_difference = cls._percentage_difference(numeric_right, numeric_left)
            comparison_summary = (
                f"{right_period} est plus élevé que {left_period}."
                if winner == "right"
                else f"{left_period} est plus élevé que {right_period}."
                if winner == "left"
                else f"{left_period} et {right_period} sont au même niveau."
            )
            answer = (
                f"Je compare les deux résultats précédents : {cls._describe_output(left)} {cls._describe_output(right)} "
                f"L'écart observé est de {abs(delta)} {label}, "
                f"et le second résultat est {direction} du premier."
            )
            comparison_context = {
                "left_period": left_period,
                "right_period": right_period,
                "left_value": numeric_left,
                "right_value": numeric_right,
                "difference": delta,
                "percentage_difference": percentage_difference,
                "winner": winner,
                "summary": comparison_summary,
            }
        else:
            answer = (
                f"Je compare les deux résultats précédents : {cls._describe_output(left)} {cls._describe_output(right)} "
                "Ils portent sur deux angles différents du dashboard, donc la comparaison reste surtout qualitative."
            )
            comparison_context = None
        return ConversationOperation(
            kind="compare",
            capability="compare_previous_results",
            payload={
                "operation": "compare",
                "answer": answer,
                "follow_up": ["Résumer cette comparaison ?", "Afficher un autre détail métier ?"],
                "summary": "Compared the two latest conversation results from memory only.",
                "used_memory_only": True,
                "referenced_outputs": 2,
                **({"comparison_context": comparison_context} if comparison_context else {}),
            },
            purpose="Comparer les deux derniers résultats mémorisés sans relancer SQL",
        )

    @classmethod
    def _summarize(cls, outputs: list[dict[str, Any]], one_sentence: bool = False) -> ConversationOperation:
        if not outputs:
            return cls._not_enough_context("summarize", 1, "résumer")
        target = outputs[-2:] if len(outputs) >= 2 else outputs[-1:]
        snippets = " ".join(cls._describe_output(item) for item in target)
        answer = snippets if one_sentence else f"Voici le résumé utile du contexte récent : {snippets}"
        return ConversationOperation(
            kind="summarize",
            capability="summarize_previous_results",
            payload={
                "operation": "summarize",
                "answer": answer,
                "follow_up": ["Comparer les deux derniers résultats ?", "Développer un point précis ?"],
                "summary": "Summarized the latest conversation results from memory only.",
                "used_memory_only": True,
                "referenced_outputs": len(target),
            },
            purpose="Résumer les derniers résultats mémorisés sans nouvel appel outil",
        )

    @classmethod
    def _continue(cls, outputs: list[dict[str, Any]]) -> ConversationOperation:
        if not outputs:
            return cls._not_enough_context("continue", 1, "continuer")
        answer = (
            f"Je poursuis à partir du dernier résultat : {cls._describe_output(outputs[-1])} "
            "Sans nouvelle donnée, la suite logique est d'explorer la même piste sous un autre angle."
        )
        return ConversationOperation(
            kind="continue",
            capability="continue_previous_result",
            payload={
                "operation": "continue",
                "answer": answer,
                "follow_up": ["Développer davantage ?", "Comparer avec le résultat précédent ?"],
                "summary": "Continued from the latest conversation result using memory only.",
                "used_memory_only": True,
                "referenced_outputs": 1,
            },
            purpose="Continuer le dernier résultat en s'appuyant sur la mémoire conversationnelle",
        )

    @classmethod
    def _expand(cls, outputs: list[dict[str, Any]]) -> ConversationOperation:
        if not outputs:
            return cls._not_enough_context("expand", 1, "développer")
        answer = (
            f"Je développe le dernier point sans relancer les outils : {cls._describe_output(outputs[-1])} "
            "Autrement dit, ce résultat apporte déjà un angle exploitable et peut être approfondi avant toute nouvelle requête."
        )
        return ConversationOperation(
            kind="expand",
            capability="expand_previous_result",
            payload={
                "operation": "expand",
                "answer": answer,
                "follow_up": ["Résumer ce point ?", "Comparer avec le résultat précédent ?"],
                "summary": "Expanded the latest conversation result using memory only.",
                "used_memory_only": True,
                "referenced_outputs": 1,
            },
            purpose="Développer le dernier résultat à partir de la mémoire conversationnelle",
        )

    @classmethod
    def _not_enough_context(cls, operation: str, required: int, verb: str) -> ConversationOperation:
        plural = "résultats" if required > 1 else "résultat"
        return ConversationOperation(
            kind=operation,
            capability=f"{operation}_previous_results",
            payload={
                "operation": operation,
                "answer": f"Je peux {verb} le contexte précédent, mais il me faut au moins {required} {plural} mémorisés.",
                "follow_up": ["Poser une nouvelle question métier", "Afficher un résultat d'abord"],
                "summary": "Conversation operation recognized but there was not enough memory context.",
                "used_memory_only": True,
                "referenced_outputs": 0,
            },
            purpose=f"Traiter l'opération conversationnelle '{operation}' à partir de la mémoire",
        )

    @staticmethod
    def _numeric_primary_value(summary: dict[str, Any]) -> float | None:
        value = summary.get("primary_value")
        if isinstance(value, (int, float)):
            return float(value)
        return None

    @staticmethod
    def _format_number(value: Any) -> str:
        if not isinstance(value, (int, float)):
            return "N/A"
        if float(value).is_integer():
            return str(int(value))
        return f"{value:.2f}"

    @classmethod
    def _describe_output(cls, output: dict[str, Any]) -> str:
        summary = output.get("result_summary") if isinstance(output.get("result_summary"), dict) else {}
        comparison = summary.get("comparison_context") if isinstance(summary.get("comparison_context"), dict) else None
        if comparison:
            text = str(comparison.get("summary") or "").strip()
            difference = comparison.get("difference")
            if isinstance(difference, (int, float)):
                value = int(abs(difference)) if float(difference).is_integer() else round(abs(float(difference)), 2)
                text = f"{text} Écart : {value} transactions d'écart.".strip()
            if text:
                return text
        label = str(summary.get("value_label") or "").strip()
        primary = summary.get("primary_value")
        detail = str(summary.get("detail") or "").strip()
        if label and isinstance(primary, (int, float)):
            value = int(primary) if float(primary).is_integer() else round(float(primary), 2)
            period = str(summary.get("period") or output.get("period") or "").strip()
            if detail:
                return f"{value} {label} {detail}."
            if period:
                return f"{value} {label} sur {period}."
            return f"{value} {label}."
        excerpt = str(output.get("answer_excerpt") or "").strip()
        return excerpt if excerpt else "Un résultat précédent est disponible."

    @staticmethod
    def _rate_level(rate: float) -> str:
        if rate >= 8:
            return "plutôt élevé"
        if rate >= 3:
            return "modéré"
        return "faible"

    @staticmethod
    def _percentage_difference(right: float | None, left: float | None) -> float | None:
        if right is None or left in {None, 0}:
            return None
        return round((right - left) / left * 100, 2)
