#!/usr/bin/env python3
import argparse
import json
import queue
import subprocess
import threading
import time


class Peer:
    def __init__(self, executable, demo):
        self.process = subprocess.Popen(
            [executable] + (['--demo'] if demo else []),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', bufsize=1,
        )
        self.inbox = queue.Queue()
        self.events = []
        self.sequence = 0
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            for line in self.process.stdout:
                self.inbox.put(json.loads(line))
        except Exception as error:
            self.inbox.put(error)
        finally:
            self.inbox.put(EOFError('core stdout closed'))

    def next(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('core response timed out')
        item = self.inbox.get(timeout=remaining)
        if isinstance(item, Exception):
            raise item
        return item

    def call(self, method, params=None, timeout=15):
        self.sequence += 1
        request_id = str(self.sequence)
        self.process.stdin.write(json.dumps({
            'id': request_id, 'method': method, 'params': params or {},
        }, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        deadline = time.monotonic() + timeout
        while True:
            item = self.next(deadline)
            if item.get('id') == request_id:
                return item
            assert 'event' in item, 'unexpected reply or non-protocol output'
            self.events.append(item)

    def event(self, name, predicate=lambda data: True, timeout=15):
        deadline = time.monotonic() + timeout
        while True:
            for index, item in enumerate(self.events):
                if item.get('event') == name and predicate(item['data']):
                    return self.events.pop(index)['data']
            item = self.next(deadline)
            assert 'event' in item, 'unexpected response while waiting for event'
            self.events.append(item)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
                raise AssertionError('core did not stop after stdin EOF')


def check_demo(executable):
    peer = Peer(executable, demo=True)
    try:
        response = peer.call('initialize')
        assert response['ok'] and response['result']['mode'] == 'demo'
        snapshot = peer.event('snapshot')
        assert snapshot['profile']['id'] and snapshot['contacts'] and snapshot['chats']
        chat_id = snapshot['chats'][0]['id']
        history = peer.call('messages.list', {'chat_id': chat_id, 'limit': 50})
        assert history['ok'] and isinstance(history['result']['messages'], list)
        text = 'GTK 整合檢查 <script>不執行</script> 🟢'
        reply = peer.call('messages.send', {'chat_id': chat_id, 'text': text})
        assert reply['ok'], reply
        sent = reply['result']['message']
        assert sent['text'] == text and sent['mine'] and sent['chat_id'] == chat_id
        history = peer.call('messages.list', {'chat_id': chat_id, 'limit': 50})
        assert sum(msg['id'] == sent['id'] for msg in history['result']['messages']) == 1
        assert peer.call('messages.read', {'chat_id': chat_id, 'message_id': sent['id']})['ok']
        for invalid in ('', '  \n', '字' * 10001):
            assert not peer.call('messages.send', {'chat_id': chat_id, 'text': invalid})['ok']
        assert not peer.call('messages.send', {'chat_id': 'invalid', 'text': 'hello'})['ok']
        assert not peer.call('unknown.method')['ok']
        assert peer.call('shutdown')['ok']
        assert peer.process.wait(timeout=5) == 0
        print('PASS demo initialize / history / Unicode send / dedup / validation / shutdown')
    finally:
        peer.close()


def check_live_qr(executable):
    peer = Peer(executable, demo=False)
    try:
        assert peer.call('initialize', timeout=40)['ok']
        peer.event('session', lambda data: data['state'] == 'logged_out', timeout=40)
        assert peer.call('login.qr')['ok']
        auth = peer.event('auth', lambda data: data.get('stage') in ('qr', 'error'), timeout=60)
        assert auth['stage'] == 'qr', 'LINE did not return a QR challenge; no credential data printed'
        assert auth.get('qr_png') or auth.get('qr_url')
        assert peer.call('login.cancel')['ok']
        peer.event('session', lambda data: data['state'] == 'logged_out')
        print('PASS live QR challenge creation and cancellation (no account login performed)')
    finally:
        peer.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('executable')
    parser.add_argument('--live-qr', action='store_true')
    arguments = parser.parse_args()
    check_demo(arguments.executable)
    if arguments.live_qr:
        check_live_qr(arguments.executable)
