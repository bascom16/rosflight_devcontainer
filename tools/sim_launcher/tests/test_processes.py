import asyncio
import os
import time

from sim_launcher.processes import OutputBuffer, ProcessManager


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with open(f'/proc/{pid}/stat') as f:
        return f.read().rsplit(')', 1)[1].split()[0] != 'Z'


def test_stop_kills_whole_process_tree(tmp_path):
    async def run():
        pm = ProcessManager()
        # The child ignores SIGINT and SIGTERM, so stopping it needs the SIGKILL escalation.
        script = "trap '' INT TERM; sleep 100 & echo child=$!; wait"
        proc = await pm.start('tree', ['bash', '-c', script], tmp_path)
        for _ in range(50):
            lines, _ = pm.output('tree').since(0)
            child = next((l.split('=')[1] for l in lines if l.startswith('child=')), None)
            if child:
                break
            await asyncio.sleep(0.1)
        assert child and _alive(int(child))
        start = time.monotonic()
        await proc.stop(grace=0.5)
        assert not proc.running
        assert not _alive(int(child))
        assert time.monotonic() - start < 15

    asyncio.run(run())


def test_output_buffer_since():
    buf = OutputBuffer(maxlen=3)
    for i in range(5):
        buf.append(str(i))
    assert buf.since(0) == (['2', '3', '4'], 5)
    assert buf.since(4) == (['4'], 5)
    assert buf.since(5) == ([], 5)
