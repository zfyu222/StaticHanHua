import unittest
from glossary_families import family_plan, batch_hints


def entries(names):
    return [{'ref': f'terms:{i}', 'row': {'source': name, 'recommended_translation': name}}
            for i, name in enumerate(names)]


class FamilyTests(unittest.TestCase):
    def test_variants_derivatives_and_compounds_together(self):
        rows = entries(['Warden', 'Unrelated', 'Wardens', 'The Warden',
                        'Alchemy', 'Alchemist', 'Alchemical', 'Blood Moon', 'Blood Moon Cult'])
        batches, hints = family_plan(rows, 4)
        groups = [{e['row']['source'] for e in b} for b in batches]
        self.assertTrue(any({'Warden', 'Wardens', 'The Warden'} <= g for g in groups))
        self.assertTrue(any({'Alchemy', 'Alchemist', 'Alchemical'} <= g for g in groups))
        self.assertTrue(any({'Blood Moon', 'Blood Moon Cult'} <= g for g in groups))
        self.assertEqual({h['kind'] for h in hints}, {'variant', 'root', 'component'})

    def test_no_arbitrary_prefix_matching_or_translation_mutation(self):
        rows = entries(['Fire', 'Firearm', 'Iron', 'Irony'])
        _, hints = family_plan(rows, 60)
        self.assertEqual(hints, [])
        self.assertEqual(rows, entries(['Fire', 'Firearm', 'Iron', 'Irony']))

    def test_large_family_has_no_lost_duplicate_or_oversized_batches(self):
        rows = entries([f'Blood Moon {i}' for i in range(181)])
        batches, hints = family_plan(rows, 60)
        self.assertEqual([len(b) for b in batches], [60, 60, 60, 1])
        self.assertEqual([e for b in batches for e in b], rows)
        view = batch_hints(batches[0], rows, hints)
        self.assertLessEqual(len(view['related_terms']), 12)
        self.assertLessEqual(len(view['candidates']), 120)
        self.assertTrue(all('evidence_rows' not in e for e in view['related_terms']))

    def test_empty_and_unrelated_entries(self):
        self.assertEqual(family_plan([], 60), ([[]], []))
        rows = entries(['甲', '乙', 'First', 'Second', 'Third'])
        batches, _ = family_plan(rows, 2)
        self.assertEqual([len(b) for b in batches], [2, 2, 1])

    def test_numbered_names_are_not_identical_variants(self):
        _, hints = family_plan(entries(['Sector 1', 'Sector 2']), 60)
        self.assertFalse(any(h['kind'] == 'variant' for h in hints))


if __name__ == '__main__':
    unittest.main()
