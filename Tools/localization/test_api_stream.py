import unittest
from types import SimpleNamespace as N
from ainiee_bridge import collect_api_stream, ApiFailure


class Response:
    def __init__(self, chunks): self.chunks = chunks; self.closed = False
    def __iter__(self): return iter(self.chunks)
    def close(self): self.closed = True


def chunk(content=None, finish=None, usage=None, reasoning=None):
    return N(model='fixture', usage=usage, choices=[N(index=0, finish_reason=finish,
        delta=N(content=content, reasoning_content=reasoning))])


class StreamTests(unittest.TestCase):
    def test_reasoning_never_enters_answer_and_final_usage_is_not_double_counted(self):
        usage = N(prompt_tokens=10, completion_tokens=20, total_tokens=30,
                  completion_tokens_details={'reasoning_tokens': 12})
        response = Response([chunk(reasoning='private reasoning'), chunk('{"analysis":'),
            chunk('{}', usage=usage), chunk('}', 'stop'), N(choices=[], usage=usage, model='fixture')])
        content, data = collect_api_stream(response)
        self.assertEqual(content, '{"analysis":{}}')
        self.assertEqual(data['total_tokens'], 30)
        self.assertEqual(data['completion_tokens_details']['reasoning_tokens'], 12)
        self.assertTrue(response.closed)

    def test_missing_terminal_event_rejects_even_valid_json(self):
        response = Response([chunk('{}')])
        with self.assertRaises(ApiFailure) as error: collect_api_stream(response)
        self.assertEqual(str(error.exception), 'incomplete_stream')
        self.assertFalse(error.exception.usage['usage_reported'])
        self.assertTrue(response.closed)

    def test_length_and_missing_usage_are_not_fabricated(self):
        response = Response([chunk('{', 'length')])
        content, data = collect_api_stream(response)
        self.assertEqual(content, '{')
        self.assertEqual(data['finish_reason'], 'length')
        self.assertFalse(data['usage_reported'])
        self.assertNotIn('total_tokens', data)

    def test_disconnect_preserves_last_reported_usage_and_never_accepts_partial(self):
        class Broken(Response):
            def __iter__(self):
                yield chunk('{}', usage=N(prompt_tokens=10, completion_tokens=20, total_tokens=30))
                raise OSError('fixture disconnect')
        response = Broken([])
        with self.assertRaises(ApiFailure) as error: collect_api_stream(response)
        self.assertEqual(str(error.exception), 'stream_interrupted')
        self.assertEqual(error.exception.usage['total_tokens'], 30)
        self.assertTrue(response.closed)


if __name__ == '__main__': unittest.main()
