from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.chatbot.business_understanding import BusinessUnderstanding
from app.chatbot.memory import ConversationState
from app.chatbot.reasoning import EvidenceBundle, ReasoningResult


@dataclass(frozen=True, slots=True)
class BusinessAnalysis:
    facts: list[str]
    insights: list[str]
    interpretation: list[str]
    possible_causes: list[str]
    anomalies: list[str]
    recommendations: list[str]
    confidence: float
    confidence_label: str
    limitations: list[str]
    evidence_basis: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "facts": self.facts,
            "insights": self.insights,
            "interpretation": self.interpretation,
            "possible_causes": self.possible_causes,
            "anomalies": self.anomalies,
            "recommendations": self.recommendations,
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "limitations": self.limitations,
            "evidence_basis": self.evidence_basis,
        }


class BusinessAnalysisEngine:
    """Turns collected evidence into bounded business analysis."""

    @classmethod
    def analyze(
        cls,
        *,
        understanding: BusinessUnderstanding | None,
        state: ConversationState | None,
        bundle: EvidenceBundle,
        tool_results: dict[str, Any],
        knowledge: list[dict[str, Any]] | None = None,
        reasoning: ReasoningResult | None = None,
    ) -> BusinessAnalysis:
        facts: list[str] = []
        insights: list[str] = []
        interpretation: list[str] = []
        possible_causes: list[str] = []
        anomalies: list[str] = []
        recommendations: list[str] = []
        limitations = list(reasoning.limitations if reasoning else bundle.limitations)
        evidence_basis = list(bundle.tools)

        handled = False
        if "get_transactions_between_dates" in tool_results:
            handled = True
            cls._analyze_transactions(tool_results["get_transactions_between_dates"], facts, insights, interpretation, recommendations, limitations)
        if "multi_period_metric" in tool_results:
            handled = True
            cls._analyze_multi_period_metric(tool_results["multi_period_metric"], facts, limitations)
        if "get_tpe_count_by_period" in tool_results:
            handled = True
            cls._analyze_tpe_count(tool_results["get_tpe_count_by_period"], facts, insights, interpretation, recommendations, limitations)
        if "compare_date_ranges" in tool_results:
            handled = True
            cls._analyze_date_range_comparison(tool_results["compare_date_ranges"], facts, insights, interpretation, anomalies, recommendations, limitations)
        if "compare_periods" in tool_results:
            handled = True
            cls._analyze_period_comparison(tool_results["compare_periods"], facts, insights, interpretation, anomalies, recommendations)
        if "get_top_merchants" in tool_results:
            handled = True
            cls._analyze_ranking(tool_results["get_top_merchants"], "commerçants", facts, insights, interpretation, recommendations, limitations)
        if "get_top_tpe" in tool_results:
            handled = True
            cls._analyze_ranking(tool_results["get_top_tpe"], "TPE", facts, insights, interpretation, recommendations, limitations)
        if "generate_dashboard_opinion" in tool_results:
            handled = True
            cls._analyze_opinion(tool_results["generate_dashboard_opinion"], facts, insights, interpretation, recommendations)
        if "get_kpi_summary" in tool_results or "get_kpi_by_period" in tool_results:
            handled = True
            data = tool_results.get("get_kpi_summary") or tool_results.get("get_kpi_by_period")
            cls._analyze_kpi(data, facts, insights, interpretation, recommendations)

        search_key = next((key for key in tool_results if key.startswith("search_") or key.startswith("explain_")), None)
        if search_key and not handled:
            cls._analyze_knowledge(tool_results.get(search_key) or knowledge or [], facts, insights, interpretation, recommendations, limitations)

        if state and state.last_business_query:
            insights.append("Cette réponse tient compte du contexte conversationnel précédent.")

        if not facts:
            limitations.append("Les preuves disponibles ne contiennent pas assez de données métier pour produire une analyse chiffrée.")
            recommendations.append("Reformuler la question ou demander une période plus précise.")
        if facts and not limitations:
            limitations.append("L'analyse reste limitée aux preuves collectées et ne doit pas être extrapolée au-delà de cette période.")

        confidence = cls._confidence(bundle, facts, limitations)
        return BusinessAnalysis(
            facts=cls._dedupe(facts),
            insights=cls._dedupe(insights),
            interpretation=cls._dedupe(interpretation),
            possible_causes=cls._dedupe(possible_causes),
            anomalies=cls._dedupe(anomalies),
            recommendations=cls._dedupe(recommendations),
            confidence=confidence,
            confidence_label=cls._confidence_label(confidence),
            limitations=cls._dedupe(limitations),
            evidence_basis=evidence_basis,
        )

    @staticmethod
    def _analyze_transactions(data: dict[str, Any], facts: list[str], insights: list[str], interpretation: list[str], recommendations: list[str], limitations: list[str]) -> None:
        source_name = data.get("source")
        start = data.get("start_date", "?")
        end = data.get("end_date", "?")
        if source_name == "sql_unavailable":
            facts.append("Connexion SQL Server indisponible pour la période demandée.")
            limitations.append("Le comptage SQL contrôlé n'a pas pu être exécuté.")
            recommendations.append("Réessayer lorsque SQL Server est disponible ou consulter les KPI courants séparément.")
            return
        if source_name == "sql_error":
            facts.append("L'outil SQL contrôlé a rencontré une erreur interne.")
            limitations.append("Le détail technique reste dans les logs backend.")
            recommendations.append("Vérifier la configuration SQL avant de conclure sur cette période.")
            return
        total = int(data.get("transactions") or 0)
        refused = int(data.get("refused") or 0)
        non_completed = int(data.get("non_completed") or 0)
        refusal_rate = float(data.get("refusal_rate") or 0)
        if total == 0:
            facts.append(f"Pas de transaction trouvée pour la période {start} à {end}.")
            limitations.append("Aucune conclusion métier robuste n'est possible sans volume observé.")
            recommendations.append("Tester une période plus large ou comparer avec les KPI courants.")
            return
        facts.append(f"{total} transactions observées sur {start} à {end}.")
        facts.append(f"{refused} refus, soit {refusal_rate:.2f} % du volume.")
        if non_completed:
            facts.append(f"{non_completed} transactions non abouties.")
            if data.get("metric_subject") == "non_completed":
                facts.append(f"Taux de transactions non abouties : {non_completed / total * 100:.1f} %.")
        authorized = int(data.get("authorized_count") or max(0, total - refused))
        completed_success = int(data.get("completed_success_count") or max(0, total - refused - non_completed))
        success_rate = float(data.get("success_rate") or (completed_success / total * 100 if total else 0))
        insights.append(f"{authorized} transactions sont non refusées sur {total}.")
        insights.append(f"{completed_success} transactions sont abouties avec succès, soit {success_rate:.2f} % du volume.")
        interpretation.append("Le taux de refus mesure la part des transactions rejetées dans le volume observé.")
        interpretation.append("Ce KPI indique la qualité d'acceptation des paiements, mais ne suffit pas à identifier une cause sans détail commerçant, TPE ou horaire.")
        recommendations.append("Comparer cette période avec une période voisine.")
        recommendations.append("Observer les commerçants et les créneaux horaires associés.")

    @staticmethod
    def _analyze_multi_period_metric(data: dict[str, Any], facts: list[str], limitations: list[str]) -> None:
        values = list(data.get("values") or []) if isinstance(data, dict) else []
        if not values:
            limitations.append("Aucune valeur multi-période exploitable n'a été collectée.")
            return
        metric = str(data.get("metric") or "total")
        metric_subject = str(data.get("metric_subject") or "")
        for item in values:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or item.get("period") or "période")
            if metric_subject == "tpe":
                facts.append(f"{label} : {int(item.get('active_tpe') or 0)} TPE distincts.")
            elif metric in {"refusal_rate", "refused_count"}:
                facts.append(f"{label} : {item.get('refused', 0)} refus, soit {float(item.get('refusal_rate') or 0):.2f} %.")
            else:
                facts.append(f"{label} : {int(item.get('transactions') or 0)} transactions.")

    @staticmethod
    def _analyze_kpi(data: dict[str, Any], facts: list[str], insights: list[str], interpretation: list[str], recommendations: list[str]) -> None:
        kpis = data.get("snapshot", {}).get("kpis", data) if isinstance(data, dict) else {}
        metric_subject = str(data.get("metric_subject") or "")
        business_query = data.get("business_query") if isinstance(data.get("business_query"), dict) else {}
        requested_metric = str(business_query.get("metric") or "")
        if metric_subject == "transactions_lentes" or requested_metric == "slow_count":
            if kpis.get("slow_transactions_available"):
                facts.append(f"Transactions lentes : {int(kpis.get('slow_transactions') or 0)}.")
                avg = kpis.get("average_processing_time_ms")
                if kpis.get("avg_processing_time_available") and avg is not None:
                    facts.append(f"Temps moyen de traitement : {float(avg):.1f} ms.")
                insights.append("Ce KPI vient du snapshot de supervision courant.")
                interpretation.append("Une transaction est comptée comme lente quand son temps de traitement dépasse le seuil opérationnel configuré.")
                recommendations.append("Comparer avec les anomalies et les TPE les plus concernés.")
                return
            facts.append("Le KPI transactions lentes existe dans le dashboard, mais il est marqué indisponible pour cette période.")
            recommendations.append("Vérifier la disponibilité du champ processing_time_ms dans la source rejouée.")
            return
        if metric_subject == "fraud_timeouts" or requested_metric == "fraud_timeout_count":
            if kpis.get("fraud_timeout_available"):
                facts.append(f"Timeouts anti-fraude : {int(kpis.get('fraud_timeouts') or 0)}.")
                insights.append("Ce KPI mesure un signal anti-fraude opérationnel, pas une qualification juridique de fraude confirmée.")
                interpretation.append("Un timeout anti-fraude correspond à une décision anti-fraude lente ou suspecte selon le seuil configuré.")
                recommendations.append("Comparer avec les refus et les anomalies visibles.")
                return
            facts.append("Le KPI timeouts anti-fraude existe dans le dashboard, mais il est marqué indisponible pour cette période.")
            recommendations.append("Vérifier la disponibilité des champs risk_processing_time_ms ou risk_decision dans la source rejouée.")
            return
        total = int(kpis.get("total_transactions") or 0)
        refusal = float(kpis.get("refusal_rate") or 0)
        success = float(kpis.get("success_rate") or 0)
        facts.append(f"Le snapshot de replay contient {total} transactions.")
        facts.append(f"Taux de refus : {refusal:.1f} %. Taux de succès : {success:.1f} %.")
        if total:
            insights.append("Les KPI synthétisent l'état du replay, pas un flux bancaire temps réel de production.")
        interpretation.append("Le taux de refus mesure la proportion de paiements rejetés.")
        interpretation.append("Il est important car il oriente l'analyse vers les commerçants, TPE ou horaires à risque.")
        interpretation.append("Il ne permet pas, seul, d'attribuer une cause technique ou métier précise.")
        recommendations.append("Comparer avec hier ou avec les sept jours précédents.")
        recommendations.append("Contrôler les classements commerçants/TPE si l'objectif est de localiser le risque.")

    @staticmethod
    def _analyze_tpe_count(data: dict[str, Any], facts: list[str], insights: list[str], interpretation: list[str], recommendations: list[str], limitations: list[str]) -> None:
        start = data.get("start_date", "?")
        end = data.get("end_date", "?")
        if data.get("source") == "sql_unavailable":
            facts.append("Connexion SQL Server indisponible pour compter les TPE.")
            limitations.append("Le comptage distinct des TPE nécessite l'outil SQL contrôlé.")
            recommendations.append("Réessayer lorsque SQL Server est disponible.")
            return
        if data.get("source") == "sql_error":
            facts.append("L'outil SQL contrôlé a rencontré une erreur pendant le comptage TPE.")
            limitations.append("Le résultat TPE n'est pas exploitable tant que l'erreur SQL n'est pas résolue.")
            recommendations.append("Vérifier la configuration SQL avant de conclure.")
            return
        active_tpe = int(data.get("active_tpe") or data.get("transactions") or 0)
        if active_tpe == 0:
            facts.append(f"Aucun TPE distinct observé sur {start} à {end}.")
            limitations.append("Sans TPE observé, il n'est pas possible d'analyser la concentration terminal.")
            recommendations.append("Tester une période plus large.")
            return
        facts.append(f"{active_tpe} TPE distincts observés sur {start} à {end}.")
        insights.append("Ce comptage mesure la couverture terminal observée sur la période, pas le volume de transactions.")
        interpretation.append("Un nombre de TPE aide à dimensionner l'activité et à décider si une analyse par terminal est pertinente.")
        interpretation.append("Ce KPI ne dit pas, seul, quels TPE posent problème.")
        recommendations.append("Comparer avec une période voisine pour détecter une variation de couverture.")
        recommendations.append("Consulter le top TPE si l'objectif est d'identifier les terminaux à risque.")

    @staticmethod
    def _analyze_date_range_comparison(data: dict[str, Any], facts: list[str], insights: list[str], interpretation: list[str], anomalies: list[str], recommendations: list[str], limitations: list[str]) -> None:
        left = data.get("left", {})
        right = data.get("right", {})
        delta = data.get("delta", {})
        facts.append(f"{left.get('label')} : {left.get('transactions')} transactions, {left.get('refusal_rate')} % de refus.")
        facts.append(f"{right.get('label')} : {right.get('transactions')} transactions, {right.get('refusal_rate')} % de refus.")
        facts.append(f"L'écart observé est de {delta.get('transactions')} transactions et {delta.get('refusal_rate')} point(s) de refus.")
        left_total = int(left.get("transactions") or 0)
        right_total = int(right.get("transactions") or 0)
        cls_delta = int(delta.get("transactions") or 0)
        if right_total:
            evolution = round(cls_delta / right_total * 100, 2)
            insights.append(f"L'évolution du volume par rapport à {right.get('label')} est de {evolution} %.")
        else:
            limitations.append("L'évolution relative du volume ne peut pas être calculée car la période de référence vaut zéro.")
        rate_delta = float(delta.get("refusal_rate") or 0)
        if cls_delta or rate_delta:
            anomalies.append("La comparaison montre une variation à investiguer; elle ne suffit pas à prouver une anomalie opérationnelle.")
        else:
            insights.append("Les deux périodes sont stables sur les indicateurs disponibles.")
        interpretation.append("La comparaison met en évidence les écarts, mais l'explication nécessite des dimensions complémentaires.")
        recommendations.append("Comparer les commerçants les plus contributeurs sur les deux périodes.")
        recommendations.append("Examiner les horaires si l'écart porte sur les refus.")

    @staticmethod
    def _analyze_period_comparison(data: dict[str, Any], facts: list[str], insights: list[str], interpretation: list[str], anomalies: list[str], recommendations: list[str]) -> None:
        left = data.get("left", {})
        right = data.get("right", {})
        delta = data.get("delta", {})
        facts.append(f"Comparaison {left.get('period')} vs {right.get('period')} : {delta.get('transactions')} transactions d'écart.")
        facts.append(f"Écart de taux de refus : {delta.get('refusal_rate')} point(s).")
        if delta.get("transactions") or delta.get("refusal_rate"):
            anomalies.append("Un écart existe entre les périodes comparées; il doit être relié aux dimensions disponibles avant conclusion.")
        else:
            insights.append("Aucun écart significatif n'apparaît dans les métriques comparées.")
        interpretation.append("Cette comparaison repose sur les périodes supportées par le snapshot contrôlé.")
        recommendations.append("Identifier les commerçants ou TPE qui expliquent l'écart.")

    @staticmethod
    def _analyze_ranking(data: Any, label: str, facts: list[str], insights: list[str], interpretation: list[str], recommendations: list[str], limitations: list[str]) -> None:
        if not isinstance(data, dict):
            limitations.append(f"Le classement {label} n'est pas dans un format exploitable.")
            return
        if data.get("source") == "sql_unavailable":
            facts.append(f"Classement {label} indisponible car SQL Server n'est pas accessible.")
            limitations.append("Le classement nécessite l'outil SQL contrôlé.")
            recommendations.append("Rejouer le classement lorsque SQL Server est disponible.")
            return
        rows = list(data.get("rows") or [])
        diagnostic = data.get("diagnostic") if isinstance(data.get("diagnostic"), dict) else {}
        start = diagnostic.get("start_date")
        end = diagnostic.get("end_date")
        if not rows:
            facts.append(f"Aucun élément trouvé dans le classement {label}.")
            limitations.append("Le classement ne permet pas d'identifier une concentration.")
            recommendations.append("Tester une autre période ou une autre dimension.")
            return
        period = f" sur {start} à {end}" if start and end else ""
        facts.append(f"{len(rows)} élément(s) retourné(s) dans le classement {label}{period}.")
        top = rows[0]
        name = top.get("terminal_id") if label == "TPE" else top.get("merchant_name")
        name = name or top.get("merchant_name") or top.get("name") or top.get("terminal_id") or "N/A"
        facts.append(f"Premier élément : {name}, avec {top.get('transactions', 'N/A')} transactions et {top.get('refused', 'N/A')} refus.")
        insights.append("Le classement sert à prioriser l'investigation sur les points les plus visibles dans les preuves.")
        interpretation.append("Un classement élevé indique une concentration observable, pas nécessairement une responsabilité causale.")
        recommendations.append("Comparer ce classement avec les incidents horaires.")
        recommendations.append("Vérifier si le même élément reste présent sur une autre période.")

    @staticmethod
    def _analyze_knowledge(chunks: Any, facts: list[str], insights: list[str], interpretation: list[str], recommendations: list[str], limitations: list[str]) -> None:
        normalized_chunks = BusinessAnalysisEngine._normalize_knowledge_chunks(chunks)
        if not normalized_chunks:
            limitations.append("Aucun extrait RAG local pertinent n'a été trouvé.")
            recommendations.append("Reformuler la question d'architecture ou cibler un composant précis.")
            return
        text = str(normalized_chunks[0].get("text") or "").strip()
        facts.append(text[:520])
        insights.append("Cette réponse repose sur la documentation locale du projet.")
        interpretation.append("Le RAG documentaire local explique l'architecture; il ne remplace pas les SQL Tools pour analyser les données métier.")

    @staticmethod
    def _normalize_knowledge_chunks(chunks: Any) -> list[dict[str, Any]]:
        if chunks is None:
            return []
        if isinstance(chunks, str):
            text = chunks.strip()
            return [{"text": text}] if text else []
        if isinstance(chunks, dict):
            text = chunks.get("text") or chunks.get("content") or chunks.get("answer") or chunks.get("summary")
            if text:
                return [{**chunks, "text": str(text)}]
            nested = chunks.get("chunks") or chunks.get("results") or chunks.get("documents")
            return BusinessAnalysisEngine._normalize_knowledge_chunks(nested)
        if isinstance(chunks, list):
            normalized: list[dict[str, Any]] = []
            for item in chunks:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or item.get("answer") or item.get("summary")
                    if text:
                        normalized.append({**item, "text": str(text)})
                elif isinstance(item, str) and item.strip():
                    normalized.append({"text": item.strip()})
            return normalized
        return []

    @staticmethod
    def _analyze_opinion(opinion: dict[str, Any], facts: list[str], insights: list[str], interpretation: list[str], recommendations: list[str]) -> None:
        kpi = opinion.get("kpi_context", {})
        facts.append(f"{kpi.get('total_transactions', 'N/A')} transactions rejouées dans le contexte disponible.")
        facts.append(f"Taux de refus : {kpi.get('refusal_rate', 'N/A')} %. Score de risque : {kpi.get('global_risk_score', 'N/A')}.")
        insights.append("Le replay historique est utile pour une démonstration reproductible.")
        interpretation.append("La crédibilité vient de l'articulation entre KPI, EventBus, Redis, WebSocket et replay contrôlé.")
        interpretation.append("La limite principale est que ce n'est pas un flux bancaire temps réel de production.")
        recommendations.append("Présenter explicitement cette limite lors d'une soutenance.")

    @staticmethod
    def _confidence(bundle: EvidenceBundle, facts: list[str], limitations: list[str]) -> float:
        if not facts:
            return 0.25
        if any("indisponible" in item.lower() or "erreur" in item.lower() for item in limitations):
            return 0.45
        if bundle.has_sql or bundle.has_snapshot:
            return 0.91
        if bundle.has_rag:
            return 0.72
        return 0.6

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        if confidence >= 0.8:
            return "élevé"
        if confidence >= 0.55:
            return "moyen"
        return "faible"

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for item in items:
            clean = str(item).strip()
            if clean and clean not in seen:
                seen.add(clean)
                result.append(clean)
        return result
