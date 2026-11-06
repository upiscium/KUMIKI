"""R22 disposable regression: Host locator switched while native GET succeeds."""
from __future__ import annotations
import unittest
from tests.test_kagari_vnext_main_v2_session_observation import (
    V2SessionLocator, SID)
from tests.test_metadata_v2 import REPOSITORY
from task_record import encode_record
from tools.kagari_vnext_main import MainError

class HostLocatorRecheckTests(unittest.TestCase):
    def setUp(self):
        # Keep the foreign TestCase class out of this module's globals:
        # CI's complete contract inventory requires each test to have one home.
        from tests.test_kagari_vnext_main_v2_session_observation import (
            R5B0SessionReadOnlyTests)
        self.source=R5B0SessionReadOnlyTests("runTest")
        self.addCleanup(self.source.doCleanups)
        self.source.setUp()
        self.source.existing_worktree()
        self.source.native_asks()
        self.before=self.source.snapshot()

    def test_root_session_switch_during_native_get_refuses(self):
        source=self.source
        state={"sid":SID,"changed":False}
        subsequent="ses_changedhostroot123456"
        reads=[]
        def locator(req):
            reads.append(state["sid"])
            return V2SessionLocator("PRESENT",req.repository,req.issue,req.contract_id,
                str(source.src.project/".worktrees"/str(req.issue)),
                state["sid"],"http://127.0.0.1:43211")
        def changed(path):
            if path=="/permission" and not state["changed"]:
                state.update(sid=subsequent,changed=True)
        source.locator_hook=locator
        source.upstream_hook=changed
        with self.assertRaises(MainError) as found:
            source.probe(mode="direct")
        self.assertEqual(found.exception.code,"V2_SESSION_LOCATOR_CHANGED")
        self.assertEqual(reads,[SID,subsequent])
        self.assertTrue(state["changed"])
        source.preserved(self.before)

    def test_stable_current_root_session_remains_observable(self):
        source=self.source
        reads=[]
        def locator(req):
            reads.append(SID)
            return V2SessionLocator("PRESENT",req.repository,req.issue,req.contract_id,
                str(source.src.project/".worktrees"/str(req.issue)),
                SID,"http://127.0.0.1:43211")
        source.locator_hook=locator
        observation=source.probe(mode="direct")
        self.assertEqual(reads,[SID,SID])
        self.assertEqual(observation["session_observation"]["state"],"PENDING_ASK")
        self.assertEqual(observation["session_observation"]["session_id"],SID)
        source.preserved(self.before)

    def test_current_record_changed_during_second_locator_read_refuses(self):
        source=self.source
        calls=[0]
        changed=[False]
        def locator(req):
            calls[0]+=1
            if calls[0]==2:
                src=source.src
                rid, record=encode_record(
                    REPOSITORY,"101",src.base,
                    {"schema_version":1,
                     "branch_ref":src.approved.branch_ref,
                     "contract_id":src.registered.contract_id,
                     "disposition":{"kind":"cancelled"}})
                src.fixture.store().publish_issue(
                    "101",[record],
                    record_update=(src.registered.record_id,rid))
                changed[0]=True
            return V2SessionLocator("PRESENT",req.repository,req.issue,req.contract_id,
                str(source.src.project/".worktrees"/str(req.issue)),
                SID,"http://127.0.0.1:43211")
        source.locator_hook=locator
        with self.assertRaises(MainError) as found:
            source.probe(mode="direct")
        self.assertTrue(changed[0])
        self.assertEqual(calls,[2])
        self.assertIn(found.exception.code,
                      ("V2_MAIN_CURRENT_TASK_UNAVAILABLE","V2_SESSION_CURRENT_CHANGED"))
        source.preserved(self.before)

    def test_issue_changed_during_second_locator_read_refuses(self):
        source=self.source
        calls=[0]
        def locator(req):
            calls[0]+=1
            if calls[0]==2:
                source.facts[101]["title"]="changed during final Host locator read"
            return V2SessionLocator("PRESENT",req.repository,req.issue,req.contract_id,
                str(source.src.project/".worktrees"/str(req.issue)),
                SID,"http://127.0.0.1:43211")
        source.locator_hook=locator
        with self.assertRaises(MainError) as found:
            source.probe(mode="direct")
        self.assertEqual(calls,[2])
        self.assertIn(found.exception.code,
                      ("V2_MAIN_ISSUE_SNAPSHOT_DRIFT","V2_SESSION_CURRENT_CHANGED"))
        source.preserved(self.before)

    def test_second_locator_read_error_is_fail_closed(self):
        source=self.source
        calls=[0]
        def locator(req):
            calls[0]+=1
            if calls[0]>1:raise RuntimeError("locator temporarily unavailable")
            return V2SessionLocator("PRESENT",req.repository,req.issue,req.contract_id,
                str(source.src.project/".worktrees"/str(req.issue)),
                SID,"http://127.0.0.1:43211")
        source.locator_hook=locator
        with self.assertRaises(MainError) as found:
            source.probe(mode="direct")
        self.assertEqual(found.exception.code,"V2_SESSION_LOCATOR_UNAVAILABLE")
        self.assertEqual(calls,[2])
        source.preserved(self.before)

if __name__=="__main__":
    unittest.main()
