import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import translation_workflow as workflow
import test_translation_workflow as fixtures
from workflow_allocation import allocation_session


class PreparationCacheTests(unittest.TestCase):
    def test_same_transaction_reads_shared_inputs_once_and_keeps_exact_plans(self):
        fixture = fixtures.WorkflowTests(); fixture.setUp(); self.addCleanup(fixture.tearDown)
        flow = fixture.flow; fixture.extract(True); flow.prepare('glossary_review')
        with flow.db() as db:
            task = db.execute('SELECT payload FROM tasks WHERE stage="glossary_review" LIMIT 1').fetchone()
            payload = json.loads(task['payload']); before = copy.deepcopy(payload)
            @allocation_session
            def twice(instance):
                return (instance.plan_task_allocation(db,payload,'api'),
                        instance.plan_task_allocation(db,payload,'api'))
            with patch.object(workflow,'read_json',wraps=workflow.read_json) as read:
                first, second = twice(flow)
                self.assertEqual(first,second)
                self.assertEqual(payload,before)
                snapshot_reads = lambda: sum(Path(c.args[0]).name=='glossary_original.json' for c in read.call_args_list)
                self.assertEqual(snapshot_reads(),1)
                # The cache has ended: a later independent operation rereads.
                self.assertEqual(flow.plan_task_allocation(db,payload,'api'),first)
                self.assertEqual(snapshot_reads(),2)

    def test_exception_restores_outer_session_without_leaking(self):
        from workflow_allocation import _PREPARATION_INPUTS
        target=object()
        @allocation_session
        def fail(instance):
            self.assertIs(_PREPARATION_INPUTS.get()[0],instance)
            raise ValueError('fixture')
        self.assertIsNone(_PREPARATION_INPUTS.get())
        with self.assertRaises(ValueError): fail(target)
        self.assertIsNone(_PREPARATION_INPUTS.get())


if __name__=='__main__': unittest.main()
