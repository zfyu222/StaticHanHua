"""Scoring invariants independent of actual model performance."""
import unittest
from compare_models import distance,normalized,ordered

class ComparisonTests(unittest.TestCase):
    def test_edit_distance_counts_errors_and_empty_outputs(self):
        self.assertEqual(distance('kitten','sitting'),3)
        self.assertEqual(distance('draw',''),4)
        self.assertEqual(distance('','abc'),3)

    def test_normalization_keeps_chinese_numbers_and_removes_layout(self):
        self.assertEqual(normalized('ＡＢＣ 75%\n湖景：小屋！'),'abc75湖景小屋')

    def test_reading_order_uses_geometry_not_reference_text(self):
        r=lambda text,box:dict(text=text,box=box)
        self.assertEqual(ordered([r('WORLD',[40,11,90,22]),r('NEXT',[0,30,40,40]),r('HELLO',[0,10,35,21])]),'HELLO WORLD NEXT')

if __name__=='__main__':unittest.main()
