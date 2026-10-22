"""Literal term boundaries and configured protected spans; no model requests."""
import unittest
from unittest.mock import patch

import ainiee_bridge as bridge
import test_translation_workflow as fixtures


class TermBoundaryTests(unittest.TestCase):
    options = {'case_sensitive': False, 'whole_word': True}

    def match(self, source, terms, options=None, rules=None):
        analysis = {'terms': [{'source': src, 'recommended_translation': dst} for src, dst in terms]}
        return [(r['src'], r['dst']) for r in bridge.matched_terms(
            analysis, [source], options or self.options, rules=rules)]

    def test_protected_marker_contents_do_not_become_prose_terms(self):
        terms = [('Player', '玩家'), ('man', '男人'), ('woman', '女人'), ('red', '红色')]
        self.assertEqual(self.match('[Player:man/woman] <color=red> [color="red"]', terms), [])
        self.assertEqual(self.match('[Player:man/woman] meets a man.', terms), [('man', '男人')])

    def test_explicit_whole_markers_and_keep_original_entries_still_match(self):
        terms = [('Player', '玩家'), ('[Player:man/woman]', '保留原文'), ('[mc]', '保留原文')]
        matched = self.match('[Player:man/woman] [mc]', terms)
        self.assertEqual(matched, [('[Player:man/woman]', '保留原文'), ('[mc]', '保留原文')])
        analysis = {'non_translate': [{'source': 'Player'}, {'source': '[Player:man/woman]'}]}
        result = bridge.matched_terms(analysis, ['[Player:man/woman]'], self.options)
        self.assertEqual([t['src'] for t in result], ['[Player:man/woman]'])

    def test_do_not_match_contraction_fragments_but_preserve_full_contractions(self):
        terms = [('Don', '唐河'), ('t', '某人'), ('re', '某地')]
        for source in ("don't", 'don’t', "DON'T", "you're", 'you’re'):
            with self.subTest(source=source):
                self.assertEqual(self.match(source, terms), [])
        self.assertEqual(self.match("don't", [("don't", '不要')]), [("don't", '不要')])

    def test_real_names_quoted_names_and_possessives_remain_matchable(self):
        for source in ('Don', "Don's river", 'Don’s river', "'Don'", '‘Don’', 'Don!'):
            with self.subTest(source=source):
                self.assertEqual(self.match(source, [('Don', '唐河')]), [('Don', '唐河')])
        self.assertEqual(self.match("O'Neill's sword", [("O'Neill", '奥尼尔')]), [("O'Neill", '奥尼尔')])

    def test_whole_word_and_case_options_keep_their_meaning(self):
        self.assertEqual(self.match('London', [('Don', '唐河')]), [])
        self.assertEqual(self.match("don't", [('Don', '唐河')], {**self.options, 'whole_word': False}), [('don', '唐河')])
        self.assertEqual(self.match('don', [('Don', '唐河')], {**self.options, 'case_sensitive': True}), [])

    def test_protection_configuration_is_part_of_the_match_cache_key(self):
        analysis = {'terms': [{'source': 'Player', 'recommended_translation': '玩家'}]}
        text = ['[Player:Name]']
        self.assertEqual(bridge.matched_terms(analysis, text, self.options), [])
        disabled = {**self.options, 'placeholder_family_overrides': {'square_bracket': None}}
        self.assertEqual([r['src'] for r in bridge.matched_terms(analysis, text, disabled)], ['Player'])
        # Actual per-project rules override fallback options, including named-curly choices.
        self.assertEqual(bridge.matched_terms(analysis, ['{Player}'], self.options,
                                            rules=bridge.patterns(True)), [])
        self.assertEqual([r['src'] for r in bridge.matched_terms(analysis, ['{Player}'], self.options,
                                                              rules=bridge.patterns(False))], ['Player'])
        self.assertEqual(self.match('HIDE_Player', [('Player', '玩家')],
            {**self.options, 'whole_word': False, 'exclusions': [{'markers': 'HIDE_Player'}]}), [])

    def test_filter_before_longest_match_and_leave_tagged_prose_visible(self):
        terms = [('Sword', '剑'), ('Magic Sword', '魔法剑'), ('i', '某人')]
        self.assertEqual(self.match('[i]Magic Sword[/i]', terms), [('Magic Sword', '魔法剑')])
        # Suppress a long match cut off inside a tag before resolving overlap,
        # allowing the genuine shorter prose term to survive.
        self.assertEqual(self.match('Sword [Player:Name]', [('Sword', '剑'), ('Sword [Player', '错误译名')]),
                         [('Sword', '剑')])

    def test_non_latin_terms_and_explicit_regex_still_work(self):
        self.assertEqual(self.match('勇者', [('勇者', '英雄')]), [('勇者', '英雄')])
        analysis = {'terms': [{'source': r'Magic\s+Sword', 'recommended_translation': '魔法剑',
                              'src_state': 'regex'}]}
        self.assertEqual([r['dst'] for r in bridge.matched_terms(
            analysis, ['Magic Sword'], self.options)], ['魔法剑'])

    def test_frozen_punctuation_is_literal_not_inferred_regex(self):
        terms = [('[Sorrow Flame]', '[悲伤之焰]'), ('Matkina (Young)', '年轻玛特金娜'),
                 ('V.', '维'), ('Urmpfh?', '乌尔姆普夫？')]
        self.assertEqual(self.match('A normal sentence with spaces.', terms), [])
        for source, target in terms:
            with self.subTest(source=source):
                self.assertEqual(self.match(source, terms), [(source, target)])
        analysis = {'non_translate': [{'source': '[DEBUG] Continue.'}]}
        self.assertEqual(bridge.matched_terms(analysis, ['D and E are letters.'], self.options), [])


class TermCacheVersionTests(unittest.TestCase):
    def test_old_matching_policy_receipts_are_rechecked_without_retranslating(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        fixture.freeze(True)
        fixture.translate()
        flow = fixture.flow
        with patch.object(bridge, 'TERM_MATCH_VERSION', 1):
            self.assertTrue(flow.draft_scan()['clean'])
        with flow.db() as db:
            before = [tuple(r) for r in db.execute('SELECT id,translation,exceptions FROM records ORDER BY ordinal')]
        result = flow.draft_scan()
        self.assertTrue(result['clean'])
        self.assertEqual(result['checked_records'], 4)
        with flow.db() as db:
            self.assertEqual(before, [tuple(r) for r in db.execute('SELECT id,translation,exceptions FROM records ORDER BY ordinal')])


if __name__ == '__main__':
    unittest.main()
