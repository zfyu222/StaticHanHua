import copy
import json
from pathlib import Path
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from unittest.mock import patch

import ainiee_bridge as bridge
import translation_workflow as w
from workflow_api_failover import ProviderPool, fault_kind, validate_policy
from api_stability_report import build
from workflow_api_budget import budget_requests, effective_attempts


class PoolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.calls=[]

    def pool(self, failures, names=('yjapi','vortex','acy7','deepseek'), limits=None):
        failures=dict(failures);calls=self.calls
        class Client:
            def __init__(self,s,key):self.name=s['model_name']
            def close(self):pass
            def request(self,system,user):
                calls.append(self.name)
                if failures.get(self.name,0):
                    failures[self.name]-=1
                    raise bridge.ApiFailure('InternalServerError:502',True)
                return 'PRIVATE RESPONSE',{'usage_reported':True,'prompt_tokens':7,'completion_tokens':11,
                    'total_tokens':18,'prompt_tokens_details':{'cached_tokens':2},
                    'completion_tokens_details':{'reasoning_tokens':3},'response_model':self.name}
        specs=[{'name':name,'key':'fixture-SECRET-'+name,'settings':{
            **w.DEFAULT['api'],'api_url':'https://fixture-user:fixture-password@fixture.invalid/v1',
            'model_name':name,'think_switch':True,'concurrency':(limits or {}).get(name,25)}}for name in names]
        p=ProviderPool(specs,{'enabled':True,'priority':list(names),'failure_threshold':5,'cooldown_seconds':60},
            self.root,'translate',Client);self.addCleanup(p.close)
        self.now=0;p.clock=lambda:self.now
        return p

    def advance(self,p,seconds):
        with p.cv:self.now+=seconds;p.cv.notify_all()

    def execute(self,p,lease=None):
        return p.execute(lease or p.acquire(),'PRIVATE SOURCE','PRIVATE REQUEST',len(self.calls)+1)

    def test_order_bounded_retries_costs_and_no_secrets(self):
        p=self.pool({'yjapi':5,'vortex':5,'acy7':5})
        for _ in range(15):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        _,usage=self.execute(p)
        self.assertEqual(self.calls,['yjapi']*5+['vortex']*5+['acy7']*5+['deepseek'])
        self.assertEqual(usage['api_name'],'deepseek')
        s=p.audit.summary();self.assertEqual(s['providers']['deepseek']['reported_tokens']['total'],18)
        self.assertEqual(s['providers']['deepseek']['reported_tokens']['reasoning'],3)
        self.assertIsNone(s['providers']['yjapi']['reported_tokens']['total'])
        self.assertEqual(s['providers']['yjapi']['usage_unknown_requests'],5)
        self.assertEqual(len(s['transitions']),3)
        text=p.audit.log.read_text(encoding='utf8')+json.dumps(s)
        for secret in ('fixture-SECRET','fixture-password','fixture-user','PRIVATE REQUEST','PRIVATE RESPONSE'):
            self.assertNotIn(secret,text)

    def test_concurrent_old_failures_do_not_skip_next_provider(self):
        p=self.pool({'yjapi':100},names=('yjapi','vortex'),limits={'vortex':2})
        old=[p.acquire()for _ in range(25)]
        for lease in old[:5]:
            with self.assertRaises(bridge.ApiFailure):self.execute(p,lease)
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')
        self.execute(p)
        for lease in old[5:]:
            with self.assertRaises(bridge.ApiFailure):self.execute(p,lease)
        self.assertEqual(p.state_snapshot()['vortex'],'normal')
        self.assertEqual(p.states[1].consecutive_failures,0)
        self.advance(p,60)
        probe=p.acquire();self.assertEqual(probe.index,0);self.assertTrue(probe.probe)
        self.assertEqual(p.state_snapshot()['yjapi'],'awaiting_retry')
        one=p.acquire();two=p.acquire()
        self.assertEqual(one.index,1);self.assertEqual(two.index,1)
        self.assertEqual(p.active[1],2)
        with self.assertRaisesRegex(bridge.ApiFailure,'dispatch_stopped'):p.acquire(lambda:True)
        with self.assertRaises(bridge.ApiFailure):self.execute(p,probe)
        p.release(one);p.release(two)
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')

    def test_all_disabled_wait_until_cooldown_then_one_probe_per_api(self):
        p=self.pool({name:100 for name in ('yjapi','vortex','acy7','deepseek')})
        for _ in range(20):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        self.assertFalse(p.exhausted)
        self.assertTrue(all(s=='disabled'for s in p.state_snapshot().values()))
        with ThreadPoolExecutor(max_workers=1)as executor:
            pending=executor.submit(p.acquire)
            try:
                with self.assertRaises(TimeoutError):pending.result(timeout=.03)
            finally:self.advance(p,60)
            first=pending.result(timeout=2)
        probes=[first]+[p.acquire()for _ in range(3)]
        self.assertEqual([l.index for l in probes],[0,1,2,3])
        self.assertTrue(all(s=='awaiting_retry'for s in p.state_snapshot().values()))
        with self.assertRaisesRegex(bridge.ApiFailure,'dispatch_stopped'):p.acquire(lambda:True)
        for lease in probes:p.release(lease)
        self.assertEqual(len(self.calls),20)

    def test_high_priority_returns_after_one_successful_probe(self):
        p=self.pool({'yjapi':5},names=('yjapi','vortex'))
        for _ in range(5):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        self.execute(p);self.assertEqual(self.calls[-1],'vortex')
        self.advance(p,59)
        lease=p.acquire();self.assertEqual(lease.index,1);p.release(lease)
        self.advance(p,1)
        lease=p.acquire();self.assertEqual(lease.index,0);self.assertTrue(lease.probe)
        self.execute(p,lease)
        self.assertEqual(p.state_snapshot()['yjapi'],'normal')
        lease=p.acquire();self.assertEqual(lease.index,0);self.assertFalse(lease.probe);p.release(lease)

    def test_failed_probe_disables_for_another_full_minute(self):
        p=self.pool({'yjapi':10},names=('yjapi','vortex'))
        for _ in range(5):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        self.advance(p,60)
        with self.assertRaises(bridge.ApiFailure):self.execute(p)
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')
        self.advance(p,59)
        lease=p.acquire();self.assertEqual(lease.index,1);p.release(lease)
        self.advance(p,1)
        lease=p.acquire();self.assertEqual(lease.index,0);self.assertTrue(lease.probe);p.release(lease)

    def test_failed_task_rescans_priority_instead_of_sticking_to_backup(self):
        p=self.pool({'yjapi':5,'vortex':1},names=('yjapi','vortex'))
        for _ in range(5):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        backup=p.acquire();self.assertEqual(backup.index,1)
        self.advance(p,60)  # Primary cooldown expires while the backup is in flight.
        with self.assertRaises(bridge.ApiFailure):self.execute(p,backup)
        self.assertEqual(p.state_snapshot()['vortex'],'normal')
        retry=p.acquire();self.assertEqual(retry.index,0);self.assertTrue(retry.probe);p.release(retry)

    def test_cancelled_probe_and_late_success_do_not_restore_normal(self):
        p=self.pool({'yjapi':5},names=('yjapi','vortex'))
        stale=p.acquire()
        for _ in range(5):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        p.release(stale,success=True)
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')
        self.advance(p,60)
        probe=p.acquire();p.release(probe)
        self.assertEqual(p.state_snapshot()['yjapi'],'retry_ready')
        lease=p.acquire();self.assertTrue(lease.probe);p.release(lease)

    def test_old_failure_cannot_disable_an_api_restored_by_probe(self):
        p=self.pool({'yjapi':5},names=('yjapi','vortex'))
        old=[p.acquire()for _ in range(3)]
        for _ in range(5):
            with self.assertRaises(bridge.ApiFailure):self.execute(p)
        self.advance(p,60);self.execute(p)
        for lease in old:p.release(lease,'InternalServerError:502')
        self.assertEqual(p.state_snapshot()['yjapi'],'normal')
        self.assertEqual(p.states[0].consecutive_failures,0)

    def test_success_resets_consecutive_failure_count(self):
        p=self.pool({},names=('yjapi','vortex'))
        for _ in range(4):p.release(p.acquire(),'InternalServerError:502')
        self.assertEqual(p.states[0].consecutive_failures,4)
        self.execute(p)
        self.assertEqual(p.states[0].consecutive_failures,0)
        p.release(p.acquire(),'InternalServerError:502')
        self.assertEqual(p.state_snapshot()['yjapi'],'normal')

    def test_model_or_control_error_does_not_disable_api(self):
        for code in ('reasoning_budget_exhausted','invalid_model_result',
                     'Required term/exception missing'):
            self.assertIsNone(fault_kind(code))
        p=self.pool({},names=('yjapi','vortex'))
        self.execute(p);p.audit.outcome(1,'invalid_model_result')
        self.assertEqual(p.state_snapshot()['yjapi'],'normal')
        self.assertEqual(p.audit.summary()['providers']['yjapi']['model_or_contract_rejections'],1)

    def test_http_request_errors_also_use_five_consecutive_failures(self):
        p=self.pool({},names=('yjapi','vortex'))
        for _ in range(4):p.release(p.acquire(),'BadRequestError:400')
        self.assertEqual(p.state_snapshot()['yjapi'],'normal')
        p.release(p.acquire(),'BadRequestError:400')
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')

    def test_initialization_failure_is_redacted_and_not_a_wire_call(self):
        p=self.pool({},names=('yjapi','vortex'))
        def broken(settings,key):raise ValueError('bad '+key)
        p.factory=broken
        for _ in range(5):
            with self.assertRaises(bridge.ApiFailure)as error:self.execute(p)
        self.assertFalse(error.exception.usage['request_issued'])
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')
        self.assertEqual(p.audit.summary()['providers'],{})
        self.assertNotIn('fixture-SECRET',p.audit.log.read_text(encoding='utf8'))

    def test_backup_uses_its_configured_concurrency_and_logs_the_peak(self):
        p=self.pool({},names=('yjapi','vortex'),limits={'vortex':2})
        for _ in range(5):p.release(p.acquire(),'PermissionDeniedError:403')
        self.execute(p)
        gate=threading.Event();entered=threading.Event();lock=threading.Lock();state={'active':0,'peak':0}
        original=p.clients[1].request
        def blocked(system,user):
            with lock:
                state['active']+=1;state['peak']=max(state['peak'],state['active'])
                if state['active']==2:entered.set()
            if not gate.wait(3):raise ValueError('Fixture gate timed out')
            try:return original(system,user)
            finally:
                with lock:state['active']-=1
        p.clients[1].request=blocked
        def work(number):return p.execute(p.acquire(),'','',number+100)
        with ThreadPoolExecutor(max_workers=6)as executor:
            futures=[executor.submit(work,n)for n in range(6)]
            try:self.assertTrue(entered.wait(2));self.assertEqual(state['peak'],2)
            finally:gate.set()
            for future in futures:future.result()
        self.assertEqual(p.audit.summary()['providers']['vortex']['peak_in_flight'],2)

    def test_authorization_errors_also_follow_five_failure_threshold(self):
        p=self.pool({},names=('yjapi','vortex'))
        for _ in range(4):p.release(p.acquire(),'PermissionDeniedError:403')
        self.assertEqual(p.state_snapshot()['yjapi'],'normal')
        p.release(p.acquire(),'PermissionDeniedError:403')
        self.assertEqual(p.state_snapshot()['yjapi'],'disabled')
        self.execute(p);self.assertEqual(self.calls,['vortex'])

    def test_report_replays_journal_and_keeps_partial_logs_unknown(self):
        p=self.pool({},names=('yjapi',));self.execute(p)
        p.audit.start('unfinished',p.specs[0]);p.audit.save()
        with p.audit.log.open('a',encoding='utf8')as f:f.write('{partial')
        report=build(self.root);entry=report['providers']['yjapi']
        self.assertEqual(entry['requests'],2);self.assertEqual(entry['unfinished_requests'],1)
        self.assertEqual(entry['response_success_rate'],1)
        self.assertEqual(entry['usage_unknown_requests'],1)
        self.assertEqual(len(report['malformed_log_lines']),1)

    def test_priority_validation_and_explicit_pin(self):
        for value in ({'enabled':True,'priority':[]},{'enabled':True,'priority':['a','a']},
                      {'enabled':True,'priority':['missing']},{'enabled':True,'priority':['a'],'failure_threshold':0}):
            with self.assertRaises(ValueError):validate_policy(value,{'a':{}})
        config={'default_api':'b','api_failover':{'enabled':True,'priority':['a','b']},
            'apis':{n:{'api_url':'https://fixture.invalid','api_key':'fixture-key','model_name':n}for n in ('a','b')}}
        path=self.root/'api.local.jsonc';path.write_text(json.dumps(config),encoding='utf8')
        self.assertEqual(w.local_api_name(path),'a')
        p=w.make_api_pool(w.DEFAULT,self.root,'extract',name='b',path=path)
        self.addCleanup(p.close)
        self.assertTrue(p.managed);self.assertEqual([s['name']for s in p.specs],['b'])


