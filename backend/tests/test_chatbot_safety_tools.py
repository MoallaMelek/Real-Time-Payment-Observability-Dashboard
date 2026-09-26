import unittest
from pathlib import Path

from app.chatbot.answer_sanitizer import AnswerSanitizer
from app.chatbot.project_code_search import ProjectCodeSearchTool


class ChatbotSafetyToolsTests(unittest.TestCase):
    def test_answer_sanitizer_blocks_code_for_non_code_answer(self) -> None:
        answer = "Voici le composant: const [messages, setMessages] = useState([])"
        sanitized = AnswerSanitizer.sanitize(answer, code_allowed=False)
        self.assertNotIn("useState", sanitized)
        self.assertNotIn("const ", sanitized)

    def test_answer_sanitizer_allows_code_when_explicit(self) -> None:
        answer = "const value = 1"
        self.assertEqual(AnswerSanitizer.sanitize(answer, code_allowed=True), answer)

    def test_project_code_search_finds_chat_component(self) -> None:
        root = Path(__file__).resolve().parents[2]
        results = ProjectCodeSearchTool.search("montre-moi le code du composant chat", root=root, limit=1)
        self.assertTrue(results)
        self.assertEqual(results[0]["path"], "frontend/src/components/ChatAssistant.tsx")
        self.assertIn("ChatAssistant", str(results[0]["excerpt"]))


if __name__ == "__main__":
    unittest.main()
