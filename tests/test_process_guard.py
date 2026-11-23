"""Real Windows child/grandchild processes; no model/API, small memory fixtures."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from rsi.process_guard import run_guarded, wait_for_memory, MiB

LIMITS={'memory_limit_mb':128,'cpu_limit_percent':20,'max_processes':16,'max_log_mb':4,
        'min_available_memory_mb':1024,'runtime_free_floor_mb':256}


class AdmissionTests(unittest.TestCase):
    def test_waits_without_worker_until_memory_returns(self):
        now=[0.];samples=iter([900*MiB,1200*MiB,1600*MiB]);progress=[]
        limits={**LIMITS,'memory_limit_mb':1024,'min_available_memory_mb':1536,
                'runtime_free_floor_mb':512,'startup_wait_seconds':6}
        def sleep(seconds):now[0]+=seconds
        ready,waited=wait_for_memory(limits,lambda:next(samples),lambda *args:progress.append(args),
                                     clock=lambda:now[0],sleep=sleep)
        self.assertTrue(ready);self.assertEqual(waited,4.)
        self.assertEqual([p[0] for p in progress],[900*MiB,1200*MiB])
        self.assertTrue(all(p[1]==1536*MiB for p in progress))

    def test_wait_is_bounded_and_does_not_admit_on_expiry(self):
        now=[0.];progress=[]
        def sleep(seconds):now[0]+=seconds
        ready,waited=wait_for_memory({**LIMITS,'startup_wait_seconds':3},lambda:0,
                                     lambda *args:progress.append(args),clock=lambda:now[0],sleep=sleep)
        self.assertFalse(ready);self.assertEqual(waited,3.)
        self.assertEqual([p[2] for p in progress],[0.,2.,3.])

    def test_admission_does_not_add_an_unconfigured_gibibyte(self):
        ready,waited=wait_for_memory(LIMITS,lambda:1024*MiB,lambda *args:self.fail('must admit'))
        self.assertTrue(ready)


def alive(pid):
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
    k.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];k.CloseHandle.argtypes=[ctypes.c_void_p]
    handle=k.OpenProcess(0x100000,False,pid)
    if not handle:return False
    try:return k.WaitForSingleObject(handle,0)==258
    finally:k.CloseHandle(handle)


@contextmanager
def guarded_task_directory():
    from rsi.runner import cleanup_workspace
    with tempfile.TemporaryDirectory(prefix="physics-lab-agent-") as folder:
        try:
            yield folder
        finally:
            result = cleanup_workspace(Path(folder).resolve())
            if result["status"] != "removed":
                raise OSError(f"task directory still held after bounded cleanup: {result}")


@unittest.skipUnless(os.name=='nt','Windows Job Objects are tested on Windows')
class WindowsJobTests(unittest.TestCase):
    def run_script(self,root,source,timeout=5,limits=None):
        script=root/'task.py';script.write_text(source,encoding='utf-8')
        return run_guarded([sys.executable,'-u',str(script)],root,os.environ,root/'logs',timeout,limits or LIMITS)

    def assert_dead(self,pid):
        deadline=time.monotonic()+3
        while alive(pid) and time.monotonic()<deadline:time.sleep(.05)
        self.assertFalse(alive(pid),f'job process still alive: {pid}')

    def test_log_handle_allows_path_cleanup(self):
        from rsi.process_guard import kernel, open_task_log
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            with open_task_log(kernel(), path) as stream:
                stream.write(b"checkpoint")
                self.assertEqual(path.read_bytes(), b"checkpoint")
                path.unlink()
            self.assertFalse(path.exists())

    def test_normal_exit_kills_leftover_child_and_releases_working_directory(self):
        with guarded_task_directory() as folder:
            root=Path(folder)
            result=self.run_script(root,"import subprocess,sys,json\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\nPath('child.json').write_text(json.dumps({'pid':p.pid}))\nprint('root complete',flush=True)\n")
            self.assertEqual(result['status'],'finished',result)
            self.assertTrue(result['cleanup_verified'])
            self.assertEqual(result['active_processes_after'],0)
            self.assert_dead(json.loads((root/'child.json').read_text())['pid'])
            self.assertIn('root complete',(root/'logs/events.jsonl').read_text())
            moved=root/'rename-check';(root/'child.json').rename(moved);moved.unlink()

    def test_timeout_kills_child_and_preserves_streamed_logs(self):
        with guarded_task_directory() as folder:
            root=Path(folder)
            source="import subprocess,sys,json,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\nPath('child.json').write_text(json.dumps({'pid':p.pid}))\nprint('partial output',flush=True)\ntime.sleep(30)\n"
            result=self.run_script(root,source,timeout=1)
            self.assertEqual(result['status'],'timeout',result)
            self.assertTrue(result['cleanup_verified'])
            self.assert_dead(json.loads((root/'child.json').read_text())['pid'])
            self.assertIn('partial output',(root/'logs/events.jsonl').read_text())

    def test_huge_allocation_is_refused_without_allocating_huge_memory(self):
        with guarded_task_directory() as folder:
            root=Path(folder)
            source="import time\nprint('before allocation',flush=True)\ntry:\n x=bytearray(1024**3)\nexcept MemoryError:\n print('allocation denied',flush=True)\n time.sleep(30)\n"
            result=self.run_script(root,source,timeout=5)
            self.assertEqual(result['status'],'resource_limit',result)
            self.assertEqual(result['limit_reason'],'memory')
            self.assertTrue(result['cleanup_verified'])
            self.assertEqual(result['enforced']['job_memory_limit_bytes'],128*1024**2)

    def test_soft_memory_notification_does_not_kill_task_below_hard_limit(self):
        with guarded_task_directory() as folder:
            result=self.run_script(Path(folder),"import time\nx=bytearray(100*1024**2)\nprint('under hard limit',flush=True)\ntime.sleep(.2)\n")
            self.assertEqual(result['status'],'finished',result)
            self.assertIn(11,result['notifications'])
            self.assertTrue(result['cleanup_verified'])

    def test_supervisor_termination_also_kills_entire_job(self):
        with guarded_task_directory() as folder:
            root=Path(folder)
            task=root/'task.py'
            task.write_text("import subprocess,sys,time,json,os\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\nPath('pids.tmp').write_text(json.dumps([os.getpid(),p.pid]))\nPath('pids.tmp').replace('pids.json')\ntime.sleep(30)\n",encoding='utf-8')
            script=root/'supervisor.py'
            project=Path(__file__).resolve().parents[1]
            script.write_text('import sys,os\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(project))+')\nfrom rsi.process_guard import run_guarded\nrun_guarded([sys.executable,str(Path(__file__).with_name("task.py"))],Path(__file__).parent,os.environ,Path(__file__).parent/"logs",25,'+repr(LIMITS)+')\n',encoding='utf-8')
            supervisor=subprocess.Popen([sys.executable,str(script)],cwd=root,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                deadline=time.monotonic()+5
                while not (root/'pids.json').exists() and supervisor.poll() is None and time.monotonic()<deadline:time.sleep(.05)
                self.assertTrue((root/'pids.json').is_file())
                pids=json.loads((root/'pids.json').read_text())
                # A second task cannot start while this job owns the global task slot.
                blocked=run_guarded([sys.executable,'-c','print(1)'],root,os.environ,root/'blocked',1,LIMITS)
                self.assertEqual(blocked['status'],'resource_busy')
                supervisor.terminate();supervisor.wait(timeout=5)
                for pid in pids:self.assert_dead(pid)
                # Process signalling can precede final kernel file-handle release.
                # Require release within a bounded interval; never ignore locked logs.
                log=root/'logs/events.jsonl';moved=log.with_suffix('.closed')
                deadline=time.monotonic()+3
                while True:
                    try:log.rename(moved);break
                    except PermissionError:
                        if time.monotonic()>=deadline:raise
                        time.sleep(.05)
            finally:
                if supervisor.poll() is None:supervisor.kill();supervisor.wait(timeout=5)

    def test_single_thread_environment_is_applied_to_task(self):
        with guarded_task_directory() as folder:
            root=Path(folder)
            result=self.run_script(root,"import os,json\nprint(json.dumps({k:os.environ[k] for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','BUN_OPTIONS']}))\n")
            self.assertEqual(result['status'],'finished',result)
            env=json.loads((root/'logs/events.jsonl').read_text())
            self.assertEqual(env.pop('BUN_OPTIONS'),'--smol')
            self.assertEqual(set(env.values()),{'1'})

    def test_admission_wait_does_not_consume_execution_timeout(self):
        with guarded_task_directory() as folder:
            calls=[0]
            def available(k):
                calls[0]+=1
                return 0 if calls[0]==1 else 4096*MiB
            with patch('rsi.process_guard.available_memory',side_effect=available):
                result=self.run_script(Path(folder),"import time\nprint('started',flush=True)\ntime.sleep(.15)\n",
                                       timeout=.8,limits={**LIMITS,'startup_wait_seconds':1})
            self.assertEqual(result['status'],'finished',result)
            self.assertGreaterEqual(result['startup_wait_seconds'],1.)
            self.assertGreater(result['elapsed_seconds'],result['seconds']+.9)
            self.assertTrue(result['cleanup_verified']);self.assertEqual(result['active_processes_after'],0)


if __name__=='__main__':unittest.main()