class WorkflowFailoverTests(unittest.TestCase):
    def test_public_pause_interrupts_wait_when_all_apis_are_cooling(self):
        import test_translation_workflow as fixtures
        f=fixtures.WorkflowTests();f.setUp();self.addCleanup(f.tearDown)
        f.flow.pause(True);f.flow.configure({'backend':'api'},'Cooling pause fixture',impact='none');f.flow.pause(False)
        w.API_CONFIG_FILE.write_text(json.dumps({'default_api':'a','concurrency':1,
            'api_failover':{'enabled':True,'priority':['a'],'failure_threshold':1,'cooldown_seconds':60},
            'apis':{'a':{'api_url':'https://fixture.invalid','api_key':'fixture','model_name':'a'}}}),encoding='utf8')
        called=threading.Event();calls=[]
        class Client:
            def __init__(self,*a):pass
            def close(self):pass
            def request(self,*a):
                calls.append(1);called.set();raise bridge.ApiFailure('InternalServerError:502',True)
        with ThreadPoolExecutor(max_workers=1)as executor:
            future=executor.submit(w.run_api,f.flow,'extract',Client)
            try:self.assertTrue(called.wait(2))
            finally:f.flow.pause(True)
            result=future.result(timeout=3)
        self.assertTrue(result['paused']);self.assertEqual(len(calls),1)

    def test_real_handoffs_keep_budget_and_attempts_across_provider_change(self):
        import test_translation_workflow as fixtures
        f=fixtures.WorkflowTests();f.setUp();self.addCleanup(f.tearDown)
        f.flow.pause(True)
        f.flow.configure({'backend':'api','max_attempts':1,'api':{'max_requests':1}},'Fixture priority',impact='none')
        f.flow.pause(False)
        config={'default_api':'cheap','concurrency':1,'max_requests':1,
            'api_failover':{'enabled':True,'priority':['cheap','stable'],'failure_threshold':5,'cooldown_seconds':60},
            'apis':{n:{'api_url':'https://fixture.invalid','api_key':'fixture-key','model_name':n}for n in ('cheap','stable')}}
        w.API_CONFIG_FILE.write_text(json.dumps(config),encoding='utf8');calls=[]
        class Client:
            def __init__(self,s,key):self.name=s['model_name']
            def close(self):pass
            def request(self,system,user):
                calls.append(self.name)
                if self.name=='cheap':raise bridge.ApiFailure('InternalServerError:502',True)
                return json.dumps({'analysis':{'characters':[],'terms':[],'non_translate':[]}}),{
                    'usage_reported':True,'prompt_tokens':10,'completion_tokens':10,'total_tokens':20}
        result=w.run_api(f.flow,'extract',Client)
        # One successful model call reaches the original cumulative cap; the
        # remaining source batch stays pending instead of resetting on fallback.
        self.assertTrue(result['paused']);self.assertEqual(result['accepted'],1)
        self.assertEqual(calls,['cheap']*5+['stable'])
        with f.flow.db()as db:
            self.assertEqual(budget_requests(db),1)
            task=dict(db.execute('SELECT * FROM tasks WHERE stage="extract"').fetchone())
            self.assertEqual(effective_attempts(db,task),1)
            usages=[json.loads(row[0])for row in db.execute('SELECT usage FROM api_calls WHERE status="received"')]
        self.assertEqual(usages[0]['api_name'],'stable')
        self.assertTrue(Path(result['api_provider_report']).exists())


if __name__=='__main__':unittest.main()
