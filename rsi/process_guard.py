"""Fail-closed Windows task process tree with kernel resource limits.

Children start suspended, join a non-breakaway Job Object, then run. Only the
task's stdio handles are inherited. Closing the supervisor kills the entire job.
This is resource containment, not a filesystem/network security sandbox.
"""
import ctypes as C
from ctypes import wintypes as W
import os
from pathlib import Path
import subprocess
import time
from .checkpoint import checkpoint

MiB=1024**2


class BasicLimits(C.Structure):
    _fields_=[('PerProcessUserTimeLimit',C.c_int64),('PerJobUserTimeLimit',C.c_int64),
              ('LimitFlags',W.DWORD),('MinimumWorkingSetSize',C.c_size_t),('MaximumWorkingSetSize',C.c_size_t),
              ('ActiveProcessLimit',W.DWORD),('Affinity',C.c_size_t),('PriorityClass',W.DWORD),('SchedulingClass',W.DWORD)]

class IOCounters(C.Structure):
    _fields_=[(n,C.c_uint64) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount',
                                    'ReadTransferCount','WriteTransferCount','OtherTransferCount')]

class ExtendedLimits(C.Structure):
    _fields_=[('BasicLimitInformation',BasicLimits),('IoInfo',IOCounters),('ProcessMemoryLimit',C.c_size_t),
              ('JobMemoryLimit',C.c_size_t),('PeakProcessMemoryUsed',C.c_size_t),('PeakJobMemoryUsed',C.c_size_t)]

class Accounting(C.Structure):
    _fields_=[(n,C.c_int64) for n in ('TotalUserTime','TotalKernelTime','ThisPeriodTotalUserTime','ThisPeriodTotalKernelTime')]+[
              (n,W.DWORD) for n in ('TotalPageFaultCount','TotalProcesses','ActiveProcesses','TotalTerminatedProcesses')]

class CompletionPort(C.Structure):
    _fields_=[('CompletionKey',C.c_void_p),('CompletionPort',W.HANDLE)]

class NotificationLimits(C.Structure):
    _fields_=[('IoReadBytesLimit',C.c_uint64),('IoWriteBytesLimit',C.c_uint64),('PerJobUserTimeLimit',C.c_int64),
              ('JobMemoryLimit',C.c_uint64),('RateControlTolerance',W.DWORD),
              ('RateControlToleranceInterval',W.DWORD),('LimitFlags',W.DWORD)]

class CpuLimits(C.Structure):
    _fields_=[('ControlFlags',W.DWORD),('CpuRate',W.DWORD)]

class MemoryStatus(C.Structure):
    _fields_=[('dwLength',W.DWORD),('dwMemoryLoad',W.DWORD)]+[(n,C.c_uint64) for n in (
        'ullTotalPhys','ullAvailPhys','ullTotalPageFile','ullAvailPageFile','ullTotalVirtual','ullAvailVirtual','ullAvailExtendedVirtual')]


def kernel():
    if os.name!='nt':
        raise RuntimeError('Hard task resource isolation is currently implemented for Windows only; real runs are refused on this platform')
    k=C.WinDLL('kernel32',use_last_error=True)
    signatures={
      'CreateJobObjectW':([C.c_void_p,W.LPCWSTR],W.HANDLE),
      'CreateMutexW':([C.c_void_p,W.BOOL,W.LPCWSTR],W.HANDLE),
      'ReleaseMutex':([W.HANDLE],W.BOOL),
      'CloseHandle':([W.HANDLE],W.BOOL),
      'CreateFileW':([W.LPCWSTR,W.DWORD,W.DWORD,C.c_void_p,W.DWORD,W.DWORD,W.HANDLE],W.HANDLE),
      'CreateIoCompletionPort':([W.HANDLE,W.HANDLE,C.c_size_t,W.DWORD],W.HANDLE),
      'SetInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD],W.BOOL),
      'QueryInformationJobObject':([W.HANDLE,C.c_int,C.c_void_p,W.DWORD,C.c_void_p],W.BOOL),
      'AssignProcessToJobObject':([W.HANDLE,W.HANDLE],W.BOOL),
      'TerminateJobObject':([W.HANDLE,W.UINT],W.BOOL),
      'TerminateProcess':([W.HANDLE,W.UINT],W.BOOL),
      'WaitForSingleObject':([W.HANDLE,W.DWORD],W.DWORD),
      'ResumeThread':([W.HANDLE],W.DWORD),
      'GetExitCodeProcess':([W.HANDLE,C.POINTER(W.DWORD)],W.BOOL),
      'GetQueuedCompletionStatus':([W.HANDLE,C.POINTER(W.DWORD),C.POINTER(C.c_size_t),C.POINTER(C.c_void_p),W.DWORD],W.BOOL),
      'GlobalMemoryStatusEx':([C.POINTER(MemoryStatus)],W.BOOL),
    }
    for name,(args,result) in signatures.items():
        f=getattr(k,name);f.argtypes=args;f.restype=result
    return k


