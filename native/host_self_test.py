"""Test an open native-messaging pipe; never send EOF before receiving a reply."""
import json
import queue
import struct
import subprocess
import sys
import threading


def check_host(command, timeout=10):
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    replies = queue.Queue()
    errors = bytearray()

    def error_detail():
        # stdout and stderr readers can observe process exit in either order.
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            pass
        error_thread.join(timeout=0.5)
        return errors.decode('utf-8', errors='replace').strip()

    def read_exact(size):
        data = bytearray()
        while len(data) < size:
            chunk = process.stdout.read(size - len(data))
            if not chunk:
                process.poll()
                detail = error_detail()
                raise EOFError('Native host closed stdout before replying'
                               + (f' | exit={process.returncode}' if process.returncode is not None else '')
                               + (f' | stderr={detail}' if detail else ''))
            data.extend(chunk)
        return bytes(data)

    def reader():
        try:
            while True:
                length = struct.unpack('=I', read_exact(4))[0]
                if length > 1024 * 1024:
                    raise ValueError('Invalid response length')
                replies.put(json.loads(read_exact(length).decode('utf-8')))
        except Exception as error:
            replies.put(error)

    def drain_errors():
        while True:
            chunk = process.stderr.read(1024)
            if not chunk:
                return
            if len(errors) < 8192:
                errors.extend(chunk)

    output_thread = threading.Thread(target=reader, daemon=True)
    error_thread = threading.Thread(target=drain_errors, daemon=True)
    output_thread.start()
    error_thread.start()
    try:
        for index, frame_size in enumerate((266, 282)):
            # 0x0A and 0x1A in the header detect Windows text-mode translation/EOF.
            probe = 'Cagri-Çağrı-İı-雪-' + str(index)
            payload = json.dumps({'action':'ping','probe':probe},ensure_ascii=False).encode('utf-8')
            payload += b' ' * (frame_size-len(payload))
            try:
                process.stdin.write(struct.pack('=I', len(payload)) + payload)
                process.stdin.flush()
            except (BrokenPipeError, OSError):
                raise EOFError('Native host closed input before request | ' + error_detail()) from None
            try:
                reply = replies.get(timeout=timeout)
            except queue.Empty:
                process.poll()
                detail = error_detail()
                raise TimeoutError('Native host did not answer with stdin still open'
                                   + (f' | exit={process.returncode}' if process.returncode is not None else '')
                                   + (f' | stderr={detail}' if detail else '')) from None
            if isinstance(reply, Exception):
                raise reply
            if not reply.get('ok') or not reply.get('version') or reply.get('echo') != probe:
                raise ValueError('Invalid ping response')
        return reply['version']
    finally:
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        output_thread.join(timeout=2)
        error_thread.join(timeout=2)
        process.stdout.close()
        process.stderr.close()


if __name__ == '__main__':
    try:
        version = check_host(sys.argv[1:])
        print('BAGLANTI TESTI BASARILI - Ajan ' + version + ' - 2/2 ping')
    except Exception as error:
        print('BAGLANTI TESTI BASARISIZ: ' + str(error), file=sys.stderr)
        sys.exit(1)
