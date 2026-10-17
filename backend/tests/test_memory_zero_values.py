import unittest

from app.chatbot.memory import ConversationMemory


class MemoryZeroValuesTests(unittest.TestCase):
    def test_zero_summary_values_override_stale_fallbacks(self):
        memory = ConversationMemory()
        memory.update_state('zero', result_summary={
            'rate': 0, 'count': 0,
            'source_values': {'refusal_rate': 50, 'refused': 9, 'transactions': 0, 'active_tpe': 7},
        })
        context = memory.state('zero').current_analysis_context
        self.assertIsNotNone(context)
        self.assertEqual(context.rate, 0)
        self.assertEqual(context.count, 0)
        self.assertEqual(context.primary_value, 0)

    def test_absent_values_still_use_source_fallbacks(self):
        memory = ConversationMemory()
        memory.update_state('fallback', result_summary={
            'source_values': {'refusal_rate': 12, 'refused': 3, 'transactions': 25},
        })
        context = memory.state('fallback').current_analysis_context
        self.assertEqual((context.rate, context.count, context.primary_value), (12, 3, 25))