def check(ok):
    if not ok:raise C.WinError(C.get_last_error())
    return ok


def available_memory(k):
    m=MemoryStatus();m.dwLength=C.sizeof(m);check(k.GlobalMemoryStatusEx(C.byref(m)))
    return m.ullAvailPhys


def wait_for_memory(limits, sample, progress, clock=None, sleep=None):
    """Wait before creating a task; no model/worker exists during admission wait."""
    clock=clock or time.monotonic
    sleep=sleep or time.sleep
    minimum=max(limits['min_available_memory_mb'],
                limits['memory_limit_mb']+limits['runtime_free_floor_mb'])*MiB
    started=clock();budget=limits.get('startup_wait_seconds',0)
    while True:
        free=sample();elapsed=clock()-started
        if free>=minimum:return True,elapsed
        progress(free,minimum,elapsed)
        if elapsed>=budget:return False,elapsed
        sleep(min(2,budget-elapsed))


def thread_environment(environment):
    value=dict(environment)
    for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS','VECLIB_MAXIMUM_THREADS'):
        value[name]='1'
    value['PYTHONUNBUFFERED']='1';value['PYTHONUTF8']='1'
    # Compiled Bun executables support BUN_OPTIONS. Keep the task's private
    # OpenCode server on the small-heap/early-GC path; this is not a hard cap.
    value['BUN_OPTIONS']='--smol'
    value['OPENCODE_DISABLE_FILEWATCHER']='true'
    return value


