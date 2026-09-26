from dataclasses import dataclass
import json
from pathlib import Path
import re

from app.chatbot.router import normalize_text


@dataclass(frozen=True, slots=True)
class KnowledgeChunk:
    title: str
    text: str
    tags: tuple[str, ...]


BUILTIN_CHUNKS = [
    KnowledgeChunk(
        "Architecture",
        "portfolio_demo SQL Server alimente un ReplayProducer. Les transactions sont publiees dans un EventBus, consommees par l'Aggregator et le RiskAlertEngine, puis le snapshot KPI est stocke dans RedisKpiCache et diffuse au dashboard par WebSocket/API.",
        ("architecture", "replay", "eventbus", "redis"),
    ),
    KnowledgeChunk(
        "Redis",
        "Redis sert de cache KPI partage. Le backend calcule les indicateurs progressivement pendant le replay, stocke le snapshot sous dashboard:supervision:snapshot, puis le dashboard le recoit via WebSocket ou API. Si Redis est indisponible, le backend retombe sur InMemoryKpiCache.",
        ("redis", "cache", "snapshot"),
    ),
    KnowledgeChunk(
        "EventBus",
        "L'EventBus decouple la lecture SQL du calcul des KPI. Le replay publie des evenements transactionnels et l'Aggregator maintient une projection incrementale sans relire SQL Server a chaque rafraichissement.",
        ("eventbus", "architecture", "projection"),
    ),
    KnowledgeChunk(
        "Fast Forward",
        "Fast Forward accelere le replay sans sauter directement a l'etat final. Les transactions restent appliquees par lots visibles, ce qui permet au dashboard, aux WebSocket et au cache Redis de montrer des etats intermediaires.",
        ("fast-forward", "replay", "period"),
    ),
    KnowledgeChunk(
        "Affiliations",
        "Le stock d'affiliations est initialise depuis dbo.demo_affiliation_inventory. Pendant le replay, il evolue comme projection metier selon le comportement observe des commercants : frequence, volume, diversite des TPE et inactivite.",
        ("affiliation", "stock", "projection"),
    ),
    KnowledgeChunk(
        "Confidentialite",
        "Le dashboard et le chatbot excluent les donnees sensibles comme RIB, PAN, OTP, PIN, emails, msisdn, noms porteurs et cles techniques. Les reponses doivent rester agregees et non sensibles.",
        ("security", "confidentiality", "sensitive"),
    ),
    KnowledgeChunk(
        "KPI",
        "Le taux de refus correspond aux transactions refusees divisees par le total traite. Le taux de succes correspond aux transactions autorisees divisees par le total. Les KPI N/A indiquent que le signal source n'existe pas pour la periode ou dans portfolio_demo.",
        ("kpi", "metric", "refusal", "success", "na"),
    ),
]


class KnowledgeBase:
    def __init__(self, chunks: list[KnowledgeChunk] | None = None) -> None:
        self._chunks = chunks or self._load_chunks()

    def search(self, query: str, top_k: int = 3) -> list[KnowledgeChunk]:
        normalized_query = normalize_text(query)
        code_requested = self._asks_for_code(normalized_query)
        terms = {term for term in re.split(r"\W+", normalized_query) if len(term) >= 3}
        scored: list[tuple[int, KnowledgeChunk]] = []
        for chunk in self._chunks:
            if not code_requested and self._is_frontend_code_chunk(chunk):
                continue
            haystack = normalize_text(" ".join((chunk.title, chunk.text, " ".join(chunk.tags))))
            score = sum(3 for tag in chunk.tags if normalize_text(tag) in normalized_query)
            score += sum(1 for term in terms if term in haystack)
            normalized_title = normalize_text(chunk.title)
            if normalized_title and normalized_title in normalized_query:
                score += 8
            if "readme" not in chunk.tags and any(tag in terms for tag in chunk.tags):
                score += 4
            if "readme" in chunk.tags or any(tag in chunk.tags for tag in ("architecture", "redis", "eventbus", "replay", "kpi", "security", "confidentiality")):
                score += 2
            if score:
                scored.append((score, chunk))
        return [chunk for _, chunk in sorted(scored, key=lambda item: item[0], reverse=True)[:top_k]]

    @staticmethod
    def _asks_for_code(normalized_query: str) -> bool:
        return any(token in normalized_query for token in ("code", "tsx", "react", "composant", "component", "implementation", "fonction", "function"))

    @staticmethod
    def _is_frontend_code_chunk(chunk: KnowledgeChunk) -> bool:
        title = chunk.title.lower()
        text = chunk.text
        if any(title.endswith(ext) or ext in title for ext in (".tsx", ".ts", ".jsx", ".js", ".py")):
            return True
        return any(token in text for token in ("useState", "useEffect", "<div", "interface ", "export ", "import ", "const ", "def ", "class ", "async def", "from app.", "```", "flowchart", "-->"))

    @staticmethod
    def _load_chunks() -> list[KnowledgeChunk]:
        chunks = list(BUILTIN_CHUNKS)
        root = Path(__file__).resolve().parents[3]
        index = root / "backend" / "data" / "rag_business_index.json"
        if index.exists():
            try:
                payload = json.loads(index.read_text(encoding="utf-8"))
                for item in payload.get("chunks", []):
                    chunks.append(
                        KnowledgeChunk(
                            title=str(item.get("title") or "Project"),
                            text=str(item.get("text") or "")[:1800],
                            tags=tuple(str(tag) for tag in item.get("tags", ("project",))),
                        )
                    )
            except (OSError, json.JSONDecodeError, TypeError):
                pass
        readme = root / "README.md"
        if readme.exists():
            text = readme.read_text(encoding="utf-8", errors="ignore")
            sections = re.split(r"\n(?=##\s+)", text)
            for section in sections:
                clean = section.strip()
                if not clean:
                    continue
                title = clean.splitlines()[0].strip("# ").strip()[:80]
                chunks.append(KnowledgeChunk(title=title or "README", text=clean[:1600], tags=("readme", "project")))
        return chunks
