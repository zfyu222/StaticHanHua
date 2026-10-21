import copy
import unittest
from test_api_minimal import MinimalAPITests
from workflow_quality import validate_glossary_reviews


class DiagnosticTests(unittest.TestCase):
    def test_wrong_excerpt_keeps_rejection_and_identifies_bound_context(self):
        fixture=MinimalAPITests(); payload=fixture.glossary(); response=fixture.decisions()
        response['entry_reviews'][0]['source_excerpt']='Rest there.'
        result=fixture.decode(payload,{'decisions':response})['decisions']; before=copy.deepcopy(result)
        with self.assertRaises(ValueError) as error: validate_glossary_reviews(result,payload)
        self.assertIn('ref=terms:12',str(error.exception))
        self.assertIn('available_evidence_rows=[73]',str(error.exception))
        self.assertIn('excerpt_matches_cited_rows=[]',str(error.exception))
        self.assertEqual(result,before)

    def test_nonprovided_ordinal_is_reported_and_not_substituted(self):
        fixture=MinimalAPITests(); payload=fixture.glossary(); payload['entries'][0]['row']['evidence_rows'].append(74)
        response=fixture.decisions(); response['entry_reviews'][0]['evidence_rows']=[74]
        result=fixture.decode(payload,{'decisions':response})['decisions']
        with self.assertRaises(ValueError) as error: validate_glossary_reviews(result,payload)
        self.assertIn('invalid_or_unprovided_rows=[74]',str(error.exception))
        self.assertEqual(result['entry_reviews'][0]['evidence_rows'],[74])


if __name__=='__main__': unittest.main()