def open_task_log(kernel32, path):
    import msvcrt
    # Let cleanup release the pathname even if Windows is still closing an
    # inherited log handle after task termination. The active handle keeps
    # writing to the same file until it closes.
    generic_write = 0x40000000
    share_read_write_delete = 0x7
    create_always = 2
    normal_attributes = 0x80
    handle = kernel32.CreateFileW(str(path), generic_write, share_read_write_delete,
                                  None, create_always, normal_attributes, None)
    if handle == C.c_void_p(-1).value:
        raise C.WinError(C.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except BaseException:
        kernel32.CloseHandle(handle)
        raise
    return os.fdopen(fd, 'wb', buffering=0)


def run_guarded(command,cwd,environment,log_directory,timeout_seconds,limits):
    """Stream logs to disk and return only after all task descendants are dead."""
    k=kernel()
    import _winapi
    import msvcrt
    log_directory=Path(log_directory);log_directory.mkdir(parents=True,exist_ok=True)
    started=time.monotonic();active_started=None
    job=port=mutex=process=thread=None;owned_mutex=False;assigned=False
    result={'status':'supervisor_error','exit_code':None,'root_pid':None,'seconds':0.,
            'backend':'windows-job-object','limits':dict(limits),'cleanup_verified':False,
            'active_processes_after':None,'peak_job_memory_bytes':0,'limit_reason':None,'notifications':[],
            'startup_wait_seconds':0.,'elapsed_seconds':0.}
    def query(cls,typ):
        value=typ();check(k.QueryInformationJobObject(job,cls,C.byref(value),C.sizeof(value),None));return value
    def update():
        now=time.monotonic()
        result['seconds']=max(0.,now-active_started) if active_started is not None else 0.
        result['elapsed_seconds']=now-started
        checkpoint(log_directory/'resources.json',result)
    def notifications():
        reason=None
        while True:
            msg=W.DWORD();key=C.c_size_t();overlap=C.c_void_p()
            if not k.GetQueuedCompletionStatus(port,C.byref(msg),C.byref(key),C.byref(overlap),0):
                if C.get_last_error()==258:break
                raise C.WinError(C.get_last_error())
            if msg.value in (3,9,10,11):
                result['notifications'].append(int(msg.value))
            # JOB_OBJECT_MSG_NOTIFICATION_LIMIT (11) is advisory. The kernel
            # hard limit remains enforced separately; do not score a warning
            # at 75% as if the task exhausted its declared 1 GiB allowance.
            if msg.value in (3,9,10):
                reason='process_count' if msg.value==3 else 'memory'
        return reason
    try:
        mutex=check(k.CreateMutexW(None,False,'Local\\physics-lab-agent-task-slot'))
        wait=k.WaitForSingleObject(mutex,0)
        if wait not in (0,128):
            result['status']='resource_busy';update();return result
        owned_mutex=True
        announced=False
        def waiting(free,minimum,elapsed):
            nonlocal announced
            result.update(status='waiting_for_memory',startup_wait_seconds=elapsed,
                          available_memory_mb=int(free/MiB),required_memory_mb=int(minimum/MiB))
            update()
            if not announced:
                print(f'Waiting for task memory: {int(free/MiB)} MiB available, '
                      f'{int(minimum/MiB)} MiB required; no worker started',flush=True)
                announced=True
        admitted,waited=wait_for_memory(limits,lambda:available_memory(k),waiting)
        result['startup_wait_seconds']=waited
        if not admitted:
            result.update(status='host_memory_low',limit_reason='insufficient_free_memory_before_start');update();return result
        if announced:print(f'Memory available; starting task after {waited:.1f}s wait',flush=True)
        job=check(k.CreateJobObjectW(None,None))
        info=ExtendedLimits()
        # KILL_ON_JOB_CLOSE + JOB_MEMORY + ACTIVE_PROCESS + BELOW_NORMAL_PRIORITY.
        info.BasicLimitInformation.LimitFlags=0x2000|0x200|0x8|0x20
        info.BasicLimitInformation.ActiveProcessLimit=limits['max_processes']
        info.BasicLimitInformation.PriorityClass=0x4000
        info.JobMemoryLimit=limits['memory_limit_mb']*MiB
        check(k.SetInformationJobObject(job,9,C.byref(info),C.sizeof(info)))
        cpu=CpuLimits(0x1|0x4,limits['cpu_limit_percent']*100)
        check(k.SetInformationJobObject(job,15,C.byref(cpu),C.sizeof(cpu)))
        port=check(k.CreateIoCompletionPort(C.c_void_p(-1),None,0,1))
        associate=CompletionPort(1,port)
        check(k.SetInformationJobObject(job,7,C.byref(associate),C.sizeof(associate)))
        notify=NotificationLimits();notify.JobMemoryLimit=limits['memory_limit_mb']*MiB*3//4;notify.LimitFlags=0x200
        check(k.SetInformationJobObject(job,12,C.byref(notify),C.sizeof(notify)))
        actual=query(9,ExtendedLimits);actual_cpu=query(15,CpuLimits)
        result['enforced']={'job_memory_limit_bytes':int(actual.JobMemoryLimit),
                            'memory_notification_bytes':int(notify.JobMemoryLimit),
                            'cpu_limit_percent':actual_cpu.CpuRate/100,
                            'max_processes':int(actual.BasicLimitInformation.ActiveProcessLimit)}
        paths=[log_directory/'events.jsonl',log_directory/'stderr.log']
        with (open_task_log(k, paths[0]) as stdout,
              open_task_log(k, paths[1]) as stderr,
              open(os.devnull,'rb',buffering=0) as stdin):
            handles=[msvcrt.get_osfhandle(s.fileno()) for s in (stdin,stdout,stderr)]
            old=[os.get_handle_inheritable(h) for h in handles]
            si=subprocess.STARTUPINFO();si.dwFlags=subprocess.STARTF_USESTDHANDLES
            si.hStdInput,si.hStdOutput,si.hStdError=handles
            si.lpAttributeList={'handle_list':handles}
            try:
                for h in handles:os.set_handle_inheritable(h,True)
                process,thread,pid,_=_winapi.CreateProcess(None,subprocess.list2cmdline([os.fspath(a) for a in command]),
                    None,None,True,0x4|0x80000|0x8000000|0x4000,thread_environment(environment),str(cwd),si)
            finally:
                for h,inherit in zip(handles,old):os.set_handle_inheritable(h,inherit)
            result['root_pid']=pid
            check(k.AssignProcessToJobObject(job,process))
            assigned=True
            if k.ResumeThread(thread)==0xffffffff:raise C.WinError(C.get_last_error())
            active_started=time.monotonic()
            _winapi.CloseHandle(thread);thread=None
            result['status']='running';update();last_update=active_started
            while True:
                reason=notifications()
                result['peak_job_memory_bytes']=max(result['peak_job_memory_bytes'],int(query(9,ExtendedLimits).PeakJobMemoryUsed))
                if reason:
                    result.update(status='resource_limit',limit_reason=reason);break
                if any(p.stat().st_size>limits['max_log_mb']*MiB for p in paths):
                    result.update(status='resource_limit',limit_reason='log_size');break
                if k.WaitForSingleObject(process,50)==0:
                    # Drain memory errors that arrive with a fast failed allocation/exit.
                    reason=notifications()
                    code=W.DWORD();check(k.GetExitCodeProcess(process,C.byref(code)))
                    result.update(status='resource_limit' if reason else 'finished' if code.value==0 else 'process_failed',
                                  limit_reason=reason,exit_code=int(code.value));break
                if time.monotonic()-active_started>=timeout_seconds:
                    result['status']='timeout';break
                if time.monotonic()-last_update>=1:
                    if available_memory(k)<limits['runtime_free_floor_mb']*MiB:
                        result.update(status='resource_limit',limit_reason='host_memory_pressure');break
                    update();last_update=time.monotonic()
    except KeyboardInterrupt:
        result['status']='interrupted'
    except BaseException as exc:
        result.update(status='supervisor_error',error=f'{type(exc).__name__}: {exc}')
    finally:
        # No background processes survive normal exit, timeout, limit, or supervisor errors.
        try:
            if process and job:
                check(k.TerminateJobObject(job,1))
                # Also handles the suspended-before-assignment failure path.
                if not assigned and k.WaitForSingleObject(process,0)!=0:check(k.TerminateProcess(process,1))
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    account=query(1,Accounting)
                    if account.ActiveProcesses==0 and k.WaitForSingleObject(process,0)==0:break
                    time.sleep(.05)
                result.update(active_processes_after=int(query(1,Accounting).ActiveProcesses),
                              total_job_processes=int(query(1,Accounting).TotalProcesses))
                result['cleanup_verified']=result['active_processes_after']==0 and k.WaitForSingleObject(process,0)==0
                if not result['cleanup_verified']:result['status']='cleanup_failed'
            elif process:
                check(k.TerminateProcess(process,1));k.WaitForSingleObject(process,10000)
            else:
                result.update(cleanup_verified=True,active_processes_after=0)
        except BaseException as exc:
            result.update(status='cleanup_failed',cleanup_verified=False,cleanup_error=f'{type(exc).__name__}: {exc}')
        finally:
            if thread:_winapi.CloseHandle(thread)
            if process:_winapi.CloseHandle(process)
            if job:k.CloseHandle(job)
            if port:k.CloseHandle(port)
            if owned_mutex:k.ReleaseMutex(mutex)
            if mutex:k.CloseHandle(mutex)
            update()
    return result
